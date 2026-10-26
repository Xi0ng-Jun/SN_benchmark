"""Real LCD round ports over existing durable workbench services.

No models are called here. Agent creation/message stores own remote uncertainty;
round state owns orchestration, while measurements own execution footprints.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math

from .clusters import ClusterStore, referenced_cluster, scan
from .comparisons import ComparisonService
from .context import resolve_context
from .gate import validate_contract
from .hypotheses import benchmark_material, scan_text, validate_card
from .ledger import LedgerService
from .repository import Conflict


class RoundOperations:
    def __init__(self, iteration_api, creation_api, message_api, session_reader):
        from .loop_measurements import RoundMeasurements
        self.api = iteration_api
        self.service = iteration_api.service
        self.repository = iteration_api.repository
        self.creations = creation_api
        self.messages = message_api
        self.sessions = session_reader
        self.actor = iteration_api.actor
        self.measurements = RoundMeasurements(iteration_api)
        self.clusters = ClusterStore(self.repository, self.service, iteration_api.views)
        self.messages.store.run_views = iteration_api.views
        with self.repository.connect(write=True) as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS round_operation_state '
                         '(key TEXT PRIMARY KEY, value_json TEXT NOT NULL)')

    @staticmethod
    def _key(kind, key):
        return 'round:' + kind + ':' + hashlib.sha256(key.encode()).hexdigest()

    def _get(self, key):
        with self.repository.connect() as conn:
            row = conn.execute('SELECT value_json FROM round_operation_state WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def _save(self, key, value):
        with self.repository.connect(write=True) as conn:
            conn.execute('INSERT INTO round_operation_state(key,value_json) VALUES(?,?) '
                         'ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json',
                         (key, json.dumps(value, ensure_ascii=False)))
        return value

    def _agent_actor(self, key):
        receipt_key = self._key('agent-actor', key)
        prior = self._get(receipt_key)
        if prior:
            return prior['actor']
        actor = self.actor
        parts = key.split('/')
        if len(parts) > 2 and parts[0] == 'round':
            with self.repository.connect() as conn:
                if conn.execute("SELECT 1 FROM sqlite_master WHERE name='iteration_round_invocations'").fetchone():
                    row = conn.execute('SELECT record FROM iteration_round_invocations WHERE id=?', (parts[1],)).fetchone()
                    if row:
                        actor = json.loads(row[0]).get('actor', actor)
        self._save(receipt_key, {'actor': actor})
        return actor

    def _stopped(self, key):
        parts = key.split('/')
        if len(parts) < 3 or parts[0] != 'round':
            return False
        with self.repository.connect() as conn:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='iteration_round_invocations'").fetchone():
                return False
            row = conn.execute('SELECT stop_actor FROM iteration_round_invocations WHERE id=?', (parts[1],)).fetchone()
        return bool(row and row['stop_actor'] is not None)

    def _pending_budget_blocked(self, tree, requests):
        """Observe a frozen request footprint; a newly filed request is not exhaustion."""
        from .budgets import BudgetLedger, exceeded_caps
        policy = tree['protocol'].get('budget')
        if not policy:
            return False
        ledger = BudgetLedger(self.repository)
        with self.repository.connect() as conn:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='action_dispatches'").fetchone():
                return False
            summary = ledger._summary(conn, tree['id'])
            per_case, _ = ledger._estimate(conn, tree)
            for request in requests:
                row = conn.execute('SELECT steps_json FROM action_dispatches WHERE request_id=?', (request['id'],)).fetchone()
                if not row:
                    continue
                missing = [step for step in json.loads(row[0]) if not conn.execute(
                    'SELECT 1 FROM iteration_budget_reservations WHERE iteration_id=? AND candidate_id=? AND request_key=?',
                    (tree['id'], step['candidate_id'], step['request_key'])).fetchone()]
                if exceeded_caps(policy, summary, missing, per_case):
                    return True
        return False

    def _tree(self, iteration_id):
        self.api._scope(iteration_id=iteration_id)
        return self.service.iteration(iteration_id)

    def describe(self, iteration_id):
        from .budgets import BudgetLedger, exhausted
        tree = self._tree(iteration_id)
        self.api._write()
        protocol = tree.get('protocol') or {}
        from .loop import validate_settings
        settings = validate_settings(protocol.get('round_settings') or {})
        profiles = {p['id'] for p in self.creations.profiles}
        if any(settings[name] not in profiles for name in ('analyst_profile', 'implementer_profile', 'merge_profile')):
            raise ValueError('Round Agent profile is not configured on this server')
        if not self.creations.configured or not self.messages.configured:
            raise ValueError('Round Agent creation and message bridges must be configured')
        if not callable(getattr(getattr(self.sessions, 'bridge', None), 'call', None)):
            raise ValueError('Round Agent observation bridge must be configured')
        budget = BudgetLedger(self.repository).summary(iteration_id)
        budget['policy'] = protocol.get('budget')
        budget_exhausted = exhausted(budget)
        with self.repository.connect() as conn:
            completed = {r[0] for r in conn.execute('SELECT batch_id FROM iteration_fresh_batches '
                         'WHERE iteration_id=?', (iteration_id,))}
        return dict(task_id=tree['task_id'], workspace_id=self.api.workspace['id'],
                    root_candidate_id=tree['root_candidate_id'], settings=settings,
                    k_base=protocol.get('k_base', 2),
                    evaluated_count=sum(c['state'] == 'evaluated' for c in tree['candidates']),
                    fresh_remaining=sum(b['batch_id'] not in completed for b in protocol.get('fresh_batches', [])),
                    budget_exhausted=budget_exhausted, budget_usage=budget)

    def measure(self, candidate_id, **kwargs):
        return self.measurements.measure(candidate_id, **kwargs)

    def fresh(self, candidate_id, **kwargs):
        return self.measurements.fresh(candidate_id, **kwargs)

    def agent(self, role, candidate_id, *, iteration_id, key, profile, text, scope=None, hypothesis_id=None,
              message_stage='initial', start_grace_seconds=300):
        actor = self._agent_actor(key)
        tree = self._tree(iteration_id)
        task_id = tree['task_id']
        if candidate_id is not None and candidate_id not in {c['id'] for c in tree['candidates']}:
            raise ValueError('Agent candidate belongs to another iteration')
        # A merge is still an implementer at the existing role/privacy boundary.
        stored_role = 'implementer' if role in ('implementer', 'merge') else role
        if stored_role == 'implementer' and hypothesis_id is None and candidate_id:
            hypothesis_id = self.service.candidate(candidate_id)['proposal_id']
        creation_key = self._key('agent-create', key)
        prior = next((i for i in self.creations.store.list(task_id, actor=actor)
                      if i['request_key'] == creation_key), None)
        intent = prior or self.creations.store.prepare(task_id, profile, actor=actor,
                    request_key=creation_key, role=stored_role, candidate_id=None if scope == 'iteration' else candidate_id,
                    hypothesis_id=hypothesis_id, scope=scope)
        if intent['status'] == 'prepared':
            if self._stopped(key):
                return dict(status='failed', reason='operator_stop')
            intent = self.creations.store.attempt(task_id, intent['id'], actor=actor,
                                                  bridge=self.creations.bridge)
        if intent['status'] != 'created':
            return dict(status='unknown' if intent['status'] == 'unknown' else 'failed',
                        reason='agent_creation_' + intent['status'])
        session_id = intent['session_id']
        message_identity = key if message_stage == 'initial' else key + '/followup/' + message_stage
        message_key = self._key('agent-message', message_identity)
        if message_stage != 'initial':
            stage = message_stage.rsplit('/', 1)[0]
            instruction = {
                'cards_missing': 'Finish legal contract card drafts for the existing frozen clusters.',
                'cluster_result_not_read': 'Cluster-run finished. Read cluster result, then request the full base runs.',
                'full_base_runs_not_requested': 'Cluster verification is complete. Request node run with the frozen k_base attempts.',
                'candidate_not_frozen': 'Complete the implementation and request node freeze before cluster-run.',
                'cluster_runs_not_completed': 'Request the approved cluster-run and continue when its result is ready.',
            }.get(stage, 'Complete the next missing artifact in the required LCD sequence.')
            text = ('Follow-up: ' + instruction +
                    ' Long executions continue after a turn ends. Continue the task below.\n' + text)
        message = next((i for i in self.messages.store.list(task_id, actor=actor)
                        if i['request_key'] == message_key), None)
        if message is None:
            reference = dict(kind='brief', session_id=session_id)
            brief = resolve_context(self.repository, task_id, reference, run_views=self.api.views)
            message = self.messages.store.prepare(task_id, session_id, text,
                        [dict(reference=reference, sha256=brief['sha256'])], 'steer', actor=actor,
                        request_key=message_key, preflight=self.messages._validate_preflight)
        # Existing message snapshots are reused without resolving a changed brief.
        if message['status'] == 'prepared':
            if self._stopped(key):
                return dict(status='failed', session_id=session_id, reason='operator_stop')
            message = self.messages.store.attempt(task_id, message['id'], actor=actor,
                                                   bridge=self.messages.bridge)
        if message['status'] != 'acknowledged':
            return dict(status='unknown' if message['status'] == 'unknown' else 'failed',
                        session_id=session_id, reason='agent_message_' + message['status'])
        observed = self.sessions.read_agent(task_id, session_id)['result']
        if observed.get('status') != 'ok':
            return dict(status='running', session_id=session_id, reason='agent_observation_unavailable')
        status = observed['agent']['status']
        observation_key = self._key('agent-observation', message_identity)
        tracking = self._get(observation_key) or dict(seen_active=False)
        if status in ('running', 'working', 'busy'):
            tracking['seen_active'] = True
            self._save(observation_key, tracking)
            return dict(status='running', session_id=session_id)
        if status in ('failed', 'error', 'archived', 'closed'):
            return dict(status='failed', session_id=session_id, reason='agent_' + status)
        # A queued turn can briefly report idle immediately after send. Require a
        # real active observation or a persisted send grace before declaring stall.
        finished_at = message.get('finished_at')
        elapsed = 0
        if finished_at:
            stamp = datetime.fromisoformat(finished_at.replace('Z', '+00:00'))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            elapsed = (datetime.now(timezone.utc) - stamp).total_seconds()
        return dict(status='idle' if status == 'idle' and (tracking['seen_active'] or elapsed >= start_grace_seconds)
                    else 'running', session_id=session_id)

    def _search_executions(self, tree, baseline_id):
        baseline = next(c for c in tree['candidates'] if c['id'] == baseline_id)
        measurements = ComparisonService.terminal(ComparisonService.scoped(baseline, None))
        search = set(tree['protocol']['splits']['search'])
        return [dict(execution_id=m['execution_id'], cases=[self.api.views.observation(r.id, cid)
                    for r in self.api.views.execution_runs(m['execution_id']) for cid in r.case_ids if cid in search])
                for m in measurements]

    def _card_legal(self, task_id, record, cluster):
        try:
            raw = dict(record['card'])
            raw['predictions'] = [{k: v for k, v in p.items() if v is not None} for p in raw.get('predictions', [])]
            card = validate_card(raw)
            contract = validate_contract(card['contract'])
            if referenced_cluster(card) != cluster['cluster_id']:
                return False
            if not set(contract['predicted_flips']) <= set(cluster['scan']['members']):
                return False
            from .agent_tools import task_run_ids
            material = benchmark_material(self.api.views, task_run_ids(self.api.views, task_id))
            visible = {k: card.get(k) for k in ('title', 'component', 'mechanism', 'implementer_brief',
                                               'possible_harm', 'change_scope')}
            return not scan_text(json.dumps(visible, ensure_ascii=False), material)
        except (ValueError, KeyError):
            return False

    def select(self, iteration_id, baseline_id, *, max_clusters, b, pi):
        tree = self._tree(iteration_id)
        executions = self._search_executions(tree, baseline_id)
        ledger = self.ledger(iteration_id)
        resolved = {r['cluster_id'] for r in ledger['structure_ledger']['clusters'] if r['status'] != 'open'}
        with self.repository.connect() as conn:
            ids = [r[0] for r in conn.execute('SELECT cluster_id FROM clusters WHERE iteration_id=? '
                                             'ORDER BY cluster_id', (iteration_id,))]
        cards = self.api.hypotheses.list(tree['task_id'])
        ranking = []
        for cluster_id in ids:
            cluster = self.clusters.get(tree['task_id'], cluster_id)
            current = scan(tree, dict(cluster, primary=cluster['scan']['primary']), executions)
            q, m, v = (current[k] for k in ('q', 'm', 'v'))
            missing_attribution = [name for name, value in (('m', m), ('v', v)) if value is None]
            strength = max([pi] + [value for value in (m, v) if value is not None])
            fallback = ('unmeasurable_attribution_use_pi' if len(missing_attribution) == 2
                        else 'partial_attribution' if missing_attribution else None)
            score = current['n_cases'] * (1 - q) * strength if q is not None else None
            legal = [r for r in cards if r.get('parent_candidate_id') == baseline_id
                     and r['status'] in ('draft', 'approved') and self._card_legal(tree['task_id'], r, cluster)]
            legal.sort(key=lambda r: (r['created_at'], r['id']))
            ranking.append(dict(cluster_id=cluster_id, n_cases=current['n_cases'], q=q, m=m, v=v, pi=pi,
                                score=score, fallback=fallback, missing_attribution=missing_attribution,
                                status='resolved' if cluster_id in resolved else 'open',
                                hypothesis_id=legal[0]['id'] if legal else None,
                                card_status=legal[0]['status'] if legal else None))
        ranking.sort(key=lambda r: (r['score'] is None, -(r['score'] or 0), r['cluster_id']))
        selected = [dict(hypothesis_id=r['hypothesis_id'], cluster_id=r['cluster_id'], status=r['card_status'])
                    for r in ranking if r['status'] == 'open' and r['hypothesis_id'] and r['score'] is not None][:b]
        return dict(ranking=ranking, cards=selected, clusters_count=len(ids))

    def card_status(self, hypothesis_id, *, iteration_id):
        task_id = self._tree(iteration_id)['task_id']
        return self.api.hypotheses.get(task_id, hypothesis_id)['status']

    def approve(self, hypothesis_id, *, iteration_id, key, actor):
        task_id = self._tree(iteration_id)['task_id']
        card = self.api.hypotheses.get(task_id, hypothesis_id)
        if card['status'] == 'draft':
            cluster = self.clusters.get(task_id, referenced_cluster(card['card']))
            if not self._card_legal(task_id, card, cluster):
                return dict(status='rejected', reason='invalid_contract_or_leak')
            card = self.api.hypotheses.decide(task_id, hypothesis_id, 'approve', actor=actor,
                                             note='LCD round approval: ' + key)
        return dict(status=card['status'])

    def derive(self, baseline_id, *, hypothesis_id, key, actor, integrated_from=None):
        self.api._write()
        self.api._scope(candidate_id=baseline_id)
        baseline = self.service.candidate(baseline_id)
        integrated = []
        for source in integrated_from or []:
            cid = source if isinstance(source, str) else source['candidate_id']
            candidate = self.service.candidate(cid)
            if candidate['iteration_id'] != baseline['iteration_id'] or not candidate.get('frozen_commit'):
                raise ValueError('Merge sources must be frozen candidates of this iteration')
            integrated.append(dict(candidate_id=cid, method='merge', note='LCD accepted source'))
        if integrated:
            tree = self._tree(baseline['iteration_id'])
            marker = self._key('integration-card', key)
            existing = next((r for r in self.api.hypotheses.list(tree['task_id']) if r['author'] == marker), None)
            if existing is None:
                sources = [self.service.candidate(i['candidate_id']) for i in integrated]
                safe_sources = '; '.join(f'{c["id"]}: commit {c["frozen_commit"]}' for c in sources)
                card = dict(title='合并已接受改动', component='integration', mechanism='合并本轮已接受来源',
                            implementer_brief='只合并以下来源、解决冲突，不加新改动：' + safe_sources,
                            predictions=[],
                            falsification='合并后 validation 超过冻结噪声带退化')
                existing = self.api.hypotheses.draft(tree['task_id'], card, author=marker,
                                                      parent_candidate_id=baseline_id)
            hypothesis_id = existing['id']
            if existing['status'] == 'draft':
                self.api.hypotheses.decide(tree['task_id'], hypothesis_id, 'approve', actor=actor,
                                           note='Synthetic merge-only card; no benchmark prediction')
        if self._stopped(key):
            raise Conflict('Round was stopped before candidate provisioning')
        receipt_key = self._key('derive', key)
        prior = self._get(receipt_key)
        if prior is not None:
            return prior['candidate_id']
        candidate = self.service.derive(baseline_id, actor=actor, request_key=key, proposal_id=hypothesis_id,
                                         integrated_from=integrated)
        self._save(receipt_key, {'candidate_id': candidate['id']})
        return candidate['id']

    def _round_action_ids(self):
        """Read durable measurement ownership, independent of labels/reasons."""
        with self.repository.connect() as conn:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='round_measurements'").fetchone():
                return set()
            rows = conn.execute('SELECT payload_json FROM round_measurements').fetchall()
        owned = set()
        for row in rows:
            state = json.loads(row[0])
            owned.update(state.get('actions') or [])
            if state.get('action_id'):
                owned.add(state['action_id'])
        return owned

    def implementation(self, candidate_id, *, key=None, actor=None, infra_retry=1, timeout_seconds=7200, observe_only=False):
        self.api._scope(candidate_id=candidate_id)
        actor = actor or (self._agent_actor(key) if key else self.actor)
        candidate = self.service.candidate(candidate_id)
        tree = self._tree(candidate['iteration_id'])
        k_base = tree['protocol'].get('k_base', 2)
        requests = [r for r in self.api.requests.list(task_id=tree['task_id']) if r['target_id'] == candidate_id]
        if not candidate.get('frozen_commit'):
            awaiting = any(r['verb'] == 'node-freeze' and r['status'] in ('pending', 'confirmed') for r in requests)
            return dict(status='running', reason='candidate_not_frozen', awaiting_confirmation=awaiting)
        attempts = [a for a in candidate['attempts'] if a.get('batch_id') in (None, 'base')]
        in_flight = any(a.get('execution_state') == 'running' for a in candidate['attempts'])
        card = self.api.hypotheses.get(tree['task_id'], candidate['proposal_id'])['card'] if candidate['proposal_id'] else {}
        cluster_id = referenced_cluster(card)
        verbs = ['node-cluster-run', 'node-run'] if cluster_id else ['node-run']
        for verb in verbs:
            own = [r for r in requests if r['verb'] == verb]
            if own and own[-1]['status'] == 'failed':
                return dict(status='failed', reason=verb + '_request_failed', execution_in_flight=in_flight)
            pending = [r for r in own if r['status'] == 'pending']
            if pending:
                return dict(status='running', reason='awaiting_request_confirmation',
                            budget_exhausted=self._pending_budget_blocked(tree, pending), execution_in_flight=in_flight, awaiting_confirmation=True)
            if cluster_id and verb == 'node-cluster-run':
                clustered = [a for a in candidate['attempts'] if a.get('batch_id') == 'cluster:' + cluster_id]
                round_actions = self._round_action_ids()
                original_actions = [r for r in own if r['id'] not in round_actions]
                if any(r['status'] == 'confirmed' for r in original_actions):
                    return dict(status='running', reason='initial_cluster_dispatch_in_progress', execution_in_flight=True)
                if not original_actions:
                    parent = self.service.candidate(candidate['parent_candidate_id'])
                    parent_clustered = [a for a in parent['attempts'] if a.get('batch_id') == 'cluster:' + cluster_id]
                    if len(clustered) < k_base or len(parent_clustered) < k_base:
                        return dict(status='running', reason='cluster_runs_not_completed', execution_in_flight=in_flight)
                if clustered:
                    retry = self.measurements.cluster(candidate_id, cluster_id,
                        key=key or 'implementation-cluster:' + candidate_id, actor=actor,
                        infra_retry=infra_retry, timeout_seconds=timeout_seconds,
                        **({'observe_only': True} if observe_only else {}))
                    if retry['status'] != 'complete':
                        if retry['status'] in ('failed', 'stopped'):
                            return dict(status='failed', reason=retry.get('reason') or 'cluster_infrastructure_retry_exhausted')
                        return dict(status='running', reason='cluster_infrastructure_retry_' + retry['status'],
                                    execution_in_flight=retry['status'] == 'running',
                                    budget_exhausted=retry['status'] == 'budget_exhausted')
                    candidate = self.service.candidate(candidate_id)
                    clustered = [a for a in candidate['attempts'] if a.get('batch_id') == 'cluster:' + cluster_id]
                if len(clustered) < k_base or any(a.get('execution_state') == 'running' for a in clustered):
                    return dict(status='running', reason='cluster_runs_not_completed', execution_in_flight=in_flight)
                parent = self.service.candidate(candidate['parent_candidate_id'])
                current = self.api.comparisons._stored(ComparisonService.scoped(candidate, 'cluster:' + cluster_id),
                    ComparisonService.scoped(parent, 'cluster:' + cluster_id), 'cluster', actor, store=False)
                if not current.get('id'):
                    with self.repository.connect() as conn:
                        original = conn.execute("SELECT id FROM candidate_comparisons WHERE candidate_id=? AND kind='cluster' LIMIT 1",
                                                (candidate_id,)).fetchone()
                    if original is None:
                        return dict(status='running', reason='cluster_result_not_read', execution_in_flight=in_flight)
                    current = self.api.comparisons.for_cluster(candidate_id, cluster_id, actor='round:retry-refresh')
                    refreshed_cluster = dict(observed_original_read=True, refreshed_cluster_comparison_id=current.get('id'))
                else:
                    refreshed_cluster = dict(observed_original_read=True, refreshed_cluster_comparison_id=current.get('id'))
        runs = [r for r in requests if r['verb'] == 'node-run']
        round_actions = self._round_action_ids()
        if any(r['status'] == 'confirmed' and r['id'] not in round_actions for r in runs):
            return dict(status='running', reason='initial_node_dispatch_in_progress', execution_in_flight=True)
        requested = sum(r['params'].get('attempts', 1) for r in runs if r['status'] in ('confirmed', 'executed'))
        if len(attempts) >= k_base or requested >= k_base:
            return dict(status='ready', execution_in_flight=in_flight, **(refreshed_cluster if cluster_id else {}))
        return dict(status='running', reason='full_base_runs_not_requested', execution_in_flight=in_flight,
                    **(refreshed_cluster if cluster_id else {}))

    def gate(self, candidate_id, *, key, actor):
        self.api._scope(candidate_id=candidate_id)
        candidate = self.service.candidate(candidate_id)
        decision = candidate.get('decision') or {}
        comparison = self.api.comparisons.for_candidate(candidate_id, actor=actor)['parent']
        validation = comparison.get('validation') or {}
        missing_batches = set(validation.get('missing_batches') or [])
        tree = self._tree(candidate['iteration_id'])
        from .splits import batches
        parent = self.service.candidate(candidate['parent_candidate_id'])
        k_base = tree['protocol'].get('k_base', 2)
        for batch in batches(self.repository, tree['id'], completed=True):
            scope = batch['batch_id']
            for side in (candidate, parent):
                admitted = ComparisonService.admitted(ComparisonService.scoped(side, scope))
                if len(admitted) < k_base:
                    missing_batches.add(scope)
        if missing_batches:
            revision = hashlib.sha256(json.dumps(sorted(missing_batches)).encode()).hexdigest()
            return dict(verdict='pending', measurement_revision=revision, reason='validation_pool_expanded')
        verdict = decision.get('gate_verdict') if decision.get('comparison_id') == comparison.get('id') else None
        if verdict is None:
            checked = self.api.check(candidate_id, comparison=comparison)
            verdict = (checked.get('verification') or {}).get('verdict')
        return dict(verdict or dict(verdict='unmeasurable', reasons=['missing_contract_verdict']))

    def merge_result(self, candidate_id, baseline_id, *, key, actor):
        self.api._scope(candidate_id=candidate_id)
        self.api._scope(candidate_id=baseline_id)
        receipt_key = self._key('merge-result', key)
        prior = self._get(receipt_key)
        if prior is not None:
            return prior
        comparison = self.api.comparisons.pinned(candidate_id, baseline_id)
        validation = comparison.get('validation') or {}
        delta = validation.get('delta')
        eta = (comparison.get('noise') or {}).get('eta')
        measurable = (comparison.get('admission') or {}).get('status') == 'admitted' and not validation.get('incomplete')
        measurable = measurable and all(type(v) in (int, float) and math.isfinite(v) for v in (delta, eta))
        result = dict(status='unmeasurable', delta=delta, eta=eta)
        if measurable:
            accepted = delta >= -eta
            result['status'] = 'accepted' if accepted else 'withdrawn'
            decision = self.service.candidate(candidate_id).get('decision') or {}
            note = 'LCD merge result: ' + key
            if decision.get('note') != note:
                self.service.decide(candidate_id, 'adopted' if accepted else 'rejected', actor=actor, note=note)
        return self._save(receipt_key, result)

    def ledger(self, iteration_id):
        self.api._scope(iteration_id=iteration_id)
        return LedgerService(self.repository, self.service, self.api.views).read(iteration_id)
