"""Real round adapter seams with synthetic evidence and fake external bridges."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from rag_eval.workbench.creation_api import CreationAPI
from rag_eval.workbench.hypotheses import validate_card
from rag_eval.workbench.iterations_api import IterationAPI
from rag_eval.workbench.loop_services import RoundOperations
from rag_eval.workbench.message_api import MessageAPI
from rag_eval.workbench.sessions import TaskSessionReader
from test_workbench_agent_creations import Bridge, created
from test_workbench_agent_messages import acknowledged
from test_workbench_clusters import frozen_cluster, env  # noqa: F401
from test_workbench_iterations import BUDGET, finish


class ReadBridge:
    def __init__(self, creation_bridge):
        self.creations = creation_bridge
        self.status = 'idle'

    def call(self, command):
        original = next(c for c in self.creations.commands if c['agentId'] == command['agentId'])
        agent = created(original)['result']['agent']
        agent['status'] = self.status
        return dict(result=dict(status='ok', agent=agent), release=dict(status='ok'))


@pytest.fixture
def adapter(frozen_cluster):
    f = frozen_cluster
    service, repo, views = f['service'], f['env']['repo'], f['views']
    views.task_runs_context = lambda task_id: ('runs', json.dumps(dict(runs=[dict(kind='execution', id=e)
                                                         for e in views.partitions])), {})
    views.run = lambda run_id: dict(official=dict(primary_metric='answer_f1', metrics=dict(answer_f1=.5)))
    config = dict(task_worktrees=dict(create_candidates=True), job_preparation=service.preparation_config,
                  job_execution=service.execution_config,
                  agent_creation=dict(workspaceId='fixture-evaluator', profiles={
                      'pi-flash': dict(provider='codex/model-example', modeId='full-access')}))
    workspace = f['env']['workspace']
    api = IterationAPI(repo, workspace, service.trees, plans=service.plans,
                       job_api=SimpleNamespace(store=service.jobs, execution=service.executions),
                       actor='alice', config=config, run_views=views, creator=f['env']['creator'])
    creation_bridge, message_bridge = Bridge(created), Bridge(acknowledged)
    creation = CreationAPI(repo, workspace, config, actor='alice', bridge=creation_bridge)
    creation.store.evaluated_count = lambda task_id: sum(c['state'] == 'evaluated' for c in api.service.iteration(f['tree']['id'])['candidates'])
    creation.store.candidate_state = lambda cid: api.service.candidate(cid)['state']
    message = MessageAPI(repo, workspace, config, actor='alice', bridge=message_bridge)
    reader = TaskSessionReader(repo, ReadBridge(creation_bridge))
    operations = RoundOperations(api, creation, message, reader)
    selection = api.hypotheses.draft(f['tree']['task_id'], f['proposal_card'], author='fixture-analyst',
                                      parent_candidate_id=f['tree']['root_candidate_id'])
    return dict(f=f, ops=operations, api=api, creation=creation, message=message,
                creation_bridge=creation_bridge, message_bridge=message_bridge, reader=reader, selection=selection)


def agent(adapter, **changes):
    f = adapter['f']
    args = dict(iteration_id=f['tree']['id'], key='agent-test', profile='pi-flash', text='Analyze search evidence.')
    args.update(changes)
    return adapter['ops'].agent('analyst', f['tree']['root_candidate_id'], **args)


def test_preflight_checks_all_profiles_before_measurement(adapter):
    ops, api, f = adapter['ops'], adapter['api'], adapter['f']
    info = ops.describe(f['tree']['id'])
    assert info['k_base'] == 2 and info['evaluated_count'] == 1
    assert info['settings']['analyst_profile'] == 'pi-flash'
    adapter['creation']._profiles.clear()
    before = len(api.service.candidate(f['tree']['root_candidate_id'])['attempts'])
    with pytest.raises(ValueError, match='profile'):
        ops.describe(f['tree']['id'])
    assert len(api.service.candidate(f['tree']['root_candidate_id'])['attempts']) == before


def test_agent_recovery_reuses_brief_and_never_resends_acknowledged_intent(adapter, monkeypatch):
    first = agent(adapter)
    assert first['status'] == 'running', first  # immediate post-send idle is not completion
    original = adapter['message_bridge'].commands[0]['text']
    monkeypatch.setattr('rag_eval.workbench.loop_services.resolve_context', lambda *a, **k: pytest.fail('brief re-resolved'))
    adapter['reader'].bridge.status = 'running'
    assert agent(adapter)['status'] == 'running'
    adapter['reader'].bridge.status = 'idle'
    assert agent(adapter)['status'] == 'idle'
    assert len(adapter['creation_bridge'].commands) == len(adapter['message_bridge'].commands) == 1
    assert adapter['message_bridge'].commands[0]['text'] == original
    adapter['reader'].bridge.status = 'closed'
    assert agent(adapter)['status'] == 'failed'


def test_uncertain_creation_is_never_replayed(adapter):
    def unavailable(command):
        raise OSError('synthetic unavailable')
    adapter['creation_bridge'].reply = unavailable
    assert agent(adapter)['status'] == 'unknown'
    assert agent(adapter)['status'] == 'unknown'
    assert len(adapter['creation_bridge'].commands) == 1
    assert not adapter['message_bridge'].commands


def test_stop_during_creation_prevents_followup_message(adapter):
    repo = adapter['api'].repository
    with repo.connect(write=True) as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS iteration_round_invocations '
                     '(id TEXT PRIMARY KEY, iteration_id TEXT, record TEXT, stop_actor TEXT, stop_at REAL)')
        conn.execute("INSERT INTO iteration_round_invocations VALUES('invocation',?,'{}',NULL,NULL)",
                     (adapter['f']['tree']['id'],))
    def stop_then_created(command):
        with repo.connect(write=True) as conn:
            conn.execute("UPDATE iteration_round_invocations SET stop_actor='operator' WHERE id='invocation'")
        return created(command)
    adapter['creation_bridge'].reply = stop_then_created
    result = agent(adapter, key='round/invocation/1/analyst')
    assert result['status'] == 'failed' and result['reason'] == 'operator_stop'
    assert len(adapter['creation_bridge'].commands) == 1
    assert not adapter['message_bridge'].commands


def test_select_rescans_current_baseline_but_never_changes_frozen_members(adapter):
    f, ops = adapter['f'], adapter['ops']
    before = f['store'].get(f['tree']['task_id'], 'c01')
    selected = ops.select(f['tree']['id'], f['tree']['root_candidate_id'], max_clusters=6, b=2, pi=.3)
    assert selected['cards'], json.dumps(selected)
    assert selected['cards'][0]['hypothesis_id'] in {adapter['selection']['id'], f['proposal']['id']}
    for scores in f['views'].scores.values():
        scores.update({'qasper:q': 1, 'qasper:q2': 1})
    rescanned = ops.select(f['tree']['id'], f['tree']['root_candidate_id'], max_clusters=6, b=2, pi=.3)
    assert rescanned['ranking'][0]['q'] == 1 and rescanned['ranking'][0]['score'] == 0
    assert f['store'].get(f['tree']['task_id'], 'c01') == before
    assert 'qasper:' not in json.dumps(rescanned)


def test_alce_unknown_attribution_remains_null_with_pi_fallback():
    from rag_eval.workbench.clusters import scan
    row = dict(case_id='search', status='success', response=dict(answer='unsupported'), metrics=dict(str_em=0))
    cluster = dict(primary='str_em', signature=dict(kind='range', field='metrics.str_em', max=.4))
    result = scan(dict(protocol=dict(splits=dict(search=['search']))), cluster,
                  [dict(execution_id='e', cases=[row])])
    assert result['q'] == 0 and result['m'] is None and result['v'] is None
    assert result['retrieval_ceiling'] is None and result['verification_ceiling'] is None


def test_partial_attribution_keeps_measurable_dimension(adapter):
    f, ops = adapter['f'], adapter['ops']
    original = f['views'].observation
    def without_missing_metric(run_id, cid):
        row = original(run_id, cid)
        row['stage_metrics'].pop('gold_evidence_recall', None)
        row['deterministic']['citation_invalid_count'] = 1
        return row
    f['views'].observation = without_missing_metric
    ranked = ops.select(f['tree']['id'], f['tree']['root_candidate_id'], max_clusters=6, b=2, pi=.3)['ranking'][0]
    assert ranked['m'] is None and ranked['v'] == 1
    assert ranked['missing_attribution'] == ['m']
    assert ranked['score'] == pytest.approx(ranked['n_cases'] * (1-ranked['q']))


def test_integration_card_is_metadata_only_and_not_selectable(adapter):
    f, ops = adapter['f'], adapter['ops']
    child = f['candidate']
    Path(child['worktree']['path'], 'backend/app/__init__.py').write_text('VALUE = 3\n')
    f['service'].freeze(child['id'], actor='alice')
    merged = ops.derive(f['tree']['root_candidate_id'], hypothesis_id=None, key='merge-derive', actor='alice',
                        integrated_from=[child['id']])
    again = ops.derive(f['tree']['root_candidate_id'], hypothesis_id=None, key='merge-derive', actor='alice',
                       integrated_from=[child['id']])
    assert merged == again
    card_id = f['service'].candidate(merged)['proposal_id']
    record = adapter['api'].hypotheses.get(f['tree']['task_id'], card_id)
    card = record['card']
    assert card['component'] == 'integration' and card['predictions'] == [] and 'contract' not in card
    assert f['service'].candidate(child['id'])['frozen_commit'] in card['implementer_brief']
    assert not ops._card_legal(f['tree']['task_id'], record, f['store'].get(f['tree']['task_id'], 'c01'))
    for component in ('retrieval', 'integration'):
        invalid = dict(card, component=component, cluster_id='c01')
        with pytest.raises(ValueError, match='predictions'):
            validate_card(invalid)


def test_implementation_waits_for_cluster_result_and_full_confirmed_base_request(adapter):
    f, ops, api = adapter['f'], adapter['ops'], adapter['api']
    candidate = f['candidate']
    cid = candidate['id']
    assert ops.implementation(cid)['reason'] == 'candidate_not_frozen'
    Path(candidate['worktree']['path'], 'backend/app/__init__.py').write_text('VALUE = 3\n')
    api.service.freeze(cid, actor='alice')
    assert ops.implementation(cid)['reason'] == 'cluster_runs_not_completed'
    request = api.requests.request(f['tree']['task_id'], 'node-cluster-run', cid, dict(cluster_id='c01'),
                                    reason='Cluster verification', requested_by='fixture')
    api.requests.finish(request['id'], error='synthetic execution failure')
    assert ops.implementation(cid)['status'] == 'failed'


def test_implementer_agent_only_receives_sanitized_approved_material(adapter):
    f = adapter['f']
    result = adapter['ops'].agent('implementer', f['candidate']['id'], iteration_id=f['tree']['id'],
        key='implementation-agent', profile='pi-flash', text='Implement the approved mechanism.',
        hypothesis_id=f['proposal']['id'])
    assert result['status'] == 'running', result
    body = adapter['message_bridge'].commands[0]['text']
    assert 'node cluster-run' in body and '--attempts 2' in body
    assert f['secret'] not in body and 'qasper:' not in body
    assert f['questions']['a'] not in body


def test_merge_derivation_recovers_crash_between_service_and_adapter_receipts(adapter, monkeypatch):
    f, ops = adapter['f'], adapter['ops']
    child = f['candidate']
    Path(child['worktree']['path'], 'backend/app/__init__.py').write_text('VALUE = 3\n')
    f['service'].freeze(child['id'], actor='alice')
    original = ops._save
    def crash(key, value):
        if key == ops._key('derive', 'crash-merge'):
            raise RuntimeError('crash after service receipt')
        return original(key, value)
    monkeypatch.setattr(ops, '_save', crash)
    with pytest.raises(RuntimeError, match='after service receipt'):
        ops.derive(f['tree']['root_candidate_id'], hypothesis_id=None, key='crash-merge', actor='alice',
                   integrated_from=[child['id']])
    count = len(f['service'].iteration(f['tree']['id'])['candidates'])
    monkeypatch.setattr(ops, '_save', original)
    result = ops.derive(f['tree']['root_candidate_id'], hypothesis_id=None, key='crash-merge', actor='alice',
                        integrated_from=[child['id']])
    assert len(f['service'].iteration(f['tree']['id'])['candidates']) == count
    assert f['service'].candidate(result)['integrated_from'][0]['candidate_id'] == child['id']


def test_agent_recovery_keeps_original_actor_after_server_actor_change(adapter):
    assert agent(adapter)['status'] == 'running'
    adapter['ops'].actor = 'new-server-actor'
    adapter['reader'].bridge.status = 'running'
    assert agent(adapter)['status'] == 'running'
    assert len(adapter['creation_bridge'].commands) == len(adapter['message_bridge'].commands) == 1


def test_iteration_scoped_analyst_creation_does_not_bind_baseline_node(adapter):
    f, ops = adapter['f'], adapter['ops']
    candidate = f['candidate']
    Path(candidate['worktree']['path'], 'backend/app/__init__.py').write_text('VALUE = 3\n')
    adapter['api'].service.freeze(candidate['id'], actor='alice')
    result = adapter['api'].service.run(candidate['id'], budget=BUDGET, actor='alice', request_key='evaluated-child')
    eid = result['attempts'][-1]['execution_id']
    f['views'].scores[eid] = {'qasper:q': 1, 'qasper:q2': 1}
    f['views'].partitions[eid] = [('qasper:q', 'qasper:q2')]
    finish(f['env'], result)
    out = agent(adapter, scope='iteration', key='iteration-agent')
    assert out['status'] == 'running', out
    intent = adapter['creation'].store.list(f['tree']['task_id'], actor='alice')[0]
    assert intent['scope'] == 'iteration' and not intent.get('candidate_id')


def test_actual_cluster_card_gate_and_fresh_exhaustion_are_persisted(adapter, monkeypatch):
    f, ops, api = adapter['f'], adapter['ops'], adapter['api']
    root, cid = f['tree']['root_candidate_id'], f['candidate']['id']
    # Same frozen source/attempt ledger, with an independent validation case.
    with api.repository.connect(write=True) as conn:
        protocol = json.loads(conn.execute('SELECT protocol_json FROM iterations WHERE id=?', (f['tree']['id'],)).fetchone()[0])
        protocol['splits'].update(search=['qasper:q'], validation=['qasper:q2'])
        conn.execute('UPDATE iterations SET protocol_json=? WHERE id=?', (json.dumps(protocol), f['tree']['id']))
    for values in f['views'].scores.values():
        values.update({'qasper:q': 0, 'qasper:q2': 0})
    Path(f['candidate']['worktree']['path'], 'backend/app/__init__.py').write_text('VALUE = 3\n')
    api.service.freeze(cid, actor='alice')
    for i in range(2):
        attempt = api.service.run(cid, budget=BUDGET, actor='alice', request_key=f'candidate-run-{i}')
        eid = attempt['attempts'][-1]['execution_id']
        f['views'].scores[eid] = {'qasper:q': 1, 'qasper:q2': 1}
        f['views'].partitions[eid] = [('qasper:q', 'qasper:q2')]
        finish(f['env'], attempt)
    # Existing checker reads added source diff via normal candidate binding.
    f['views']._binding_read = lambda task_id: ({'files': []}, None)
    verdict = ops.gate(cid, key='gate', actor='alice')
    assert verdict['verdict'] == 'accept'
    assert api.service.candidate(cid)['decision']['gate_verdict']['verdict'] == 'accept'
    monkeypatch.setattr(api, 'check', lambda *a: pytest.fail('unchanged gate checked again'))
    assert ops.gate(cid, key='gate', actor='alice') == verdict
    assert ops.fresh(cid, key='fresh', actor='alice', infra_retry=1, timeout_seconds=60)['status'] == 'exhausted'
    assert ops.fresh(cid, key='fresh', actor='alice', infra_retry=1, timeout_seconds=60)['status'] == 'exhausted'


@pytest.mark.parametrize('delta,eta,status', [(-.2, .1, 'withdrawn'), (-.1, .1, 'accepted'),
                                            (None, .1, 'unmeasurable')])
def test_merge_result_uses_validation_noise_and_replays_decision(adapter, monkeypatch, delta, eta, status):
    ops, api, cid, baseline = adapter['ops'], adapter['api'], adapter['f']['candidate']['id'], adapter['f']['tree']['root_candidate_id']
    comparison = dict(admission=dict(status='admitted'), validation=dict(delta=delta, incomplete=False), noise=dict(eta=eta))
    monkeypatch.setattr(api.comparisons, 'pinned', lambda *args: comparison)
    decisions = []
    original = api.service.candidate
    monkeypatch.setattr(api.service, 'candidate', lambda identity: dict(original(identity), decision=None))
    monkeypatch.setattr(api.service, 'decide', lambda *args, **kwargs: decisions.append((args, kwargs)))
    result = ops.merge_result(cid, baseline, key='merge-verdict', actor='alice')
    assert result['status'] == status
    monkeypatch.setattr(api.comparisons, 'pinned', lambda *args: pytest.fail('recomputed frozen merge result'))
    assert ops.merge_result(cid, baseline, key='merge-verdict', actor='alice') == result
    assert len(decisions) == (0 if status == 'unmeasurable' else 1)
    if decisions:
        assert decisions[0][0][1] == ('adopted' if status == 'accepted' else 'rejected')


def test_new_pending_request_is_not_budget_exhaustion_and_not_implementation_ready(adapter):
    f, ops, api = adapter['f'], adapter['ops'], adapter['api']
    cid = f['candidate']['id']
    Path(f['candidate']['worktree']['path'], 'backend/app/__init__.py').write_text('VALUE = 3\n')
    api.service.freeze(cid, actor='alice')
    with api.repository.connect(write=True) as conn:
        row = conn.execute('SELECT protocol_json FROM iterations WHERE id=?', (f['tree']['id'],)).fetchone()
        protocol = json.loads(row[0])
        protocol['budget'] = dict(limit_cny=1, max_runs_per_candidate=100)
        conn.execute('UPDATE iterations SET protocol_json=? WHERE id=?', (json.dumps(protocol), f['tree']['id']))
    request = api.requests.request(f['tree']['task_id'], 'node-cluster-run', cid, dict(cluster_id='c01'),
                                    reason='Cluster verification', requested_by='fixture')
    pending = ops.implementation(cid)
    assert pending['status'] == 'running' and not pending['budget_exhausted']
    assert pending['awaiting_confirmation']
    # A durable frozen dispatch footprint exceeding budget is a real exhaustion.
    from rag_eval.workbench.execution_scopes import initialize
    initialize(api.repository)
    steps = [dict(candidate_id=cid, request_key='synthetic-step', batch_id='cluster:c01', case_count=100000)]
    with api.repository.connect(write=True) as conn:
        conn.execute('INSERT INTO action_dispatches VALUES(?,?,?,?)',
                     (request['id'], f['tree']['id'], json.dumps(steps), '2026-10-26'))
    blocked = ops.implementation(cid)
    assert blocked['status'] == 'running' and blocked['budget_exhausted']


def test_gate_returns_topup_revision_when_sibling_expands_validation_pool(adapter, monkeypatch):
    ops, api, cid = adapter['ops'], adapter['api'], adapter['f']['candidate']['id']
    monkeypatch.setattr(api.comparisons, 'for_candidate', lambda *args, **kwargs: dict(parent=dict(
        id='immutable-new-pool', validation=dict(missing_batches=['fresh:2', 'fresh:1']))))
    monkeypatch.setattr(api, 'check', lambda *args: pytest.fail('persisted premature gate verdict'))
    first = ops.gate(cid, key='gate', actor='alice')
    assert first['verdict'] == 'pending' and len(first['measurement_revision']) == 64
    monkeypatch.setattr(api.comparisons, 'for_candidate', lambda *args, **kwargs: dict(parent=dict(
        id='immutable-new-pool', validation=dict(missing_batches=['fresh:1', 'fresh:2']))))
    assert ops.gate(cid, key='gate', actor='alice')['measurement_revision'] == first['measurement_revision']


def test_gate_passes_exact_observed_comparison_to_check(adapter, monkeypatch):
    ops, api, cid = adapter['ops'], adapter['api'], adapter['f']['candidate']['id']
    observed = dict(id='frozen-observed-record', validation=dict(missing_batches=[]))
    calls = []
    def compare(*args, **kwargs):
        calls.append('compare')
        assert len(calls) == 1
        return dict(parent=observed)
    monkeypatch.setattr(api.comparisons, 'for_candidate', compare)
    def check(identity, *, comparison=None):
        assert identity == cid and comparison is observed
        return dict(verification=dict(verdict=dict(verdict='accept')))
    monkeypatch.setattr(api, 'check', check)
    assert ops.gate(cid, key='pinned-gate', actor='alice')['verdict'] == 'accept'


def test_cluster_retry_waits_and_requires_new_footprint_result_read(adapter, monkeypatch):
    f, ops, api = adapter['f'], adapter['ops'], adapter['api']
    cid = f['candidate']['id']
    Path(f['candidate']['worktree']['path'], 'backend/app/__init__.py').write_text('VALUE = 3\n')
    api.service.freeze(cid, actor='alice')
    api.service.cluster_run(cid, 'c01', budget=BUDGET, actor='alice', request_key='cluster-initial')
    # One terminal failure must enter the cluster-specific retry port; no base
    # measurement may bypass the cluster stage.
    candidate = api.service.candidate(cid)
    attempts = [a for a in candidate['attempts'] if a.get('batch_id') == 'cluster:c01']
    seen = []
    def retry(candidate_id, cluster_id, **kwargs):
        seen.append((candidate_id, cluster_id, kwargs))
        return dict(status='running')
    monkeypatch.setattr(ops.measurements, 'cluster', retry)
    waiting = ops.implementation(cid, key='round/invocation/1/implementation', actor='frozen-actor',
                                 infra_retry=3, timeout_seconds=90)
    assert waiting['status'] == 'running' and waiting['execution_in_flight']
    assert seen[0][2] == dict(key='round/invocation/1/implementation', actor='frozen-actor',
                              infra_retry=3, timeout_seconds=90)
    monkeypatch.setattr(ops.measurements, 'cluster', lambda *args, **kwargs: dict(status='failed'))
    assert ops.implementation(cid)['status'] == 'failed'
    # Mark own and parent cluster attempts terminal and publish the current read.
    for node in (cid, f['tree']['root_candidate_id']):
        for attempt in api.service.candidate(node)['attempts']:
            if attempt.get('batch_id') != 'cluster:c01':
                continue
            eid = attempt['execution_id']
            f['views'].scores[eid] = {'qasper:q': 1, 'qasper:q2': 1}
            f['views'].partitions[eid] = [('qasper:q', 'qasper:q2')]
            f['env']['executions']._finish(eid, state='finished', exit_code=0, error_type=None,
                                           run_state=dict(phase='finished'))
    monkeypatch.setattr(ops.measurements, 'cluster', lambda *args, **kwargs: dict(status='complete'))
    assert ops.implementation(cid)['reason'] == 'cluster_result_not_read'
    api.comparisons.for_cluster(cid, 'c01', actor='agent:implementer')
    assert ops.implementation(cid)['reason'] == 'full_base_runs_not_requested'
    # A later terminal retry changes comparison identity; an older read is stale.
    later = api.service.run(cid, batch_id='cluster:c01', budget=BUDGET, actor='alice', request_key='later-retry')
    eid = later['attempts'][-1]['execution_id']
    f['views'].scores[eid] = {'qasper:q': 1, 'qasper:q2': 1}
    f['views'].partitions[eid] = [('qasper:q', 'qasper:q2')]
    finish(f['env'], later)
    refreshed = ops.implementation(cid)
    assert refreshed['reason'] == 'full_base_runs_not_requested'
    assert refreshed['observed_original_read'] and refreshed['refreshed_cluster_comparison_id']
    request = api.requests.request(f['tree']['task_id'], 'node-run', cid, dict(attempts=2),
                                    reason='full base run after read', requested_by='fixture')
    api.requests.claim(request['id'], actor='fixture')
    assert ops.implementation(cid)['status'] == 'running'
    api.requests.finish(request['id'], result=dict(status='dispatched'))
    assert ops.implementation(cid)['status'] == 'ready'


def test_cluster_monitor_waits_for_original_dispatch_but_not_owned_retry(adapter, monkeypatch):
    f, ops, api = adapter['f'], adapter['ops'], adapter['api']
    cid = f['candidate']['id']
    Path(f['candidate']['worktree']['path'], 'backend/app/__init__.py').write_text('VALUE = 3\n')
    api.service.freeze(cid, actor='alice')
    request = api.requests.request(f['tree']['task_id'], 'node-cluster-run', cid, dict(cluster_id='c01'),
                                    reason='initial cluster dispatch', requested_by='fixture')
    api.requests.claim(request['id'], actor='fixture')
    from rag_eval.workbench.execution_scopes import cluster_plan
    cluster_plan(api.service, cid, 'c01')
    api.service.run(cid, batch_id='cluster:c01', budget=BUDGET, actor='alice', request_key='first-child')
    calls = []
    def observe(candidate_id, cluster_id, **kwargs):
        own = [a for a in api.service.candidate(candidate_id)['attempts'] if a.get('batch_id') == 'cluster:' + cluster_id]
        calls.append(own)
        return dict(status='running')
    monkeypatch.setattr(ops.measurements, 'cluster', observe)
    result = ops.implementation(cid)
    assert result['reason'] == 'initial_cluster_dispatch_in_progress' and not calls
    second = api.service.run(cid, batch_id='cluster:c01', budget=BUDGET, actor='alice', request_key='second-child')
    failed_eid = second['attempts'][-1]['execution_id']
    f['env']['executions']._finish(failed_eid, state='failed', exit_code=1, error_type='InfrastructureError',
                                   run_state=dict(phase='failed'))
    api.requests.finish(request['id'], result=dict(status='dispatched'))
    assert ops.implementation(cid)['status'] == 'running'
    assert len(calls[0]) == 2 and any(a['execution_state'] == 'failed' for a in calls[0])
    # Coordinator retry ownership is durable, not inferred from action wording.
    retry = api.requests.request(f['tree']['task_id'], 'node-cluster-run', cid, dict(cluster_id='c01'),
                                  reason='arbitrary operator-visible label', requested_by='fixture')
    api.requests.claim(retry['id'], actor='fixture')
    with api.repository.connect(write=True) as conn:
        conn.execute('INSERT INTO round_measurements VALUES(?,?)',
                     ('ownership-fixture', json.dumps(dict(actions=[retry['id']]))))
    assert ops.implementation(cid)['reason'] == 'cluster_infrastructure_retry_running'
    assert len(calls) == 2


def test_observation_failure_is_retryable_and_followup_is_once(adapter, monkeypatch):
    first = agent(adapter)
    original = adapter['reader'].read_agent
    monkeypatch.setattr(adapter['reader'], 'read_agent', lambda *a: dict(result=dict(status='unavailable')))
    assert agent(adapter)['status'] == 'running'
    monkeypatch.setattr(adapter['reader'], 'read_agent', original)
    second = agent(adapter, message_stage='cards_missing/1', text='Finish drafting cards.')
    assert second['session_id'] == first['session_id']
    agent(adapter, message_stage='cards_missing/1', text='Finish drafting cards.')
    assert len(adapter['creation_bridge'].commands) == 1
    assert len(adapter['message_bridge'].commands) == 2


def test_pending_freeze_is_confirmation_wait(adapter):
    f, ops, api = adapter['f'], adapter['ops'], adapter['api']
    api.requests.request(f['tree']['task_id'], 'node-freeze', f['candidate']['id'], {},
                         reason='freeze', requested_by='fixture')
    assert ops.implementation(f['candidate']['id'])['awaiting_confirmation'] is True


def test_confirmed_base_dispatch_fences_measurement_topups(adapter, monkeypatch):
    f, ops, api = adapter['f'], adapter['ops'], adapter['api']
    cid = f['candidate']['id']
    Path(f['candidate']['worktree']['path'], 'backend/app/__init__.py').write_text('VALUE = 3\n')
    api.service.freeze(cid, actor='alice')
    monkeypatch.setattr('rag_eval.workbench.loop_services.referenced_cluster', lambda _: None)
    request = api.requests.request(f['tree']['task_id'], 'node-run', cid, dict(attempts=2),
                                   reason='dispatching', requested_by='fixture')
    api.requests.claim(request['id'], actor='alice', note='dispatching')
    result = ops.implementation(cid)
    assert result['status'] == 'running'
    assert result['execution_in_flight'] is True
    assert result['reason'] == 'initial_node_dispatch_in_progress'


def test_provider_idle_queue_uses_configured_start_grace(adapter, monkeypatch):
    from datetime import datetime, timedelta, timezone
    agent(adapter)
    store = adapter['message'].store
    original = store.list
    def aged(*args, **kwargs):
        return [dict(m, finished_at=(datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat())
                for m in original(*args, **kwargs)]
    monkeypatch.setattr(store, 'list', aged)
    assert agent(adapter)['status'] == 'running'
    assert agent(adapter, start_grace_seconds=45)['status'] == 'idle'
