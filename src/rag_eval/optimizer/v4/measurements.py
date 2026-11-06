"""V4-only logical attempt policy over durable v2 dispatch/budget/receipt machinery."""
from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict

from rag_eval.workbench.loop_measurements import RoundMeasurements
from ..adapter import EvaluationUnavailable
from . import results


class V41Measurements(RoundMeasurements):
    def __init__(self, iteration_api):
        super().__init__(iteration_api)
        self._validated = OrderedDict()
        self._validated_lock = threading.Lock()

    def _retry_roots(self, iteration_id, candidate_id):
        with self.repository.connect() as conn:
            exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name='round_infra_retries'").fetchone()
            return ({row['actual_attempt']: row['root_attempt'] for row in conn.execute(
                'SELECT actual_attempt,root_attempt FROM round_infra_retries '
                'WHERE iteration_id=? AND candidate_id=? AND actual_attempt IS NOT NULL',
                (iteration_id, candidate_id))} if exists else {})

    def _plan(self, candidate, tree, kind, key):
        if kind != 'subset':
            raise ValueError('v4 private measurements support only subset operations')
        state = super()._plan(candidate, tree, kind, key)
        steps = []
        sides = {candidate['id']}
        if candidate.get('_round_subset_paired'):
            sides.add(candidate['parent_candidate_id'])
        scope = state['scope_id']
        k = candidate['_round_subset_k']
        from rag_eval.workbench.execution_scopes import get
        count = len(get(self.repository, tree['id'], scope)['case_ids'])
        for cid in sorted(sides):
            retries = self._retry_roots(tree['id'], cid)
            attempts = [a for a in self.service.candidate(cid)['attempts'] if a.get('batch_id') == scope]
            roots = {retries.get(a['attempt'], a['attempt']) for a in attempts}
            for index in range(max(0, k - len(roots))):
                stepkey = 'round-measure:' + hashlib.sha256(f'{key}:{cid}:{scope}:v41:{index}'.encode()).hexdigest()
                steps.append(dict(candidate_id=cid, request_key=stepkey, batch_id=scope,
                                  case_count=count, counts_toward_candidate_cap=cid == candidate['id']))
        state['generations'] = [steps]
        state['v41_policy'] = results.POLICY
        return state

    def _projection(self, attempt, expected):
        if attempt.get('execution_state') != 'finished' or not attempt.get('execution_id'):
            raise EvaluationUnavailable('v4 logical measurement has no finished execution')
        key = (attempt['execution_id'], tuple(sorted(expected)))
        with self._validated_lock:
            hit = self._validated.get(key)
        if hit is not None:
            return hit
        projection = results.read_execution(self.views, attempt['execution_id'], expected)
        with self._validated_lock:
            self._validated[key] = projection
            if len(self._validated) > 128:
                self._validated.popitem(last=False)
        return projection

    def _infra(self, attempt):
        if attempt.get('execution_state') in (None, 'running'):
            return False
        runs = self.views.execution_runs(attempt.get('execution_id')) if attempt.get('execution_id') else []
        expected = [task for run in runs for task in run.case_ids]
        projection = self._projection(attempt, expected)
        return any(row['failure_kind'] == 'infra_exhausted' for row in projection['rows'].values())

    def slots(self, candidate_id, scope, k):
        from rag_eval.workbench.execution_scopes import get
        candidate = self.service.candidate(candidate_id)
        expected = get(self.repository, candidate['iteration_id'], scope)['case_ids']
        retry_roots = self._retry_roots(candidate['iteration_id'], candidate_id)
        groups = {}
        for attempt in candidate['attempts']:
            if attempt.get('batch_id') == scope:
                root = retry_roots.get(attempt['attempt'], attempt['attempt'])
                groups.setdefault(root, []).append(attempt)
        if len(groups) < k:
            raise EvaluationUnavailable(f'v4 expected {k} independent measurements, got {len(groups)}')
        slots = []
        for root in sorted(groups)[:k]:
            ordered = sorted(groups[root], key=lambda attempt: attempt['attempt'])
            slots.append((root, results.resolve_slot([self._projection(a, expected) for a in ordered])))
        return slots

    @staticmethod
    def _selection(slots):
        return [dict(root=root, selected={task: dict(score=row['score'], failure=row['failure_kind'],
                                                   **row['trajectory'])
                                         for task, row in projection['rows'].items()})
                for root, projection in slots]

    def read_result(self, candidate_id, scope, k, *, key=None):
        slots = self.slots(candidate_id, scope, k)
        if key is not None:
            storage_key = 'round-measure:' + hashlib.sha256(key.encode()).hexdigest()
            state = self._read(storage_key) or {}
            if state.get('v41_policy') != results.POLICY or self._selection(slots) != (state.get('v41_slots') or {}).get(candidate_id):
                raise EvaluationUnavailable('v4 frozen logical measurement selection differs on replay')
        return results.result_from_slots(slots, candidate_id, scope)

    def _complete(self, state, tree, observed):
        if state.get('v41_policy') != results.POLICY:
            raise EvaluationUnavailable('v4 measurement policy differs; start a new iteration')
        k, scope = state['k'], state['scope_id']
        sides = {step['candidate_id'] for step, _ in observed} | {state['candidate_id']}
        frozen = {}
        for cid in sides:
            slots = self.slots(cid, scope, k)
            self.read_result(cid, scope, k)  # refuses scoring/candidate/unknown missing values
            frozen[cid] = self._selection(slots)
        state['v41_slots'] = frozen
        return True
