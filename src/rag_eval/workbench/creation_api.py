"""Server-owned profile selection and safe HTTP projection of creation intents.

The app's registered local workspace is the authorization scope, even when its
remote configuration is temporarily absent. Tasks have no owner column; actor
ownership applies to intents. SQLite remains authoritative for at-most-once
dispatch. Created is an observed reference, not proof of creation causality,
effective mode/effort, or future idleness. Nothing here selects a session or sends.
"""
from __future__ import annotations

from typing import Literal

import copy

from fastapi import APIRouter
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .agent_creations import AgentCreationStore
from .creation_bridge import validate_create_command


def _validation_command():
    # Pure validation only: these placeholders are never persisted or dispatched.
    return {'op': 'createAgent', 'workspaceId': 'validation', 'expectedCwd': '/',
            'agentId': '00000000-0000-4000-8000-000000000000',
            'idempotencyKey': 'validation', 'config': {'provider': 'validation/model'}}


def _choice(value):
    if type(value) is not str or value.strip() != value:
        raise ValueError('Invalid creation choice')
    # Matches the Node title/opaque UTF-16 budget and whitespace rules, plus
    # Store's stricter Python whitespace rule. Preserve the accepted original.
    validate_create_command({**_validation_command(), 'title': value})
    return value


class CreationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    profile_id: str = Field(strict=True, min_length=1, max_length=200)
    title: str | None = Field(default=None, strict=True, max_length=200)
    request_key: str = Field(strict=True, min_length=1, max_length=200)
    role: Literal['analyst', 'implementer'] | None = None
    hypothesis_id: str | None = Field(default=None, strict=True, min_length=1, max_length=200)
    candidate_id: str | None = Field(default=None, strict=True, pattern='^[0-9a-f]{32}$')

    @field_validator('profile_id', 'title')
    @classmethod
    def choice(cls, value):
        return None if value is None else _choice(value)

    @field_validator('request_key')
    @classmethod
    def key(cls, value):
        if not value.strip() or '\0' in value:
            raise ValueError('Invalid creation request key')
        # This is a local receipt key, not the SDK key. Spaces are significant.
        return value


def _binding(raw, cwd):
    if (type(raw) is not dict or set(raw) != {'workspaceId', 'profiles'}
            or type(raw['profiles']) is not dict):
        raise ValueError('Invalid server creation configuration')
    base = {**_validation_command(), 'workspaceId': raw['workspaceId'], 'expectedCwd': cwd}
    # Validate remote identity/path even if there are currently no profiles.
    validate_create_command(base)
    profiles = {}
    for profile_id, config in raw['profiles'].items():
        _choice(profile_id)
        profiles[profile_id] = validate_create_command({**base, 'config': config})['config']
    return {'workspaceId': raw['workspaceId'], 'profiles': profiles}


def project_creation(record):
    """Consume Store's validated receipt; exclude command and raw snapshots."""
    public = {key: record[key] for key in (
        'id', 'task_id', 'actor', 'profile_id', 'title', 'request_key', 'agent_id', 'status',
        'prepared_at', 'started_at', 'finished_at', 'session_id')}
    public.update({k: record[k] for k in ('role', 'hypothesis_id', 'candidate_id') if record.get(k) is not None})
    envelope = record['envelope']
    public['envelope'] = None
    if envelope is not None:
        result = envelope['result']
        safe = {key: result[key] for key in ('status', 'code', 'cause', 'agentId', 'ready', 'readiness')
                if key in result}
        if result['status'] == 'created':
            safe['agent'] = {key: result['agent'][key] for key in ('id', 'title', 'status', 'provider', 'model')
                             if key in result['agent']}
        elif result['status'] == 'unknown' and 'observed' in result:
            # Producer validation already requires a complete UUID. The raw
            # observed Agent (including cwd) is deliberately excluded.
            safe['observed'] = {'handleId': result['observed']['handleId']}
        public['envelope'] = {'result': safe, 'release': dict(envelope['release'])}
    return public


class _ClosedCreationRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def closed(request):
            try:
                return await handler(request)
            except RequestValidationError:
                # Do not echo rejected browser config or arbitrary extra fields.
                return JSONResponse({'error': 'Invalid creation request'}, status_code=422)
        return closed


class CreationAPI:
    def __init__(self, repository, workspace, config, *, actor, bridge):
        self.repository = repository
        self.workspace_id = workspace['id']
        self.actor = _choice(actor)
        binding = _binding(config['agent_creation'], workspace['location']) if 'agent_creation' in config else None
        self._profiles = []
        if binding is not None:
            for profile_id, profile in binding['profiles'].items():
                provider, model = profile['provider'].split('/', 1)
                self._profiles.append({'id': profile_id, 'provider': provider, 'model': model,
                                       **{key: profile[key] for key in ('modeId', 'thinkingOptionId') if key in profile}})
            if bridge is None and config.get('agent_bridge') is not None:
                from .creation_bridge import AgentCreationBridgeAdapter
                bridge = AgentCreationBridgeAdapter(config['agent_bridge'])
        self.bridge = bridge
        self.store = AgentCreationStore(repository, {workspace['id']: binding} if binding is not None else {})

    @property
    def configured(self):
        # Configuration ability, not a daemon health or Agent readiness claim.
        return bool(self._profiles) and callable(getattr(self.bridge, 'call', None))

    @property
    def profiles(self):
        return copy.deepcopy(self._profiles)

    def _task_scope(self, task_id):
        with self.repository.connect() as conn:
            task = self.repository._require(conn, 'tasks', task_id)
            if task['workspace_id'] != self.workspace_id:
                raise ValueError('Task is not in the registered creation workspace')

    def router(self):
        router = APIRouter(route_class=_ClosedCreationRoute)

        @router.post('/api/tasks/{task_id}/agent-creations')
        def create(task_id: str, command: CreationRequest):
            self._task_scope(task_id)
            intent = self.store.prepare(task_id, actor=self.actor, **command.model_dump())
            # Prior receipts remain available even if configuration/bridge was
            # removed. Prepared is the only state eligible for initial dispatch.
            if intent['status'] == 'prepared':
                if not self.configured:
                    raise ValueError('Creation bridge is not configured')
                intent = self.store.attempt(task_id, intent['id'], actor=self.actor, bridge=self.bridge)
            return project_creation(intent)

        @router.get('/api/tasks/{task_id}/agent-creations')
        def list_creations(task_id: str):
            self._task_scope(task_id)
            return [project_creation(record) for record in self.store.list(task_id, actor=self.actor)]

        @router.get('/api/tasks/{task_id}/agent-creations/{intent_id}')
        def get_creation(task_id: str, intent_id: str):
            self._task_scope(task_id)
            return project_creation(self.store.get(task_id, intent_id, actor=self.actor))

        return router
