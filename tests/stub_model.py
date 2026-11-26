"""Stand-in for the upstream model service: fixed replies with fixed usage, no model.

Speaks just enough of two formats:
  POST /v1/messages          Anthropic Messages, JSON or SSE (body "stream": true)
  POST /v1/chat/completions  OpenAI chat, JSON or SSE
  GET  /v1/models            a model list
Every reply reports the same usage (ANTHROPIC_USAGE / OPENAI_USAGE). Requests are
kept in ``received``. Set ``fail_status`` to answer every request with that status.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ANTHROPIC_USAGE = {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 3,
                   "cache_creation_input_tokens": 2}
OPENAI_USAGE = {"prompt_tokens": 15, "completion_tokens": 4,
                "prompt_tokens_details": {"cached_tokens": 6}}
# What the gateway should count for one call of each format.
ANTHROPIC_COUNTED = {"input_tokens": 10, "output_tokens": 5, "cache_read_tokens": 3,
                     "cache_write_tokens": 2}
OPENAI_COUNTED = {"input_tokens": 9, "output_tokens": 4, "cache_read_tokens": 6,
                  "cache_write_tokens": 0}


class StubModel:
    def __init__(self, key="upstream-secret"):
        self.key = key
        self.received = []
        self.fail_status = None
        self.reply_model = None
        self._server = None

    def start(self):
        owner = self

        class Handler(_Handler):
            stub = owner

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def stop(self):
        self._server.shutdown()
        self._server.server_close()

    @property
    def url(self):
        return "http://127.0.0.1:%d" % self._server.server_address[1]


class _Handler(BaseHTTPRequestHandler):
    stub = None
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        return

    def _send(self, status, obj=None, sse_events=None):
        if sse_events is not None:
            body = b"".join(b"data: " + json.dumps(e).encode() + b"\n\n" for e in sse_events)
            body += b"data: [DONE]\n\n"
            ctype = "text/event-stream"
        else:
            body, ctype = json.dumps(obj).encode(), "application/json"
        self.send_response(status)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._record(None)
        if not self._authorised():
            return
        self._send(200, {"data": [{"id": "stub-solver"}, {"id": "stub-developer"}]})

    def do_POST(self):
        n = int(self.headers.get("content-length") or 0)
        body = json.loads(self.rfile.read(n) or b"null")
        self._record(body)
        if not self._authorised():
            return
        if self.stub.fail_status:
            self._send(self.stub.fail_status, {"error": {"message": "stub failure"}})
            return
        model = self.stub.reply_model or body.get("model")
        stream = bool(body.get("stream"))
        if self.path == "/v1/messages":
            msg = {"type": "message", "model": model, "role": "assistant",
                   "content": [{"type": "text", "text": "ok"}]}
            if not stream:
                self._send(200, dict(msg, usage=ANTHROPIC_USAGE))
                return
            start_usage = dict(ANTHROPIC_USAGE, output_tokens=1)
            self._send(200, sse_events=[
                {"type": "message_start", "message": dict(msg, content=[], usage=start_usage)},
                {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "ok"}},
                {"type": "message_delta", "usage": {"output_tokens": 5}},
                {"type": "message_stop"}])
        elif self.path == "/v1/chat/completions":
            choice = {"index": 0, "message": {"role": "assistant", "content": "ok"}}
            if not stream:
                self._send(200, {"model": model, "choices": [choice], "usage": OPENAI_USAGE})
                return
            self._send(200, sse_events=[
                {"model": model, "choices": [{"index": 0, "delta": {"content": "ok"}}]},
                {"model": model, "choices": [], "usage": OPENAI_USAGE}])
        else:
            self._send(404, {"error": {"message": "no such path"}})

    def _record(self, body):
        self.stub.received.append({"method": self.command, "path": self.path,
                                   "headers": {k.lower(): v for k, v in self.headers.items()},
                                   "body": body})

    def _authorised(self):
        ok = (self.headers.get("x-api-key") == self.stub.key
              or self.headers.get("authorization") == "Bearer " + self.stub.key)
        if not ok:
            self._send(401, {"error": {"message": "bad upstream key"}})
        return ok
