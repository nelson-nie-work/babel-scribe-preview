import json

from scribe import settings


def test_settings_default_and_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert settings.load() == settings.DEFAULTS
    settings.save({"llm_mode": "auto"})
    assert settings.load()["llm_mode"] == "auto"


def test_new_settings_use_single_engine_with_polish_only_when_configured(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    for available in (False, True):
        prefs = settings.load(polish_available=available)
        assert prefs["smart_polish"] is available
        assert prefs["dual_engine"] is False
        assert prefs["llm_mode"] == "gpt"


def test_saved_engine_and_polish_preferences_take_priority(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    settings.save({"smart_polish": False, "dual_engine": True})
    prefs = settings.load(polish_available=True)
    assert prefs["smart_polish"] is False
    assert prefs["dual_engine"] is True


def test_settings_unknown_mode_falls_back(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    d = tmp_path / "BABEL Scribe"
    d.mkdir()
    (d / "settings.json").write_text(json.dumps({"llm_mode": "xx"}), encoding="utf-8")
    assert settings.load()["llm_mode"] == "gpt"


def test_settings_corrupt_file_falls_back(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    d = tmp_path / "BABEL Scribe"
    d.mkdir()
    (d / "settings.json").write_text("{not json", encoding="utf-8")
    assert settings.load() == settings.DEFAULTS
