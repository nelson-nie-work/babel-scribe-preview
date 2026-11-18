import hashlib

import pytest

from scribe import download, model_files


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


FILES = {"a.txt": b"aaaa", "b.txt": b"bbb"}


@pytest.fixture
def fake_model():
    return model_files.Model("fake", "org/repo", "rev1",
                             tuple((name, len(data), sha(data)) for name, data in FILES.items()))


@pytest.fixture(autouse=True)
def isolated_dirs(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hfhome"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return tmp_path


def _snapshot(root, files):
    snap = root / "models--org--repo" / "snapshots" / "abc123"
    snap.mkdir(parents=True)
    for name, data in files.items():
        (snap / name).write_bytes(data)
    return snap


@pytest.fixture
def legacy_cache(isolated_dirs):
    return _snapshot(isolated_dirs / "home" / ".cache" / "huggingface" / "hub", FILES)


@pytest.fixture
def legacy_cache_corrupt(isolated_dirs):
    return _snapshot(isolated_dirs / "home" / ".cache" / "huggingface" / "hub", {"a.txt": b"xxxx", "b.txt": b"bb"})


@pytest.fixture
def ready_dir(fake_model):
    d = model_files.model_dir(fake_model)
    d.mkdir(parents=True)
    for name, data in FILES.items():
        (d / name).write_bytes(data)
    return d


def test_sources_put_hf_endpoint_first(monkeypatch):
    monkeypatch.setenv("HF_ENDPOINT", "https://mirror.example/")
    assert model_files.sources() == ["https://mirror.example", "https://huggingface.co", "https://hf-mirror.com"]


def test_sources_without_endpoint(monkeypatch):
    monkeypatch.delenv("HF_ENDPOINT", raising=False)
    assert model_files.sources() == ["https://huggingface.co", "https://hf-mirror.com"]


def test_items_use_pinned_revision(monkeypatch):
    monkeypatch.delenv("HF_ENDPOINT", raising=False)
    item = model_files.items(model_files.SENSEVOICE)[1]
    assert item.urls[0].endswith("/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"
                                 "/resolve/2365baeacb507f821a0c8120fcee3d484dba7a07/tokens.txt")
    assert item.path == "tokens.txt"
    assert item.size == 315894
    assert len(item.urls) == 2


def test_whisper_manifest_matches_plan():
    names = [f[0] for f in model_files.WHISPER.files]
    assert names == ["config.json", "model.bin", "preprocessor_config.json", "tokenizer.json", "vocabulary.json"]
    assert sum(f[1] for f in model_files.WHISPER.files) == 2263 + 1617884929 + 340 + 2710337 + 1068114


def test_model_dir_is_under_model_cache(fake_model, isolated_dirs):
    assert model_files.model_dir(fake_model) == isolated_dirs / "local" / "BABEL Scribe" / "Model Cache" / "models" / "fake"


def test_matching_legacy_snapshot_is_adopted_without_download(fake_model, legacy_cache, monkeypatch):
    calls = []
    monkeypatch.setattr(download, "fetch",
                        lambda items, dest, **kw: calls.append([i for i in items if not download.present(i, dest)]))
    assert model_files.ensure(fake_model) == model_files.model_dir(fake_model)
    assert calls == [[]] and model_files.ready(fake_model)
    assert (model_files.model_dir(fake_model) / "a.txt").read_bytes() == b"aaaa"


def test_legacy_under_hf_home_and_installed_cache_are_searched(fake_model, isolated_dirs):
    _snapshot(isolated_dirs / "hfhome" / "hub", {"a.txt": b"aaaa"})
    _snapshot(isolated_dirs / "local" / "BABEL Scribe" / "Model Cache" / "hub", {"b.txt": b"bbb"})
    assert model_files.adopt_legacy(fake_model) == 2
    assert model_files.ready(fake_model)


def test_legacy_file_with_wrong_hash_is_not_adopted(fake_model, legacy_cache_corrupt):
    assert model_files.adopt_legacy(fake_model) == 0
    assert not model_files.ready(fake_model)


def test_already_present_files_are_not_adopted_again(fake_model, legacy_cache, ready_dir):
    assert model_files.adopt_legacy(fake_model) == 0


def test_missing_bytes_subtracts_present_and_partial(fake_model, isolated_dirs):
    d = model_files.model_dir(fake_model)
    d.mkdir(parents=True)
    assert model_files.missing_bytes(fake_model) == 7
    (d / "a.txt").write_bytes(b"aaaa")
    (d / "b.txt.part").write_bytes(b"b")
    assert model_files.missing_bytes(fake_model) == 2


def test_verify_deletes_corrupted_file(fake_model, ready_dir):
    (ready_dir / "a.txt").write_bytes(b"xxxx")      # 大小不变内容变
    assert model_files.verify(fake_model) is False
    assert not (ready_dir / "a.txt").exists()
    assert (ready_dir / "b.txt").exists()


def test_verify_all_good(fake_model, ready_dir):
    assert model_files.verify(fake_model) is True


def test_remove_deletes_directory(fake_model, ready_dir):
    model_files.remove(fake_model)
    assert not ready_dir.exists()
    model_files.remove(fake_model)      # 再删不报错
