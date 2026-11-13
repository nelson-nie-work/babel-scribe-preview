from pathlib import Path

from scribe import paths


def test_settings_dir_under_appdata(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    d = paths.settings_dir()
    assert d == tmp_path / "BABEL Scribe" and d.is_dir()


def test_logs_dir_created(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert paths.logs_dir() == tmp_path / "BABEL Scribe" / "logs" and paths.logs_dir().is_dir()


def test_shared_data_dir_is_sibling_of_repo():
    d = paths.shared_data_dir()
    assert d.name == "babel-data" and d.parent == Path(paths.__file__).resolve().parents[2]
