"""Local HTTP forward proxy for the target agents' Wikipedia calls (infrastructure, not agent code).

web_search_agent opens a new connection per Wikipedia request (``http://en.wikipedia.org``). On
this machine every new connection goes through the system proxy, where ~40% of connects hang
for 30 s and bursts of fresh connections get HTTP 429. This proxy serves those plain-HTTP
requests over a small pool of persistent HTTPS connections (upstream: ``--upstream``, the system
proxy), retries hung connects and 429s with backoff, and caches successful responses in SQLite,
so every evaluation (seed, candidates, GEPA) sees the same Wikipedia snapshot.

    python -m rag_eval.vero.wiki_proxy --port 4010 --upstream http://127.0.0.1:7897 --cache .vero-state/wiki-cache.sqlite

Targets use it via ``HTTP_PROXY=http://127.0.0.1:4010``. Only absolute-URI GETs are served;
CONNECT (HTTPS) is refused, so it never sees TLS traffic. The agent's own headers (User-Agent)
are forwarded unchanged.
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

import httpx

log = logging.getLogger("wiki_proxy")

HTTPS_HOSTS = ("wikipedia.org", "wikimedia.org", "wikidata.org")   # fetched over HTTPS directly (no 301 hop)
FORWARD_HEADERS = ("user-agent", "accept", "accept-language")


class Cache:
    def __init__(self, path: Optional[str]):
        self.db = None
        self.lock = threading.Lock()
        if path:
            self.db = sqlite3.connect(path, check_same_thread=False)
            self.db.execute("CREATE TABLE IF NOT EXISTS responses (key TEXT PRIMARY KEY, url TEXT, status INTEGER, "
                            "content_type TEXT, body BLOB, fetched_at REAL)")
            self.db.commit()

    @staticmethod
    def key(url: str) -> str:
        return hashlib.sha256(url.encode()).hexdigest()

    def get(self, url: str):
        if self.db is None:
            return None
        with self.lock:
            return self.db.execute("SELECT status, content_type, body FROM responses WHERE key=?",
                                   (self.key(url),)).fetchone()

    def put(self, url: str, status: int, content_type: str, body: bytes) -> None:
        if self.db is None:
            return
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO responses VALUES (?,?,?,?,?,?)",
                            (self.key(url), url, status, content_type, body, time.time()))
            self.db.commit()


class Upstream:
    """Paced upstream fetches: at most *rate* requests/s overall, and a shared cooldown after a 429.

    Wikimedia's API gateway answers bursts with 429 + Retry-After (~40 s) for the whole client. Every
    request therefore waits for its pacing slot and for the end of any cooldown; a request that cannot be
    served within *deadline* seconds fails fast (502) so the agent's own 30 s timeout and retry loop apply.
    """

    def __init__(self, proxy: Optional[str], connections: int, attempts: int, rate: float = 2.0,
                 deadline: float = 25.0, user_agent: Optional[str] = None):
        limits = httpx.Limits(max_connections=connections, max_keepalive_connections=connections)
        self.client = httpx.Client(follow_redirects=True, trust_env=False, timeout=httpx.Timeout(20, connect=8),
                                   transport=httpx.HTTPTransport(proxy=proxy, retries=3, limits=limits))
        self.attempts, self.interval, self.deadline, self.user_agent = attempts, 1.0 / rate, deadline, user_agent
        self.stats = dict(fetched=0, cached=0, retried=0, failed=0, throttled=0)
        self.lock = threading.Lock()
        self._next_slot = 0.0
        self._cooldown_until = 0.0

    def count(self, key: str) -> None:
        with self.lock:
            self.stats[key] += 1

    def _wait_slot(self, deadline: float) -> bool:
        """Sleep until this request's pacing slot (after any cooldown); False when it would miss *deadline*."""
        with self.lock:
            now = time.monotonic()
            slot = max(now, self._next_slot, self._cooldown_until)
            if slot > deadline:
                return False
            self._next_slot = slot + self.interval
        time.sleep(max(0.0, slot - time.monotonic()))
        return True

    def _cool_down(self, response: httpx.Response) -> None:
        try:
            wait = float(response.headers.get("retry-after", "10"))
        except ValueError:
            wait = 10.0
        with self.lock:
            self._cooldown_until = max(self._cooldown_until, time.monotonic() + min(wait, 120.0))
            self.stats["throttled"] += 1
        log.warning("429 from upstream: pausing all requests for %.0f s", min(wait, 120.0))

    def fetch(self, url: str, headers: dict) -> httpx.Response:
        if self.user_agent:
            headers = dict(headers, **{"User-Agent": self.user_agent})
        deadline = time.monotonic() + self.deadline
        last: Optional[BaseException] = None
        for attempt in range(self.attempts):
            if attempt:
                self.count("retried")
            if not self._wait_slot(deadline):
                break
            try:
                r = self.client.get(url, headers=headers)
            except httpx.TransportError as exc:
                last = exc
                continue
            if r.status_code == 429:
                self._cool_down(r)
                last = RuntimeError("HTTP 429")
                continue
            if r.status_code >= 500:
                last = RuntimeError(f"HTTP {r.status_code}")
                continue
            return r
        raise RuntimeError(f"upstream not served within {self.deadline:.0f} s: {last or 'throttled'}")


def upgrade(url: str) -> str:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme == "http" and any(host == h or host.endswith("." + h) for h in HTTPS_HOSTS):
        return urlunsplit(("https",) + tuple(parts[1:]))
    return url


def make_handler(cache: Cache, upstream: Upstream):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # quiet: one line per upstream fetch is logged below
            pass

        def _send(self, status: int, content_type: str, body: bytes, source: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type or "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Wiki-Proxy", source)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            url = self.path
            if not url.startswith("http://"):
                return self._send(400, "text/plain", b"absolute http:// URL expected (forward proxy)", "error")
            target = upgrade(url)
            hit = cache.get(target)
            if hit is not None:
                upstream.count("cached")
                return self._send(hit[0], hit[1], hit[2], "cache")
            headers = {k: v for k, v in self.headers.items() if k.lower() in FORWARD_HEADERS}
            start = time.monotonic()
            try:
                r = upstream.fetch(target, headers)
            except Exception as exc:  # noqa: BLE001
                upstream.count("failed")
                log.warning("FAIL %.1fs %s: %s", time.monotonic() - start, target[:200], exc)
                return self._send(502, "text/plain", str(exc).encode(), "error")
            upstream.count("fetched")
            body, ctype = r.content, r.headers.get("content-type", "")
            if r.status_code == 200:
                cache.put(target, r.status_code, ctype, body)
            log.info("%d %.1fs %s", r.status_code, time.monotonic() - start, target[:200])
            self._send(r.status_code, ctype, body, "upstream")

        def do_CONNECT(self):
            self._send(405, "text/plain", b"CONNECT not supported; route HTTPS elsewhere", "error")

        do_POST = do_CONNECT

    return Handler


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=4010)
    p.add_argument("--upstream", default=None, help="proxy for upstream HTTPS (e.g. the system proxy)")
    p.add_argument("--cache", default=None, help="SQLite file; omit to disable caching")
    p.add_argument("--connections", type=int, default=8)
    p.add_argument("--attempts", type=int, default=6)
    p.add_argument("--rate", type=float, default=2.0, help="upstream requests per second, all clients together")
    p.add_argument("--deadline", type=float, default=25.0,
                   help="answer (or fail with 502) within this many seconds; below the agent's 30 s timeout")
    p.add_argument("--user-agent", default=None, help="replace the client's User-Agent upstream")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cache = Cache(args.cache)
    upstream = Upstream(args.upstream, args.connections, args.attempts, rate=args.rate, deadline=args.deadline,
                        user_agent=args.user_agent)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(cache, upstream))
    server.daemon_threads = True

    def report():
        while True:
            time.sleep(300)
            log.info("stats %s", upstream.stats)
    threading.Thread(target=report, daemon=True).start()
    log.info("wiki proxy on %s:%d upstream=%s cache=%s", args.host, args.port, args.upstream, args.cache)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
