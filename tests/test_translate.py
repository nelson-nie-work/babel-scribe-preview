import pytest

from scribe.llm import LlmError
from scribe.translate import (NoService, TranslateSession, build_translate_messages,
                              clip_text, translate)


class FakeLlm:
    def __init__(self, reply="译文", providers=None, sid="zhipu"):
        self.reply = reply
        self.providers = [object()] if providers is None else providers
        self.sid = sid
        self.calls = []

    def complete(self, messages, accept=None):
        self.calls.append(messages)
        return self.reply, self.sid

    def label(self, sid):
        return "智谱" if sid == "zhipu" else sid


class FakeLexicon:
    def prompt(self, a, b):
        return "BABEL Scribe"


def test_clip_text_truncates_at_2000():
    text, cut = clip_text("字" * 2500)
    assert len(text) == 2000 and cut


def test_clip_text_strips_and_keeps_short():
    assert clip_text("  hi \n") == ("hi", False)


def test_messages_name_target_and_keep_terms():
    m = build_translate_messages("hello", "zh", "BABEL Scribe")
    assert "中文" in m[0]["content"] and "BABEL Scribe" in m[0]["content"] and m[1]["content"] == "hello"


def test_messages_translate_mixed_text_fully():
    # 真机反馈: 中英夹杂译成中文时留着英文词。不能有「已经是目标语言就原样返回」, 词库词也不强制保留
    sys = build_translate_messages("我把 readme update 了", "zh", "readme")[0]["content"]
    assert "中英夹杂" in sys and "原样返回" not in sys
    assert "拼写参考" in sys and "照原样保留): readme" not in sys


def test_messages_target_en():
    assert "English" in build_translate_messages("你好", "en", "")[0]["content"]


def test_translate_reports_service_and_unchanged():
    llm = FakeLlm(reply="Whisper is good.")
    r = translate(llm, FakeLexicon(), "Whisper is good.", "en")
    assert r.unchanged and r.service == "智谱" and not r.truncated


def test_translate_passes_clipped_text_and_terms():
    llm = FakeLlm()
    r = translate(llm, FakeLexicon(), "字" * 2500, "zh")
    assert r.truncated and not r.unchanged and r.text == "译文"
    msgs = llm.calls[0]
    assert len(msgs[1]["content"]) == 2000 and "BABEL Scribe" in msgs[0]["content"]


def test_translate_without_services_raises():
    with pytest.raises(NoService):
        translate(FakeLlm(providers=[]), FakeLexicon(), "x", "zh")


def test_empty_reply_is_failure():
    with pytest.raises(LlmError):
        translate(FakeLlm(reply="  "), FakeLexicon(), "x", "zh")


def test_session_discards_stale_results():
    s = TranslateSession(); a = s.start(); s.cancel()
    assert not s.current(a)
    b = s.start(); c = s.start()
    assert not s.current(b) and s.current(c)
