"""在一组或几组录音上跑识别流水线, 出错字率、路径计数与分段耗时。

    python bench/run_bench.py --set <your-audio-directory> --engine single --polish off
    python bench/run_bench.py --set <your-audio-directory> --engine single --polish natural --vocab <隔离的 vocab.json>
    python bench/run_bench.py --set <your-audio-directory> --clean <your-reference-overrides.json> --engine dual --mode gpt

目录内 rNN.wav / sN_*.mp3 + truth.json({"rNN": "标准答案" | null})。真人录音不入库, 命令行传路径。
--set 可重复: 汇总是所有组合在一起的结果。
--engine single 只加载 SenseVoice(不需要 N 卡); dual 同时加载 Whisper。
--polish off 为直出; natural / punctuation / formal 调用整理服务(对应设置里的「整理程度」)。
--vocab 指定一份 vocab.json, 不给则用空词库, 不读个人词库。
每段计时前先做一次 prewarm(对应 app 里按下热键时的预热, 说话期间就完成了)。
总耗时 = Pipeline.process(松手到文字生成, 不含粘贴), 另记其中的识别与整理耗时。
不跑实时字幕, 所以 app 里字幕占用 SenseVoice 的那部分等待不在这里。
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
SAMPLE_RATE = 16000


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


def load_vocab(path) -> list:
    """只读指定的 vocab.json;不给路径 → 空词库。不会写入任何文件。"""
    if not path:
        return []
    terms = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(terms, list):
        raise ValueError("vocab.json 应为词条列表")
    return terms


def summarize(rows: list) -> dict:
    scored = [r["cer"] for r in rows if r.get("cer") is not None]
    fast = [r["ms"] for r in rows if r["path"] == "fast"]
    llm = [r["ms"] for r in rows if r["path"] in LLM_PATHS]
    total = [r["ms"] for r in rows]
    asr = [r["asr_ms"] for r in rows if "asr_ms" in r]
    polish = [r["polish_ms"] for r in rows if r.get("polish_ms")]
    return {
        "n": len(rows),
        "n_truth": len(scored),
        "cer": sum(scored) / len(scored) if scored else None,
        "paths": dict(collections.Counter(r["path"] for r in rows)),
        "fast_p50": percentile(fast, 50), "fast_p90": percentile(fast, 90),
        "llm_p50": percentile(llm, 50), "llm_p90": percentile(llm, 90),
        "total_p50": percentile(total, 50), "total_p90": percentile(total, 90),
        "asr_p50": percentile(asr, 50), "asr_p90": percentile(asr, 90),
        "polish_p50": percentile(polish, 50), "polish_p90": percentile(polish, 90),
    }


def _sim(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, normalize(a), normalize(b), autojunk=False).ratio()


def _fmt_cer(value) -> str:
    return "-" if value is None else "%.3f" % value


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", action="append", required=True, help="录音目录, 可重复")
    ap.add_argument("--clean", default=None, help="clean_overrides.json")
    ap.add_argument("--engine", default="dual", choices=("single", "dual"),
                    help="single = 只用 SenseVoice; dual = SenseVoice + Whisper(需要 N 卡)")
    ap.add_argument("--polish", default="natural", choices=("off", "punctuation", "natural", "formal"),
                    help="off = 直出; 其余为整理程度")
    ap.add_argument("--mode", default="gpt", choices=("gpt", "deepseek", "auto"), help="整理服务的顺序(按 keys.json 中的三家排序)")
    ap.add_argument("--vocab", default=None, help="vocab.json 路径;不给则用空词库")
    ap.add_argument("--keys", default=None, help="keys.json 路径;默认取本机 babel-data")
    ap.add_argument("--out", default=None, help="逐段结果与汇总写成 JSON")
    ap.add_argument("--scored-only", action="store_true", help="只处理有标准答案的录音")
    ap.add_argument("--summary-only", action="store_true", help="仅打印汇总，不显示逐段原话")
    ap.add_argument("--offline", action="store_true", help="只用本机缓存模型，禁用云端整理")
    args = ap.parse_args(argv)

    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    if args.offline:
        os.environ["HF_HUB_OFFLINE"] = "1"
    import numpy as np
    from faster_whisper import decode_audio

    from scribe import keys
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

    smart = args.polish != "off"
    dual = args.engine == "dual"
    style = "natural" if args.polish == "off" else args.polish
    keys_path = Path(args.keys) if args.keys else shared_data_dir() / "keys.json"
    from scribe.provider_store import migrate_order
    entries = [] if args.offline or not smart else migrate_order(keys.legacy_entries(keys_path), args.mode)
    llm = LlmClient([c for c in (keys.conf_from_entry(e) for e in entries) if c is not None])
    words = Vocabulary(load_vocab(args.vocab))
    whisper, sense = WhisperEngine(), SenseVoiceEngine()
    sense.load()
    if dual:
        whisper.load()
    pipe = Pipeline(whisper, sense, llm, vocabulary=lambda: words,
                    dual=lambda: dual, smart=lambda: smart, style=lambda: style)
    warm = np.zeros(SAMPLE_RATE, np.float32)          # 首次 CUDA/ONNX 初始化不计入
    sense.transcribe(warm)
    if dual:
        whisper.transcribe(warm)
    print("engine %s, polish %s, mode %s, providers %s, 词库 %d 条, %d 段" % (
        args.engine, args.polish, args.mode, ",".join(c.name for c in llm.providers) or "-",
        len(words.terms), len(items)))

    rows = []
    for it in items:
        audio = decode_audio(str(it["file"]), sampling_rate=SAMPLE_RATE)
        if smart:
            llm.prewarm()
        t0 = time.perf_counter()
        res = pipe.process(audio)
        ms = (time.perf_counter() - t0) * 1000
        truth = it["truth"]
        row = {"set": it["set"], "key": it["key"], "audio_s": round(len(audio) / SAMPLE_RATE, 1),
               "path": res.path, "provider": res.provider,
               "ms": round(ms), "asr_ms": res.asr_ms, "polish_ms": res.polish_ms,
               "text": res.text, "a": res.a, "b": res.b, "truth": truth,
               "cer": None if truth is None else round(cer(truth, res.text), 4),
               "cer_raw": None if truth is None else round(cer(truth, res.b or res.a), 4),
               "sim_raw": round(max(_sim(res.text, res.a), _sim(res.text, res.b)), 3)}
        rows.append(row)
        if not args.summary_only:
            print("%-12s %-10s %5.1fs %-16s %-8s %5d ms (识别 %d, 整理 %d)  cer=%-6s raw=%-6s  %s" % (
                it["set"], it["key"], row["audio_s"], res.path, res.provider or "-", ms,
                res.asr_ms, res.polish_ms, _fmt_cer(row["cer"]), _fmt_cer(row["cer_raw"]), res.text))

    s = summarize(rows)
    service = "" if not smart else ", " + ("offline" if args.offline else args.mode)
    print("\n== 汇总 (engine %s, polish %s%s) ==" % (args.engine, args.polish, service))
    print("有标准答案 %d 段, 平均错字率 %s" % (s["n_truth"], "-" if s["cer"] is None else "%.2f%%" % (s["cer"] * 100)))
    print("路径: %s" % s["paths"])
    print("总耗时  P50 %s ms  P90 %s ms" % (s["total_p50"], s["total_p90"]))
    print("识别    P50 %s ms  P90 %s ms" % (s["asr_p50"], s["asr_p90"]))
    print("整理    P50 %s ms  P90 %s ms" % (s["polish_p50"], s["polish_p90"]))
    if smart:
        print("最近平均: %s" % {n: llm.recent_ms(n) for n in sorted(llm.providers)})
    if args.out:
        Path(args.out).write_text(json.dumps({"engine": args.engine, "polish": args.polish,
                                              "mode": "offline" if args.offline else args.mode,
                                              "summary": s, "rows": rows},
                                             ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
