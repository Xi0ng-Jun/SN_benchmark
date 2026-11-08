"""Real creation stores + run state; only the remote model/executions are synthetic."""
import json

import pytest

from rag_eval.optimizer.v4 import cli
from rag_eval.workbench.agent_preflight import AgentPreflightError
from test_workbench_agent_creations import created
from .test_v4_rehearsal import rig4, env, eight_papers, card, GOOD, first_round_only  # noqa: F401


@pytest.fixture(autouse=True)
def recovery_pool(eight_papers, monkeypatch):
    from . import test_sn_rehearsal
    monkeypatch.setattr(test_sn_rehearsal, 'PAPERS', 36)


def test_deployment_refused_before_any_measurement_or_creation(rig4, monkeypatch):
    monkeypatch.setattr(rig4.creator, 'preflight', lambda _: (_ for _ in ()).throw(AgentPreflightError('SCOPE_MISMATCH')))
    monkeypatch.setattr(rig4.api.service, 'run', lambda *a, **k: pytest.fail('measurement before deployment preflight'))
    assert rig4.run4() == cli.EXIT_USAGE
    assert not (rig4.run_dir / 'state.json').exists()
    assert rig4.creator.commands == []


@pytest.mark.parametrize('legacy_interrupted,calibrated', [(False, False), (True, False), (True, True)])
def test_failed_analysis_explicit_recovery_reuses_baseline_and_preserves_receipts(rig4, monkeypatch, legacy_interrupted, calibrated):
    run = rig4.run4
    if calibrated:
        from test_workbench_iterations import iteration
        from test_workbench_loop_services import SAFEGUARDS
        rig4.case_ids = [f'qasper:p{p}q{q}' for p in range(30) for q in range(3)]
        rig4.tree = iteration(rig4.env, request_key='calibrated-agent-recovery', case_ids=rig4.case_ids,
                              split_mode='deferred', gate_rule='lcd-v2', safeguards=SAFEGUARDS, k_base=2)
        rig4.protocol, rig4.task_id = rig4.tree['protocol'], rig4.tree['task_id']
        rig4.role = {task: 'search' for task in rig4.case_ids}
        rig4.api.service.register_calibration_samples(rig4.tree['id'], rig4.case_ids[:3], rig4.case_ids[3:6], actor='operator')
        with rig4:
            assert rig4.v4('calibrate', '--v4-config', str(rig4.v4_config), '--calibration-k', '2', '--no-advisor') == 0
        assert rig4.v4('proposal', 'set', 'validation_size=6') == 0
        run = lambda: rig4.v4('run', '--from-proposal', str(rig4.run_dir / 'proposal.json'), '--no-test-set')
    rig4.creator.reply = lambda c: dict(result=dict(status='not_attempted', code='SCOPE_MISMATCH', agentId=c['agentId']),
                                      release=dict(status='ok'))
    with rig4:
        assert run() == cli.EXIT_HALTED
    st = rig4.state4()
    assert st['halt_reason'] == 'process_failure' and st['run_failures'] == 1
    assert 'SCOPE_MISMATCH' in st['failure_detail']
    assert st['current_round']['cards'] is None and st['current_round']['round'] == 1
    immutable = {name: (rig4.run_dir / name).read_bytes() for name in
                 ('calibration.json', 'run-config.json', 'resolved-config.json', 'baseline-remainder.json')
                 if (rig4.run_dir / name).exists()}
    before = rig4.api.service.candidate(rig4.tree['root_candidate_id'])['attempts']
    old = rig4.intents('analyst')[0]
    assert old['status'] == 'not_attempted'
    if legacy_interrupted:
        st.update(halt_reason=None, failure_detail=None, run_failures=0)
        (rig4.run_dir / 'state.json').write_text(json.dumps(st))
    # Ordinary resume neither creates a replacement nor remeasures the baseline.
    with rig4:
        assert run() == cli.EXIT_HALTED
    assert len(rig4.creator.commands) == 1
    assert rig4.api.service.candidate(rig4.tree['root_candidate_id'])['attempts'] == before
    if legacy_interrupted:
        st = rig4.state4()
        st.update(halt_reason=None, failure_detail=None, failure_kind=None, run_failures=0)
        (rig4.run_dir / 'state.json').write_text(json.dumps(st))
    args = ('recover-agent-creation', '--intent-id', old['id'])
    assert rig4.v4(*args) == 0   # preview has no write to the intent chain
    assert len(rig4.intents('analyst')) == 1
    assert rig4.v4(*args, '--confirm') == 0
    assert rig4.v4(*args, '--confirm') == 0
    assert len(rig4.intents('analyst')) == 2 and len(rig4.creator.commands) == 1
    assert rig4.creation.store.get(rig4.task_id, old['id'], actor=old['actor']) == old
    rig4.creator.reply = created
    rig4.effects = {'Recovered card': GOOD}
    rig4.analyst_cards = first_round_only([card('Recovered card')])
    with rig4:
        assert run() == 0
    assert rig4.state4()['rounds'][0]['cards']
    after = rig4.api.service.candidate(rig4.tree['root_candidate_id'])['attempts']
    assert after[:len(before)] == before
    # First candidate legitimately introduces a paired smoke measurement; all
    # calibration/validation/diagnosis receipts must remain exactly as before.
    assert all(a['batch_id'].startswith('smoke:') for a in after[len(before):])
    assert rig4.state4()['key_generation'] == 0
    assert len(rig4.intents('analyst')) >= 2
    assert rig4.creation.store.get(rig4.task_id, old['id'], actor=old['actor']) == old
    assert all((rig4.run_dir / name).read_bytes() == content for name, content in immutable.items())


def test_prepared_successor_binding_change_is_a_visible_agent_failure(rig4, monkeypatch):
    rig4.creator.reply = lambda c: dict(result=dict(status='not_attempted', code='SCOPE_MISMATCH', agentId=c['agentId']),
                                      release=dict(status='ok'))
    with rig4:
        assert rig4.run4() == cli.EXIT_HALTED
    old = rig4.intents('analyst')[0]
    assert rig4.v4('recover-agent-creation', '--intent-id', old['id'], '--confirm') == 0
    prepared = rig4.intents('analyst')
    attempts = rig4.api.service.candidate(rig4.tree['root_candidate_id'])['attempts']
    # Simulate another deployment after preparing the successor. The real store
    # must reject its now-stale command even though today's preflight succeeds.
    rig4.creation.store._bindings[rig4.env['workspace']['id']]['workspaceId'] = 'private-changed-binding'
    with rig4:
        assert rig4.run4() == cli.EXIT_HALTED
    st = rig4.state4()
    assert st['failure_kind'] == 'agent_request'
    assert 'AGENT_REQUEST_REFUSED' in st['failure_detail']
    assert 'private-changed-binding' not in st['failure_detail']
    assert len(rig4.creator.commands) == 1
    assert rig4.intents('analyst') == prepared
    assert rig4.api.service.candidate(rig4.tree['root_candidate_id'])['attempts'] == attempts


def test_child_preflight_error_is_visible_without_optimization_state(rig4, monkeypatch):
    from rag_eval.workbench import auto_runs
    from rag_eval.optimizer.v4.run_view import read_run
    from rag_eval.workbench.auto_run_control import AutoRunControl
    rig4.run_dir.mkdir()
    bound = auto_runs.register(rig4.repo, rig4.tree['id'], run_id='r4', state_dir=str(rig4.run_dir),
                              actor='operator', method='method-v4')
    healthy = rig4.creator.preflight
    monkeypatch.setattr(rig4.creator, 'preflight', lambda _: (_ for _ in ()).throw(AgentPreflightError('SCOPE_MISMATCH')))
    assert rig4.run4() == cli.EXIT_USAGE
    assert not (rig4.run_dir / 'state.json').exists()
    view = read_run(rig4.run_dir, alive=False, binding=bound)
    assert view['startup_error']['code'] == 'SCOPE_MISMATCH'
    ctl = AutoRunControl(rig4.api, rig4.config)
    assert ctl.calibration(rig4.tree['id'])['startup_error']['code'] == 'SCOPE_MISMATCH'
    assert not read_run(rig4.run_dir, alive=True, binding=bound).get('startup_error')
    assert not read_run(rig4.run_dir, alive=False, binding=dict(bound, actor='other')).get('startup_error')
    monkeypatch.setattr(rig4.creator, 'preflight', healthy)
    with rig4:
        assert rig4.run4() == 0
    assert not (rig4.run_dir / 'startup-error.json').exists()


def test_recovery_refuses_wrong_owner_foreign_intent_saved_cards_and_live_run(rig4):
    from rag_eval.optimizer.run_view import hold_run_lock
    rig4.creator.reply = lambda c: dict(result=dict(status='not_attempted', code='SCOPE_MISMATCH', agentId=c['agentId']),
                                      release=dict(status='ok'))
    with rig4:
        assert rig4.run4() == cli.EXIT_HALTED
    old = rig4.intents('analyst')[0]
    args = ('recover-agent-creation', '--intent-id', old['id'], '--confirm')
    assert rig4.v4(*args, '--actor', 'other') == cli.EXIT_USAGE
    foreign = rig4.creation.store.prepare(rig4.task_id, 'pi-flash', actor=old['actor'],
        request_key='foreign-round', role='analyst', candidate_id=old['candidate_id'], brief='method-v4')
    assert rig4.v4('recover-agent-creation', '--intent-id', foreign['id'], '--confirm') == cli.EXIT_USAGE
    with hold_run_lock(rig4.run_dir):
        assert rig4.v4(*args) == cli.EXIT_USAGE
    st = rig4.state4()
    st['current_round']['cards'] = []
    (rig4.run_dir / 'state.json').write_text(json.dumps(st))
    assert rig4.v4(*args) == cli.EXIT_USAGE
    assert len(rig4.creator.commands) == 1
    assert len(rig4.intents('analyst')) == 2  # original + the unrelated prepared request only
