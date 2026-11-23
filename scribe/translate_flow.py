"""划词翻译的接线逻辑：悬浮条 → 读选区 → 判断可编辑 → 翻译 → 卡片 → 替换或复制。

与 Qt 无关，所有副作用都由构造参数注入，便于用假的依赖单测。
除 card_keys、key_down 外的方法都在 Qt 主线程调用；这两个在键盘钩子线程调用，
只读现成的集合和前台窗口，或把集合整体换成空集。
悬浮条出现时只记下窗口和位置，不读选区、不碰剪贴板；用户点了按钮才开始读。
日志只记目标语言、字数、服务名、耗时和结果类型，绝不记原文和译文。
"""
import time
from typing import Callable, Protocol

from scribe.llm import BUDGET_ATTEMPTS
from scribe.selection_watch import BarTimer
from scribe.translate import NoService, Translation, TranslateSession

ESC, ENTER = 0x1B, 0x0D
BAR_OFFSET = (8, 14)          # 悬浮条在松开鼠标处右下方
_REPLACED = ("pasted", "typed")

NO_SELECTION = "没读到选中的文字"
TARGET_LEFT = "原窗口已不在前台，译文已复制"


def logical_point(x: int, y: int, screens) -> tuple:
    """低级鼠标钩子给的是物理像素，Qt 窗口用逻辑像素。
    screens: [(左, 上, 逻辑宽, 逻辑高, 缩放比)]；Qt 在 Windows 上保持每块屏左上角两种坐标相同。"""
    for left, top, width, height, ratio in screens:
        if left <= x < left + width * ratio and top <= y < top + height * ratio:
            return left + round((x - left) / ratio), top + round((y - top) / ratio)
    return x, y


class FlowView(Protocol):
    def show_bar(self, x: int, y: int, first_time: bool) -> None: ...
    def hide_bar(self) -> None: ...
    def fade_bar(self) -> None: ...
    def show_loading(self, x: int, y: int, target: str, source: str) -> None: ...
    def show_result(self, t: Translation, target: str, editable: bool) -> None: ...
    def show_error(self, services: list) -> None: ...
    def show_no_service(self) -> None: ...
    def toast(self, text: str) -> None: ...
    def close_card(self) -> None: ...
    def card_visible(self) -> bool: ...
    def bar_visible(self) -> bool: ...
    def contains(self, x: int, y: int) -> bool: ...


class TranslateFlow:
    def __init__(self, *, read: Callable[[int], str | None], editable: Callable[[int], bool],
                 run: Callable[[str, str], Translation], deliver: Callable[[int, str], str],
                 copy: Callable[[str], None], foreground: Callable[[], int],
                 submit: Callable[[Callable], None], post: Callable[[Callable], None],
                 view: FlowView, log: Callable[[str], None],
                 services: Callable[[], list] = lambda: [],
                 first_time: Callable[[], bool] = lambda: False,
                 noticed: Callable[[], None] = lambda: None,
                 clock: Callable[[], float] = time.monotonic,
                 prewarm: Callable[[], None] = lambda: None):
        self._read = read
        self._editable = editable
        self._run = run
        self._deliver = deliver
        self._copy = copy
        self._foreground = foreground
        self._submit = submit
        self._post = post
        self._view = view
        self._log = log
        self._services = services
        self._first_time = first_time
        self._noticed = noticed
        self._clock = clock
        self._prewarm = prewarm
        self._session = TranslateSession()
        self._timer = BarTimer()
        self._fading = False
        self._window = 0
        self._point = (0, 0)
        self._card = False                 # 卡片开着(加载中、结果、出错都算)
        self._job = None                   # (原文, 目标语言, 目标窗口) 供重试和替换
        self._target = 0                   # 本次翻译(卡片)对应的窗口; 钩子线程据此判断前台
        self._read_id = 0                  # 本次读选区的令牌; 可编辑判断结果按它认领
        self._editable_known: bool | None = None   # None = 可编辑判断还没回来
        self._waiting = None               # 译文已出、等可编辑判断: (令牌, Translation, 目标语言)
        self._epoch = 0                    # 每次开新翻译加一; 替换失败时据此判断能否重开卡片
        self._result: Translation | None = None
        self._editable_now = False
        self._keys = frozenset()           # 钩子线程读取; 整体替换, 从不原地修改

    # ---- 悬浮条 ----
    def offer(self, x: int, y: int, window: int) -> None:
        self._window = window
        self._point = (x + BAR_OFFSET[0], y + BAR_OFFSET[1])
        first = bool(self._first_time())
        self._view.show_bar(*self._point, first)
        self._timer.shown(x, y, self._clock())
        self._fading = False
        if first:
            self._noticed()
        self._submit(self._warm)   # 冷连接首个请求要多 2 秒左右; 趁用户移鼠标去点时先连好

    def _warm(self) -> None:
        try:
            self._prewarm()
        except Exception:
            pass

    def hover(self, on: bool) -> None:
        self._timer.hover(on, self._clock())
        if on:
            self._fading = False

    def moved(self, x: int, y: int) -> None:
        self._timer.moved(x, y)

    def tick(self, now: float) -> None:
        if not self._view.bar_visible():
            return
        if self._foreground() != self._window:
            self._hide_bar()
            return
        state = self._timer.tick(now)
        if state == "hide":
            self._hide_bar()
        elif state == "fade" and not self._fading:
            self._fading = True
            self._view.fade_bar()

    def dictation_started(self) -> None:
        self._hide_bar()

    def _hide_bar(self) -> None:
        self._fading = False
        if self._view.bar_visible():
            self._view.hide_bar()

    # ---- 翻译 ----
    def choose(self, target: str) -> None:
        token = self._session.start()
        self._epoch += 1
        window = self._target = self._window
        self._hide_bar()

        def job():
            try:
                text = self._read(window)
            except Exception:
                text = None
            if not text or not text.strip():
                self._post(lambda: self._no_selection(token, target))
                return
            self._post(lambda: self._begin(token, text, target))
            # 可编辑判断(PowerShell 冷启动 0.5–2.5 秒)只看焦点, 与翻译并行, 结果在主线程汇合
            self._submit(lambda: self._check_editable(token, window))
            self._translate(token, text, target)

        self._submit(job)

    def retry(self) -> None:
        if self._job is None or not self._card:
            return
        text, target, _window = self._job
        token = self._session.start()
        self._epoch += 1
        self._loading(token, text, target)
        self._submit(lambda: self._translate(token, text, target))

    def _check_editable(self, read_id: int, window: int) -> None:
        """后台线程: 判断可编辑, 结果交回主线程。"""
        try:
            editable = bool(self._editable(window))
        except Exception:
            editable = False
        self._post(lambda: self._editable_done(read_id, editable))

    def _translate(self, token: int, text: str, target: str) -> None:
        """后台线程: 调用翻译, 结果交回主线程。"""
        try:
            t, outcome = self._run(text, target), "ok"
        except NoService:
            t, outcome = None, "no_service"
        except Exception:      # 任何失败都要落到出错卡片, 不能一直转圈
            t, outcome = None, "error"
        self._post(lambda: self._finish(token, text, target, t, outcome))

    def _no_selection(self, token: int, target: str) -> None:
        if not self._session.current(token):
            return
        self._report(target, 0, None, "no_selection")
        self._view.toast(NO_SELECTION)

    def _begin(self, token: int, text: str, target: str) -> None:
        if not self._session.current(token):
            return
        self._read_id, self._editable_known, self._waiting = token, None, None
        self._loading(token, text, target)

    def _loading(self, token: int, text: str, target: str) -> None:
        if not self._session.current(token):
            return
        self._card, self._result, self._editable_now, self._waiting = True, None, False, None
        self._keys = frozenset({ESC})
        self._view.show_loading(*self._point, target, text)

    def _editable_done(self, read_id: int, editable: bool) -> None:
        if read_id != self._read_id:
            return          # 已换了一次选区或卡片已关
        self._editable_known = editable
        if self._waiting is not None:
            token, t, target = self._waiting
            self._waiting = None
            if self._session.current(token):
                self._show_result(t, target, editable)

    def _show_result(self, t: Translation, target: str, editable: bool) -> None:
        self._result, self._editable_now = t, editable
        self._keys = frozenset({ESC, ENTER}) if editable else frozenset({ESC})
        self._view.show_result(t, target, editable)

    def _finish(self, token, text, target, t, outcome) -> None:
        if not self._session.current(token):
            return          # 卡片已关或已重开: 丢弃迟到的结果
        self._job = (text, target, self._target)
        self._report(target, len(text.strip()), t, outcome)
        if outcome == "ok":
            if self._editable_known is None:
                self._waiting = (token, t, target)   # 等可编辑判断回来再显示
            else:
                self._show_result(t, target, self._editable_known)
        elif outcome == "no_service":
            self._view.show_no_service()
        else:
            # 整条链最多试 BUDGET_ATTEMPTS 家, 只列按顺序排在前面的这几家
            self._view.show_error(list(self._services())[:BUDGET_ATTEMPTS])

    # ---- 卡片按钮 ----
    def replace(self) -> None:
        t = self._result
        if t is None or not self._editable_now or not self._card:
            return
        job, chars = self._job, len(self._job[0].strip())
        window = job[2]                  # 翻译时的窗口; 之后的悬浮条可能已换成别的窗口
        target, epoch = job[1], self._epoch
        self.close()
        if self._foreground() != window:
            if self._copy_text(t.text):
                self._report(target, chars, t, "copied")
                self._view.toast(TARGET_LEFT)
            return

        def paste():
            try:
                outcome = self._deliver(window, t.text)
            except Exception as exc:     # deliver 不接剪贴板/按键的 OSError; 只记类型
                self._log("translate replace error: %s" % type(exc).__name__)
                outcome = "exception"
            if outcome in _REPLACED:
                self._report(target, chars, t, "replaced")
                return
            copied = outcome == "copied_only"
            self._report(target, chars, t, "copied" if copied else "error")
            self._post(lambda: self._replace_failed(epoch, job, t, copied))

        self._submit(paste)

    def _replace_failed(self, epoch: int, job: tuple, t: Translation, copied: bool) -> None:
        """替换没成: 重开结果卡片, 译文不丢。
        用户已开始新的翻译, 或原窗口已不在前台、另有窗口划了词(悬浮条开着)时只提示,
        免得下一次回车把译文贴进别的窗口。"""
        window = job[2]
        if (epoch != self._epoch or self._card or self._foreground() != window
                or self._window != window or self._view.bar_visible()):
            self._view.toast("没能替换，译文已复制" if copied else "没能替换，请重新翻译")
            return
        token = self._session.start()
        self._job, self._card, self._target = job, True, window
        self._loading(token, job[0], job[1])       # 卡片位置与状态由 show_loading 设定
        self._show_result(t, job[1], True)          # 只有可编辑的结果才会走到替换
        self._view.toast("没能替换，译文已复制" if copied else "没能替换，译文还在卡片里")

    def copy_result(self) -> None:
        t = self._result
        if t is None or not self._card:
            return
        target, chars = self._job[1], len(self._job[0].strip())
        self.close()
        if self._copy_text(t.text):
            self._report(target, chars, t, "copied")
            self._view.toast("已复制")

    def close(self) -> None:
        self._session.cancel()
        self._keys = frozenset()
        self._card, self._result, self._editable_now = False, None, False
        self._read_id, self._waiting = 0, None
        self._view.close_card()

    def _copy_text(self, text: str) -> bool:
        try:
            self._copy(text)
            return True
        except OSError:
            self._view.toast("复制失败")
            return False

    # ---- 键盘与鼠标 ----
    def card_keys(self) -> frozenset:
        """钩子线程调用: 卡片没开 → 空; 开着 → Esc; 译文已出且可替换 → Esc 和回车。
        别的窗口不经输入抢到前台时也返回空, 回车/Esc 交给那个窗口。"""
        keys = self._keys
        if keys and self._foreground() != self._target:
            return frozenset()
        return keys

    def key_down(self, vk: int) -> None:
        """钩子线程调用, 在把 on_key 交给主线程之前: 回车/Esc 以外的键要关卡片,
        先把集合换成空集, 免得主线程还没关卡片时紧跟的回车被截下又被忽略。"""
        if vk not in (ESC, ENTER):
            self._keys = frozenset()

    def on_card_key(self, vk: int) -> None:
        if vk == ESC:
            self.close()
        elif vk == ENTER:
            self.replace()

    def on_key(self, vk: int) -> None:
        self._hide_bar()
        if self._card and vk not in (ESC, ENTER):
            self.close()     # 该键照常交给原程序

    def on_mouse_down(self, x: int, y: int) -> None:
        if self._view.contains(x, y):
            return
        self._hide_bar()
        self.close()         # 还在读选区时点别处, 也丢弃这次翻译

    # ---- 日志 ----
    def _report(self, target: str, chars: int, t: Translation | None, outcome: str) -> None:
        service, ms = (t.service, t.ms) if t is not None else ("-", 0)
        self._log("translate: target=%s chars=%d service=%s ms=%d outcome=%s"
                  % (target, chars, service, ms, outcome))
