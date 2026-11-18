"""faster-whisper 识别引擎(参数依据 2026-11-10 实测, 见 docs/specs §3.1)。"""
import os
import site
import sys
import threading
from pathlib import Path

import numpy as np

from scribe import model_files
from scribe.asr.filters import keep_segment
from scribe.config import WHISPER_COMPUTE


_dll_directories = {}


def search_dirs() -> list[str]:
    """NVIDIA 运行库 DLL 的搜索目录：本程序下载的运行库优先，其次是 SCRIBE_CUDA_DLL_DIRS、
    CUDA_PATH 与 pip 装的 nvidia 包。移植自 BABEL。"""
    from scribe import nvidia_runtime
    bases = [site.getusersitepackages()]
    try:
        bases += site.getsitepackages()
    except Exception:
        pass
    bases.append(str(Path(sys.executable).resolve().parent / "lib"))
    dirs = list(nvidia_runtime.dll_dirs())
    dirs += [Path(path) for path in os.environ.get("SCRIBE_CUDA_DLL_DIRS", "").split(os.pathsep) if path.strip()]
    if cuda := os.environ.get("CUDA_PATH"):
        dirs.append(Path(cuda) / "bin")
    for base in bases:
        nvidia = Path(base) / "nvidia"
        if nvidia.is_dir():
            dirs.extend(sub / "bin" for sub in nvidia.iterdir())
    return list(dict.fromkeys(str(path) for path in dirs if path.is_absolute() and path.is_dir()))


def _register_nvidia_dlls() -> None:
    """把运行库目录加入 DLL 搜索路径，否则 cuda 模式报 cublas64_12.dll not found。"""
    search = search_dirs()
    for directory in search:
        if directory not in _dll_directories:
            try:
                _dll_directories[directory] = os.add_dll_directory(directory)
            except OSError:
                pass
    if search:
        existing = os.environ.get("PATH", "").split(os.pathsep)
        os.environ["PATH"] = os.pathsep.join(dict.fromkeys(search + existing))


class WhisperEngine:
    def __init__(self, compute_type: str = WHISPER_COMPUTE):
        self.compute_type = compute_type
        self.ready = False
        self.failed = False          # 加载失败后为 True, controller 据此报错并丢弃录音
        self._model = None
        self._lock = threading.Lock()

    def load(self) -> None:
        self.failed = False
        try:
            from faster_whisper import WhisperModel
            model_files.adopt_legacy(model_files.WHISPER)
            if not model_files.ready(model_files.WHISPER):
                raise FileNotFoundError("whisper model not prepared")
            _register_nvidia_dlls()
            self._model = WhisperModel(str(model_files.model_dir(model_files.WHISPER)), device="cuda",
                                       compute_type=self.compute_type)
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
