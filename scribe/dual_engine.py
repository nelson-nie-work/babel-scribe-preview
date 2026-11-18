"""双引擎准备：显卡检测 → 下载 NVIDIA 运行库 → 下载 Whisper 模型，一个状态机串起来，与界面无关。"""
import os
import shutil
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path

from scribe import gpu_check, model_files, nvidia_runtime
from scribe.download import Cancelled, DownloadFailed, Progress
from scribe.paths import model_cache_dir


class Phase(Enum):
    IDLE = "idle"
    DOWNLOADING = "downloading"
    READY = "ready"
    FAILED = "failed"


@dataclass(frozen=True)
class SetupState:
    phase: Phase
    reason: str = ""
    runtime: Progress | None = None
    model: Progress | None = None
    files_bytes: int = 0


def _free_disk() -> int:
    return shutil.disk_usage(model_cache_dir()).free


def _spawn(fn: Callable[[], None]) -> None:
    threading.Thread(target=fn, name="scribe-dual-setup", daemon=True).start()


def _dir_bytes(path: Path) -> int:
    total = 0
    for base, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(base, name))
            except OSError:
                pass
    return total


class DualEngineSetup:
    def __init__(self, *, on_change: Callable[[SetupState], None], detect=gpu_check.detect,
                 free_disk: Callable[[], int] = _free_disk, runtime=nvidia_runtime, models=model_files,
                 spawn: Callable[[Callable[[], None]], None] = _spawn, log: Callable[[str], None] = lambda line: None):
        self._on_change = on_change
        self._detect = detect
        self._free_disk = free_disk
        self._runtime = runtime
        self._models = models
        self._spawn = spawn
        self._log = log
        self._cancel = threading.Event()
        self._resume = False
        self._lock = threading.Lock()
        self.state = SetupState(Phase.READY if self.ready else Phase.IDLE, files_bytes=self._files_bytes())

    @property
    def ready(self) -> bool:
        runtime_ok = self._runtime.installed() or self._runtime.system_runtime()
        return bool(runtime_ok and self._models.ready(self._models.WHISPER))

    def _runtime_needed(self) -> bool:
        return not (self._runtime.installed() or self._runtime.system_runtime())

    def download_bytes(self) -> int:
        runtime = self._runtime.missing_bytes() if self._runtime_needed() else 0
        return runtime + self._models.missing_bytes(self._models.WHISPER)

    def _files_bytes(self) -> int:
        return _dir_bytes(self._runtime.root()) + _dir_bytes(self._models.model_dir(self._models.WHISPER))

    def _set(self, **changes) -> None:
        with self._lock:
            self.state = replace(self.state, files_bytes=self._files_bytes(), **changes)
            state = self.state
        self._on_change(state)

    def check(self) -> str | None:
        need = 0 if self.ready else gpu_check.MIN_DISK
        return gpu_check.check(self._detect(), self._free_disk(), need_disk=need)

    def start(self) -> str | None:
        if self.state.phase is Phase.DOWNLOADING:
            if self._cancel.is_set():     # 关了又马上打开：撤销取消，线程接着下或从断点重来
                self._cancel.clear()
                self._resume = True
            return None
        reason = self.check()
        if reason:
            self._set(phase=Phase.FAILED, reason=reason)
            return reason
        if self.ready:
            self._set(phase=Phase.READY, reason="")
            return None
        self._cancel = threading.Event()
        self._resume = False
        self._set(phase=Phase.DOWNLOADING, reason="", runtime=None, model=None)
        self._spawn(self._download)
        return None

    def _download(self) -> None:
        cancel = self._cancel
        while True:
            try:
                if self._runtime_needed():
                    self._runtime.install(on_progress=lambda p: self._set(runtime=p), cancel=cancel)
                else:
                    self._set(runtime=Progress(1, 1, 0.0, ""))
                self._models.ensure(self._models.WHISPER, on_progress=lambda p: self._set(model=p), cancel=cancel)
                break
            except Cancelled:
                if self._resume and not cancel.is_set():   # 线程退出前用户又打开了，从断点接着下
                    self._resume = False
                    continue
                self._set(phase=Phase.IDLE, reason="")
                return
            except DownloadFailed as error:
                self._log("dual setup download failed: %r" % (error.__cause__,))
                self._set(phase=Phase.FAILED, reason="download")
                return
            except Exception as error:
                self._log("dual setup error: %s" % type(error).__name__)
                self._set(phase=Phase.FAILED, reason="download")
                return
        self._set(phase=Phase.READY if self.ready else Phase.FAILED, reason="" if self.ready else "download")

    def cancel(self) -> None:
        self._cancel.set()

    def mark_load_failed(self) -> None:
        self._set(phase=Phase.FAILED, reason="load")

    def remove_files(self) -> None:
        if self.state.phase is Phase.DOWNLOADING:
            raise RuntimeError("dual engine files are downloading")
        self._runtime.remove()
        self._models.remove(self._models.WHISPER)
        self._set(phase=Phase.IDLE, reason="", runtime=None, model=None)


def active(pref: bool, setup: DualEngineSetup) -> bool:
    return bool(pref and setup.ready)
