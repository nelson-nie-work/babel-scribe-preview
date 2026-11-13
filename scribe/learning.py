"""从最近一次输出的改正中提议一个别名。"""
from difflib import SequenceMatcher

from scribe.pinyin import syllables

_MAX_TERM_LENGTH = 24
_MAX_SELECTION_LENGTH = 2000


def valid_word(text: str) -> bool:
    return (2 <= len(text) <= _MAX_TERM_LENGTH
            and any(c.isalpha() for c in text)
            and all(c.isalnum() or c in " .+#'’_-/" for c in text))


def is_word_selection(text: str) -> bool:
    """短词或短词组走一键收词；较长内容仍走纠错窗。"""
    return len(text) <= 16 and valid_word(text) and not text.endswith(".") and len(text.split()) <= 3


def _single_spoken_word(original: str, selected: str) -> tuple[str, str] | None:
    sound = syllables(selected)
    if not sound or selected in original:
        return None
    width = len(selected)
    matches = []
    for start in range(len(original) - width + 1):
        candidate = original[start:start + width]
        if syllables(candidate) == sound:
            matches.append(candidate)
            if len(matches) > 1:
                return None
    return (matches[0], selected) if matches else None


def suggest_alias(original: str, selected: str, *, known_terms=()) -> tuple[str, str] | None:
    """只接受一个有把握的替换，或最近原文中唯一匹配的选中同音词。"""
    original, selected = original.strip(), selected.strip()
    if (not original or not selected or original == selected
            or max(len(original), len(selected)) > _MAX_SELECTION_LENGTH):
        return None

    if valid_word(selected):
        by_sound = _single_spoken_word(original, selected)
        if by_sound:
            return by_sound

    matcher = SequenceMatcher(None, original, selected, autojunk=False)
    operations = matcher.get_opcodes()
    start = operations[0][2] if operations[0][0] == "delete" else 0
    end = operations[-1][1] if operations[-1][0] == "delete" else len(original)
    if start or end != len(original):
        # 片段的两端都要有相同文字，避免把没选中的原句开头误学成错词。
        first = 1 if start else 0
        last = -1 if end != len(original) else len(operations)
        fragment = operations[first:last]
        if (not fragment or fragment[0][0] != "equal" or fragment[-1][0] != "equal"
                or sum(b - a for op, a, b, _, _ in fragment if op == "equal") < 4):
            return None
        original = original[start:end]
        matcher = SequenceMatcher(None, original, selected, autojunk=False)
    if matcher.ratio() < 0.5:
        return None
    changes = [op for op in matcher.get_opcodes()
               if op[0] != "equal" and any(c.isalnum() for c in original[op[1]:op[2]] + selected[op[3]:op[4]])]
    if len(changes) != 1 or changes[0][0] != "replace":
        return None
    _, a, b, c, d = changes[0]
    alias, term = original[a:b], selected[c:d]
    if not any(ch.isalpha() for ch in alias) or not any(ch.isalpha() for ch in term):
        return None
    if valid_word(alias) and valid_word(term):
        return alias, term

    known = []
    for word in known_terms:
        if not valid_word(word):
            continue
        for start in range(max(0, d - len(word)), c + 1):
            end = start + len(word)
            if not selected.startswith(word, start):
                continue
            left, right = selected[start:c], selected[d:end]
            if a >= len(left) and original[:a].endswith(left) and original[b:].startswith(right):
                candidate = left + alias + right
                if valid_word(candidate):
                    known.append((candidate, word))
    if known:
        return max(known, key=lambda pair: len(pair[1]))

    # 单字误听借一个相同的邻字作上下文，禁止把单字直接写成全局别名。
    if b < len(original) and d < len(selected) and original[b] == selected[d]:
        if valid_word(alias + original[b]) and valid_word(term + selected[d]):
            return alias + original[b], term + selected[d]
    if a > 0 and c > 0 and original[a - 1] == selected[c - 1]:
        if valid_word(original[a - 1] + alias) and valid_word(selected[c - 1] + term):
            return original[a - 1] + alias, selected[c - 1] + term
    return None
