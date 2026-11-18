"""固定版本的模型文件清单：下载、接管旧 Hugging Face 缓存、核对与删除。"""
import logging
import os
import shutil
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from scribe import download
from scribe.paths import model_cache_dir

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Model:
    name: str
    repo: str
    revision: str
    files: tuple[tuple[str, int, str], ...]       # (文件名, 字节, SHA-256)


# 2026-11-18 在本机缓存上实测
SENSEVOICE = Model(
    "sensevoice", "csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17",
    "2365baeacb507f821a0c8120fcee3d484dba7a07", (
        ("model.int8.onnx", 239233841, "c71f0ce00bec95b07744e116345e33d8cbbe08cef896382cf907bf4b51a2cd51"),
        ("tokens.txt", 315894, "f449eb28dc567533d7fa59be34e2abca8784f771850c78a47fb731a31429a1dc"),
    ))

WHISPER = Model(
    "whisper", "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
    "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf", (
        ("config.json", 2263, "b0253ea6c0d3bea6b1e19e91a02acfd3b53f4467362efcb5a3e6b16c9b3a9b7e"),
        ("model.bin", 1617884929, "e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da"),
        ("preprocessor_config.json", 340, "7ccc62c6f2765af1f3b46c00c9b5894426835a05021c8b9c01eecb6dfb542711"),
        ("tokenizer.json", 2710337, "297b13372ac43916285644fb9687add3cc62ee2a1adb60da3dc25cc94c1871fd"),
        ("vocabulary.json", 1068114, "c69260f2ab26d659b7c398f9a2b2b48ed0df16c3b47d7326782fd9cba71690c1"),
    ))


def sources() -> list[str]:
    result = []
    if endpoint := os.environ.get("HF_ENDPOINT", "").strip():
        result.append(endpoint.rstrip("/"))
    for default in ("https://huggingface.co", "https://hf-mirror.com"):
        if default not in result:
            result.append(default)
    return result


def items(model: Model) -> list[download.Item]:
    roots = sources()
    return [download.Item(name, size, digest,
                          tuple(f"{root}/{model.repo}/resolve/{model.revision}/{name}" for root in roots))
            for name, size, digest in model.files]


def model_dir(model: Model) -> Path:
    return model_cache_dir() / "models" / model.name


def ready(model: Model) -> bool:
    d = model_dir(model)
    return all(download.present(i, d) for i in items(model))


def missing_bytes(model: Model) -> int:
    d = model_dir(model)
    total = 0
    for name, size, _digest in model.files:
        if (d / name).is_file() and (d / name).stat().st_size == size:
            continue
        part = d / (name + ".part")
        total += size - (min(part.stat().st_size, size) if part.is_file() else 0)
    return total


def _legacy_roots() -> list[Path]:
    roots = []
    if hf_home := os.environ.get("HF_HOME"):
        roots.append(Path(hf_home) / "hub")
    roots.append(Path.home() / ".cache" / "huggingface" / "hub")
    roots.append(model_cache_dir() / "hub")
    return roots


def adopt_legacy(model: Model) -> int:
    """旧 Hugging Face 缓存里大小与哈希都对的文件直接接管，免得重下。"""
    d = model_dir(model)
    folder = "models--" + model.repo.replace("/", "--")
    adopted = 0
    for name, size, digest in model.files:
        target = d / name
        if target.is_file() and target.stat().st_size == size:
            continue
        for root in _legacy_roots():
            found = False
            for candidate in sorted((root / folder / "snapshots").glob(f"*/{name}")):
                try:
                    real = candidate.resolve()
                    if real.stat().st_size != size or download.sha256_file(real) != digest:
                        continue
                    d.mkdir(parents=True, exist_ok=True)
                    target.unlink(missing_ok=True)
                    try:
                        os.link(real, target)
                    except OSError:
                        part = d / (name + ".part")
                        shutil.copyfile(real, part)
                        os.replace(part, target)
                except OSError as error:
                    log.warning("接管旧缓存失败：%s（%s）", candidate, error)
                    continue
                log.info("接管旧缓存：%s", real)
                adopted += 1
                found = True
                break
            if found:
                break
    return adopted


def ensure(model: Model, *, on_progress: Callable[[download.Progress], None] = lambda p: None,
           cancel: threading.Event | None = None) -> Path:
    adopt_legacy(model)
    d = model_dir(model)
    download.fetch(items(model), d, on_progress=on_progress, cancel=cancel)
    return d


def verify(model: Model) -> bool:
    d = model_dir(model)
    ok = True
    for name, size, digest in model.files:
        path = d / name
        if not path.is_file():
            ok = False
            continue
        if path.stat().st_size != size or download.sha256_file(path) != digest:
            log.warning("模型文件损坏，已删除：%s", path)
            path.unlink(missing_ok=True)
            ok = False
    return ok


def remove(model: Model) -> None:
    shutil.rmtree(model_dir(model), ignore_errors=True)
