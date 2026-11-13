from scribe.capsule import CapsuleModel


def test_done_auto_hides_after_600ms():
    m = CapsuleModel()
    m.listening(0.0)
    m.processing()
    m.done()
    assert m.state == "done" and m.hide_after_ms == 600


def test_error_carries_message_and_hides_after_2s():
    m = CapsuleModel()
    m.error("未检测到麦克风")
    assert m.message == "未检测到麦克风" and m.hide_after_ms == 2000


def test_timer_text():
    m = CapsuleModel()
    m.listening(10.0)
    assert m.timer_text(14.2) == "0:04" and m.timer_text(75.0) == "1:05"


def test_levels_keep_last_nine_normalized():
    m = CapsuleModel()
    m.listening(0.0)
    for _ in range(12):
        m.push_level(0.04)
    assert len(m.bars) == 9 and m.bars[-1] == 0.5


def test_lock_flag_resets_on_new_listening():
    m = CapsuleModel()
    m.listening(0.0)
    m.lock()
    assert m.locked
    m.done()
    m.listening(5.0)
    assert not m.locked


def test_window_never_takes_focus(qapp_offscreen):
    from PySide6.QtCore import Qt

    from scribe.capsule import CapsuleWindow

    w = CapsuleWindow(CapsuleModel())
    assert w.windowFlags() & Qt.WindowDoesNotAcceptFocus
    assert w.testAttribute(Qt.WA_ShowWithoutActivating)


def test_preview_only_while_listening():
    m = CapsuleModel()
    m.set_preview("你好")
    assert m.preview == ""
    m.listening(0.0)
    m.set_preview("你好")
    assert m.preview == "你好"
    m.processing()
    assert m.preview == "你好"
    m.done()
    assert m.preview == ""
    m.listening(1.0)
    m.set_preview("再来")
    m.hide()
    assert m.preview == ""
    m.listening(2.0)
    m.set_preview("再来")
    m.error("x")
    assert m.preview == ""
    m.set_preview("迟到")
    assert m.preview == ""


def test_polishing_keeps_preview():
    m = CapsuleModel()
    m.listening(0.0)
    m.set_preview("你好")
    m.polishing()
    assert (m.state, m.message, m.preview) == ("processing", "整理中", "你好")


def test_listening_clears_previous_preview():
    m = CapsuleModel()
    m.listening(0.0)
    m.set_preview("旧的")
    m.processing()
    m.listening(1.0)
    assert m.preview == ""


def test_window_grows_with_preview_and_caps_at_520(qapp_offscreen):
    from scribe.capsule import CapsuleWindow

    m = CapsuleModel()
    w = CapsuleWindow(m)
    m.listening(0.0)
    w.refresh()
    base = w.width()
    m.set_preview("帮我把")
    w.refresh()
    assert w.width() > base
    m.set_preview("…" + "很长的一段话" * 10)
    w.refresh()
    assert w.width() == 520
    m.processing()
    w.refresh()
    assert w.width() <= 520
    w.hide()


def test_polishing_with_preview_still_shows_label(qapp_offscreen):
    from PySide6.QtGui import QFontMetrics

    from scribe.capsule import CapsuleWindow

    m = CapsuleModel()
    w = CapsuleWindow(m)
    m.listening(0.0)
    m.set_preview("帮我把")
    m.polishing()
    label = QFontMetrics(w._font).horizontalAdvance("整理中")
    assert w._fixed_width() >= 22 + label
    w.refresh()
    with_preview = w.width()
    m.preview = ""
    w.refresh()
    assert with_preview > w.width()
    w.hide()
