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


def test_first_service_turns_polish_on_unless_user_turned_it_off():
    base = {**settings.DEFAULTS, "smart_polish": False}
    assert settings.apply_service_change(base, 0, 1)["smart_polish"] is True
    assert settings.apply_service_change(base, 1, 2)["smart_polish"] is False
    assert settings.apply_service_change(base, 1, 0)["smart_polish"] is False
    off = {**base, "polish_user_off": True}
    assert settings.apply_service_change(off, 0, 1)["smart_polish"] is False


def test_load_keeps_new_flags_boolean(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    settings.save({"polish_user_off": "x", "services_migrated": 1})
    prefs = settings.load()
    assert prefs["polish_user_off"] is False and prefs["services_migrated"] is False


import pytest  # noqa: E402


@pytest.mark.parametrize("installed,existed,changes,enable", [
    (True, False, {"startup_defaulted": True}, True),
    (True, True, {"startup_defaulted": True, "ready_noticed": True}, False),
    (False, True, {"ready_noticed": True}, False),
    (False, False, {}, False),
])
def test_first_run_defaults(installed, existed, changes, enable):
    prefs, on = settings.first_run_defaults(dict(settings.DEFAULTS), installed=installed, existed=existed)
    assert prefs == {**settings.DEFAULTS, **changes} and on is enable


def test_user_turned_off_startup_stays_off():
    prefs = {**settings.DEFAULTS, "startup_defaulted": True}
    assert settings.first_run_defaults(prefs, installed=True, existed=True) == (prefs, False)


def test_onboarding_flags_default_false_and_are_validated(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    flags = ("dual_prompted", "ready_noticed", "startup_defaulted")
    assert all(settings.load()[f] is False for f in flags)
    settings.save({f: "yes" for f in flags})
    assert all(settings.load()[f] is False for f in flags)
    settings.save({f: True for f in flags})
    assert all(settings.load()[f] is True for f in flags)


def test_exists_reports_settings_file(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert settings.exists() is False
    settings.save({})
    assert settings.exists() is True


def test_translate_defaults_and_cleanup(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    settings.save({"selection_translate": "yes", "translate_noticed": 1,
                   "translate_excluded": ["PS.exe", "", 3, "ps.exe", "Acad.exe"]})
    d = settings.load()
    assert d["selection_translate"] is False and d["translate_noticed"] is False
    assert d["translate_excluded"] == ["acad.exe", "ps.exe"]
    settings.save({"translate_excluded": "x.exe"})
    assert settings.load()["translate_excluded"] == []
