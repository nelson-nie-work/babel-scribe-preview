import json
from pathlib import Path

import pytest

from scribe import vocab
from scribe.vocab_store import VocabularyStore


def test_quick_add_saves_only_spelling_and_undo_preserves_other_words(tmp_path):
    store = VocabularyStore(tmp_path, vocab.load_terms(tmp_path))
    entry = store.add_word("青砚桥", source="manual")
    assert entry and entry["aliases"] == []
    assert "青砚桥" in store.current.hotword_terms
    assert store.current.correct("清样桥") == "清样桥"
    assert store.add_word("青砚桥", source="learned") is None
    store.add_word("北辰桥", source="learned")
    assert store.undo_word(entry)
    assert "青砚桥" not in store.current.hotword_terms
    assert any(row["term"] == "北辰桥" for row in vocab.load_terms(tmp_path))


def test_undo_cannot_remove_word_after_it_gained_a_fixed_alias(tmp_path):
    store = VocabularyStore(tmp_path, vocab.load_terms(tmp_path))
    entry = store.add_word("青砚桥", source="manual")
    assert entry and store.learn_alias("清样桥", "青砚桥")
    assert not store.undo_word(entry)
    assert store.current.correct("清样桥") == "青砚桥"


def test_learning_refreshes_vocab_after_save_and_keeps_existing_metadata(tmp_path):
    path = tmp_path / "vocab.json"
    store = VocabularyStore(tmp_path, vocab.load_terms(tmp_path))
    old = store.current
    rows = vocab.load_terms(tmp_path)
    rows[-1].update({"pinyin": True, "note": "owner note"})
    path.write_text(json.dumps(rows), encoding="utf-8")

    assert old.correct("辛侨") == "辛侨"
    assert store.learn_alias("欣桥", "新桥")
    assert old.correct("欣桥") == "欣桥"
    assert store.current.correct("欣桥") == "新桥"
    assert store.current.correct("辛侨") == "新桥"
    saved = vocab.load_terms(tmp_path)[-1]
    assert saved["note"] == "owner note" and saved["pinyin"] is True
    assert store.learn_alias("欣桥", "新桥") is False
    assert vocab.load_terms(tmp_path)[-1]["aliases"].count("欣桥") == 1


def test_new_learned_term_is_alias_only_and_conflict_keeps_disk_unchanged(tmp_path):
    store = VocabularyStore(tmp_path, vocab.load_terms(tmp_path))
    assert store.learn_alias("磁库", "词库")
    assert store.current.correct("磁库") == "词库"
    assert store.current.correct("瓷库") == "瓷库"
    saved = (tmp_path / "vocab.json").read_bytes()
    with pytest.raises(ValueError, match="另一个词"):
        store.learn_alias("磁库", "新桥")
    assert (tmp_path / "vocab.json").read_bytes() == saved


def test_failed_replace_keeps_disk_and_live_snapshot(tmp_path, monkeypatch):
    store = VocabularyStore(tmp_path, vocab.load_terms(tmp_path))
    path = tmp_path / "vocab.json"
    before = path.read_bytes()

    def blocked(_self, _path):
        raise PermissionError("test")

    monkeypatch.setattr(Path, "replace", blocked)
    with pytest.raises(PermissionError):
        store.learn_alias("欣桥", "新桥")
    assert path.read_bytes() == before
    assert store.current.correct("欣桥") == "欣桥"
    assert list(tmp_path.iterdir()) == [path]


def test_dictionary_edit_and_restore_keep_metadata_and_other_words(tmp_path):
    store = VocabularyStore(tmp_path, vocab.load_terms(tmp_path))
    learned = store.add_word("青砚桥", source="learned")
    saved = store.save_entry(learned, term="青砚桥", aliases=["清样桥", "清样桥"], note="项目", pinyin=False)
    assert saved["source"] == "learned" and saved["created_at"] == learned["created_at"]
    assert saved["aliases"] == ["清样桥"]
    assert store.current.correct("清样桥") == "青砚桥"
    undo = store.delete_entry(saved)
    assert store.current.correct("清样桥") == "清样桥"
    store.add_word("北辰桥", source="manual")
    assert store.restore_entry(undo) == saved
    assert "北辰桥" in store.current.hotword_terms
    assert store.current.correct("清样桥") == "青砚桥"


def test_dictionary_editor_rejects_stale_rows_and_normalized_conflicts(tmp_path):
    store = VocabularyStore(tmp_path, vocab.load_terms(tmp_path))
    entry = store.add_word("青砚桥", source="manual")
    assert store.learn_alias("清样桥", "青砚桥")
    before = (tmp_path / "vocab.json").read_bytes()
    with pytest.raises(ValueError, match="已在其他地方更改"):
        store.delete_entry(entry)
    with pytest.raises(ValueError, match="其他词条"):
        store.save_entry(None, term="ＢＵＢＢＬＥ", aliases=[], note="", pinyin=False)
    with pytest.raises(ValueError, match="只能使用固定错写"):
        store.save_entry(None, term="青砚站", aliases=[], note="", pinyin=True)
    assert (tmp_path / "vocab.json").read_bytes() == before
