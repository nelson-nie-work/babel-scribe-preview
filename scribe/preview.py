"""实时字幕 + 边录边识别(spec §2.2, 2026-11-17 长段基线)。

录音期间每 0.5 秒取一次录音快照:
1. 未识别部分满 8 秒且遇到停顿, 就切下一段(scribe.segmenter)交给 SenseVoice 正式识别, 结果存进本次录音的
   LiveSession。松手后流水线只需识别最后一段, 长录音也不用整段重解, 识别更准(真人长录音整段识别会大段漏字)。
2. 字幕 = 已识别的小段 + 未切部分的最近 8 秒。引擎正忙时字幕跳过这一轮。

只是预览, 从不往输入框打字; stop() 之后保证不再回调, 且立即返回(松手时主线程不卡)。
stop() 返回本次录音的 LiveSession, 由处理队列带着走; 下一次录音另开一个, 互不串用。
快照包含按下前的预录, 与松手后 Recorder.end() 的音频起点一致, 小段的位置可以直接对上。
"""
import threading
from typing import Callable, Optional

from scribe.audio import is_silent
from scribe.segmenter import join_texts, next_cut, pause_length, soften_end

MIN_AUDIO_S = 0.5
SESSION_WAIT_S = 5.0        # 松手后等本次录音在途的那一段识别完, 最多等 5 秒
_SEGMENT_MIN_VOICED = 3


def tail_text(text: str, max_chars: int = 28) -> str:
    """超长只留末尾 max_chars 个字并加前缀"…"。"""
    return text if len(text) <= max_chars else "…" + text[-max_chars:]


class LiveSession:
    """一次录音里已经正式识别的小段: [(开始采样, 结束采样, 文字)], 首尾相接、从 0 开始。"""

    def __init__(self):
        self._parts: list[tuple[int, int, str]] = []
        self._lock = threading.Lock()
        self._done = threading.Event()

    @property
    def end(self) -> int:
        with self._lock:
            return self._parts[-1][1] if self._parts else 0

    def add(self, start: int, stop: int, text: str) -> None:
        with self._lock:
            self._parts.append((start, stop, text))

    def texts(self) -> list[str]:
        with self._lock:
            return [text for _a, _b, text in self._parts]

    def finish(self) -> None:
        self._done.set()

    def segments(self, timeout: float = SESSION_WAIT_S) -> list[tuple[int, int, str]]:
        """工作线程调用: 等录音线程收尾(在途那段识别完)后返回全部小段。"""
        self._done.wait(timeout)
        with self._lock:
            return list(self._parts)


class LivePreview:
    def __init__(self, engine, recorder, on_text: Callable[[str], None], interval: float = 0.5,
                 window_s: float = 8.0, sleep: Optional[Callable[[float], None]] = None):
        self.engine = engine
        self.recorder = recorder
        self.on_text = on_text
        self.interval = interval
        self.window_s = window_s
        self._sleep = sleep
        self._lock = threading.Lock()       # on_text 与 stop 互斥: stop 返回后不会再回调
        self._stop_evt: Optional[threading.Event] = None
        self._session: Optional[LiveSession] = None

    def start(self) -> None:
        self.stop()
        evt, session = threading.Event(), LiveSession()
        t = threading.Thread(target=self._loop, args=(evt, session), daemon=True, name="scribe-preview")
        with self._lock:
            self._stop_evt, self._session = evt, session
        t.start()

    def stop(self) -> Optional[LiveSession]:
        """立即返回本次录音的 LiveSession(没在录音时为 None)。在途的那段识别会自行完成并记入。"""
        with self._lock:
            evt, session = self._stop_evt, self._session
            self._stop_evt = self._session = None
            if evt is not None:
                evt.set()
        return session

    def _wait(self, evt: threading.Event) -> None:
        if self._sleep is None:
            evt.wait(self.interval)
        else:
            self._sleep(self.interval)

    def _loop(self, evt: threading.Event, session: LiveSession) -> None:
        try:
            self._run(evt, session)
        finally:
            session.finish()

    def _commit(self, evt, session, audio, sr) -> bool:
        """切出并正式识别所有已成熟的小段;识别出错返回 False, 本次录音不再边录边识别。"""
        while not evt.is_set() and (cut := next_cut(audio, session.end, sr)) is not None:
            start = session.end
            piece = audio[start:cut]
            try:
                text = "" if is_silent(piece, min_voiced_blocks=_SEGMENT_MIN_VOICED) else \
                    (self.engine.transcribe(piece) or "").strip()
            except Exception:
                return False
            # 切点之后还有录音, 这里一定是衔接处: 句中换气的段末句号改逗号
            session.add(start, cut, soften_end(text, pause_length(audio, cut, sr)))
        return True

    def _run(self, evt: threading.Event, session: LiveSession) -> None:
        last = ""
        sr = getattr(self.recorder, "sr", 16000)
        segmenting = True
        while not evt.is_set():
            self._wait(evt)
            if evt.is_set():
                break
            eng = self.engine
            if not getattr(eng, "ready", False) or getattr(eng, "failed", False):
                continue
            audio = self.recorder.snapshot(include_preroll=True)
            if segmenting:
                segmenting = self._commit(evt, session, audio, sr)
                if evt.is_set():
                    break
            tail = audio[session.end:][-int(self.window_s * sr):] if segmenting else audio[-int(self.window_s * sr):]
            done = session.texts() if segmenting else []
            if len(tail) < int(MIN_AUDIO_S * sr) or is_silent(tail):
                text = ""
            else:
                decode = getattr(eng, "try_transcribe", None) or eng.transcribe
                try:
                    text = decode(tail)
                except Exception:
                    continue
                if text is None:                # 正式识别占着引擎: 让路, 下一轮再试
                    continue
            shown = join_texts(done + [text])
            if not shown or shown == last:
                continue
            last = shown
            with self._lock:
                if evt.is_set():
                    break
                self.on_text(tail_text(shown))
