"""Local message intent HTTP: explicit inputs, safe receipts, single dispatch.

SDK acknowledgement is not execution/completion. Message IDs are frozen intent
identities, not fields checked in SDK ACK payload. GET never calls the bridge;
unknown and prior prepared receipts never authorize resubmission. Context content
comes from the existing resolver, whose case projection excludes gold; selectable
files do not provide a general gold-isolation guarantee.
"""
from __future__ import annotations

import copy
from typing import Literal

from fastapi import APIRouter
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .agent_messages import (
    AgentMessageStore, MessageContextChanged, MessageContextUnavailable,
    MessageInputRejected, SessionRemoved, MAX_MESSAGE_BYTES, MAX_REFERENCES,
)
from .message_bridge import validate_send_request


def _text(value, *, opaque=False):
    if (not value.strip() or '\0' in value or (opaque and value.strip() != value)):
        raise ValueError('Invalid message input')
    try:
        value.encode('utf-8')
    except UnicodeError:
        raise ValueError('Invalid message input') from None
    return value


class ContextSelection(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    reference: dict[str, str]
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$', min_length=64, max_length=64)

    @field_validator('reference')
    @classmethod
    def reference_shape(cls, value):
        shapes = {'goal': {'kind'}, 'file': {'kind', 'path'},
                  'result': {'kind', 'result_id'}, 'case': {'kind', 'result_id', 'case_id'},
                  'run_case': {'kind', 'run_id', 'case_id'}, 'task_runs': {'kind'}, 'iteration_tree': {'kind'}, 'brief': {'kind', 'session_id'}}
        if value.get('kind') not in shapes or set(value) != shapes[value['kind']]:
            raise ValueError('Invalid message context reference')
        for item in value.values():
            _text(item)
        return value


class MessageRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    session_id: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=MAX_MESSAGE_BYTES)
    contexts: list[ContextSelection] = Field(max_length=MAX_REFERENCES)
    request_key: str = Field(min_length=1, max_length=200)
    active_turn_behavior: Literal['interrupt', 'steer']

    @field_validator('session_id')
    @classmethod
    def session_identity(cls, value):
        return _text(value, opaque=True)

    @field_validator('request_key')
    @classmethod
    def exact_key(cls, value):
        # Local recovery key: whitespace is significant; never trim it.
        return _text(value)

    @field_validator('text')
    @classmethod
    def original_text(cls, value):
        _text(value)
        if len(value.encode('utf-8')) > MAX_MESSAGE_BYTES:
            raise ValueError('Message text exceeds current budget')
        return value


def project_message(record):
    """White-list local binding fields; never return model body or snapshots."""
    return copy.deepcopy({key: record[key] for key in (
        'id', 'task_id', 'actor', 'session_id', 'agent_id', 'message_id', 'text', 'contexts',
        'request_key', 'active_turn_behavior', 'body_format', 'input_sha256', 'body_sha256',
        'status', 'envelope', 'prepared_at', 'started_at', 'finished_at')})


def _prewrite_refusal(code, error, request_key, *, status_code=422):
    return JSONResponse({'error': error, 'code': code, 'request_key': request_key}, status_code=status_code)


class _ClosedMessageRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def closed(request):
            try:
                return await handler(request)
            except RequestValidationError as exc:
                # Reject free config/text extensions without echoing their input.
                key = exc.body.get('request_key') if type(exc.body) is dict else None
                if type(key) is str and 0 < len(key) <= 200:
                    try:
                        _text(key)
                    except ValueError:
                        pass
                    else:
                        return _prewrite_refusal('MESSAGE_INPUT_REJECTED_PREWRITE',
                                                 'Message input rejected before write', key)
                return JSONResponse({'error': 'Invalid message request'}, status_code=422)
        return closed


class MessageAPI:
    def __init__(self, repository, workspace, config, *, actor, bridge):
        self.actor = _text(actor, opaque=True)
        server_config = copy.deepcopy(config.get('agent_bridge'))
        if bridge is None and server_config is not None:
            from .message_bridge import AgentMessageBridgeAdapter
            bridge = AgentMessageBridgeAdapter(server_config)
        self.bridge = bridge
        self.store = AgentMessageStore(repository, workspace_ids=[workspace['id']])
        self._preflight = getattr(bridge, 'validate_request', None)
        if not callable(self._preflight):
            # Internal synthetic bridges without preflight use the same pure
            # serializer with real server config when supplied. With no config,
            # the fixed synthetic URL is ONLY a fixture budget, not remote health
            # or a guarantee about some unconfigured external implementation.
            frozen_config = server_config if server_config is not None else {'url': 'ws://injected.invalid'}
            self._preflight = lambda command: validate_send_request(command, frozen_config)

    def _validate_preflight(self, command):
        # Only this server-owned serializer boundary is masked. Scope/source
        # conflicts raised by prepare retain their separate domain semantics.
        try:
            self._preflight(command)
        except (ValueError, TypeError, RecursionError):
            raise MessageInputRejected('Invalid message request size') from None

    @property
    def configured(self):
        return callable(getattr(self.bridge, 'call', None))

    def router(self):
        router = APIRouter(route_class=_ClosedMessageRoute)

        @router.post('/api/tasks/{task_id}/agent-messages')
        def send(task_id: str, command: MessageRequest):
            # This read verifies real Task/workspace/actor scope before config or
            # input preparation. Prior receipts bypass current source resolution.
            prior = next((record for record in self.store.list(task_id, actor=self.actor)
                          if record['request_key'] == command.request_key), None)
            if prior is not None:
                matched = self.store.prepare(task_id, actor=self.actor, **command.model_dump())
                return project_message(matched)
            if not self.configured:
                return _prewrite_refusal('MESSAGE_BRIDGE_UNCONFIGURED_PREWRITE',
                                         'Message bridge is not configured', command.request_key)
            try:
                intent = self.store.prepare(task_id, actor=self.actor, preflight=self._validate_preflight,
                                            **command.model_dump())
            except MessageContextChanged:
                # Only this dedicated pre-INSERT refusal proves no intent write
                # or dispatch. Ordinary conflicts keep their uncertain contract.
                return _prewrite_refusal('MESSAGE_CONTEXT_CHANGED_PREWRITE',
                                         'Selected message context changed before write',
                                         command.request_key, status_code=409)
            except MessageContextUnavailable:
                return _prewrite_refusal('MESSAGE_CONTEXT_UNAVAILABLE_PREWRITE',
                                         'Selected message context unavailable before write', command.request_key)
            except SessionRemoved:
                return _prewrite_refusal('AGENT_REMOVED_PREWRITE', '这个 Agent 已从工作台移除，不能再发消息',
                                         command.request_key)
            except MessageInputRejected:
                return _prewrite_refusal('MESSAGE_INPUT_REJECTED_PREWRITE',
                                         'Message input rejected before write', command.request_key)
            if intent['status'] == 'prepared':
                intent = self.store.attempt(task_id, intent['id'], actor=self.actor, bridge=self.bridge)
            return project_message(intent)

        @router.get('/api/tasks/{task_id}/agent-messages')
        def list_messages(task_id: str):
            return [project_message(record) for record in self.store.list(task_id, actor=self.actor)]

        @router.get('/api/tasks/{task_id}/agent-messages/{intent_id}')
        def get_message(task_id: str, intent_id: str):
            return project_message(self.store.get(task_id, intent_id, actor=self.actor))

        return router
