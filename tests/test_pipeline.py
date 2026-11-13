import numpy as np

from scribe import vocab
from scribe.llm import LlmError
from scribe.pipeline import Pipeline, Result
from scribe.vocab_store import Vocabulary

AUDIO = np.zeros(16000, np.float32)


class FakeEngine:
    def __init__(self, text="", ready=True, failed=False, exc=None):
        self.text, self.ready, self.failed, self.exc = text, ready, failed, exc
        self.calls = 0
        self.hotwords = ""

    def load(self):
        pass

    def transcribe(self, audio, *, hotwords=""):
        self.calls += 1
        self.hotwords = hotwords
        if self.exc:
            raise self.exc
        return self.text


class FakeLlm:
    def __init__(self, answer=("整理后", "openai"), exc=None):
        self.answer, self.exc = answer, exc
        self.calls = []

    def complete(self, messages, mode, accept=None):
        self.calls.append((messages, mode))
        if self.exc:
            raise self.exc
        return self.answer


def make(a, b, llm=None, **kw):
    w = a if isinstance(a, FakeEngine) else FakeEngine(a)
    s = b if isinstance(b, FakeEngine) else FakeEngine(b)
    llm = llm or FakeLlm()
    return Pipeline(w, s, llm, **kw), w, s, llm


def test_agreement_takes_fast_path():
    p, w, s, llm = make("好的", "好的。")
    r = p.process(AUDIO)
    assert (r.text, r.path) == ("好的。", "fast") and llm.calls == []


def test_intentional_single_engine_can_still_use_smart_polish():
    p, w, s, llm = make("不会使用", "你好呀", FakeLlm(("你好呀。", "openai")), dual=lambda: False)
    assert p.ready and p.process(AUDIO).path == "llm"
    assert w.calls == 0 and s.calls == 1 and len(llm.calls) == 1


def test_smart_off_preserves_both_transcripts_without_cloud_call():
    p, _, _, llm = make("测试会话", "测试会议", smart=lambda: False)
    assert p.process(AUDIO) == Result("测试会议", "local", None, "测试会话", "测试会议")
    assert not llm.calls


def test_punctuation_mode_rejects_word_changes():
    p, _, _, llm = make("报告好了", "报告好了啊", FakeLlm(("报告好了。", "openai")),
                        style=lambda: "punctuation")
    assert p.process(AUDIO).path == "guarded"
    assert "只加标点" in llm.calls[0][0][0]["content"]


def test_alias_correction_can_make_engines_agree_without_changing_raw_transcripts():
    p, _, _, llm = make("更新备播", "更新BABEL。", correct=vocab.build_alias_corrector(vocab.SEED))
    assert p.process(AUDIO) == Result("更新BABEL。", "fast", None, "更新备播", "更新BABEL。")
    assert llm.calls == []


def test_alias_correction_survives_cloud_failure():
    p, _, _, llm = make("看一下新桥", "请找新乔。", FakeLlm(exc=LlmError("offline")),
                        correct=vocab.build_alias_corrector(vocab.SEED))
    assert p.process(AUDIO) == Result("请找新桥。", "fallback", None, "看一下新桥", "请找新乔。")
    assert llm.calls[0][0][1]["content"] == "<A>看一下新桥</A>\n<B>请找新桥。</B>"


def test_alias_correction_works_with_one_engine():
    p, _, _, llm = make(FakeEngine(ready=False, failed=True), "更新备播。",
                        correct=vocab.build_alias_corrector(vocab.SEED))
    assert p.process(AUDIO) == Result("更新BABEL。", "single", None, "", "更新备播。")
    assert llm.calls == []


def test_hotword_leak_uses_corrected_sensevoice_and_never_calls_cloud():
    words = Vocabulary(vocab.SEED)
    p, w, s, llm = make("BABEL, Claude Code, DeepSeek", "稍等，问一下新乔。", vocabulary=lambda: words)
    assert p.process(AUDIO) == Result("稍等，问一下新桥。", "hotword_fallback", None,
                                     "BABEL, Claude Code, DeepSeek", "稍等，问一下新乔。")
    assert w.hotwords == words.hotwords and not s.hotwords and not llm.calls


def test_prompt_only_shares_relevant_terms_from_same_snapshot():
    words = Vocabulary(vocab.SEED)
    p, *_rest, llm = make("我打算重启备播", "把BABEL重启一下。",
                          FakeLlm(("我打算重启BABEL。", "openai")), vocabulary=lambda: words)
    assert p.process(AUDIO).path == "llm"
    system_prompt = llm.calls[0][0][0]["content"]
    assert "BABEL(" in system_prompt and "DeepSeek(" not in system_prompt


def test_disagreement_polishes():
    stages = []
    p, w, s, llm = make("我在Bable项目", "我在babo项目", FakeLlm(("我在BABEL项目。", "openai")),
                        terms=lambda: "BABEL(项目名)", mode=lambda: "deepseek")
    r = p.process(AUDIO, on_stage=stages.append)
    assert (r.text, r.path, r.provider) == ("我在BABEL项目。", "llm", "openai")
    assert stages == ["polish"]
    msgs, mode = llm.calls[0]
    assert mode == "deepseek" and "BABEL(项目名)" in msgs[0]["content"]
    assert msgs[1]["content"] == "<A>我在Bable项目</A>\n<B>我在babo项目</B>"


def test_all_llm_fail_outputs_raw():
    p, *_ = make("甲乙丙", "甲乙丁", FakeLlm(exc=LlmError("x")))
    r = p.process(AUDIO)
    assert (r.text, r.path) == ("甲乙丁", "fallback")


def test_fallback_uses_whisper_when_sensevoice_empty():
    p, *_ = make("甲乙丙", "", FakeLlm(exc=LlmError("x")))
    assert p.process(AUDIO) == Result("甲乙丙", "fallback", None, "甲乙丙", "")


def test_fabrication_guarded():
    a = "随便说一段你平时会用语音输入的话"
    b = "随便说一段，你平时会用语音输入的画。"
    fake = FakeLlm(("我平时用语音输入最多的场景就是随手记一些想法比如刚才我在想要不要试试别的输入法", "openai"))
    p, *_ = make(a, b, fake)
    r = p.process(AUDIO)
    assert (r.text, r.path, r.provider) == (b, "guarded", "openai")


def test_single_engine_mode():
    p, w, s, llm = make("你好", FakeEngine("", ready=False, failed=True))
    assert p.ready and not p.failed
    r = p.process(AUDIO)
    assert (r.text, r.path) == ("你好", "single") and llm.calls == [] and s.calls == 0


def test_ready_waits_for_second_engine_still_loading():
    p, *_ = make("你好", FakeEngine("", ready=False))
    assert not p.ready and not p.failed


def test_both_failed():
    p, *_ = make(FakeEngine(ready=False, failed=True), FakeEngine(ready=False, failed=True))
    assert p.failed and not p.ready


def test_both_empty():
    p, w, s, llm = make("", "")
    assert p.process(AUDIO).path == "empty" and llm.calls == []


def test_engine_exception_treated_as_empty():
    p, w, s, llm = make(FakeEngine(exc=RuntimeError("cuda")), "你好呀", FakeLlm(("你好呀。", "openai")))
    r = p.process(AUDIO)
    assert r.path == "llm" and len(llm.calls) == 1
    assert llm.calls[0][0][1]["content"] == "<A></A>\n<B>你好呀</B>"


def test_one_engine_error_is_logged():
    logs = []
    p, *_ = make(FakeEngine(exc=RuntimeError("cuda gone")), "你好呀", FakeLlm(("你好呀。", "openai")),
                 log=logs.append)
    p.process(AUDIO)
    assert any("whisper" in line and "cuda gone" in line for line in logs)


def test_all_usable_engines_raising_is_an_error():
    import pytest
    p, *_ = make(FakeEngine(exc=RuntimeError("a")), FakeEngine(exc=RuntimeError("b")))
    with pytest.raises(RuntimeError):
        p.process(AUDIO)
    p1, *_ = make(FakeEngine(exc=RuntimeError("a")), FakeEngine("", ready=False, failed=True))
    with pytest.raises(RuntimeError):
        p1.process(AUDIO)


def test_agreed_self_correction_goes_to_llm():
    stages = []
    said = "我们约在周三，不对，周四下午两点开会。"
    p, w, s, llm = make("我们约在周三不对周四下午两点开会", said,
                        FakeLlm(("我们约在周四下午两点开会。", "openai")))
    r = p.process(AUDIO, on_stage=stages.append)
    assert (r.text, r.path, r.provider) == ("我们约在周四下午两点开会。", "llm", "openai")
    assert stages == ["polish"] and len(llm.calls) == 1


def test_agreed_plain_sentence_stays_fast():
    p, w, s, llm = make("这个版本的延迟比上一版低了很多", "这个版本的延迟比上一版低了很多。")
    r = p.process(AUDIO)
    assert r.path == "fast" and llm.calls == []


def test_agreed_filler_llm_failure_falls_back_to_agreed_text():
    said = "嗯，那个，我们下周一再定。"
    p, *_ = make("嗯那个我们下周一再定", said, FakeLlm(exc=LlmError("x")))
    assert (p.process(AUDIO).text, p.process(AUDIO).path) == (said, "fallback")
