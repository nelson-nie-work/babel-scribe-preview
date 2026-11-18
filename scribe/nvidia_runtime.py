"""从 PyPI 的 NVIDIA wheel 取出 cuBLAS 运行库 DLL，供 Whisper 在 N 卡上运行。"""
import json
import shutil
import threading
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from scribe import download
from scribe.paths import model_cache_dir


@dataclass(frozen=True)
class Wheel:
    package: str
    version: str
    filename: str
    size: int
    sha256: str
    path: str                   # 镜像 packages/ 之后的完整路径，含文件名


# PyPI 2026-11-18
WHEELS: tuple[Wheel, ...] = (
    Wheel("nvidia-cublas-cu12", "12.9.2.10", "nvidia_cublas_cu12-12.9.2.10-py3-none-win_amd64.whl",
          553162896, "623f43027d40d44ceadf0043f002bd25cf353e8f13ce90b9a87057019f560661",
          "20/e2/fc9a0e985249d873150276d5afb02e39a66817fedbf1a385724393e505ed/"
          "nvidia_cublas_cu12-12.9.2.10-py3-none-win_amd64.whl"),
)   # cuDNN 与 nvrtc 都不需要，见 docs/reports/2026-11-18-nvidia-runtime-calibration.md

MIRRORS = ("https://files.pythonhosted.org/packages",
           "https://mirrors.aliyun.com/pypi/packages",
           "https://pypi.tuna.tsinghua.edu.cn/packages")
REQUIRED_DLLS = ("cublas64_12.dll", "cublasLt64_12.dll")
_MARK = ".complete"


def root() -> Path:
    return model_cache_dir() / "nvidia"


def bin_dir(w: Wheel) -> Path:
    return root() / f"{w.package}-{w.version}" / "bin"


def _complete(w: Wheel) -> bool:
    d = bin_dir(w)
    try:
        listing = json.loads((d / _MARK).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(listing, dict) or not listing:
        return False
    for name, size in listing.items():
        f = d / name
        if not f.is_file() or f.stat().st_size != size:
            return False
    return True


def installed() -> bool:
    return all(_complete(w) for w in WHEELS)


def system_runtime() -> bool:
    """源码版用户自己装的 CUDA（CUDA_PATH、pip 的 nvidia 包等）已经够用。"""
    from scribe.asr.whisper import search_dirs
    dirs = [Path(d) for d in search_dirs()]
    return all(any((d / name).is_file() for d in dirs) for name in REQUIRED_DLLS)


def _item_path(w: Wheel) -> str:
    return f"_downloads/{w.filename}"


def items() -> list[download.Item]:
    return [download.Item(_item_path(w), w.size, w.sha256, tuple(f"{m}/{w.path}" for m in MIRRORS))
            for w in WHEELS]


def missing_bytes() -> int:
    total = 0
    for w in WHEELS:
        if _complete(w):
            continue
        wheel = root() / _item_path(w)
        if wheel.is_file() and wheel.stat().st_size == w.size:
            continue
        part = wheel.with_name(wheel.name + ".part")
        total += w.size - (min(part.stat().st_size, w.size) if part.is_file() else 0)
    return total


def extract(w: Wheel, wheel_path: Path) -> None:
    target = bin_dir(w)
    staging = target.with_name("bin.tmp")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    listing = {}
    with zipfile.ZipFile(wheel_path) as z:
        for info in z.infolist():
            parts = PurePosixPath(info.filename).parts
            if (len(parts) == 4 and parts[0] == "nvidia" and parts[2] == "bin"
                    and parts[3].lower().endswith(".dll")):
                with z.open(info) as src, open(staging / parts[3], "wb") as dst:
                    shutil.copyfileobj(src, dst, 1024 * 1024)
                listing[parts[3]] = info.file_size
    (staging / _MARK).write_text(json.dumps(listing, ensure_ascii=False), encoding="utf-8")
    shutil.rmtree(target, ignore_errors=True)
    staging.rename(target)
    Path(wheel_path).unlink(missing_ok=True)


def install(*, on_progress: Callable[[download.Progress], None] = lambda p: None,
            cancel: threading.Event | None = None) -> None:
    pending = [w for w in WHEELS if not _complete(w)]
    if not pending:
        return
    wanted = {_item_path(w) for w in pending}
    download.fetch([i for i in items() if i.path in wanted], root(), on_progress=on_progress, cancel=cancel)
    for w in pending:
        extract(w, root() / _item_path(w))


def dll_dirs() -> list[Path]:
    return [bin_dir(w) for w in WHEELS if _complete(w)]


def remove() -> None:
    shutil.rmtree(root(), ignore_errors=True)
