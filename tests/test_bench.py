import json

from bench.run_bench import cer, load_set, percentile, summarize


def test_cer_ignores_punctuation_and_case():
    assert cer("你好，世界。", "你好世届") == 0.25
    assert cer("Let's go.", "lets go") == 0.0
    assert cer("", "多") == 1.0


def test_percentile_nearest_rank():
    xs = [100, 200, 300, 400, 500, 600, 700, 800, 900, 1000]
    assert percentile(xs, 50) == 500 and percentile(xs, 90) == 900
    assert percentile([], 50) is None


def test_load_set_applies_clean_overrides(tmp_path):
    d = tmp_path / "audio_real"
    d.mkdir()
    (d / "truth.json").write_text(json.dumps({"r01": "甲", "r02": "嗯乙", "r03": None}), encoding="utf-8")
    for n in ("r01.wav", "r02.wav", "r03.wav", "notes.txt"):
        (d / n).write_bytes(b"")
    clean = {"audio_real": {"r02": "乙"}, "other": {"r01": "x"}}
    items = load_set(d, clean)
    assert [(i["key"], i["truth"]) for i in items] == [("r01", "甲"), ("r02", "乙"), ("r03", None)]


def test_load_set_synth_names(tmp_path):
    d = tmp_path / "audio_synth"
    d.mkdir()
    (d / "truth.json").write_text(json.dumps({"s1_short": "甲"}), encoding="utf-8")
    (d / "s1_short.mp3").write_bytes(b"")
    assert load_set(d, {})[0]["key"] == "s1_short"


def test_summarize_groups_paths():
    rows = [
        {"path": "fast", "ms": 100, "cer": 0.0},
        {"path": "fast", "ms": 300, "cer": 0.1},
        {"path": "llm", "ms": 900, "cer": 0.05},
        {"path": "guarded", "ms": 1100, "cer": None},
        {"path": "fallback", "ms": 2600, "cer": 0.2},
    ]
    s = summarize(rows)
    assert s["n_truth"] == 4 and abs(s["cer"] - 0.0875) < 1e-9
    assert s["paths"] == {"fast": 2, "llm": 1, "guarded": 1, "fallback": 1}
    assert s["fast_p50"] == 100 and s["fast_p90"] == 300
    assert s["llm_p50"] == 1100 and s["llm_p90"] == 2600
