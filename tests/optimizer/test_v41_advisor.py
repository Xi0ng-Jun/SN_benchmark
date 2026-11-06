import json
from types import SimpleNamespace

import pytest

from rag_eval.optimizer import ledger_io
from rag_eval.optimizer.adapter import EvalResult
from rag_eval.optimizer.v4 import advisor, calibration_session as session, proposal
from rag_eval.optimizer.v4.config import V4Config
from rag_eval.optimizer.v4.loop import V4Run
from rag_eval.optimizer.v4.simulate import simulate
from rag_eval.workbench import auto_runs
from .test_v41_calibration_session import calibration_env, env, eight_papers, measure, close  # noqa: F401
from .test_v4_rehearsal import rig4  # noqa: F401


def base(rig):
    bundle = measure(rig)
    proposed = proposal.Proposal.from_dict(ledger_io.read_json(rig[3] / session.PROPOSAL_FILE))
    return bundle, proposed


def test_advisor_rejects_out_of_range_and_unbound_simulations(calibration_env):
    bundle, proposed = base(calibration_env)
    values = dict(b=dict(value=100, reason='too many'),
                  max_concurrent_executions=dict(value=4, sim_id='fabricated', sim_params=proposed.params()),
                  calibration_k=dict(value=5))
    accepted = advisor.accept(proposed, dict(values=values), bundle)
    assert accepted.values() == proposed.values()
    assert accepted.notes['advisor']['rejected']


def test_advisor_accepts_recomputed_simulation_and_preserves_user_fields(calibration_env):
    bundle, proposed = base(calibration_env)
    params = dict(proposed.params(), max_concurrent_executions=4)
    sim = simulate(params, bundle['summary'], bundle['history'])
    raw = dict(values={'max_concurrent_executions': dict(value=4, sim_id=sim['sim_id'], sim_params=params,
        reason='more execution slots', confidence='medium')}, failure_modes=['empty retrieval'], message='Estimate only')
    accepted = advisor.accept(proposed, raw, bundle)
    assert accepted.items['max_concurrent_executions'].source == 'agent'
    overridden = proposal.apply_user_edit(accepted, 'max_concurrent_executions', 2)
    persisted = proposal.Proposal.from_dict(overridden.to_dict())
    checked = proposal.validate(persisted, bundle['summary'], bundle['history'])
    assert checked['ok']
    assert checked['proposal'].items['max_concurrent_executions'].agent_value == 4
    assert checked['proposal'].items['max_concurrent_executions'].value == 2
    edited = proposal.apply_user_edit(proposed, 'max_concurrent_executions', 2)
    assert advisor.accept(edited, raw, bundle).items['max_concurrent_executions'].value == 2


def mock_bridge(rig, responder):
    adapter, root = rig[2:]
    adapter._request_key = lambda *parts: '/'.join(['v4', 'advisor-test', *parts])
    adapter.candidate_id = lambda seed: 'seed'
    adapter._track_agent = lambda *args: None
    adapter.diagnostic_views = lambda traces: {t: dict(question='D:' + t) for t in traces}
    calls, cancelled = [], []
    def agent(role, candidate, **kwargs):
        calls.append((role, candidate, kwargs))
        return responder(root)
    adapter._round_ops = SimpleNamespace(agent=agent)
    adapter._cancel_agent = lambda *args: cancelled.append(args)
    return adapter, calls, cancelled


def test_mock_bridge_receives_only_d_and_completed_advice_is_not_repeated(calibration_env):
    bundle, proposed = base(calibration_env)
    def reply(root):
        ledger_io.atomic_write_json(root / 'proposal.agent.json', dict(values={}, failure_modes=['retrieval'], message='limits'))
        return dict(status='idle', session_id='one')
    adapter, calls, cancelled = mock_bridge(calibration_env, reply)
    out = advisor.run(adapter, adapter.seed, calibration_env[3], profile='mock')
    assert out.notes['advisor']['status'] == 'complete'
    payload = (calibration_env[3] / 'advisor-evidence.json').read_text()
    assert all(task not in payload for task in bundle['probe'])
    assert all('D:' + task in payload for task in bundle['diagnosis'])
    assert calls[0][0] == 'calibrator' and cancelled[0][1] == 'one'
    advisor.run(adapter, adapter.seed, calibration_env[3], profile='mock')
    assert len(calls) == 1


def test_timeout_falls_back_to_rules_and_closes_only_advisor(calibration_env):
    bundle, proposed = base(calibration_env)
    adapter, calls, cancelled = mock_bridge(calibration_env, lambda root: dict(status='running', session_id='late'))
    clock = SimpleNamespace(now=0.)
    adapter._runtime_wait = lambda seconds: setattr(clock, 'now', clock.now + 601)
    out = advisor.run(adapter, adapter.seed, calibration_env[3], profile='mock', clock=lambda: clock.now)
    assert out.values() == proposed.values()
    assert out.notes['advisor']['reason'] == 'TimeoutError'
    assert len(calls) == len(cancelled) == 1
    # A late output cannot overwrite the durable fallback.
    ledger_io.atomic_write_json(calibration_env[3] / 'proposal.agent.json', dict(values={'b': {'value': 16}}))
    assert advisor.run(adapter, adapter.seed, calibration_env[3], profile='mock').values() == proposed.values()


def test_unknown_exception_reports_stage_without_leaking_raw_message(calibration_env):
    base(calibration_env)
    def fail(root):
        raise ValueError('password=private https://private.invalid/case')
    adapter, _, _ = mock_bridge(calibration_env, fail)
    out = advisor.run(adapter, adapter.seed, calibration_env[3], profile='mock')
    note = out.notes['advisor']
    assert note['reason'] == 'ValueError' and note['stage'] == 'agent_request'
    assert 'private' not in json.dumps(note)


def test_role_allowlist_and_root_run_binding(calibration_env, monkeypatch):
    service, tree, adapter, path = calibration_env
    bundle, proposed = base(calibration_env)
    repo = service.repository
    assert auto_runs.agent_refusal(repo, tree['task_id'], 'calibrator', 'calibration show')
    auto_runs.register(repo, tree['id'], run_id='cal', state_dir=str(path), actor='alice', method='method-v4')
    for cmd in ('cases', 'evidence', 'iteration ledger', 'node run', 'proposal draft'):
        assert auto_runs.agent_refusal(repo, tree['task_id'], 'calibrator', cmd)
    assert auto_runs.agent_refusal(repo, tree['task_id'], 'calibrator', 'simulate') is None
    from rag_eval.workbench import agent_tools
    monkeypatch.setattr(agent_tools, 'iteration_service', lambda *a, **k: service)
    opts = SimpleNamespace(task=tree['task_id'], run_id='wrong', node=tree['root_candidate_id'],
                           parent=None, candidate=None, command='calibration show')
    with pytest.raises(ValueError, match='run-id'):
        advisor.tool(opts, repo, None)
    opts.run_id = 'cal'
    assert advisor.tool(opts, repo, None)['complete']
    opts.node = 'someone-else'
    with pytest.raises(ValueError, match='seed'):
        advisor.tool(opts, repo, None)


def test_failure_classification_is_given_only_to_first_analysis(calibration_env):
    bundle, proposed = base(calibration_env)
    _, _, adapter, path = calibration_env
    run = V4Run(adapter, adapter.seed, V4Config(time_budget_hours=1), search=bundle['diagnosis'],
        validation=bundle['probe'], frozen_config=dict(advisor=dict(failure_modes=['empty retrieval'])))
    calls = []
    adapter.analyse = lambda *a, **kw: calls.append(kw) or []
    run._diagnostics[adapter.seed.content_hash] = EvalResult(**bundle['results']['diagnosis'])
    try:
        run._analysis(adapter.seed, 1, [], [], 0)
        run._analysis(adapter.seed, 2, [], [], 0)
        assert calls[0]['calibration_advice'] == ['empty retrieval']
        assert 'calibration_advice' not in calls[1]
    finally:
        close(run)


def test_creation_store_binds_calibrator_to_seed_and_evaluation_workspace(rig4):
    from rag_eval.workbench.briefs import resolve_brief
    auto_runs.register(rig4.repo, rig4.tree['id'], run_id='cal', state_dir=str(rig4.run_dir),
                       actor='alice', method='method-v4')
    root = rig4.tree['root_candidate_id']
    store = rig4.creation.store
    intent = store.prepare(rig4.task_id, 'pi-flash', actor='alice', request_key='calibrator-test',
                           role='calibrator', candidate_id=root, brief='method-v4')
    created = store.attempt(rig4.task_id, intent['id'], actor='alice', bridge=rig4.creation.bridge)
    assert created['status'] == 'created'
    _, brief, refs = resolve_brief(rig4.repo, rig4.task_id, created['session_id'], tool='sn-eval')
    assert '--role calibrator' in brief and '--run-id cal' in brief
    assert refs['candidate_id'] == root
    with rig4.repo.connect() as conn:
        workspace = conn.execute('SELECT w.location FROM workspaces w JOIN tasks t ON t.workspace_id=w.id '
                                 'WHERE t.id=?', (rig4.task_id,)).fetchone()
    assert intent['command']['expectedCwd'] == workspace['location']
    with pytest.raises(ValueError, match='seed'):
        store.prepare(rig4.task_id, 'pi-flash', actor='alice', request_key='wrong-calibrator',
                      role='calibrator', candidate_id='different', brief='method-v4')
