"""HTTP verbs for iterations and candidates; the same names as `sn-eval`.

Reads are always available. Writes (create, derive, freeze, run, decide) need
`task_worktrees.create_candidates` in trusted configuration, as worktree and
commit creation touch the configured SN repository. Execution requests always
name a candidate; nothing here depends on what the browser has selected.
"""
from __future__ import annotations

import asyncio
from contextlib import nullcontext
import hashlib
import json
from pathlib import Path

from fastapi import APIRouter, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .action_requests import RUN_VERBS, VERBS, ActionRequestStore, execute
from .comparisons import Admission, ComparisonService, summary
from .hypotheses import HypothesisStore, leak_check
from .iterations import IterationService
from .jobs_api import build_execution_spec
from .notebook_job import SUMMARY
from .repository import Conflict, UnknownIdentity


class _Command(BaseModel):
    model_config = ConfigDict(extra='forbid')


class CreateIteration(_Command):
    title: str = Field(min_length=1, max_length=200)
    start_commit: str = Field(pattern='^[0-9a-f]{40}$')
    input_id: str = Field(min_length=1, max_length=200)
    expected_bundle_id: str = Field(pattern='^[0-9a-f]{64}$')
    case_ids: list[str] = Field(min_length=1)
    mode: str = Field(pattern='^(chunk|reasoning)$')
    request_key: str = Field(min_length=1, max_length=200)
    seeded_from: dict | None = None
    sealed_ref: str | None = None
    split_seed: int = 0
    fresh_batch_count: int = Field(default=0, ge=0, le=100)
    fresh_papers_per_batch: int = Field(default=8, ge=1, le=100)
    budget: dict | None = None
    execution: dict | None = None
    round_settings: dict | None = None
    case_retry: dict | None = None


class Integration(_Command):
    candidate_id: str = Field(pattern='^[0-9a-f]{32}$')
    method: str = Field(pattern='^(merge|cherry-pick|rewrite)$')
    note: str = Field(default='', max_length=2000)


class Derive(_Command):
    proposal_id: str | None = Field(default=None, pattern='^[0-9a-f]{32}$')
    note: str | None = Field(default=None, max_length=2000)
    integrated_from: list[Integration] = Field(default_factory=list)
    request_key: str = Field(min_length=1, max_length=200)


class Run(_Command):
    budget: dict
    request_key: str = Field(min_length=1, max_length=200)


class Proposal(_Command):
    card: dict


class ClusterRun(Run):
    cluster_id: str = Field(min_length=1, max_length=64)


class Decide(_Command):
    decision: str = Field(pattern='^(adopted|rejected|undecided)$')
    note: str = Field(default='', max_length=4000)


class Empty(_Command):
    pass


class ActionDecision(_Command):
    note: str = Field(default='', max_length=4000)


class IterationAPI:
    def __init__(self, repository, workspace, worktree_service, *, plans, job_api, actor, config, run_views,
                 creator=None):
        self.workspace = workspace
        self.actor = actor
        self.writable = config.get('task_worktrees', {}).get('create_candidates') is True
        self.service = IterationService(
            repository, worktree_service, plans=plans, jobs=job_api.store, executions=job_api.execution,
            creator=creator, preparation_config=config.get('job_preparation'),
            execution_config=config.get('job_execution'), build_spec=build_execution_spec,
            admission=Admission(run_views))
        self.comparisons = ComparisonService(repository, self.service, run_views)
        self.hypotheses = HypothesisStore(repository)
        self.views = run_views
        self.requests = ActionRequestStore(repository, execution_config=self.service.execution_config)
        self.repository = repository
        self._shown = {}  # iteration id -> (version, tree) last served

    def primary(self, candidate, task_id=None):
        """The reference's official primary metric.

        Several partitions use the execution's own case-weighted summary, written
        once at scoring time over every planned case; otherwise none is shown.
        """
        reference = candidate.get('reference')
        if reference is None:
            return None
        task_id = task_id or self.service.view(candidate['id']).task_id
        execution = self.service._execution(task_id, reference['execution_id'])
        summary_path = Path(execution['run_dir']) / SUMMARY if execution else None
        if summary_path is not None and summary_path.is_file() and not summary_path.is_symlink():
            saved = json.loads(summary_path.read_text(encoding='utf-8'))
            if saved.get('scored') == saved.get('planned') and saved.get('mean') is not None:
                return dict(metric=saved['metric'], value=saved['mean'], partitions=saved['partitions'],
                            cases=saved['planned'])
        runs = self.views.execution_runs(reference['execution_id'])
        if len(runs) != 1:
            return dict(metric=None, value=None, reason=f'{len(runs)} partitions')
        official = self.views.run(runs[0].id)['official']
        metric = official.get('primary_metric')
        return dict(metric=metric, value=(official.get('metrics') or {}).get(metric), run_id=runs[0].id)

    # Everything the tree view is built from that can change: rows of the
    # iteration's tables and the task's history (execution starts and terminal
    # states). Run files of a terminal execution are not re-read: admission and
    # summaries are fixed once it ends.
    _VERSION_QUERIES = (
        ('iterations', 'SELECT * FROM iterations WHERE id=?', 'id'),
        ('candidates', 'SELECT * FROM candidates WHERE iteration_id=? ORDER BY id', 'id'),
        ('candidate_attempts', 'SELECT a.* FROM candidate_attempts a JOIN candidates c ON c.id=a.candidate_id '
                               'WHERE c.iteration_id=? ORDER BY a.candidate_id,a.attempt', 'id'),
        ('candidate_links', 'SELECT l.candidate_id,l.execution_id FROM candidate_links l JOIN candidates c '
                            'ON c.id=l.candidate_id WHERE c.iteration_id=? ORDER BY 1,2', 'id'),
        ('candidate_worktrees', 'SELECT w.candidate_id,w.status,w.binding_json FROM candidate_worktrees w '
                                'JOIN candidates c ON c.id=w.candidate_id WHERE c.iteration_id=? ORDER BY 1', 'id'),
        ('candidate_comparisons', 'SELECT COUNT(*),MAX(rowid) FROM candidate_comparisons WHERE iteration_id=?', 'id'),
        ('iteration_fresh_batches', 'SELECT * FROM iteration_fresh_batches WHERE iteration_id=? ORDER BY batch_id', 'id'),
        ('hypotheses', 'SELECT id,status,parent_candidate_id FROM hypotheses WHERE task_id=? ORDER BY id', 'task'),
        ('iteration_budget_reservations', 'SELECT * FROM iteration_budget_reservations WHERE iteration_id=? ORDER BY id', 'id'),
        ('history', 'SELECT MAX(seq) FROM history WHERE task_id=?', 'task'),
        # Sealed cases come from every iteration's protocol (splits.Policy).
        ('all_iterations', 'SELECT COUNT(*),MAX(rowid) FROM iterations', None),
    )

    def _version(self, iteration_id):
        from .iterations import _has_table
        digest = hashlib.sha256()
        with self.repository.connect() as conn:
            row = conn.execute('SELECT task_id FROM iterations WHERE id=?', (iteration_id,)).fetchone()
            if row is None:
                raise UnknownIdentity('Unknown iteration identity')
            for table, sql, key in self._VERSION_QUERIES:
                if table not in ('iterations', 'history', 'all_iterations') and not _has_table(conn, table):
                    continue
                params = () if key is None else (iteration_id if key == 'id' else row['task_id'],)
                rows = conn.execute(sql, params).fetchall()
                digest.update(json.dumps([table, [tuple(r) for r in rows]], default=str).encode())
        return digest.hexdigest()

    def show(self, iteration_id):
        """The iteration tree with comparisons; returns (tree, version).

        A GET whose version matches the last one served is answered from memory
        without rebuilding anything. Otherwise executions are discovered once,
        the tree is built once and shared by fresh-batch reconciliation and
        every candidate's comparison lookup.
        """
        cached = self._shown.get(iteration_id)
        if cached is not None and cached[0] == self._version(iteration_id):
            return cached[1], cached[0]
        scope = getattr(self.views, 'request_scope', None)
        with scope() if scope is not None else nullcontext():
            tree = self.service.iteration(iteration_id)
            if self.comparisons.reconcile_fresh(iteration_id, actor=self.actor, tree=tree):
                tree = self.service.iteration(iteration_id)
            for candidate in tree['candidates']:
                both = self.comparisons.for_candidate(candidate['id'], actor=self.actor, iteration=tree,
                                                      reconcile=False)
                candidate['comparisons'] = dict(parent=summary(both['parent']), root=summary(both['root']))
                candidate['primary'] = self.primary(candidate, task_id=tree['task_id'])
        # Taken after any comparison records this request stored.
        version = self._version(iteration_id)
        self._shown[iteration_id] = (version, tree)
        return tree, version

    def _request(self, request_id):
        record = self.requests.get(request_id)
        if self.repository.task(record['task_id'])['workspace_id'] != self.workspace['id']:
            raise UnknownIdentity('Unknown action request identity')
        if record['target_id'] is not None:
            self._scope(candidate_id=record['target_id'])
        return record

    def confirm(self, request_id, note=''):
        """A person confirms; the server executes the recorded parameters unchanged."""
        record = self._request(request_id)
        if record['status'] in ('executed', 'failed'):
            return record
        if VERBS[record['verb']][1]:
            self._write()
        if record['verb'] in RUN_VERBS:
            # Refused before confirmation: the request stays open for after the server is fixed.
            self.service.preflight()
        self.requests.claim(request_id, actor=self.actor, note=note)
        try:
            result = execute(self.service, record, workspace_id=self.workspace['id'],
                             actor=record.get('decided_by') or self.actor, budget_override=True, human_confirmed=True)
        except (Conflict, ValueError) as error:
            return self.requests.finish(request_id, error=str(error))
        return self.requests.finish(request_id, result=result)

    def check(self, candidate_id, *, comparison=None):
        """Check the proposal's predictions against the candidate's Δ父 record."""
        from .agent_tools import task_run_ids
        candidate = self.service.candidate(candidate_id)
        if candidate['proposal_id'] is None:
            raise ValueError('Only a candidate derived from a proposal has predictions to check')
        record = comparison if comparison is not None else self.comparisons.for_candidate(candidate_id, actor=self.actor)['parent']
        if 'id' not in record:
            raise ValueError('Δ父 comparison unavailable: ' + str(record.get('reason')))
        task_id = self.service.view(candidate_id).task_id
        leak = leak_check(self.views, task_id, task_run_ids(self.views, task_id), candidate_id=candidate_id)
        flips = dict(metric=record['metric'], admission=record['admission']['status'], rows=record['rows'],
                     counts=record['counts'], note=record['admission']['reason'])
        return self.hypotheses.record_check(
            task_id, candidate['proposal_id'], baseline_run=record['reference']['runs'],
            candidate_run=record['subject']['runs'], flips=flips, comparison_id=record['id'],
            leak={k: leak[k] for k in ('status', 'findings', 'checked_files')}, actor=self.actor)

    def _write(self):
        if not self.writable:
            raise ValueError('服务器未开放迭代与候选的写操作')

    @property
    def heavy(self):
        """Tree, ledger, comparisons and changes: one computation per key at a time,
        two at most across keys (see heavy_reads)."""
        if '_heavy' not in self.__dict__:
            from .heavy_reads import HeavyReads
            self._heavy = HeavyReads(limit=2)
        return self._heavy

    def _prepare(self, *, iteration_id=None, candidate_id=None, versioned=True):
        """Scope check, then the iteration version a heavy read is cached at (None: not cached)."""
        self._scope(iteration_id=iteration_id, candidate_id=candidate_id)
        if not versioned:
            return None
        return self._version(iteration_id or self._iteration_of(candidate_id))

    def _iteration_of(self, candidate_id):
        # The bare row: the full candidate view measures (admits) every attempt.
        with self.repository.connect() as conn:
            return self.service._candidate(conn, candidate_id)['iteration_id']

    def _scope(self, iteration_id=None, candidate_id=None):
        """Reject identities outside this workspace before acting on them."""
        if candidate_id is not None:
            iteration_id = self._iteration_of(candidate_id)
        if iteration_id is not None:
            ids = {i['id'] for i in self.service.iterations(self.workspace['id'])}
            if iteration_id not in ids:
                raise UnknownIdentity('Unknown iteration identity')

    def router(self):
        router = APIRouter()
        service, actor = self.service, self.actor

        @router.get('/api/iterations')
        def iteration_list():
            return service.iterations(self.workspace['id'])

        @router.get('/api/project/commits')
        def project_commits(q: str | None = None):
            return service.project_commits(self.workspace['id'], query=q)

        @router.post('/api/iterations')
        def iteration_create(command: CreateIteration):
            self._write()
            return service.create_iteration(self.workspace['id'], actor=actor, **command.model_dump())

        @router.get('/api/iterations/{iteration_id}')
        async def iteration_show(iteration_id: str, request: Request):
            # show() returns the version taken after the comparison records it stored.
            tree, version = await self.heavy.get(('show', iteration_id), lambda: self.show(iteration_id),
                                                 prepare=lambda: self._prepare(iteration_id=iteration_id),
                                                 version_of=lambda shown: shown[1])
            etag = '"' + version + '"'
            headers = {'ETag': etag, 'Cache-Control': 'no-cache'}
            if request.headers.get('if-none-match') == etag:
                return Response(status_code=304, headers=headers)
            return JSONResponse(jsonable_encoder(tree), headers=headers)

        @router.get('/api/iterations/{iteration_id}/ledger')
        async def iteration_ledger(iteration_id: str):
            def read():
                from .ledger import LedgerService
                from .sealed import SealedService
                score = SealedService(self.repository, service, self.views).score(iteration_id)
                return LedgerService(self.repository, service, self.views).read(iteration_id, sealed_score=score)
            return await self.heavy.get(('ledger', iteration_id), read,
                                        prepare=lambda: self._prepare(iteration_id=iteration_id))

        @router.get('/api/candidates/{candidate_id}')
        def node_show(candidate_id: str):
            self._scope(candidate_id=candidate_id)
            return service.candidate(candidate_id)

        @router.get('/api/candidates/{candidate_id}/changes')
        async def node_changes(candidate_id: str):
            def read():
                view = service.view(candidate_id)
                return {'binding': None} if view.binding(view.task_id) is None else view.read(view.task_id)
            # The worktree changes outside the database: never cached, only shared.
            return await self.heavy.get(('changes', candidate_id), read,
                                        prepare=lambda: self._prepare(candidate_id=candidate_id, versioned=False))

        @router.get('/api/candidates/{candidate_id}/comparisons')
        async def node_comparisons(candidate_id: str):
            return await self.heavy.get(('comparisons', candidate_id),
                                        lambda: self.comparisons.for_candidate(candidate_id, actor=actor),
                                        prepare=lambda: self._prepare(candidate_id=candidate_id))

        @router.get('/api/comparisons/{comparison_id}')
        def comparison_show(comparison_id: str):
            record = self.comparisons.get(comparison_id)
            self._scope(candidate_id=record['candidate_id'])
            return record

        @router.get('/api/candidates/{candidate_id}/compare')
        def node_compare(candidate_id: str, reference: str):
            self._scope(candidate_id=candidate_id)
            self._scope(candidate_id=reference)
            return self.comparisons.pinned(candidate_id, reference)

        @router.post('/api/candidates/{candidate_id}/check')
        def node_check(candidate_id: str, command: Empty):
            # DB-only like proposal_draft: records a verification, touches no external system.
            self._scope(candidate_id=candidate_id)
            return self.check(candidate_id)

        @router.get('/api/action-requests')
        def action_requests(status: str | None = None):
            return self.requests.list(workspace_id=self.workspace['id'], status=status)

        @router.get('/api/action-requests/{request_id}')
        def action_request(request_id: str):
            return self._request(request_id)

        @router.post('/api/action-requests/{request_id}/confirm')
        def action_confirm(request_id: str, command: ActionDecision):
            return self.confirm(request_id, command.note)

        @router.post('/api/action-requests/{request_id}/reject')
        def action_reject(request_id: str, command: ActionDecision):
            self._request(request_id)
            return self.requests.reject(request_id, actor=actor, note=command.note)

        @router.post('/api/candidates/{candidate_id}/provision')
        def node_provision(candidate_id: str, command: Empty):
            self._write()
            self._scope(candidate_id=candidate_id)
            return service.provision(candidate_id)

        @router.post('/api/candidates/{candidate_id}/derive')
        def node_derive(candidate_id: str, command: Derive):
            self._write()
            self._scope(candidate_id=candidate_id)
            return service.derive(candidate_id, actor=actor, request_key=command.request_key,
                                  proposal_id=command.proposal_id, note=command.note,
                                  integrated_from=[item.model_dump() for item in command.integrated_from])

        @router.post('/api/candidates/{candidate_id}/freeze')
        def node_freeze(candidate_id: str, command: Empty):
            self._write()
            self._scope(candidate_id=candidate_id)
            return service.freeze(candidate_id, actor=actor)

        @router.post('/api/candidates/{candidate_id}/run')
        def node_run(candidate_id: str, command: Run):
            self._write()
            self._scope(candidate_id=candidate_id)
            if service.iteration(service.candidate(candidate_id)['iteration_id'])['protocol'].get('budget'):
                service.dispatch_run(candidate_id, 'node-run', budget=command.budget, actor=actor,
                                     request_key=command.request_key)
                return service.candidate(candidate_id)
            service.fill_validation(candidate_id, budget=command.budget, actor=actor, request_key=command.request_key)
            return service.run(candidate_id, budget=command.budget, actor=actor, request_key=command.request_key)

        @router.post('/api/candidates/{candidate_id}/fresh-run')
        def node_fresh_run(candidate_id: str, command: Run):
            self._write()
            self._scope(candidate_id=candidate_id)
            return service.fresh_run(candidate_id, budget=command.budget, actor=actor, request_key=command.request_key)

        @router.post('/api/candidates/{candidate_id}/cluster-run')
        def node_cluster_run(candidate_id: str, command: ClusterRun):
            self._write()
            self._scope(candidate_id=candidate_id)
            return service.cluster_run(candidate_id, command.cluster_id, budget=command.budget,
                                       actor=actor, request_key=command.request_key)

        @router.get('/api/candidates/{candidate_id}/clusters/{cluster_id}/comparison')
        def cluster_comparison(candidate_id: str, cluster_id: str):
            self._scope(candidate_id=candidate_id)
            return self.comparisons.for_cluster(candidate_id, cluster_id, actor=actor)

        @router.post('/api/candidates/{candidate_id}/adopt')
        def node_adopt(candidate_id: str, command: Run):
            """Root only: register the server-configured finished runs as an attempt."""
            self._write()
            self._scope(candidate_id=candidate_id)
            return service.run(candidate_id, budget=command.budget, actor=actor, request_key=command.request_key,
                               adopt=True)

        @router.post('/api/candidates/{candidate_id}/proposals')
        def proposal_draft(candidate_id: str, command: Proposal):
            # Intentionally not behind _write(): drafting a proposal and
            # recording a decision are DB-only operations that create no
            # external resources (no Paseo workspace, no git commit).
            # _write() gates operations that touch external systems.
            self._scope(candidate_id=candidate_id)
            return service.draft_proposal(candidate_id, command.card, author=actor)

        @router.post('/api/candidates/{candidate_id}/decision')
        def node_decide(candidate_id: str, command: Decide):
            # Same rationale as proposal_draft: no external side-effect.
            self._scope(candidate_id=candidate_id)
            return service.decide(candidate_id, command.decision, actor=actor, note=command.note)

        return router
