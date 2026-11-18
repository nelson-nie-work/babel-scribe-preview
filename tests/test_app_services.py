import json

from scribe import app, settings
from scribe.provider_store import ProviderStore


def store(tmp_path):
    return ProviderStore(tmp_path / "providers.json", protect=lambda b: b[::-1], unprotect=lambda b: b[::-1])


def test_load_services_imports_and_migrates_once(tmp_path):
    (tmp_path / "keys.json").write_text(json.dumps({"openai": {"api_key": "o"}, "deepseek": {"api_key": "d"}}),
                                        encoding="utf-8")
    prefs = {**settings.DEFAULTS, "llm_mode": "deepseek"}
    s = store(tmp_path)
    entries, prefs = app.load_services(tmp_path, prefs, s)
    assert [e["preset"] for e in entries] == ["deepseek", "openai"]
    assert prefs["services_migrated"] is True
    assert [e["preset"] for e in s.load()] == ["deepseek", "openai"]

    again, prefs = app.load_services(tmp_path, {**prefs, "llm_mode": "gpt"}, s)
    assert [e["preset"] for e in again] == ["deepseek", "openai"]
    assert [e["id"] for e in again] == [e["id"] for e in entries]


def test_load_services_without_any_config(tmp_path):
    entries, prefs = app.load_services(tmp_path, dict(settings.DEFAULTS), store(tmp_path))
    assert entries == [] and prefs["services_migrated"] is True
    assert not (tmp_path / "providers.json").exists()


from types import SimpleNamespace  # noqa: E402

from scribe.download import Progress  # noqa: E402
from scribe.dual_engine import Phase, SetupState  # noqa: E402

MiB = 1024 ** 2
IDLE = SetupState(Phase.IDLE)
READY_ENGINE = SimpleNamespace(ready=True)
NOT_READY = SimpleNamespace(ready=False)


def sv(progress=None, ready=True, failed=False, error=""):
    return SimpleNamespace(progress=progress, ready=ready, failed=failed, error=error)


def status(**kw):
    args = dict(paused=False, sensevoice=sv(), engine=READY_ENGINE, dual_pref=False, setup_state=IDLE,
                whisper_ready=False, hotkey_label="右 Ctrl")
    args.update(kw)
    return app.tray_status(**args)


def test_status_paused_first():
    assert status(paused=True, sensevoice=sv(failed=True)) == ("已释放模型 · 下次听写时加载", "")


def test_status_shows_sensevoice_download_with_source():
    progress = Progress(38, 100, 12 * MiB, "hf-mirror.com")
    assert status(sensevoice=sv(progress=progress, ready=False), engine=NOT_READY) \
        == ("下载模型 38% · 12.0 MB/s", "来源 hf-mirror.com")


def test_status_offers_retry_after_download_failure():
    assert status(sensevoice=sv(ready=False, failed=True, error="download"), engine=NOT_READY) \
        == ("模型下载失败 · 点击重试", "")


def test_status_load_failure():
    assert status(sensevoice=sv(ready=False, failed=True, error="load"), engine=NOT_READY) == ("模型加载失败", "")


def test_status_single_engine_while_dual_downloads():
    state = SetupState(Phase.DOWNLOADING, runtime=Progress(30, 50, 1.0, "a"), model=Progress(8, 50, 1.0, "b"))
    assert status(dual_pref=True, setup_state=state) == ("就绪（单引擎）· 下载双引擎 38%", "")


def test_status_single_engine_when_dual_off():
    assert status() == ("就绪（单引擎） · 按住 右 Ctrl 说话", "")


def test_status_drops_single_engine_once_whisper_ready():
    assert status(dual_pref=True, whisper_ready=True, setup_state=SetupState(Phase.READY)) \
        == ("就绪 · 按住 右 Ctrl 说话", "")


def test_status_loading_otherwise():
    assert status(engine=NOT_READY, sensevoice=sv(ready=False)) == ("加载模型中…", "")
