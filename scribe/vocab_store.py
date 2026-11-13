"""向识别线程提供词库快照，并在用户确认后保存别名。"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile

from scribe import vocab
from scribe.learning import is_word_selection


class Vocabulary:
    """一次识别使用同一份词库，保存新词后替换整个快照。"""

    def __init__(self, terms: list):
        self.terms = terms
        self.correct = vocab.build_corrector(terms)
        self.hotword_terms = vocab.hotword_terms(terms)
        self.hotwords = " ".join(self.hotword_terms)

    def prompt(self, a: str, b: str) -> str:
        return vocab.terms_line(vocab.relevant_terms(self.terms, a, b))


class VocabularyStore:
    def __init__(self, data_dir: Path, terms: list):
        self.data_dir = data_dir
        self.current = Vocabulary(terms)

    def reload(self) -> None:
        self.current = Vocabulary(vocab.load_terms(self.data_dir))

    def add_word(self, term: str, *, source: str) -> dict | None:
        """只存正确写法，返回可用于撤销这次添加的词条。"""
        term = term.strip()
        if not is_word_selection(term):
            raise ValueError("请选择一个词或短词组")
        key = vocab.spelling_key(term)
        entries = vocab.load_terms(self.data_dir)
        if any(vocab.spelling_key(row["term"]) == key for row in entries):
            self.current = Vocabulary(entries)
            return None
        if any(vocab.spelling_key(alias) == key
               for row in entries for alias in row.get("aliases", ())):
            raise ValueError("这个写法已被登记为其他词的错写")
        entry = self._new_entry(term, source)
        entries.insert(0, entry)  # 同样的使用次数下，刚收的词先参与热词提示。
        self._publish(entries)
        return entry

    def undo_word(self, entry: dict) -> bool:
        """撤销尚未被修改的新词，保留此后添加的其他词。"""
        entries = vocab.load_terms(self.data_dir)
        if entry not in entries:
            return False
        entries.remove(entry)
        self._publish(entries)
        return True

    def learn_alias(self, alias: str, term: str) -> bool:
        """从最新落盘内容合并一对别名，成功后才发布快照。"""
        alias, term = alias.strip(), term.strip()
        if not alias or not term or alias == term:
            raise ValueError("请填写两个不同的写法")
        if any(len(word) > 40 or not word.isprintable() or not any(c.isalnum() for c in word)
               for word in (alias, term)):
            raise ValueError("每个写法最多 40 字，须包含文字或数字")

        entries = vocab.load_terms(self.data_dir)
        entry = next((row for row in entries if row["term"] == term), None)
        if any(row is not entry and (row["term"] == alias or alias in row.get("aliases", ()))
               for row in entries):
            raise ValueError("这个错写已对应另一个词，请检查词库")
        if any(row is not entry and term in row.get("aliases", ()) for row in entries):
            raise ValueError("正确写法已被登记为其他词的错写，请检查词库")
        if entry is not None and alias in entry.get("aliases", ()):
            self.current = Vocabulary(entries)
            return False
        if entry is None:
            entry = self._new_entry(term, "learned")
            entries.append(entry)
        entry.setdefault("aliases", []).append(alias)
        self._publish(entries)
        return True

    def save_entry(self, original: dict | None, *, term: str, aliases: list[str],
                   note: str, pinyin: bool) -> dict:
        """编辑独立词条，保留旧条目的来源、日期和其他已有字段。"""
        term = term.strip()
        term_changed = original is None or term != original["term"]
        aliases_changed = original is None or aliases != list(original.get("aliases", ()))
        if term_changed and not self._valid_spelling(term):
            raise ValueError("请输入 1 到 40 字的正确写法，须包含文字或数字。")
        if aliases_changed:
            unique = {}
            for alias in aliases:
                alias = alias.strip()
                if alias:
                    unique.setdefault(vocab.spelling_key(alias), alias)
            aliases = list(unique.values())
            if not all(self._valid_spelling(alias) for alias in aliases):
                raise ValueError("每个错写最多 40 字，须包含文字或数字。")

        entries = vocab.load_terms(self.data_dir)
        index = self._entry_index(entries, original) if original is not None else None
        other_entries = entries if index is None else entries[:index] + entries[index + 1:]
        entry = self._new_entry(term, "manual") if index is None else dict(entries[index])
        entry.update(term=term, aliases=aliases, note=note.strip())
        if term_changed or aliases_changed:
            self._check_spellings(entry, other_entries)
        if pinyin:
            was_enabled = entry.get("pinyin") is True and not term_changed
            if not was_enabled and not vocab.can_match_pinyin(term, (row["term"] for row in other_entries)):
                raise ValueError("这个词当前只能使用固定错写纠正。")
            entry["pinyin"] = True
        elif entry.get("pinyin") is True:
            entry.pop("pinyin", None)
        if index is None:
            entries.insert(0, entry)
        else:
            entries[index] = entry
        self._publish(entries)
        return entry

    def delete_entry(self, original: dict) -> tuple[int, dict]:
        entries = vocab.load_terms(self.data_dir)
        index = self._entry_index(entries, original)
        entry = entries.pop(index)
        self._publish(entries)
        return index, entry

    def restore_entry(self, deleted: tuple[int, dict]) -> dict:
        index, entry = deleted
        entries = vocab.load_terms(self.data_dir)
        self._check_spellings(entry, entries)
        entries.insert(index, entry)
        self._publish(entries)
        return entry

    @staticmethod
    def _new_entry(term: str, source: str) -> dict:
        now = datetime.now().astimezone()
        return {"term": term, "aliases": [], "note": "", "source": source, "hits": 1,
                "created": now.date().isoformat(),
                "created_at": now.astimezone(timezone.utc).isoformat()}

    @staticmethod
    def _valid_spelling(text: str) -> bool:
        return len(text) <= 40 and text.isprintable() and any(c.isalnum() for c in text)

    @staticmethod
    def _entry_index(entries: list, original: dict) -> int:
        try:
            return entries.index(original)
        except ValueError:
            raise ValueError("这条词已在其他地方更改，请重新打开词库核对。") from None

    @staticmethod
    def _check_spellings(entry: dict, other_entries: list) -> None:
        term = vocab.spelling_key(entry["term"])
        aliases = {vocab.spelling_key(alias) for alias in entry.get("aliases", ())}
        if term in aliases:
            raise ValueError("错写需要和正确写法不同。")
        used = {vocab.spelling_key(spelling) for row in other_entries
                for spelling in (row["term"], *row.get("aliases", ()))}
        if ({term} | aliases) & used:
            raise ValueError("这条写法已在其他词条里，请先搜索并核对。")

    def _publish(self, entries: list) -> None:
        snapshot = Vocabulary(entries)
        self._save(entries)
        self.current = snapshot

    def _save(self, entries: list) -> None:
        path = self.data_dir / "vocab.json"
        tmp_path = None
        try:
            with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                    prefix=".vocab-", suffix=".tmp", delete=False) as handle:
                tmp_path = Path(handle.name)
                json.dump(entries, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            tmp_path.replace(path)
        finally:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)
