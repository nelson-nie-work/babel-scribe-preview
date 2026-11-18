"""整理服务的预设表与服务记录 → 连接配置(spec 2026-11-18 整理服务配置)。

永不打印或记录 api_key。
"""
import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

_NO_THINK = {"thinking": {"type": "disabled"}}
_GENERIC = {"temperature": 0.2, "max_tokens": 600}


@dataclass(frozen=True)
class Preset:
    label: str
    base_url: str
    model: str
    params: dict
    needs_key: bool = True


PRESETS = {
    "openai": Preset("OpenAI", "https://api.openai.com/v1", "gpt-5.4-mini",
                     {"reasoning_effort": "none", "max_completion_tokens": 600}),
    "deepseek": Preset("DeepSeek", "https://api.deepseek.com", "deepseek-v4-flash",
                       {"temperature": 0.2, "max_tokens": 600, "extra_body": _NO_THINK}),
    "glm": Preset("智谱", "https://open.bigmodel.cn/api/paas/v4", "glm-4.5-air",
                  {"temperature": 0.2, "max_tokens": 600, "extra_body": _NO_THINK}),
    "openrouter": Preset("OpenRouter", "https://openrouter.ai/api/v1", "openai/gpt-5.4-mini", _GENERIC),
    "siliconflow": Preset("硅基流动", "https://api.siliconflow.cn/v1", "Qwen/Qwen2.5-7B-Instruct", _GENERIC),
    "ollama": Preset("Ollama 本机", "http://localhost:11434/v1", "qwen2.5:7b", _GENERIC, needs_key=False),
    "custom": Preset("其他兼容 OpenAI 的服务", "", "", _GENERIC, needs_key=False),   # 本地服务常不要密钥
}
_LEGACY = ("openai", "deepseek", "glm")
_NO_KEY_PLACEHOLDER = "ollama"     # openai SDK 要求 api_key 非空


@dataclass
class ProviderConf:
    name: str                      # 预设名, 决定专属参数
    base_url: Optional[str]
    api_key: str = field(repr=False)
    model: str
    params: dict
    id: str = ""
    label: str = ""

    def __repr__(self) -> str:      # 防止 key 进日志
        return "ProviderConf(%s, %s)" % (self.label or self.name, self.model)


def _copy(params: dict) -> dict:
    return {k: (dict(v) if isinstance(v, dict) else v) for k, v in params.items()}


def conf_from_entry(entry: dict) -> Optional[ProviderConf]:
    """一条服务记录 → 连接配置;缺地址、模型或必需的密钥时返回 None。"""
    preset = PRESETS.get(entry.get("preset"), PRESETS["custom"])
    base_url = str(entry.get("base_url") or "").strip()
    model = str(entry.get("model") or "").strip()
    api_key = str(entry.get("api_key") or "").strip()
    if not base_url or not model:
        return None
    if not api_key:
        if preset.needs_key:
            return None
        api_key = _NO_KEY_PLACEHOLDER
    return ProviderConf(name=entry.get("preset") or "custom", base_url=base_url, api_key=api_key,
                        model=model, params=_copy(preset.params), id=str(entry.get("id") or ""),
                        label=str(entry.get("name") or preset.label).strip())


def missing_field(values: dict) -> Optional[str]:
    """服务记录缺什么: missing_field(地址或模型) / missing_key(必填的密钥) / None(齐了)。"""
    if not str(values.get("base_url") or "").strip() or not str(values.get("model") or "").strip():
        return "missing_field"
    preset = PRESETS.get(values.get("preset"), PRESETS["custom"])
    if preset.needs_key and not str(values.get("api_key") or "").strip():
        return "missing_key"
    return None


def same_connection(values: dict, saved: Optional[dict]) -> bool:
    """两份服务记录的服务商、地址、模型、密钥是否相同(忽略名称和首尾空白)。"""
    if not saved:
        return False
    fields = ("preset", "base_url", "model", "api_key")
    return all(str(values.get(f) or "").strip() == str(saved.get(f) or "").strip() for f in fields)


def legacy_entries(path: Path) -> list:
    """旧 keys.json → 服务记录(按 openai、deepseek、glm 顺序, 只取填了密钥的);缺失或损坏 → []。"""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(raw, dict):
        return []
    out = []
    for name in _LEGACY:
        item = raw.get(name)
        key = str(item.get("api_key") or "").strip() if isinstance(item, dict) else ""
        if not key:
            continue
        preset = PRESETS[name]
        out.append({"id": uuid.uuid4().hex, "preset": name, "name": preset.label,
                    "base_url": item.get("base_url") or preset.base_url, "model": preset.model,
                    "api_key": key})
    return out

