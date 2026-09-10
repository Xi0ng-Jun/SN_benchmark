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

from deepeval.models import DeepEvalBaseLLM


class ModelCallError(RuntimeError):
    """Avoid SDK TypeError fallback causing an unplanned second model request."""


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

    def generate(self, prompt, schema=None):
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
        common = {**binding, "call_id": call_id, "model_id": self.model_id,
                  "role": self.role, "config_sha256": self.config_sha256}
        self.sink({**common, "event": "started", "prompt": prompt, "schema": document,
                   "client_messages": messages, "response_schema_hint": hint,
                   "requested_parameters": self.parameters, "bypass_cache": True,
                   "observation_boundary": "chat_json API; client may add system prompt, retries and service overrides"})
        raw = None
        try:
            raw = self.model.chat_json(messages, hint, bypass_cache=True, **self.parameters)
            parsed = schema.model_validate_json(raw) if isinstance(raw, str) else schema.model_validate(raw)
        except Exception as exc:
            self.sink({**common, "event": "failed", "raw_client_response": raw,
                       "error_type": type(exc).__name__,
                       "phase": "client" if raw is None else "schema_parse"})
            # Do not persist exception text, which may contain private service addresses.
            raise ModelCallError(f"{self.role} call failed; see event {call_id}") from exc
        self.sink({**common, "event": "completed", "raw_client_response": raw,
                   "parsed_response": parsed.model_dump(mode="json")})
        return parsed

    async def a_generate(self, prompt, schema=None):
        return await asyncio.to_thread(self.generate, prompt, schema)
