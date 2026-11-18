"""多源、断点续传、SHA-256 核对的下载器，模型与 NVIDIA 运行库共用，与界面无关。"""
import hashlib
import logging
import os
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx

log = logging.getLogger(__name__)

_BLOCK = 1024 * 1024          # 每写满 1 MiB 检查一次取消
_REPORT_EVERY = 0.25          # 进度回调最短间隔(秒)
_RATE_WINDOW = 2.0            # 速度按最近约 2 秒计算


@dataclass(frozen=True)
class Item:
    path: str                 # 相对目标目录，正斜杠
    size: int
    sha256: str
    urls: tuple[str, ...]


@dataclass(frozen=True)
class Progress:
    done: int
    total: int
    rate: float               # 字节/秒
    source: str               # 当前 URL 的主机名


class Cancelled(Exception):
    pass


class DownloadFailed(Exception):
    def __init__(self):
        super().__init__("下载失败")


def present(item: Item, dest: Path) -> bool:
    target = Path(dest) / item.path
    return target.is_file() and target.stat().st_size == item.size


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(_BLOCK):
            digest.update(block)
    return digest.hexdigest()


class _Meter:
    def __init__(self, total: int, done: int, on_progress: Callable[[Progress], None]):
        self.total = total
        self.done = done
        self.source = ""
        self._on_progress = on_progress
        self._samples: deque[tuple[float, int]] = deque([(time.monotonic(), done)])
        self._last = 0.0

    def add(self, n: int) -> None:
        self.done += n
        self.report()

    def set_done(self, done: int) -> None:
        self.done = done
        self.report()

    def report(self, force: bool = False) -> None:
        now = time.monotonic()
        self._samples.append((now, self.done))
        while len(self._samples) > 2 and now - self._samples[0][0] > _RATE_WINDOW:
            self._samples.popleft()
        if not force and now - self._last < _REPORT_EVERY:
            return
        self._last = now
        t0, d0 = self._samples[0]
        rate = max(0.0, (self.done - d0) / (now - t0)) if now > t0 else 0.0
        self._on_progress(Progress(self.done, self.total, rate, self.source))


def _part_size(part: Path) -> int:
    return part.stat().st_size if part.is_file() else 0


def _download_one(item: Item, dest: Path, client: httpx.Client, meter: _Meter,
                  cancel: threading.Event | None) -> None:
    target = dest / item.path
    part = target.with_name(target.name + ".part")
    target.parent.mkdir(parents=True, exist_ok=True)
    base = meter.done - _part_size(part)      # 本文件之前已完成的字节
    if _part_size(part) > item.size:
        part.unlink()
        meter.set_done(base)
    last_error: Exception | None = None
    for url in item.urls:
        meter.source = urlsplit(url).hostname or ""
        have = _part_size(part)
        if have < item.size:
            headers = {"Accept-Encoding": "identity"}       # 字节偏移须与文件一致，不要压缩传输
            if have:
                headers["Range"] = f"bytes={have}-"
            try:
                with client.stream("GET", url, headers=headers) as response:
                    if response.status_code == 206 and have:
                        mode = "ab"
                    elif response.status_code == 200:
                        mode, have = "wb", 0
                        meter.set_done(base)
                    else:
                        last_error = httpx.HTTPStatusError(
                            f"HTTP {response.status_code}", request=response.request, response=response)
                        log.warning("下载源返回 %s：%s", response.status_code, url)
                        continue
                    with open(part, mode) as f:
                        since_check = 0
                        for chunk in response.iter_bytes():   # 不定长分块：断开时已收到的字节都落盘
                            f.write(chunk)
                            meter.add(len(chunk))
                            since_check += len(chunk)
                            if since_check >= _BLOCK:
                                since_check = 0
                                if cancel is not None and cancel.is_set():
                                    raise Cancelled()
                    if cancel is not None and cancel.is_set():
                        raise Cancelled()
            except Cancelled:
                raise
            except (httpx.HTTPError, OSError) as error:
                last_error = error
                log.warning("下载中断，换下一个源：%s（%s）", url, error)
                meter.set_done(base + _part_size(part))
                continue
        if _part_size(part) != item.size or sha256_file(part) != item.sha256:
            last_error = ValueError(f"内容校验不符：{item.path} 来自 {url}")
            log.warning("%s", last_error)
            part.unlink(missing_ok=True)
            meter.set_done(base)
            continue
        os.replace(part, target)
        meter.set_done(base + item.size)
        return
    raise DownloadFailed() from last_error


def fetch(items: Sequence[Item], dest: Path, *, on_progress: Callable[[Progress], None] = lambda p: None,
          cancel: threading.Event | None = None, client: httpx.Client | None = None,
          timeout: float = 15.0) -> None:
    dest = Path(dest)
    total = sum(i.size for i in items)
    done = 0
    for i in items:
        if present(i, dest):
            done += i.size
        else:
            target = dest / i.path
            done += min(_part_size(target.with_name(target.name + ".part")), i.size)
    meter = _Meter(total, done, on_progress)
    meter.report(force=True)
    own = client is None
    if own:
        client = httpx.Client(follow_redirects=True, timeout=timeout)
    try:
        for i in items:
            if not present(i, dest):
                _download_one(i, dest, client, meter, cancel)
    finally:
        if own:
            client.close()
    meter.report(force=True)
