"""听写快捷键: 纯逻辑状态机 + pynput 全局监听适配。

状态转移见 docs/plans/2026-11-10-m0-m1-first-usable.md Task 2 的表。
"""
import enum
import time
from typing import Callable

from scribe.config import DOUBLE_TAP_S, MIN_HOLD_S

_RIGHT_CTRL = 0xA3
_ESC = 0x1B
_L = 0x4C
_MODIFIERS = frozenset((0x10, 0x11, 0x12, 0x5B, 0x5C, *range(0xA0, 0xA6)))
_KEY_DOWN = (0x100, 0x104)
_KEY_UP = (0x101, 0x105)
_INJECTED = 0x12


class Action(enum.Enum):
    START = "start"
    STOP = "stop"
    CANCEL = "cancel"
    LOCK = "lock"


class HotkeyMachine:
    def __init__(self, min_hold_s: float = MIN_HOLD_S, double_tap_s: float = DOUBLE_TAP_S):
        self.min_hold_s = min_hold_s
        self.double_tap_s = double_tap_s
        self.state = "idle"
        self._down_at = 0.0
        self._last_short_up = None   # 上次"短按松开"的时间, 供双击判断
        self._physically_down = False  # 键还按着时再来的 down 都是系统自动重复

    def on_trigger_down(self, t: float) -> list:
        if self._physically_down:
            return []
        self._physically_down = True
        if self.state == "idle":
            if self._last_short_up is not None and t - self._last_short_up <= self.double_tap_s:
                self._last_short_up = None
                self.state = "handsfree"
                return [Action.START, Action.LOCK]
            self._down_at = t
            self.state = "holding"
            return [Action.START]
        if self.state == "handsfree":
            self.state = "wait_release"
            return [Action.STOP]
        return []   # holding 时的键盘自动重复, wait_release 时的任何按下

    def on_trigger_up(self, t: float) -> list:
        self._physically_down = False
        if self.state == "holding":
            self.state = "idle"
            if t - self._down_at < self.min_hold_s:
                self._last_short_up = t
                return [Action.CANCEL]
            return [Action.STOP]
        if self.state == "wait_release":
            self.state = "idle"
        return []

    def on_other_key(self, t: float) -> list:
        if self.state == "holding":
            self.state = "wait_release"
            return [Action.CANCEL]
        return []

    def on_esc(self, t: float) -> list:
        if self.state == "holding":
            self.state = "wait_release"
            return [Action.CANCEL]
        if self.state == "handsfree":
            self.state = "idle"
            return [Action.CANCEL]
        return []


class HotkeyListener:
    """Windows 全局监听。仅吞掉学词用的 L，保留左 Ctrl+L 和普通快捷键。"""

    def __init__(self, machine: HotkeyMachine, emit: Callable[[Action], None],
                 clock: Callable[[], float] = time.monotonic, *,
                 on_learn: Callable[[], None] | None = None,
                 trigger: Callable[[], int] = lambda: _RIGHT_CTRL,
                 enabled: Callable[[], bool] = lambda: True):
        self.machine = machine
        self.emit = emit
        self.clock = clock
        self.on_learn = on_learn
        self.trigger = trigger
        self.enabled = enabled
        self._listener = None
        self._held_modifiers = set()
        self._l_down = False
        self._suppress_l = False
        self._learn_pending = False
        self._handsfree_stop = False
        self._pressed_trigger = None
        self._last_trigger = None

    def _dispatch(self, actions):
        for a in actions:
            self.emit(a)

    def _press(self, vk: int) -> bool:
        if vk in _MODIFIERS:
            self._held_modifiers.add(vk)
        if not self.enabled():
            return False
        if vk == self._pressed_trigger:
            return vk not in _MODIFIERS
        if vk == self.trigger() and self._pressed_trigger is None:
            if self._held_modifiers - {vk} or self._l_down:
                return False
            if vk != self._last_trigger:
                self.machine._last_short_up = None
                self._last_trigger = vk
            self._pressed_trigger = vk
            if self.machine.state == "handsfree":
                # 免手持时等松键，分辨单按结束和普通组合键。
                self._handsfree_stop = True
            else:
                self._dispatch(self.machine.on_trigger_down(self.clock()))
            return vk not in _MODIFIERS

        self._handsfree_stop = False
        if vk == _L:
            repeated = self._l_down
            self._l_down = True
            if self._suppress_l:
                return True
            if (not repeated and self.on_learn is not None
                    and self._held_modifiers == {_RIGHT_CTRL}
                    and (self.machine.state == "idle"
                         or (self.machine.state == "holding" and self._pressed_trigger == _RIGHT_CTRL))):
                self._suppress_l = self._learn_pending = True
                self._dispatch(self.machine.on_other_key(self.clock()))
                return True

        self._learn_pending = False
        action = self.machine.on_esc if vk == _ESC else self.machine.on_other_key
        self._dispatch(action(self.clock()))
        return False

    def _release(self, vk: int) -> bool:
        self._held_modifiers.discard(vk)
        suppressed = vk == _L and self._suppress_l
        if vk == _L:
            self._l_down = self._suppress_l = False
        if vk == self._pressed_trigger:
            self._pressed_trigger = None
            suppressed = suppressed or vk not in _MODIFIERS
            t = self.clock()
            if self._handsfree_stop and self.machine.state == "handsfree":
                self._dispatch(self.machine.on_trigger_down(t))
            self._handsfree_stop = False
            self._dispatch(self.machine.on_trigger_up(t))
        if self._learn_pending and not self._held_modifiers and not self._l_down:
            self._learn_pending = False
            if self.enabled() and self.machine.state == "idle":
                self.on_learn()
        return suppressed

    def _win32_filter(self, message, data):
        # 在 Windows 钩子内按实际到达顺序处理，不能等待 pynput 的异步回调
        # 再拦 L，否则编辑器可能已执行 Ctrl+L 并丢掉选区。
        if not data.flags & _INJECTED:
            if message in _KEY_DOWN:
                suppressed = self._press(data.vkCode)
            elif message in _KEY_UP:
                suppressed = self._release(data.vkCode)
            else:
                suppressed = False
            if suppressed:
                self._listener.suppress_event()
        return False  # 不再转发给 pynput 回调。未调用 suppress_event 的按键照常交给 Windows。

    def start(self):
        from pynput import keyboard
        self._listener = keyboard.Listener(win32_event_filter=self._win32_filter)
        self._listener.start()

    def stop(self):
        if self._listener:
            self._listener.stop()
            self._listener = None
