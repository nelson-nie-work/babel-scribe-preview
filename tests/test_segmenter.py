import numpy as np

from scribe.segmenter import MAX_S, SOFT_S, join_texts, next_cut, split

SR = 16000
rng = np.random.default_rng(0)


def speech(seconds, level=0.05):
    return (rng.standard_normal(int(seconds * SR)) * level).astype(np.float32)


def quiet(seconds, level=1e-4):
    return (rng.standard_normal(int(seconds * SR)) * level).astype(np.float32)


def concat(*parts):
    return np.concatenate(parts)


def test_short_audio_is_not_cut():
    audio = concat(speech(3), quiet(0.5), speech(3))
    assert next_cut(audio, 0) is None
    assert split(audio) == [(0, len(audio))]


def test_live_cut_waits_for_soft_length_then_uses_last_long_pause():
    audio = concat(speech(4), quiet(0.5), speech(3), quiet(0.5), speech(1))
    cut = next_cut(audio, 0)
    second_pause = (4 + 0.5 + 3) * SR
    assert second_pause <= cut <= second_pause + 0.5 * SR
    assert next_cut(audio[: int((SOFT_S - 1) * SR)], 0) is None


def test_live_cut_respects_start_offset():
    audio = concat(speech(10), quiet(0.5), speech(4), quiet(0.5), speech(5))
    first = next_cut(audio, 0)
    assert 10 * SR <= first <= 10.5 * SR
    second = next_cut(audio, first)
    assert second is not None and second > first + 3 * SR


def test_no_long_pause_falls_back_to_short_pause_before_hard_limit():
    audio = concat(speech(6), quiet(0.15), speech(7.5))
    cut = next_cut(audio, 0)
    assert 6 * SR <= cut <= 6.15 * SR


def test_continuous_speech_waits_until_max_then_cuts_at_quietest_spot():
    loud = speech(MAX_S + 1)
    dip = int(10.5 * SR)
    loud[dip:dip + 1440] *= 0.05                   # 90 ms 的轻声处
    assert next_cut(loud[: int((MAX_S - 1) * SR)], 0) is None
    cut = next_cut(loud, 0)
    assert dip <= cut <= dip + 1440


def test_split_keeps_every_segment_within_max_and_covers_audio():
    audio = concat(*[concat(speech(5), quiet(0.4)) for _ in range(12)])
    bounds = split(audio)
    assert bounds[0][0] == 0 and bounds[-1][1] == len(audio)
    assert all(a < b for a, b in bounds)
    assert all(b1 == a2 for (_, b1), (a2, _) in zip(bounds, bounds[1:]))
    assert all(b - a <= MAX_S * SR for a, b in bounds)
    assert len(bounds) >= 5


def test_split_handles_quiet_recording_levels():
    audio = concat(*[concat(speech(5, level=0.01), quiet(0.4, level=2e-5)) for _ in range(6)])
    assert all(b - a <= MAX_S * SR for a, b in split(audio))
    assert len(split(audio)) >= 3


def test_join_texts_spaces_only_between_latin_words():
    assert join_texts(["今天先开会。", "然后写代码"]) == "今天先开会。然后写代码"
    assert join_texts(["send the report", "by Friday."]) == "send the report by Friday."
    assert join_texts(["用 Claude", "写代码"]) == "用 Claude写代码"
    assert join_texts(["", "你好", ""]) == "你好"


def test_pause_length_measures_the_silence_around_a_cut():
    from scribe.segmenter import pause_length

    audio = concat(speech(3), quiet(0.3), speech(3), quiet(1.0), speech(3))
    short_cut = int(3.15 * SR)
    long_cut = int((3 + 0.3 + 3 + 0.5) * SR)
    assert 0.2 <= pause_length(audio, short_cut) <= 0.4
    assert 0.9 <= pause_length(audio, long_cut) <= 1.1
    assert pause_length(audio, int(1.5 * SR)) == 0.0


def test_soften_end_only_turns_periods_into_commas_at_short_pauses():
    from scribe.segmenter import soften_end

    assert soften_end("通过cloud内置的一些。", 0.3) == "通过cloud内置的一些，"
    assert soften_end("先这样。", 0.9) == "先这样。"
    assert soften_end("send it.", 0.2) == "send it,"
    assert soften_end("你明白吗？", 0.2) == "你明白吗？"
    assert soften_end("没有标点", 0.2) == "没有标点"
    assert soften_end("", 0.2) == ""


def test_join_texts_adds_space_after_softened_latin_period():
    assert join_texts(["send the report,", "by Friday."]) == "send the report, by Friday."
    assert join_texts(["先这样，", "然后再说"]) == "先这样，然后再说"


def test_pipeline_joins_mid_sentence_cuts_with_commas():
    from scribe.pipeline import Pipeline
    from scribe.vocab_store import Vocabulary

    class Sense:
        ready, failed = True, False

        def __init__(self):
            self.n = 0

        def transcribe(self, audio):
            self.n += 1
            return "第%d段。" % self.n

    class NoWhisper:
        ready, failed = False, True

    audio = concat(speech(7), quiet(0.3), speech(7), quiet(1.0), speech(7))
    p = Pipeline(NoWhisper(), Sense(), None, vocabulary=lambda: Vocabulary([]),
                 dual=lambda: False, smart=lambda: False)
    text = p.process(audio).text
    assert text.startswith("第1段，") and text.endswith("第3段。")
