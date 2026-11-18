import json

import pytest

from scribe.provider_store import ProviderStore, migrate_order, protect, unprotect


def fake(store_dir):
    return ProviderStore(store_dir / "providers.json", protect=lambda b: b[::-1], unprotect=lambda b: b[::-1])


def test_round_trip_strips_whitespace(tmp_path):
    s = fake(tmp_path)
    s.save([{"id": "1", "preset": "openai", "name": " OpenAI ", "base_url": "https://api.openai.com/v1 ",
             "model": "gpt-5.4-mini", "api_key": " sk-a\n"}])
    got = s.load()[0]
    assert got["api_key"] == "sk-a" and got["name"] == "OpenAI"
    assert got["base_url"] == "https://api.openai.com/v1"
    assert "sk-a" not in (tmp_path / "providers.json").read_text(encoding="utf-8")
    assert s.unreadable is False


def test_missing_file_means_no_services(tmp_path):
    s = fake(tmp_path)
    assert s.load() == [] and s.unreadable is False


def test_unreadable_file_means_no_services(tmp_path):
    (tmp_path / "providers.json").write_text('{"version": 1, "blob": "!!"}', encoding="utf-8")
    s = fake(tmp_path)
    assert s.load() == [] and s.unreadable is True


def test_decrypt_failure_means_no_services(tmp_path):
    fake(tmp_path).save([{"id": "1", "preset": "openai", "api_key": "k"}])

    def refuse(_blob):
        raise OSError("other user")

    s = ProviderStore(tmp_path / "providers.json", protect=lambda b: b, unprotect=refuse)
    assert s.load() == [] and s.unreadable is True


def test_real_dpapi_round_trip():
    blob = protect(b"secret")
    assert blob != b"secret" and unprotect(blob) == b"secret"


def test_import_legacy_skips_existing_combo(tmp_path):
    keys_path = tmp_path / "keys.json"
    keys_path.write_text(json.dumps({"openai": {"api_key": "o"}, "deepseek": {"api_key": "d"}}), encoding="utf-8")
    have = [{"id": "x", "preset": "openai", "name": "OpenAI", "base_url": "u", "model": "m", "api_key": "o"}]
    got = fake(tmp_path).import_legacy(have, keys_path)
    assert [(e["preset"], e["api_key"]) for e in got] == [("openai", "o"), ("deepseek", "d")]
    assert have == [have[0]]


@pytest.mark.parametrize(("mode", "order"), [
    ("gpt", ["openai", "deepseek", "glm"]), ("deepseek", ["deepseek", "openai", "glm"]),
    ("auto", ["openai", "deepseek", "glm"])])
def test_migrate_order(mode, order):
    entries = [{"preset": p} for p in ("glm", "custom", "deepseek", "openai")]
    assert [e["preset"] for e in migrate_order(entries, mode)] == order + ["custom"]


def test_store_path_is_per_machine(tmp_path, monkeypatch):
    from scribe.provider_store import store_path
    monkeypatch.setenv("COMPUTERNAME", "NEW BEAST/1")
    assert store_path(tmp_path) == tmp_path / "providers.NEW-BEAST-1.json"


def test_save_after_unreadable_sets_old_file_aside(tmp_path):
    path = tmp_path / "providers.json"
    path.write_text('{"version": 1, "blob": "!!"}', encoding="utf-8")
    s = fake(tmp_path)
    assert s.load() == [] and s.unreadable
    s.save([{"id": "1", "preset": "openai", "api_key": "k"}])
    kept = list(tmp_path.glob("providers.unreadable-*.json"))
    assert len(kept) == 1 and kept[0].read_text(encoding="utf-8") == '{"version": 1, "blob": "!!"}'
    assert s.unreadable and s.set_aside == kept[0]
    assert [e["id"] for e in fake(tmp_path).load()] == ["1"]


def write_keys(tmp_path, data):
    p = tmp_path / "keys.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def test_deleted_imported_service_is_not_imported_again(tmp_path):
    kp = write_keys(tmp_path, {"openai": {"api_key": "o"}})
    s = fake(tmp_path)
    s.save(s.import_legacy(s.load(), kp))
    s.save([])                                   # 用户在界面里删掉
    s2 = fake(tmp_path)
    assert s2.import_legacy(s2.load(), kp) == []


def test_rotated_key_does_not_bring_old_key_back(tmp_path):
    kp = write_keys(tmp_path, {"openai": {"api_key": "old"}})
    s = fake(tmp_path)
    entries = s.import_legacy(s.load(), kp)
    entries[0]["api_key"] = "new"                # 用户在界面里换了密钥
    s.save(entries)
    s2 = fake(tmp_path)
    assert [e["api_key"] for e in s2.import_legacy(s2.load(), kp)] == ["new"]


def test_new_key_added_to_keys_json_later_is_imported(tmp_path):
    kp = write_keys(tmp_path, {"openai": {"api_key": "o"}})
    s = fake(tmp_path)
    s.save(s.import_legacy(s.load(), kp))
    write_keys(tmp_path, {"openai": {"api_key": "o"}, "glm": {"api_key": "g"}})
    s2 = fake(tmp_path)
    assert [e["preset"] for e in s2.import_legacy(s2.load(), kp)] == ["openai", "glm"]
