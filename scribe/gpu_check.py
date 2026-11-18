"""通过 nvml.dll 检测 NVIDIA 显卡，判断能否运行双引擎。"""
import ctypes
from collections.abc import Callable
from dataclasses import dataclass

GiB = 1024 ** 3
MIN_VRAM = 4 * GiB
MIN_DISK = 4 * GiB              # cuBLAS 下载 + 解压 + Whisper 模型 + 余量，见运行库校准报告
MIN_DRIVER = (528, 33)          # CUDA 12 在 Windows 上次版本兼容的最低驱动
DRIVER_URL = "https://www.nvidia.com/drivers/"

REASONS = {
    "no_gpu": "没有检测到 NVIDIA 显卡，双引擎需要 N 卡。",
    "old_driver": "显卡驱动太旧，请先更新驱动。",
    "low_vram": "显存不足 4 GB，无法运行 Whisper。",
    "low_disk": f"磁盘空间不足，需要约 {MIN_DISK // GiB} GB。",
    "download": "下载失败，请检查网络后重试。",
    "load": "Whisper 加载失败，详情见日志。",
}


@dataclass(frozen=True)
class Gpu:
    name: str
    vram: int
    driver: tuple[int, ...]
    driver_text: str


class _Memory(ctypes.Structure):
    _fields_ = [("total", ctypes.c_ulonglong), ("free", ctypes.c_ulonglong), ("used", ctypes.c_ulonglong)]


def _parse_driver(text: str) -> tuple[int, ...]:
    parts = []
    for piece in text.split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def detect(load: Callable[[], object] = lambda: ctypes.WinDLL("nvml.dll")) -> Gpu | None:
    try:
        nvml = load()
    except OSError:
        return None
    try:
        if nvml.nvmlInit_v2() != 0:
            return None
    except Exception:
        return None
    try:
        buf = ctypes.create_string_buffer(80)
        if nvml.nvmlSystemGetDriverVersion(buf, 80) != 0:
            return None
        driver_text = buf.value.decode(errors="replace")
        count = ctypes.c_uint(0)
        if nvml.nvmlDeviceGetCount_v2(ctypes.pointer(count)) != 0:
            return None
        best = None
        for index in range(count.value):
            handle = ctypes.c_void_p()
            if nvml.nvmlDeviceGetHandleByIndex_v2(index, ctypes.pointer(handle)) != 0:
                return None
            name = ctypes.create_string_buffer(96)
            if nvml.nvmlDeviceGetName(handle, name, 96) != 0:
                return None
            memory = _Memory()
            if nvml.nvmlDeviceGetMemoryInfo(handle, ctypes.pointer(memory)) != 0:
                return None
            gpu = Gpu(name.value.decode(errors="replace"), memory.total, _parse_driver(driver_text), driver_text)
            if best is None or gpu.vram > best.vram:
                best = gpu
        return best
    except Exception:
        return None
    finally:
        try:
            nvml.nvmlShutdown()
        except Exception:
            pass


def check(gpu: Gpu | None, free_disk: int, need_disk: int = MIN_DISK) -> str | None:
    if gpu is None:
        return "no_gpu"
    if gpu.driver < MIN_DRIVER:
        return "old_driver"
    if gpu.vram < MIN_VRAM:
        return "low_vram"
    if free_disk < need_disk:
        return "low_disk"
    return None
