"""整理模型客户端链: 按模式顺序回退或两家竞速、单家硬超时、耗时统计、连接预热(spec §3.2)。

三家都走 OpenAI 兼容接口。永不打印或记录 api_key。
"""
import collections
import concurrent.futures as cf
import re
import threading
import time
from typing import Callable, Optional

from scribe import keys

PREWARM_IDLE_S = 60.0
KEEPALIVE_S = 120.0        # 空闲连接保留时长, 须长于预热间隔, 否则按下时预热的连接活不到松手
DEMOTE_S = 60.0            # 刚失败过的家 60 秒内排到最后(断网/被墙时不必每句都先等它超时)
BUDGET_ATTEMPTS = 2        # 整条链最多等 2 个单家超时, 断网时不会等满 3 家
LONG_CHARS = 100           # 单份识别超过 100 字后, 每多 100 字单家超时加 1 秒(长段整理本身就要更久)
_AB = re.compile(r"<A>(.*)</A>\n<B>(.*)</B>", re.S)


class LlmError(Exception):
    """没有可用的家, 或全部失败。"""


class LlmClient:
    def __init__(self, providers: dict, call: Optional[Callable] = None,
                 warm: Optional[Callable] = None, clock: Callable[[], float] = time.monotonic,
                 timeout: float = 2.5):
        self.providers = dict(providers)
        self.timeout = timeout
        self.clock = clock
        self._call = call or self._default_call
        self._warm = warm or self._default_warm
        self._lock = threading.Lock()
        self._stats = {n: collections.deque(maxlen=10) for n in self.providers}
        self._last = {}                    # 家名 → 最近一次发请求(含预热)的时刻
        self._failed_at = {}               # 家名 → 最近一次失败的时刻
        self._clients = {}
        # 落败或超时的请求留在池里自行结束(客户端自身也有超时), 不阻塞调用方
        self._pool = cf.ThreadPoolExecutor(max_workers=6, thread_name_prefix="scribe-llm")

    # ---- 对外
    def complete(self, messages: list, mode: str,
                 accept: Optional[Callable[[str], bool]] = None) -> tuple:
        """返回 (文字, 家名)。竞速时 accept 决定谁算赢(spec: 先到且通过防护);
        竞速里全都没通过 accept 时返回最先到的那份, 由调用方再做防护。"""
        first, rest = keys.order(mode)
        first = [n for n in first if n in self.providers]
        rest = [n for n in rest if n in self.providers]
        if not first and not rest:
            raise LlmError("没有可用的整理服务")
        timeout = self.timeout_for(messages)
        deadline = time.monotonic() + BUDGET_ATTEMPTS * timeout
        if len(first) >= 2:
            got = self._race(first, messages, accept, timeout)
            if got is not None:
                return got
        else:
            rest = first + rest
        errors = []
        for name in self._healthy_first(rest):
            left = deadline - time.monotonic()
            if left <= 0:
                errors.append("%s: 超出总时限" % name)
                break
            fut = self._pool.submit(self._attempt, name, messages, timeout)
            try:
                return fut.result(timeout=min(timeout, left)), name
            except Exception as e:          # 超时 / 报错 / 空回答 / 被截断 → 下一家
                self._mark_failed(name)
                errors.append("%s: %s" % (name, type(e).__name__))
        raise LlmError("; ".join(errors) or "全部失败")

    def timeout_for(self, messages: list) -> float:
        """单家超时: 2.5 秒;识别文字较长时按长度加时。"""
        content = messages[-1]["content"] if messages else ""
        m = _AB.search(content)
        n = max(len(m.group(1)), len(m.group(2))) if m else len(content)
        return self.timeout + max(0, n - LONG_CHARS) / LONG_CHARS

    def recent_ms(self, name: str) -> Optional[int]:
        with self._lock:
            d = self._stats.get(name)
            if not d:
                return None
            return int(round(sum(d) / len(d)))

    def prewarm(self, mode: str) -> None:
        """首选家(自动模式为两家)距上次请求 > 60 秒才预热;同步执行, 调用方放后台。"""
        first, rest = keys.order(mode)
        names = [n for n in first if n in self.providers] or [n for n in rest if n in self.providers][:1]
        now = self.clock()
        for name in names:
            with self._lock:
                last = self._last.get(name)
                if last is not None and now - last <= PREWARM_IDLE_S:
                    continue
                self._last[name] = now
            try:
                self._warm(self.providers[name])
            except Exception:
                pass

    # ---- 内部
    def _healthy_first(self, names: list) -> list:
        now = self.clock()
        with self._lock:
            recent = {n for n in names if n in self._failed_at and now - self._failed_at[n] < DEMOTE_S}
        return [n for n in names if n not in recent] + [n for n in names if n in recent]

    def _mark_failed(self, name: str) -> None:
        with self._lock:
            self._failed_at[name] = self.clock()

    def _race(self, names, messages, accept, timeout):
        futs = {self._pool.submit(self._attempt, n, messages, timeout): n for n in names}
        fallback = None
        try:
            for fut in cf.as_completed(futs, timeout=timeout):
                try:
                    text = fut.result()
                except Exception:
                    self._mark_failed(futs[fut])
                    continue
                if accept is None or accept(text):
                    return text, futs[fut]
                if fallback is None:
                    fallback = (text, futs[fut])
        except cf.TimeoutError:
            pass
        return fallback

    def _attempt(self, name: str, messages: list, timeout: float) -> str:
        conf = self.providers[name]
        t0 = self.clock()
        with self._lock:
            self._last[name] = t0
        text = (self._call(conf, messages, timeout) or "").strip()
        if not text:
            raise LlmError("%s: 空回答" % name)
        ms = (self.clock() - t0) * 1000
        with self._lock:
            self._stats[name].append(ms)
            self._failed_at.pop(name, None)
        return text

    def _client(self, conf):
        with self._lock:
            c = self._clients.get(conf.name)
            if c is None:
                import httpx
                import openai
                # httpx 默认空闲 5 秒就断开连接, 每句都要重新握手(实测多 0.3~0.6 秒);保持 120 秒
                http = openai.DefaultHttpxClient(limits=httpx.Limits(
                    max_connections=20, max_keepalive_connections=10, keepalive_expiry=KEEPALIVE_S))
                c = openai.OpenAI(base_url=conf.base_url, api_key=conf.api_key,
                                  timeout=self.timeout, max_retries=0, http_client=http)
                self._clients[conf.name] = c
            return c

    def _default_call(self, conf, messages, timeout) -> str:
        r = self._client(conf).chat.completions.create(model=conf.model, messages=messages,
                                                       timeout=timeout, **conf.params)
        choice = r.choices[0]
        if getattr(choice, "finish_reason", None) == "length":     # 被截断: 宁可回退原文也不丢后半句
            raise LlmError("%s: 回答被截断" % conf.name)
        return (choice.message.content or "").strip()

    def _default_warm(self, conf) -> None:
        try:
            self._client(conf).models.list()
        except Exception:
            pass
