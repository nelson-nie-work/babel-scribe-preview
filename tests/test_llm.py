import concurrent.futures as cf
import threading
import time

import httpx
import openai
import pytest

from scribe.keys import ProviderConf
from scribe.llm import LlmClient, LlmError, ServiceStatus, classify_error

MSGS = [{"role": "user", "content": "x"}]


def conf(name, base_url=None, params=None):
    return ProviderConf(name, base_url, "k", "m-" + name, dict(params or {}), id=name, label=name)


def provs(*names):
    return [conf(n) for n in names]


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
        sid = conf.id.removeprefix("test:")      # 测试连接用带前缀的临时 id
        self.calls.append(sid)
        self.timeouts.append(timeout)
        b = self.behaviors[sid]
        if callable(b):
            return b(self.clock)
        if isinstance(b, BaseException):
            raise b
        if isinstance(b, tuple):
            time.sleep(b[0])
            return b[1]
        return b


ALL = ("openai", "deepseek", "glm")


def test_first_service_is_used_first():
    call = FakeCall({"openai": "整理后", "deepseek": "D", "glm": "G"})
    c = LlmClient(provs(*ALL), call=call)
    assert c.complete(MSGS) == ("整理后", "openai")
    assert call.calls == ["openai"]


def test_chain_falls_back_on_error():
    call = FakeCall({"openai": RuntimeError("500"), "deepseek": "D", "glm": "G"})
    assert LlmClient(provs(*ALL), call=call).complete(MSGS) == ("D", "deepseek")


def test_chain_falls_back_on_timeout():
    call = FakeCall({"openai": TimeoutError(), "deepseek": "D", "glm": "G"})
    assert LlmClient(provs(*ALL), call=call).complete(MSGS) == ("D", "deepseek")
    assert call.timeouts[0] == 2.5


def test_chain_falls_back_when_call_hangs_past_timeout():
    call = FakeCall({"openai": (1.0, "late"), "deepseek": "D", "glm": "G"})
    t = time.monotonic()
    assert LlmClient(provs(*ALL), call=call, timeout=0.1).complete(MSGS) == ("D", "deepseek")
    assert time.monotonic() - t < 0.5


def test_empty_answer_counts_as_failure():
    call = FakeCall({"openai": "  ", "deepseek": "D", "glm": "G"})
    assert LlmClient(provs(*ALL), call=call).complete(MSGS) == ("D", "deepseek")


def test_all_fail_raises():
    call = FakeCall({n: RuntimeError(n) for n in ALL})
    with pytest.raises(LlmError):
        LlmClient(provs(*ALL), call=call).complete(MSGS)


def test_no_providers_raises():
    with pytest.raises(LlmError):
        LlmClient([], call=FakeCall({})).complete(MSGS)


def test_recent_ms_average_of_last_ten():
    clock = Clock()
    durations = iter(range(100, 1300, 100))       # 100..1200 ms, 12 次

    def answer(clk):
        clk.t += next(durations) / 1000
        return "ok"

    c = LlmClient(provs("openai"), call=FakeCall({"openai": answer}, clock), clock=clock)
    assert c.recent_ms("openai") is None
    for _ in range(12):
        c.complete(MSGS)
    assert c.recent_ms("openai") == sum(range(300, 1300, 100)) // 10


def test_prewarm_only_after_idle():
    clock = Clock()
    warmed = []
    c = LlmClient(provs(*ALL), call=FakeCall({"openai": "ok"}), warm=lambda cf: warmed.append(cf.id),
                  clock=clock)
    c.complete(MSGS)
    c.prewarm()
    assert warmed == []
    clock.t += 61
    c.prewarm()
    assert warmed == ["openai"]
    c.prewarm()
    assert warmed == ["openai"]


def test_repr_never_shows_key():
    secret = ProviderConf("openai", None, "sk-secret", "gpt-5.4-mini", {})
    assert "sk-secret" not in repr(secret)


def long_msgs(n):
    return [{"role": "system", "content": "s"}, {"role": "user", "content": "<A>%s</A>\n<B>%s</B>" % ("字" * n, "字" * n)}]


def test_long_input_gets_longer_timeout():
    call = FakeCall({"openai": "ok"})
    LlmClient(provs("openai"), call=call).complete(long_msgs(300))
    assert abs(call.timeouts[0] - 4.5) < 0.05


def test_chain_budget_caps_total_wait_when_all_hang():
    call = FakeCall({n: (1.0, "late") for n in ALL})
    t = time.monotonic()
    with pytest.raises(LlmError):
        LlmClient(provs(*ALL), call=call, timeout=0.1).complete(MSGS)
    assert time.monotonic() - t < 0.35
    assert call.calls == ["openai", "deepseek"]


def test_fast_errors_still_reach_third_provider():
    call = FakeCall({"openai": RuntimeError(), "deepseek": RuntimeError(), "glm": "G"})
    assert LlmClient(provs(*ALL), call=call).complete(MSGS) == ("G", "glm")


def test_recently_failed_provider_is_demoted():
    clock = Clock()
    state = {"openai_ok": False}

    def openai(clk):
        if not state["openai_ok"]:
            raise RuntimeError("down")
        return "O"

    call = FakeCall({"openai": openai, "deepseek": "D", "glm": "G"}, clock)
    c = LlmClient(provs(*ALL), call=call, clock=clock)
    assert c.complete(MSGS) == ("D", "deepseek")
    assert c.complete(MSGS) == ("D", "deepseek")
    assert call.calls == ["openai", "deepseek", "deepseek"]
    clock.t += 61
    state["openai_ok"] = True
    assert c.complete(MSGS) == ("O", "openai")


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
    c._clients["openai"] = ((None, "k"), _FakeOpenAI(_Resp("整理到一半", "length")))
    c._clients["deepseek"] = ((None, "k"), _FakeOpenAI(_Resp("完整的。", "stop")))
    assert c.complete(MSGS) == ("完整的。", "deepseek")


def test_client_keeps_connections_alive_past_prewarm_window():
    """httpx 默认 5 秒就断空闲连接, 预热(60 秒)形同虚设;连接要能活过预热间隔。"""
    from scribe import llm as llm_mod

    c = LlmClient(provs("openai"))
    client = c._client(c.providers[0])
    pool = client._client._transport._pool
    assert pool._keepalive_expiry >= llm_mod.PREWARM_IDLE_S + 30


def test_output_limit_grows_with_long_dictation():
    assert LlmClient.output_limit(long_msgs(50), 600) == 600
    assert LlmClient.output_limit(long_msgs(800), 600) == 1800
    assert LlmClient.output_limit(long_msgs(5000), 600) == 4096


def test_attempt_sends_scaled_token_limit_without_touching_provider_defaults():
    seen = []
    base = conf("openai", params={"max_completion_tokens": 600, "reasoning_effort": "none"})
    c = LlmClient([base], call=lambda cf, msgs, timeout: seen.append(cf.params) or "ok")
    c.complete(long_msgs(800))
    assert seen[0] == {"max_completion_tokens": 1800, "reasoning_effort": "none"}
    assert base.params["max_completion_tokens"] == 600


def test_deadline_uses_injected_clock():
    clock = Clock()

    def slow_fail(_clock):
        clock.t += 10                   # 第一家耗尽总时限
        raise RuntimeError("500")

    call = FakeCall({"openai": slow_fail, "deepseek": "D"}, clock)
    with pytest.raises(LlmError, match="超出总时限"):
        LlmClient(provs("openai", "deepseek"), call=call, clock=clock).complete(MSGS)
    assert call.calls == ["openai"]


REQ = httpx.Request("POST", "http://localhost/v1/chat/completions")


def openai_error(name, status, message=""):
    response = httpx.Response(status, request=REQ, json={"error": {"message": message}})
    return getattr(openai, name)(message or name, response=response, body={"error": {"message": message}})


def test_falls_back_in_list_order_and_records_status():
    call = FakeCall({"a": RuntimeError("down"), "b": "好", "c": "C"})
    c = LlmClient(provs("a", "b", "c"), call=call)
    assert c.complete(MSGS) == ("好", "b") and call.calls == ["a", "b"]
    assert c.status("a").kind == "bad_reply" and c.status("b").kind == "ok"
    assert c.status("c") is None


def test_list_order_not_vendor_order():
    call = FakeCall({"glm": "G", "openai": "O"})
    assert LlmClient(provs("glm", "openai"), call=call).complete(MSGS) == ("G", "glm")


def test_set_providers_does_not_break_inflight_request():
    gate = threading.Event()

    def slow(_clock):
        gate.wait(1)
        return "旧"

    c = LlmClient([conf("a")], call=FakeCall({"a": slow, "b": "新"}))
    fut = cf.ThreadPoolExecutor(1).submit(c.complete, MSGS)
    time.sleep(0.05)
    c.set_providers([conf("b")])
    gate.set()
    assert fut.result(2) == ("旧", "a")
    assert c.complete(MSGS) == ("新", "b")


@pytest.mark.parametrize(("exc", "kind"), [
    (openai_error("AuthenticationError", 401), "auth"),
    (openai_error("PermissionDeniedError", 403), "auth"),
    (openai_error("NotFoundError", 404, "The model `x` does not exist"), "model"),
    (openai_error("NotFoundError", 404, "Not Found"), "address"),
    (openai.APIConnectionError(request=REQ), "address"),
    (openai.APITimeoutError(request=REQ), "timeout"),
    (TimeoutError(), "timeout"),
    (cf.TimeoutError(), "timeout"),
    (LlmError("空回答"), "bad_reply")])
def test_classify_error(exc, kind):
    assert classify_error(exc) == kind


def test_connection_test_success_reports_time():
    clock = Clock()

    def answer(clk):
        clk.t += 0.9
        return "整理后的句子。"

    c = LlmClient([], call=FakeCall({"x": answer}, clock), clock=clock)
    assert c.test(conf("x")) == ServiceStatus("ok", 900)
    assert c.status("x") is None             # 测试结果由调用方决定是否记为状态


def test_connection_test_reports_address_when_server_absent():
    status = LlmClient([]).test(conf("o", base_url="http://127.0.0.1:9/v1"), timeout=5)   # Windows 连本机关闭端口约 2 秒才报拒绝
    assert status.kind == "address"


def test_label_maps_id_to_display_name():
    c = LlmClient([ProviderConf("openai", None, "k", "m", {}, id="x1", label="OpenAI")])
    assert c.label("x1") == "OpenAI" and c.label("gone") == "gone"


def keyed(sid, key, url="https://a.example/v1"):
    return ProviderConf("custom", url, key, "m", {}, id=sid, label=sid)


def test_edited_key_rebuilds_the_cached_client():
    c = LlmClient([keyed("a", "OLDKEY-1111", "https://old.example/v1")])
    old = c._client(c.providers[0])
    c.set_providers([keyed("a", "NEWKEY-2222", "https://new.example/v1")])
    new = c._client(c.providers[0])
    assert new is not old and new.api_key == "NEWKEY-2222" and "new.example" in str(new.base_url)


def test_edited_service_forgets_old_failure_and_status():
    clock = Clock()
    call = FakeCall({"a": RuntimeError("bad key")}, clock)
    c = LlmClient([keyed("a", "old")], call=call, clock=clock)
    with pytest.raises(LlmError):
        c.complete(MSGS)
    assert c.status("a") is not None
    c.set_providers([keyed("a", "new")])
    assert c.status("a") is None and "a" not in c._failed_at


def test_connection_test_leaves_live_client_and_status_alone():
    c = LlmClient([keyed("a", "SAVED-0000")])
    live = c._client(c.providers[0])
    c.test(keyed("a", "UNSAVED-9999", "http://127.0.0.1:9/v1"), timeout=5)
    assert c._client(c.providers[0]) is live and live.api_key == "SAVED-0000"
    assert c.status("a") is None
    assert set(c._clients) == {"a"}


def test_second_connection_test_uses_new_values():
    seen = []
    c = LlmClient([], call=lambda conf, msgs, timeout: seen.append(conf.api_key) or "好")
    c.test(keyed("x", "typo"))
    c.test(keyed("x", "fixed"))
    assert seen == ["typo", "fixed"]


def test_slow_first_test_is_measured_again_on_a_warm_connection():
    clock = Clock()
    durations = iter([3.6, 0.7])

    def answer(clk):
        clk.t += next(durations)
        return "好"

    c = LlmClient([], call=FakeCall({"x": answer}, clock), clock=clock)
    assert c.test(keyed("x", "k")) == ServiceStatus("ok", 700, slow=False)


def test_test_flags_service_slower_than_dictation_timeout():
    clock = Clock()

    def answer(clk):
        clk.t += 3.0
        return "好"

    c = LlmClient([], call=FakeCall({"x": answer}, clock), clock=clock)
    result = c.test(keyed("x", "k"))
    assert result.kind == "ok" and result.slow is True


def test_record_status_sets_badge():
    c = LlmClient([keyed("a", "k")])
    c.record_status("a", ServiceStatus("auth"))
    assert c.status("a") == ServiceStatus("auth")


@pytest.mark.parametrize(("exc", "kind"), [
    (openai_error("BadRequestError", 400, "Model Not Exist"), "model"),
    (openai_error("BadRequestError", 400, "模型不存在，请检查模型代码。"), "model"),
    (openai_error("UnprocessableEntityError", 422, "unknown model: foo"), "model"),
    (openai_error("BadRequestError", 400, "temperature out of range"), "bad_reply")])
def test_classify_bad_request_mentioning_model(exc, kind):
    assert classify_error(exc) == kind
