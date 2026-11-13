"""在一组或几组录音上跑完整双引擎流水线, 出错字率、路径计数与耗时。

    python bench/run_bench.py --set <your-audio-directory> --offline
    python bench/run_bench.py --set <your-audio-directory> --clean <your-reference-overrides.json> --mode gpt

目录内 rNN.wav / sN_*.mp3 + truth.json({"rNN": "标准答案" | null})。真人录音不入库, 命令行传路径。
--set 可重复: 汇总是所有组合在一起的结果。每段计时前先做一次 prewarm(对应 app 里按下热键时的预热,
说话期间就完成了), 计时范围 = Pipeline.process(松手到文字出来, 不含粘贴)。
"""
import argparse
import collections
import difflib
import json
import math
import os
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scribe.fuse import normalize  # noqa: E402

AUDIO_EXT = (".wav", ".mp3")
LLM_PATHS = ("llm", "guarded", "fallback")      # 调用了整理服务的路径


def cer(ref: str, hyp: str) -> float:
    """字错率: normalize 后的编辑距离 / 标准答案长度。"""
    r, h = normalize(ref), normalize(hyp)
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = d[j]
            d[j] = min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev = cur
    return d[len(h)] / max(1, len(r))


def percentile(xs, p):
    """最近秩百分位;空列表 → None。"""
    if not xs:
        return None
    xs = sorted(xs)
    return xs[max(0, math.ceil(p / 100 * len(xs)) - 1)]


def load_set(d: Path, clean: dict) -> list:
    """[{key, file, truth}];truth 先取 truth.json, 再用 clean[目录名] 覆盖(口头禅/改口句的整理后答案)。"""
    d = Path(d)
    truth = json.loads((d / "truth.json").read_text(encoding="utf-8"))
    truth.update(clean.get(d.name, {}))
    items = []
    for f in sorted(d.iterdir()):
        if f.suffix.lower() in AUDIO_EXT:
            items.append({"key": f.stem, "file": f, "truth": truth.get(f.stem)})
    return items


def summarize(rows: list) -> dict:
    scored = [r["cer"] for r in rows if r.get("cer") is not None]
    fast = [r["ms"] for r in rows if r["path"] == "fast"]
    llm = [r["ms"] for r in rows if r["path"] in LLM_PATHS]
    return {
        "n": len(rows),
        "n_truth": len(scored),
        "cer": sum(scored) / len(scored) if scored else None,
        "paths": dict(collections.Counter(r["path"] for r in rows)),
        "fast_p50": percentile(fast, 50), "fast_p90": percentile(fast, 90),
        "llm_p50": percentile(llm, 50), "llm_p90": percentile(llm, 90),
    }


def _sim(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, normalize(a), normalize(b), autojunk=False).ratio()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", action="append", required=True, help="录音目录, 可重复")
    ap.add_argument("--clean", default=None, help="clean_overrides.json")
    ap.add_argument("--mode", default="gpt", choices=("gpt", "deepseek", "auto"))
    ap.add_argument("--out", default=None, help="逐段结果与汇总写成 JSON")
    ap.add_argument("--scored-only", action="store_true", help="只处理有标准答案的录音")
    ap.add_argument("--summary-only", action="store_true", help="仅打印汇总，不显示逐段原话")
    ap.add_argument("--offline", action="store_true", help="只用本机缓存模型，禁用云端整理")
    ap.add_argument("--alias-only", action="store_true", help="只做原有别名替换，用于和新词库功能对照")
    args = ap.parse_args(argv)

    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    if args.offline:
        os.environ["HF_HUB_OFFLINE"] = "1"
    import numpy as np
    from faster_whisper import decode_audio

    from scribe import keys, vocab
    from scribe.asr.sensevoice import SenseVoiceEngine
    from scribe.asr.whisper import WhisperEngine
    from scribe.llm import LlmClient
    from scribe.paths import shared_data_dir
    from scribe.pipeline import Pipeline
    from scribe.vocab_store import Vocabulary

    clean = json.loads(Path(args.clean).read_text(encoding="utf-8")) if args.clean else {}
    items = [dict(i, set=Path(s).name) for s in args.set for i in load_set(Path(s), clean)]
    if args.scored_only:
        items = [item for item in items if item["truth"] is not None]

    data = shared_data_dir()
    llm = LlmClient({} if args.offline else keys.load_providers(data / "keys.json"))
    entries = vocab.load_terms(data)
    whisper, sense = WhisperEngine(), SenseVoiceEngine()
    whisper.load()
    sense.load()
    if args.alias_only:
        correct = vocab.build_alias_corrector(entries)
        terms = vocab.terms_line(entries)
        pipe = Pipeline(whisper, sense, llm, terms=lambda: terms, mode=lambda: args.mode, correct=correct)
    else:
        words = Vocabulary(entries)
        pipe = Pipeline(whisper, sense, llm, mode=lambda: args.mode, vocabulary=lambda: words)
    warm = np.zeros(16000, np.float32)          # 首次 CUDA/ONNX 初始化不计入
    whisper.transcribe(warm)
    sense.transcribe(warm)
    print("mode %s, providers %s, %d 段" % (args.mode, ",".join(sorted(llm.providers)), len(items)))

    rows = []
    for it in items:
        audio = decode_audio(str(it["file"]), sampling_rate=16000)
        llm.prewarm(args.mode)
        t0 = time.perf_counter()
        res = pipe.process(audio)
        ms = (time.perf_counter() - t0) * 1000
        row = {"set": it["set"], "key": it["key"], "path": res.path, "provider": res.provider,
               "ms": round(ms), "text": res.text, "a": res.a, "b": res.b,
               "cer": None if it["truth"] is None else round(cer(it["truth"], res.text), 4),
               "sim_raw": round(max(_sim(res.text, res.a), _sim(res.text, res.b)), 3)}
        rows.append(row)
        if not args.summary_only:
            print("%-12s %-4s %-8s %-8s %5d ms  cer=%-6s sim=%.2f  %s" % (
                it["set"], it["key"], res.path, res.provider or "-", ms,
                "-" if row["cer"] is None else "%.3f" % row["cer"], row["sim_raw"], res.text))

    s = summarize(rows)
    print("\n== 汇总 (%s) ==" % ("offline" if args.offline else args.mode))
    print("有标准答案 %d 段, 平均错字率 %s" % (s["n_truth"], "-" if s["cer"] is None else "%.2f%%" % (s["cer"] * 100)))
    print("路径: %s" % s["paths"])
    print("fast  P50 %s ms  P90 %s ms" % (s["fast_p50"], s["fast_p90"]))
    print("%-5s P50 %s ms  P90 %s ms" % ("local" if args.offline else "llm", s["llm_p50"], s["llm_p90"]))
    print("最近平均: %s" % {n: llm.recent_ms(n) for n in sorted(llm.providers)})
    if args.out:
        Path(args.out).write_text(json.dumps({"mode": "offline" if args.offline else args.mode, "summary": s, "rows": rows},
                                             ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
