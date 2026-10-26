"""Same-origin HTTP adapter for explicit imports and durable review records."""
from __future__ import annotations

import json
import hmac
from contextlib import asynccontextmanager
from pathlib import Path
import secrets
from typing import Annotated, Literal

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from .ingest import import_result, read_case, read_document, read_result, read_snapshot
from .configuration import input_catalog, task_worktree_services
from .result_comparison import compare_task_results
from .plan_inputs import NotebookPlanInputs
from .evaluation_plans import PlanDraftStore
from .context import resolve_context
from .repository import Conflict, WorkbenchRepository, now
from .bridge_adapter import AgentBridgeAdapter
from .sessions import TaskSessionReader
from .candidates import CandidateStore
from .timeline import TaskTimelineReader
from .sourced_candidates import SourcedCandidates
from .candidate_receipts import CandidateReceipts
from .creation_api import CreationAPI
from .message_api import MessageAPI
from .jobs_api import JobAPI


class Command(BaseModel):
    model_config = ConfigDict(extra='forbid')


class HypothesisDecision(Command):
    decision: str = Field(pattern='^(approve|reject)$')
    note: str = Field(default='', max_length=4000)


class HypothesisCheck(Command):
    baseline_run: str = Field(min_length=1, max_length=200)
    candidate_run: str = Field(min_length=1, max_length=200)


class HypothesisConclusion(Command):
    effect: str = Field(max_length=100)
    cause: str = Field(max_length=100)
    note: str = Field(default='', max_length=4000)


class HypothesisDraft(Command):
    card: dict


class Session(Command):
    token: str = Field(max_length=200)


class NewTask(Command):
    workspace_id: str
    title: str = Field(min_length=1, max_length=200)
    goal: str = Field(default='', max_length=10000)
    request_key: str = Field(min_length=1, max_length=200)


class Attach(Command):
    source_id: str


class BindWorktree(Command):
    worktree_id: str = Field(min_length=1, max_length=200)


class ComparisonRead(Command):
    left_result_id: str = Field(pattern='^[0-9a-f]{64}$')
    right_result_id: str = Field(pattern='^[0-9a-f]{64}$')


class SavePlan(Command):
    title: str = Field(min_length=1, max_length=500)
    input_id: str = Field(min_length=1, max_length=200)
    expected_bundle_id: str = Field(pattern='^[0-9a-f]{64}$')
    case_ids: list[str] = Field(min_length=1)
    mode: Literal['chunk', 'reasoning']
    plan_id: str | None = None
    base_revision: str | None = None
    request_key: str = Field(min_length=1, max_length=200)


class AttachSession(Command):
    agent_id: str = Field(min_length=1, max_length=200)
    request_key: str = Field(min_length=1, max_length=200)


class ContextPreview(Command):
    reference: dict[str, str]


class Review(Command):
    result_id: str
    case_id: str
    judgment: Literal['confirmed', 'rejected', 'undecided']
    reason: str = Field(min_length=1, max_length=10000)
    evidence: list[str] = Field(max_length=1000)
    base_revision: str | None
    request_key: str = Field(min_length=1, max_length=200)


class NoteContext(Command):
    result_id: str
    case_id: str


class Note(Command):
    text: str = Field(min_length=1, max_length=10000)
    context: NoteContext | None = None
    request_key: str = Field(min_length=1, max_length=200)


class CandidateDecision(Command):
    judgment: Literal['confirmed', 'rejected', 'undecided']
    reason: str = Field(min_length=1, max_length=10000)
    base_revision: str | None
    request_key: str = Field(min_length=1, max_length=200)


class ReceiptRead(Command):
    request_key: str = Field(min_length=1, max_length=200)


class TimelineCursor(Command):
    epoch: str = Field(min_length=1, max_length=512)
    seq: StrictInt = Field(ge=0, le=9007199254740991)


class TimelineRead(Command):
    direction: Literal['tail', 'before', 'after'] | None = None
    cursor: TimelineCursor | None = None


class SourcedCandidate(Command):
    locator: dict
    kind: Literal['finding', 'direction', 'next_step']
    text: str = Field(min_length=1, max_length=10000)
    request_key: str = Field(min_length=1, max_length=200)


def local_access_token(root):
    path = Path(root) / 'access-token'
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        with path.open('x') as stream:
            path.chmod(0o600)
            stream.write(secrets.token_urlsafe(32))
    except FileExistsError:
        pass
    return path.read_text().strip()


def create_app(state_root, config, *, actor='local-reviewer', access_token=None, static_directory=None,
               agent_bridge=None, agent_creation_bridge=None, agent_message_bridge=None,
               hypothesis_worktree_creator=None):
    repository = WorkbenchRepository(state_root)
    worktree_service, registered_worktrees = task_worktree_services(repository, config)
    token = access_token or local_access_token(state_root)
    workspace = repository.register_workspace(config['workspace']['title'], config['workspace']['location'])
    creation_api = CreationAPI(repository, workspace, config, actor=actor, bridge=agent_creation_bridge)
    message_api = MessageAPI(repository, workspace, config, actor=actor, bridge=agent_message_bridge)
    if agent_bridge is None and config.get('agent_bridge') is not None:
        agent_bridge = AgentBridgeAdapter(config['agent_bridge'])
    session_reader = TaskSessionReader(repository, agent_bridge)
    candidate_receipts = CandidateReceipts(repository)
    candidate_store = CandidateStore(repository)
    timeline_reader = TaskTimelineReader(repository, agent_bridge)
    sourced_candidates = SourcedCandidates(repository, timeline_reader)
    sources = {s['id']: s for s in config.get('sources', [])}
    registered_inputs = input_catalog(config)
    plan_inputs = NotebookPlanInputs({key: entry['bundle'] for key, entry in registered_inputs.items()})
    plans = PlanDraftStore(repository, plan_inputs)
    if len(sources) != len(config.get('sources', [])):
        raise ValueError('Duplicate catalog source IDs')
    app = FastAPI(title='SN workbench', docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(creation_api.router())
    app.include_router(message_api.router())
    job_api = JobAPI(repository, workspace['id'], actor=actor,
                     execution_config=config.get('job_execution'),
                     preparation_config=config.get('job_preparation'),
                     worktree_service=worktree_service)
    app.include_router(job_api.router())
    from .run_views import RunViews
    views = RunViews.from_config(config.get('run_views'), base=Path.cwd(), worktree_service=worktree_service,
                                 execution_enabled=isinstance(config.get('job_execution'), dict),
                                 execution_config=config.get('job_execution'),
                                 execution_records=lambda: job_api.execution.records(workspace['id']))
    from .iterations_api import IterationAPI
    iteration_api = IterationAPI(repository, workspace, worktree_service, plans=plans, job_api=job_api,
                                 actor=actor, config=config, run_views=views, creator=hypothesis_worktree_creator)
    app.include_router(iteration_api.router())

    def evaluated_count(task_id):
        found = [i for i in iteration_api.service.iterations() if i['task_id'] == task_id]
        return sum(c['state'] == 'evaluated' for c in iteration_api.service.iteration(found[0]['id'])['candidates']) \
            if found else None
    creation_api.store.evaluated_count = evaluated_count
    creation_api.store.candidate_state = lambda candidate_id: iteration_api.service.candidate(candidate_id)['state']
    app.include_router(views.router())
    message_api.store.run_views = views

    from .loop import RoundService
    from .loop_services import RoundOperations
    from .loop_api import RoundAPI, RoundManager
    round_operations = RoundOperations(iteration_api, creation_api, message_api, session_reader)
    round_service = RoundService(repository, round_operations)

    def round_allowed(iteration_id):
        return iteration_api.writable and iteration_id in {
            row['id'] for row in iteration_api.service.iterations(workspace['id'])}

    round_manager = RoundManager(round_service, allowed=round_allowed)
    app.include_router(RoundAPI(iteration_api, round_service, round_manager).router())
    app.state.round_service = round_service
    app.state.round_manager = round_manager

    @asynccontextmanager
    async def lifespan(app):
        round_manager.start()
        try:
            yield
        finally:
            round_manager.close()

    app.router.lifespan_context = lifespan

    @app.post('/api/tasks/{task_id}/worktree')
    def bind_worktree(task_id: str, command: BindWorktree):
        entry = registered_worktrees.get(command.worktree_id)
        if entry is None:
            return JSONResponse({'error': 'Worktree is not registered'}, status_code=404)
        return worktree_service.bind(task_id, Path(entry['path']),
                                     paseo_workspace_id=entry.get('paseo_workspace_id'))

    @app.get('/api/tasks/{task_id}/worktree')
    def read_worktree(task_id: str):
        binding = worktree_service.binding(task_id)
        return {'binding': None} if binding is None else worktree_service.read(task_id)

    @app.middleware('http')
    async def access(request: Request, call_next):
        if request.url.path.startswith('/api/') and request.url.path != '/api/session':
            supplied = request.headers.get('Authorization', '').removeprefix('Bearer ') or request.cookies.get('sn_workbench_access', '')
            if not hmac.compare_digest(supplied.encode(), token.encode()):
                return JSONResponse({'error': '需要工作台访问链接或访问码'}, status_code=401)
            if request.method not in {'GET', 'HEAD'}:
                origin = request.headers.get('origin')
                if request.headers.get('X-Workbench') != '1' or (origin and origin != str(request.base_url).rstrip('/')):
                    return JSONResponse({'error': '拒绝跨站写请求'}, status_code=403)
        return await call_next(request)

    @app.exception_handler(Conflict)
    async def conflict(_request, exc):
        return JSONResponse({'error': str(exc)}, status_code=409)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        # Job-specific domain errors are handled by its router. A remaining
        # ValueError is an internal failure; preserve it for logs/TestClient
        # and let the normal server boundary emit a fixed, safe 500 response.
        parts = request.url.path.split('/')
        if len(parts) >= 5 and parts[1:3] == ['api', 'tasks'] and parts[4] == 'jobs':
            raise exc
        return JSONResponse({'error': str(exc)}, status_code=422)

    @app.exception_handler(FileNotFoundError)
    async def missing(request, exc):
        parts = request.url.path.split('/')
        if len(parts) >= 5 and parts[1:3] == ['api', 'tasks'] and parts[4] == 'jobs':
            raise exc
        return JSONResponse({'error': '所需本地工件不可用；原始记录未更改'}, status_code=404)

    @app.post('/api/session')
    def session(command: Session):
        if not hmac.compare_digest(command.token.encode(), token.encode()):
            return JSONResponse({'error': '访问码无效'}, status_code=401)
        response = JSONResponse({'actor': actor})
        response.set_cookie('sn_workbench_access', token, httponly=True, samesite='strict')
        return response

    @app.get('/api/bootstrap')
    def bootstrap():
        return dict(workspaces=repository.workspaces(), actor=actor,
                    sources=[{k: s[k] for k in ('id', 'title', 'description')} for s in sources.values()],
                    inputs=[{'id': key, 'title': value.get('title', key),
                             'description': value.get('description', '')} for key, value in registered_inputs.items()],
                    creation_profiles=creation_api.profiles,
                    execution_worktrees=[{'id': identity, 'title': entry.get('title', identity),
                                         'path': str(entry['path'])}
                                        for identity, entry in registered_worktrees.items()],
                    capabilities=dict(result_import=True, reviews=True, notes=True, agent=False,
                                      agent_create=creation_api.configured,
                                      agent_send=message_api.configured,
                                      agent_read=agent_bridge is not None, result_comparison=True,
                                      plan_drafts=True, job_registration=True,
                                      hypothesis_worktrees=tree_creation_enabled,
                                      execution=isinstance(config.get('job_execution'), dict)))

    @app.get('/api/inputs/{input_id}')
    def input_description(input_id: str):
        return plan_inputs.describe(input_id)

    @app.get('/api/tasks/{task_id}/plans')
    def plan_list(task_id: str):
        return plans.list(task_id)

    # Literal receipt route precedes the plan identity route.
    @app.get('/api/tasks/{task_id}/plans/receipt')
    def plan_receipt(task_id: str, request_key: str):
        return {'request_key': request_key, 'record': plans.receipt(task_id, actor=actor, request_key=request_key)}

    @app.get('/api/tasks/{task_id}/plans/{plan_id}')
    def plan_revision(task_id: str, plan_id: str, revision: str | None = None):
        return plans.get(task_id, plan_id, revision)

    @app.post('/api/tasks/{task_id}/plans')
    def save_plan(task_id: str, command: SavePlan):
        try:
            saved = plans.save(task_id=task_id, actor=actor, **command.model_dump())
            return {'request_key': command.request_key, 'record': saved}
        except (Conflict, ValueError, FileNotFoundError) as error:
            # These exceptions leave the Store write transaction rolled back.
            # The fixed code is a response to this command, not proof that its
            # key never existed. Generic transport/proxy errors lack this code.
            status = 409 if isinstance(error, Conflict) else 404 if isinstance(error, FileNotFoundError) else 422
            return JSONResponse({'error': str(error), 'code': 'PLAN_SAVE_REJECTED',
                                 'request_key': command.request_key}, status_code=status)

    @app.post('/api/tasks')
    def create_task(command: NewTask):
        return repository.create_task(command.workspace_id, command.title, command.goal, request_key=command.request_key)

    @app.get('/api/tasks/{task_id}')
    def task(task_id: str):
        return repository.task(task_id)

    @app.post('/api/tasks/{task_id}/sessions')
    def attach_session(task_id: str, command: AttachSession):
        return repository.attach_session(task_id, command.agent_id, actor=actor, request_key=command.request_key)

    @app.post('/api/tasks/{task_id}/sessions/{session_id}/remove')
    def remove_session(task_id: str, session_id: str):
        return session_reader.remove(task_id, session_id, actor=actor)

    @app.get('/api/tasks/{task_id}/sessions/{session_id}/agent')
    def read_session_agent(task_id: str, session_id: str):
        if agent_bridge is None:
            session_reader.scope(task_id, session_id)
            return {'result': {'status': 'unavailable', 'code': 'BRIDGE_NOT_CONFIGURED'},
                    'release': {'status': 'not_needed'}, 'observed_at': now()}
        return session_reader.read_agent(task_id, session_id)

    from .hypotheses import HypothesisStore, leak_check
    from .hypothesis_worktrees import HypothesisWorktreeService
    hypotheses = HypothesisStore(repository)
    hypothesis_trees = HypothesisWorktreeService(repository, worktree_service, creator=hypothesis_worktree_creator)
    tree_creation_enabled = config.get('task_worktrees', {}).get('create_from_hypothesis') is True

    def task_run_ids(task_id):
        return views.task_run_ids(task_id)

    @app.get('/api/tasks/{task_id}/hypotheses')
    def list_hypotheses(task_id: str):
        return [dict(card, worktree=hypothesis_trees.get(task_id, card['id'])) for card in hypotheses.list(task_id)]

    @app.post('/api/tasks/{task_id}/hypotheses/{hypothesis_id}/worktree')
    def create_hypothesis_worktree(task_id: str, hypothesis_id: str, command: Command):
        if not tree_creation_enabled:
            raise ValueError('服务器未开放从假设卡创建 worktree')
        return hypothesis_trees.create(task_id, hypothesis_id, actor=actor)

    @app.post('/api/tasks/{task_id}/hypotheses/{hypothesis_id}/worktree/activate')
    def select_hypothesis_worktree(task_id: str, hypothesis_id: str, command: Command):
        if any(r['task_id'] == task_id and not r['terminal'] for r in job_api.execution.records(workspace['id'])):
            raise Conflict('本任务有未结束的评测，不能切换更改目录')
        return hypothesis_trees.activate(task_id, hypothesis_id, actor=actor)

    @app.post('/api/tasks/{task_id}/hypotheses')
    def draft_hypothesis(task_id: str, command: HypothesisDraft):
        return hypotheses.draft(task_id, command.card, author=actor)

    @app.post('/api/tasks/{task_id}/hypotheses/{hypothesis_id}/decision')
    def decide_hypothesis(task_id: str, hypothesis_id: str, command: HypothesisDecision):
        return hypotheses.decide(task_id, hypothesis_id, command.decision, actor=actor, note=command.note)

    @app.post('/api/tasks/{task_id}/hypotheses/{hypothesis_id}/check')
    def check_hypothesis(task_id: str, hypothesis_id: str, command: HypothesisCheck):
        flips = views.flips(command.baseline_run, command.candidate_run)
        leak = leak_check(views, task_id, task_run_ids(task_id), hypothesis_id=hypothesis_id)
        return hypotheses.record_check(task_id, hypothesis_id, baseline_run=command.baseline_run,
                                       candidate_run=command.candidate_run, flips=flips,
                                       leak={k: leak[k] for k in ('status', 'findings', 'checked_files')}, actor=actor)

    @app.post('/api/tasks/{task_id}/hypotheses/{hypothesis_id}/conclusion')
    def conclude_hypothesis(task_id: str, hypothesis_id: str, command: HypothesisConclusion):
        return hypotheses.conclude(task_id, hypothesis_id, effect=command.effect, cause=command.cause,
                                   note=command.note, actor=actor)

    @app.get('/api/tasks/{task_id}/leak-check')
    def task_leak_check(task_id: str):
        return leak_check(views, task_id, task_run_ids(task_id))

    @app.post('/api/tasks/{task_id}/context')
    def context_preview(task_id: str, command: ContextPreview):
        return resolve_context(repository, task_id, command.reference, run_views=views)

    @app.post('/api/tasks/{task_id}/sessions/{session_id}/timeline')
    def read_session_timeline(task_id: str, session_id: str, command: TimelineRead):
        return timeline_reader.read_page(task_id, session_id, direction=command.direction,
                                         cursor=command.cursor.model_dump() if command.cursor else None)

    @app.post('/api/tasks/{task_id}/sessions/{session_id}/candidates')
    def save_sourced_candidate(task_id: str, session_id: str, command: SourcedCandidate):
        return sourced_candidates.save(task_id, session_id, actor=actor, **command.model_dump())

    @app.get('/api/tasks/{task_id}/candidates')
    def candidates(task_id: str):
        return candidate_store.list(task_id)

    @app.post('/api/tasks/{task_id}/candidates/{candidate_id}/decisions')
    def decide_candidate(task_id: str, candidate_id: str, command: CandidateDecision):
        return candidate_store.decide(task_id, candidate_id, actor=actor, **command.model_dump())

    @app.post('/api/tasks/{task_id}/sessions/{session_id}/candidate-receipt')
    def read_candidate_receipt(task_id: str, session_id: str, command: ReceiptRead):
        return candidate_receipts.candidate(task_id, session_id, actor=actor, request_key=command.request_key)

    @app.post('/api/tasks/{task_id}/candidates/{candidate_id}/decision-receipt')
    def read_decision_receipt(task_id: str, candidate_id: str, command: ReceiptRead):
        return candidate_receipts.decision(task_id, candidate_id, actor=actor, request_key=command.request_key)

    @app.post('/api/tasks/{task_id}/results')
    def attach(task_id: str, command: Attach):
        repository.task(task_id)
        source = sources.get(command.source_id)
        if source is None:
            raise ValueError('Unknown registered result source')
        imported = import_result(source['bundle'], source['submission'], source['scores'], state_root)
        return repository.attach_result(task_id, imported['id'])

    @app.get('/api/results/{result_id}')
    def result(result_id: str):
        return read_result(state_root, result_id)

    @app.get('/api/tasks/{task_id}/comparison')
    def comparison(task_id: str, query: Annotated[ComparisonRead, Query()]):
        # Scope before reading any snapshot; configured identities alone select
        # trusted canonical paths. Neither a request nor COMMIT grants a path.
        with repository.connect() as conn:
            repository._require(conn, 'tasks', task_id)
            ids = {row['result_id'] for row in conn.execute(
                'SELECT result_id FROM task_results WHERE task_id=?', (task_id,))}
        if not {query.left_result_id, query.right_result_id} <= ids:
            raise ValueError('Result is not attached to this task')
        canonical = None
        try:
            snapshot = read_snapshot(state_root, query.left_result_id)
        except (OSError, ValueError):
            pass  # The domain returns both sides' actual artifact failures.
        else:
            submission = snapshot.get('submission')
            bundle_id = submission.get('bundle_id') if isinstance(submission, dict) else None
            canonical = next((entry['bundle'] for entry in registered_inputs.values()
                              if bundle_id is not None and entry.get('bundle_id') == bundle_id), None)
        return compare_task_results(repository, task_id, query.left_result_id, query.right_result_id,
                                    canonical_bundle_directory=canonical)

    @app.get('/api/results/{result_id}/case')
    def case(result_id: str, case_id: str):
        return read_case(state_root, result_id, case_id)

    @app.get('/api/results/{result_id}/document')
    def document(result_id: str, document_id: str):
        return read_document(state_root, result_id, document_id)

    @app.get('/api/tasks/{task_id}/reviews')
    def reviews(task_id: str, result_id: str, case_id: str):
        return repository.reviews(task_id, result_id, case_id)

    @app.post('/api/tasks/{task_id}/reviews')
    def save_review(task_id: str, command: Review):
        return repository.save_review(task_id=task_id, reviewer=actor, **command.model_dump())

    @app.post('/api/tasks/{task_id}/notes')
    def note(task_id: str, command: Note):
        return repository.save_note(task_id, command.text, actor=actor, request_key=command.request_key,
                                    context=command.context.model_dump() if command.context else None)

    @app.get('/api/tasks/{task_id}/export')
    def export(task_id: str):
        result = dict(format='sn-workbench-task-export-v1', **repository.task(task_id))
        return JSONResponse(result, headers={'Content-Disposition': f'attachment; filename="task-{task_id}.json"'})

    if static_directory is not None:
        app.mount('/', StaticFiles(directory=static_directory, html=True), name='workbench')
    return app
