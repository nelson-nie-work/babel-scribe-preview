import hashlib
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from scribe.download import Cancelled, DownloadFailed, Item, fetch, present, sha256_file

DATA = os.urandom(3 * 1024 * 1024 + 123)
CHUNK = 64 * 1024


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _dead_url() -> str:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return f"http://127.0.0.1:{port}/blob.bin"


DEAD_URL = _dead_url()


class _Server:
    """本地下载源：按路径决定行为 ok / bad / half / norange / slow。"""

    def __init__(self):
        self.ranges: list[str | None] = []
        self.requests: list[str] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                mode = self.path.strip("/").split("/")[0]
                owner.requests.append(mode)
                header = self.headers.get("Range")
                owner.ranges.append(header)
                body = bytes(len(DATA)) if mode == "bad" else DATA
                start = 0
                if header and mode != "norange":
                    start = int(header.removeprefix("bytes=").rstrip("-"))
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {start}-{len(body) - 1}/{len(body)}")
                else:
                    self.send_response(200)
                payload = body[start:]
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                if mode == "half":
                    self.wfile.write(payload[: len(payload) // 2])
                    self.wfile.flush()
                    self.close_connection = True
                    return
                for i in range(0, len(payload), CHUNK):
                    try:
                        self.wfile.write(payload[i:i + CHUNK])
                    except OSError:
                        return
                    if mode == "slow":
                        time.sleep(0.02)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def url(self, mode: str) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}/{mode}/blob.bin"


@pytest.fixture
def server():
    s = _Server()
    yield s
    s.httpd.shutdown()
    s.httpd.server_close()


def item(*urls, path="blob.bin") -> Item:
    return Item(path, len(DATA), sha(DATA), tuple(urls))


def test_falls_back_when_first_source_refuses(server, tmp_path):
    fetch([item(DEAD_URL, server.url("ok"), path="a/blob.bin")], tmp_path)
    assert (tmp_path / "a/blob.bin").read_bytes() == DATA


def test_resumes_partial_file_with_range(server, tmp_path):
    (tmp_path / "blob.bin.part").write_bytes(DATA[:4096])
    fetch([item(server.url("ok"))], tmp_path)
    assert server.ranges == ["bytes=4096-"]
    assert (tmp_path / "blob.bin").read_bytes() == DATA
    assert not (tmp_path / "blob.bin.part").exists()


def test_hash_mismatch_discards_part_and_tries_next(server, tmp_path):
    fetch([item(server.url("bad"), server.url("ok"))], tmp_path)
    assert (tmp_path / "blob.bin").read_bytes() == DATA
    assert server.requests == ["bad", "ok"]
    assert server.ranges == [None, None]


def test_dropped_connection_keeps_part_then_next_source_resumes(server, tmp_path):
    fetch([item(server.url("half"), server.url("ok"))], tmp_path)
    assert (tmp_path / "blob.bin").read_bytes() == DATA
    assert server.ranges == [None, f"bytes={len(DATA) // 2}-"]


def test_server_ignoring_range_restarts_from_zero(server, tmp_path):
    (tmp_path / "blob.bin.part").write_bytes(DATA[:4096])
    fetch([item(server.url("norange"))], tmp_path)
    assert server.ranges == ["bytes=4096-"]
    assert (tmp_path / "blob.bin").read_bytes() == DATA


def test_oversized_part_is_discarded(server, tmp_path):
    (tmp_path / "blob.bin.part").write_bytes(DATA + b"extra")
    fetch([item(server.url("ok"))], tmp_path)
    assert server.ranges == [None]
    assert (tmp_path / "blob.bin").read_bytes() == DATA


def test_all_sources_fail_raises_and_keeps_part(server, tmp_path):
    with pytest.raises(DownloadFailed) as info:
        fetch([item(server.url("half"))], tmp_path)
    assert str(info.value) == "下载失败"
    assert info.value.__cause__ is not None
    assert (tmp_path / "blob.bin.part").stat().st_size > 0
    assert not (tmp_path / "blob.bin").exists()


def test_cancel_raises_and_keeps_part(server, tmp_path):
    cancel = threading.Event()
    with pytest.raises(Cancelled):
        fetch([item(server.url("slow"))], tmp_path, cancel=cancel, on_progress=lambda p: cancel.set())
    assert (tmp_path / "blob.bin.part").exists()
    assert not (tmp_path / "blob.bin").exists()


def test_present_file_is_not_requested(server, tmp_path):
    (tmp_path / "blob.bin").write_bytes(DATA)
    seen = []
    fetch([item(server.url("ok"))], tmp_path, on_progress=seen.append)
    assert server.requests == []
    assert seen[-1].done == seen[-1].total == len(DATA)


def test_progress_ends_complete_with_source_host(server, tmp_path):
    seen = []
    fetch([item(server.url("ok"))], tmp_path, on_progress=seen.append)
    assert seen[-1].done == seen[-1].total == len(DATA)
    assert seen[-1].source == "127.0.0.1"


def test_progress_total_counts_all_items(server, tmp_path):
    seen = []
    fetch([item(server.url("ok"), path="one.bin"), item(server.url("ok"), path="two.bin")], tmp_path,
          on_progress=seen.append)
    assert seen[-1].done == seen[-1].total == 2 * len(DATA)
    assert all(p.done <= p.total for p in seen)


def test_present_requires_matching_size(tmp_path):
    target = tmp_path / "blob.bin"
    assert not present(item("x"), tmp_path)
    target.write_bytes(DATA[:10])
    assert not present(item("x"), tmp_path)
    target.write_bytes(DATA)
    assert present(item("x"), tmp_path)


def test_sha256_file(tmp_path):
    (tmp_path / "f").write_bytes(DATA)
    assert sha256_file(tmp_path / "f") == sha(DATA)
