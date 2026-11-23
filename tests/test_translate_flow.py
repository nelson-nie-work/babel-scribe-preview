from types import SimpleNamespace

from scribe.llm import LlmError
from scribe.output import _parse_editable
from scribe.translate import NoService, Translation
from scribe.translate_flow import TranslateFlow


class FakeView:
    def __init__(self):
        self.bar_shown = False
        self.first_time = None
        self.card = None          # None | "loading" | "result" | "error" | "no_service"
        self.result = None
        self.editable = None
        self.errors = None
        self.toasts = []
        self.inside = True
        self.faded = 0

    def show_bar(self, x, y, first_time):
        self.bar_shown, self.first_time = True, first_time

    def hide_bar(self):
        self.bar_shown = False

    def fade_bar(self):
        self.faded += 1

    def show_loading(self, x, y, target, source):
        self.card = "loading"

    def show_result(self, t, target, editable):
        self.card, self.result, self.editable = "result", t, editable

    def show_error(self, services):
        self.card, self.errors = "error", services

    def show_no_service(self):
        self.card = "no_service"

    def toast(self, text):
        self.toasts.append(text)

    def close_card(self):
        self.card = None

    def card_visible(self):
        return self.card is not None

    def bar_visible(self):
        return self.bar_shown

    def contains(self, x, y):
        return self.inside


def flow(read="hi", editable=False, reply="hello", foreground=(7,), defer=False, fail=None,
         deliver_result="pasted", services=("智谱", "DeepSeek"), prewarm=None):
    """假的依赖; submit/post 同步执行。foreground 每调用一次取下一个值, 最后一个一直保持。
    真的 read/editable 内部会核对一次前台窗口, 假的也各取一次, 以模拟时间推移。"""
    view = FakeView()
    shots = [] if callable(foreground) else list(foreground)
    calls = SimpleNamespace(read=0, copy=0, deliver=0, run=0, copied=[], delivered=[],
                            view=view, deferred=[], logs=[], noticed=0, editable=0)

    def fg():
        if callable(foreground):          # 可变的前台窗口: 测试里随时切换
            return foreground()
        return shots.pop(0) if len(shots) > 1 else shots[0]

    def fake_read(window):
        calls.read += 1
        fg()
        return read

    def fake_editable(window):
        calls.editable += 1
        fg()
        return editable

    def fake_run(text, target):
        calls.run += 1
        if fail is not None:
            raise fail
        return Translation(reply, "智谱", 640, False, False)

    def fake_deliver(window, text):
        calls.deliver += 1
        calls.delivered.append(text)
        if isinstance(deliver_result, Exception):
            raise deliver_result
        return deliver_result

    def fake_copy(text):
        calls.copy += 1
        calls.copied.append(text)

    def submit(fn):
        if defer:
            calls.deferred.append(fn)
        else:
            fn()

    def finish_deferred():
        while calls.deferred:
            calls.deferred.pop(0)()

    def mark_noticed():
        calls.noticed += 1

    calls.finish_deferred = finish_deferred
    f = TranslateFlow(read=fake_read, editable=fake_editable, run=fake_run, deliver=fake_deliver,
                      copy=fake_copy, foreground=fg, submit=submit, post=lambda fn: fn(),
                      view=view, log=calls.logs.append, services=lambda: list(services),
                      first_time=lambda: calls.noticed == 0, noticed=mark_noticed,
                      clock=lambda: 0.0, **({"prewarm": prewarm} if prewarm else {}))
    return f, calls


def test_offer_touches_nothing():
    f, calls = flow()
    f.offer(10, 10, 7)
    assert calls.read == calls.copy == calls.deliver == calls.run == 0 and calls.view.bar_shown


def test_offer_prewarms_the_service_in_the_background():
    # 真机: 冷连接时首选服务第一次要 2.5 秒以上; 悬浮条一出就预热, 用户移鼠标去点的时间里连好
    warmed = []
    f, calls = flow(defer=True, prewarm=lambda: warmed.append(1))
    f.offer(10, 10, 7)
    assert warmed == [] and calls.deferred      # 不在钩子/主线程里做网络请求
    calls.finish_deferred()
    assert warmed == [1] and calls.read == calls.run == 0


def test_choose_reads_translates_and_shows_editable_result():
    f, calls = flow(read="hi", editable=True, reply="你好")
    f.offer(10, 10, 7); f.choose("zh")
    assert calls.view.result.text == "你好" and calls.view.editable and f.card_keys() == {0x1B, 0x0D}


def test_no_selection_toasts():
    f, calls = flow(read=None)
    f.offer(10, 10, 7); f.choose("zh")
    assert calls.view.toasts == ["没读到选中的文字"] and calls.run == 0


def test_closed_card_ignores_late_result():
    f, calls = flow(read="hi", reply="你好", defer=True)
    f.offer(10, 10, 7); f.choose("zh"); f.close(); calls.finish_deferred()
    assert calls.view.result is None


def test_replace_falls_back_to_copy_when_target_left():
    f, calls = flow(read="hi", editable=True, reply="你好", foreground=[7, 7, 3])
    f.offer(10, 10, 7); f.choose("zh"); f.replace()
    assert calls.deliver == 0 and calls.copied == ["你好"]


def test_other_key_closes_card_but_is_not_claimed():
    f, calls = flow(read="hi", editable=False, reply="hello")
    f.offer(10, 10, 7); f.choose("en")
    assert f.card_keys() == {0x1B}
    f.on_key(0x41); assert not calls.view.card_visible()


def test_click_elsewhere_closes_bar_and_card():
    f, calls = flow(read="hi", reply="hello")
    f.offer(10, 10, 7); f.choose("en"); calls.view.inside = False
    f.on_mouse_down(500, 500)
    assert not calls.view.card_visible() and not calls.view.bar_visible()


def test_bar_hides_when_foreground_changes():
    f, calls = flow(foreground=[3])
    f.offer(10, 10, 7); f.tick(0.1)
    assert not calls.view.bar_visible()


def test_parse_editable():
    assert _parse_editable("1\r\n") and not _parse_editable("") and not _parse_editable("0")


# ---- 以下为补充: 简报之外、规格写明的行为 ----

def test_whitespace_selection_counts_as_no_selection():
    f, calls = flow(read="  \r\n ")
    f.offer(10, 10, 7); f.choose("zh")
    assert calls.view.toasts == ["没读到选中的文字"] and calls.run == 0 and calls.view.card is None


def test_replace_delivers_when_target_still_foreground_and_closes_card():
    f, calls = flow(read="hi", editable=True, reply="你好")
    f.offer(10, 10, 7); f.choose("zh"); f.replace()
    assert calls.delivered == ["你好"] and calls.copied == [] and not calls.view.card_visible()
    assert f.card_keys() == frozenset()


def test_enter_card_key_replaces_and_esc_closes():
    f, calls = flow(read="hi", editable=True, reply="你好")
    f.offer(10, 10, 7); f.choose("zh")
    f.on_key(0x0D)                     # 回车先报给 on_key: 不关卡片
    assert calls.view.card_visible()
    f.on_card_key(0x0D)
    assert calls.delivered == ["你好"]
    f.offer(10, 10, 7); f.choose("zh"); f.on_card_key(0x1B)
    assert not calls.view.card_visible() and calls.deliver == 1


def test_replace_ignored_when_not_editable():
    f, calls = flow(read="hi", editable=False, reply="hello")
    f.offer(10, 10, 7); f.choose("en"); f.replace()
    assert calls.deliver == 0 and calls.copied == [] and calls.view.card_visible()


def test_copy_result_copies_toasts_and_closes():
    f, calls = flow(read="hi", reply="hello")
    f.offer(10, 10, 7); f.choose("en"); f.copy_result()
    assert calls.copied == ["hello"] and calls.view.toasts == ["已复制"] and not calls.view.card_visible()


def test_errors_show_failure_card_and_retry_runs_again():
    f, calls = flow(read="hi", fail=LlmError("all down"))
    f.offer(10, 10, 7); f.choose("en")
    assert calls.view.card == "error" and calls.view.errors == ["智谱", "DeepSeek"]
    assert f.card_keys() == {0x1B}
    f.retry()
    assert calls.run == 2 and calls.read == 1


def test_no_service_card():
    f, calls = flow(read="hi", fail=NoService("none"))
    f.offer(10, 10, 7); f.choose("en")
    assert calls.view.card == "no_service"


def test_card_keys_empty_until_card_shows():
    f, calls = flow(read="hi", reply="hello", defer=True)
    f.offer(10, 10, 7)
    assert f.card_keys() == frozenset()
    f.choose("en")
    assert f.card_keys() == frozenset()
    calls.finish_deferred()
    assert f.card_keys() == {0x1B}


def test_click_inside_keeps_card_and_key_hides_bar():
    f, calls = flow()
    f.offer(10, 10, 7); f.on_mouse_down(12, 12)
    assert calls.view.bar_visible()
    f.on_key(0x41)
    assert not calls.view.bar_visible()


def test_first_offer_shows_notice_once():
    f, calls = flow()
    f.offer(10, 10, 7); first = calls.view.first_time
    f.offer(10, 10, 7)
    assert first is True and calls.view.first_time is False and calls.noticed == 1


def test_bar_timer_fades_hides_and_dictation_hides():
    f, calls = flow()
    f.offer(10, 10, 7); f.tick(3.1)
    assert calls.view.faded == 1 and calls.view.bar_visible()
    f.tick(3.3)
    assert not calls.view.bar_visible()
    f.offer(10, 10, 7); f.hover(True); f.tick(10.0)
    assert calls.view.bar_visible()
    f.moved(400, 400); f.tick(10.1)
    assert not calls.view.bar_visible()
    f.offer(10, 10, 7); f.dictation_started()
    assert not calls.view.bar_visible()


def test_logs_never_contain_text():
    f, calls = flow(read="秘密原文", editable=True, reply="secret translation")
    f.offer(10, 10, 7); f.choose("en"); f.replace()
    joined = "\n".join(calls.logs)
    assert "秘密" not in joined and "secret" not in joined
    assert calls.logs[0] == "translate: target=en chars=4 service=智谱 ms=640 outcome=ok"
    assert calls.logs[-1].endswith("outcome=replaced")


def test_logical_point_maps_hook_pixels_to_qt_coordinates():
    from scribe.translate_flow import logical_point
    screens = [(0, 0, 1280, 720, 1.5), (1920, 0, 1920, 1080, 1.0)]   # 左屏 150% 缩放
    assert logical_point(300, 150, screens) == (200, 100)
    assert logical_point(2000, 50, screens) == (2000, 50)
    assert logical_point(-50, -50, screens) == (-50, -50)             # 不在任何屏上: 原样


# ---- 审查修复 1: 替换失败不丢译文 ----

def test_replace_failure_reopens_result_card():
    f, calls = flow(read="hi", editable=True, reply="你好", deliver_result="clipboard_protected")
    f.offer(10, 10, 7); f.choose("zh"); f.replace()
    assert calls.view.card == "result" and calls.view.result.text == "你好" and calls.view.editable
    assert calls.view.toasts == ["没能替换，译文还在卡片里"]
    assert f.card_keys() == {0x1B, 0x0D}
    assert calls.logs[-1].endswith("outcome=error")
    f.copy_result()                    # 复制仍然可用
    assert calls.copied == ["你好"]


def test_replace_exception_is_logged_and_reopens_card():
    f, calls = flow(read="秘密", editable=True, reply="secret", deliver_result=OSError("剪贴板被占用"))
    f.offer(10, 10, 7); f.choose("zh"); f.replace()
    assert calls.view.card == "result" and calls.view.result.text == "secret"
    assert calls.view.toasts == ["没能替换，译文还在卡片里"]
    joined = " | ".join(calls.logs)
    assert "OSError" in joined and "outcome=error" in joined
    assert "秘密" not in joined and "secret" not in joined and "剪贴板被占用" not in joined


def test_replace_copied_only_reopens_with_copied_toast():
    f, calls = flow(read="hi", editable=True, reply="你好", deliver_result="copied_only")
    f.offer(10, 10, 7); f.choose("zh"); f.replace()
    assert calls.view.card == "result" and calls.view.toasts == ["没能替换，译文已复制"]
    assert calls.logs[-1].endswith("outcome=copied")


def test_failed_replace_does_not_reopen_over_a_newer_translation():
    f, calls = flow(read="hi", editable=True, reply="你好", deliver_result="clipboard_protected", defer=True)
    f.offer(10, 10, 7); f.choose("zh"); calls.finish_deferred()
    f.replace()                        # paste 任务排队中
    f.offer(10, 10, 7); f.choose("en")  # 用户已开始新的翻译
    paste = calls.deferred.pop(0); paste()
    assert calls.view.card is None and calls.view.toasts == ["没能替换，请重新翻译"]


# ---- 审查修复 2: 可编辑判断与翻译并行 ----

def test_run_does_not_wait_for_editable():
    f, calls = flow(read="hi", editable=True, reply="你好", defer=True)
    f.offer(10, 10, 7); calls.finish_deferred()   # 预热
    f.choose("zh")
    calls.deferred.pop(0)()            # 读选区 + 翻译; 可编辑判断另起任务排着
    assert calls.run == 1 and calls.editable == 0 and calls.view.card == "loading"
    assert f.card_keys() == {0x1B}
    calls.finish_deferred()            # 可编辑判断完成
    assert calls.view.card == "result" and calls.view.editable is True
    assert f.card_keys() == {0x1B, 0x0D}


def test_errors_show_without_waiting_for_editable_and_retry_keeps_it():
    f, calls = flow(read="hi", editable=True, fail=LlmError("down"), defer=True)
    f.offer(10, 10, 7); calls.finish_deferred()   # 预热
    f.choose("zh")
    calls.deferred.pop(0)()
    assert calls.view.card == "error" and calls.editable == 0
    calls.finish_deferred()            # 迟到的可编辑结果不改出错卡片
    assert calls.view.card == "error" and calls.editable == 1


# ---- 终审修复 ----

def test_failed_replace_does_not_reopen_while_another_window_shows_the_bar():
    """A: 替换失败时用户已在另一个窗口划词, 不能重开可替换卡片, 回车不能把译文贴进新窗口。"""
    win = {"v": 7}
    f, calls = flow(read="hi", editable=True, reply="译文A", deliver_result="clipboard_protected",
                    defer=True, foreground=lambda: win["v"])
    f.offer(10, 10, 7); f.choose("zh"); calls.finish_deferred()
    f.replace()                        # paste 任务排队中
    win["v"] = 3; f.offer(50, 50, 3)   # 用户在窗口 B 选了字, B 的悬浮条出现
    calls.deferred.pop(0)()            # 替换失败
    assert calls.view.card is None and f.card_keys() == frozenset()
    assert calls.view.toasts == ["没能替换，请重新翻译"]
    f.on_card_key(0x0D)
    calls.finish_deferred()
    assert calls.delivered == ["译文A"]          # 只有窗口 A 那一次


def test_failed_replace_does_not_reopen_after_switching_window_without_selection():
    win = {"v": 7}
    f, calls = flow(read="hi", editable=True, reply="译文A", deliver_result="clipboard_protected",
                    defer=True, foreground=lambda: win["v"])
    f.offer(10, 10, 7); f.choose("zh"); calls.finish_deferred()
    f.replace()
    win["v"] = 3                       # 切到窗口 B, 没有新的选区
    calls.deferred.pop(0)()
    assert calls.view.card is None and f.card_keys() == frozenset()
    assert calls.view.toasts == ["没能替换，请重新翻译"]


def test_replace_targets_the_job_window_not_the_latest_offer():
    win = {"v": 7}
    f, calls = flow(read="hi", editable=True, reply="你好", foreground=lambda: win["v"])
    f.offer(10, 10, 7); f.choose("zh")
    f.offer(50, 50, 3)                 # 新的悬浮条记下了别的窗口, 但前台仍是 A
    f.replace()
    assert calls.delivered == ["你好"]


def test_card_keys_empty_when_another_window_is_in_front():
    """C: 别的窗口不经输入抢到前台时, 回车/Esc 不再截给卡片。"""
    win = {"v": 7}
    f, calls = flow(read="hi", editable=True, reply="你好", foreground=lambda: win["v"])
    f.offer(10, 10, 7); f.choose("zh")
    assert f.card_keys() == {0x1B, 0x0D}
    win["v"] = 3
    assert f.card_keys() == frozenset()
    win["v"] = 7
    assert f.card_keys() == {0x1B, 0x0D}


def test_key_down_clears_card_keys_immediately_for_other_keys():
    """D: 钩子线程上其他键一按下就清空, 不等主线程关卡片; 紧跟的回车不被截。"""
    f, calls = flow(read="hi", editable=True, reply="你好")
    f.offer(10, 10, 7); f.choose("zh")
    keys = f._keys
    f.key_down(0x0D); f.key_down(0x1B)
    assert f.card_keys() == {0x1B, 0x0D} and f._keys is keys     # 回车/Esc 不清
    f.key_down(0x41)
    assert f.card_keys() == frozenset() and keys == {0x1B, 0x0D}  # 整体替换, 原集合不变


def test_key_down_then_enter_not_claimed_by_listener():
    from scribe.hotkey import HotkeyListener, HotkeyMachine
    f, calls = flow(read="hi", editable=True, reply="你好")
    f.offer(10, 10, 7); f.choose("zh")
    posted = []
    l = HotkeyListener(HotkeyMachine(), [].append, card_keys=f.card_keys,
                       on_card_key=lambda vk: posted.append(vk),
                       on_key=lambda vk: (f.key_down(vk), posted.append(("key", vk))))
    assert l._press(0x41) is False     # 关卡片的调用还排在主线程
    assert l._press(0x0D) is False and 0x0D not in posted


def test_error_card_names_only_services_actually_tried():
    """E: 整条链最多试 BUDGET_ATTEMPTS 家, 出错卡片只列这几家。"""
    f, calls = flow(read="hi", fail=LlmError("down"), services=("智谱", "DeepSeek", "通义"))
    f.offer(10, 10, 7); f.choose("en")
    assert calls.view.errors == ["智谱", "DeepSeek"]
