import dataclasses

import pytest

from scribe.selection_watch import (BarTimer, Press, WindowInfo,
                                    is_selection_gesture, should_offer)


def p(x=0, y=0, t=0.0, ibeam=True): return Press(x, y, t, 7, ibeam)
ARGS = dict(drag_px=4, dbl_ms=500, dbl_w=4, dbl_h=4)

def test_drag_on_text_is_gesture():
    assert is_selection_gesture(p(), 10, 0, 0.3, previous=None, **ARGS)

def test_arrow_drag_is_gesture():
    # 真机: Claude 窗口里从段落左侧空白拖到句末, 按下和松开都是箭头, 也要弹出
    assert is_selection_gesture(p(ibeam=False), 50, 0, 0.3, previous=None, **ARGS)

def test_arrow_drag_in_file_manager_is_not_gesture():
    # 资源管理器和桌面里的箭头拖动是拖文件或框选图标
    assert not is_selection_gesture(p(ibeam=False), 50, 0, 0.3, previous=None,
                                    file_app=True, **ARGS)

def test_text_drag_in_file_manager_is_gesture():
    assert is_selection_gesture(p(), 50, 0, 0.3, previous=None, file_app=True, **ARGS)

@pytest.mark.parametrize("ibeam", [True, False])
def test_drag_that_moves_or_resizes_window_is_not_gesture(ibeam):
    assert not is_selection_gesture(p(ibeam=ibeam), 50, 0, 0.3, previous=None,
                                    window_changed=True, **ARGS)

def test_drag_from_margin_ending_on_text_is_gesture():
    # 从段落左侧空白处开始拖(按下时是箭头), 松开时在文字上(竖线)
    assert is_selection_gesture(p(ibeam=False), 50, 0, 0.3, previous=None,
                                up_ibeam=True, **ARGS)

def test_double_click_needs_text_at_press():
    assert not is_selection_gesture(p(t=0.3, ibeam=False), 1, 0, 0.35,
                                    previous=p(t=0.0), up_ibeam=True, **ARGS)

def test_plain_click_is_not_gesture():
    assert not is_selection_gesture(p(), 2, 1, 0.1, previous=None, **ARGS)

def test_double_click_is_gesture():
    assert is_selection_gesture(p(t=0.3), 1, 0, 0.35, previous=p(t=0.0), **ARGS)

def test_slow_second_click_is_not_double():
    assert not is_selection_gesture(p(t=0.9), 1, 0, 0.95, previous=p(t=0.0), **ARGS)

INFO = WindowInfo(7, own=False, fullscreen=False, elevated=False, app_id="chrome.exe")

@pytest.mark.parametrize("change", [dict(own=True), dict(fullscreen=True), dict(elevated=True)])
def test_filtered_windows(change):
    assert not should_offer(dataclasses.replace(INFO, **change), enabled=True, dictating=False, excluded=set())

def test_filters_off_dictating_excluded():
    assert should_offer(INFO, enabled=True, dictating=False, excluded=set())
    assert not should_offer(INFO, enabled=False, dictating=False, excluded=set())
    assert not should_offer(INFO, enabled=True, dictating=True, excluded=set())
    assert not should_offer(INFO, enabled=True, dictating=False, excluded={"chrome.exe"})

def test_bar_timer_fades_after_3s_and_pauses_on_hover():
    b = BarTimer(); b.shown(100, 100, 0.0)
    assert b.tick(2.9) == "show" and b.tick(3.0) == "fade" and b.tick(3.3) == "hide"
    b.shown(100, 100, 10.0); b.hover(True, 12.0)
    assert b.tick(20.0) == "show"
    b.hover(False, 20.0)
    assert b.tick(22.9) == "show" and b.tick(23.0) == "fade"

def test_bar_timer_hides_when_mouse_goes_far():
    b = BarTimer(); b.shown(100, 100, 0.0); b.moved(330, 100)
    assert b.tick(0.1) == "hide"


# ---- SelectionWatcher 事件序列 ----
from types import SimpleNamespace

from scribe import selection_watch as sw


def _watcher(monkeypatch, offers, log=None, on_offer=None):
    clock = SimpleNamespace(t=0.0)
    monkeypatch.setattr(sw, "_cursor_is_ibeam", lambda: True)
    monkeypatch.setattr(sw.output, "foreground_window", lambda: 7)
    monkeypatch.setattr(sw, "window_info", lambda w: INFO)
    monkeypatch.setattr(sw._u32, "GetDpiForWindow", lambda w: 96)
    monkeypatch.setattr(sw._u32, "GetDoubleClickTime", lambda: 500)
    monkeypatch.setattr(sw._u32, "GetSystemMetrics", lambda i: 4)
    w = sw.SelectionWatcher(on_offer or (lambda x, y, win: offers.append((x, y, win))),
                            lambda x, y: None, enabled=lambda: True,
                            dictating=lambda: False, excluded=set,
                            clock=lambda: clock.t, log=log or (lambda m: None))
    return w, clock


def test_watcher_double_click_offers(monkeypatch):
    offers = []
    w, clock = _watcher(monkeypatch, offers)
    w._on_down(50, 50); clock.t = 0.05; w._on_up(50, 50)
    assert offers == []
    clock.t = 0.2; w._on_down(51, 50); clock.t = 0.25; w._on_up(51, 50)
    assert offers == [(51, 50, 7)]


def test_watcher_single_click_does_not_offer(monkeypatch):
    offers = []
    w, clock = _watcher(monkeypatch, offers)
    w._on_down(50, 50); clock.t = 0.05; w._on_up(50, 50)
    assert offers == []


def test_hook_exception_is_logged_not_raised(monkeypatch):
    logs = []

    def boom(x, y, win): raise ValueError("secret text")
    w, clock = _watcher(monkeypatch, [], log=logs.append, on_offer=boom)
    data = lambda x: SimpleNamespace(flags=0, pt=SimpleNamespace(x=x, y=0))
    w._win32_filter(sw._WM_LBUTTONDOWN, data(0))
    clock.t = 0.1
    assert w._win32_filter(sw._WM_LBUTTONUP, data(40)) is False
    assert logs and "ValueError" in logs[0] and "secret" not in logs[0]
