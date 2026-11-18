"""双引擎流水线(spec §3): 并行识别 → 一致直出 / 大模型综合 → 防护 → 结果。

path:
  empty    两个引擎都没识别出字
  single   只有一个引擎可用，输出本机结果
  fast     两份归一化后一致且没有口头禅/改口, 直出 SenseVoice 版本
  hotword_fallback  Whisper 吐出了热词串, 改用纠正后的 SenseVoice 版本
  local    关闭智能整理，输出本机结果
  llm      大模型整理并通过防护
  fallback 整理服务全部失败, 输出本地纠正后的 SenseVoice 版本(空则 Whisper)
  guarded  整理结果未通过检查, 输出同 fallback

SenseVoice 按停顿分段识别(scribe.segmenter): 录音中已识别的小段由 live.segments() 交来,
松手后只识别剩余部分; 没有 live 时整段切开再识别。Whisper 自带 VAD 与 30 秒分窗, 仍识别整段。
"""
import concurrent.futures as cf
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from scribe import fuse
from scribe.asr.filters import hotword_leak
from scribe.audio import is_silent
from scribe.llm import LlmError
from scribe.segmenter import join_texts, pause_length, soften_end, split
from scribe.vocab_store import Vocabulary

_SEGMENT_MIN_VOICED = 3     # 小段里有声块少于 3 个(约 0.1 秒)就不送识别


@dataclass
class Result:
    text: str
    path: str
    provider: Optional[str] = None
    a: str = ""            # Whisper 原文(日志与基准用)
    b: str = ""            # SenseVoice 原文
    asr_ms: int = field(default=0, compare=False)      # 识别耗时(两引擎并行时取整体)
    polish_ms: int = field(default=0, compare=False)   # 整理耗时, 没调用整理服务为 0


_EMPTY = Vocabulary([])


def _usable(engine) -> bool:
    return bool(engine.ready) and not getattr(engine, "failed", False)


def _usable_parts(parts, length: int) -> list:
    """录音中识别的小段须从 0 开始首尾相接且不超出音频, 否则整段重新识别。"""
    parts = list(parts or [])
    end = 0
    for a, b, _text in parts:
        if a != end or not a < b <= length:
            return []
        end = b
    return parts


def _ms(since: float) -> int:
    return max(0, round((time.perf_counter() - since) * 1000))


class Pipeline:
    def __init__(self, whisper, sensevoice, llm,
                 log: Callable[[str], None] = lambda line: None,
                 *, vocabulary: Callable[[], Vocabulary] = lambda: _EMPTY,
                 dual: Callable[[], bool] = lambda: True,
                 smart: Callable[[], bool] = lambda: True,
                 style: Callable[[], str] = lambda: "natural",
                 available: Callable[[], bool] = lambda: True):
        self.log = log
        self.whisper = whisper
        self.sensevoice = sensevoice
        self.llm = llm
        self.vocabulary = vocabulary
        self.dual = dual
        self.smart = smart
        self.style = style
        self.available = available
        self._pool = cf.ThreadPoolExecutor(max_workers=2, thread_name_prefix="scribe-asr2")

    @property
    def ready(self) -> bool:
        if not self.available():
            return False
        w, s = self.whisper, self.sensevoice
        if not self.dual():
            return _usable(s)
        return (_usable(w) and _usable(s)) or (_usable(w) and s.failed) or (_usable(s) and w.failed)

    @property
    def failed(self) -> bool:
        return bool(self.available() and self.sensevoice.failed and (not self.dual() or self.whisper.failed))

    def _run(self, name: str, transcribe: Callable[[], str]):
        """(文字, 异常);异常时文字为 "" 并记日志。"""
        try:
            return (transcribe() or "").strip(), None
        except Exception as e:
            self.log("asr %s error: %r" % (name, e))
            return "", e

    def _whisper(self, audio, hotwords: str) -> str:
        return self.whisper.transcribe(audio, hotwords=hotwords) if hotwords else self.whisper.transcribe(audio)

    def _sensevoice(self, audio, live) -> str:
        """录音中已识别的小段 + 剩余部分按停顿切开逐段识别, 拼成一份。"""
        parts = _usable_parts(live.segments() if live is not None else [], len(audio))
        start = parts[-1][1] if parts else 0
        texts = [text for _a, _b, text in parts]
        bounds = split(audio[start:])
        several = len(bounds) > 1 or bool(parts)    # 整段只有一段时静音判断由控制器负责
        for i, (a, b) in enumerate(bounds):
            piece = audio[start + a:start + b]
            if several and is_silent(piece, min_voiced_blocks=_SEGMENT_MIN_VOICED):
                continue
            text = self.sensevoice.transcribe(piece) or ""
            if i < len(bounds) - 1:                  # 衔接处: 句中换气的段末句号改逗号
                text = soften_end(text, pause_length(audio, start + b))
            texts.append(text)
        if len(texts) > 1:
            self.log("sensevoice: %d 段, 其中录音中已识别 %d 段" % (len(texts), len(parts)))
        return join_texts(texts)

    def process(self, audio, on_stage: Callable[[str], None] = lambda s: None, *, live=None) -> Result:
        dual = self.dual()
        smart, style = self.smart(), self.style()
        w_ok, s_ok = dual and _usable(self.whisper), _usable(self.sensevoice)
        lexicon = self.vocabulary()
        correct = lexicon.correct
        started = time.perf_counter()
        if w_ok != s_ok:
            if w_ok:
                text, err = self._run("whisper", lambda: self._whisper(audio, ""))
            else:
                text, err = self._run("sensevoice", lambda: self._sensevoice(audio, live))
            if err is not None:
                raise RuntimeError("识别引擎出错") from err
            asr_ms = _ms(started)
            a, b = (text, "") if w_ok else ("", text)
            corrected = correct(text)
            if not dual and smart and corrected:
                return self._polish("", corrected, a, b, lexicon, style, on_stage, asr_ms)
            return Result(corrected, "single" if text else "empty", None, a, b, asr_ms)
        if not w_ok:
            return Result("", "empty")
        fa = self._pool.submit(self._run, "whisper", lambda: self._whisper(audio, lexicon.hotwords))
        fb = self._pool.submit(self._run, "sensevoice", lambda: self._sensevoice(audio, live))
        (a, ea), (b, eb) = fa.result(), fb.result()
        asr_ms = _ms(started)
        if ea is not None and eb is not None:       # 两个都出错: 让控制器报「识别出错」, 不静默丢话
            raise RuntimeError("识别引擎出错") from eb
        if not a and not b:
            return Result("", "empty", None, a, b, asr_ms)
        corrected_b = correct(b)
        if hotword_leak(a, b, lexicon.hotword_terms):
            self.log("whisper hotword leak: using sensevoice")
            return Result(corrected_b, "hotword_fallback", None, a, b, asr_ms)
        corrected_a = correct(a)
        local_text = corrected_b or corrected_a
        if not smart:
            return Result(local_text, "local", None, a, b, asr_ms)
        if (fuse.agree(corrected_a, corrected_b)
                and not (fuse.needs_polish(corrected_a) or fuse.needs_polish(corrected_b))):
            return Result(corrected_b, "fast", None, a, b, asr_ms)
        return self._polish(corrected_a, corrected_b, a, b, lexicon, style, on_stage, asr_ms)

    def _polish(self, corrected_a, corrected_b, a, b, lexicon, style, on_stage, asr_ms) -> Result:
        on_stage("polish")
        started = time.perf_counter()
        local_text = corrected_b or corrected_a
        terms = lexicon.prompt(a, b)
        term_names = [entry["term"] for entry in lexicon.terms]   # 核对时用整个词库: 只查表, 不占提示长度

        def accepted(text: str) -> bool:
            text = lexicon.correct_aliases(text)
            if style == "punctuation":
                return fuse.same_words(text, local_text)
            if style == "natural" and not fuse.keeps_natural_words(text, local_text, term_names):
                return False
            return fuse.guard_ok(text, corrected_a, corrected_b, terms)

        try:
            out, service = self.llm.complete(fuse.build_messages(corrected_a, corrected_b, terms, style),
                                             accept=accepted)
            provider = self.llm.label(service)       # 日志和历史记服务名, 不记内部 id
        except LlmError as e:
            self.log("llm error: %s" % e)
            return Result(local_text, "fallback", None, a, b, asr_ms, _ms(started))
        if not accepted(out):
            return Result(local_text, "guarded", provider, a, b, asr_ms, _ms(started))
        return Result(lexicon.correct_aliases(out), "llm", provider, a, b, asr_ms, _ms(started))
