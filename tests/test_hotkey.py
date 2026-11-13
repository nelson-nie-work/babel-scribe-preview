from itertools import count
from types import SimpleNamespace

from scribe.hotkey import Action as A
from scribe.hotkey import HotkeyListener, HotkeyMachine


def test_hold_then_release_starts_and_stops():
    m = HotkeyMachine()
    assert m.on_trigger_down(0.0) == [A.START]
    assert m.on_trigger_up(1.0) == [A.STOP]
    assert m.state == "idle"


def test_short_press_cancels():
    m = HotkeyMachine()
    m.on_trigger_down(0.0)
    assert m.on_trigger_up(0.2) == [A.CANCEL]


def test_key_repeat_is_ignored():
    m = HotkeyMachine()
    m.on_trigger_down(0.0)
    assert m.on_trigger_down(0.05) == []
    assert m.on_trigger_up(0.5) == [A.STOP]


def test_combo_key_cancels_and_waits_release():
    m = HotkeyMachine()
    m.on_trigger_down(0.0)
    assert m.on_other_key(0.4) == [A.CANCEL]
    assert m.on_trigger_up(0.6) == [] and m.state == "idle"


def test_double_tap_enters_handsfree_and_next_press_stops():
    m = HotkeyMachine()
    m.on_trigger_down(0.0)
    m.on_trigger_up(0.1)
    assert m.on_trigger_down(0.3) == [A.START, A.LOCK]
    assert m.on_trigger_up(0.4) == []
    assert m.on_other_key(1.0) == []
    assert m.on_trigger_down(5.0) == [A.STOP]
    m.on_trigger_up(5.1)
    assert m.state == "idle"


def test_slow_second_tap_is_plain_hold():
    m = HotkeyMachine()
    m.on_trigger_down(0.0)
    m.on_trigger_up(0.1)
    assert m.on_trigger_down(0.6) == [A.START]


def test_esc_cancels_handsfree():
    m = HotkeyMachine()
    m.on_trigger_down(0.0)
    m.on_trigger_up(0.1)
    m.on_trigger_down(0.2)
    assert m.on_esc(2.0) == [A.CANCEL]
    assert m.state == "idle"


def event(listener, vk, *, down=True, flags=0):
    suppressed = []
    listener._listener = SimpleNamespace(suppress_event=lambda: suppressed.append(True))
    listener._win32_filter(0x100 if down else 0x101, SimpleNamespace(vkCode=vk, flags=flags))
    return bool(suppressed)


def test_adapter_ignores_injected_events():
    got = []
    clock = iter([0.0, 1.0]).__next__
    listener = HotkeyListener(HotkeyMachine(), got.append, clock=clock)
    event(listener, 0xA3)
    event(listener, 0xA2, flags=0x10)  # Scribe 自己模拟的 Ctrl+V
    event(listener, 0x56, flags=0x10)
    event(listener, 0xA3, down=False)
    assert got == [A.START, A.STOP]


def test_autorepeat_does_not_end_handsfree():
    m = HotkeyMachine()
    m.on_trigger_down(0.0)
    m.on_trigger_up(0.1)
    assert m.on_trigger_down(0.25) == [A.START, A.LOCK]
    assert m.on_trigger_down(0.75) == []        # 第二下按住不放, 系统自动重复
    assert m.state == "handsfree"
    m.on_trigger_up(0.9)
    assert m.on_trigger_down(3.0) == [A.STOP]   # 真正的再按一下才结束


def test_right_ctrl_learn_cancels_recording_swallows_l_and_waits_for_both_keys():
    for release_order in ((0x4C, 0xA3), (0xA3, 0x4C)):
        got = []
        listener = HotkeyListener(HotkeyMachine(), got.append, on_learn=lambda: got.append("learn"))
        assert not event(listener, 0xA3)
        assert not event(listener, 0x4C, flags=0x10)
        assert event(listener, 0x4C)
        assert event(listener, 0x4C)  # 按住 L 的自动重复
        assert got == [A.START, A.CANCEL]
        assert event(listener, release_order[0], down=False) == (release_order[0] == 0x4C)
        assert got == [A.START, A.CANCEL]
        assert event(listener, release_order[1], down=False) == (release_order[1] == 0x4C)
        assert got == [A.START, A.CANCEL, "learn"]


def test_learn_during_handsfree_keeps_recording():
    machine = HotkeyMachine()
    machine.on_trigger_down(0)
    machine.on_trigger_up(0.1)
    machine.on_trigger_down(0.2)
    machine.on_trigger_up(0.3)
    got = []
    listener = HotkeyListener(machine, got.append, on_learn=lambda: got.append("learn"))
    for key in (0xA3, 0x4C):
        assert not event(listener, key)
    for key in (0x4C, 0xA3):
        assert not event(listener, key, down=False)
    assert got == []
    assert machine.state == "handsfree"
    event(listener, 0xA3)
    event(listener, 0xA3, down=False)
    assert got == [A.STOP]


def test_left_ctrl_old_studio_shortcut_and_other_combos_reach_foreground():
    for keys in ((0xA2, 0x4C), (0xA2, 0xA4, 0x4C), (0xA3, 0xA4, 0x4C),
                 (0xA3, 0x43), (0xA3, 0xA1, 0x4C)):
        got = []
        listener = HotkeyListener(HotkeyMachine(), got.append, on_learn=lambda: got.append("learn"))
        for key in keys:
            assert not event(listener, key)
        for key in reversed(keys):
            assert not event(listener, key, down=False)
        assert "learn" not in got and A.STOP not in got


def test_holding_l_before_right_ctrl_does_not_turn_autorepeat_into_learning():
    got = []
    listener = HotkeyListener(HotkeyMachine(), got.append, on_learn=lambda: got.append("learn"))
    for key in (0x4C, 0xA3, 0x4C):
        assert not event(listener, key)
    for key in (0x4C, 0xA3):
        assert not event(listener, key, down=False)
    assert "learn" not in got


def test_f8_dictation_does_not_replace_right_ctrl_learning():
    got = []
    listener = HotkeyListener(HotkeyMachine(), got.append, trigger=lambda: 0x77,
                              on_learn=lambda: got.append("learn"), clock=count().__next__)
    assert event(listener, 0x77)
    assert event(listener, 0x77, down=False)
    assert not event(listener, 0xA3)
    assert event(listener, 0x4C)
    assert event(listener, 0x4C, down=False)
    assert not event(listener, 0xA3, down=False)
    assert got == [A.START, A.STOP, "learn"]
