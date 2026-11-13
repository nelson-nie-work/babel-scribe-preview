"""把热键、录音、识别流水线、输出、胶囊串起来。

线程约定: handle()/tick() 与所有胶囊状态改动都在主线程;
识别与输出在 run_async 安排的工作线程里跑, 结果与阶段变化经 post() 回到主线程。
engine 是 pipeline: ready、failed、process(audio, on_stage) -> Result。
"""
import collections
import time
from typing import Callable, Optional

from scribe.audio import Recorder, is_silent
from scribe.capsule import CapsuleModel
from scribe.hotkey import Action
from scribe.output import typed_text
from scribe.pipeline import Result

_OK_RESULTS = ("pasted", "typed")
FALLBACK_HINT = "整理服务不可用,已输入原文"
LEARN_WINDOW_S = 600


class Controller:
    def __init__(self, recorder: Recorder, engine, output: Callable[[str, int], str],
                 capsule: CapsuleModel, on_change: Callable[[], None] = lambda: None,
                 clock: Callable[[], float] = time.monotonic,
                 run_async: Optional[Callable[[Callable[[], None]], None]] = None,
                 post: Callable[[Callable[[], None]], None] = lambda fn: fn(),
                 log: Callable[[str], None] = lambda line: None,
                 foreground: Callable[[], int] = lambda: 0,
                 on_start: Callable[[], None] = lambda: None,
                 on_stop: Callable[[], None] = lambda: None,
                 on_output: Callable[[str, int], None] = lambda text, target: None,
                 identify: Callable[[int], str] = lambda target: "",
                 on_record: Callable[[Result, str, int, str], None] = lambda res, app, ms, outcome: None):
        self.recorder = recorder
        self.engine = engine
        self.output = output
        self.capsule = capsule
        self.on_change = on_change
        self.clock = clock
        self.run_async = run_async or (lambda fn: fn())
        self.post = post
        self.log = log
        self.foreground = foreground        # 松开时记下前台窗口, 输出时不一样就只复制
        self.on_start = on_start            # 录音开始: 预热整理服务 + 启动实时字幕
        self.on_stop = on_stop              # 录音结束/取消: 停实时字幕
        self.on_output = on_output
        self.identify = identify
        self.on_record = on_record
        self._queue = collections.deque()   # (audio, 松开时刻, 目标窗口, 目标应用)
        self._busy = False
        self._last_output: Optional[tuple[int, str, float]] = None

    # ---- 主线程
    def handle(self, action: Action) -> None:
        if action is Action.START:
            self.recorder.begin()
            self.capsule.listening(self.clock())
            self.on_start()
        elif action is Action.LOCK:
            self.capsule.lock()
        elif action is Action.CANCEL:
            self.on_stop()
            self.recorder.cancel()
            self.capsule.hide()
        elif action is Action.STOP:
            self.on_stop()
            audio = self.recorder.end()
            if is_silent(audio):
                self.capsule.hide()
            else:
                self.capsule.processing()      # 先离开 listening, 否则会被当成"新录音进行中"
                released, target = self.clock(), self.foreground()
                self._queue.append((audio, released, target, self.identify(target)))
                self._pump()
        self.on_change()

    def tick(self) -> None:
        if self.recorder.recording and self.recorder.over_limit:
            self.handle(Action.STOP)
            return
        self._pump()

    @property
    def can_learn(self) -> bool:
        return not (self.recorder.recording or self._busy or self._queue)

    def learn_source(self, target: int) -> Optional[str]:
        """只有同一个窗口最近成功出字、且没有录音或处理在途时才学词。"""
        if not target or not self.can_learn or self._last_output is None:
            return None
        hwnd, text, when = self._last_output
        return text if hwnd == target and self.clock() - when <= LEARN_WINDOW_S else None

    def on_preview(self, text: str) -> None:
        """实时字幕回调(任意线程)。只在聆听中显示, 松手后迟到的一律丢弃。"""
        def apply():
            if self._listening():
                self.capsule.set_preview(text)
                self.on_change()
        self.post(apply)

    def _on_stage(self, stage: str) -> None:
        def apply():
            if stage == "polish" and self.capsule.state == "processing":
                self.capsule.polishing()
                self.on_change()
        self.post(apply)

    def _listening(self) -> bool:
        return self.capsule.state == "listening"

    def _pump(self) -> None:
        if self._busy or not self._queue:
            return
        if getattr(self.engine, "failed", False):
            self._queue.clear()             # 模型加载失败: 不再积压录音, 明确报错
            if not self._listening():
                self.capsule.error("模型未就绪")
                self.on_change()
            return
        if not self.engine.ready:
            if not self._listening() and self.capsule.state != "loading":
                self.capsule.loading()
                self.on_change()
            return
        self._busy = True
        audio, t_release, target, app = self._queue.popleft()
        if not self._listening():
            self.capsule.processing()
            self.on_change()
        self.run_async(lambda: self._work(audio, t_release, target, app))

    def _finish(self, result: str, err: Optional[Exception], path: str, text: str, target: int) -> None:
        self._busy = False
        if err is None and result in _OK_RESULTS and text:
            delivered = typed_text(text) if result == "typed" else text
            self._last_output = (target, delivered, self.clock())
            if self.can_learn:
                self.on_output(delivered, target)
        if not self._listening():
            if err is not None:
                self.capsule.error("识别出错")
            elif result in _OK_RESULTS and path == "fallback":
                self.capsule.error(FALLBACK_HINT)
            elif result in _OK_RESULTS:
                self.capsule.done()
            elif result == "copied_only":
                self.capsule.error("已复制,请手动粘贴")
            elif result == "clipboard_protected":
                self.capsule.error("剪贴板已保留，听写可在历史中复制")
            elif result == "failed":
                self.capsule.error("粘贴失败")
            else:
                self.capsule.hide()
            self.on_change()
        self._pump()

    # ---- 工作线程
    def _work(self, audio, t_release: float, target: int, app: str) -> None:
        result, err, path, text = "empty", None, "", ""
        res = None
        try:
            res = self.engine.process(audio, on_stage=self._on_stage)
            path = res.path
            text = res.text
            t_text = self.clock()
            result = self.output(res.text, target)
            self.log("%d ms, %.1f s, %s, %d 字, %s, %s" % (
                (t_text - t_release) * 1000, len(audio) / self.recorder.sr, result, len(res.text),
                res.path, res.provider or "-"))
        except Exception as e:     # 识别或输出出错: 报在胶囊上, 不让整个程序停下
            err = e
            self.log("error: %r" % (e,))
        self.post(lambda: self._finish(result, err, path, text, target))
        if res is not None and text:
            elapsed_ms = max(0, round((t_text - t_release) * 1000))
            self.on_record(res, app, elapsed_ms, result if err is None else "failed")
