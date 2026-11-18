from contextlib import contextmanager
from io import StringIO
import json
import threading
from types import SimpleNamespace

from scribe import edit_watch
from scribe.edit_watch import edited_word


def test_detects_full_term_even_when_last_character_did_not_change():
    assert edited_word("请把清样桥的版本标成待复核。", "请把青砚桥的版本标成待复核。", "清样桥") == ("清样桥", "青砚桥")


def test_single_character_edit_uses_known_term_boundary():
    original = "请查中心国际的公告。"
    edited = "请查中芯国际的公告。"
    assert edited_word(original, edited, "心", known_terms=("中芯国际",)) == ("中心国际", "中芯国际")
    assert edited_word(original, edited, "心") is None
    assert edited_word(original, edited, "心", known_terms=("中芯国际", "中芯")) is None
    assert edited_word(original, "请查中xin国际的公告。", "心", known_terms=("中芯国际",)) is None


def test_does_not_guess_boundaries_or_teach_multiple_changes():
    assert edited_word("请把清样桥的版本标成待复核。", "请把青砚桥的版本标成待复核。") is None
    assert edited_word("请把清样桥的版本标成待复核。", "请把青砚桥的版本归档。", "清样桥") is None
    assert edited_word("清样桥或清样桥", "青砚桥或清样桥", "清样桥") is None


def test_detects_single_word_separated_by_spaces_without_a_selection():
    assert edited_word("请发给 Jons。", "请发给 Jones。") == ("Jons", "Jones")
    assert edited_word("数字填 12。", "数字填 13。") is None


def test_pinyin_preedit_is_not_a_correction():
    for preedit in ("zhong", "zhongx", "zhongxin", "zhong'", "zhong'x", "zhong'xin", "zhong’xin"):
        assert edited_word("请把 中兴 发给我。", f"请把 {preedit} 发给我。") is None
    assert edited_word("请发给备播。", "请发给 BABEL。", "备播") == ("备播", "BABEL")
    assert edited_word("请发给备播。", "请发给 github。", "备播") == ("备播", "github")


def test_watch_skips_an_intermediate_deletion_and_waits_for_the_replacement(monkeypatch):
    original = "请把清样桥的版本标成待复核。"
    changes = (
        {"edited": "请把的版本标成待复核。", "selected": "清样桥"},
        {"edited": "请把松的版本标成待复核。", "selected": "清样桥"},
        {"edited": "请把松砚桥的版本标成待复核。", "selected": "清样桥"},
    )

    @contextmanager
    def process(*args, **kwargs):
        payload = "\n".join(json.dumps(change) for change in changes)
        yield SimpleNamespace(stdout=StringIO(payload), poll=lambda: 0)

    monkeypatch.setattr(edit_watch, "uia_command", lambda script: ["fake-windows-reader"])
    monkeypatch.setattr(edit_watch.subprocess, "Popen", process)
    assert edit_watch.watch_edited_word(42, original, threading.Event(), lambda: True) == ("清样桥", "松砚桥")


def test_watch_waits_for_the_ime_to_commit_chinese(monkeypatch):
    original = "请把中兴的公告发给我。"
    changes = (
        {"edited": "请把zhong的公告发给我。", "selected": "中兴"},
        {"edited": "请把zhong'xin的公告发给我。", "selected": "中兴"},
        {"edited": "请把中心的公告发给我。", "selected": "中兴"},
    )

    @contextmanager
    def process(*args, **kwargs):
        payload = "\n".join(json.dumps(change) for change in changes)
        yield SimpleNamespace(stdout=StringIO(payload), poll=lambda: 0)

    monkeypatch.setattr(edit_watch, "uia_command", lambda script: ["fake-windows-reader"])
    monkeypatch.setattr(edit_watch.subprocess, "Popen", process)
    assert edit_watch.watch_edited_word(42, original, threading.Event(), lambda: True) == ("中兴", "中心")


def test_watch_learns_the_full_known_term_after_a_single_character_ime_edit(monkeypatch):
    original = "请查中心国际的公告。"
    changes = (
        {"edited": "请查中xin国际的公告。", "selected": "心"},
        {"edited": "请查中芯国际的公告。", "selected": "心"},
    )

    @contextmanager
    def process(*args, **kwargs):
        payload = "\n".join(json.dumps(change) for change in changes)
        yield SimpleNamespace(stdout=StringIO(payload), poll=lambda: 0)

    monkeypatch.setattr(edit_watch, "uia_command", lambda script: ["fake-windows-reader"])
    monkeypatch.setattr(edit_watch.subprocess, "Popen", process)
    assert edit_watch.watch_edited_word(
        42, original, threading.Event(), lambda: True, known_terms=("中芯国际",),
    ) == ("中心国际", "中芯国际")


def test_watch_waits_for_original_window_before_accepting_a_candidate(monkeypatch):
    original = "请把清样桥的版本标成待复核。"
    change = {"edited": "请把青砚桥的版本标成待复核。", "selected": "清样桥"}

    @contextmanager
    def process(*args, **kwargs):
        payload = "\n".join(json.dumps(change) for _ in range(2))
        yield SimpleNamespace(stdout=StringIO(payload), poll=lambda: 0)

    monkeypatch.setattr(edit_watch, "uia_command", lambda script: ["fake-windows-reader"])
    monkeypatch.setattr(edit_watch.subprocess, "Popen", process)
    target_ok = iter((False, True)).__next__
    assert edit_watch.watch_edited_word(42, original, threading.Event(), target_ok) == ("清样桥", "青砚桥")
