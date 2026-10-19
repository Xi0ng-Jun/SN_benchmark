"""HTTP verbs for iterations and candidates; the same names as `sn-eval`.

Reads are always available. Writes (create, derive, freeze, run, decide) need
`task_worktrees.create_candidates` in trusted configuration, as worktree and
commit creation touch the configured SN repository. Execution requests always
name a candidate; nothing here depends on what the browser has selected.
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from .action_requests import VERBS, ActionRequestStore, execute
from .comparisons import Admission, ComparisonService, summary
from .hypotheses import HypothesisStore, leak_check
from .iterations import IterationService
from .jobs_api import build_execution_spec
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
        self.requests = ActionRequestStore(repository)
        self.repository = repository

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
        self.requests.claim(request_id, actor=self.actor, note=note)
        try:
            result = execute(self.service, record, workspace_id=self.workspace['id'], actor=self.actor)
        except (Conflict, ValueError) as error:
            return self.requests.finish(request_id, error=str(error))
        return self.requests.finish(request_id, result=result)

    def check(self, candidate_id):
        """Check the proposal's predictions against the candidate's Δ父 record."""
        from .agent_tools import task_run_ids
        candidate = self.service.candidate(candidate_id)
        if candidate['proposal_id'] is None:
            raise ValueError('Only a candidate derived from a proposal has predictions to check')
        record = self.comparisons.for_candidate(candidate_id, actor=self.actor)['parent']
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

    def _scope(self, iteration_id=None, candidate_id=None):
        """Reject identities outside this workspace before acting on them."""
        if candidate_id is not None:
            iteration_id = self.service.candidate(candidate_id)['iteration_id']
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

        @router.post('/api/iterations')
        def iteration_create(command: CreateIteration):
            self._write()
            return service.create_iteration(self.workspace['id'], actor=actor, **command.model_dump())

        @router.get('/api/iterations/{iteration_id}')
        def iteration_show(iteration_id: str):
            self._scope(iteration_id=iteration_id)
            tree = service.iteration(iteration_id)
            for candidate in tree['candidates']:
                both = self.comparisons.for_candidate(candidate['id'], actor=actor, iteration=tree)
                candidate['comparisons'] = dict(parent=summary(both['parent']), root=summary(both['root']))
            return tree

        @router.get('/api/candidates/{candidate_id}')
        def node_show(candidate_id: str):
            self._scope(candidate_id=candidate_id)
            return service.candidate(candidate_id)

        @router.get('/api/candidates/{candidate_id}/changes')
        def node_changes(candidate_id: str):
            self._scope(candidate_id=candidate_id)
            view = service.view(candidate_id)
            return {'binding': None} if view.binding(view.task_id) is None else view.read(view.task_id)

        @router.get('/api/candidates/{candidate_id}/comparisons')
        def node_comparisons(candidate_id: str):
            self._scope(candidate_id=candidate_id)
            return self.comparisons.for_candidate(candidate_id, actor=actor)

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
            return service.run(candidate_id, budget=command.budget, actor=actor, request_key=command.request_key)

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
