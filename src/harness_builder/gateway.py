"""The model exit (rule 4): every model call of the solver and developer agents goes through here.

A local HTTP pass-through to one upstream model service. Each credential is bound
to one model and one budget pool and may carry its own caps. A request is refused
(nothing is forwarded) when its credential is unknown or revoked, when it is not a
JSON request naming the bound model, or when a cap of the credential or of its
pool is reached. Requests already under way are not cut off, so a cap can be
overshot by the requests in flight at that moment.

Usage is read from responses in the Anthropic Messages and OpenAI chat formats,
streamed or not. A request is accounted before the client can see the end of its
response (non-streamed bodies are received in full first; streams are accounted
before their final terminator), so a client that waits for each reply never gets
past a cap. No retries, no caching. One line per request goes to the request log.
"""
from __future__ import annotations

import http.client
import json
import os
import re
import secrets
import socketserver
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .ledger import now

TOKEN_COUNTERS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")
CAP_KEYS = ("requests", *TOKEN_COUNTERS, "total_tokens")

# Implementation detail: how long closing a key waits for its requests still in flight.
CLOSE_WAIT_SECONDS = 30

HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te",
              "trailers", "transfer-encoding", "upgrade", "host", "content-length",
              "x-api-key", "authorization", "accept-encoding"}


class GatewayError(Exception):
    pass


@dataclass
class Usage:
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    upstream_errors: int = 0
    refused: dict = field(default_factory=dict)
    response_models: set = field(default_factory=set)
    wait_seconds: float = 0.0
    seconds: float = 0.0

    def value(self, key: str) -> int:
        if key == "total_tokens":
            return sum(getattr(self, k) for k in TOKEN_COUNTERS)
        return getattr(self, key)

    def add(self, other: "Usage") -> None:
        for k in ("requests", *TOKEN_COUNTERS, "upstream_errors"):
            setattr(self, k, getattr(self, k) + getattr(other, k))
        for k, n in other.refused.items():
            self.refused[k] = self.refused.get(k, 0) + n
        self.response_models |= other.response_models
        self.wait_seconds += other.wait_seconds
        self.seconds += other.seconds

    def to_dict(self) -> dict:
        return {"requests": self.requests, **{k: getattr(self, k) for k in TOKEN_COUNTERS},
                "total_tokens": self.value("total_tokens"),
                "upstream_errors": self.upstream_errors, "refused": dict(self.refused),
                "response_models": sorted(self.response_models),
                "wait_seconds": round(self.wait_seconds, 3), "seconds": round(self.seconds, 3)}


def exceeded(usage: Usage, caps: dict | None) -> list[str]:
    return [f"{k} {usage.value(k)} >= {cap}" for k, cap in (caps or {}).items()
            if usage.value(k) >= cap]


@dataclass
class Credential:
    owner: str
    pool: str
    model: str
    caps: dict
    usage: Usage = field(default_factory=Usage)
    open: bool = True
    inflight: int = 0


def expand_env(value: str, env=None) -> str:
    """Replace ${VAR} and ${VAR:-default}; an unset VAR without default is an error."""
    env = os.environ if env is None else env

    def sub(m):
        name, default = m.group(1), m.group(3)
        if name in env:
            return env[name]
        if default is not None:
            return default
        raise GatewayError(f"environment variable {name} is not set")
    return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(:-([^}]*))?\}", sub, value)


class Gateway:
    def __init__(self, *, upstream_url: str, upstream_key: str,
                 auth_header: str = "x-api-key", auth_prefix: str = "",
                 listen_host: str = "127.0.0.1", listen_port: int = 0,
                 url_host: str | None = None, upstream_timeout: float = 600.0,
                 log_path: str | Path | None = None):
        u = urllib.parse.urlsplit(upstream_url)
        if u.scheme not in ("http", "https") or not u.hostname or u.username or u.password:
            raise GatewayError("upstream_url must be an http(s) URL without credentials")
        if not upstream_key:
            raise GatewayError("upstream key is empty")
        self._u = u
        self._key = upstream_key
        self.auth_header, self.auth_prefix = auth_header, auth_prefix
        self.listen_host, self.listen_port = listen_host, int(listen_port)
        self.url_host = url_host or ("127.0.0.1" if listen_host in ("", "0.0.0.0")
                                     else listen_host)
        self.upstream_timeout = upstream_timeout
        self.log_path = Path(log_path) if log_path else None
        self._lock = threading.Lock()
        self._idle = threading.Condition(self._lock)
        self._inflight = 0
        self._creds: dict[str, Credential] = {}
        self._pools: dict[str, Usage] = {}
        self._pool_caps: dict[str, dict] = {}
        self._server: ThreadingHTTPServer | None = None
        self._unix: socketserver.ThreadingUnixStreamServer | None = None
        self._load_log()

    def _load_log(self) -> None:
        """Pool usage survives restarts: it is summed from the request log."""
        if not self.log_path or not self.log_path.exists():
            return
        for line in self.log_path.read_text().splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if not e.get("pool"):
                continue
            u = Usage(requests=e.get("requests", 0), upstream_errors=e.get("upstream_errors", 0),
                      **{k: e.get(k, 0) for k in TOKEN_COUNTERS})
            self._pools.setdefault(e["pool"], Usage()).add(u)

    @classmethod
    def from_scenario(cls, scenario, *, log_path=None, env=None) -> "Gateway":
        m = scenario.models
        env = os.environ if env is None else env
        key_env = m["upstream_key_env"]
        if not env.get(key_env):
            raise GatewayError(f"environment variable {key_env} (upstream key) is not set")
        return cls(upstream_url=expand_env(m["upstream_url"], env), upstream_key=env[key_env],
                   auth_header=m.get("upstream_auth_header", "x-api-key"),
                   auth_prefix=m.get("upstream_auth_prefix", ""),
                   listen_host=m.get("listen_host", "127.0.0.1"),
                   listen_port=m.get("listen_port", 0), url_host=m.get("url_host"),
                   log_path=log_path)

    # -- lifecycle -------------------------------------------------------------------------
    def start(self) -> "Gateway":
        owner = self

        class Handler(_Handler):
            gateway = owner

        self._server = ThreadingHTTPServer((self.listen_host, self.listen_port), Handler)
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def start_unix(self, path: str | Path) -> None:
        """Also serve on a Unix socket (for sandboxes without a network)."""
        owner = self

        class Handler(_Handler):
            gateway = owner

        class Server(socketserver.ThreadingUnixStreamServer):
            daemon_threads = True

        self._unix = Server(str(path), Handler)
        threading.Thread(target=self._unix.serve_forever, daemon=True).start()

    def stop_unix(self) -> None:
        if self._unix is not None:
            self._unix.shutdown()
            self._unix.server_close()
            self._unix = None

    def stop(self) -> None:
        for name in ("_server", "_unix"):
            server = getattr(self, name)
            if server is not None:
                server.shutdown()
                server.server_close()
                setattr(self, name, None)

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()

    @property
    def url(self) -> str:
        if self._server is None:
            raise GatewayError("gateway not started")
        return f"http://{self.url_host}:{self._server.server_address[1]}"

    # -- credentials and pools -------------------------------------------------------------
    def set_pool_caps(self, pool: str, caps: dict | None) -> None:
        with self._lock:
            self._pool_caps[pool] = dict(caps or {})
            self._pools.setdefault(pool, Usage())

    def issue(self, owner: str, pool: str, model: str, caps: dict | None = None) -> str:
        key = "hb-" + secrets.token_urlsafe(24)
        with self._lock:
            self._creds[key] = Credential(owner, pool, model, dict(caps or {}))
            self._pools.setdefault(pool, Usage())
        return key

    def close(self, key: str, wait: float = CLOSE_WAIT_SECONDS) -> dict:
        """Revoke ``key``; wait (up to ``wait`` s) for its requests in flight; return its usage."""
        with self._idle:
            c = self._creds[key]
            c.open = False
            self._idle.wait_for(lambda: c.inflight == 0, timeout=wait)
            return {"owner": c.owner, "pool": c.pool, "model": c.model, "caps": c.caps,
                    **c.usage.to_dict(), "in_flight_at_close": c.inflight}

    def wait_idle(self, timeout: float | None = None) -> bool:
        """Wait until no request is in flight; False on timeout."""
        with self._idle:
            return self._idle.wait_for(lambda: self._inflight == 0, timeout=timeout)

    def pool_usage(self, pool: str) -> dict:
        with self._lock:
            return self._pools.get(pool, Usage()).to_dict()

    def pool_exceeded(self, pool: str) -> list[str]:
        with self._lock:
            return exceeded(self._pools.get(pool, Usage()), self._pool_caps.get(pool))

    # -- used by the request handler -------------------------------------------------------
    def _admit(self, token: str | None, body: bytes, method: str):
        """(credential, None) if the request may go upstream, else (credential|None, refusal).

        A refusal is (http status, error type, message, reason counted in usage).
        """
        with self._lock:
            c = self._creds.get(token or "")
            if c is None or not c.open:
                return None, (401, "authentication_error", "unknown or revoked key", None)
            c.inflight += 1  # until _account
            self._inflight += 1
            if method != "GET":
                try:
                    req = json.loads(body or b"null")
                except ValueError:
                    req = None
                model = req.get("model") if isinstance(req, dict) else None
                if model != c.model:
                    return c, (403, "permission_error",
                               f"model {model!r} not allowed; this key may use {c.model!r}",
                               "model")
            over = exceeded(c.usage, c.caps) + exceeded(self._pools[c.pool],
                                                       self._pool_caps.get(c.pool))
            if over:
                return c, (403, "permission_error", "budget reached: " + "; ".join(over),
                           "budget")
            return c, None

    def _account(self, c: Credential | None, u: Usage, refusal: str | None,
                 entry: dict) -> None:
        with self._lock:
            if c is not None:
                if refusal:
                    u.refused[refusal] = 1
                c.usage.add(u)
                self._pools[c.pool].add(u)
                c.inflight -= 1
                self._inflight -= 1
                self._idle.notify_all()
            if self.log_path:
                with open(self.log_path, "a") as f:
                    f.write(json.dumps(entry) + "\n")

    def _connect(self) -> http.client.HTTPConnection:
        cls = (http.client.HTTPSConnection if self._u.scheme == "https"
               else http.client.HTTPConnection)
        return cls(self._u.hostname, self._u.port, timeout=self.upstream_timeout)

    def _target(self, path: str) -> str:
        return self._u.path.rstrip("/") + (path if path.startswith("/") else "/" + path)


class _UsageReader:
    """Reads usage and model from Anthropic or OpenAI responses, JSON or SSE."""

    def __init__(self, sse: bool):
        self.sse, self.buf = sse, b""
        self.model: str | None = None
        self.counts = dict.fromkeys(TOKEN_COUNTERS, 0)

    def feed(self, chunk: bytes) -> None:
        self.buf += chunk
        if not self.sse:
            return
        while b"\n" in self.buf:
            line, self.buf = self.buf.split(b"\n", 1)
            line = line.strip()
            if line.startswith(b"data:"):
                try:
                    self._object(json.loads(line[5:].strip() or b"null"))
                except ValueError:
                    pass

    def close(self) -> None:
        if self.sse:
            self.feed(b"\n")
            return
        try:
            self._object(json.loads(self.buf or b"null"))
        except ValueError:
            pass

    def _object(self, obj) -> None:
        if not isinstance(obj, dict):
            return
        if obj.get("type") == "message_start" and isinstance(obj.get("message"), dict):
            obj = obj["message"]
        if isinstance(obj.get("model"), str):
            self.model = obj["model"]
        u = obj.get("usage")
        if isinstance(u, dict):
            self._usage(u)

    def _usage(self, u: dict) -> None:
        def num(v):
            return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else 0
        if "prompt_tokens" in u or "completion_tokens" in u:  # OpenAI: prompt includes cached
            details = u.get("prompt_tokens_details")
            cached = num(details.get("cached_tokens")) if isinstance(details, dict) else 0
            seen = {"input_tokens": max(num(u.get("prompt_tokens")) - cached, 0),
                    "cache_read_tokens": cached,
                    "output_tokens": num(u.get("completion_tokens"))}
        else:                                                 # Anthropic
            seen = {"input_tokens": num(u.get("input_tokens")),
                    "output_tokens": num(u.get("output_tokens")),
                    "cache_read_tokens": num(u.get("cache_read_input_tokens")),
                    "cache_write_tokens": num(u.get("cache_creation_input_tokens"))}
        for k, v in seen.items():  # streamed usage is cumulative: keep the largest value
            self.counts[k] = max(self.counts[k], v)


class _Handler(BaseHTTPRequestHandler):
    gateway: Gateway
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        return

    def do_GET(self):
        self._handle()

    def do_POST(self):
        self._handle()

    def _read_body(self) -> bytes:
        if "chunked" in (self.headers.get("transfer-encoding") or "").lower():
            parts = []
            while True:
                size = int(self.rfile.readline().split(b";")[0].strip() or b"0", 16)
                if size == 0:
                    while self.rfile.readline() not in (b"\r\n", b"\n", b""):
                        pass
                    return b"".join(parts)
                parts.append(self.rfile.read(size))
                self.rfile.readline()
        n = int(self.headers.get("content-length") or 0)
        return self.rfile.read(n) if n else b""

    def _token(self) -> str | None:
        if self.headers.get("x-api-key"):
            return self.headers["x-api-key"].strip()
        auth = self.headers.get("authorization") or ""
        return auth[7:].strip() if auth[:7].lower() == "bearer " else None

    def _reply(self, status: int, kind: str, message: str) -> None:
        body = json.dumps({"type": "error", "error": {"type": kind, "message": message}}).encode()
        try:
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            self.close_connection = True

    def _handle(self) -> None:
        gw = self.gateway
        t0 = time.monotonic()
        body = self._read_body()
        c, refusal = gw._admit(self._token(), body, self.command)
        entry = {"time": now(), "owner": c.owner if c else None,
                 "pool": c.pool if c else None, "method": self.command, "path": self.path}
        u = Usage()
        settled = []

        def settle(status: int, reason: str | None = None) -> None:
            """Account this request once; must run before the client sees the response end."""
            if settled:
                return
            settled.append(status)
            u.seconds = time.monotonic() - t0
            entry["status"] = status
            if refusal is None:
                entry.update({k: v for k, v in u.to_dict().items() if k != "refused"})
            gw._account(c, u, reason, entry)

        try:
            if refusal:
                status, kind, message, reason = refusal
                entry["refused"] = message
                settle(status, reason)
                self._reply(status, kind, message)
                return
            u.requests = 1
            self._forward(body, u, settle)
        finally:
            settle(0)  # only if something above failed before settling

    def _forward(self, body: bytes, u: Usage, settle) -> None:
        gw = self.gateway
        headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP_BY_HOP}
        headers[gw.auth_header] = gw.auth_prefix + gw._key
        headers["accept-encoding"] = "identity"
        headers["content-length"] = str(len(body))
        conn = gw._connect()
        t_sent = time.monotonic()
        try:
            conn.request(self.command, gw._target(self.path), body=body, headers=headers)
            resp = conn.getresponse()
        except (OSError, http.client.HTTPException) as e:
            conn.close()
            u.upstream_errors += 1
            u.wait_seconds = time.monotonic() - t_sent
            settle(502)
            self._reply(502, "api_error", f"upstream unreachable: {e}")
            return
        u.wait_seconds = time.monotonic() - t_sent
        if resp.status == 429 or resp.status >= 500:
            u.upstream_errors += 1
        try:
            if "text/event-stream" in (resp.getheader("content-type") or ""):
                self._relay_stream(resp, u, settle)
            else:
                self._relay_whole(resp, u, settle)
        finally:
            conn.close()

    def _send_head(self, resp: http.client.HTTPResponse, length: int | None) -> bool:
        try:
            self.send_response(resp.status)
            for k, v in resp.getheaders():
                if k.lower() not in HOP_BY_HOP and k.lower() != "content-encoding":
                    self.send_header(k, v)
            if length is None:
                self.send_header("transfer-encoding", "chunked")
            else:
                self.send_header("content-length", str(length))
            self.end_headers()
            return True
        except OSError:
            return False

    @staticmethod
    def _take(reader: "_UsageReader", u: Usage) -> None:
        reader.close()
        for k, v in reader.counts.items():
            setattr(u, k, v)
        if reader.model:
            u.response_models.add(reader.model)

    def _relay_whole(self, resp: http.client.HTTPResponse, u: Usage, settle) -> None:
        """Receive the whole body, account, then send it."""
        try:
            data = resp.read()
        except (OSError, http.client.HTTPException) as e:
            u.upstream_errors += 1
            settle(502)
            self._reply(502, "api_error", f"upstream read failed: {e}")
            return
        reader = _UsageReader(sse=False)
        reader.feed(data)
        self._take(reader, u)
        settle(resp.status)
        if not self._send_head(resp, len(data)):
            self.close_connection = True
            return
        try:
            self.wfile.write(data)
            self.wfile.flush()
        except OSError:
            self.close_connection = True

    def _relay_stream(self, resp: http.client.HTTPResponse, u: Usage, settle) -> None:
        """Pass a stream through as it comes; account before the final terminator."""
        reader = _UsageReader(sse=True)
        client_ok = self._send_head(resp, None)
        complete = False
        while True:  # keep reading after a client disconnect so usage is still counted
            try:
                chunk = resp.read1(65536)
            except (OSError, http.client.HTTPException):
                u.upstream_errors += 1
                break
            if not chunk:
                complete = True
                break
            reader.feed(chunk)
            if client_ok:
                try:
                    self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                    self.wfile.flush()
                except OSError:
                    client_ok = False
        self._take(reader, u)
        settle(resp.status)
        if client_ok and complete:
            try:
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except OSError:
                client_ok = False
        if not (complete and client_ok):
            self.close_connection = True
