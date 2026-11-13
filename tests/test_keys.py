import json

from scribe import keys


def write(tmp_path, d):
    p = tmp_path / "keys.json"
    p.write_text(json.dumps(d), encoding="utf-8")
    return p


def test_load_providers_skips_missing_keys(tmp_path):
    p = write(tmp_path, {"openai": {"api_key": "sk-test"}, "deepseek": {"base_url": "https://x", "api_key": ""}})
    provs = keys.load_providers(p)
    assert set(provs) == {"openai"}
    assert provs["openai"].model == "gpt-5.4-mini"
    assert provs["openai"].params["reasoning_effort"] == "none"
    assert provs["openai"].params["max_completion_tokens"] == 600
    assert "temperature" not in provs["openai"].params


def test_deepseek_params_disable_thinking(tmp_path):
    p = write(tmp_path, {"deepseek": {"base_url": "https://api.deepseek.com", "api_key": "k"},
                         "glm": {"base_url": "https://glm", "api_key": "k"}})
    provs = keys.load_providers(p)
    ds = provs["deepseek"]
    assert ds.model == "deepseek-v4-flash" and ds.base_url == "https://api.deepseek.com"
    assert ds.params["extra_body"] == {"thinking": {"type": "disabled"}}
    assert ds.params["temperature"] == 0.2 and ds.params["max_tokens"] == 600
    assert provs["glm"].model == "glm-4.5-air"
    assert provs["glm"].params["extra_body"] == {"thinking": {"type": "disabled"}}


def test_missing_key_file_returns_empty(tmp_path):
    assert keys.load_providers(tmp_path / "nope.json") == {}


def test_order_modes():
    assert keys.order("gpt") == (["openai"], ["deepseek", "glm"])
    assert keys.order("deepseek") == (["deepseek"], ["openai", "glm"])
    assert keys.order("auto") == (["openai", "deepseek"], ["glm"])
