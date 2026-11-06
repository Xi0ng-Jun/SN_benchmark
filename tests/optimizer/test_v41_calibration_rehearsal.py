"""Calibration CLI over real service receipts; synthetic execution/scoring only."""
import time
import pytest
from rag_eval.optimizer.v4.loop import V4Run
from rag_eval.optimizer.v4 import calibration_session as session
from test_workbench_iterations import iteration
from test_workbench_loop_services import SAFEGUARDS
from .test_v4_rehearsal import rig4, env, eight_papers  # noqa: F401


def test_deferred_cli_calibration_then_run_keeps_seed_probe_receipts(rig4, env, monkeypatch):
    rig4.tree = iteration(env, request_key='calibration-rehearsal', case_ids=rig4.case_ids,
                          split_mode='deferred', gate_rule='lcd-v2', safeguards=SAFEGUARDS, k_base=2)
    rig4.protocol = rig4.tree['protocol']
    rig4.task_id = rig4.tree['task_id']
    rig4.role = {task: 'search' for task in rig4.case_ids}
    with rig4:
        assert rig4.v4('calibrate', '--v4-config', str(rig4.v4_config), '--calibration-k', '2', '--no-advisor') == 0
    bundle = session.load_bundle(rig4.run_dir / session.CALIBRATION_FILE)
    candidate = rig4.api.service.candidate(rig4.tree['root_candidate_id'])
    before = [(a['attempt'], a['execution_id'], a['batch_id']) for a in candidate['attempts']]
    assert len(before) == 3  # two independent P roots, one D root
    monkeypatch.setattr(V4Run, '_stop_reason', lambda run: 'time_budget')
    with rig4:
        assert rig4.v4('run', '--from-proposal', str(rig4.run_dir / 'proposal.json')) == 0
    candidate = rig4.api.service.candidate(rig4.tree['root_candidate_id'])
    assert [(a['attempt'], a['execution_id'], a['batch_id']) for a in candidate['attempts']] == before
    assert rig4.messenger.commands == []
    assert bundle['summary']['cold_wall_s'] is None  # no fake cache claims from the rehearsal


@pytest.mark.parametrize('limit', [1, 2, 3])
def test_calibration_physical_execution_cap_and_overlap(rig4, env, monkeypatch, limit):
    rig4.tree = iteration(env, request_key='concurrent-calibration', case_ids=rig4.case_ids,
                          split_mode='deferred', gate_rule='lcd-v2', safeguards=SAFEGUARDS, k_base=2)
    rig4.protocol = rig4.tree['protocol']
    rig4.task_id = rig4.tree['task_id']
    rig4.role = {task: 'search' for task in rig4.case_ids}
    settle = rig4._settle
    snapshots = []
    waiting_since = None

    def observe_before_settling():
        nonlocal waiting_since
        running = [a for c in rig4.candidates() for a in c['attempts'] if a.get('execution_state') == 'running']
        if not running:
            return
        snapshots.append([a['batch_id'].split(':')[1] for a in running])
        target = min(limit, 2 if not rig4.finished else 4)
        waiting_since = waiting_since or time.monotonic()
        # Keep receipts running so admission is measured across launches, not
        # merely the number of dispatch threads. Bounded wait prevents deadlock.
        if len(running) < target and time.monotonic() - waiting_since < .5:
            return
        settle()
        waiting_since = None

    monkeypatch.setattr(rig4, '_settle', observe_before_settling)
    with rig4:
        assert rig4.v4('calibrate', '--v4-config', str(rig4.v4_config), '--calibration-k', '5',
                      '--calibration-concurrency', str(limit), '--no-advisor') == 0
    assert max(map(len, snapshots)) == limit
    if limit > 1:
        assert any('probe' in row and 'diag' in row for row in snapshots)
    bundle = session.load_bundle(rig4.run_dir / session.CALIBRATION_FILE)
    assert bundle['schedule']['concurrency'] == limit
    attempts = rig4.api.service.candidate(rig4.tree['root_candidate_id'])['attempts']
    assert len(attempts) == 6  # five P roots, one D root; no repeated cumulative requests
    before = {a['execution_id'] for a in attempts}
    with rig4:
        assert rig4.v4('calibrate', '--v4-config', str(rig4.v4_config), '--calibration-k', '5',
                      '--calibration-concurrency', str(limit), '--no-advisor') == 0
    assert {a['execution_id'] for a in rig4.api.service.candidate(rig4.tree['root_candidate_id'])['attempts']} == before


def test_real_d_growth_keeps_disjoint_scopes_and_freeze_reuses_both(rig4, env, monkeypatch):
    rig4.tree = iteration(env, request_key='growth-calibration', case_ids=rig4.case_ids,
                          split_mode='deferred', gate_rule='lcd-v2', safeguards=SAFEGUARDS, k_base=2)
    rig4.api.service.register_calibration_samples(rig4.tree['id'], rig4.case_ids[:3], rig4.case_ids[3:6], actor='operator')
    rig4.protocol = rig4.api.service.iteration(rig4.tree['id'])['protocol']
    rig4.task_id = rig4.tree['task_id']
    rig4.role = {task: 'search' for task in rig4.case_ids}
    with rig4:
        assert rig4.v4('calibrate', '--v4-config', str(rig4.v4_config), '--calibration-k', '2', '--no-advisor') == 0
    bundle = session.load_bundle(rig4.run_dir / session.CALIBRATION_FILE)
    batches = bundle['schedule']['diagnosis_batches']
    assert len(batches) == 2 and set(batches[0]['tasks']).isdisjoint(batches[1]['tasks'])
    scopes = bundle['results']['diagnosis']['context']['scopes']
    assert set(scopes) == set(bundle['diagnosis']) and len(set(scopes.values())) == 2
    before = {a['execution_id'] for a in rig4.api.service.candidate(rig4.tree['root_candidate_id'])['attempts']}
    assert len(before) == 4
    monkeypatch.setattr(V4Run, '_stop_reason', lambda run: 'time_budget')
    with rig4:
        assert rig4.v4('run', '--from-proposal', str(rig4.run_dir / 'proposal.json')) == 0
    assert {a['execution_id'] for a in rig4.api.service.candidate(rig4.tree['root_candidate_id'])['attempts']} == before
