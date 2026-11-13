from pathlib import Path
import numpy as np
import pytest
from faster_whisper import decode_audio

from scribe.asr.whisper import WhisperEngine


@pytest.fixture(scope="module")
def engine():
    e = WhisperEngine()
    e.load()
    return e


@pytest.mark.gpu
def test_transcribe_fixture(request):
    fixture = Path("tests/fixtures/s1_short.mp3")
    if not fixture.is_file():
        pytest.skip("Supply the optional local speech fixture described in README")
    engine = request.getfixturevalue("engine")
    assert engine.ready
    assert "张总" in engine.transcribe(decode_audio(str(fixture), sampling_rate=16000))


@pytest.mark.gpu
def test_transcribe_silence_returns_empty(engine):
    assert engine.transcribe(np.zeros(16000 * 2, np.float32)) == ""


def test_load_marks_failed_when_library_missing(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    e = WhisperEngine()
    with pytest.raises(ImportError):
        e.load()
    assert e.failed and not e.ready


def test_hotwords_only_forwarded_when_supplied():
    class FakeModel:
        def transcribe(self, audio, **options):
            calls.append(options)
            return iter(()), None

    calls = []
    e = WhisperEngine()
    e._model = FakeModel()
    audio = np.zeros(16000, np.float32)
    e.transcribe(audio)
    e.transcribe(audio, hotwords="BABEL Claude Code")
    assert "hotwords" not in calls[0]
    assert calls[1] == {**calls[0], "hotwords": "BABEL Claude Code"}
