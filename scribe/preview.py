"""实时字幕: 录音期间每 0.5 秒用 SenseVoice 识别最近 20 秒录音快照, 文字有变化才回调(spec §2.2)。

只是预览, 从不往输入框打字;stop() 之后保证不再回调。
"""
import threading
from typing import Callable, Optional

from scribe.audio import is_silent

MIN_AUDIO_S = 0.5


def tail_text(text: str, max_chars: int = 28) -> str:
    """超长只留末尾 max_chars 个字并加前缀"…"。"""
    return text if len(text) <= max_chars else "…" + text[-max_chars:]


class LivePreview:
    def __init__(self, engine, recorder, on_text: Callable[[str], None], interval: float = 0.5,
                 window_s: float = 20.0, sleep: Optional[Callable[[float], None]] = None):
        self.engine = engine
        self.recorder = recorder
        self.on_text = on_text
        self.interval = interval
        self.window_s = window_s
        self._sleep = sleep
        self._lock = threading.Lock()       # on_text 与 stop 互斥: stop 返回后不会再回调
        self._stop_evt: Optional[threading.Event] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self.stop()
        evt = threading.Event()
        t = threading.Thread(target=self._loop, args=(evt,), daemon=True, name="scribe-preview")
        with self._lock:
            self._stop_evt, self._thread = evt, t
        t.start()

    def stop(self) -> None:
        with self._lock:
            evt, t = self._stop_evt, self._thread
            self._stop_evt = self._thread = None
            if evt is not None:
                evt.set()
        if t is not None and t is not threading.current_thread():
            t.join(1.0)

    def _wait(self, evt: threading.Event) -> None:
        if self._sleep is None:
            evt.wait(self.interval)
        else:
            self._sleep(self.interval)

    def _loop(self, evt: threading.Event) -> None:
        last = ""
        sr = getattr(self.recorder, "sr", 16000)
        while not evt.is_set():
            self._wait(evt)
            if evt.is_set():
                break
            eng = self.engine
            if not getattr(eng, "ready", False) or getattr(eng, "failed", False):
                continue
            audio = self.recorder.snapshot(include_preroll=False)[-int(self.window_s * sr):]
            if len(audio) < int(MIN_AUDIO_S * sr) or is_silent(audio):
                continue
            try:
                text = (eng.transcribe(audio) or "").strip()
            except Exception:
                continue
            if not text or text == last:
                continue
            last = text
            with self._lock:
                if evt.is_set():
                    break
                self.on_text(tail_text(text))
