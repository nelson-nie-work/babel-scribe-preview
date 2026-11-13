from pathlib import Path
import re

import numpy as np
import pytest
from faster_whisper import decode_audio

from scribe.asr.sensevoice import SenseVoiceEngine


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
