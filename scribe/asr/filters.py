"""Whisper 输出过滤: 字幕腔幻觉、低置信、复读退化、热词泄漏。"""
import re
import unicodedata
from difflib import SequenceMatcher

from scribe.fuse import normalize

# Whisper 对静音/音乐段的经典幻觉话术(训练数据里的字幕腔)。
# 强套话: 正常口述几乎不会出现, 一律丢弃;
# 弱套话: 工作消息里也常说("请大家多多支持""我们下期再讨论"), 只有整句基本就是这句话、
# 或识别置信度低(no_speech_prob > 0.5)时才丢弃。
_STRONG_PHRASES = (
    "请不吝点赞", "点赞支持", "点赞订阅", "点赞关注", "点赞投币", "订阅转发", "订阅频道",
    "关注频道", "打赏支持", "字幕由", "字幕提供", "一键三连",
)
_WEAK_PHRASES = (
    "请点赞", "请订阅", "关注公众号", "转发分享", "分享这些", "分享这条", "谢谢观看", "谢谢收看",
    "感谢观看", "感谢收看", "谢谢支持", "多多支持", "我们下期", "下期再见", "下期再见拜拜", "欢迎收看",
)
_WEAK_MARGIN = 2          # 整句只比套话多 2 个字以内, 视为"基本就是这句"
_LOW_CONFIDENCE = 0.5
_CHANNEL_WORDS = ("点赞", "订阅", "分享", "关注", "打赏", "投币", "转发",
                  "频道", "三连", "弹幕", "观看", "收看")
_HOTWORD_GAP = re.compile(r"[\s,;、]+")
_HOTWORD_MAX_SIMILARITY = 0.5


def is_hallucination(text: str, no_speech_prob: float = 0.0, avg_logprob: float = 0.0) -> bool:
    t = re.sub(r"[，。！？、,.!?\s]", "", text or "")
    if not t:
        return True
    if no_speech_prob > 0.7 and avg_logprob < -1.0:
        return True
    # 强套话: 门槛 32 字(实测出现过 20 字的"请不吝点赞…明镜与点点栏目")
    if len(t) <= 32 and any(p in t for p in _STRONG_PHRASES):
        return True
    low_conf = no_speech_prob > _LOW_CONFIDENCE
    for p in _WEAK_PHRASES:
        if p in t and (low_conf or len(t) <= len(p) + _WEAK_MARGIN):
            return True
    # 频道词组合: 模糊信号, 只在低置信的 16 字短句内判
    if low_conf and len(t) <= 16 and sum(1 for w in _CHANNEL_WORDS if w in t) >= 2:
        return True
    return False


def keep_segment(text: str, no_speech_prob: float, avg_logprob: float,
                 compression_ratio: float) -> bool:
    if avg_logprob < -1.0 or compression_ratio > 2.4:
        return False
    return not is_hallucination(text, no_speech_prob, avg_logprob)


def _normalize_hotword_text(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def hotword_leak(whisper_text: str, sensevoice_text: str, hotwords: list[str]) -> bool:
    """三种热词成串出现且另一份识别明显不同，才认作整串热词泄漏。"""
    sensevoice = normalize(sensevoice_text)
    words = {_normalize_hotword_text(word) for word in hotwords} - {""}
    if not sensevoice or len(words) < 3:
        return False
    # 完整词优先，保留词内空格和句点，例如 Claude Code 和 CLAUDE.md。
    alternatives = "|".join(re.escape(word) for word in sorted(words, key=len, reverse=True))
    pattern = re.compile(
        r"(?<![^\s,;、:「『“(])(?:" + alternatives + r")(?=[\s,;、)」』”]|[.!?。！？](?:\s|$)|$)"
    )
    text = _normalize_hotword_text(whisper_text)
    run = set()
    end = 0
    for match in pattern.finditer(text):
        if not _HOTWORD_GAP.fullmatch(text[end:match.start()]):
            run.clear()
        run.add(match.group())
        if len(run) >= 3:
            similarity = SequenceMatcher(None, normalize(whisper_text), sensevoice, autojunk=False).ratio()
            return similarity < _HOTWORD_MAX_SIMILARITY
        end = match.end()
    return False
