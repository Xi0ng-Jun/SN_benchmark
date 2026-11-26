"""Step 3: the model exit meters per credential, holds each key to its model, enforces caps."""
import http.client
import json
import socket
import threading
import time
import urllib.parse

import pytest
from stub_model import ANTHROPIC_COUNTED, OPENAI_COUNTED

from harness_builder import scenario as sc
from harness_builder.gateway import Gateway, GatewayError, expand_env
from harness_builder.runs import Runner, RunRefused

TOKENS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")


def call(gw, key, model="stub-solver", path="/v1/messages", stream=False, method="POST",
         auth="x-api-key", raw_body=None):
    u = urllib.parse.urlsplit(gw.url)
    conn = http.client.HTTPConnection(u.hostname, u.port, timeout=10)
    headers = {"content-type": "application/json"}
    if auth == "x-api-key":
        headers["x-api-key"] = key
    else:
        headers["authorization"] = "Bearer " + key
    body = None
    if method == "POST":
        body = raw_body if raw_body is not None else json.dumps(
            {"model": model, "stream": stream, "messages": [{"role": "user", "content": "x"}]})
    conn.request(method, path, body=body, headers=headers)
    r = conn.getresponse()
    data = r.read()
    conn.close()
    return r.status, data


def usage_of(gw, key):
    return gw.close(key)


# --- forwarding and metering ---------------------------------------------------------------

def test_forwards_with_the_upstream_key_only(make_gateway, stub_model):
    gw = make_gateway()
    key = gw.issue("run-1", "dev", "stub-solver")
    status, data = call(gw, key)
    assert status == 200 and json.loads(data)["content"][0]["text"] == "ok"
    got = stub_model.received[-1]
    assert got["headers"]["x-api-key"] == stub_model.key
    assert "authorization" not in got["headers"]
    assert got["body"]["model"] == "stub-solver"
    assert key not in json.dumps(got)


@pytest.mark.parametrize("path,stream,counted", [
    ("/v1/messages", False, ANTHROPIC_COUNTED),
    ("/v1/messages", True, ANTHROPIC_COUNTED),
    ("/v1/chat/completions", False, OPENAI_COUNTED),
    ("/v1/chat/completions", True, OPENAI_COUNTED),
], ids=["anthropic", "anthropic-sse", "openai", "openai-sse"])
def test_usage_is_counted_per_credential(make_gateway, path, stream, counted):
    gw = make_gateway()
    key = gw.issue("run-1", "dev", "stub-solver")
    other = gw.issue("run-2", "dev", "stub-solver")
    for _ in range(2):
        status, data = call(gw, key, path=path, stream=stream)
        assert status == 200
        if stream:
            assert b"data:" in data
    u = usage_of(gw, key)
    assert u["requests"] == 2 and u["upstream_errors"] == 0
    for k in TOKENS:
        assert u[k] == 2 * counted[k], k
    assert u["total_tokens"] == 2 * sum(counted.values())
    assert u["response_models"] == ["stub-solver"]
    assert u["owner"] == "run-1" and u["model"] == "stub-solver"
    assert usage_of(gw, other)["requests"] == 0
    assert gw.pool_usage("dev")["requests"] == 2


def test_bearer_auth_is_accepted(make_gateway):
    gw = make_gateway()
    key = gw.issue("run-1", "dev", "stub-solver")
    assert call(gw, key, auth="bearer")[0] == 200


def test_get_requests_pass_without_model(make_gateway):
    gw = make_gateway()
    key = gw.issue("run-1", "dev", "stub-solver")
    assert call(gw, key, method="GET", path="/v1/models")[0] == 200
    assert usage_of(gw, key)["requests"] == 1


def test_response_model_is_recorded(make_gateway, stub_model):
    stub_model.reply_model = "something-else"
    gw = make_gateway()
    key = gw.issue("run-1", "dev", "stub-solver")
    call(gw, key)
    assert usage_of(gw, key)["response_models"] == ["something-else"]


# --- refusals ------------------------------------------------------------------------------

@pytest.mark.parametrize("kw", [{"model": "bigger-model"}, {"raw_body": "{}"},
                                {"raw_body": "not json"}],
                         ids=["other model", "no model", "not json"])
def test_other_models_are_refused_and_not_forwarded(make_gateway, stub_model, kw):
    gw = make_gateway()
    key = gw.issue("run-1", "dev", "stub-solver")
    status, data = call(gw, key, **kw)
    assert status == 403 and b"not allowed" in data
    assert stub_model.received == []
    u = usage_of(gw, key)
    assert u["requests"] == 0 and u["refused"] == {"model": 1}


def test_unknown_and_revoked_keys_are_refused(make_gateway, stub_model):
    gw = make_gateway()
    assert call(gw, "hb-made-up")[0] == 401
    key = gw.issue("run-1", "dev", "stub-solver")
    assert call(gw, key)[0] == 200
    gw.close(key)
    assert call(gw, key)[0] == 401
    assert len(stub_model.received) == 1


def test_credential_cap(make_gateway, stub_model):
    gw = make_gateway()
    key = gw.issue("run-1", "dev", "stub-solver", caps={"requests": 2})
    assert [call(gw, key)[0] for _ in range(3)] == [200, 200, 403]
    assert len(stub_model.received) == 2
    u = usage_of(gw, key)
    assert u["requests"] == 2 and u["refused"] == {"budget": 1}


def test_pool_cap_stops_every_key_in_the_pool(make_gateway):
    gw = make_gateway()
    per_call = sum(ANTHROPIC_COUNTED.values())
    gw.set_pool_caps("dev", {"total_tokens": per_call + 1})
    a = gw.issue("dev-agent", "dev", "stub-developer")
    b = gw.issue("run-1", "dev", "stub-solver")
    c = gw.issue("exam-1", "exam", "stub-solver")
    assert call(gw, a, model="stub-developer")[0] == 200
    assert gw.pool_exceeded("dev") == []
    assert call(gw, b)[0] == 200
    assert gw.pool_exceeded("dev")
    assert call(gw, a, model="stub-developer")[0] == 403
    assert call(gw, b)[0] == 403
    assert call(gw, c)[0] == 200
    assert gw.pool_exceeded("exam") == []
    assert gw.pool_usage("dev")["total_tokens"] == 2 * per_call


def test_pool_usage_survives_a_restart(make_gateway, tmp_path):
    gw = make_gateway()
    key = gw.issue("run-1", "dev", "stub-solver")
    for _ in range(3):
        call(gw, key)
    before = gw.pool_usage("dev")
    gw.stop()
    again = make_gateway()
    assert again.pool_usage("dev") == {**before, "wait_seconds": 0.0, "seconds": 0.0,
                                       "response_models": []}
    again.set_pool_caps("dev", {"requests": 3})
    assert again.pool_exceeded("dev")
    lines = [json.loads(l) for l in (tmp_path / "requests.jsonl").read_text().splitlines()]
    assert len(lines) == 3 and all(l["owner"] == "run-1" and l["status"] == 200 for l in lines)


def _call_in_background(gw, key, stub_model):
    result = []
    t = threading.Thread(target=lambda: result.append(call(gw, key)[0]))
    t.start()
    deadline = time.monotonic() + 5
    while not stub_model.received and time.monotonic() < deadline:
        time.sleep(0.01)
    return t, result


def test_closing_a_key_waits_for_its_request_in_flight(make_gateway, stub_model):
    stub_model.delay = 1
    gw = make_gateway()
    key = gw.issue("run-1", "dev", "stub-solver")
    t, result = _call_in_background(gw, key, stub_model)
    u = gw.close(key)
    t.join()
    assert result == [200]
    assert u["requests"] == 1 and u["in_flight_at_close"] == 0
    assert u["input_tokens"] == ANTHROPIC_COUNTED["input_tokens"]


def test_closing_a_key_reports_requests_still_in_flight(make_gateway, stub_model):
    stub_model.delay = 2
    gw = make_gateway()
    key = gw.issue("run-1", "dev", "stub-solver")
    t, _ = _call_in_background(gw, key, stub_model)
    u = gw.close(key, wait=0.1)
    assert u["in_flight_at_close"] == 1 and u["requests"] == 0
    t.join()
    assert gw.wait_idle(timeout=5)
    assert gw.pool_usage("dev")["requests"] == 1  # counted in the pool once it finished


# --- upstream trouble ----------------------------------------------------------------------

def test_upstream_error_is_relayed_and_counted(make_gateway, stub_model):
    stub_model.fail_status = 500
    gw = make_gateway()
    key = gw.issue("run-1", "dev", "stub-solver")
    assert call(gw, key)[0] == 500
    assert usage_of(gw, key)["upstream_errors"] == 1


def test_unreachable_upstream_gives_502(tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    gw = Gateway(upstream_url=f"http://127.0.0.1:{port}", upstream_key="k").start()
    try:
        key = gw.issue("run-1", "dev", "stub-solver")
        assert call(gw, key)[0] == 502
        assert usage_of(gw, key)["upstream_errors"] == 1
    finally:
        gw.stop()


# --- configuration -------------------------------------------------------------------------

def test_gateway_from_scenario(stub_dir, stub_model):
    s = sc.load(stub_dir)
    with pytest.raises(GatewayError, match="STUB_UPSTREAM_KEY"):
        Gateway.from_scenario(s, env={})
    env = {"STUB_UPSTREAM_KEY": stub_model.key, "STUB_UPSTREAM_URL": stub_model.url}
    gw = Gateway.from_scenario(s, env=env).start()
    try:
        key = gw.issue("run-1", "dev", "stub-solver")
        assert call(gw, key)[0] == 200
    finally:
        gw.stop()


def test_expand_env():
    assert expand_env("${A}/x", {"A": "a"}) == "a/x"
    assert expand_env("${B:-http://h:1}", {}) == "http://h:1"
    with pytest.raises(GatewayError):
        expand_env("${C}", {})


def test_upstream_key_is_hidden_from_scenario_commands(stub_dir, tmp_path, monkeypatch):
    monkeypatch.setenv("STUB_UPSTREAM_KEY", "s3cret")
    s = sc.load(stub_dir)
    r = s.enter("p1", ["sh", "-c", 'echo "${STUB_UPSTREAM_KEY:-unset}"'], tmp_path / "w",
                tmp_path / "logs")
    assert r.stdout.read_text().strip() == "unset"


# --- runs through the gateway --------------------------------------------------------------

@pytest.fixture
def state(open_stub_state):
    return open_stub_state()


def agent_log(runner, record):
    return (runner.run_dir(record["run"]) / "out" / "agent.log").read_text()


def test_each_run_gets_its_own_metered_key(state, make_gateway, make_harness):
    gw = make_gateway()
    v = state.repo.save(make_harness("correct", model_calls=2))["version"]
    runner = Runner(state, "dev", state.scenario.practice, gateway=gw)
    r1, r2 = runner.run("p1", v), runner.run("p2", v)
    for r in (r1, r2):
        u = r["usage"]
        assert u["requests"] == 2 and u["model"] == "stub-solver"
        assert u["input_tokens"] == 2 * ANTHROPIC_COUNTED["input_tokens"]
        assert u["caps"] == {"requests": 5}
        assert json.loads((runner.run_dir(r["run"]) / "usage.json").read_text()) == u
        assert agent_log(runner, r).count("-> 200") == 2
        assert r["invalid"]["verdict"] == "valid"
    assert r1["usage"]["owner"] != r2["usage"]["owner"]
    assert state.ledger.last("run_finished")["data"]["usage"] == r2["usage"]
    assert gw.pool_usage("dev")["requests"] == 4
    assert all(not c.open for c in gw._creds.values())


def test_solver_asking_for_another_model_is_refused(state, make_gateway, make_harness):
    gw = make_gateway()
    v = state.repo.save(make_harness("correct", model_calls=2, model="bigger-model"))["version"]
    r = Runner(state, "dev", state.scenario.practice, gateway=gw).run("p1", v)
    assert r["usage"]["requests"] == 0 and r["usage"]["refused"] == {"model": 2}


def test_run_budget_caps_one_run(state, make_gateway, make_harness):
    gw = make_gateway()
    v = state.repo.save(make_harness("correct", model_calls=7))["version"]
    r = Runner(state, "dev", state.scenario.practice, gateway=gw).run("p1", v)
    assert r["usage"]["requests"] == 5 and r["usage"]["refused"] == {"budget": 2}


def test_dev_budget_refuses_calls_then_runs(state, make_gateway, make_harness):
    gw = make_gateway()
    gw.set_pool_caps("dev", {"requests": 3})
    v = state.repo.save(make_harness("correct", model_calls=2))["version"]
    runner = Runner(state, "dev", state.scenario.practice, gateway=gw)
    assert runner.run("p1", v)["usage"]["requests"] == 2
    second = runner.run("p2", v)["usage"]
    assert second["requests"] == 1 and second["refused"] == {"budget": 1}
    with pytest.raises(RunRefused, match="budget"):
        runner.run("p3", v)
    refused = state.ledger.last("run_refused")["data"]
    assert refused["task"] == "p3" and "requests 3 >= 3" in refused["reason"]
    assert len(state.ledger.entries("run_started")) == 2
