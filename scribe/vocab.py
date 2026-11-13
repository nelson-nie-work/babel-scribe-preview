"""个人词库的读取、别名纠正与提示词格式。"""
import json
import re
from pathlib import Path
from unicodedata import normalize

from scribe.pinyin import PINYIN, syllables

_HOTWORD_COUNT = 15
_HOTWORD_CHARS = 100
_RELATED_COUNT = 20
# 已知容易误改的正常写法。显式别名仍以词库中的设置为准。
_PROTECTED_WORDS = {"回车", "回撤", "颜值"}

SEED = [
    {"term": "BABEL", "aliases": ["备播", "Bubble", "Bebo", "Bagel"], "note": "项目名"},
    {"term": "Claude", "aliases": ["Cloud", "克劳德", "Claw"], "note": "AI 助手"},
    {"term": "Claude Code", "aliases": ["Cloud Code"], "note": "编程工具"},
    {"term": "DeepSeek", "aliases": ["Deep Seek", "Deep Stick"], "note": "大模型"},
    {"term": "Typeless", "aliases": [], "note": "语音输入软件"},
    {"term": "Whisper", "aliases": ["Visper"], "note": "识别模型"},
    {"term": "GLM", "aliases": [], "note": "大模型"},
    {"term": "智谱", "aliases": ["智普", "智火"], "note": "公司名"},
    {"term": "CLAUDE.md", "aliases": ["cloud.md"], "note": "配置文件"},
    {"term": "新桥", "aliases": ["新乔", "心桥"], "note": "示例专有词"},
]


def load_terms(data_dir: Path) -> list:
    """读 data_dir/vocab.json;不存在则写入种子后返回。"""
    p = Path(data_dir) / "vocab.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(SEED, ensure_ascii=False, indent=2), encoding="utf-8")
    return [dict(t, aliases=list(t["aliases"])) for t in SEED]


def spelling_key(text: str) -> str:
    return normalize("NFKC", text).strip().casefold()


def can_match_pinyin(term: str, other_terms=()) -> bool:
    if len(term) < 2 or term in _PROTECTED_WORDS:
        return False
    sound = syllables(term)
    return sound is not None and all(syllables(other) != sound for other in other_terms)


def _aliases(terms):
    replacements = {}
    for entry in terms:
        for alias in entry.get("aliases", []):
            if alias and alias != entry["term"]:
                replacements.setdefault(alias, entry["term"])
    return replacements


def _bounded_word(word: str) -> str:
    # 英文词不能吃掉更长的英文词，也可以紧挨着中文。
    prefix = r"(?<![a-zA-Z0-9_])" if word[0].isascii() and word[0].isalnum() else ""
    suffix = r"(?![a-zA-Z0-9_])" if word[-1].isascii() and word[-1].isalnum() else ""
    return prefix + re.escape(word) + suffix


def _word_pattern(words):
    return re.compile("|".join(_bounded_word(word) for word in sorted(words, key=len, reverse=True))) if words else None


def build_alias_corrector(terms):
    """把已登记的别名一次性改成正确写法，保留标点和空白。"""
    replacements = _aliases(terms)
    pattern = _word_pattern(replacements)
    if pattern is None:
        return lambda text: text
    return lambda text: pattern.sub(lambda match: replacements[match.group()], text)


def _rewrite_gaps(text, anchors, replace_gap, replace_anchor=lambda word: word):
    """在原文中分段，避免再处理已经匹配的词或跨过词的边缘。"""
    if anchors is None:
        return replace_gap(text)
    pieces = []
    end = 0
    for match in anchors.finditer(text):
        pieces.extend((replace_gap(text[end:match.start()]), replace_anchor(match.group())))
        end = match.end()
    pieces.append(replace_gap(text[end:]))
    return "".join(pieces)


def _phonetic_windows(text, lengths):
    sounds = tuple(PINYIN.get(char) for char in text)
    for start, sound in enumerate(sounds):
        if sound is None:
            continue
        for length in lengths:
            end = start + length
            if end <= len(sounds):
                yield start, end, sounds[start:end]


def build_corrector(terms):
    """别名优先。仅给逐词启用、可读、无同音冲突的词做本地拼音纠错。"""
    terms = list(terms)
    by_sound = {}
    enabled = set()
    for entry in terms:
        word = entry["term"]
        sound = syllables(word) if len(word) >= 2 else None
        if sound is not None:
            by_sound.setdefault(sound, set()).add(word)
            if entry.get("pinyin") is True and word not in _PROTECTED_WORDS:
                enabled.add(word)

    by_sound = {sound: next(iter(words)) for sound, words in by_sound.items()
                if len(words) == 1 and words <= enabled}
    if not by_sound:
        return build_alias_corrector(terms)

    lengths = sorted({len(sound) for sound in by_sound}, reverse=True)
    aliases = _aliases(terms)
    alias_pattern = _word_pattern(aliases)
    protected = dict.fromkeys(entry["term"] for entry in terms if entry["term"])
    protected.update(dict.fromkeys(sorted(_PROTECTED_WORDS)))
    protected_pattern = _word_pattern(protected)

    def fix_homophones(text):
        pieces = []
        end = 0
        for start, stop, sound in _phonetic_windows(text, lengths):
            if start < end:
                continue
            word = by_sound.get(sound)
            if word is not None:
                pieces.extend((text[end:start], word))
                end = stop
        pieces.append(text[end:])
        return "".join(pieces)

    def fix_unmatched(text):
        return _rewrite_gaps(text, protected_pattern, fix_homophones)

    return lambda text: _rewrite_gaps(text, alias_pattern, fix_unmatched, aliases.__getitem__)


def _hits(entry):
    hits = entry.get("hits", 0)
    return hits if type(hits) is int and hits > 0 else 0


def hotword_terms(terms) -> list[str]:
    """按使用次数取最多 15 个不同词，同分保留词库顺序，整体最多 100 字符。"""
    ranked = dict.fromkeys(entry["term"].strip() for entry in sorted(terms, key=_hits, reverse=True))
    chosen = []
    size = 0
    for word in [word for word in ranked if word][:_HOTWORD_COUNT]:
        next_size = size + bool(chosen) + len(word)
        if next_size <= _HOTWORD_CHARS:
            chosen.append(word)
            size = next_size
    return chosen


def hotwords(terms) -> str:
    """Whisper 的热词参数沿用空格分隔，词内空格保留。"""
    return " ".join(hotword_terms(terms))


def relevant_terms(terms, a: str, b: str) -> list:
    """先找本句出现的词和别名，再补充可能的同音词，最多 20 条。"""
    text = (a + "\n" + b).casefold()
    direct, candidates = [], []
    for entry in terms:
        words = (entry["term"], *entry.get("aliases", []))
        if any(word and re.search(_bounded_word(word.casefold()), text) for word in words):
            direct.append(entry)
            if len(direct) == _RELATED_COUNT:
                return direct
        elif len(entry["term"]) >= 2 and (sound := syllables(entry["term"])) is not None:
            candidates.append((sound, entry))
    lengths = {len(sound) for sound, _ in candidates}
    heard = {sound for _, _, sound in _phonetic_windows(text, lengths)}
    return (direct + [entry for sound, entry in candidates if sound in heard])[:_RELATED_COUNT]


def terms_line(terms) -> str:
    """每项 term(note;常被识别成 a/b),无别名省略分号段,用"、"连接。"""
    parts = []
    for t in terms:
        inner = t.get("note", "")
        if t.get("aliases"):
            inner += ";常被识别成 " + "/".join(t["aliases"])
        parts.append("%s(%s)" % (t["term"], inner))
    return "、".join(parts)
