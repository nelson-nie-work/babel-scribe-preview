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


# ---- 翻译卡片开着时截获回车 / Esc ----

def listener(**kw):
    return HotkeyListener(HotkeyMachine(), [].append, clock=count().__next__, **kw)


def test_enter_passes_when_card_closed():
    l = listener(card_keys=lambda: frozenset())
    assert l._press(0x0D) is False and l._release(0x0D) is False


def test_enter_passes_when_card_cannot_replace():
    l = listener(card_keys=lambda: frozenset({0x1B}))
    assert l._press(0x0D) is False and l._release(0x0D) is False


def test_enter_suppressed_and_reported_when_replace_ready():
    seen = []
    l = listener(card_keys=lambda: frozenset({0x1B, 0x0D}), on_card_key=seen.append)
    assert l._press(0x0D) is True and l._release(0x0D) is True and seen == [0x0D]


def test_esc_suppressed_while_card_visible():
    seen = []
    l = listener(card_keys=lambda: frozenset({0x1B}), on_card_key=seen.append)
    assert l._press(0x1B) is True and l._release(0x1B) is True and seen == [0x1B]


def test_every_key_reported_to_on_key():
    seen = []
    l = listener(on_key=seen.append)
    l._press(0x41)
    l._press(0xA0)
    assert seen == [0x41]


def test_card_key_claimed_even_when_dictation_disabled():
    l = listener(card_keys=lambda: frozenset({0x0D}), enabled=lambda: False)
    assert l._press(0x0D) is True and l._release(0x0D) is True


def test_claimed_key_does_not_reach_dictation_machine():
    got = []
    l = HotkeyListener(HotkeyMachine(), got.append, clock=count().__next__,
                       card_keys=lambda: frozenset({0x1B, 0x0D}))
    assert l._press(0x1B) is True       # 听写空闲: 卡片截下 Esc, 状态机收不到
    assert l._press(0x0D) is True
    assert got == [] and l.machine.state == "idle"


def test_card_keys_not_claimed_while_holding_to_dictate():
    got, seen = [], []
    l = HotkeyListener(HotkeyMachine(), got.append, clock=count().__next__,
                       card_keys=lambda: frozenset({0x1B, 0x0D}), on_card_key=seen.append)
    l._press(0xA3)                      # 右 Ctrl 按住, 正在录音
    assert l._press(0x1B) is False      # Esc 照常取消听写, 不截给卡片
    assert got == [A.START, A.CANCEL] and seen == []
    assert l._release(0x1B) is False


def test_card_keys_not_claimed_during_handsfree():
    got, seen = [], []
    l = HotkeyListener(HotkeyMachine(), got.append, clock=(i * 0.05 for i in count()).__next__,
                       card_keys=lambda: frozenset({0x1B, 0x0D}), on_card_key=seen.append)
    l._press(0xA3); l._release(0xA3)    # 双击进入免手持
    l._press(0xA3); l._release(0xA3)
    assert l.machine.state == "handsfree"
    assert l._press(0x0D) is False      # 回车不截
    assert l._press(0x1B) is False      # Esc 取消免手持听写
    assert got[-1] == A.CANCEL and l.machine.state == "idle" and seen == []


def test_unclaimed_key_still_cancels_dictation():
    got = []
    l = HotkeyListener(HotkeyMachine(), got.append, clock=count().__next__,
                       card_keys=lambda: frozenset({0x0D}))
    l._press(0xA3)
    l._press(0x41)
    assert got == [A.START, A.CANCEL]


def test_claimed_key_does_not_trigger_learn_word():
    learned = []
    l = HotkeyListener(HotkeyMachine(), [].append, clock=count().__next__,
                       on_learn=lambda: learned.append(1), trigger=lambda: 0x77,
                       card_keys=lambda: frozenset({0x4C}))   # 听写键是 F8: 右 Ctrl 只是修饰键
    l._press(0xA3)
    assert l._press(0x4C) is True       # 卡片声明了 L: 照截, 但不是学词
    l._release(0x4C)
    l._release(0xA3)
    assert learned == []


def test_release_claimed_even_if_card_keys_changed():
    keys = {"v": frozenset({0x0D})}
    l = listener(card_keys=lambda: keys["v"])
    assert l._press(0x0D) is True
    keys["v"] = frozenset()             # 按住期间卡片关了
    assert l._release(0x0D) is True
    assert l._release(0x0D) is False    # 登记已清, 之后的松开照常放行


def test_release_of_unclaimed_press_passes_even_if_card_opens():
    keys = {"v": frozenset()}
    l = listener(card_keys=lambda: keys["v"])
    assert l._press(0x0D) is False
    keys["v"] = frozenset({0x0D})
    assert l._release(0x0D) is False


def test_autorepeat_of_claimed_key_reports_once_and_stays_claimed():
    seen = []
    keys = {"v": frozenset({0x0D})}
    l = listener(card_keys=lambda: keys["v"], on_card_key=seen.append)
    assert l._press(0x0D) is True
    keys["v"] = frozenset()
    assert l._press(0x0D) is True       # 自动重复仍属同一次被截的按下
    assert seen == [0x0D]


def test_modifiers_still_tracked_and_never_reported():
    seen = []
    l = listener(on_key=seen.append, card_keys=lambda: frozenset({0xA3}))
    l._press(0xA3)
    assert 0xA3 in l._held_modifiers and seen == []
    l._release(0xA3)
    assert 0xA3 not in l._held_modifiers


def test_callback_exceptions_cannot_break_the_hook():
    def boom(vk):
        raise RuntimeError("callback bug")

    def bad_keys():
        raise RuntimeError("card_keys bug")

    l = listener(card_keys=lambda: frozenset({0x0D}), on_card_key=boom, on_key=boom)
    assert l._press(0x0D) is True and l._release(0x0D) is True
    l2 = listener(card_keys=bad_keys)
    assert l2._press(0x0D) is False and l2._release(0x0D) is False
