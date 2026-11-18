from pathlib import Path
import re

import numpy as np
import pytest
from faster_whisper import decode_audio

from scribe import download, model_files
from scribe.asr.sensevoice import SenseVoiceEngine


def _raise(error):
    def fn(*args, **kwargs):
        raise error
    return fn


def test_sensevoice_marks_download_error(monkeypatch):
    monkeypatch.setattr(model_files, "ensure", _raise(download.DownloadFailed()))
    e = SenseVoiceEngine()
    with pytest.raises(download.DownloadFailed):
        e.load()
    assert e.failed and not e.ready and e.error == "download"


def test_sensevoice_load_error_verifies_files(monkeypatch, tmp_path):
    import sherpa_onnx
    verified = []
    monkeypatch.setattr(model_files, "ensure", lambda model, **kw: tmp_path)
    monkeypatch.setattr(model_files, "verify", lambda model: verified.append(model) or False)
    monkeypatch.setattr(sherpa_onnx.OfflineRecognizer, "from_sense_voice", _raise(RuntimeError("bad model")))
    e = SenseVoiceEngine()
    with pytest.raises(RuntimeError):
        e.load()
    assert e.failed and e.error == "load"
    assert verified == [model_files.SENSEVOICE]


def test_sensevoice_reports_progress_then_clears(monkeypatch, tmp_path):
    import sherpa_onnx
    seen = []
    progress = download.Progress(5, 10, 1.0, "huggingface.co")

    def ensure(model, *, on_progress, cancel=None):
        on_progress(progress)
        assert e.progress == progress
        return tmp_path

    monkeypatch.setattr(model_files, "ensure", ensure)
    monkeypatch.setattr(sherpa_onnx.OfflineRecognizer, "from_sense_voice", lambda **kw: object())
    e = SenseVoiceEngine(on_progress=seen.append)
    e.load()
    assert seen == [progress]
    assert e.ready and e.progress is None and e.error == ""


@pytest.fixture(scope="module")
def engine():
    e = SenseVoiceEngine()
    e.load()
    return e


@pytest.mark.model
def test_transcribe_fixture(request):
    fixture = Path("tests/fixtures/s1_short.mp3")
    if not fixture.is_file():
        pytest.skip("Supply the optional local speech fixture described in README")
    engine = request.getfixturevalue("engine")
    assert engine.ready and not engine.failed
    assert "张总" in engine.transcribe(decode_audio(str(fixture), sampling_rate=16000))


@pytest.mark.model
def test_silence_returns_short_or_empty(engine):
    text = engine.transcribe(np.zeros(16000 * 2, np.float32))
    assert len(re.sub(r"[\W_]+", "", text)) <= 2
