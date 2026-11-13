"""SenseVoice-Small 识别引擎(sherpa-onnx, CPU, int8;参数见 docs/plans M2 Global Constraints)。"""
import threading
from pathlib import Path

import numpy as np

from scribe.config import SAMPLE_RATE

REPO = "csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"
FILES = ["model.int8.onnx", "tokens.txt"]


class SenseVoiceEngine:
    def __init__(self, threads: int = 4):
        self.threads = threads
        self.ready = False
        self.failed = False
        self._rec = None
        self._lock = threading.Lock()

    def load(self) -> None:
        self.failed = False
        try:
            import sherpa_onnx
            from huggingface_hub import snapshot_download
            d = Path(snapshot_download(REPO, allow_patterns=FILES))
            self._rec = sherpa_onnx.OfflineRecognizer.from_sense_voice(
                model=str(d / FILES[0]), tokens=str(d / FILES[1]), num_threads=self.threads,
                sample_rate=SAMPLE_RATE, language="auto", use_itn=True)
        except Exception:
            self.failed = True
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
            if self._rec is None:
                return ""
            stream = self._rec.create_stream()
            stream.accept_waveform(SAMPLE_RATE, audio)
            self._rec.decode_stream(stream)
            return stream.result.text.strip()
