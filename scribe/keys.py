"""读 babel-data/keys.json → 各家整理模型的连接配置;模式 → 调用顺序(spec §3.2)。

永不打印或记录 api_key。
"""
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

_NO_THINK = {"thinking": {"type": "disabled"}}

# 家名 → (模型, 默认 base_url, 参数)
_SPECS = {
    "openai": ("gpt-5.4-mini", None,
               {"reasoning_effort": "none", "max_completion_tokens": 600}),
    "deepseek": ("deepseek-v4-flash", "https://api.deepseek.com",
                 {"temperature": 0.2, "max_tokens": 600, "extra_body": _NO_THINK}),
    "glm": ("glm-4.5-air", "https://open.bigmodel.cn/api/paas/v4",
            {"temperature": 0.2, "max_tokens": 600, "extra_body": _NO_THINK}),
}

_ORDERS = {
    "gpt": (["openai"], ["deepseek", "glm"]),
    "deepseek": (["deepseek"], ["openai", "glm"]),
    "auto": (["openai", "deepseek"], ["glm"]),
}


@dataclass
class ProviderConf:
    name: str
    base_url: Optional[str]
    api_key: str = field(repr=False)
    model: str
    params: dict

    def __repr__(self) -> str:      # 防止 key 进日志
        return "ProviderConf(%s, %s)" % (self.name, self.model)


def load_providers(path: Path) -> dict:
    """只返回填了 api_key 的家;文件缺失或损坏 → {}。"""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out = {}
    for name, (model, default_url, params) in _SPECS.items():
        entry = raw.get(name) if isinstance(raw, dict) else None
        if not isinstance(entry, dict) or not str(entry.get("api_key") or "").strip():
            continue
        out[name] = ProviderConf(
            name=name, base_url=entry.get("base_url") or default_url,
            api_key=str(entry["api_key"]).strip(), model=model,
            params={k: (dict(v) if isinstance(v, dict) else v) for k, v in params.items()})
    return out


def order(mode: str) -> tuple:
    """(第一组: 一家顺序 / 两家竞速, 其余按序回退);未知 mode 按 gpt。"""
    first, rest = _ORDERS.get(mode, _ORDERS["gpt"])
    return list(first), list(rest)
