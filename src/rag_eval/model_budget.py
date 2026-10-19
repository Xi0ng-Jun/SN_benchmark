"""Evaluation-side request reservations. No prompts, headers or addresses saved.

Public list prices are estimates, not an account invoice. UTF-8 input bytes plus
framing margin and the published output ceiling bound each admitted request.
Unknown/partial usage retains that reservation; provider usage above a bound
halts future requests. A separate flock serializes all batch processes.
"""
from contextlib import contextmanager
import fcntl
import json
import math
import os
from pathlib import Path
import tempfile
import uuid

RATES = {'qwen-turbo': (.30, 3.00), 'qwen-flash': (.15, 1.50), 'text-embedding-v4': (.50, 0),
         'qwen3-rerank': (.50, 0)}
CHAT_MODELS = frozenset({'qwen-turbo', 'qwen-flash'})
# qwen-flash is priced per request by input-length tier; RATES carry the first
# tier only. Input bytes bound tokens, so a byte bound inside the tier is safe.
INPUT_TIER_LIMIT = {'qwen-flash': 131072}


class BudgetStopped(KeyboardInterrupt):
    """Bypasses SN's ordinary exception/retry handlers; CLI retains interrupted state."""


class ModelBudget:
    def __init__(self, path):
        self.path = Path(path).resolve()

    @classmethod
    def create(cls, path, *, limit_cny):
        limit = float(limit_cny)
        if not math.isfinite(limit) or limit <= 0:
            raise ValueError('Invalid approved budget')
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('x', encoding='utf-8') as stream:
            json.dump(dict(format='sn-model-budget-v1', approved_limit_cny=limit,
                           rates=RATES, halted=False, requests={}), stream)
        return cls(path)

    @contextmanager
    def _locked(self):
        with self.path.with_suffix(self.path.suffix + '.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            row = json.loads(self.path.read_text())
            if row.get('format') != 'sn-model-budget-v1' or row.get('rates') != {k: list(v) for k, v in RATES.items()}:
                raise BudgetStopped('Budget identity invalid')
            yield row
            with tempfile.NamedTemporaryFile('w', dir=self.path.parent, delete=False) as tmp:
                json.dump(row, tmp, ensure_ascii=False, allow_nan=False)
                tmp.flush()
                os.fsync(tmp.fileno())
            os.replace(tmp.name, self.path)

    def reserve(self, payload):
        if not isinstance(payload, dict) or payload.get('model') not in RATES:
            raise BudgetStopped('Model outside approved budget')
        model = payload['model']
        # Token IDs, modalities and unknown payloads need a separate budget policy.
        inputs = payload.get('messages') if model in CHAT_MODELS else payload.get('input')
        if model == 'qwen3-rerank':
            inputs = payload.get('input', payload)
        serialized = json.dumps(inputs, ensure_ascii=False).encode('utf-8')
        if model == 'text-embedding-v4' and (not isinstance(inputs, (str, list)) or
                isinstance(inputs, list) and any(not isinstance(x, str) for x in inputs)):
            raise BudgetStopped('Unsupported embedding input')
        multiplier = 1
        if model == 'qwen3-rerank':
            documents = (inputs or {}).get('documents', []) if isinstance(inputs, dict) else []
            multiplier = max(1, len(documents))
        input_bound = len(serialized) * multiplier + 4096
        if input_bound > INPUT_TIER_LIMIT.get(model, math.inf):
            raise BudgetStopped('Request outside priced tier')
        output_bound = 16384 if model in CHAT_MODELS else 0
        rate_in, rate_out = RATES[model]
        cost = (input_bound * rate_in + output_bound * rate_out) / 1e6
        rid = uuid.uuid4().hex
        with self._locked() as row:
            used = sum(r['cost_cny'] for r in row['requests'].values())
            if row['halted'] or used + cost > row['approved_limit_cny']:
                raise BudgetStopped('Approved budget unavailable')
            row['requests'][rid] = dict(model=model, input_bound=input_bound, output_bound=output_bound,
                                       cost_cny=cost, reserved_cny=cost, status='reserved')
        return rid

    def settle(self, rid, usage):
        if not isinstance(usage, dict):
            return
        inp = usage.get('prompt_tokens', usage.get('input_tokens', usage.get('total_tokens')))
        out = usage.get('completion_tokens', usage.get('output_tokens', 0))
        if any(type(v) is not int or v < 0 for v in (inp, out)):
            return
        with self._locked() as row:
            request = row['requests'][rid]
            if request['model'] in CHAT_MODELS and 'completion_tokens' not in usage and 'output_tokens' not in usage:
                return
            rate_in, rate_out = RATES[request['model']]
            cost = (inp * rate_in + out * rate_out) / 1e6
            request.update(status='observed', input_tokens=inp, output_tokens=out, cost_cny=cost)
            if inp > request['input_bound'] or out > request['output_bound']:
                row['halted'] = True


class _Usage:
    def __init__(self):
        self.buffer = b''
        self.usage = None
        self.done = False

    def feed(self, chunk):
        self.buffer += chunk
        while b'\n' in self.buffer:
            line, self.buffer = self.buffer.split(b'\n', 1)
            if line.startswith(b'data:'):
                data = line[5:].strip()
                if data == b'[DONE]':
                    self.done = True
                else:
                    self.parse(data)
        # Usage trailers are tiny; never retain unlimited response text.
        if len(self.buffer) > 1048576:
            self.buffer = b''

    def parse(self, raw):
        try:
            obj = json.loads(raw)
        except (ValueError, UnicodeError):
            return
        if isinstance(obj, dict) and isinstance(obj.get('usage'), dict):
            self.usage = obj['usage']


@contextmanager
def install_budget_guard(path):
    """Wrap the physical sync transports used by the captured SN runtime.

    Covers retries individually. Async and requests streaming are refused since
    these are outside this experiment's observed transport contract.
    """
    import httpx
    import requests
    budget = ModelBudget(path)
    original_http = httpx.Client.send
    original_async = httpx.AsyncClient.send
    original_requests = requests.Session.send

    def reserve(raw):
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError, UnicodeError):
            raise BudgetStopped('Unsupported outgoing request') from None
        return budget.reserve(payload)

    def http_send(client, request, **kwargs):
        rid = reserve(request.content)
        response = original_http(client, request, **{**kwargs, 'stream': True})
        original_bytes = response.iter_bytes
        def iter_bytes(chunk_size=None):
            observer = _Usage()
            # Observe the same decoded bytes the SDK consumes (including gzip),
            # while leaving HTTPX's transport, decoder and byte ordering intact.
            for chunk in original_bytes(chunk_size=chunk_size):
                observer.feed(chunk)
                if observer.done:
                    budget.settle(rid, observer.usage)
                yield chunk
            observer.parse(observer.buffer)
            budget.settle(rid, observer.usage)
        response.iter_bytes = iter_bytes
        if not kwargs.get('stream', False):
            response.read()
            observer = _Usage()
            observer.parse(response.content)
            budget.settle(rid, observer.usage)
        return response

    def request_send(session, request, **kwargs):
        if kwargs.get('stream'):
            raise BudgetStopped('Unsupported streaming transport')
        rid = reserve(request.body)
        response = original_requests(session, request, **kwargs)
        observer = _Usage()
        observer.parse(response.content)
        budget.settle(rid, observer.usage)
        return response

    async def async_send(*args, **kwargs):
        raise BudgetStopped('Unsupported async transport')

    httpx.Client.send, httpx.AsyncClient.send, requests.Session.send = http_send, async_send, request_send
    try:
        yield budget
    finally:
        httpx.Client.send, httpx.AsyncClient.send, requests.Session.send = original_http, original_async, original_requests
