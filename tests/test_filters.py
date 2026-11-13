from scribe.asr.filters import hotword_leak, is_hallucination, keep_segment


def test_hallucination_subscribe_phrase():
    assert is_hallucination("请不吝点赞订阅转发打赏支持明镜与点点栏目")


def test_hallucination_thanks_for_watching():
    assert is_hallucination("谢谢观看")


def test_real_sentence_kept():
    assert not is_hallucination("帮我把这个文件发给张总。")


def test_low_confidence_segment_dropped():
    assert not keep_segment("嗯嗯嗯", 0.1, -1.3, 1.2)


def test_repetitive_segment_dropped():
    assert not keep_segment("好的好的好的好的好的好的", 0.1, -0.2, 2.6)


def test_ordinary_phrase_inside_real_sentence_kept():
    assert not is_hallucination("请大家多多支持", 0.1, -0.3)
    assert not is_hallucination("这个我们下期再讨论", 0.1, -0.3)


def test_weak_phrase_alone_still_dropped():
    assert is_hallucination("多多支持", 0.1, -0.3)


def test_weak_phrase_dropped_when_low_confidence():
    assert is_hallucination("请大家多多支持", 0.6, -0.3)


def test_hotword_leak_detects_consecutive_complete_entries():
    words = ["Claude", "Claude Code", "CLAUDE.md", "DEMO TOOLS", "BABEL"]
    assert hotword_leak("ｃｌａｕｄｅ  Code，CLAUDE.md、DEMO TOOLS。", "请把文件发给张总", words)
    assert hotword_leak("Claude Code CLAUDE.md DEMO TOOLS?", "请把文件发给张总", words)


def test_hotword_leak_requires_three_distinct_complete_entries():
    words = ["BABEL", "Claude", "Whisper"]
    sensevoice = "请把文件发给张总"
    assert not hotword_leak("BABEL Claude Claude", sensevoice, words)
    assert not hotword_leak("BABEL Claude Whisper.cpp", sensevoice, words)
    assert not hotword_leak("BABEL Claude Whispering", sensevoice, words)
    assert not hotword_leak("BABEL Claude and Whisper", sensevoice, words)


def test_hotword_leak_requires_different_nonempty_sensevoice():
    words = ["BABEL", "Claude", "Whisper"]
    text = "BABEL, Claude, Whisper."
    assert not hotword_leak(text, "Babel Claude Whisper", words)
    assert not hotword_leak(text, "。", words)
