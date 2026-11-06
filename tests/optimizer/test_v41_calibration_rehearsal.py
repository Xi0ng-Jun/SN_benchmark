"""Calibration CLI over real service receipts; synthetic execution/scoring only."""
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
