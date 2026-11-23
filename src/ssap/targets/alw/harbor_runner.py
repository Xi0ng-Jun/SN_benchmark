"""Harbor job configuration, host-environment hygiene and the zero-cache metering proxy.

Metering proxy (``MeteringProxy``): a pass-through reverse proxy on the host. The agent container
talks to it with an ephemeral per-trial token (``x-api-key`` as Harbor's ``ANTHROPIC_API_KEY`` or
``Authorization: Bearer`` as ``ANTHROPIC_AUTH_TOKEN``); the proxy replaces it with the real
upstream key, which never enters a container. No request or response is cached, stored or
replayed. Per trial it counts requests, tokens, the upstream ``"model"`` of every response (a
silent model swap shows up in ``upstream_models_seen``), HTTP 429 / 5xx and

* ``gateway_wait_seconds`` = request sent -> first upstream response byte (TTFB), plus the
  backoff slept before 429 / 5xx / connection-error retries;
* ``llm_stream_seconds`` = first response byte -> end of the body (token streaming), kept apart.

Multi-key: ``SSAP_UPSTREAM_KEYS`` is accepted only for ``-spec`` / ``-special`` model ids unless
``gateway.multi_key_enabled`` is ``true``; paired arms never mix regular and ``-spec`` /
``-special`` ids. A trial keeps one key for its whole life (``trial_index % len(keys)``).

Harbor job config (``build_harbor_job_config``): one Harbor job per trial, so a per-trial token
maps to exactly one trial. Field names follow the Harbor 0.24 job-config layout as recorded in
``docs/alw-progressive-plan.md``; ``override_timeout_sec`` and ``kwargs.config`` are provisional
until checked against the installed Harbor on the server.
"""
from __future__ import annotations

import copy
import http.client
import json
import math
import os
import re
import secrets as _secrets
import socket
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterable, Mapping

from ...kernel.artifact_summary import REDACTED, literal_secrets_from_env, scrub
from ...kernel.watermarks import Watermarks
from .agent_wrapper import (HARNESS_MOUNT, LOOPBACK_NO_PROXY, assert_model_homogeneous,
                            model_env)

ARMS = ("H_raw", "H_base", "H_star")
TOKEN_ENV = "SSAP_TRIAL_TOKEN"
TOKEN_REF = "${" + TOKEN_ENV + "}"
CLAUDE_CODE_VERSION = "2.1.302"
DEFAULT_ALLOW = ("PATH", "HOME", "USER", "LANG", "LC_ALL", "TERM", "TMPDIR", "DOCKER_HOST",
                 "DOCKER_CONFIG", "VIRTUAL_ENV")
DEFAULT_STRIP_PREFIXES = ("ANTHROPIC_", "CLAUDE_", "AWS_", "SSAP_UPSTREAM_")
DEFAULT_STRIP_NAMES = ("MAX_THINKING_TOKENS", "DISABLE_PROMPT_CACHING")
PROXY_VAR = re.compile(r"(?i)^.*_proxy$")
FORBIDDEN_HARBOR_ENV = ("http_proxy", "https_proxy", "all_proxy")
CRED_URL = re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]*://[^\s/@]+@")
HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te",
              "trailers", "transfer-encoding", "upgrade", "host", "content-length",
              "x-api-key", "authorization", "accept-encoding"}
RETRY_STATUSES = {429, 500, 502, 503, 504, 529}


# ---------------------------------------------------------------------------------------------
# config overrides
# ---------------------------------------------------------------------------------------------

def apply_cli_overrides(cfg: Mapping, *, model: str | None = None, parallel: int | None = None,
                        t_warn: float | None = None, t_soft: float | None = None,
                        delta_t_rep: float | None = None, theta_gw_max: float | None = None,
                        agent_image: str | None = None, timeout_s: float | None = None) -> dict:
    """Deep copy of *cfg* with command-line overrides applied and validated."""
    out = copy.deepcopy(dict(cfg))
    if model:
        out.setdefault("agent", {})["model_name"] = model
        out.setdefault("model_homogeneity", {})["pin_to"] = model
    run = out.setdefault("run", {})
    if parallel is not None:
        cap = int(run.get("parallel_max", parallel))
        if not 1 <= int(parallel) <= cap:
            raise ValueError(f"parallel must be in [1, {cap}]")
        run["parallel"] = int(parallel)
    wm = out.setdefault("watermarks", {})
    for key, val in (("t_warn_sec", t_warn), ("t_soft_sec", t_soft),
                     ("delta_t_rep_sec", delta_t_rep), ("t_max_sec", timeout_s)):
        if val is not None:
            wm[key] = float(val)
    Watermarks.from_mapping(wm)                    # ordering T_warn <= T_soft <= T_max
    if theta_gw_max is not None:
        if not 0.0 < float(theta_gw_max) <= 1.0:
            raise ValueError("theta_gw_max must be in (0, 1]")
        out.setdefault("gateway", {})["theta_gw_max"] = float(theta_gw_max)
    if agent_image is not None:
        out.setdefault("images", {})["agent_image"] = agent_image
    if timeout_s is not None:
        run["agent_timeout_sec"] = float(timeout_s)
    return out


# ---------------------------------------------------------------------------------------------
# host environment
# ---------------------------------------------------------------------------------------------

def _policy(cfg: Mapping | None) -> dict:
    return dict((cfg or {}).get("host_env_policy") or {})


def _stripped(name: str, pol: dict) -> bool:
    if any(name.startswith(p) for p in pol.get("strip_prefixes", DEFAULT_STRIP_PREFIXES)):
        return True
    if name in pol.get("strip_names", DEFAULT_STRIP_NAMES):
        return True
    pats = pol.get("strip_patterns") or [PROXY_VAR.pattern]
    return PROXY_VAR.match(name) is not None or any(re.match(p, name) for p in pats)


def build_host_env(cfg: Mapping | None, extra: Mapping[str, str] | None = None, *,
                   env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Allowlisted host environment for Harbor / docker subprocesses.

    Only ``host_env_policy.allow`` names survive; ``ANTHROPIC_*``, ``CLAUDE_*``, ``AWS_*``,
    ``SSAP_UPSTREAM_*``, ``MAX_THINKING_TOKENS``, ``DISABLE_PROMPT_CACHING`` and every
    ``*_proxy`` / ``*_PROXY`` are dropped even if allowlisted. *extra* (e.g. ``SSAP_TRIAL_TOKEN``)
    is subject to the same strip rules; a proxy variable in *extra* is an error."""
    pol = _policy(cfg)
    src = os.environ if env is None else env
    allow = set(pol.get("allow", DEFAULT_ALLOW))
    out = {k: v for k, v in src.items() if k in allow and not _stripped(k, pol)}
    for k, v in (extra or {}).items():
        if PROXY_VAR.match(k):
            raise ValueError(f"{k}: proxy variables never enter the run environment")
        if _stripped(k, pol):
            raise ValueError(f"{k}: stripped by host_env_policy; pass it explicitly elsewhere")
        out[k] = str(v)
    return out


def check_no_forbidden_proxy(env: Mapping[str, str] | None, where: str) -> None:
    for k, v in (env or {}).items():
        if k.lower() in FORBIDDEN_HARBOR_ENV or (PROXY_VAR.match(k) and k.lower() != "no_proxy"):
            raise ValueError(f"{where}: {k} must not be passed into Harbor (Option B prohibited)")
        if isinstance(v, str) and CRED_URL.search(v):
            raise ValueError(f"{where}: {k} carries a credential URL")


# ---------------------------------------------------------------------------------------------
# Harbor job config
# ---------------------------------------------------------------------------------------------

def build_harbor_job_config(cfg: Mapping, *, arm: str, task_paths: Iterable[str | Path],
                            job_name: str, jobs_dir: str | Path, model: str | None = None,
                            harness_dir: str | Path | None = None,
                            compose_overlays: Iterable[str | Path] = (),
                            extra_instructions: str | list[str] | tuple[str, ...] | None = None,
                            trial_meta: Mapping[str, str] | None = None,
                            agent_env_extra: Mapping[str, str] | None = None,
                            environment_env: Mapping[str, str] | None = None,
                            n_attempts: int = 1, timeout_s: float | None = None) -> dict:
    """Harbor job config for one trial. The token is referenced as ``${SSAP_TRIAL_TOKEN}`` (host
    environment of the Harbor process), never inlined."""
    if arm not in ARMS:
        raise ValueError(f"arm must be one of {ARMS}")
    agent_cfg = dict(cfg.get("agent") or {})
    model = model or agent_cfg.get("model_name") or (cfg.get("model_homogeneity") or {}).get("pin_to")
    if not model:
        raise ValueError("no model id (agent.model_name / SSAP_MODEL)")
    check_no_forbidden_proxy(agent_env_extra, "agents[].env")
    check_no_forbidden_proxy(environment_env, "environment.env")
    gw = cfg.get("gateway") or {}
    advertise = gw.get("proxy_advertise") or f"host.docker.internal:{gw.get('proxy_port', 8791)}"
    env = model_env(model, cfg)
    env.update({"ANTHROPIC_BASE_URL": f"http://{advertise}", "ANTHROPIC_API_KEY": TOKEN_REF,
                "NO_PROXY": LOOPBACK_NO_PROXY, "no_proxy": LOOPBACK_NO_PROXY})
    for k, v in (cfg.get("container_env") or {}).items():
        env.setdefault(k, str(v))
    if arm != "H_raw":
        env["MCR_HARNESS_PYTHON"] = f"{HARNESS_MOUNT}/python"
        for k, v in (trial_meta or {}).items():
            env[f"MCR_{k.upper()}"] = str(v)
    for k, v in (agent_env_extra or {}).items():
        if k in env and k.startswith(("ANTHROPIC_", "CLAUDE_CODE_")):
            raise ValueError(f"{k}: model / gateway slots are fixed by the runner")
        env[k] = str(v)
    check_no_forbidden_proxy(env, "agents[].env")
    assert_model_homogeneous(env, model)
    kwargs = dict(agent_cfg.get("kwargs") or {})
    kwargs.pop("extra_instructions", None)
    kwargs["version"] = CLAUDE_CODE_VERSION
    if arm != "H_raw":
        if harness_dir is None:
            raise ValueError(f"{arm} needs the staged harness bundle")
        kwargs["config"] = str(Path(harness_dir).resolve() / "settings.json")
    else:
        kwargs.pop("config", None)
    agent = {"name": agent_cfg.get("name", "claude-code"), "model_name": model,
             "kwargs": kwargs, "env": env}
    t = timeout_s or (cfg.get("run") or {}).get("agent_timeout_sec") \
        or (cfg.get("watermarks") or {}).get("t_max_sec")
    if t:
        agent["override_timeout_sec"] = float(t)
    envcfg = dict(cfg.get("environment") or {"type": "docker", "delete": True})
    overlays = [str(p) for p in compose_overlays]
    envcfg["extra_docker_compose"] = overlays or list(envcfg.get("extra_docker_compose") or [])
    run = cfg.get("run") or {}
    if run.get("trial_cpus"):
        envcfg["override_cpus"] = int(run["trial_cpus"])
    if run.get("trial_memory_mb"):
        envcfg["override_memory_mb"] = int(run["trial_memory_mb"])
    if environment_env:
        envcfg["env"] = {k: str(v) for k, v in environment_env.items()}
    v_budget = int((cfg.get("watermarks") or {}).get("verifier_budget_sec") or 3300)
    out = {
        "job_name": job_name,
        "jobs_dir": str(jobs_dir),
        "n_attempts": int(n_attempts),
        "n_concurrent_trials": 1,
        "retry": {"max_retries": 0},
        "verifier": {"env": {
            "ALB_VERIFIER_NO_TIMEOUT": "1",
            "ALB_VERIFIER_BUDGET_SEC": str(v_budget),
            "ALB_VERIFIER_RECOVER_COMPLETED": "1",
            "ALB_VERIFIER_SOLVER_POLICY": "auto",
        }},
        "environment": envcfg,
        "agents": [agent],
        "tasks": [{"path": str(p)} for p in task_paths],
    }
    if extra_instructions:
        extra_list = [extra_instructions] if isinstance(extra_instructions, str) else [
            str(x) for x in extra_instructions if str(x)
        ]
        if extra_list:
            out["extra_instructions"] = extra_list
    return out


def redact_job_config(job: Mapping, secrets: Iterable[str] = (), env: Mapping | None = None) -> dict:
    """Scrubbed copy: credential URLs, literal secrets (upstream keys from the environment plus
    *secrets*) and any non-reference token value."""
    lits = list(secrets) + literal_secrets_from_env(env=env)
    out = scrub(copy.deepcopy(dict(job)), lits)
    for a in out.get("agents", []):
        e = a.get("env") or {}
        for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
            if k in e and e[k] != TOKEN_REF:
                e[k] = REDACTED
        for k in list(e):
            if PROXY_VAR.match(k) and k.lower() != "no_proxy":
                e.pop(k)
    return out


def persist_job_config(job: Mapping, path: str | Path, secrets: Iterable[str] = ()) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(redact_job_config(job, secrets), indent=1, sort_keys=True))
    return p


# ---------------------------------------------------------------------------------------------
# key policy
# ---------------------------------------------------------------------------------------------

def is_special_model(model: str) -> bool:
    return "-spec" in (model or "")          # covers -spec and -special


def parse_keys(value: str | None) -> list[str]:
    return [s for s in re.split(r"[,\s]+", value or "") if s]


def upstream_keys_from_env(cfg: Mapping | None = None, env: Mapping[str, str] | None = None) -> list[str]:
    env = os.environ if env is None else env
    gw = (cfg or {}).get("gateway") or {}
    multi = parse_keys(env.get(gw.get("upstream_keys_env", "SSAP_UPSTREAM_KEYS")))
    if multi:
        return multi
    return parse_keys(env.get(gw.get("upstream_key_env", "SSAP_UPSTREAM_KEY")))[:1]


def validate_key_policy(model: str, keys: list[str], *, multi_key_enabled: bool = False,
                        paired_models: Iterable[str] = ()) -> None:
    models = [m for m in [model, *paired_models] if m]
    kinds = {is_special_model(m) for m in models}
    if len(kinds) > 1:
        raise ValueError("paired arms mix regular and -spec/-special model ids: "
                         f"{sorted(set(models))}")
    if not keys:
        raise ValueError("no upstream key (SSAP_UPSTREAM_KEY / SSAP_UPSTREAM_KEYS)")
    if len(keys) > 1 and not (is_special_model(model) or multi_key_enabled is True):
        raise ValueError("multiple upstream keys are only allowed for -spec/-special models "
                         "unless gateway.multi_key_enabled is true")


# ---------------------------------------------------------------------------------------------
# metering proxy
# ---------------------------------------------------------------------------------------------

@dataclass
class TrialStats:
    trial_id: str
    trial_index: int
    key_slot: int
    proxy_requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cache_creation_tokens: int = 0
    upstream_models_seen: set = field(default_factory=set)
    request_models_seen: set = field(default_factory=set)
    http_429_count: int = 0
    http_5xx_count: int = 0
    unrecovered_gateway_error: bool = False
    unrecovered_gateway_errors: int = 0
    gateway_wait_seconds: float = 0.0
    llm_stream_seconds: float = 0.0
    retries: int = 0

    def as_dict(self, expected_model: str | None = None) -> dict:
        d = {k: getattr(self, k) for k in self.__dataclass_fields__}
        d["upstream_models_seen"] = sorted(self.upstream_models_seen)
        d["request_models_seen"] = sorted(self.request_models_seen)
        d["gateway_wait_seconds"] = round(self.gateway_wait_seconds, 6)
        d["llm_stream_seconds"] = round(self.llm_stream_seconds, 6)
        if expected_model is not None:
            d["model_swap"] = any(m != expected_model for m in self.upstream_models_seen)
        return d


class _UsageParser:
    """Incremental reader of Messages API usage / model from JSON bodies and SSE streams."""

    def __init__(self, sse: bool):
        self.sse = sse
        self.buf = b""
        self.model: str | None = None
        self.input = self.output = self.cached = self.created = 0

    def _usage(self, u: dict, final: bool) -> None:
        if not isinstance(u, dict):
            return
        for attr, key in (("input", "input_tokens"), ("cached", "cache_read_input_tokens"),
                          ("created", "cache_creation_input_tokens")):
            v = u.get(key)
            if isinstance(v, int) and v >= getattr(self, attr):
                setattr(self, attr, v)
        v = u.get("output_tokens")
        if isinstance(v, int):              # message_delta / final JSON usage is cumulative
            self.output = v if final else max(self.output, v)

    def _event(self, obj: Any) -> None:
        if not isinstance(obj, dict):
            return
        t = obj.get("type")
        if t == "message_start" and isinstance(obj.get("message"), dict):
            msg = obj["message"]
            if isinstance(msg.get("model"), str):
                self.model = msg["model"]
            self._usage(msg.get("usage") or {}, False)
        elif t == "message_delta":
            self._usage(obj.get("usage") or {}, True)

    def feed(self, chunk: bytes) -> None:
        self.buf += chunk
        if not self.sse:
            return
        while b"\n" in self.buf:
            line, self.buf = self.buf.split(b"\n", 1)
            line = line.strip()
            if line.startswith(b"data:"):
                try:
                    self._event(json.loads(line[5:].strip() or b"null"))
                except ValueError:
                    pass

    def close(self) -> None:
        if self.sse:
            self.feed(b"\n")
            return
        try:
            body = json.loads(self.buf or b"null")
        except ValueError:
            return
        if isinstance(body, dict):
            if isinstance(body.get("model"), str):
                self.model = body["model"]
            self._usage(body.get("usage") or {}, True)


class MeteringProxy:
    """Zero-cache pass-through metering proxy (no request cache, no replay)."""

    def __init__(self, upstream_base: str, upstream_keys: list[str], *, model: str,
                 upstream_auth_header: str = "x-api-key", upstream_auth_prefix: str = "",
                 bind: str = "127.0.0.1", port: int = 0, multi_key_enabled: bool = False,
                 paired_models: Iterable[str] = (), max_retries: int = 3,
                 retry_backoff_sec: float = 1.0, max_backoff_sec: float = 30.0,
                 upstream_timeout_sec: float = 3000.0):
        u = urllib.parse.urlsplit(upstream_base)
        if u.scheme not in ("http", "https") or not u.hostname:
            raise ValueError("upstream_base must be an http(s) URL")
        if u.username or u.password:
            raise ValueError("upstream_base must not carry credentials")
        validate_key_policy(model, upstream_keys, multi_key_enabled=multi_key_enabled,
                            paired_models=paired_models)
        self.model = model
        self._u = u
        self._keys = list(upstream_keys)
        self.auth_header = upstream_auth_header or "x-api-key"
        self.auth_prefix = upstream_auth_prefix or ""
        self.max_retries, self.backoff = int(max_retries), float(retry_backoff_sec)
        self.max_backoff, self.timeout = float(max_backoff_sec), float(upstream_timeout_sec)
        self._lock = threading.Lock()
        self._tokens: dict[str, str] = {}
        self._stats: dict[str, TrialStats] = {}
        self._next_index = 0
        self._bind, self._port = bind, int(port)
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @classmethod
    def from_config(cls, cfg: Mapping, *, env: Mapping[str, str] | None = None,
                    paired_models: Iterable[str] = (), **kw) -> "MeteringProxy":
        gw = cfg.get("gateway") or {}
        model = kw.pop("model", None) or (cfg.get("agent") or {}).get("model_name")
        keys = upstream_keys_from_env(cfg, env)
        return cls(gw.get("upstream_base", ""), keys, model=model,
                   upstream_auth_header=gw.get("upstream_auth_header") or "x-api-key",
                   upstream_auth_prefix=gw.get("upstream_auth_prefix") or "",
                   bind=kw.pop("bind", gw.get("proxy_bind") or "127.0.0.1"),
                   port=kw.pop("port", int(gw.get("proxy_port") or 0)),
                   multi_key_enabled=gw.get("multi_key_enabled") is True,
                   paired_models=paired_models, **kw)

    # -- lifecycle ---------------------------------------------------------------------------
    def start(self) -> "MeteringProxy":
        proxy = self

        class Handler(_Handler):
            owner = proxy

        self._server = ThreadingHTTPServer((self._bind, self._port), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def __enter__(self) -> "MeteringProxy":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()

    @property
    def port(self) -> int:
        return self._server.server_address[1] if self._server else self._port

    @property
    def url(self) -> str:
        host = "127.0.0.1" if self._bind in ("0.0.0.0", "") else self._bind
        return f"http://{host}:{self.port}"

    # -- trials ------------------------------------------------------------------------------
    def new_trial(self, trial_id: str, trial_index: int | None = None) -> str:
        """Issue an ephemeral token; the upstream key slot is fixed for the trial's lifetime."""
        with self._lock:
            if trial_id in self._stats:
                raise ValueError(f"trial {trial_id!r} already registered")
            idx = self._next_index if trial_index is None else int(trial_index)
            self._next_index = max(self._next_index, idx + 1)
            token = "ssap-trial-" + _secrets.token_urlsafe(24)
            self._tokens[token] = trial_id
            self._stats[trial_id] = TrialStats(trial_id, idx, idx % len(self._keys))
            return token

    def revoke(self, token: str) -> None:
        with self._lock:
            self._tokens.pop(token, None)

    def stats(self, trial_id: str) -> dict:
        with self._lock:
            return self._stats[trial_id].as_dict(self.model)

    def ledger(self) -> dict[str, dict]:
        with self._lock:
            return {k: v.as_dict(self.model) for k, v in self._stats.items()}

    def _trial_for(self, headers) -> TrialStats | None:
        cands = []
        if headers.get("x-api-key"):
            cands.append(headers["x-api-key"].strip())
        auth = headers.get("authorization") or ""
        if auth[:7].lower() == "bearer ":
            cands.append(auth[7:].strip())
        with self._lock:
            for c in cands:
                tid = self._tokens.get(c)
                if tid is not None:
                    return self._stats[tid]
        return None

    def _key(self, st: TrialStats) -> str:
        return self._keys[st.key_slot]

    def _connect(self) -> http.client.HTTPConnection:
        u = self._u
        cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
        return cls(u.hostname, u.port, timeout=self.timeout)

    def _target(self, path: str) -> str:
        base = self._u.path.rstrip("/")
        return base + (path if path.startswith("/") else "/" + path)


class _Handler(BaseHTTPRequestHandler):
    owner: MeteringProxy
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):            # no request logging (no blobs, no tokens)
        return

    def _reply(self, status: int, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._forward()

    def do_POST(self):
        self._forward()

    def _forward(self) -> None:
        px = self.owner
        n = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(n) if n else b""
        st = px._trial_for(self.headers)
        if st is None:
            self._reply(401, {"type": "error", "error": {"type": "authentication_error",
                                                         "message": "invalid or missing trial token"}})
            return
        req_model = None
        if body:
            try:
                req = json.loads(body)
                req_model = req.get("model") if isinstance(req, dict) else None
            except ValueError:
                pass
        headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP_BY_HOP}
        headers[px.auth_header] = px.auth_prefix + px._key(st)
        headers["accept-encoding"] = "identity"
        headers["content-length"] = str(len(body))
        target = px._target(self.path)
        wait = 0.0
        resp = conn = None
        with px._lock:
            st.proxy_requests += 1
            if isinstance(req_model, str):
                st.request_models_seen.add(req_model)
        for attempt in range(px.max_retries + 1):
            conn = px._connect()
            t_sent = None
            try:
                conn.request(self.command, target, body=body, headers=headers)
                t_sent = time.monotonic()
                resp = conn.getresponse()           # returns once the first bytes arrived
                wait += time.monotonic() - t_sent
            except (OSError, http.client.HTTPException):
                if t_sent is not None:
                    wait += time.monotonic() - t_sent
                conn.close()
                resp = None
                if attempt < px.max_retries:
                    with px._lock:
                        st.retries += 1
                    wait += self._sleep(attempt, None)
                    continue
                break
            if resp.status == 429 or resp.status >= 500:
                with px._lock:
                    if resp.status == 429:
                        st.http_429_count += 1
                    else:
                        st.http_5xx_count += 1
                if resp.status in RETRY_STATUSES and attempt < px.max_retries:
                    retry_after = resp.getheader("retry-after")
                    resp.read()
                    conn.close()
                    with px._lock:
                        st.retries += 1
                    wait += self._sleep(attempt, retry_after)
                    continue
            break
        if resp is None:
            with px._lock:
                st.gateway_wait_seconds += wait
                st.unrecovered_gateway_error = True
                st.unrecovered_gateway_errors += 1
            self._reply(502, {"type": "error", "error": {"type": "api_error",
                                                         "message": "upstream unreachable"}})
            return
        if resp.status == 429 or resp.status >= 500:
            with px._lock:
                st.unrecovered_gateway_error = True
                st.unrecovered_gateway_errors += 1
        self._relay(resp, st, wait)
        conn.close()

    def _sleep(self, attempt: int, retry_after: str | None) -> float:
        px = self.owner
        d = px.backoff * (2 ** attempt)
        try:
            if retry_after is not None:
                d = float(retry_after)
        except ValueError:
            pass
        d = max(0.0, min(d, px.max_backoff))
        time.sleep(d)
        return d

    def _relay(self, resp: http.client.HTTPResponse, st: TrialStats, wait: float) -> None:
        px = self.owner
        ctype = resp.getheader("content-type") or ""
        sse = "text/event-stream" in ctype
        parser = _UsageParser(sse)
        self.send_response(resp.status)
        for k, v in resp.getheaders():
            if k.lower() not in HOP_BY_HOP and k.lower() != "content-encoding":
                self.send_header(k, v)
        length = resp.getheader("content-length")
        chunked = sse or length is None
        if chunked:
            self.send_header("transfer-encoding", "chunked")
        else:
            self.send_header("content-length", length)
        self.end_headers()
        t0 = time.monotonic()
        client_ok = True
        try:
            while True:
                chunk = resp.read1(65536) if sse else resp.read(65536)
                if not chunk:
                    break
                parser.feed(chunk)
                if client_ok:
                    try:
                        if chunked:
                            self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                        else:
                            self.wfile.write(chunk)
                        self.wfile.flush()
                    except OSError:
                        client_ok = False
            if chunked and client_ok:
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
        except (OSError, http.client.HTTPException):
            with px._lock:
                st.unrecovered_gateway_error = True
                st.unrecovered_gateway_errors += 1
        stream = time.monotonic() - t0
        parser.close()
        with px._lock:
            st.gateway_wait_seconds += wait
            st.llm_stream_seconds += stream
            st.input_tokens += parser.input
            st.output_tokens += parser.output
            st.cached_tokens += parser.cached
            st.cache_creation_tokens += parser.created
            if parser.model:
                st.upstream_models_seen.add(parser.model)


# ---------------------------------------------------------------------------------------------
# infra classification
# ---------------------------------------------------------------------------------------------

def infra_reasons(stats: Mapping, *, wall_sec: float | None, theta_gw_max: float = 0.35,
                  exit_code: int | None = None) -> list[str]:
    """Infrastructure-fault reasons for one trial (empty = a scorable task outcome)."""
    out = []
    if exit_code == 137:
        out.append("oom_exit_137")
    if stats.get("unrecovered_gateway_error"):
        out.append("unrecovered_gateway_error")
    if int(stats.get("proxy_requests") or 0) == 0:
        out.append("proxy_requests_zero")
    if wall_sec and math.isfinite(wall_sec) and wall_sec > 0:
        if float(stats.get("gateway_wait_seconds") or 0.0) / wall_sec > theta_gw_max:
            out.append("gateway_wait_fraction_exceeded")
    return out


def free_port(host: str = "127.0.0.1") -> int:
    with socket.socket() as s:
        s.bind((host, 0))
        return s.getsockname()[1]
