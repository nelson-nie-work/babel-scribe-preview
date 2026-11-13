"""短时间跟进刚才出字的编辑框，提议一次有明确边界的改词。"""
import json
import os
from queue import Empty, SimpleQueue
import subprocess
import threading
import time
from typing import Callable

from scribe.learning import is_word_selection
from scribe.output import uia_command


START_GRACE_MS = 30_000
EDIT_GRACE_MS = 45_000
MAX_WATCH_MS = 90_000

_WATCH_EDIT = """
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Add-Type -AssemblyName UIAutomationClient

function Normalize-Text([string] $text) {
    return $text.Replace(([string][char]13 + [char]10), [string][char]10).Replace([char]13, [char]10)
}

$original = Normalize-Text $env:SCRIBE_EDIT_SOURCE
$walker = [System.Windows.Automation.TreeWalker]::RawViewWalker

function In-Target($element) {
    for ($i = 0; $null -ne $element -and $i -lt 96; $i++) {
        if ($element.Current.NativeWindowHandle -eq TARGET_WINDOW) { return $true }
        $element = $walker.GetParent($element)
    }
    return $false
}

function Write-Status([string] $value) {
    [Console]::WriteLine((@{ status = $value } | ConvertTo-Json -Compress))
}

# 浏览器可能晚一拍才发布粘贴/逐字输入后的 UIA 文本。
$watch = [Diagnostics.Stopwatch]::StartNew()
$identity = ''
$reason = 'focus_unavailable'
do {
    try {
        $focused = [System.Windows.Automation.AutomationElement]::FocusedElement
        if ($null -ne $focused -and -not $focused.Current.IsPassword) {
            $pattern = $null
            if ($focused.TryGetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern, [ref]$pattern) -and
                (In-Target $focused)) {
                $reason = 'baseline_unmatched'
                $baseline = Normalize-Text ($pattern.DocumentRange.GetText(8193))
                $offset = $baseline.IndexOf($original, [StringComparison]::Ordinal)
                if ($baseline.Length -lt 8193 -and $offset -ge 0 -and
                    $offset -eq $baseline.LastIndexOf($original, [StringComparison]::Ordinal)) {
                    $end = $offset + $original.Length
                    $selection = $pattern.GetSelection()
                    if ($selection.Length -eq 1) {
                        $caretRange = $pattern.DocumentRange.Clone()
                        $caretRange.MoveEndpointByRange([System.Windows.Automation.Text.TextPatternRangeEndpoint]::End,
                            $selection[0], [System.Windows.Automation.Text.TextPatternRangeEndpoint]::Start)
                        $beforeCaret = Normalize-Text ($caretRange.GetText(8193))
                        if ($beforeCaret.Length -ge $offset -and $beforeCaret.Length -le $end) {
                            $identity = $focused.GetRuntimeId() -join ','
                        }
                    }
                }
            }
        }
    } catch {
        $reason = 'uia_error'
    }
    if ($identity) { break }
    Start-Sleep -Milliseconds 160
} while ($watch.ElapsedMilliseconds -lt 2400)
if (-not $identity) { Write-Status $reason; exit }
$left = $baseline.Substring(0, $offset)
$right = $baseline.Substring($end)

$selected = ''
$last = $baseline
$stable = 0
$deadline = $watch.ElapsedMilliseconds + [long]START_GRACE_MS
while ($watch.ElapsedMilliseconds -lt $deadline) {
    $now = [System.Windows.Automation.AutomationElement]::FocusedElement
    if ($null -eq $now -or ($now.GetRuntimeId() -join ',') -cne $identity) {
        Start-Sleep -Milliseconds 180
        continue
    }
    $current = Normalize-Text ($pattern.DocumentRange.GetText(8193))
    if ($current.Length -ge 8193) { exit }
    if ($current -ceq $baseline) {
        $ranges = $pattern.GetSelection()
        if ($ranges.Length -eq 1) {
            $sample = $ranges[0].GetText(25)
            if ($sample.Length -ge 2 -and $sample.Length -le 24 -and
                $sample -cne $selected -and $original.Contains($sample)) {
                $selected = $sample
                $deadline = [Math]::Min([long]MAX_WATCH_MS, $watch.ElapsedMilliseconds + EDIT_GRACE_MS)
            }
        }
        $stable = 0
    } elseif ($current -ceq $last) {
        $stable++
        if ($stable -eq 6) {
            $changed = $current.Substring($left.Length, $current.Length - $left.Length - $right.Length)
            # 删除和输入法选字可能产生过渡文本，由调用方校验。继续等待下一次编辑。
            [Console]::WriteLine((@{ edited = $changed; selected = $selected } | ConvertTo-Json -Compress))
        }
    } else {
        if ($current.Length -lt ($left.Length + $right.Length) -or
            -not $current.StartsWith($left, [StringComparison]::Ordinal) -or
            -not $current.EndsWith($right, [StringComparison]::Ordinal)) {
            Write-Status 'outside_dictation'
            exit
        }
        $deadline = [Math]::Min([long]MAX_WATCH_MS, $watch.ElapsedMilliseconds + EDIT_GRACE_MS)
        $stable = 0
    }
    $last = $current
    Start-Sleep -Milliseconds 180
}
"""


def edited_word(original: str, edited: str, selected_before: str = "") -> str | None:
    """选中错词后改写时，保留包括未变后缀在内的完整正确词。"""
    if not edited or original == edited:
        return None
    if selected_before and original.count(selected_before) == 1 and is_word_selection(selected_before):
        left, _, right = original.partition(selected_before)
        if edited.startswith(left) and edited.endswith(right) and len(edited) >= len(left) + len(right):
            end = len(edited) - len(right)
            word = edited[len(left):end].strip()
            if word != selected_before and is_word_selection(word):
                return word
    # 没看到选区时，只提议有空格分隔的词；连续中文句子不猜词边界。
    old_parts, new_parts = original.split(), edited.split()
    if len(old_parts) == len(new_parts) and len(old_parts) > 1:
        changes = [(a, b) for a, b in zip(old_parts, new_parts) if a != b]
        if len(changes) == 1:
            before, after = (part.strip("，。！？、；：,.!?;:") for part in changes[0])
            if before != after and is_word_selection(before) and is_word_selection(after):
                return after
    return None


def watch_edited_word(target: int, original: str, stop: threading.Event,
                      target_ok: Callable[[], bool], log: Callable[[str], None] = lambda _: None) -> str | None:
    """只有同一窗口、同一编辑控件才返回候选。停录后观察一次，可随时取消。"""
    original = original.replace("\r\n", "\n").replace("\r", "\n")
    if not target or not 2 <= len(original) <= 600 or stop.is_set():
        return None
    script = (_WATCH_EDIT.replace("TARGET_WINDOW", str(int(target)))
              .replace("START_GRACE_MS", str(START_GRACE_MS))
              .replace("EDIT_GRACE_MS", str(EDIT_GRACE_MS))
              .replace("MAX_WATCH_MS", str(MAX_WATCH_MS)))
    command = uia_command(script)
    if command is None:
        return None
    try:
        with subprocess.Popen(command, env={**os.environ, "SCRIBE_EDIT_SOURCE": original},
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              encoding="utf-8", errors="replace",
                              creationflags=subprocess.CREATE_NO_WINDOW) as process:
            lines: SimpleQueue[str | None] = SimpleQueue()

            def read() -> None:
                try:
                    for line in process.stdout:
                        lines.put(line)
                finally:
                    lines.put(None)

            reader = threading.Thread(target=read, name="scribe-edit-reader", daemon=True)
            reader.start()
            deadline = time.monotonic() + MAX_WATCH_MS / 1000 + 4
            unmatched = False
            try:
                while not stop.is_set() and time.monotonic() < deadline:
                    try:
                        line = lines.get(timeout=0.25)
                    except Empty:
                        continue
                    if line is None:
                        return None
                    data = json.loads(line)
                    if data.get("status") in {"focus_unavailable", "baseline_unmatched", "uia_error",
                                              "outside_dictation"}:
                        log("learn: edit watch " + data["status"])
                        return None
                    term = edited_word(original, data["edited"], data.get("selected", ""))
                    if term and not stop.is_set() and target_ok():
                        return term
                    if not term and not unmatched:
                        log("learn: edit watch no_bounded_word")
                        unmatched = True
            finally:
                if process.poll() is None:
                    process.kill()
                reader.join(timeout=0.5)
    except (OSError, ValueError, TypeError, KeyError):
        return None
    return None
