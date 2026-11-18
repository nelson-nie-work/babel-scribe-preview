"""SenseVoice-Small 识别引擎(sherpa-onnx, CPU, int8;参数见 docs/plans M2 Global Constraints)。"""
import threading
from collections.abc import Callable

import numpy as np

from scribe import download, model_files
from scribe.config import SAMPLE_RATE


class SenseVoiceEngine:
    def __init__(self, threads: int = 4, on_progress: Callable[[download.Progress], None] | None = None):
        self.threads = threads
        self.ready = False
        self.failed = False
        self.error = ""                                  # "" / "download" / "load"
        self.progress: download.Progress | None = None  # 下载中最近一次进度
        self._on_progress = on_progress
        self._rec = None
        self._lock = threading.Lock()

    def _progress(self, progress: download.Progress) -> None:
        self.progress = progress
        if self._on_progress is not None:
            self._on_progress(progress)

    def load(self) -> None:
        self.failed = False
        self.error = ""
        try:
            d = model_files.ensure(model_files.SENSEVOICE, on_progress=self._progress)
        except Exception:
            self.failed, self.error = True, "download"
            raise
        finally:
            self.progress = None
        try:
            import sherpa_onnx
            self._rec = sherpa_onnx.OfflineRecognizer.from_sense_voice(
                model=str(d / "model.int8.onnx"), tokens=str(d / "tokens.txt"), num_threads=self.threads,
                sample_rate=SAMPLE_RATE, language="auto", use_itn=True)
        except Exception:
            self.failed, self.error = True, "load"
            model_files.verify(model_files.SENSEVOICE)   # 删掉坏文件，下次重试会重下
            raise
        self.ready = True

    def unload(self) -> None:
        with self._lock:
            self.ready = False
            self._rec = None

    def transcribe(self, audio: np.ndarray) -> str:
        audio = np.nan_to_num(np.asarray(audio, dtype=np.float32))
        if len(audio) == 0:
            return ""
        with self._lock:
            return self._decode(audio)

    def try_transcribe(self, audio: np.ndarray) -> str | None:
        """引擎正忙(正式识别在跑)时立刻返回 None, 供实时字幕让路。"""
        audio = np.nan_to_num(np.asarray(audio, dtype=np.float32))
        if len(audio) == 0:
            return ""
        if not self._lock.acquire(blocking=False):
            return None
        try:
            return self._decode(audio)
        finally:
            self._lock.release()

    def _decode(self, audio: np.ndarray) -> str:
        if self._rec is None:
            return ""
        stream = self._rec.create_stream()
        stream.accept_waveform(SAMPLE_RATE, audio)
        self._rec.decode_stream(stream)
        return stream.result.text.strip()
