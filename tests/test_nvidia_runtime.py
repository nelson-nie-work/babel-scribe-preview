import json
import zipfile

import pytest

from scribe import download, nvidia_runtime
from scribe.asr import whisper

W = nvidia_runtime.Wheel("nvidia-cublas-cu12", "1.0", "nvidia_cublas_cu12-1.0-py3-none-win_amd64.whl",
                         100, "0" * 64, "aa/bb/nvidia_cublas_cu12-1.0-py3-none-win_amd64.whl")


@pytest.fixture
def tmp_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setattr(nvidia_runtime, "WHEELS", (W,))
    return tmp_path


@pytest.fixture
def no_site_runtime(monkeypatch, tmp_path):
    monkeypatch.setattr(whisper.site, "getusersitepackages", lambda: str(tmp_path / "nousersite"))
    monkeypatch.setattr(whisper.site, "getsitepackages", lambda: [str(tmp_path / "nosite")])
    monkeypatch.setattr(whisper.sys, "executable", str(tmp_path / "noexe" / "python.exe"))
    monkeypatch.delenv("CUDA_PATH", raising=False)
    monkeypatch.delenv("SCRIBE_CUDA_DLL_DIRS", raising=False)


@pytest.fixture
def fake_wheel(tmp_cache):
    path = nvidia_runtime.root() / "_downloads" / W.filename
    path.parent.mkdir(parents=True)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("nvidia/cublas/bin/cublas64_12.dll", b"dll-bytes")
        z.writestr("nvidia/cublas/include/x.h", b"header")
        z.writestr("nvidia/cublas/__init__.py", b"")
    return path


def test_extract_keeps_only_bin_dlls_and_marks_complete(tmp_cache, fake_wheel):
    nvidia_runtime.extract(W, fake_wheel)
    d = nvidia_runtime.bin_dir(W)
    assert sorted(p.name for p in d.iterdir()) == [".complete", "cublas64_12.dll"]
    assert json.loads((d / ".complete").read_text(encoding="utf-8")) == {"cublas64_12.dll": 9}
    assert not fake_wheel.exists() and nvidia_runtime.installed()
    assert nvidia_runtime.dll_dirs() == [d]


def test_bin_dir_layout(tmp_cache):
    assert nvidia_runtime.bin_dir(W) == (tmp_cache / "local" / "BABEL Scribe" / "Model Cache" / "nvidia"
                                         / "nvidia-cublas-cu12-1.0" / "bin")


def test_half_extracted_dir_is_not_installed(tmp_cache):
    d = nvidia_runtime.bin_dir(W)
    d.mkdir(parents=True)
    (d / "cublas64_12.dll").write_bytes(b"x")
    assert not nvidia_runtime.installed()
    assert nvidia_runtime.dll_dirs() == []


def test_truncated_dll_is_not_installed(tmp_cache, fake_wheel):
    nvidia_runtime.extract(W, fake_wheel)
    (nvidia_runtime.bin_dir(W) / "cublas64_12.dll").write_bytes(b"x")
    assert not nvidia_runtime.installed()


def test_items_use_all_mirrors_in_order():
    item = nvidia_runtime.items()[0]
    assert [u.split("/packages/")[0] for u in item.urls] == [
        "https://files.pythonhosted.org", "https://mirrors.aliyun.com/pypi", "https://pypi.tuna.tsinghua.edu.cn"]
    assert item.urls[0] == "https://files.pythonhosted.org/packages/" + nvidia_runtime.WHEELS[0].path
    assert item.path == "_downloads/" + nvidia_runtime.WHEELS[0].filename


def test_missing_bytes_counts_uninstalled_minus_partial(tmp_cache, fake_wheel):
    part = nvidia_runtime.root() / "_downloads" / (W.filename + ".part")
    fake_wheel.unlink()
    part.write_bytes(b"x" * 30)
    assert nvidia_runtime.missing_bytes() == 70


def test_install_downloads_then_extracts(tmp_cache, fake_wheel, monkeypatch):
    calls = []
    monkeypatch.setattr(download, "fetch", lambda items, dest, **kw: calls.append(([i.path for i in items], dest)))
    nvidia_runtime.install()
    assert calls == [(["_downloads/" + W.filename], nvidia_runtime.root())]
    assert nvidia_runtime.installed()


def test_install_skips_installed(tmp_cache, fake_wheel, monkeypatch):
    nvidia_runtime.extract(W, fake_wheel)
    calls = []
    monkeypatch.setattr(download, "fetch", lambda items, dest, **kw: calls.append(items))
    nvidia_runtime.install()
    assert calls == []


def test_remove_deletes_root(tmp_cache, fake_wheel):
    nvidia_runtime.extract(W, fake_wheel)
    nvidia_runtime.remove()
    assert not nvidia_runtime.root().exists() or not any(nvidia_runtime.root().iterdir())
    assert not nvidia_runtime.installed()


def test_runtime_dirs_are_searched_first(tmp_cache, fake_wheel):
    nvidia_runtime.extract(W, fake_wheel)
    assert whisper.search_dirs()[0] == str(nvidia_runtime.bin_dir(W))


def test_system_runtime_detected_from_cuda_path(tmp_cache, no_site_runtime, tmp_path, monkeypatch):
    assert not nvidia_runtime.system_runtime()
    (tmp_path / "cuda" / "bin").mkdir(parents=True)
    for name in nvidia_runtime.REQUIRED_DLLS:
        (tmp_path / "cuda" / "bin" / name).write_bytes(b"")
    monkeypatch.setenv("CUDA_PATH", str(tmp_path / "cuda"))
    assert nvidia_runtime.system_runtime()


def test_partial_system_runtime_is_not_enough(tmp_cache, no_site_runtime, tmp_path, monkeypatch):
    (tmp_path / "cuda" / "bin").mkdir(parents=True)
    (tmp_path / "cuda" / "bin" / "cublas64_12.dll").write_bytes(b"")
    monkeypatch.setenv("CUDA_PATH", str(tmp_path / "cuda"))
    assert not nvidia_runtime.system_runtime()


def test_manifest_is_cublas_only():
    # 2026-11-18 校准：Whisper 只用 cuBLAS（安装版 ctranslate2 4.8.2 复测同样），cuDNN 与 nvrtc 都不需要
    assert [w.package for w in nvidia_runtime.WHEELS] == ["nvidia-cublas-cu12"]
    assert nvidia_runtime.REQUIRED_DLLS == ("cublas64_12.dll", "cublasLt64_12.dll")
    assert all(w.path.endswith("/" + w.filename) for w in nvidia_runtime.WHEELS)
