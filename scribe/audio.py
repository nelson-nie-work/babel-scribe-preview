"""录音: 常开麦克风写环形缓冲(含按下前预录)、录音上限、静音判断。"""
import collections
import math
import threading
from typing import Callable, Optional

import numpy as np

from scribe.config import BLOCK, MAX_RECORD_S, PREROLL_MS, SAMPLE_RATE


class Recorder:
    """不碰硬件的录音核心: 音频回调线程调 feed(), 主线程调 begin()/end()/cancel()。"""

    def __init__(self, sr: int = SAMPLE_RATE, preroll_ms: int = PREROLL_MS,
                 max_s: float = MAX_RECORD_S, block: int = BLOCK):
        self.sr = sr
        self._max_samples = int(max_s * sr)
        self._preroll = collections.deque(maxlen=max(1, math.ceil(preroll_ms * sr / 1000 / block)))
        self._chunks = []
        self._n = 0
        self._preroll_samples = 0
        self._lock = threading.Lock()
        self.recording = False
        self.over_limit = False
        self.level = 0.0

    def feed(self, block: np.ndarray) -> None:
        self.level = float(np.sqrt(np.mean(block.astype(np.float32) ** 2))) if len(block) else 0.0
        with self._lock:
            if not self.recording:
                self._preroll.append(block)
                return
            if self.over_limit:
                return
            room = self._max_samples - self._n
            if len(block) >= room:
                block = block[:room]
                self.over_limit = True
            self._chunks.append(block)
            self._n += len(block)

    def begin(self) -> None:
        with self._lock:
            self._chunks = list(self._preroll)
            self._n = sum(len(c) for c in self._chunks)
            self._preroll_samples = self._n
            self._preroll.clear()
            self.recording = True
            self.over_limit = False

    def end(self) -> np.ndarray:
        with self._lock:
            chunks, self._chunks, self._n = self._chunks, [], 0
            self._preroll_samples = 0
            self.recording = False
            self.over_limit = False
        if not chunks:
            return np.zeros(0, np.float32)
        return np.concatenate(chunks).astype(np.float32)

    def snapshot(self, *, include_preroll: bool = True) -> np.ndarray:
        """录音中: 当前已录音频的拷贝(不结束录音);不在录音: 空数组。供实时字幕用。"""
        with self._lock:
            chunks = list(self._chunks) if self.recording else []
            offset = 0 if include_preroll else self._preroll_samples
        if not chunks:
            return np.zeros(0, np.float32)
        return np.concatenate(chunks)[offset:].astype(np.float32)

    def cancel(self) -> None:
        self.end()

    def clear_preroll(self) -> None:
        with self._lock:
            self._preroll.clear()


def is_silent(audio: np.ndarray, block: int = BLOCK, threshold: float = 0.006,
              min_voiced_blocks: int = 10) -> bool:
    """RMS 超过 threshold 的 30 ms 块少于 min_voiced_blocks 个(说话不足 0.3 秒)即判为静音。"""
    n = len(audio) // block
    if n == 0:
        return True
    frames = audio[: n * block].astype(np.float32).reshape(n, block)
    rms = np.sqrt(np.mean(frames ** 2, axis=1))
    return int(np.count_nonzero(rms > threshold)) < min_voiced_blocks


class MicStream:
    """sounddevice 常开输入流, 每块转给 Recorder。"""

    def __init__(self, recorder: Recorder, on_level: Optional[Callable[[float], None]] = None,
                 device=None):
        self.recorder = recorder
        self.on_level = on_level
        self.device = device
        self._stream = None

    def _callback(self, indata, frames, t, status):
        self.recorder.feed(indata[:, 0].copy())
        if self.on_level:
            self.on_level(self.recorder.level)

    def start(self) -> None:
        import sounddevice as sd
        stream = sd.InputStream(samplerate=self.recorder.sr, channels=1, dtype="float32",
                                blocksize=BLOCK, device=self.device, callback=self._callback)
        try:
            stream.start()
        except Exception:
            stream.close()
            raise
        self._stream = stream

    def stop(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
            finally:
                stream.close()


def input_devices() -> dict[str, int | None]:
    import sounddevice as sd

    apis = sd.query_hostapis()
    devices = {"": None}
    for index, row in enumerate(sd.query_devices()):
        if row["max_input_channels"]:
            label = f"{row['name']} ({apis[row['hostapi']]['name']})"
            devices.setdefault(label, index)
    return devices
