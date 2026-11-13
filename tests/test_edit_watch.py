from contextlib import contextmanager
from io import StringIO
import json
import threading
from types import SimpleNamespace

from scribe import edit_watch
from scribe.edit_watch import edited_word


def test_detects_full_term_even_when_last_character_did_not_change():
    assert edited_word("请把清样桥的版本标成待复核。", "请把青砚桥的版本标成待复核。", "清样桥") == "青砚桥"


def test_does_not_guess_boundaries_or_teach_multiple_changes():
    assert edited_word("请把清样桥的版本标成待复核。", "请把青砚桥的版本标成待复核。") is None
    assert edited_word("请把清样桥的版本标成待复核。", "请把青砚桥的版本归档。", "清样桥") is None
    assert edited_word("清样桥或清样桥", "青砚桥或清样桥", "清样桥") is None


def test_detects_single_word_separated_by_spaces_without_a_selection():
    assert edited_word("请发给 Jons。", "请发给 Jones。") == "Jones"
    assert edited_word("数字填 12。", "数字填 13。") is None


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
    assert edit_watch.watch_edited_word(42, original, threading.Event(), lambda: True) == "松砚桥"


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
    assert edit_watch.watch_edited_word(42, original, threading.Event(), target_ok) == "青砚桥"
