"""整理模型客户端链: 按用户排好的服务列表依次回退、单家硬超时、耗时统计、连接预热、测试连接。

所有服务都走 OpenAI 兼容接口。永不打印或记录 api_key。
"""
import collections
import concurrent.futures as cf
import dataclasses
from dataclasses import dataclass
import re
import threading
import time
from typing import Callable, Optional

from scribe import fuse
from scribe.keys import ProviderConf

PREWARM_IDLE_S = 60.0
KEEPALIVE_S = 120.0        # 空闲连接保留时长, 须长于预热间隔, 否则按下时预热的连接活不到松手
DEMOTE_S = 60.0            # 刚失败过的家 60 秒内排到最后(断网/被墙时不必每句都先等它超时)
BUDGET_ATTEMPTS = 2        # 整条链最多等 2 个单家超时, 断网时不会等满 3 家
LONG_CHARS = 100           # 单份识别超过 100 字后, 每多 100 字单家超时加 1 秒(长段整理本身就要更久)
# 输出上限按识别长度放大: 中文约 1~1.5 token/字, 留 2 倍余量。固定 600 时约 2.5 分钟以上的听写会被截断。
# 放大后仍被截断, 说明回答远长于原文(跑题或在回答问题), 换下一家重试。
OUTPUT_TOKENS_PER_CHAR = 2
OUTPUT_TOKENS_EXTRA = 200
OUTPUT_TOKENS_MAX = 4096
_TOKEN_LIMIT_KEYS = ("max_completion_tokens", "max_tokens")
_AB = re.compile(r"<A>(.*)</A>\n<B>(.*)</B>", re.S)


TEST_TIMEOUT_S = 10.0
_TEST_TEXT = "嗯我想把 readme 更新一下然后提交到 github"


class LlmError(Exception):
    """没有可用的服务, 或全部失败。"""


@dataclass(frozen=True)
class ServiceStatus:
    kind: str                      # ok / auth / model / address / timeout / bad_reply / missing_key / missing_field
    ms: Optional[int] = None
    slow: bool = False             # 能连上, 但比听写时的单家时限还慢


def classify_error(exc: BaseException) -> str:
    """把一次失败归成用户能处理的几类原因。"""
    import openai
    if isinstance(exc, (openai.APITimeoutError, TimeoutError, cf.TimeoutError)):
        return "timeout"
    if isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError)):
        return "auth"
    if isinstance(exc, openai.NotFoundError):
        return "model" if "model" in str(exc).lower() else "address"
    if isinstance(exc, openai.APIStatusError):
        if exc.status_code in (401, 403):
            return "auth"
        if exc.status_code in (400, 422) and ("model" in str(exc).lower() or "模型" in str(exc)):
            return "model"          # DeepSeek、智谱等对未知模型返回 400
        return "address" if exc.status_code == 404 else "bad_reply"
    if isinstance(exc, (openai.APIConnectionError, ConnectionError, OSError)):
        return "address"
    return "bad_reply"


_TEST_PREFIX = "test:"


def _fingerprint(conf: ProviderConf) -> tuple:
    return (conf.base_url, conf.api_key, conf.model)


def _input_chars(messages: list) -> int:
    content = messages[-1]["content"] if messages else ""
    m = _AB.search(content)
    return max(len(m.group(1)), len(m.group(2))) if m else len(content)


class LlmClient:
    def __init__(self, providers: list, call: Optional[Callable] = None,
                 warm: Optional[Callable] = None, clock: Callable[[], float] = time.monotonic,
                 timeout: float = 2.5):
        self.timeout = timeout
        self.clock = clock
        self._call = call or self._default_call
        self._warm = warm or self._default_warm
        self._lock = threading.Lock()
        self.providers: list = []
        self._stats = collections.defaultdict(lambda: collections.deque(maxlen=10))
        self._status: dict = {}            # 服务 id → 最近一次测试或请求的结果
        self._last = {}                    # 服务 id → 最近一次发请求(含预热)的时刻
        self._failed_at = {}               # 服务 id → 最近一次失败的时刻
        self._clients = {}
        self.set_providers(providers)
        # 落败或超时的请求留在池里自行结束(客户端自身也有超时), 不阻塞调用方
        self._pool = cf.ThreadPoolExecutor(max_workers=6, thread_name_prefix="scribe-llm")

    # ---- 对外
    def set_providers(self, providers: list) -> None:
        """换成新的有序服务列表;已发出的请求用旧配置跑完。
        删掉的、或地址/密钥/模型改过的服务, 丢弃它的连接、失败记录和状态。"""
        providers = list(providers)
        with self._lock:
            old = {p.id: _fingerprint(p) for p in self.providers}
            keep = {p.id for p in providers if old.get(p.id) == _fingerprint(p)}
            self.providers = providers
            for table in (self._stats, self._last, self._failed_at, self._status):
                for key in [k for k in table if k not in keep]:
                    del table[key]
            for key in [k for k in self._clients if k not in keep]:
                self._close(self._clients.pop(key)[1])

    def complete(self, messages: list, accept: Optional[Callable[[str], bool]] = None) -> tuple:
        """返回 (文字, 服务 id)。按列表顺序依次尝试, 刚失败过的往后排。
        防护由调用方对返回的文字再做一次;accept 保留给调用方传入, 这里不竞速。"""
        with self._lock:
            confs = list(self.providers)
        if not confs:
            raise LlmError("没有可用的整理服务")
        by_id = {c.id: c for c in confs}
        timeout = self.timeout_for(messages)
        deadline = self.clock() + BUDGET_ATTEMPTS * timeout
        errors = []
        for sid in self._healthy_first([c.id for c in confs]):
            left = deadline - self.clock()
            if left <= 0:
                errors.append("%s: 超出总时限" % sid)
                break
            fut = self._pool.submit(self._attempt, by_id[sid], messages, timeout)
            try:
                return fut.result(timeout=min(timeout, left)), sid
            except Exception as e:          # 超时 / 报错 / 空回答 / 被截断 → 下一家
                self._mark_failed(sid, e)
                errors.append("%s: %s" % (sid, type(e).__name__))
        raise LlmError("; ".join(errors) or "全部失败")

    def label(self, sid: str) -> str:
        """服务 id → 显示名;找不到(已删除)时原样返回。"""
        with self._lock:
            return next((p.label or p.name for p in self.providers if p.id == sid), sid)

    def status(self, sid: str) -> Optional[ServiceStatus]:
        with self._lock:
            return self._status.get(sid)

    def record_status(self, sid: str, status: ServiceStatus) -> None:
        with self._lock:
            self._status[sid] = status

    def test(self, conf: ProviderConf, timeout: float = TEST_TIMEOUT_S) -> ServiceStatus:
        """用一句固定示例真实整理一次。用单独的连接, 不碰正式连接、失败记录和状态。
        首次偏慢(多是握手)时在同一连接上再测一次, 按热连接的耗时判断是否赶得上听写时限。"""
        messages = fuse.build_messages("", _TEST_TEXT, "", "natural")
        probe = dataclasses.replace(conf, id=_TEST_PREFIX + conf.id)
        try:
            ms = self._timed_call(probe, messages, timeout)
            if ms > self.timeout * 1000:
                ms = self._timed_call(probe, messages, timeout)
            return ServiceStatus("ok", ms, slow=ms > self.timeout * 1000)
        except Exception as e:
            return ServiceStatus(classify_error(e))
        finally:
            with self._lock:
                entry = self._clients.pop(probe.id, None)
            if entry is not None:
                self._close(entry[1])

    def _timed_call(self, conf: ProviderConf, messages: list, timeout: float) -> int:
        t0 = self.clock()
        text = (self._call(conf, messages, timeout) or "").strip()
        if not text:
            raise LlmError("空回答")
        return int(round((self.clock() - t0) * 1000))

    def timeout_for(self, messages: list) -> float:
        """单家超时: 2.5 秒;识别文字较长时按长度加时。"""
        return self.timeout + max(0, _input_chars(messages) - LONG_CHARS) / LONG_CHARS

    @staticmethod
    def output_limit(messages: list, base: int) -> int:
        """输出 token 上限: 不低于各家默认值, 随识别长度放大, 最多 4096。"""
        wanted = _input_chars(messages) * OUTPUT_TOKENS_PER_CHAR + OUTPUT_TOKENS_EXTRA
        return max(base, min(OUTPUT_TOKENS_MAX, wanted))

    def recent_ms(self, sid: str) -> Optional[int]:
        with self._lock:
            d = self._stats.get(sid)
            if not d:
                return None
            return int(round(sum(d) / len(d)))

    def prewarm(self) -> None:
        """第 1 个服务距上次请求 > 60 秒才预热;同步执行, 调用方放后台。"""
        with self._lock:
            if not self.providers:
                return
            first = self.providers[0]
            now = self.clock()
            last = self._last.get(first.id)
            if last is not None and now - last <= PREWARM_IDLE_S:
                return
            self._last[first.id] = now
        try:
            self._warm(first)
        except Exception:
            pass

    # ---- 内部
    def _healthy_first(self, names: list) -> list:
        now = self.clock()
        with self._lock:
            recent = {n for n in names if n in self._failed_at and now - self._failed_at[n] < DEMOTE_S}
        return [n for n in names if n not in recent] + [n for n in names if n in recent]

    def _mark_failed(self, sid: str, exc: BaseException) -> None:
        with self._lock:
            self._failed_at[sid] = self.clock()
            self._status[sid] = ServiceStatus(classify_error(exc))

    def _attempt(self, conf: ProviderConf, messages: list, timeout: float) -> str:
        name = conf.id
        limits = {k: self.output_limit(messages, v) for k, v in conf.params.items()
                  if k in _TOKEN_LIMIT_KEYS and isinstance(v, int)}
        if limits:
            conf = dataclasses.replace(conf, params={**conf.params, **limits})
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
            self._status[name] = ServiceStatus("ok", int(round(ms)))
        return text

    def _client(self, conf):
        """按服务 id 缓存连接;地址或密钥变了就换新连接。"""
        key = (conf.base_url, conf.api_key)
        stale = None
        with self._lock:
            cached = self._clients.get(conf.id)
            if cached is not None and cached[0] == key:
                return cached[1]
            import httpx
            import openai
            # httpx 默认空闲 5 秒就断开连接, 每句都要重新握手(实测多 0.3~0.6 秒);保持 120 秒
            http = openai.DefaultHttpxClient(limits=httpx.Limits(
                max_connections=20, max_keepalive_connections=10, keepalive_expiry=KEEPALIVE_S))
            c = openai.OpenAI(base_url=conf.base_url, api_key=conf.api_key,
                              timeout=self.timeout, max_retries=0, http_client=http)
            stale = cached
            self._clients[conf.id] = (key, c)
        if stale is not None:
            self._close(stale[1])
        return c

    @staticmethod
    def _close(client) -> None:
        try:
            client.close()
        except Exception:
            pass

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
