"""Milestone 3a: metering proxy (real localhost upstream), host env hygiene, Harbor job config,
harness staging, compose overlay and the offline stateless repair invocation."""
from __future__ import annotations

import http.client
import json
import os
import stat
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from ssap.targets.alw import agent_wrapper as aw
from ssap.targets.alw import harbor_runner as hr

ROOT = Path(__file__).resolve().parents[1]
CFG = json.loads((ROOT / "configs" / "alw" / "config.server.json").read_text())
MODEL = "claude-test-model"
SPEC = "claude-test-model-spec"
TTFB = 0.30
STREAM_STEP = 0.10


# ------------------------------------------------------------------------------------------
# mock upstream
# ------------------------------------------------------------------------------------------

class Upstream:
    def __init__(self):
        self.seen: list[dict] = []
        self.fail_429 = 0
        up = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                return

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
                up.seen.append({"x-api-key": self.headers.get("x-api-key"),
                                "authorization": self.headers.get("authorization"),
                                "path": self.path, "accept-encoding": self.headers.get("accept-encoding")})
                if up.fail_429 > 0:
                    up.fail_429 -= 1
                    msg = b'{"type":"error"}'
                    self.send_response(429)
                    self.send_header("content-type", "application/json")
                    self.send_header("content-length", str(len(msg)))
                    self.end_headers()
                    self.wfile.write(msg)
                    return
                if body.get("mock_status"):
                    msg = b'{"type":"error"}'
                    self.send_response(int(body["mock_status"]))
                    self.send_header("content-length", str(len(msg)))
                    self.end_headers()
                    self.wfile.write(msg)
                    return
                model = body.get("mock_model") or body.get("model")
                time.sleep(TTFB)
                if body.get("stream"):
                    self.send_response(200)
                    self.send_header("content-type", "text/event-stream")
                    self.send_header("transfer-encoding", "chunked")
                    self.end_headers()
                    events = [
                        {"type": "message_start", "message": {"model": model, "usage": {
                            "input_tokens": 100, "cache_read_input_tokens": 40, "output_tokens": 1}}},
                        {"type": "content_block_delta", "delta": {"text": "hi"}},
                        {"type": "content_block_delta", "delta": {"text": " there"}},
                        {"type": "message_delta", "usage": {"output_tokens": 25}},
                    ]
                    for ev in events:
                        time.sleep(STREAM_STEP)
                        data = f"event: {ev['type']}\ndata: {json.dumps(ev)}\n\n".encode()
                        self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
                        self.wfile.flush()
                    self.wfile.write(b"0\r\n\r\n")
                    return
                msg = json.dumps({"model": model, "usage": {"input_tokens": 10, "output_tokens": 5,
                                                             "cache_read_input_tokens": 3}}).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(msg)))
                self.end_headers()
                self.wfile.write(msg)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}/api"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def upstream():
    u = Upstream()
    yield u
    u.close()


def call(proxy: hr.MeteringProxy, body: dict, headers: dict) -> tuple[int, bytes, str]:
    c = http.client.HTTPConnection("127.0.0.1", proxy.port, timeout=30)
    data = json.dumps(body).encode()
    c.request("POST", "/v1/messages", body=data, headers={"content-type": "application/json", **headers})
    r = c.getresponse()
    out = r.read()
    ctype = r.getheader("content-type") or ""
    c.close()
    return r.status, out, ctype


# ------------------------------------------------------------------------------------------
# proxy
# ------------------------------------------------------------------------------------------

def test_proxy_auth_x_api_key_bearer_and_401(upstream):
    with hr.MeteringProxy(upstream.url, ["upkey-zero"], model=MODEL) as px:
        tok = px.new_trial("t0")
        assert call(px, {"model": MODEL}, {"x-api-key": tok})[0] == 200
        assert call(px, {"model": MODEL}, {"authorization": f"Bearer {tok}"})[0] == 200
        assert call(px, {"model": MODEL}, {"x-api-key": "wrong"})[0] == 401
        assert call(px, {"model": MODEL}, {})[0] == 401
        px.revoke(tok)
        assert call(px, {"model": MODEL}, {"x-api-key": tok})[0] == 401
        s = px.stats("t0")
    assert s["proxy_requests"] == 2 and len(upstream.seen) == 2
    for seen in upstream.seen:                 # the trial token never reaches the upstream
        assert seen["x-api-key"] == "upkey-zero" and seen["authorization"] is None
        assert seen["path"] == "/api/v1/messages" and seen["accept-encoding"] == "identity"


def test_proxy_json_tokens_and_model(upstream):
    with hr.MeteringProxy(upstream.url, ["upkey-zero"], model=MODEL) as px:
        tok = px.new_trial("t0")
        status, body, _ = call(px, {"model": MODEL}, {"x-api-key": tok})
        call(px, {"model": MODEL}, {"x-api-key": tok})
        s = px.stats("t0")
    assert status == 200 and json.loads(body)["model"] == MODEL
    assert (s["input_tokens"], s["output_tokens"], s["cached_tokens"]) == (20, 10, 6)
    assert s["upstream_models_seen"] == [MODEL] and not s["model_swap"]


def test_proxy_sse_streaming_ttfb_vs_stream_seconds(upstream):
    with hr.MeteringProxy(upstream.url, ["upkey-zero"], model=MODEL) as px:
        tok = px.new_trial("t0")
        status, body, ctype = call(px, {"model": MODEL, "stream": True}, {"x-api-key": tok})
        s = px.stats("t0")
    assert status == 200 and "text/event-stream" in ctype
    assert body.count(b"event: ") == 4 and b"message_delta" in body       # transparent relay
    assert (s["input_tokens"], s["output_tokens"], s["cached_tokens"]) == (100, 25, 40)
    # gateway wait = TTFB only; the 4 x 0.1 s token stream is accounted separately
    assert TTFB * 0.8 <= s["gateway_wait_seconds"] < TTFB + 0.25, s
    assert s["llm_stream_seconds"] >= 4 * STREAM_STEP * 0.8, s
    assert s["gateway_wait_seconds"] < TTFB + 4 * STREAM_STEP * 0.5


def test_proxy_silent_model_swap_captured(upstream):
    with hr.MeteringProxy(upstream.url, ["upkey-zero"], model=MODEL) as px:
        tok = px.new_trial("t0")
        call(px, {"model": MODEL, "mock_model": "claude-other"}, {"x-api-key": tok})
        call(px, {"model": MODEL, "stream": True, "mock_model": "claude-other-sse"}, {"x-api-key": tok})
        s = px.stats("t0")
    assert s["upstream_models_seen"] == ["claude-other", "claude-other-sse"]
    assert s["request_models_seen"] == [MODEL] and s["model_swap"]


def test_proxy_429_retry_backoff_counts_as_gateway_wait(upstream):
    upstream.fail_429 = 1
    with hr.MeteringProxy(upstream.url, ["upkey-zero"], model=MODEL, retry_backoff_sec=0.2) as px:
        tok = px.new_trial("t0")
        assert call(px, {"model": MODEL}, {"x-api-key": tok})[0] == 200
        s = px.stats("t0")
    assert s["http_429_count"] == 1 and s["retries"] == 1 and not s["unrecovered_gateway_error"]
    assert s["gateway_wait_seconds"] >= 0.2 + TTFB * 0.8
    assert s["proxy_requests"] == 1 and len(upstream.seen) == 2


def test_proxy_unrecovered_5xx(upstream):
    with hr.MeteringProxy(upstream.url, ["upkey-zero"], model=MODEL, max_retries=1,
                          retry_backoff_sec=0.01) as px:
        tok = px.new_trial("t0")
        assert call(px, {"model": MODEL, "mock_status": 503}, {"x-api-key": tok})[0] == 503
        s = px.stats("t0")
    assert s["http_5xx_count"] == 2 and s["unrecovered_gateway_error"]
    assert "unrecovered_gateway_error" in hr.infra_reasons(s, wall_sec=100.0)


def test_proxy_unreachable_upstream_is_502_and_unrecovered():
    port = hr.free_port()
    with hr.MeteringProxy(f"http://127.0.0.1:{port}", ["upkey-zero"], model=MODEL, max_retries=1,
                          retry_backoff_sec=0.01) as px:
        tok = px.new_trial("t0")
        assert call(px, {"model": MODEL}, {"x-api-key": tok})[0] == 502
        assert px.stats("t0")["unrecovered_gateway_error"]


def test_sticky_multi_key_sharding_with_custom_upstream_header(upstream):
    keys = ["upkey-zero", "upkey-one"]
    with hr.MeteringProxy(upstream.url, keys, model=SPEC, upstream_auth_header="authorization",
                          upstream_auth_prefix="Bearer ") as px:
        toks = [px.new_trial(f"t{i}", trial_index=i) for i in range(3)]
        for _ in range(2):
            for t in toks:
                call(px, {"model": SPEC}, {"x-api-key": t})
        slots = [px.stats(f"t{i}")["key_slot"] for i in range(3)]
    assert slots == [0, 1, 0]
    got = [s["authorization"] for s in upstream.seen]
    assert got == ["Bearer upkey-zero", "Bearer upkey-one", "Bearer upkey-zero"] * 2
    assert all(s["x-api-key"] is None for s in upstream.seen)
    assert "upkey" not in json.dumps(px.ledger())          # key values never in stats


def test_multi_key_policy():
    with pytest.raises(ValueError, match="-spec"):
        hr.validate_key_policy(MODEL, ["a1234", "b1234"])
    hr.validate_key_policy(MODEL, ["a1234", "b1234"], multi_key_enabled=True)
    hr.validate_key_policy("claude-x-special", ["a1234", "b1234"])
    hr.validate_key_policy(MODEL, ["a1234"])
    with pytest.raises(ValueError, match="mix"):
        hr.validate_key_policy(SPEC, ["a1234"], paired_models=[MODEL])
    with pytest.raises(ValueError, match="mix"):
        hr.MeteringProxy("http://127.0.0.1:9", ["a1234"], model=MODEL, paired_models=["claude-y-special"])
    with pytest.raises(ValueError):
        hr.validate_key_policy(MODEL, [])
    env = {"SSAP_UPSTREAM_KEYS": "a1234, b1234", "SSAP_UPSTREAM_KEY": "c1234"}
    assert hr.upstream_keys_from_env(CFG, env) == ["a1234", "b1234"]
    assert hr.upstream_keys_from_env(CFG, {"SSAP_UPSTREAM_KEY": "c1234"}) == ["c1234"]
    with pytest.raises(ValueError, match="credentials"):
        hr.MeteringProxy("http://u:p" + "@127.0.0.1:9", ["a1234"], model=MODEL)


# ------------------------------------------------------------------------------------------
# env hygiene / job config
# ------------------------------------------------------------------------------------------

def test_build_host_env_allowlist_and_strip():
    env = {"PATH": "/usr/bin", "HOME": "/h", "ANTHROPIC_API_KEY": "x", "ANTHROPIC_BASE_URL": "y",
           "CLAUDE_CONFIG_DIR": "z", "AWS_REGION": "r", "MAX_THINKING_TOKENS": "1",
           "DISABLE_PROMPT_CACHING": "1", "SSAP_UPSTREAM_KEY": "k", "http_proxy": "p",
           "HTTPS_PROXY": "p", "ALL_PROXY": "p", "no_proxy": "n", "RANDOM": "1"}
    out = hr.build_host_env(CFG, {"SSAP_TRIAL_TOKEN": "tok"}, env=env)
    assert out == {"PATH": "/usr/bin", "HOME": "/h", "SSAP_TRIAL_TOKEN": "tok"}
    with pytest.raises(ValueError):
        hr.build_host_env(CFG, {"https_proxy": "p"}, env=env)
    with pytest.raises(ValueError):
        hr.build_host_env(CFG, {"ANTHROPIC_API_KEY": "p"}, env=env)


def _job(tmp_path, arm="H_base", **kw):
    harness = tmp_path / "harness"
    harness.mkdir(exist_ok=True)
    return hr.build_harbor_job_config(CFG, arm=arm, task_paths=["/data/task_a"], job_name="j",
                                      jobs_dir=tmp_path / "jobs", model=MODEL,
                                      harness_dir=None if arm == "H_raw" else harness, **kw)


def test_job_config_8_slot_homogeneity_and_fixed_env(tmp_path):
    job = _job(tmp_path, extra_instructions="note", trial_meta={"task_id": "x", "seed": 1})
    a = job["agents"][0]
    env = a["env"]
    assert set(aw.MODEL_SLOTS) <= set(env) and len(aw.MODEL_SLOTS) == 8
    assert all(env[s] == MODEL for s in aw.MODEL_SLOTS)
    assert env["DISABLE_NON_ESSENTIAL_MODEL_CALLS"] == "1"
    assert env["CLAUDE_CODE_TWO_STAGE_CLASSIFIER"] == "false" and env["API_TIMEOUT_MS"] == "3000000"
    assert env["NO_PROXY"] == env["no_proxy"] == aw.LOOPBACK_NO_PROXY
    assert env["ANTHROPIC_API_KEY"] == "${SSAP_TRIAL_TOKEN}"
    assert env["ANTHROPIC_BASE_URL"] == "http://host.docker.internal:8791"
    assert env["MCR_TASK_ID"] == "x" and env["MCR_SEED"] == "1"
    assert a["kwargs"]["version"] == "2.1.302" and "extra_instructions" not in a["kwargs"]
    assert job["extra_instructions"] == ["note"]
    assert job["n_concurrent_trials"] == 1 and "orchestrator" not in job
    assert job["verifier"]["env"]["ALB_VERIFIER_BUDGET_SEC"] == "3300"
    assert a["kwargs"]["config"].endswith("harness/settings.json")
    raw_job = _job(tmp_path, arm="H_raw")
    raw = raw_job["agents"][0]
    assert "config" not in raw["kwargs"] and "MCR_HARNESS_PYTHON" not in raw["env"]
    assert "extra_instructions" not in raw_job
    with pytest.raises(ValueError, match="fixed"):
        _job(tmp_path, agent_env_extra={"CLAUDE_CODE_SUBAGENT_MODEL": "claude-other"})
    with pytest.raises(ValueError):
        aw.assert_model_homogeneous({**env, "ANTHROPIC_SMALL_FAST_MODEL": "claude-haiku"}, MODEL)


@pytest.mark.parametrize("where,var", [("agent_env_extra", "http_proxy"), ("agent_env_extra", "HTTPS_PROXY"),
                                       ("environment_env", "all_proxy"), ("environment_env", "HTTP_PROXY")])
def test_option_b_proxy_in_harbor_env_is_rejected(tmp_path, where, var):
    with pytest.raises(ValueError, match="Option B"):
        _job(tmp_path, **{where: {var: "http://proxy.invalid:3128"}})


def test_persisted_job_config_is_scrubbed(tmp_path, monkeypatch):
    monkeypatch.setenv("SSAP_UPSTREAM_KEY", "literal-upstream-secret")
    job = _job(tmp_path, extra_instructions="see https://alice:pw" + "@gw.invalid/x and "
               "literal-upstream-secret and ephemeral-token-value")
    job["agents"][0]["env"]["ANTHROPIC_AUTH_TOKEN"] = "ephemeral-token-value"
    p = hr.persist_job_config(job, tmp_path / "config.json", secrets=["ephemeral-token-value"])
    text = p.read_text()
    assert "literal-upstream-secret" not in text and "ephemeral-token-value" not in text
    assert "alice:pw" not in text and "${SSAP_TRIAL_TOKEN}" in text


def test_apply_cli_overrides():
    out = hr.apply_cli_overrides(CFG, model=SPEC, parallel=6, t_warn=100, t_soft=200, delta_t_rep=50,
                                 theta_gw_max=0.2, agent_image="img:1", timeout_s=300)
    assert out["agent"]["model_name"] == SPEC and out["run"]["parallel"] == 6
    assert out["watermarks"]["t_max_sec"] == 300 and out["gateway"]["theta_gw_max"] == 0.2
    assert out["images"]["agent_image"] == "img:1" and CFG["images"]["agent_image"] != "img:1"
    with pytest.raises(ValueError):
        hr.apply_cli_overrides(CFG, parallel=7)
    with pytest.raises(ValueError):
        hr.apply_cli_overrides(CFG, t_warn=9990, t_soft=9900)


def test_infra_reasons():
    ok = {"proxy_requests": 3, "gateway_wait_seconds": 10.0}
    assert hr.infra_reasons(ok, wall_sec=100.0) == []
    assert hr.infra_reasons({**ok, "gateway_wait_seconds": 40.0}, wall_sec=100.0) == \
        ["gateway_wait_fraction_exceeded"]
    assert hr.infra_reasons({"proxy_requests": 0}, wall_sec=10.0, exit_code=137) == \
        ["oom_exit_137", "proxy_requests_zero"]


# ------------------------------------------------------------------------------------------
# harness staging / compose overlay / repair invocation
# ------------------------------------------------------------------------------------------

def test_stage_harness_bundle(tmp_path):
    dest = tmp_path / "opt_harness"
    man = aw.stage_harness_bundle(ROOT, dest, watermarks={"t_soft_sec": 100, "t_warn_sec": 50,
                                                          "t_max_sec": 200})
    assert (dest / "settings.json").is_file() and (dest / "bundle_manifest.json").is_file()
    for name in ("mcr_entrypoint.py", "mcr_hook.py", "mcr_common.py", "mcr_check", "mcr_promote"):
        assert stat.S_IMODE((dest / "bin" / name).stat().st_mode) == 0o755
    py = dest / "python" / "ssap"
    for rel in ("__init__.py", "config.py", "kernel/ratchet.py", "kernel/promote.py",
                "checkers/__init__.py", "checkers/alw/registry.py", "checkers/alw/repair.py"):
        assert (py / rel).is_file(), rel
    assert not (py / "targets").exists() and not (py / "bank").exists() and not (py / "cli.py").exists()
    assert not list(dest.rglob("__pycache__"))
    assert json.loads((dest / "mcr_config.json").read_text())["watermarks"]["t_soft_sec"] == 100
    assert "python/ssap/kernel/ratchet.py" in man["files"]
    probe = subprocess.run([sys.executable, "-I", "-c",
                            "import sys; sys.path.insert(0, sys.argv[1]); "
                            "import ssap.checkers.alw as a, ssap; print(ssap.__file__)", str(dest / "python")],
                           capture_output=True, text=True, timeout=60)
    assert probe.returncode == 0 and probe.stdout.strip().startswith(str(dest)), probe.stderr


def test_compose_overlay(tmp_path):
    p = aw.write_compose_overlay(tmp_path / "o.yaml", harness_dir=tmp_path / "h", agent_image="alw-eda-claude:2.1.302")
    t = p.read_text()
    assert '"host.docker.internal:host-gateway"' in t and "services:\n  main:\n" in t
    assert f'"{(tmp_path / "h").resolve()}:/opt/harness:ro"' in t
    assert 'ALW_BASE_IMAGE: "alw-eda-claude:2.1.302"' in t and "build:\n      args:" in t
    raw = aw.write_compose_overlay(tmp_path / "raw.yaml").read_text()
    assert "volumes" not in raw and "/opt/harness" not in raw and "build" not in raw
    assert "extra_hosts" in raw and "verifier" not in raw


def test_stateless_repair_invocation_from_archived_snapshot(tmp_path):
    pre = tmp_path / "pre_repair"
    mcr = pre / "workspace" / ".mcr"
    mcr.mkdir(parents=True)
    fork = {"schema_version": 1, "phase_a_elapsed_sec": 9900.0, "granted_repair_budget_sec": 780.0,
            "granted_repair_turns": 15,
            "phase_a_argv": ["--verbose", "--output-format=stream-json", "--resume", "abc",
                             "--max-turns", "99", "--print"]}
    gate = {"gate_triggered": True, "trigger_reasons": ["m_best_not_all_certified"],
            "granted_repair_budget_sec": 780.0, "granted_repair_turns": 15}
    (mcr / "fork_meta.json").write_text(json.dumps(fork))
    (mcr / "gate_report.json").write_text(json.dumps(gate))
    (mcr / "handoff.json").write_text(json.dumps({"schema_version": 1, "next_steps": ["fix drc"]}))
    inv = aw.build_stateless_repair_invocation(pre, mcr / "gate_report.json", model=MODEL,
                                               proxy_url="http://host.docker.internal:8791",
                                               trial_token="ephemeral-xyz")
    a = inv.argv
    assert a[0] == "/opt/claude-code/bin/claude"
    assert a[-4:] == ["-p", "--no-session-persistence", "--max-turns", "15"]
    assert "--resume" not in a and "abc" not in a and "99" not in a and "--print" not in a
    assert "--output-format=stream-json" in a
    assert inv.timeout_sec == 780.0 and inv.gate_triggered
    e = inv.env
    assert e["MCR_PHASE"] == "B" and e["CLAUDE_CONFIG_DIR"] == "/alb-workspace/.mcr/phase_b_sessions"
    assert "/opt/analog-layout/python/bin" in e["PATH"] and "/opt/analog-layout/tools/bin" in e["PATH"]
    assert "/opt/analog-layout/pdks/sky130/python" in e["PYTHONPATH"]
    df_agent = (ROOT / "container" / "alw" / "Dockerfile.agent").read_text()
    assert "/usr/local/bin/${tool}" in df_agent and "mcr_check mcr_promote" in df_agent
    assert "--network=host" in df_agent and "npm_config_strict_ssl=false" in df_agent
    assert "--strict-ssl=false" in df_agent and "NODE_USE_ENV_PROXY=1" not in df_agent
    assert "ENV http_proxy" not in df_agent and "ENV NODE_TLS_REJECT_UNAUTHORIZED" not in df_agent
    assert all(e[s] == MODEL for s in aw.MODEL_SLOTS) and e["ANTHROPIC_API_KEY"] == "ephemeral-xyz"
    assert "780 seconds" in inv.prompt and "fix drc" in inv.prompt and "mcr_promote" in inv.prompt
    d = inv.as_dict()
    assert "ephemeral-xyz" not in json.dumps(d) and d["granted_repair_budget_sec"] == 780.0
    # fallback: budget from gate_report when fork_meta is absent
    (mcr / "fork_meta.json").unlink()
    inv2 = aw.build_stateless_repair_invocation(pre, mcr / "gate_report.json", model=MODEL,
                                                proxy_url="http://127.0.0.1:1", trial_token="t",
                                                max_turns=7)
    assert inv2.timeout_sec == 780.0 and inv2.argv[-1] == "7" and "--model" in inv2.argv
    with pytest.raises(ValueError):
        aw.build_stateless_repair_invocation(pre, mcr / "gate_report.json", model="",
                                             proxy_url="http://127.0.0.1:1", trial_token="t")
