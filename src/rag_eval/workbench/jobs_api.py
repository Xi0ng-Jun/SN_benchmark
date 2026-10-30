"""Registered Job intentions plus the explicit opt-in execution adapter."""
from typing import Annotated, Literal
import json

from fastapi import APIRouter, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field

from ..model_budget import ModelPricingError
from ..artifact_store import ArtifactStore
from .evaluation_jobs import EvaluationJobStore, JobRejected
# Re-exported: the spec builder lives beside the store so CLI paths need not load this HTTP module.
from .job_execution import ExecutionRejected, JobExecutionStore, build_execution_spec  # noqa: F401
from .repository import Conflict, UnknownIdentity


class _Command(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class PrepareJob(_Command):
    plan_id: str = Field(min_length=1, max_length=200)
    revision: str = Field(min_length=1, max_length=200)
    expected_content_hash: str = Field(pattern='^[0-9a-f]{64}$')
    request_key: str = Field(min_length=1, max_length=200)


class CancelJob(_Command):
    expected_state_version: int = Field(ge=1)
    request_key: str = Field(min_length=1, max_length=200)


class RunJob(_Command):
    expected_state_version: int = Field(ge=1)
    preparation_id: str = Field(pattern='^[0-9a-f]{64}$')
    budget: dict
    request_key: str = Field(min_length=1, max_length=200)


class CapturePreparation(_Command):
    expected_state_version: int = Field(ge=1)
    request_key: str = Field(min_length=1, max_length=200)


class CancelExecution(_Command):
    request_key: str = Field(min_length=1, max_length=200)


class JobReceipt(_Command):
    operation: Literal['prepare', 'cancel']
    request_key: str = Field(min_length=1, max_length=200)


class _ClosedJobRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def closed(request):
            try:
                return await handler(request)
            except RequestValidationError:
                return JSONResponse({'error': 'Invalid Job request'}, status_code=422)
            except (JobRejected, ExecutionRejected, Conflict) as error:
                return JSONResponse({'error': 'Job record unavailable; check the original receipt and current record',
                                     'code': 'JOB_READ_REJECTED'},
                                    status_code=409 if isinstance(error, Conflict) else 422)
        return closed


class JobAPI:
    def __init__(self, repository, workspace_id, *, actor, execution_config=None,
                 preparation_config=None, worktree_service=None):
        self.repository = repository
        self.workspace_id = workspace_id
        self.actor = actor
        self.store = EvaluationJobStore(repository)
        self.execution = JobExecutionStore(repository)
        self.execution_config = execution_config
        self.preparation_config = preparation_config
        self.worktree_service = worktree_service

    def _scope(self, task_id):
        with self.repository.connect() as conn:
            try:
                task = self.repository._require(conn, 'tasks', task_id)
            except UnknownIdentity:
                raise JobRejected('Unknown Job task identity') from None
            if task['workspace_id'] != self.workspace_id:
                raise JobRejected('Task is outside the registered Job workspace')

    def _command(self, task_id, operation, key, call):
        try:
            self._scope(task_id)
            record = call()
        except ModelPricingError as error:
            return JSONResponse({'error': str(error), 'code': 'MODEL_PRICING_REJECTED',
                                 'operation': operation, 'request_key': key}, status_code=422)
        except (Conflict, JobRejected, ExecutionRejected) as error:
            status = 409 if isinstance(error, Conflict) else 422
            return JSONResponse({'error': 'Job command rejected; check the original receipt and current record',
                                 'code': 'JOB_COMMAND_REJECTED', 'operation': operation,
                                 'request_key': key}, status_code=status)
        return {'operation': operation, 'request_key': key, 'record': record}

    def _prepared_task_worktree(self, task_id, job_id, preparation_id):
        """Recover capture provenance; current Task selection cannot label it."""
        from ..identity import fingerprint
        from .hypothesis_worktrees import historical_bindings
        receipt = next((r for r in self.store.preparations(task_id, job_id)
                        if r['preparation']['preparation_id'] == preparation_id), None)
        if receipt is None:
            raise ExecutionRejected('Verified preparation is required')
        source_id = receipt['preparation']['source']['artifact_ref']['id']
        with self.repository.connect() as conn:
            bindings = {(b['worktree_path'], b['paseo_workspace_id'])
                        for b in historical_bindings(conn, task_id)}
        directory = self.repository.root / 'task-captures'
        if directory.is_symlink():
            raise ExecutionRejected('Task source capture association is unavailable')
        matches = {}
        for path in directory.glob('*.json'):
            try:
                if path.is_symlink():
                    continue
                association = json.loads(path.read_text())
                if (type(association) is not dict
                        or set(association) != {'task_id', 'worktree_path', 'paseo_workspace_id', 'source_ref'}
                        or association['task_id'] != task_id or association['source_ref'] != source_id
                        or path.stem != fingerprint(association)):
                    continue
                identity = (association['worktree_path'], association['paseo_workspace_id'])
                if identity in bindings:
                    matches[identity] = {k: association[k] for k in
                                         ('task_id', 'worktree_path', 'paseo_workspace_id')}
            except (OSError, ValueError, TypeError):
                continue
        if len(matches) != 1:
            raise ExecutionRejected('Task source capture association is unavailable or ambiguous')
        return next(iter(matches.values()))

    def router(self):
        router = APIRouter(route_class=_ClosedJobRoute)

        @router.post('/api/tasks/{task_id}/jobs')
        def prepare(task_id: str, command: PrepareJob):
            return self._command(task_id, 'prepare', command.request_key,
                                 lambda: self.store.prepare(task_id=task_id, actor=self.actor, **command.model_dump()))

        @router.get('/api/tasks/{task_id}/jobs')
        def listing(task_id: str):
            self._scope(task_id)
            return self.store.list(task_id)

        @router.get('/api/tasks/{task_id}/jobs/receipt')
        def receipt(task_id: str, query: Annotated[JobReceipt, Query()]):
            self._scope(task_id)
            return {'operation': query.operation, 'request_key': query.request_key,
                    'record': self.store.receipt(task_id, actor=self.actor, **query.model_dump())}

        @router.get('/api/tasks/{task_id}/jobs/{job_id}')
        def read(task_id: str, job_id: str):
            self._scope(task_id)
            return self.store.get(task_id, job_id)

        @router.get('/api/tasks/{task_id}/jobs/{job_id}/preparations')
        def preparations(task_id: str, job_id: str):
            self._scope(task_id)
            return self.store.preparations(task_id, job_id)

        @router.post('/api/tasks/{task_id}/jobs/{job_id}/preparations')
        def capture_preparation(task_id: str, job_id: str, command: CapturePreparation):
            def call():
                if not isinstance(self.preparation_config, dict) or self.worktree_service is None:
                    raise ExecutionRejected('Job input/source preparation is not configured')
                root = self.preparation_config.get('artifact_root')
                inputs = self.preparation_config.get('inputs')
                if not isinstance(root, str) or not root.strip() or not isinstance(inputs, dict):
                    raise ExecutionRejected('Job input/source preparation is not configured')
                job = self.store.get(task_id, job_id)
                prior = self.store.preparation_receipt(task_id, actor=self.actor, request_key=command.request_key)
                if prior is not None:
                    original = prior['job_snapshot']
                    if original['job_id'] != job_id or original['state_version'] != command.expected_state_version:
                        raise Conflict('Preparation request key changed')
                    return prior
                if job['state'] != 'not_dispatched' or job['state_version'] != command.expected_state_version:
                    raise Conflict('Job changed before preparation')
                input_id = job['plan_snapshot']['content']['input']['input_id']
                binding = inputs.get(input_id)
                if (not isinstance(binding, dict) or
                        set(binding) != {'bundle_ref', 'index_ref'}):
                    raise ExecutionRejected('No trusted artifact binding for this Job input')
                artifact_store = ArtifactStore(root)
                if artifact_store.root != self.worktree_service.store.root:
                    raise ExecutionRejected('Preparation artifact store does not match Task worktree store')
                from ..sn_source_capture import SourceChangedDuringCapture
                try:
                    sn_ref = self.worktree_service.capture_source(task_id)
                except (ValueError, OSError, SourceChangedDuringCapture):
                    raise ExecutionRejected('Task source capture is unavailable') from None
                return self.store.capture_inputs_source(
                    task_id=task_id, job_id=job_id,
                    expected_state_version=command.expected_state_version,
                    artifact_store=artifact_store, bundle_ref=binding['bundle_ref'],
                    index_ref=binding['index_ref'], sn_ref=sn_ref,
                    actor=self.actor, request_key=command.request_key)
            return self._command(task_id, 'capture', command.request_key, call)

        @router.post('/api/tasks/{task_id}/jobs/{job_id}/cancel')
        def cancel(task_id: str, job_id: str, command: CancelJob):
            def call():
                result = self.store.cancel(task_id=task_id, job_id=job_id,
                                           actor=self.actor, **command.model_dump())
                execution = self.execution.latest_for_job(task_id, job_id)
                if execution is not None and not execution['terminal']:
                    self.execution.cancel(task_id, execution['execution_id'], actor=self.actor,
                                          request_key='job-cancel:' + command.request_key)
                return result
            return self._command(task_id, 'cancel', command.request_key,
                                 call)

        @router.post('/api/tasks/{task_id}/jobs/{job_id}/run')
        def run(task_id: str, job_id: str, command: RunJob):
            def call():
                prior = self.execution.run_receipt(task_id, job_id, command.expected_state_version,
                    command.preparation_id, command.budget, actor=self.actor, request_key=command.request_key)
                if prior is not None:
                    return prior
                execution_config = dict(self.execution_config or {})
                if execution_config.get('mode') == 'notebook' and self.worktree_service is not None:
                    execution_config['task_worktree'] = self._prepared_task_worktree(
                        task_id, job_id, command.preparation_id)
                spec = build_execution_spec(self.store, task_id, job_id, command.preparation_id,
                                            execution_config, request_key=command.request_key,
                                            budget=command.budget)
                spec['budget'] = command.budget
                return self.execution.run(task_id, job_id, command.expected_state_version,
                                          execution_spec=spec, actor=self.actor,
                                          request_key=command.request_key)
            return self._command(task_id, 'run', command.request_key, call)

        @router.get('/api/execution-profile')
        def execution_profile():
            config = self.execution_config or {}
            return dict(mode=config.get('mode'), models=config.get('model_names', []),
                        max_limit_cny=config.get('max_limit_cny', 10),
                        input_ids=list((self.preparation_config or {}).get('inputs', {})))

        @router.get('/api/tasks/{task_id}/jobs/{job_id}/execution')
        def latest_execution(task_id: str, job_id: str):
            self._scope(task_id)
            self.store.get(task_id, job_id)
            return self.execution.latest_for_job(task_id, job_id)

        @router.get('/api/tasks/{task_id}/jobs/{job_id}/executions/{execution_id}')
        def execution_status(task_id: str, job_id: str, execution_id: str):
            self._scope(task_id)
            record = self.execution.get(task_id, execution_id)
            if record['job_id'] != job_id:
                raise ExecutionRejected('Execution Job ownership changed')
            return record

        @router.post('/api/tasks/{task_id}/jobs/{job_id}/executions/{execution_id}/cancel')
        def execution_cancel(task_id: str, job_id: str, execution_id: str, command: CancelExecution):
            def call():
                record = self.execution.get(task_id, execution_id)
                if record['job_id'] != job_id:
                    raise ExecutionRejected('Execution Job ownership changed')
                return self.execution.cancel(task_id, execution_id, actor=self.actor,
                                             request_key=command.request_key)
            return self._command(task_id, 'execution-cancel', command.request_key, call)
        return router
