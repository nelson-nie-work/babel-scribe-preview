"""faster-whisper 识别引擎(参数依据 2026-11-10 实测, 见 docs/specs §3.1)。"""
import os
import site
import sys
import threading
from pathlib import Path

import numpy as np

from scribe.asr.filters import keep_segment
from scribe.config import WHISPER_COMPUTE, WHISPER_MODEL


_dll_directories = {}


def _register_nvidia_dlls() -> None:
    """把 pip 装的 NVIDIA 运行库(cuBLAS/cuDNN)目录加入 DLL 搜索路径,
    否则 cuda 模式报 cublas64_12.dll not found。移植自 BABEL。"""
    bases = [site.getusersitepackages()]
    try:
        bases += site.getsitepackages()
    except Exception:
        pass
    bases.append(str(Path(sys.executable).resolve().parent / "lib"))
    dirs = [Path(path) for path in os.environ.get("SCRIBE_CUDA_DLL_DIRS", "").split(os.pathsep) if path.strip()]
    if cuda := os.environ.get("CUDA_PATH"):
        dirs.append(Path(cuda) / "bin")
    for base in bases:
        nvidia = Path(base) / "nvidia"
        if nvidia.is_dir():
            dirs.extend(sub / "bin" for sub in nvidia.iterdir())
    search = [str(path) for path in dirs if path.is_absolute() and path.is_dir()]
    for directory in dict.fromkeys(search):
        if directory not in _dll_directories:
            try:
                _dll_directories[directory] = os.add_dll_directory(directory)
            except OSError:
                pass
    if search:
        existing = os.environ.get("PATH", "").split(os.pathsep)
        os.environ["PATH"] = os.pathsep.join(dict.fromkeys(search + existing))


class WhisperEngine:
    def __init__(self, model: str = WHISPER_MODEL, compute_type: str = WHISPER_COMPUTE):
        self.model_name = model
        self.compute_type = compute_type
        self.ready = False
        self.failed = False          # 加载失败后为 True, controller 据此报错并丢弃录音
        self._model = None
        self._lock = threading.Lock()

    def load(self) -> None:
        self.failed = False
        try:
            from faster_whisper import WhisperModel
            _register_nvidia_dlls()
            self._model = WhisperModel(self.model_name, device="cuda", compute_type=self.compute_type)
        except Exception:
            self.failed = True
            raise
        self.ready = True

    def unload(self) -> None:
        with self._lock:
            self.ready = False
            self._model = None

    def transcribe(self, audio: np.ndarray, *, hotwords: str = "") -> str:
        audio = np.nan_to_num(audio).astype(np.float32)
        options = {"hotwords": hotwords} if hotwords else {}
        with self._lock:
            if self._model is None:
                return ""
            segments, _info = self._model.transcribe(
                audio, language=None, beam_size=1, vad_filter=True,
                condition_on_previous_text=False, **options)
            parts = [s.text.strip() for s in segments
                     if keep_segment(s.text, s.no_speech_prob, s.avg_logprob, s.compression_ratio)]
        return "".join(p for p in parts if p)
