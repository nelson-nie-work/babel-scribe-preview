from contextlib import contextmanager
import ctypes
from types import SimpleNamespace

from scribe import output
from scribe.output import (ClipboardProtected, deliver, modifier_plan, read_selection,
                           read_selection_full, unicode_inputs)


class FakeClip:
    def __init__(self, text=None, non_text=False, fail_set=(), fail_get=False):
        self.text = text
        self.non_text = non_text
        self.history = []          # 每次成功 set_text 的参数
        self.fail_set = set(fail_set)   # 第几次 set_text 抛 OSError (从 0 数)
        self.fail_get = fail_get
        self._n = 0

    def has_non_text(self):
        return self.non_text

    def get_text(self):
        if self.fail_get:
            raise OSError("剪贴板被占用")
        return self.text

    def set_text(self, text, *, expected_sequence=None):
        if expected_sequence is not None and expected_sequence != self._n:
            return False
        n, self._n = self._n, self._n + 1
        if n in self.fail_set:
            raise OSError("剪贴板被占用")
        self.history.append(text)
        self.text = text
        return True

    def plain_snapshot(self):
        return None if self.non_text else (self._n, self.get_text())

    @contextmanager
    def temporary_text(self, text):
        try:
            backup = self.get_text()
            self.set_text(text)
        except OSError as exc:
            raise ClipboardProtected() from exc
        sequence = self._n
        try:
            yield
        finally:
            self.set_text(backup, expected_sequence=sequence)


class FakeKeys:
    def __init__(self, fail_paste=False):
        self.calls = []
        self.fail_paste = fail_paste

    def ctrl_v(self):
        self.calls.append("ctrl_v")
        if self.fail_paste:
            raise OSError("SendInput 失败")

    def type_text(self, text):
        self.calls.append(("type", text))


def no_sleep(_s):
    pass


def test_paste_restores_previous_text():
    c, k = FakeClip(text="旧内容"), FakeKeys()
    assert deliver("你好", c, k, lambda: False, sleep=no_sleep) == "pasted"
    assert c.history == ["你好", "旧内容"] and k.calls == ["ctrl_v"]


def test_paste_restores_empty_clipboard():
    c = FakeClip(text=None)
    deliver("你好", c, FakeKeys(), lambda: False, sleep=no_sleep)
    assert c.history == ["你好", None]


def test_default_pastes_even_when_clipboard_has_supported_non_text():
    c, k = FakeClip(text="截图数据", non_text=True), FakeKeys()
    assert deliver("你好", c, k, lambda: False, sleep=no_sleep) == "pasted"
    assert c.history == ["你好", "截图数据"] and k.calls == ["ctrl_v"]


def test_explicit_app_rule_types_without_touching_clipboard():
    k = FakeKeys()
    assert deliver("你好", None, k, lambda: False, force_typing=True) == "typed"
    assert k.calls == [("type", "你好")]


def test_focus_changes_while_waiting_for_modifier_keys():
    c, k = FakeClip(text="旧"), FakeKeys()
    focus = iter((True, False)).__next__
    assert deliver("你好", c, k, lambda: False, target_ok=focus) == "copied_only"
    assert k.calls == [] and c.text == "你好"


def test_clipboard_is_not_restored_over_a_new_user_copy():
    c, k = FakeClip(text="旧"), FakeKeys()
    assert deliver("你好", c, k, lambda: False, sleep=lambda _wait: c.set_text("刚复制")) == "pasted"
    assert c.text == "刚复制"


def test_winclipboard_restores_images_and_only_replaces_safe_ole_clipboard(monkeypatch):
    clip = output.WinClipboard(42)
    dib, png = b"fixture DIB data", b"fixture PNG data"
    png_fmt, ole_fmt, unknown_fmt = 0xC101, 0xC102, 0xC103
    markdown_fmt, odt_fmt = 0xC104, 0xC105
    names = {png_fmt: "PNG", ole_fmt: "Ole Private Data", unknown_fmt: "Private format",
             markdown_fmt: "text/markdown", odt_fmt: "application/vnd.oasis.opendocument.text"}
    values = {8: clip._allocate(dib), png_fmt: clip._allocate(png), ole_fmt: clip._allocate(b"OLE")}
    sequence = 0
    copy_on_close = False

    def empty():
        nonlocal sequence
        for handle in values.values():
            output._k32.GlobalFree(handle)
        values.clear()
        sequence += 1
        return True

    def set_format(fmt, handle):
        nonlocal sequence
        values[fmt] = handle
        sequence += 1
        return handle

    def enumerate_formats(previous):
        keys = list(values)
        return next(iter(keys), 0) if not previous else next(iter(keys[keys.index(previous) + 1:]), 0)

    def get_name(fmt, buffer, _size):
        buffer.value = names.get(fmt, "")
        return len(buffer.value)

    @contextmanager
    def opened(_owner=None):
        nonlocal sequence
        yield
        if _owner is not None and list(values) == [output._CF_UNICODETEXT]:
            sequence += 3  # Windows synthesizes text formats when the clipboard is closed.
            if copy_on_close:
                replacement = clip._allocate("另一份\0".encode("utf-16-le"))
                empty()
                set_format(output._CF_UNICODETEXT, replacement)

    monkeypatch.setattr(output, "_Opened", opened)
    monkeypatch.setattr(output, "_u32", SimpleNamespace(
        GetClipboardData=values.get, EnumClipboardFormats=enumerate_formats,
        EmptyClipboard=empty, SetClipboardData=set_format, GetClipboardFormatNameW=get_name,
        GetClipboardOwner=lambda: 42,
        GetClipboardSequenceNumber=lambda: sequence,
    ))

    def read_format(fmt, size):
        handle = values[fmt]
        pointer = output._k32.GlobalLock(handle)
        try:
            return ctypes.string_at(pointer, size)
        finally:
            output._k32.GlobalUnlock(handle)

    def read_text():
        with output._Opened():
            return clip._text_open()

    try:
        keys = FakeKeys()
        assert deliver("临时文字", clip, keys, lambda: False, sleep=no_sleep) == "pasted"
        assert keys.calls == ["ctrl_v"]
        assert read_format(8, len(dib)) == dib and read_format(png_fmt, len(png)) == png
        for fmt, payload in ((markdown_fmt, b"# text\0"), (odt_fmt, b"PK\x03\x04\0")):
            set_format(fmt, clip._allocate(payload))
        assert deliver("格式兼容", clip, keys, lambda: False, sleep=no_sleep) == "pasted"
        assert read_format(markdown_fmt, 7) == b"# text\0"
        assert read_format(odt_fmt, 5) == b"PK\x03\x04\0"
        keys.calls.clear()
        set_format(unknown_fmt, clip._allocate(b"original"))      # 如 Chrome 私有格式: 备份后照常粘贴
        assert deliver("私有格式", clip, keys, lambda: False, sleep=no_sleep) == "pasted"
        assert read_format(unknown_fmt, 8) == b"original" and keys.calls == ["ctrl_v"]
        keys.calls.clear()
        metafile = 14                                              # CF_ENHMETAFILE: GDI 句柄, 无法复制
        set_format(metafile, clip._allocate(b"EMF"))
        assert deliver("不能覆盖", clip, keys, lambda: False, sleep=no_sleep) == "typed"
        assert read_format(metafile, 3) == b"EMF" and keys.calls == [("type", "不能覆盖")]
        keys.calls.clear()

        empty()
        set_format(ole_fmt, clip._allocate(b"OLE"))
        monkeypatch.setattr(output, "_ole_clipboard_is_empty", lambda: False)
        assert deliver("不能覆盖", clip, keys, lambda: False, prepare=lambda typing: not typing) == "clipboard_protected"
        assert read_format(ole_fmt, 3) == b"OLE"

        def copy_during_ole_probe():
            clip.set_text("新复制")
            return True

        monkeypatch.setattr(output, "_ole_clipboard_is_empty", copy_during_ole_probe)
        assert deliver("不能覆盖", clip, keys, lambda: False, prepare=lambda typing: not typing) == "clipboard_protected"
        assert read_text() == "新复制"

        empty()
        set_format(ole_fmt, clip._allocate(b"OLE"))
        monkeypatch.setattr(output, "_ole_clipboard_is_empty", lambda: True)
        assert deliver("正常粘贴", clip, keys, lambda: False, sleep=no_sleep) == "pasted"
        assert keys.calls == ["ctrl_v"] and not values

        clip.set_text("原内容")
        copy_on_close = True
        assert deliver("不能覆盖", clip, keys, lambda: False, sleep=no_sleep) == "typed"
        assert read_text() == "另一份"
        assert keys.calls == ["ctrl_v", ("type", "不能覆盖")]
    finally:
        empty()


def test_elevated_window_copies_only():
    c, k = FakeClip(text="旧"), FakeKeys()
    assert deliver("你好", c, k, lambda: True, sleep=no_sleep) == "copied_only"
    assert c.history == ["你好"] and k.calls == []


def test_empty_text_does_nothing():
    c, k = FakeClip(text="旧"), FakeKeys()
    assert deliver("", c, k, lambda: False) == "empty"
    assert c.history == [] and k.calls == []


def test_restore_waits_restore_delay():
    waits = []
    deliver("你好", FakeClip(text="x"), FakeKeys(), lambda: False, sleep=waits.append)
    assert waits == [0.3]


def test_unicode_inputs_never_send_virtual_keys():
    recs = unicode_inputs("ab 1,中")
    assert len(recs) == 2 * 6
    assert all(vk == 0 and flags & 0x4 for vk, _scan, flags in recs)          # 全部 KEYEVENTF_UNICODE
    assert [scan for _vk, scan, flags in recs if not flags & 0x2] == [ord(c) for c in "ab 1,中"]


def test_unicode_inputs_turn_newlines_and_tabs_into_spaces():
    text = "a" + chr(13) + chr(10) + "b" + chr(9) + "c"     # a\r\nb\tc
    downs = [scan for _vk, scan, flags in unicode_inputs(text) if not flags & 0x2]
    assert downs == [ord(c) for c in "a b c"]


def test_unicode_inputs_surrogate_pair():
    downs = [scan for _vk, scan, flags in unicode_inputs("😀") if not flags & 0x2]
    assert downs == [0xD83D, 0xDE00]


def test_modifier_plan_paste_tolerates_ctrl_releases_shift():
    assert modifier_plan({"ctrl", "shift"}) == (True, ["shift"])
    assert modifier_plan(set()) == (True, [])


def test_modifier_plan_typing_needs_ctrl_up():
    assert modifier_plan({"ctrl"}, typing=True) == (False, [])
    assert modifier_plan({"shift"}, typing=True) == (True, ["shift"])
    assert modifier_plan(set(), typing=True) == (True, [])


def test_modifier_plan_refuses_when_alt_or_win_held():
    assert modifier_plan({"alt"}) == (False, [])
    assert modifier_plan({"win", "ctrl"}) == (False, [])


def test_modifiers_not_ready_copies_only():
    c, k = FakeClip(text="旧"), FakeKeys()
    assert deliver("你好", c, k, lambda: False, sleep=no_sleep, prepare=lambda typing: False) == "copied_only"
    assert k.calls == []


def test_focus_moved_copies_only():
    c, k = FakeClip(text="旧"), FakeKeys()
    assert deliver("你好", c, k, lambda: False, sleep=no_sleep, target_ok=lambda: False) == "copied_only"
    assert k.calls == [] and c.history == ["你好"]


def test_clipboard_set_failure_types_without_replacing_original():
    c, k = FakeClip(text="旧", fail_set={0}), FakeKeys()
    assert deliver("你好", c, k, lambda: False, sleep=no_sleep) == "typed"
    assert k.calls == [("type", "你好")] and c.text == "旧"


def test_paste_error_still_restores_clipboard():
    import pytest
    c, k = FakeClip(text="旧"), FakeKeys(fail_paste=True)
    with pytest.raises(OSError):
        deliver("你好", c, k, lambda: False, sleep=no_sleep)
    assert c.text == "旧"


def test_unreadable_clipboard_types_after_modifiers_are_ready():
    c, k = FakeClip(text="原内容", fail_get=True), FakeKeys()
    modifier_checks = []
    assert deliver("你好", c, k, lambda: False, sleep=no_sleep,
                   prepare=lambda typing: modifier_checks.append(typing) or True) == "typed"
    assert modifier_checks == [False, True]
    assert c.history == [] and k.calls == [("type", "你好")] and c.text == "原内容"


def test_protected_clipboard_does_not_type_if_focus_moves_while_waiting():
    c, k = FakeClip(text="原内容", fail_get=True), FakeKeys()
    target_ok = True

    def prepare(typing):
        nonlocal target_ok
        if typing:
            target_ok = False
        return True

    assert deliver("你好", c, k, lambda: False, sleep=no_sleep, prepare=prepare,
                   target_ok=lambda: target_ok) == "clipboard_protected"
    assert c.history == [] and k.calls == []


def test_protected_clipboard_does_not_type_when_ctrl_cannot_be_released():
    c, k = FakeClip(text="原内容", fail_get=True), FakeKeys()
    assert deliver("你好", c, k, lambda: False, sleep=no_sleep,
                   prepare=lambda typing: not typing) == "clipboard_protected"
    assert c.history == [] and k.calls == []


class FakeSelectionClip:
    def __init__(self, non_text=False):
        self.text = "原剪贴板"
        self.version = 1
        self.owner = 0
        self.non_text = non_text

    def sequence(self):
        return self.version

    def plain_snapshot(self):
        return None if self.non_text else (self.version, self.text)

    def selection_snapshot(self, target, previous_sequence):
        if self.owner == target and self.version != previous_sequence:
            return self.version, self.text
        return None

    def set_text(self, text, *, expected_sequence):
        if self.version != expected_sequence:
            return False
        self.text = text
        self.version += 1
        return True

    def copy(self, text, owner):
        self.text = text
        self.owner = owner
        self.version += 1


class FakeCopyKeys:
    def __init__(self, copy):
        self.copy = copy
        self.calls = 0

    def ctrl_c(self):
        self.calls += 1
        self.copy()


def test_read_selection_copies_then_restores_previous_text():
    clip = FakeSelectionClip()
    keys = FakeCopyKeys(lambda: clip.copy("新桥", owner=9))
    assert read_selection(clip, keys, target=9, modifiers=set, sleep=no_sleep) == "新桥"
    assert clip.text == "原剪贴板"


def test_read_selection_does_not_reuse_stale_clipboard():
    clip = FakeSelectionClip()
    keys = FakeCopyKeys(lambda: None)
    assert read_selection(clip, keys, target=9, modifiers=set, sleep=no_sleep) is None
    assert clip.version == 1


def test_read_selection_ignores_external_copy():
    clip = FakeSelectionClip()
    keys = FakeCopyKeys(lambda: clip.copy("别处的内容", owner=10))
    assert read_selection(clip, keys, target=9, modifiers=set, sleep=no_sleep) is None
    assert clip.text == "别处的内容"


def test_read_selection_does_not_restore_over_new_clipboard_content():
    class InterruptedClip(FakeSelectionClip):
        def selection_snapshot(self, target, previous_sequence):
            snapshot = super().selection_snapshot(target, previous_sequence)
            self.copy("之后复制", owner=10)
            return snapshot

    clip = InterruptedClip()
    keys = FakeCopyKeys(lambda: clip.copy("新桥", owner=9))
    assert read_selection(clip, keys, target=9, modifiers=set, sleep=no_sleep) is None
    assert clip.text == "之后复制"


def test_read_selection_preserves_non_text_and_rechecks_target():
    clip = FakeSelectionClip(non_text=True)
    keys = FakeCopyKeys(lambda: None)
    assert read_selection(clip, keys, target=9, modifiers=set, sleep=no_sleep) is None
    clip.non_text = False
    focus = iter([True, False])
    assert read_selection(clip, keys, target=9, target_ok=lambda: next(focus), modifiers=set, sleep=no_sleep) is None
    assert keys.calls == 0


def test_accessible_selection_runs_windowless_and_checks_focus_again(monkeypatch):
    from scribe import output

    def run(command, **kwargs):
        assert command[-2] == "-EncodedCommand"
        assert kwargs["creationflags"] == output.subprocess.CREATE_NO_WINDOW
        return output.subprocess.CompletedProcess(command, 0, stdout="青砚桥")

    monkeypatch.setattr(output.subprocess, "run", run)
    assert output.read_accessible_selection(9, lambda: True) == "青砚桥"
    focus = iter([True, False])
    assert output.read_accessible_selection(9, lambda: next(focus)) is None


def test_registered_clipboard_formats_are_backed_up_but_gdi_handles_are_not():
    from scribe import output
    assert output.restorable_format(13)           # CF_UNICODETEXT
    assert output.restorable_format(8)            # CF_DIB
    assert output.restorable_format(0xC2AC)       # 注册格式, 如 Chromium Web Custom MIME Data Format
    assert not output.restorable_format(2)        # CF_BITMAP: GDI 句柄
    assert not output.restorable_format(14)       # CF_ENHMETAFILE
    assert not output.restorable_format(0x0200)   # 应用私有区段


class FakeRichClip:
    """多格式假剪贴板: formats 为 {格式号: 字节}; 格式 13 视为文字。"""

    def __init__(self, formats, protected=False):
        self.formats = dict(formats)
        self.version = 1
        self.owner = 0
        self.protected = protected

    def sequence(self):
        return self.version

    def copy(self, formats, owner):
        self.formats = dict(formats)
        self.owner = owner
        self.version += 1

    def full_snapshot(self):
        if self.protected:
            raise ClipboardProtected("不可还原")
        return self.version, dict(self.formats)

    def selection_snapshot(self, target, previous_sequence):
        if self.owner == target and self.version != previous_sequence:
            return self.version, self.formats[13].decode()
        return None

    def restore_snapshot(self, backup, *, expected_sequence):
        if self.version != expected_sequence:
            return False
        self.formats = dict(backup)
        self.version += 1
        return True

    def discard_snapshot(self, backup):
        backup.clear()


def test_full_read_restores_every_format():
    clip = FakeRichClip({13: b"old", 0xC00A: b"<html>"})
    keys = FakeCopyKeys(lambda: clip.copy({13: b"Whisper"}, owner=9))
    assert read_selection_full(clip, keys, target=9, modifiers=set, sleep=no_sleep) == "Whisper"
    assert clip.formats == {13: b"old", 0xC00A: b"<html>"}


def test_full_read_skips_copy_when_clipboard_cannot_be_restored():
    clip = FakeRichClip({2: b"bitmap"}, protected=True)
    keys = FakeCopyKeys(lambda: None)
    assert read_selection_full(clip, keys, target=9, modifiers=set, sleep=no_sleep) is None
    assert keys.calls == 0


def test_full_read_ignores_copy_from_other_window():
    clip = FakeRichClip({13: b"old"})
    keys = FakeCopyKeys(lambda: clip.copy({13: b"x"}, owner=5))
    assert read_selection_full(clip, keys, target=9, modifiers=set, sleep=no_sleep) is None


def test_full_read_does_not_restore_over_newer_copy():
    clip = FakeRichClip({13: b"old"})

    def copy_then_user_copies():
        clip.copy({13: b"sel"}, owner=9)
        clip.copy({13: b"user"}, owner=3)
    keys = FakeCopyKeys(copy_then_user_copies)
    read_selection_full(clip, keys, target=9, modifiers=set, sleep=no_sleep)
    assert clip.formats == {13: b"user"}


def test_full_read_stops_when_target_lost():
    clip = FakeRichClip({13: b"old"})
    keys = FakeCopyKeys(lambda: clip.copy({13: b"sel"}, owner=9))
    assert read_selection_full(clip, keys, target=9, target_ok=lambda: False, modifiers=set, sleep=no_sleep) is None
    assert keys.calls == 0


@contextmanager
def _dummy_opened(_owner=None):
    yield


def _win_clip_with_fakes(monkeypatch, *, sequence=5, set_ok=True, snapshot=None):
    """WinClipboard 的 Win32 层假件: 记录 GlobalFree 与 SetClipboardData, 句柄用整数代替。"""
    freed, set_calls = [], []
    monkeypatch.setattr(output, "_Opened", _dummy_opened)
    monkeypatch.setattr(output, "_k32", SimpleNamespace(GlobalFree=freed.append))

    def set_data(fmt, handle):
        set_calls.append((fmt, handle))
        return handle if set_ok else 0
    monkeypatch.setattr(output, "_u32", SimpleNamespace(
        GetClipboardSequenceNumber=lambda: sequence, EmptyClipboard=lambda: True,
        SetClipboardData=set_data))
    if snapshot is not None:
        monkeypatch.setattr(output.WinClipboard, "_snapshot_open", staticmethod(snapshot))
    return output.WinClipboard(42), freed, set_calls


def test_full_snapshot_returns_sequence_and_backup(monkeypatch):
    def snapshot(backup):
        backup[13] = 101
        return False
    clip, freed, _ = _win_clip_with_fakes(monkeypatch, snapshot=snapshot)
    assert clip.full_snapshot() == (5, {13: 101})
    assert freed == []


def test_full_snapshot_frees_handles_when_snapshot_raises(monkeypatch):
    def snapshot(backup):
        backup[13] = 101
        raise ClipboardProtected("不可还原")
    clip, freed, _ = _win_clip_with_fakes(monkeypatch, snapshot=snapshot)
    try:
        clip.full_snapshot()
        raise AssertionError("应抛 ClipboardProtected")
    except ClipboardProtected:
        pass
    assert freed == [101]


def test_full_snapshot_rejects_ole_only_clipboard(monkeypatch):
    clip, freed, _ = _win_clip_with_fakes(monkeypatch, snapshot=lambda backup: True)
    try:
        clip.full_snapshot()
        raise AssertionError("应抛 ClipboardProtected")
    except ClipboardProtected:
        pass
    assert freed == []


def test_restore_snapshot_refuses_on_sequence_mismatch_and_frees_handles(monkeypatch):
    clip, freed, set_calls = _win_clip_with_fakes(monkeypatch, sequence=6)
    backup = {13: 101, 0xC00A: 102}
    assert clip.restore_snapshot(backup, expected_sequence=5) is False
    assert set_calls == [] and sorted(freed) == [101, 102] and backup == {}


def test_restore_snapshot_hands_over_all_handles_without_freeing(monkeypatch):
    clip, freed, set_calls = _win_clip_with_fakes(monkeypatch)
    backup = {13: 101, 0xC00A: 102}
    assert clip.restore_snapshot(backup, expected_sequence=5) is True
    assert set_calls == [(13, 101), (0xC00A, 102)] and freed == [] and backup == {}


def test_restore_snapshot_frees_leftover_handle_when_set_fails(monkeypatch):
    clip, freed, set_calls = _win_clip_with_fakes(monkeypatch, set_ok=False)
    backup = {13: 101, 0xC00A: 102}
    try:
        clip.restore_snapshot(backup, expected_sequence=5)
        raise AssertionError("应抛 OSError")
    except OSError:
        pass
    assert set_calls == [(13, 101)] and sorted(freed) == [101, 102] and backup == {}


def test_full_read_drops_text_but_restores_when_target_lost_after_copy():
    clip = FakeRichClip({13: b"old", 0xC00A: b"<html>"})
    keys = FakeCopyKeys(lambda: clip.copy({13: b"sel"}, owner=9))
    answers = iter([True, True, False])  # 等待修饰键 / 复制前 / 还原后
    assert read_selection_full(clip, keys, target=9, target_ok=lambda: next(answers),
                               modifiers=set, sleep=no_sleep) is None
    assert clip.formats == {13: b"old", 0xC00A: b"<html>"}


def test_parse_editable_accepts_only_one():
    from scribe.output import _parse_editable
    assert _parse_editable("1\r\n") and _parse_editable(" 1 ")
    assert not _parse_editable("") and not _parse_editable("0") and not _parse_editable("10")


def test_selection_editable_runs_windowless_and_fails_closed(monkeypatch):
    from scribe import output
    seen = {}

    def run(command, **kwargs):
        seen["command"], seen["kwargs"] = command, kwargs
        return output.subprocess.CompletedProcess(command, 0, stdout="1\r\n")

    monkeypatch.setattr(output.subprocess, "run", run)
    assert output.selection_editable(9, lambda: True) is True
    assert seen["command"][-2] == "-EncodedCommand"
    assert seen["kwargs"]["creationflags"] == output.subprocess.CREATE_NO_WINDOW
    assert seen["kwargs"]["timeout"] == 2.5
    focus = iter([True, False])
    assert output.selection_editable(9, lambda: next(focus)) is False   # 目标中途换了
    assert output.selection_editable(0, lambda: True) is False

    def boom(command, **kwargs):
        raise output.subprocess.TimeoutExpired(command, 2.5)

    monkeypatch.setattr(output.subprocess, "run", boom)
    assert output.selection_editable(9, lambda: True) is False

    monkeypatch.setattr(output.subprocess, "run",
                        lambda command, **kwargs: output.subprocess.CompletedProcess(command, 1, stdout="1"))
    assert output.selection_editable(9, lambda: True) is False
