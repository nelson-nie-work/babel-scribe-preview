import time

import pytest

from scribe.keys import ProviderConf
from scribe.llm import LlmClient, LlmError

MSGS = [{"role": "user", "content": "x"}]


def provs(*names):
    return {n: ProviderConf(n, None, "k", "m-" + n, {}) for n in names}


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class FakeCall:
    """behaviors: 家名 → 返回的字符串 / 异常实例 / (sleep 秒, 字符串) / callable(clock)"""

    def __init__(self, behaviors, clock=None):
        self.behaviors = behaviors
        self.clock = clock
        self.calls = []
        self.timeouts = []

    def __call__(self, conf, messages, timeout):
        self.calls.append(conf.name)
        self.timeouts.append(timeout)
        b = self.behaviors[conf.name]
        if callable(b):
            return b(self.clock)
        if isinstance(b, BaseException):
            raise b
        if isinstance(b, tuple):
            time.sleep(b[0])
            return b[1]
        return b


ALL = ("openai", "deepseek", "glm")


def test_gpt_mode_uses_openai_first():
    call = FakeCall({"openai": "整理后", "deepseek": "D", "glm": "G"})
    c = LlmClient(provs(*ALL), call=call)
    assert c.complete(MSGS, "gpt") == ("整理后", "openai")
    assert call.calls == ["openai"]


def test_chain_falls_back_on_error():
    call = FakeCall({"openai": RuntimeError("500"), "deepseek": "D", "glm": "G"})
    assert LlmClient(provs(*ALL), call=call).complete(MSGS, "gpt") == ("D", "deepseek")


def test_chain_falls_back_on_timeout():
    call = FakeCall({"openai": TimeoutError(), "deepseek": "D", "glm": "G"})
    assert LlmClient(provs(*ALL), call=call).complete(MSGS, "gpt") == ("D", "deepseek")
    assert call.timeouts[0] == 2.5


def test_chain_falls_back_when_call_hangs_past_timeout():
    call = FakeCall({"openai": (1.0, "late"), "deepseek": "D", "glm": "G"})
    t = time.monotonic()
    assert LlmClient(provs(*ALL), call=call, timeout=0.1).complete(MSGS, "gpt") == ("D", "deepseek")
    assert time.monotonic() - t < 0.5


def test_empty_answer_counts_as_failure():
    call = FakeCall({"openai": "  ", "deepseek": "D", "glm": "G"})
    assert LlmClient(provs(*ALL), call=call).complete(MSGS, "gpt") == ("D", "deepseek")


def test_missing_provider_skipped():
    call = FakeCall({"deepseek": "D", "glm": "G"})
    assert LlmClient(provs("deepseek", "glm"), call=call).complete(MSGS, "gpt") == ("D", "deepseek")
    assert call.calls == ["deepseek"]


def test_all_fail_raises():
    call = FakeCall({n: RuntimeError(n) for n in ALL})
    with pytest.raises(LlmError):
        LlmClient(provs(*ALL), call=call).complete(MSGS, "gpt")


def test_no_providers_raises():
    with pytest.raises(LlmError):
        LlmClient({}, call=FakeCall({})).complete(MSGS, "auto")


def test_auto_mode_races_and_takes_first():
    call = FakeCall({"openai": (0.2, "O"), "deepseek": "D", "glm": "G"})
    assert LlmClient(provs(*ALL), call=call).complete(MSGS, "auto") == ("D", "deepseek")


def test_auto_mode_skips_racer_rejected_by_accept():
    call = FakeCall({"openai": (0.2, "O"), "deepseek": "编造", "glm": "G"})
    c = LlmClient(provs(*ALL), call=call)
    assert c.complete(MSGS, "auto", accept=lambda t: t != "编造") == ("O", "openai")


def test_auto_mode_both_fail_uses_glm():
    call = FakeCall({"openai": RuntimeError(), "deepseek": RuntimeError(), "glm": "G"})
    assert LlmClient(provs(*ALL), call=call).complete(MSGS, "auto") == ("G", "glm")


def test_recent_ms_average_of_last_ten():
    clock = Clock()
    durations = iter(range(100, 1300, 100))       # 100..1200 ms, 12 次

    def answer(clk):
        clk.t += next(durations) / 1000
        return "ok"

    c = LlmClient(provs("openai"), call=FakeCall({"openai": answer}, clock), clock=clock)
    assert c.recent_ms("openai") is None
    for _ in range(12):
        c.complete(MSGS, "gpt")
    assert c.recent_ms("openai") == sum(range(300, 1300, 100)) // 10


def test_prewarm_only_after_idle():
    clock = Clock()
    warmed = []
    c = LlmClient(provs(*ALL), call=FakeCall({"openai": "ok"}), warm=lambda conf: warmed.append(conf.name),
                  clock=clock)
    c.complete(MSGS, "gpt")
    c.prewarm("gpt")
    assert warmed == []
    clock.t += 61
    c.prewarm("gpt")
    assert warmed == ["openai"]
    c.prewarm("gpt")
    assert warmed == ["openai"]


def test_repr_never_shows_key():
    conf = ProviderConf("openai", None, "sk-secret", "gpt-5.4-mini", {})
    assert "sk-secret" not in repr(conf)


def long_msgs(n):
    return [{"role": "system", "content": "s"}, {"role": "user", "content": "<A>%s</A>\n<B>%s</B>" % ("字" * n, "字" * n)}]


def test_long_input_gets_longer_timeout():
    call = FakeCall({"openai": "ok"})
    LlmClient(provs("openai"), call=call).complete(long_msgs(300), "gpt")
    assert abs(call.timeouts[0] - 4.5) < 0.05


def test_chain_budget_caps_total_wait_when_all_hang():
    call = FakeCall({n: (1.0, "late") for n in ALL})
    t = time.monotonic()
    with pytest.raises(LlmError):
        LlmClient(provs(*ALL), call=call, timeout=0.1).complete(MSGS, "gpt")
    assert time.monotonic() - t < 0.35
    assert call.calls == ["openai", "deepseek"]


def test_fast_errors_still_reach_third_provider():
    call = FakeCall({"openai": RuntimeError(), "deepseek": RuntimeError(), "glm": "G"})
    assert LlmClient(provs(*ALL), call=call).complete(MSGS, "gpt") == ("G", "glm")


def test_recently_failed_provider_is_demoted():
    clock = Clock()
    state = {"openai_ok": False}

    def openai(clk):
        if not state["openai_ok"]:
            raise RuntimeError("down")
        return "O"

    call = FakeCall({"openai": openai, "deepseek": "D", "glm": "G"}, clock)
    c = LlmClient(provs(*ALL), call=call, clock=clock)
    assert c.complete(MSGS, "gpt") == ("D", "deepseek")
    assert c.complete(MSGS, "gpt") == ("D", "deepseek")
    assert call.calls == ["openai", "deepseek", "deepseek"]
    clock.t += 61
    state["openai_ok"] = True
    assert c.complete(MSGS, "gpt") == ("O", "openai")


class _Resp:
    def __init__(self, text, finish):
        msg = type("M", (), {"content": text})()
        self.choices = [type("C", (), {"message": msg, "finish_reason": finish})()]


class _FakeOpenAI:
    def __init__(self, resp):
        create = lambda **kw: resp   # noqa: E731
        self.chat = type("Chat", (), {"completions": type("Comp", (), {"create": staticmethod(create)})()})()


def test_truncated_answer_counts_as_failure():
    c = LlmClient(provs("openai", "deepseek"))
    c._clients["openai"] = _FakeOpenAI(_Resp("整理到一半", "length"))
    c._clients["deepseek"] = _FakeOpenAI(_Resp("完整的。", "stop"))
    assert c.complete(MSGS, "gpt") == ("完整的。", "deepseek")


def test_client_keeps_connections_alive_past_prewarm_window():
    """httpx 默认 5 秒就断空闲连接, 预热(60 秒)形同虚设;连接要能活过预热间隔。"""
    from scribe import llm as llm_mod

    c = LlmClient(provs("openai"))
    client = c._client(c.providers["openai"])
    pool = client._client._transport._pool
    assert pool._keepalive_expiry >= llm_mod.PREWARM_IDLE_S + 30
