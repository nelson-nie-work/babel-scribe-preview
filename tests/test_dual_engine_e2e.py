"""从空缓存真实下载 NVIDIA 运行库与 Whisper 模型（约 2.2 GB），再用双引擎识别。

只在有 NVIDIA 显卡的电脑上手动跑：设 SCRIBE_E2E=1 后 pytest tests/test_dual_engine_e2e.py -m gpu
（不设就跳过，免得整套测试每次都下载）
"""
import os
import time

import pytest

from scribe import download, model_files, nvidia_runtime
from scribe.asr import whisper
from scribe.dual_engine import DualEngineSetup, Phase


@pytest.mark.gpu
@pytest.mark.skipif(os.environ.get("SCRIBE_E2E") != "1", reason="真实下载约 2.2 GB，设 SCRIBE_E2E=1 才跑")
def test_empty_cache_to_dual_ready(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))       # 不接管旧 Hugging Face 缓存
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    for name in ("CUDA_PATH", "SCRIBE_CUDA_DLL_DIRS", "HF_HOME", "HF_ENDPOINT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(whisper.site, "getusersitepackages", lambda: str(tmp_path / "nousersite"))
    monkeypatch.setattr(whisper.site, "getsitepackages", lambda: [str(tmp_path / "nosite")])
    monkeypatch.setattr(whisper.sys, "executable", str(tmp_path / "noexe" / "python.exe"))
    assert not nvidia_runtime.system_runtime()

    sources, states = set(), []

    def on_change(state):
        states.append(state.phase)
        for p in (state.runtime, state.model):
            if p is not None and p.source:
                sources.add(p.source)

    setup = DualEngineSetup(on_change=on_change, spawn=lambda fn: fn())
    started = time.monotonic()
    assert setup.start() is None
    elapsed = time.monotonic() - started
    assert setup.state.phase is Phase.READY, setup.state
    assert nvidia_runtime.installed() and model_files.ready(model_files.WHISPER)
    with capsys.disabled():
        print(f"\ndownload {elapsed:.0f} s, sources {sorted(sources)}, files {setup.state.files_bytes} B")

    engine = whisper.WhisperEngine()
    engine.load()
    from faster_whisper import decode_audio
    assert "张总" in engine.transcribe(decode_audio("tests/fixtures/s1_short.mp3", sampling_rate=16000))
    assert all(download.present(i, model_files.model_dir(model_files.WHISPER))
               for i in model_files.items(model_files.WHISPER))
