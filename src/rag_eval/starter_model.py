"""Explicit DeepEval adapter for an already configured chat_json client.

Import this optional-dependency module only when constructing a model adapter.
Construction does not create a provider, open a repository, or send a request.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
import json
import re
from uuid import uuid4
from time import perf_counter, time

from deepeval.models import DeepEvalBaseLLM


class ModelCallError(RuntimeError):
    """Avoid SDK TypeError fallback causing an unplanned second model request."""


def call_failure_details(exc):
    """Structured facts only: don't persist provider error bodies or guess deadlines."""
    chain, visited = [], set()
    status = None
    current = exc
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        chain.append(type(current).__name__)
        code = getattr(current, 'status_code', None)
        if type(code) is int and 100 <= code <= 599:
            status = code
        current = current.__cause__ or current.__context__
    return {'error_type': type(exc).__name__, 'error_chain': chain, 'http_status': status}


class ExplicitBenchmarkModel(DeepEvalBaseLLM):
    def __init__(self, client, *, model_id, role, parameters, config_sha256, sink):
        if client is None or not callable(getattr(client, "chat_json", None)):
            raise ValueError("An explicit chat_json client is required")
        if getattr(client, "configured", True) is False:
            raise ValueError("Client is not configured")
        if not isinstance(model_id, str) or not model_id.strip() or role not in {"tested", "judge"}:
            raise ValueError("Explicit model identity and tested/judge role required")
        if not re.fullmatch(r"[0-9a-f]{64}", config_sha256):
            raise ValueError("Supply the hash of the resolved model configuration")
        allowed = {"temperature", "top_p", "max_tokens", "thinking_mode", "max_retries", "timeout"}
        if not isinstance(parameters, dict) or set(parameters) - allowed or not callable(sink):
            raise ValueError("Unsupported model parameters or missing durable event sink")
        # Preserve only explicit non-secret invocation parameters, not service URLs/keys.
        json.dumps(parameters, allow_nan=False)
        self.client, self.role = client, role
        self.model_id, self.parameters = model_id, deepcopy(parameters)
        self.config_sha256, self.sink = config_sha256, sink
        self._case = ContextVar("starter_case_" + uuid4().hex, default=None)
        self._metric = ContextVar("starter_metric_" + uuid4().hex, default={})
        self._metric_active = ContextVar("starter_metric_active_" + uuid4().hex, default=None)
        super().__init__(model=model_id)

    def load_model(self):
        return self.client

    def get_model_name(self):
        return self.model_id

    @contextmanager
    def for_case(self, case_id, request_id):
        if not case_id or not request_id:
            raise ValueError("Case/request identities are required for traceability")
        token = self._case.set({"case_id": case_id, "request_id": request_id})
        try:
            yield self
        finally:
            self._case.reset(token)

    @contextmanager
    def for_metric(self, *, metric, score_id, is_active=None, **link):
        token = self._metric.set({**link, 'metric': metric, 'score_id': score_id})
        active_token = self._metric_active.set(is_active)
        try:
            yield self
        finally:
            self._metric.reset(token)
            self._metric_active.reset(active_token)

    def generate(self, prompt, schema=None):
        active = self._metric_active.get()
        if active is not None and not active():
            raise ModelCallError('Metric already finalized; no further judge calls allowed')
        binding = self._case.get()
        if binding is None:
            raise ModelCallError("Bind the adapter with for_case before calling generate")
        if schema is None:
            raise ModelCallError("This adapter requires an explicit SDK output schema")
        if not isinstance(prompt, str):
            raise ModelCallError("SDK prompt must be text")
        document = schema.model_json_schema()
        hint = json.dumps(document, ensure_ascii=False, sort_keys=True)
        messages = [{"role": "user", "content": prompt}]
        call_id = uuid4().hex
        common = {**binding, **self._metric.get(), "call_id": call_id, "model_id": self.model_id,
                  "role": self.role, "config_sha256": self.config_sha256}
        started = perf_counter()
        self.sink({**common, "event": "started", "started_at_unix": time(), "prompt": prompt, "schema": document,
                   "prompt_utf8_bytes": len(prompt.encode('utf-8')),
                   "client_messages": messages, "response_schema_hint": hint,
                   "requested_parameters": self.parameters, "bypass_cache": True,
                   "observation_boundary": "chat_json API; client may add system prompt, retries and service overrides"})
        raw = None
        try:
            raw = self.model.chat_json(messages, hint, bypass_cache=True, **self.parameters)
            parsed = schema.model_validate_json(raw) if isinstance(raw, str) else schema.model_validate(raw)
        except Exception as exc:
            if active is None or active():
                self.sink({**common, "event": "failed", "raw_client_response": raw,
                           **call_failure_details(exc), "elapsed_seconds": perf_counter() - started,
                           "phase": "client" if raw is None else "schema_parse"})
            # Do not persist exception text, which may contain private service addresses.
            raise ModelCallError(f"{self.role} call failed; see event {call_id}") from exc
        if active is not None and not active():
            raise ModelCallError('Metric already finalized; late judge response discarded')
        self.sink({**common, "event": "completed", "raw_client_response": raw,
                   "elapsed_seconds": perf_counter() - started,
                   "parsed_response": parsed.model_dump(mode="json")})
        return parsed

    async def a_generate(self, prompt, schema=None):
        return await asyncio.to_thread(self.generate, prompt, schema)
