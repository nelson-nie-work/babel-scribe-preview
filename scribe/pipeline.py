"""双引擎流水线(spec §3): 并行识别 → 一致直出 / 大模型综合 → 防护 → 结果。

path:
  empty    两个引擎都没识别出字
  single   只有一个引擎可用，输出本机结果
  fast     两份归一化后一致且没有口头禅/改口, 直出 SenseVoice 版本
  hotword_fallback  Whisper 吐出了热词串, 改用纠正后的 SenseVoice 版本
  local    关闭智能整理，输出本机结果
  llm      大模型整理并通过防护
  fallback 整理服务全部失败, 输出本地纠正后的 SenseVoice 版本(空则 Whisper)
  guarded  整理结果被判为编造, 输出同 fallback
"""
import concurrent.futures as cf
from dataclasses import dataclass
from typing import Callable, Optional

from scribe import fuse
from scribe.asr.filters import hotword_leak
from scribe.llm import LlmError
from scribe.vocab_store import Vocabulary


@dataclass
class Result:
    text: str
    path: str
    provider: Optional[str] = None
    a: str = ""            # Whisper 原文(日志与基准用)
    b: str = ""            # SenseVoice 原文


def _usable(engine) -> bool:
    return bool(engine.ready) and not getattr(engine, "failed", False)


class Pipeline:
    def __init__(self, whisper, sensevoice, llm, terms: Callable[[], str] = lambda: "",
                 mode: Callable[[], str] = lambda: "gpt", log: Callable[[str], None] = lambda line: None,
                 *, correct: Callable[[str], str] = lambda text: text,
                 vocabulary: Optional[Callable[[], Vocabulary]] = None,
                 dual: Callable[[], bool] = lambda: True,
                 smart: Callable[[], bool] = lambda: True,
                 style: Callable[[], str] = lambda: "natural",
                 available: Callable[[], bool] = lambda: True):
        self.log = log
        self.whisper = whisper
        self.sensevoice = sensevoice
        self.llm = llm
        self.terms = terms
        self.mode = mode
        self.correct = correct
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

    def _run(self, name: str, engine, audio, hotwords: str = ""):
        """(文字, 异常);异常时文字为 "" 并记日志。"""
        try:
            text = engine.transcribe(audio, hotwords=hotwords) if hotwords else engine.transcribe(audio)
            return (text or "").strip(), None
        except Exception as e:
            self.log("asr %s error: %r" % (name, e))
            return "", e

    def process(self, audio, on_stage: Callable[[str], None] = lambda s: None) -> Result:
        dual = self.dual()
        smart, style, mode = self.smart(), self.style(), self.mode()
        w_ok, s_ok = dual and _usable(self.whisper), _usable(self.sensevoice)
        lexicon = self.vocabulary() if self.vocabulary is not None else None
        correct = lexicon.correct if lexicon is not None else self.correct
        if w_ok != s_ok:
            text, err = self._run("whisper" if w_ok else "sensevoice",
                                  self.whisper if w_ok else self.sensevoice, audio)
            if err is not None:
                raise RuntimeError("识别引擎出错") from err
            a, b = (text, "") if w_ok else ("", text)
            corrected = correct(text)
            if not dual and smart and corrected:
                return self._polish("", corrected, a, b, lexicon, style, mode, on_stage)
            return Result(corrected, "single" if text else "empty", None, a, b)
        if not w_ok:
            return Result("", "empty")
        fa = self._pool.submit(self._run, "whisper", self.whisper, audio, lexicon.hotwords if lexicon else "")
        fb = self._pool.submit(self._run, "sensevoice", self.sensevoice, audio)
        (a, ea), (b, eb) = fa.result(), fb.result()
        if ea is not None and eb is not None:       # 两个都出错: 让控制器报「识别出错」, 不静默丢话
            raise RuntimeError("识别引擎出错") from eb
        if not a and not b:
            return Result("", "empty", None, a, b)
        corrected_b = correct(b)
        if lexicon and hotword_leak(a, b, lexicon.hotword_terms):
            self.log("whisper hotword leak: using sensevoice")
            return Result(corrected_b, "hotword_fallback", None, a, b)
        corrected_a = correct(a)
        local_text = corrected_b or corrected_a
        if not smart:
            return Result(local_text, "local", None, a, b)
        if (fuse.agree(corrected_a, corrected_b)
                and not (fuse.needs_polish(corrected_a) or fuse.needs_polish(corrected_b))):
            return Result(corrected_b, "fast", None, a, b)
        return self._polish(corrected_a, corrected_b, a, b, lexicon, style, mode, on_stage)

    def _polish(self, corrected_a, corrected_b, a, b, lexicon, style, mode, on_stage) -> Result:
        on_stage("polish")
        local_text = corrected_b or corrected_a
        terms = lexicon.prompt(a, b) if lexicon is not None else self.terms()

        def accepted(text: str) -> bool:
            if style == "punctuation":
                return fuse.same_words(text, local_text)
            return fuse.guard_ok(text, corrected_a, corrected_b, terms)

        try:
            out, provider = self.llm.complete(fuse.build_messages(corrected_a, corrected_b, terms, style),
                                              mode, accept=accepted)
        except LlmError as e:
            self.log("llm error: %s" % e)
            return Result(local_text, "fallback", None, a, b)
        if not accepted(out):
            return Result(local_text, "guarded", provider, a, b)
        return Result(out, "llm", provider, a, b)
