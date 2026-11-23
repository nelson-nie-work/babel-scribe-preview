"""划词翻译的选字手势判定: 纯逻辑 + pynput 鼠标钩子适配。

只观察鼠标, 不读选区、不碰剪贴板、不联网; 绝不吞鼠标事件。
"""
import ctypes
import time
from ctypes import wintypes
from dataclasses import dataclass
from typing import Callable

from scribe import output

FADE_AFTER_S = 3.0   # 悬浮条无人悬停后开始淡出
FADE_S = 0.25        # 淡出时长
AWAY_PX = 220        # 鼠标离开悬浮条出现点超过此距离就收起
DRAG_PX = 4          # 拖动阈值(96 DPI 下), 随 DPI 缩放

_WM_LBUTTONDOWN = 0x201
_WM_LBUTTONUP = 0x202
_LLMHF_INJECTED = 0x1
_IDC_IBEAM = 32513
_SM_CXDOUBLECLK, _SM_CYDOUBLECLK = 36, 37
_MONITOR_DEFAULTTONEAREST = 2

_u32 = ctypes.WinDLL("user32", use_last_error=True)
_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_u32.GetCursorInfo.argtypes = [ctypes.c_void_p]
_u32.LoadCursorW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
_u32.LoadCursorW.restype = wintypes.HANDLE
_u32.GetDpiForWindow.argtypes = [wintypes.HWND]
_u32.GetDpiForWindow.restype = wintypes.UINT
_u32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_u32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
_u32.MonitorFromWindow.restype = wintypes.HANDLE
_u32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
_u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]


class _CURSORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("hCursor", wintypes.HANDLE), ("ptScreenPos", wintypes.POINT)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


@dataclass(frozen=True)
class Press:
    x: int
    y: int
    t: float
    window: int
    ibeam: bool
    rect: tuple = ()      # 按下时前台窗口的位置大小; 松开时变了说明是在拖窗口或调大小


# 箭头光标的拖动在这些程序里是拖文件或框选图标(资源管理器、桌面、任务栏), 不算选字
FILE_APPS = frozenset({"explorer.exe"})


@dataclass(frozen=True)
class WindowInfo:
    window: int
    own: bool
    fullscreen: bool
    elevated: bool
    app_id: str


def is_selection_gesture(press: Press, up_x: int, up_y: int, up_t: float, *,
                         previous: Press | None, drag_px: float,
                         dbl_ms: int, dbl_w: int, dbl_h: int,
                         up_ibeam: bool = False, window_changed: bool = False,
                         file_app: bool = False) -> bool:
    """拖动超过阈值, 或在文字上(竖线光标)构成双击。

    从段落左侧空白处开始的整段选择, 有的程序(如 Claude)全程显示箭头, 所以箭头拖动也算;
    只排除拖动后窗口位置大小变了(拖窗口、调大小), 和资源管理器里的箭头拖动(拖文件、框选)。
    误弹的悬浮条不点会自己淡出, 也不碰剪贴板。
    """
    if max(abs(up_x - press.x), abs(up_y - press.y)) > drag_px:
        if window_changed:
            return False
        return press.ibeam or up_ibeam or not file_app
    return (press.ibeam
            and previous is not None
            and press.t - previous.t <= dbl_ms / 1000
            and abs(press.x - previous.x) <= dbl_w / 2
            and abs(press.y - previous.y) <= dbl_h / 2)


def should_offer(info: WindowInfo, *, enabled: bool, dictating: bool,
                 excluded: set[str]) -> bool:
    return (enabled and not dictating and not info.own and not info.fullscreen
            and not info.elevated and info.app_id not in excluded)


class BarTimer:
    """悬浮条收起的纯状态机。按键、按下鼠标、换窗口、开始听写由调用方直接 hide。"""

    def __init__(self):
        self._x = self._y = 0
        self._since = 0.0
        self._hovering = False
        self._away = False

    def shown(self, x: int, y: int, t: float):
        self._x, self._y, self._since = x, y, t
        self._hovering = self._away = False

    def hover(self, on: bool, t: float):
        self._hovering = on
        if not on:
            self._since = t   # 移开后重新计 3 秒

    def moved(self, x: int, y: int):
        if max(abs(x - self._x), abs(y - self._y)) > AWAY_PX:
            self._away = True

    def tick(self, t: float) -> str:
        if self._away:
            return "hide"
        if self._hovering:
            return "show"
        idle = t - self._since
        if idle < FADE_AFTER_S:
            return "show"
        return "fade" if idle < FADE_AFTER_S + FADE_S else "hide"


def _window_pid(window: int) -> int:
    pid = wintypes.DWORD()
    _u32.GetWindowThreadProcessId(window, ctypes.byref(pid))
    return pid.value


def _is_fullscreen(window: int) -> bool:
    rect = wintypes.RECT()
    if not window or not _u32.GetWindowRect(window, ctypes.byref(rect)):
        return False
    monitor = _u32.MonitorFromWindow(window, _MONITOR_DEFAULTTONEAREST)
    info = _MONITORINFO()
    info.cbSize = ctypes.sizeof(_MONITORINFO)
    if not monitor or not _u32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return False
    m = info.rcMonitor
    return (rect.left, rect.top, rect.right, rect.bottom) == (m.left, m.top, m.right, m.bottom)


def window_info(window: int) -> WindowInfo:
    return WindowInfo(
        window,
        own=bool(window) and _window_pid(window) == _k32.GetCurrentProcessId(),
        fullscreen=_is_fullscreen(window),
        elevated=output.window_elevated(window),
        app_id=output.window_application(window))


def _window_rect(window: int) -> tuple:
    rect = wintypes.RECT()
    if not window or not _u32.GetWindowRect(window, ctypes.byref(rect)):
        return ()
    return (rect.left, rect.top, rect.right, rect.bottom)


def _cursor_is_ibeam() -> bool:
    info = _CURSORINFO()
    info.cbSize = ctypes.sizeof(_CURSORINFO)
    if not _u32.GetCursorInfo(ctypes.byref(info)):
        return False
    return bool(info.hCursor) and info.hCursor == _u32.LoadCursorW(None, _IDC_IBEAM)


class SelectionWatcher:
    """鼠标全局钩子适配: 只观察, 永不 suppress_event。
    on_offer(x, y, window) 在钩子线程调用, 接线方负责 post 到 Qt 线程。"""

    def __init__(self, on_offer: Callable[[int, int, int], None],
                 on_mouse_down: Callable[[int, int], None], *,
                 enabled: Callable[[], bool], dictating: Callable[[], bool],
                 excluded: Callable[[], set[str]],
                 clock: Callable[[], float] = time.monotonic,
                 log: Callable[[str], None] = lambda message: None):
        self.on_offer = on_offer
        self.on_mouse_down = on_mouse_down
        self.enabled = enabled
        self.dictating = dictating
        self.excluded = excluded
        self.clock = clock
        self._listener = None
        self._press: Press | None = None
        self._previous: Press | None = None
        self._last: Press | None = None
        self._log = log

    def _on_down(self, x: int, y: int):
        window = output.foreground_window()
        self._previous = self._last
        self._press = Press(x, y, self.clock(), window, _cursor_is_ibeam(), _window_rect(window))
        self.on_mouse_down(x, y)

    def _on_up(self, x: int, y: int):
        press, self._press = self._press, None
        if press is None:
            return
        self._last = press   # 已完成的上一次按下, 供下一次按下判双击
        if not self.enabled():
            return
        up_ibeam = _cursor_is_ibeam()
        loose = not (press.ibeam or up_ibeam)
        dpi = _u32.GetDpiForWindow(press.window) if press.window else 0
        scale = (dpi or 96) / 96
        if not is_selection_gesture(
                press, x, y, self.clock(), previous=self._previous,
                drag_px=DRAG_PX * scale,
                dbl_ms=int(_u32.GetDoubleClickTime()),
                dbl_w=int(_u32.GetSystemMetrics(_SM_CXDOUBLECLK)),
                dbl_h=int(_u32.GetSystemMetrics(_SM_CYDOUBLECLK)),
                up_ibeam=up_ibeam,
                window_changed=bool(press.rect) and _window_rect(press.window) != press.rect,
                file_app=loose and output.window_application(press.window) in FILE_APPS):
            return
        window = output.foreground_window()
        info = window_info(window)
        if should_offer(info, enabled=True, dictating=self.dictating(),
                        excluded=self.excluded()):
            self.on_offer(x, y, window)

    def _win32_filter(self, message, data):
        try:
            if not data.flags & _LLMHF_INJECTED:
                if message == _WM_LBUTTONDOWN:
                    self._on_down(data.pt.x, data.pt.y)
                elif message == _WM_LBUTTONUP:
                    self._on_up(data.pt.x, data.pt.y)
        except Exception as exc:   # 钩子里抛错会让 pynput 停掉监听, 整个会话失效
            try:
                self._log(f"selection hook error: {type(exc).__name__}")   # 只记类型, 不记内容
            except Exception:
                pass
        return False   # 不转发给 pynput 回调; 从不 suppress_event, 事件照常交给 Windows

    def start(self):
        from pynput import mouse
        self._listener = mouse.Listener(win32_event_filter=self._win32_filter)
        self._listener.start()

    def stop(self):
        if self._listener:
            self._listener.stop()
            self._listener = None
