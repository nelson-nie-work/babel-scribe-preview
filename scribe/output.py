"""把文字送进当前聚焦的输入框: 粘贴(还原剪贴板) / 逐字输入 / 仅复制。从不发送回车。"""
import base64
import ctypes
import os
import subprocess
import time
from contextlib import contextmanager
from ctypes import wintypes
from typing import Callable, ContextManager, Optional, Protocol

from scribe.config import RESTORE_DELAY_S


class Clipboard(Protocol):
    def plain_snapshot(self) -> tuple[int, Optional[str]] | None: ...
    def set_text(self, text: Optional[str], *, expected_sequence: int | None = None) -> bool: ...
    def temporary_text(self, text: str) -> ContextManager[None]: ...


class ClipboardProtected(OSError):
    """不能安全替换当前剪贴板内容。"""


class Keys(Protocol):
    def ctrl_v(self) -> None: ...
    def type_text(self, text: str) -> None: ...


def modifier_plan(held: set, typing: bool = False):
    """按下着的修饰键 → (能否输出, 需要先模拟松开的键)。
    粘贴: Ctrl 无害(本来就要按 Ctrl+V), Shift 先松开, Alt/Win 不行。
    逐字: Ctrl 也必须真的松开 —— 按住时系统的自动重复会把 Ctrl 插进打字中间。"""
    if held & {"alt", "win"} or (typing and "ctrl" in held):
        return False, []
    return True, sorted(held & {"shift"})


def typed_text(text: str) -> str:
    """逐字输入用空格代替换行与 Tab。"""
    for a, b in (("\r\n", " "), ("\r", " "), ("\n", " "), ("\t", " ")):
        text = text.replace(a, b)
    return text


def unicode_inputs(text: str) -> list:
    """逐字输入的按键记录，每个 UTF-16 单元一按一放，始终用 KEYEVENTF_UNICODE。"""
    data = typed_text(text).encode("utf-16-le")
    recs = []
    for i in range(0, len(data), 2):
        unit = data[i] | (data[i + 1] << 8)
        recs.append((0, unit, 0x0004))            # KEYEVENTF_UNICODE
        recs.append((0, unit, 0x0004 | 0x0002))   # | KEYEVENTF_KEYUP
    return recs


def deliver(text: str, clip: Clipboard, keys: Keys, foreground_elevated: Callable[[], bool],
            sleep: Callable[[float], None] = time.sleep,
            prepare: Callable[[bool], bool] = lambda typing: True,
            target_ok: Callable[[], bool] = lambda: True, *, force_typing: bool = False) -> str:
    """默认粘贴；无法安全借用剪贴板时，改用逐字输入。"""
    if not text:
        return "empty"

    def copy_only():
        try:
            previous = clip.plain_snapshot()
            if previous is not None and clip.set_text(text, expected_sequence=previous[0]):
                return "copied_only"
        except OSError:
            pass
        return "clipboard_protected"

    if not target_ok():               # 说话时的窗口已经不在前台: 不往别处打字
        return copy_only()
    if foreground_elevated():         # 管理员窗口收不到模拟按键
        return copy_only()

    if not prepare(force_typing):
        return copy_only()
    if not target_ok():
        return copy_only()
    if force_typing:
        keys.type_text(text)
        return "typed"

    paste_attempted = False
    try:
        with clip.temporary_text(text):
            if target_ok():
                paste_attempted = True
                keys.ctrl_v()
                sleep(RESTORE_DELAY_S)
                return "pasted"
    except ClipboardProtected:
        if (not paste_attempted and target_ok() and prepare(True)
                and target_ok() and not foreground_elevated()):
            keys.type_text(text)
            return "typed"
        return "clipboard_protected"
    return copy_only()


# ---------------------------------------------------------------- Win32 适配

_CF_UNICODETEXT = 13
_PLAIN_TEXT_FORMATS = {1, 7, 13, 16}   # CF_TEXT, CF_OEMTEXT, CF_UNICODETEXT, CF_LOCALE
_RESTORABLE_FORMATS = _PLAIN_TEXT_FORMATS | {8, 15, 17}  # CF_DIB, CF_HDROP, CF_DIBV5
_RESTORABLE_NAMES = {"PNG", "JFIF", "GIF", "HTML Format", "Rich Text Format", "Preferred DropEffect",
                     "text/markdown", "application/vnd.oasis.opendocument.text",
                     "CanUploadToCloudClipboard", "CanIncludeInClipboardHistory",
                     "ExcludeClipboardContentFromMonitorProcessing"}
_OLE_BOOKKEEPING = {"DataObject", "Ole Private Data"}
_MAX_SNAPSHOT_BYTES = 128 * 1024 * 1024
_GMEM_MOVEABLE = 0x0002

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_u32 = ctypes.WinDLL("user32", use_last_error=True)
_adv = ctypes.WinDLL("advapi32", use_last_error=True)
_ole = ctypes.WinDLL("ole32")
_ole.OleInitialize.argtypes = [ctypes.c_void_p]
_ole.OleGetClipboard.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
_ole.OleUninitialize.restype = None
_ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
_ole.CoTaskMemFree.restype = None

_k32.GlobalAlloc.restype = wintypes.HGLOBAL
_k32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
_k32.GlobalLock.restype = wintypes.LPVOID
_k32.GlobalLock.argtypes = [wintypes.HGLOBAL]
_k32.GlobalSize.restype = ctypes.c_size_t
_k32.GlobalSize.argtypes = [wintypes.HGLOBAL]
_k32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
_k32.GlobalFree.argtypes = [wintypes.HGLOBAL]
_k32.OpenProcess.restype = wintypes.HANDLE
_k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_k32.GetCurrentProcess.restype = wintypes.HANDLE
_k32.CloseHandle.argtypes = [wintypes.HANDLE]
_k32.QueryFullProcessImageNameW.restype = wintypes.BOOL
_k32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
]
_u32.OpenClipboard.argtypes = [wintypes.HWND]
_u32.GetClipboardOwner.restype = wintypes.HWND
_u32.GetClipboardSequenceNumber.restype = wintypes.DWORD
_u32.GetClipboardData.restype = wintypes.HANDLE
_u32.GetClipboardData.argtypes = [wintypes.UINT]
_u32.SetClipboardData.restype = wintypes.HANDLE
_u32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
_u32.EnumClipboardFormats.restype = wintypes.UINT
_u32.EnumClipboardFormats.argtypes = [wintypes.UINT]
_u32.GetClipboardFormatNameW.argtypes = [wintypes.UINT, wintypes.LPWSTR, ctypes.c_int]
_u32.GetForegroundWindow.restype = wintypes.HWND
_u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_u32.GetAsyncKeyState.restype = ctypes.c_short
_u32.GetAsyncKeyState.argtypes = [ctypes.c_int]
_adv.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
_adv.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID,
                                     wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]


class _Opened:
    """OpenClipboard 的 with 包装; 粘贴后目标程序与剪贴板管理器会短暂占用, 重试 10×50 ms。"""

    def __init__(self, owner_window=None):
        self.owner_window = owner_window

    def __enter__(self):
        for _ in range(10):
            if _u32.OpenClipboard(self.owner_window):
                return self
            time.sleep(0.05)
        raise OSError("剪贴板被占用")

    def __exit__(self, *exc):
        _u32.CloseClipboard()


class _FormatEtc(ctypes.Structure):
    _fields_ = [("cfFormat", wintypes.WORD), ("ptd", ctypes.c_void_p),
                ("dwAspect", wintypes.DWORD), ("lindex", wintypes.LONG), ("tymed", wintypes.DWORD)]


def _com_method(obj, index, result, *args):
    table = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(result, ctypes.c_void_p, *args)(table[index])


def _ole_clipboard_is_empty() -> bool:
    """只枚举可读取的数据格式。失败或存在任意内容时保留原剪贴板。"""
    if _ole.OleInitialize(None) < 0:
        return False
    data, enumerator = ctypes.c_void_p(), ctypes.c_void_p()
    item = _FormatEtc()
    try:
        if _ole.OleGetClipboard(ctypes.byref(data)) != 0 or not data:
            return False
        enum_formats = _com_method(data, 8, ctypes.c_long, wintypes.DWORD,
                                   ctypes.POINTER(ctypes.c_void_p))  # IDataObject::EnumFormatEtc
        if enum_formats(data, 1, ctypes.byref(enumerator)) != 0 or not enumerator:  # DATADIR_GET
            return False
        next_format = _com_method(enumerator, 3, ctypes.c_long, wintypes.ULONG,
                                  ctypes.POINTER(_FormatEtc), ctypes.POINTER(wintypes.ULONG))
        fetched = wintypes.ULONG()
        result = next_format(enumerator, 1, ctypes.byref(item), ctypes.byref(fetched))
        return result == 1 and fetched.value == 0  # S_FALSE: 已枚举完毕。
    finally:
        if item.ptd:
            _ole.CoTaskMemFree(item.ptd)
        for obj in (enumerator, data):
            if obj:
                _com_method(obj, 2, wintypes.ULONG)(obj)  # IUnknown::Release
        _ole.OleUninitialize()


class WinClipboard:
    def __init__(self, owner_window=None):
        self._owner_window = owner_window

    @staticmethod
    def _non_text_open():
        fmt = _u32.EnumClipboardFormats(0)
        while fmt:
            if fmt not in _PLAIN_TEXT_FORMATS:
                return True
            fmt = _u32.EnumClipboardFormats(fmt)
        return False

    def has_non_text(self) -> bool:
        try:
            with _Opened():
                return self._non_text_open()
        except OSError:
            return True   # 读不了就当有重要内容, 走逐字输入, 不冒险覆盖

    @staticmethod
    def _text_open() -> Optional[str]:
        h = _u32.GetClipboardData(_CF_UNICODETEXT)
        if not h:
            return None
        p = _k32.GlobalLock(h)
        if not p:
            raise OSError("剪贴板文字无法读取")
        try:
            return ctypes.wstring_at(p)
        finally:
            _k32.GlobalUnlock(h)

    def get_text(self) -> Optional[str]:
        with _Opened():
            return self._text_open()

    def sequence(self) -> int:
        return _u32.GetClipboardSequenceNumber()

    def plain_snapshot(self) -> tuple[int, Optional[str]] | None:
        with _Opened():
            if self._non_text_open():
                return None
            text = self._text_open()
            if text is None and _u32.EnumClipboardFormats(0):
                raise OSError("剪贴板文字无法备份")
            return self.sequence(), text

    def selection_snapshot(self, target: int, previous_sequence: int) -> tuple[int, Optional[str]] | None:
        with _Opened():
            if self.sequence() == previous_sequence:
                return None
            owner = _u32.GetClipboardOwner()
            target_pid = _window_pid(target)
            if not target_pid or not owner or _window_pid(owner) != target_pid:
                return None
            text = self._text_open()
            return self.sequence(), text

    @staticmethod
    def _allocate(data: bytes):
        handle = _k32.GlobalAlloc(_GMEM_MOVEABLE, len(data))
        if not handle:
            raise OSError("GlobalAlloc 失败")
        pointer = _k32.GlobalLock(handle)
        if not pointer:
            _k32.GlobalFree(handle)
            raise OSError("GlobalLock 失败")
        try:
            ctypes.memmove(pointer, data, len(data))
        finally:
            _k32.GlobalUnlock(handle)
        return handle

    @staticmethod
    def _snapshot_open(backup: dict[int, int]) -> bool:
        """备份可恢复的格式，返回是否仅有待核实的 OLE 系统标记。"""
        formats = []
        fmt = _u32.EnumClipboardFormats(0)
        while fmt:
            formats.append(fmt)
            fmt = _u32.EnumClipboardFormats(fmt)
        size = 0
        has_ole_metadata = False
        for fmt in formats:
            if fmt == 2 and (8 in formats or 17 in formats):
                continue  # Windows 可从 DIB 重建 CF_BITMAP。
            name = ctypes.create_unicode_buffer(128)
            if fmt >= 0xC000:
                _u32.GetClipboardFormatNameW(fmt, name, len(name))
            if name.value in _OLE_BOOKKEEPING:
                has_ole_metadata = True
                continue
            if fmt not in _RESTORABLE_FORMATS and name.value not in _RESTORABLE_NAMES:
                raise ClipboardProtected("剪贴板含无法安全还原的格式")
            handle = _u32.GetClipboardData(fmt)
            length = _k32.GlobalSize(handle) if handle else 0
            size += length
            if not length or size > _MAX_SNAPSHOT_BYTES:
                raise ClipboardProtected("剪贴板无法安全备份")
            pointer = _k32.GlobalLock(handle)
            if not pointer:
                raise ClipboardProtected("剪贴板数据无法读取")
            try:
                backup[fmt] = WinClipboard._allocate(ctypes.string_at(pointer, length))
            finally:
                _k32.GlobalUnlock(handle)
        return has_ole_metadata and not backup

    @staticmethod
    def _restore_open(backup: dict[int, int]) -> None:
        for fmt, handle in list(backup.items()):
            if not _u32.SetClipboardData(fmt, handle):
                raise OSError("剪贴板还原失败")
            del backup[fmt]  # 已移交给 Windows。

    def _write_temporary_open(self, text: str, backup: dict[int, int]) -> int:
        handle = self._allocate(text.encode("utf-16-le") + b"\x00\x00")
        try:
            if not _u32.EmptyClipboard():
                raise ClipboardProtected("剪贴板无法替换")
            written = _u32.SetClipboardData(_CF_UNICODETEXT, handle)
            if not written:
                self._restore_open(backup)
                raise ClipboardProtected("剪贴板无法写入临时文字")
            handle = None
            return written
        finally:
            if handle is not None:
                _k32.GlobalFree(handle)

    @contextmanager
    def temporary_text(self, text: str):
        backup = {}
        try:
            try:
                with _Opened(self._owner_window):
                    ole_only = self._snapshot_open(backup)
                    if ole_only:
                        sequence = self.sequence()
                    else:
                        temporary = self._write_temporary_open(text, backup)
                if ole_only:
                    # OLE 会自行打开剪贴板。重新打开后确认期间没有新的复制操作。
                    if not _ole_clipboard_is_empty():
                        raise ClipboardProtected("剪贴板对象需要原应用提供")
                    with _Opened(self._owner_window):
                        if self.sequence() != sequence:
                            raise ClipboardProtected("剪贴板内容已变化")
                        temporary = self._write_temporary_open(text, backup)
                # Windows can synthesize standard text formats when CloseClipboard runs.
                # Verify ownership and the exact payload before taking the new baseline.
                with _Opened():
                    if (_u32.GetClipboardOwner() != self._owner_window
                            or _u32.GetClipboardData(_CF_UNICODETEXT) != temporary):
                        raise ClipboardProtected("剪贴板内容已变化")
                    sequence = self.sequence()
            except OSError as exc:
                raise ClipboardProtected("剪贴板未能安全替换") from exc
            try:
                yield
            finally:
                with _Opened(self._owner_window):
                    if self.sequence() == sequence and _u32.EmptyClipboard():
                        self._restore_open(backup)
        finally:
            for saved in backup.values():
                _k32.GlobalFree(saved)

    def set_text(self, text: Optional[str], *, expected_sequence: int | None = None) -> bool:
        data = None if text is None else text.encode("utf-16-le") + b"\x00\x00"
        h = None
        if data is not None:
            h = self._allocate(data)
        try:
            with _Opened(self._owner_window):
                if expected_sequence is not None and self.sequence() != expected_sequence:
                    return False
                if not _u32.EmptyClipboard():
                    raise OSError("EmptyClipboard 失败")
                if h is not None:
                    if not _u32.SetClipboardData(_CF_UNICODETEXT, h):
                        raise OSError("SetClipboardData 失败")
                    h = None          # 成功后归系统所有
                return True
        finally:
            if h is not None:
                _k32.GlobalFree(h)


# SendInput 结构体(联合体须含最大的 MOUSEINPUT, 否则 cbSize 不对, 调用静默失败)
class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


_u32.SendInput.restype = wintypes.UINT
_u32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int]


def _send(recs: list) -> None:
    """recs: [(vk, scan, flags)], 每批 64 条。"""
    for i in range(0, len(recs), 64):
        batch = recs[i:i + 64]
        arr = (_INPUT * len(batch))()
        for j, (vk, scan, flags) in enumerate(batch):
            arr[j].type = 1   # INPUT_KEYBOARD
            arr[j].u.ki = _KEYBDINPUT(vk, scan, flags, 0, 0)
        if _u32.SendInput(len(batch), arr, ctypes.sizeof(_INPUT)) != len(batch):
            raise OSError("SendInput 被拦截")


_VK_CONTROL, _VK_C, _VK_V = 0x11, 0x43, 0x56
_MOD_VKS = {"ctrl": (0xA2, 0xA3), "shift": (0xA0, 0xA1), "alt": (0xA4, 0xA5), "win": (0x5B, 0x5C)}


class WinKeys:
    def ctrl_c(self) -> None:
        _send([(_VK_CONTROL, 0, 0), (_VK_C, 0, 0), (_VK_C, 0, 2), (_VK_CONTROL, 0, 2)])

    def ctrl_v(self) -> None:
        _send([(_VK_CONTROL, 0, 0), (_VK_V, 0, 0), (_VK_V, 0, 2), (_VK_CONTROL, 0, 2)])

    def type_text(self, text: str) -> None:
        _send(unicode_inputs(text))


def held_modifiers() -> set:
    return {name for name, vks in _MOD_VKS.items()
            if any(_u32.GetAsyncKeyState(vk) & 0x8000 for vk in vks)}


def read_selection(clip: WinClipboard, keys: WinKeys, *, target: int,
                   target_ok: Callable[[], bool] = lambda: True,
                   modifiers: Callable[[], set] = held_modifiers,
                   sleep: Callable[[float], None] = time.sleep) -> Optional[str]:
    """获取目标窗口的选中文字。跳过富剪贴板，避免读取旧值或还原覆盖他处新复制。"""
    try:
        for _ in range(20):
            if not target_ok():
                return None
            if not modifiers():
                break
            sleep(0.025)
        else:
            return None

        previous = clip.plain_snapshot()
        if previous is None or not target_ok() or modifiers():
            return None
        sequence, backup = previous
        if clip.sequence() != sequence:
            return None
        keys.ctrl_c()
        for _ in range(25):
            if clip.sequence() != sequence:
                copied = clip.selection_snapshot(target, sequence)
                if copied is None:
                    return None
                copy_sequence, selected = copied
                if clip.set_text(backup, expected_sequence=copy_sequence) and target_ok():
                    return selected
                return None
            sleep(0.03)
    except OSError:
        return None
    return None


_UIA_SELECTION = """
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Add-Type -AssemblyName UIAutomationClient
$focused = [System.Windows.Automation.AutomationElement]::FocusedElement
$element = $focused
$walker = [System.Windows.Automation.TreeWalker]::RawViewWalker
for ($i = 0; $null -ne $element -and $i -lt 32; $i++) {
    if ($element.Current.NativeWindowHandle -eq TARGET_WINDOW) {
        $pattern = $null
        if ($focused.TryGetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern, [ref]$pattern)) {
            $ranges = $pattern.GetSelection()
            if ($ranges.Length -eq 1) {
                [Console]::Write($ranges[0].GetText(2001))
            }
        }
        exit
    }
    $element = $walker.GetParent($element)
}
"""


def uia_command(script: str) -> list[str] | None:
    command = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    system_root = os.environ.get("SystemRoot")
    if not system_root:
        return None
    powershell = os.path.join(system_root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    return [powershell, "-NoProfile", "-NonInteractive", "-EncodedCommand", command]


def read_accessible_selection(target: int, target_ok: Callable[[], bool]) -> Optional[str]:
    """从支持 Windows 文本选区的编辑器读取选中的文字，不碰剪贴板。"""
    if not target or not target_ok():
        return None
    command = uia_command(_UIA_SELECTION.replace("TARGET_WINDOW", str(int(target))))
    if command is None:
        return None
    try:
        result = subprocess.run(
            command,
            capture_output=True, encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW, timeout=2.5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = result.stdout.strip()
    if result.returncode == 0 and 0 < len(text) <= 2000 and target_ok():
        return text
    return None


def prepare_modifiers(typing: bool, sleep: Callable[[float], None] = time.sleep) -> bool:
    """等修饰键到可输出状态(粘贴最多 1 秒; 逐字最多 30 秒 —— 用户可能正按着右 Ctrl 说下一句),
    再模拟松开 Shift。等不到返回 False。"""
    deadline = time.monotonic() + (30.0 if typing else 1.0)
    while True:
        ok, release = modifier_plan(held_modifiers(), typing)
        if ok:
            recs = [(vk, 0, 2) for name in release for vk in _MOD_VKS[name]
                    if _u32.GetAsyncKeyState(vk) & 0x8000]
            if recs:
                _send(recs)
            return True
        if time.monotonic() >= deadline:
            return False
        sleep(0.05)


def foreground_window() -> int:
    return int(_u32.GetForegroundWindow() or 0)


def _window_pid(window: int) -> int:
    pid = wintypes.DWORD()
    _u32.GetWindowThreadProcessId(window, ctypes.byref(pid))
    return pid.value


def window_application(window: int) -> str:
    """进程文件名用于历史和本机逐应用输出规则。"""
    if not window or not (pid := _window_pid(window)):
        return ""
    process = _k32.OpenProcess(0x1000, False, pid)
    if not process:
        return ""
    try:
        length = wintypes.DWORD(32768)
        filename = ctypes.create_unicode_buffer(length.value)
        if _k32.QueryFullProcessImageNameW(process, 0, filename, ctypes.byref(length)):
            return os.path.basename(filename.value).casefold()
        return ""
    finally:
        _k32.CloseHandle(process)


def _token_elevated(process_handle) -> Optional[bool]:
    tok = wintypes.HANDLE()
    if not _adv.OpenProcessToken(process_handle, 0x0008, ctypes.byref(tok)):   # TOKEN_QUERY
        return None
    try:
        val, ret = wintypes.DWORD(), wintypes.DWORD()
        if not _adv.GetTokenInformation(tok, 20, ctypes.byref(val), 4, ctypes.byref(ret)):  # TokenElevation
            return None
        return bool(val.value)
    finally:
        _k32.CloseHandle(tok)


def foreground_elevated() -> bool:
    """前台窗口属于提权进程而本进程未提权 → True(模拟按键会被 Windows 拦掉)。"""
    if _token_elevated(_k32.GetCurrentProcess()):
        return False
    pid = _window_pid(foreground_window())
    if not pid:
        return False
    h = _k32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return True
    try:
        elevated = _token_elevated(h)
        return True if elevated is None else elevated
    finally:
        _k32.CloseHandle(h)
