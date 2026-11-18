"""长录音按停顿切成小段(2026-11-17 长段基线)。

SenseVoice 整段识别真人长录音时, 错字率随时长上升(40 秒以上大段漏字); 切成 ≤ 12 秒的小段逐段识别,
错字率回到短句水平, 解码总时也更短。只用能量判停顿, 不依赖额外模型。

切点优先级: ≥ 0.3 秒的停顿 → ≥ 0.12 秒的短停顿 → 最安静的 90 毫秒。
边录边切(live)时, 未切部分满 SOFT_S 秒且有长停顿就切, 让大部分音频在松手前识别完;
满 MAX_S 秒仍无长停顿才退而求其次。整段切(final)只在剩余部分超过 MAX_S 时才切。
"""
import numpy as np

SAMPLE_RATE = 16000
FRAME = 480                 # 30 ms
SOFT_S = 8.0                # 边录边切: 未切部分满 8 秒才开始找停顿
MAX_S = 12.0                # 小段上限
MIN_SEG_S = 3.0             # 小段下限, 切点不早于段首后 3 秒
LONG_PAUSE_S = 0.3
SHORT_PAUSE_S = 0.12
QUIET_RATIO = 0.15          # 帧电平低于语音电平(90 分位)的 15% 视为停顿
QUIET_FLOOR = 1e-4
_DIP_FRAMES = 3             # 兜底: 找最安静的连续 3 帧
_DIP_SEARCH_S = 4.0         # 兜底只在上限前 4 秒内找


def _frame_rms(audio: np.ndarray) -> np.ndarray:
    n = len(audio) // FRAME
    if n == 0:
        return np.zeros(0, np.float32)
    frames = audio[: n * FRAME].astype(np.float32).reshape(n, FRAME)
    return np.sqrt((frames ** 2).mean(axis=1))


def _pauses(quiet: np.ndarray, min_frames: int) -> list[tuple[int, int]]:
    out, start = [], None
    for i, q in enumerate(np.append(quiet, False)):
        if q and start is None:
            start = i
        elif not q and start is not None:
            if i - start >= min_frames:
                out.append((start, i))
            start = None
    return out


def _last_pause_mid(quiet, min_s, lo, hi, sr):
    frames = max(1, round(min_s * sr / FRAME))
    mids = [(a + b) // 2 for a, b in _pauses(quiet, frames)]
    mids = [m for m in mids if lo <= m <= hi]
    return mids[-1] if mids else None


def next_cut(audio: np.ndarray, start: int, sr: int = SAMPLE_RATE, *, final: bool = False) -> int | None:
    """audio[start:] 的下一个切点(绝对采样位置);暂时不该切返回 None。"""
    region = audio[start:]
    seconds = len(region) / sr
    if seconds <= MAX_S if final else seconds < SOFT_S:
        return None
    rms = _frame_rms(region)
    if len(rms) == 0:
        return None
    level = float(np.percentile(rms, 90))
    quiet = rms < max(level * QUIET_RATIO, QUIET_FLOOR)
    frames_per_s = sr / FRAME
    lo = int(MIN_SEG_S * frames_per_s)
    hi = min(len(rms) - 1, int(MAX_S * frames_per_s))

    mid = _last_pause_mid(quiet, LONG_PAUSE_S, lo, hi, sr)
    if mid is None and seconds < MAX_S:
        return None                    # 还没到上限, 等下一个长停顿
    if mid is None:
        mid = _last_pause_mid(quiet, SHORT_PAUSE_S, lo, hi, sr)
    if mid is None:
        search_lo = max(lo, hi - int(_DIP_SEARCH_S * frames_per_s))
        window = np.convolve(rms[search_lo:hi + 1], np.ones(_DIP_FRAMES), mode="valid")
        mid = search_lo + int(np.argmin(window)) + _DIP_FRAMES // 2
    return start + mid * FRAME + FRAME // 2


def split(audio: np.ndarray, sr: int = SAMPLE_RATE) -> list[tuple[int, int]]:
    """整段切成 [(开始, 结束)], 首尾相接, 每段不超过 MAX_S 秒。"""
    bounds, start = [], 0
    while (cut := next_cut(audio, start, sr, final=True)) is not None:
        bounds.append((start, cut))
        start = cut
    bounds.append((start, len(audio)))
    return bounds


SENTENCE_PAUSE_S = 0.6      # 衔接处停顿短于它, 视为同一句话里的换气
_SOFTEN = {"。": "，", ".": ",", "！": "，", "？": "？", "!": ",", "?": "?"}
_PAUSE_PROBE_S = 1.5        # 量停顿时, 往切点两侧各看 1.5 秒


def pause_length(audio: np.ndarray, cut: int, sr: int = SAMPLE_RATE) -> float:
    """切点所在停顿的长度(秒);切点不在停顿里返回 0。电平阈值取切点前后 6 秒的语音电平。"""
    lo, hi = max(0, cut - 6 * sr), min(len(audio), cut + 6 * sr)
    rms = _frame_rms(audio[lo:hi])
    if len(rms) == 0:
        return 0.0
    quiet = rms < max(float(np.percentile(rms, 90)) * QUIET_RATIO, QUIET_FLOOR)
    at = min(len(quiet) - 1, (cut - lo) // FRAME)
    if not quiet[at]:
        return 0.0
    reach = int(_PAUSE_PROBE_S * sr / FRAME)
    left = at
    while left > 0 and at - left < reach and quiet[left - 1]:
        left -= 1
    right = at
    while right < len(quiet) - 1 and right - at < reach and quiet[right + 1]:
        right += 1
    return (right - left + 1) * FRAME / sr


def soften_end(text: str, pause_s: float) -> str:
    """段末句号若落在短停顿处(句中换气), 改成逗号;问号保留。只用于后面还有下一段的衔接处。"""
    text = text.rstrip()
    if not text or pause_s >= SENTENCE_PAUSE_S or text[-1] not in _SOFTEN:
        return text
    return text[:-1] + _SOFTEN[text[-1]]


def join_texts(texts) -> str:
    """拼接各段识别结果;前段以拉丁字母、数字或英文标点结尾且后段以拉丁字母或数字开头时补一个空格。"""
    out = ""
    for text in texts:
        text = text.strip()
        if not text:
            continue
        latin_end = out and out[-1].isascii() and (out[-1].isalnum() or out[-1] in ",.!?;:")
        if latin_end and text[0].isascii() and text[0].isalnum():
            out += " "
        out += text
    return out
