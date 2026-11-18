import numpy as np
import pytest

from scribe import vocab
from scribe.llm import LlmError
from scribe.pipeline import Pipeline, Result
from scribe.vocab_store import Vocabulary, VocabularyStore

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

    def label(self, sid):
        return {"id-openai": "OpenAI"}.get(sid, sid)

    def complete(self, messages, accept=None):
        self.calls.append((messages,))
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


@pytest.mark.parametrize(("original", "edited"), [
    ("呃，我是想public多一些人看到哎。", "呃，我是想让更多人看到。"),
    ("我先把文档公开，然后把 GitHub 链接发给大家。", "我先把文档公开，然后把链接发给大家。"),
    ("如果审核通过就公开。随后把文档放进预览仓库，再发链接。", "把文档放进预览仓库，再发链接。"),
    ("先生成安静的预览。暂时保留原目录。之后让大家核对版本。", "先生成安静的预览。之后让大家核对版本。"),
    ("跑完 CPU 检查以后，别公开安装包。最后核对 SHA。", "跑完 CPU 检查以后，公开安装包。最后核对 SHA。"),
    ("等 3.5 秒再重试。然后记下时间和退出码。", "等 35 秒再重试。然后记下时间和退出码。"),
    ("检查 status != 200 时有没有弹出提示。", "检查 status = 200 时有没有弹出提示。"),
])
@pytest.mark.parametrize("dual", [False, True])
def test_natural_falls_back_when_edit_loses_content(original, edited, dual):
    alternate = original + "嗯"  # 两引擎时仍走整理分支。
    p, *_ = make(alternate, original, FakeLlm((edited, "openai")), dual=lambda: dual)
    result = p.process(AUDIO)
    assert (result.text, result.path) == (original, "guarded")


@pytest.mark.parametrize(("original", "edited"), [
    ("呃，我是想public多一些人看到哎。", "我是想 public，多一些人看到。"),
    ("嗯，先把延持压下来，然后再做瓷库。", "先把延迟压下来，然后再做词库。"),
])
def test_natural_accepts_light_edits(original, edited):
    p, *_ = make("", original, FakeLlm((edited, "openai")), dual=lambda: False)
    assert p.process(AUDIO).text == edited and edited != original


def test_smart_off_preserves_both_transcripts_without_cloud_call():
    p, _, _, llm = make("测试会话", "测试会议", smart=lambda: False)
    assert p.process(AUDIO) == Result("测试会议", "local", None, "测试会话", "测试会议")
    assert not llm.calls


def test_punctuation_mode_rejects_word_changes():
    p, _, _, llm = make("报告好了", "报告好了啊", FakeLlm(("报告好了。", "openai")),
                        style=lambda: "punctuation")
    assert p.process(AUDIO).path == "guarded"
    assert "只加标点" in llm.calls[0][0][0]["content"]


SEED_WORDS = Vocabulary(vocab.SEED)


def test_alias_correction_can_make_engines_agree_without_changing_raw_transcripts():
    p, _, _, llm = make("更新备播", "更新BABEL。", vocabulary=lambda: SEED_WORDS)
    assert p.process(AUDIO) == Result("更新BABEL。", "fast", None, "更新备播", "更新BABEL。")
    assert llm.calls == []


def test_alias_correction_survives_cloud_failure():
    p, _, _, llm = make("看一下新桥", "请找新乔。", FakeLlm(exc=LlmError("offline")),
                        vocabulary=lambda: SEED_WORDS)
    assert p.process(AUDIO) == Result("请找新桥。", "fallback", None, "看一下新桥", "请找新乔。")
    assert llm.calls[0][0][1]["content"] == "<A>看一下新桥</A>\n<B>请找新桥。</B>"


def test_alias_correction_works_with_one_engine():
    p, _, _, llm = make(FakeEngine(ready=False, failed=True), "更新备播。",
                        vocabulary=lambda: SEED_WORDS)
    assert p.process(AUDIO) == Result("更新BABEL。", "single", None, "", "更新备播。")
    assert llm.calls == []


def test_learned_correction_and_undo_take_effect_in_both_engine_modes(tmp_path):
    store = VocabularyStore(tmp_path, vocab.load_terms(tmp_path))
    dual = False
    p, w, s, llm = make("请发给中心国际。", "请发给中心国际。", dual=lambda: dual, smart=lambda: dual,
                        vocabulary=lambda: store.current)
    addition = store.add_alias("中心国际", "中芯国际")
    assert addition is not None
    assert p.process(AUDIO) == Result("请发给中芯国际。", "single", None, "", s.text)
    dual = True
    assert p.process(AUDIO) == Result("请发给中芯国际。", "fast", None, w.text, s.text)
    assert store.undo_alias(addition)
    assert p.process(AUDIO).text == s.text
    dual = False
    assert p.process(AUDIO).text == s.text
    assert not llm.calls


def test_dual_polish_cannot_reintroduce_a_learned_wrong_spelling(tmp_path):
    store = VocabularyStore(tmp_path, vocab.load_terms(tmp_path))
    assert store.learn_alias("中心国际", "中芯国际")
    wrong = "请把纪要发给中心国际。"
    p, *_ = make("请把会议纪要发给中芯国际。", wrong, FakeLlm((wrong, "openai")),
                 vocabulary=lambda: store.current)
    assert p.process(AUDIO).text == "请把纪要发给中芯国际。"


def test_hotword_leak_uses_corrected_sensevoice_and_never_calls_cloud():
    words = Vocabulary(vocab.SEED)
    p, w, s, llm = make("BABEL, Claude Code, DeepSeek", "稍等，问一下新乔。", vocabulary=lambda: words)
    assert p.process(AUDIO) == Result("稍等，问一下新桥。", "hotword_fallback", None,
                                     "BABEL, Claude Code, DeepSeek", "稍等，问一下新乔。")
    assert w.hotwords == words.hotwords and not s.hotwords and not llm.calls


def test_prompt_only_shares_relevant_terms_from_same_snapshot():
    words = Vocabulary(vocab.SEED)
    p, *_rest, llm = make("我打算重启备播", "把BABEL重启一下。",
                          FakeLlm(("把BABEL重启一下。", "openai")), vocabulary=lambda: words)
    assert p.process(AUDIO).path == "llm"
    system_prompt = llm.calls[0][0][0]["content"]
    assert "BABEL(" in system_prompt and "DeepSeek(" not in system_prompt


def test_disagreement_polishes():
    stages = []
    words = Vocabulary([{"term": "BABEL", "aliases": ["babo"], "note": "项目名"}])
    p, w, s, llm = make("我在Bable项目", "我在babo项目", FakeLlm(("我在BABEL项目。", "openai")),
                        vocabulary=lambda: words, style=lambda: "formal")
    r = p.process(AUDIO, on_stage=stages.append)
    assert (r.text, r.path, r.provider) == ("我在BABEL项目。", "llm", "openai")
    assert stages == ["polish"]
    (msgs,) = llm.calls[0]
    assert "BABEL(项目名;常被识别成 babo)" in msgs[0]["content"]
    assert msgs[1]["content"] == "<A>我在Bable项目</A>\n<B>我在BABEL项目</B>"


def test_result_reports_recognition_and_polish_time():
    p, *_ = make("", "你好呀", FakeLlm(("你好呀。", "openai")), dual=lambda: False)
    r = p.process(AUDIO)
    assert r.path == "llm" and r.asr_ms >= 0 and r.polish_ms >= 0
    direct = make("", "你好呀", dual=lambda: False, smart=lambda: False)[0].process(AUDIO)
    assert direct.path == "single" and direct.polish_ms == 0


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


def test_natural_keeps_both_parts_of_self_correction():
    stages = []
    said = "我们约在周三，不对，周四下午两点开会。"
    p, w, s, llm = make("我们约在周三不对周四下午两点开会", said,
                        FakeLlm(("我们约在周四下午两点开会。", "openai")))
    r = p.process(AUDIO, on_stage=stages.append)
    assert (r.text, r.path, r.provider) == (said, "guarded", "openai")
    assert stages == ["polish"] and len(llm.calls) == 1


def test_agreed_plain_sentence_stays_fast():
    p, w, s, llm = make("这个版本的延迟比上一版低了很多", "这个版本的延迟比上一版低了很多。")
    r = p.process(AUDIO)
    assert r.path == "fast" and llm.calls == []


def test_agreed_filler_llm_failure_falls_back_to_agreed_text():
    said = "嗯，那个，我们下周一再定。"
    p, *_ = make("嗯那个我们下周一再定", said, FakeLlm(exc=LlmError("x")))
    assert (p.process(AUDIO).text, p.process(AUDIO).path) == (said, "fallback")


class RecordingEngine(FakeEngine):
    """按收到音频的长度返回不同文字, 记下每次识别的长度。"""

    def __init__(self):
        super().__init__("x")
        self.lengths = []

    def transcribe(self, audio, *, hotwords=""):
        self.lengths.append(len(audio))
        return "段%d" % len(self.lengths)


def long_speech(seconds_list, pause=0.5):
    rng = np.random.default_rng(1)
    parts = []
    for s in seconds_list:
        parts += [rng.standard_normal(int(s * 16000)).astype(np.float32) * 0.05,
                  np.zeros(int(pause * 16000), np.float32)]
    return np.concatenate(parts)


class FakeLive:
    def __init__(self, parts):
        self.parts = parts

    def segments(self):
        return self.parts


def test_long_audio_is_recognized_in_short_segments_and_joined():
    s = RecordingEngine()
    p, *_ = make("", s, dual=lambda: False, smart=lambda: False)
    audio = long_speech([7, 7, 7, 7])
    r = p.process(audio)
    assert len(s.lengths) >= 2 and max(s.lengths) <= 12 * 16000
    assert sum(s.lengths) == len(audio)
    assert r.text == "".join("段%d" % (i + 1) for i in range(len(s.lengths)))


def test_live_segments_are_reused_and_only_the_rest_is_recognized():
    s = RecordingEngine()
    p, *_ = make("", s, dual=lambda: False, smart=lambda: False)
    audio = long_speech([7, 7, 3])
    cut = int(7.25 * 16000)
    r = p.process(audio, live=FakeLive([(0, cut, "录音中已识别。")]))
    assert s.lengths == [len(audio) - cut]
    assert r.text == "录音中已识别。段1"


def test_live_segments_that_do_not_fit_the_audio_are_ignored():
    s = RecordingEngine()
    p, *_ = make("", s, dual=lambda: False, smart=lambda: False)
    audio = long_speech([3])
    r = p.process(audio, live=FakeLive([(0, len(audio) + 10, "别的录音")]))
    assert r.text == "段1" and s.lengths == [len(audio)]


def test_dual_engine_segments_sensevoice_but_not_whisper():
    s, w = RecordingEngine(), FakeEngine("整段 Whisper")
    w.lengths = []
    w_transcribe = w.transcribe
    w.transcribe = lambda audio, **kw: (w.lengths.append(len(audio)), w_transcribe(audio, **kw))[1]
    p, *_ = make(w, s, smart=lambda: False)
    audio = long_speech([7, 7, 7])
    r = p.process(audio)
    assert w.lengths == [len(audio)] and len(s.lengths) >= 2
    assert r.a == "整段 Whisper" and r.b.startswith("段1段2")


def test_natural_accepts_case_and_vocabulary_corrections():
    words = Vocabulary([{"term": "Claude", "note": "AI 编程助手"}])
    source = "用cloud写代码的时候，记得先让whisper读一下文件。"
    edited = "用 Claude 写代码的时候，记得先让 Whisper 读一下文件。"
    p, *_ = make("", source, FakeLlm((edited, "openai")), dual=lambda: False, vocabulary=lambda: words)
    assert (p.process(AUDIO).text, p.process(AUDIO).path) == (edited, "llm")
    p2, *_ = make("", source, FakeLlm((edited, "openai")), dual=lambda: False)   # 词库里没有 Claude
    assert p2.process(AUDIO).path == "guarded"


def test_smart_polish_with_no_services_keeps_local_text():
    p, *_ = make("", "本机识别稿。", FakeLlm(exc=LlmError("没有可用的整理服务")), dual=lambda: False)
    r = p.process(AUDIO)
    assert (r.text, r.path) == ("本机识别稿。", "fallback")


def test_result_records_service_display_name_not_id():
    p, *_ = make("", "你好呀", FakeLlm(("你好呀。", "id-openai")), dual=lambda: False)
    assert p.process(AUDIO).provider == "OpenAI"
