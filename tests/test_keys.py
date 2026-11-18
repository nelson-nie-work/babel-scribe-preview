import json

from scribe import keys


def write(tmp_path, d):
    p = tmp_path / "keys.json"
    p.write_text(json.dumps(d), encoding="utf-8")
    return p


def test_presets_keep_vendor_params():
    assert keys.PRESETS["openai"].params["reasoning_effort"] == "none"
    assert keys.PRESETS["openai"].params["max_completion_tokens"] == 600
    assert "temperature" not in keys.PRESETS["openai"].params
    for name in ("deepseek", "glm"):
        p = keys.PRESETS[name].params
        assert p["extra_body"] == {"thinking": {"type": "disabled"}}
        assert p["temperature"] == 0.2 and p["max_tokens"] == 600
    assert keys.PRESETS["custom"].params == {"temperature": 0.2, "max_tokens": 600}
    assert keys.PRESETS["ollama"].needs_key is False
    assert keys.PRESETS["deepseek"].model == "deepseek-v4-flash"
    assert keys.PRESETS["glm"].model == "glm-4.5-air"


def test_conf_from_entry_strips_key_and_rejects_incomplete():
    e = {"id": "a1", "preset": "siliconflow", "name": "硅基流动",
         "base_url": "https://api.siliconflow.cn/v1", "model": "Qwen/Qwen3-8B", "api_key": "  sk-xyz\n"}
    conf = keys.conf_from_entry(e)
    assert conf.id == "a1" and conf.api_key == "sk-xyz" and conf.label == "硅基流动"
    assert "sk-xyz" not in repr(conf)
    assert keys.conf_from_entry({**e, "api_key": " "}) is None
    assert keys.conf_from_entry({**e, "model": ""}) is None
    ollama = keys.conf_from_entry({**e, "preset": "ollama", "api_key": ""})
    assert ollama is not None and ollama.api_key == "ollama"


def test_conf_params_are_copies():
    e = {"id": "a", "preset": "openai", "name": "OpenAI", "base_url": "https://api.openai.com/v1",
         "model": "gpt-5.4-mini", "api_key": "k"}
    keys.conf_from_entry(e).params["max_completion_tokens"] = 1
    assert keys.PRESETS["openai"].params["max_completion_tokens"] == 600


def test_legacy_entries_in_vendor_order(tmp_path):
    p = write(tmp_path, {"glm": {"api_key": "g"}, "openai": {"api_key": "o"}, "deepseek": {"api_key": ""}})
    got = keys.legacy_entries(p)
    assert [(e["preset"], e["api_key"]) for e in got] == [("openai", "o"), ("glm", "g")]
    assert got[0]["base_url"] == "https://api.openai.com/v1" and got[0]["model"] == "gpt-5.4-mini"
    assert got[0]["id"] != got[1]["id"]
    assert keys.legacy_entries(tmp_path / "nope.json") == []


def test_legacy_entries_keep_custom_base_url(tmp_path):
    p = write(tmp_path, {"deepseek": {"base_url": "https://proxy.example/v1", "api_key": "d"}})
    assert keys.legacy_entries(p)[0]["base_url"] == "https://proxy.example/v1"


def test_same_connection_ignores_name_and_whitespace():
    saved = {"id": "a", "preset": "openai", "name": "OpenAI", "base_url": "https://api.openai.com/v1",
             "model": "gpt-5.4-mini", "api_key": "sk-1"}
    assert keys.same_connection({**saved, "name": "我的", "api_key": " sk-1\n"}, saved)
    assert not keys.same_connection({**saved, "api_key": "sk-2"}, saved)
    assert not keys.same_connection({**saved, "model": "gpt-5.5"}, saved)
    assert not keys.same_connection(saved, None)


def test_custom_service_key_is_optional():
    e = {"id": "lm", "preset": "custom", "name": "LM Studio", "base_url": "http://localhost:1234/v1",
         "model": "qwen2.5-7b", "api_key": ""}
    assert keys.conf_from_entry(e) is not None
    assert keys.missing_field(e) is None


def test_missing_field_names_what_to_fill():
    base = {"preset": "openai", "base_url": "https://api.openai.com/v1", "model": "gpt-5.4-mini", "api_key": "k"}
    assert keys.missing_field(base) is None
    assert keys.missing_field({**base, "api_key": " "}) == "missing_key"
    assert keys.missing_field({**base, "model": ""}) == "missing_field"
    assert keys.missing_field({**base, "preset": "ollama", "api_key": ""}) is None


def test_local_and_siliconflow_defaults_avoid_thinking_models():
    # Qwen3 默认开思考, 单句整理常超过 2.5 秒的听写时限
    assert keys.PRESETS["siliconflow"].model == "Qwen/Qwen2.5-7B-Instruct"
    assert keys.PRESETS["ollama"].model == "qwen2.5:7b"
