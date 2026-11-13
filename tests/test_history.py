from scribe.history import HistoryStore, RETENTION_S
from scribe.pipeline import Result


def test_history_retention_search_and_raw_results(tmp_path):
    now = 2_000_000_000.0
    history = HistoryStore(tmp_path / "history.sqlite", now=lambda: now)
    history.add(Result("过期内容", "fast"), "code.exe", 200, "pasted")
    now += RETENTION_S + 1
    history.add(Result("保留这句话。", "llm", "openai", "保留这一句话", "保留这句话"),
                "notepad.exe", 840, "pasted")
    history.add(Result("直接输入", "single", b="直接输入"), "weixin.exe", 260, "typed")
    history.add(Result("", "empty"), "", 0, "empty")

    assert [row.text for row in history.list()] == ["直接输入", "保留这句话。"]
    polished, = history.list("记事本", "smart")
    assert (polished.whisper, polished.sensevoice, polished.provider) == ("保留这一句话", "保留这句话", "openai")
    direct, = history.list("输入", "raw")
    assert (direct.app_id, direct.outcome) == ("weixin.exe", "typed")
    assert history.list("过期内容") == []
