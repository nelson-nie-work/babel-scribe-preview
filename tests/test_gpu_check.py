import pytest

from scribe import gpu_check
from scribe.gpu_check import Gpu

GiB = 1024 ** 3


def _raise(error):
    def fn(*args, **kwargs):
        raise error
    return fn


class FakeNvml:
    """属性为 Python 函数的假 nvml.dll；结构体参数用 ctypes.pointer 传入，经 .contents 写值。"""

    def __init__(self, name=b"GPU", total=8 * GiB, driver=b"591.44", init_rc=0, cards=None):
        self.cards = cards or [(name, total)]
        self.driver = driver
        self.init_rc = init_rc
        self.shutdown_calls = 0

    def nvmlInit_v2(self):
        return self.init_rc

    def nvmlShutdown(self):
        self.shutdown_calls += 1
        return 0

    def nvmlSystemGetDriverVersion(self, buf, size):
        buf.value = self.driver
        return 0

    def nvmlDeviceGetCount_v2(self, count):
        count.contents.value = len(self.cards)
        return 0

    def nvmlDeviceGetHandleByIndex_v2(self, index, handle):
        handle.contents.value = index + 1
        return 0

    def nvmlDeviceGetName(self, handle, buf, size):
        buf.value = self.cards[handle.value - 1][0]
        return 0

    def nvmlDeviceGetMemoryInfo(self, handle, memory):
        memory.contents.total = self.cards[handle.value - 1][1]
        return 0


def test_missing_nvml_means_no_gpu():
    assert gpu_check.detect(load=_raise(OSError())) is None


def test_detect_reads_name_vram_driver():
    nvml = FakeNvml(name=b"NVIDIA GeForce RTX 5070 Ti", total=16 * GiB, driver=b"591.44")
    gpu = gpu_check.detect(load=lambda: nvml)
    assert gpu == Gpu("NVIDIA GeForce RTX 5070 Ti", 16 * GiB, (591, 44), "591.44")
    assert nvml.shutdown_calls == 1


def test_largest_card_is_chosen():
    nvml = FakeNvml(cards=[(b"small", 4 * GiB), (b"big", 12 * GiB)])
    assert gpu_check.detect(load=lambda: nvml).name == "big"


def test_nvml_error_means_no_gpu():
    assert gpu_check.detect(load=lambda: FakeNvml(init_rc=9)) is None


def test_zero_cards_means_no_gpu():
    nvml = FakeNvml()
    nvml.cards = []
    assert gpu_check.detect(load=lambda: nvml) is None


@pytest.mark.parametrize("gpu,disk,reason", [
    (None, 100 * GiB, "no_gpu"),
    (Gpu("x", 16 * GiB, (520, 0), "520.0"), 100 * GiB, "old_driver"),
    (Gpu("x", 2 * GiB, (591, 44), "591.44"), 100 * GiB, "low_vram"),
    (Gpu("x", 16 * GiB, (591, 44), "591.44"), 1 * GiB, "low_disk"),
    (Gpu("x", 16 * GiB, (591, 44), "591.44"), 100 * GiB, None),
])
def test_check_reasons(gpu, disk, reason):
    assert gpu_check.check(gpu, disk) == reason


def test_check_uses_given_disk_need():
    gpu = Gpu("x", 16 * GiB, (591, 44), "591.44")
    assert gpu_check.check(gpu, 2 * GiB, need_disk=1 * GiB) is None


def test_min_disk_covers_cublas_and_whisper():
    # 剩余下载 2.03 GiB + 解压 0.72 GiB + 0.5 GiB 余量 = 3.24 GiB，取整 4 GiB
    assert gpu_check.MIN_DISK == 4 * GiB
    assert gpu_check.check(Gpu("x", 16 * GiB, (591, 44), "591.44"), 4 * GiB) is None


def test_reasons_cover_every_code():
    assert set(gpu_check.REASONS) == {"no_gpu", "old_driver", "low_vram", "low_disk", "download", "load"}
    assert gpu_check.REASONS["low_disk"] == f"磁盘空间不足，需要约 {gpu_check.MIN_DISK // GiB} GB。"
