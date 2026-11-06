"""Real deferred split service, deterministic offline measurement adapter."""
import dataclasses
import json
from types import SimpleNamespace

import pytest

from rag_eval.optimizer import ledger_io
from rag_eval.optimizer.v4 import calibration_session as session, calibrate, cli, proposal
from rag_eval.optimizer.v4.config import load
from rag_eval.optimizer.v4.loop import V4Run
from rag_eval.optimizer.v4.simulate import simulate
from test_workbench_iterations import env, iteration  # noqa: F401
from .test_sn_rehearsal import eight_papers  # noqa: F401
from .v4_mock import V4Mock


@pytest.fixture
def calibration_env(env, tmp_path):
    ids = [f'qasper:p{p}q{q}' for p in range(8) for q in range(3)]
    tree = iteration(env, case_ids=ids, split_mode='deferred')
    service = env['service']
    service.register_calibration_samples(tree['id'], ids[:3], ids[3:6], actor='alice')
    adapter = V4Mock([], base=.8)
    return service, tree, adapter, tmp_path / 'calibration'


def measure(rig, k=3):
    service, tree, adapter, path = rig
    return session.run_calibration(adapter, adapter.seed, service, tree['id'], 'alice', path,
        dict(time_budget_hours=2, calibration_k=k))


def close(run):
    for pool in [*run._pools.values(), run._prefetch_pool, run._analysis_pool]:
        pool.shutdown(wait=True)


@pytest.mark.parametrize('k', [2, 5])
def test_calibrate_freeze_and_import_reuses_p_d_and_only_fills_remaining_validation(calibration_env, k):
    service, tree, adapter, path = calibration_env
    bundle = measure(calibration_env, k)
    assert bundle['summary']['k'] == k
    assert bundle['summary']['cold_wall_s'] is None and bundle['summary']['warm_wall_s'] is None
    assert bundle['summary']['rerun_var'] == pytest.approx(calibrate._aa(adapter._result(adapter.seed, bundle['probe'], k))['rerun_var'])
    calls = dict(adapter.calls)
    assert measure(calibration_env, k) == bundle and adapter.calls == calls
    frozen, imported = session.freeze_run(service, tree['id'], 'alice', path)
    assert frozen['config']['split']['validation_size'] == len(frozen['split']['validation'])
    runtime = session.runtime_config(frozen, bundle)
    ledger_io.atomic_write_json(path / 'runtime.json', runtime)
    cfg = load(path / 'runtime.json')
    assert cfg.adapter_settings['execution_timeout_seconds'] == frozen['config']['adapter']['execution_timeout_seconds']
    assert cfg.adapter_settings['max_concurrent_measurements'] == cfg.config.max_concurrent_executions
    assert cfg.config.calibration_k == k
    run = V4Run(adapter, adapter.seed, cfg.config, **frozen['split'], state_dir=path,
                calibration_bundle=bundle, frozen_config=frozen)
    try:
        run._calibrate()
        assert set(run.parent_val(adapter.seed).per_task_scores) == set(frozen['split']['validation'])
        assert set(run._diagnose(adapter.seed).per_task_scores) == set(bundle['diagnosis'])
        assert adapter.calls['subset:probe'] == calls['subset:probe']
        assert adapter.calls['subset:rest'] == 1
        after = dict(adapter.calls)
        run._calibrate()
        assert adapter.calls == after
        assert run.forecast and run.feasibility is None
    finally:
        close(run)
    assert session.freeze_run(service, tree['id'], 'alice', path)[0] == frozen
    public = json.dumps(session.public_summary(bundle))
    assert not any(task in public for task in tree['protocol']['splits']['pool'])


def test_interruption_after_probe_resumes_only_diagnosis_and_identity_is_checked(calibration_env, monkeypatch):
    import threading
    _, _, adapter, _ = calibration_env
    original = adapter.evaluate_search
    subset = adapter.evaluate_subset
    completed = threading.Event()
    def probe(*args, **kwargs):
        result = subset(*args, **kwargs)
        if args[2] == 'probe' and args[4] == 3:
            completed.set()
        return result
    def interrupted(*args, **kwargs):
        assert completed.wait(3)
        raise KeyboardInterrupt()
    monkeypatch.setattr(adapter, 'evaluate_subset', probe)
    monkeypatch.setattr(adapter, 'evaluate_search', interrupted)
    with pytest.raises(KeyboardInterrupt):
        measure(calibration_env)
    calls = adapter.calls['subset:probe']
    monkeypatch.setattr(adapter, 'evaluate_search', original)
    measure(calibration_env)
    assert adapter.calls['subset:probe'] == calls
    with pytest.raises(ValueError, match='identity'):
        measure(calibration_env, 5)


def test_freeze_rejects_impossible_count_and_tampering(calibration_env):
    service, tree, _, path = calibration_env
    bundle = measure(calibration_env)
    proposed = proposal.Proposal.from_dict(ledger_io.read_json(path / session.PROPOSAL_FILE))
    invalid = proposal.apply_user_edit(proposed, 'validation_size', len(bundle['probe']) + 1)
    with pytest.raises(ValueError, match='whole-paper'):
        session.freeze_run(service, tree['id'], 'alice', path, invalid)
    session.freeze_run(service, tree['id'], 'alice', path)
    changed = proposal.apply_user_edit(proposed, 'b', 1 if proposed.values()['b'] != 1 else 2)
    with pytest.raises(ValueError):
        session.freeze_run(service, tree['id'], 'alice', path, changed)
    saved = ledger_io.read_json(path / session.CALIBRATION_FILE)
    saved['results']['probe']['per_task_scores'][bundle['probe'][0]] = 1
    ledger_io.atomic_write_json(path / session.CALIBRATION_FILE, saved)
    with pytest.raises(ValueError, match='identity'):
        session.load_bundle(path / session.CALIBRATION_FILE)


def test_real_cache_receipts_override_execution_order_and_overhead_is_not_invented():
    timing, source = session._timing([
        dict(wall_s=30, cases=3, cache_hits=1), dict(wall_s=40, cases=3, cache_misses=1),
        dict(wall_s=34, cases=3, cache_hits=1)], 104, 3, 3)
    assert timing['cold_wall_s'] == 40 and timing['warm_wall_s'] == 32
    assert timing['exec_overhead_s'] == 0 and 'assumed' in source
    measured, source = session._timing([
        dict(wall_s=16, cases=3, cache_hits=1), dict(wall_s=22, cases=6, cache_hits=1)], 38, 3, 2)
    assert measured['exec_overhead_s'] == 10 and measured['per_task_s'] == 2
    assert source.startswith('fit')


def test_missing_token_usage_does_not_become_zero_cost(calibration_env):
    bundle = measure(calibration_env)
    assert bundle['summary']['tokens_per_task'] is None
    p = proposal.Proposal.from_dict(ledger_io.read_json(calibration_env[3] / session.PROPOSAL_FILE))
    assert simulate(dict(p.params(), price_per_1k_tokens=1), bundle['summary'])['spend']['tokens'] is None
    with pytest.raises(ValueError, match='measured token'):
        simulate(dict(p.params(), spend_budget=10, price_per_1k_tokens=1), bundle['summary'])


@pytest.mark.parametrize('change_after_freeze', [False, True])
def test_cli_calibrated_run_uses_every_frozen_field_and_does_not_recalibrate(calibration_env, monkeypatch, change_after_freeze):
    service, tree, adapter, path = calibration_env
    bundle = measure(calibration_env)
    adapter.version_for = lambda cid: adapter.seed
    from rag_eval.workbench.agent_creations import AgentCreationStore
    workspace_id = service.repository.task(tree['task_id'])['workspace_id']
    binding = dict(workspaceId='test-workspace', profiles={'pi-flash': dict(provider='codex/test')})
    store = AgentCreationStore(service.repository, {workspace_id: binding})
    adapter._round_ops = SimpleNamespace(service=service, creations=SimpleNamespace(store=store))
    adapter._protocol = lambda: service.iteration(tree['id'])['protocol']
    adapter.partitions = lambda: dict(adapter._protocol()['splits'], fresh_batches=[], k_base=2)
    adapter.set_evidence_dir = lambda p: None
    settings_seen = []
    def build(*a, **kw):
        settings_seen.append(kw['adapter_settings'])
        adapter._config = dict(agent_creation=json.loads(json.dumps(binding)))
        if change_after_freeze and len(settings_seen) == 2:
            adapter._config['agent_creation']['profiles']['pi-flash']['provider'] = 'codex/unapproved'
        return cli.Services(adapter, tree['id'], tree['task_id'], tree['root_candidate_id'], True)
    monkeypatch.setattr(cli, 'build_services', build)
    measured = dict(adapter.calls)
    def run_once(run):
        assert not change_after_freeze, 'changed model configuration reached optimisation'
        run._calibrate()
        frozen = run.frozen_config['config']
        for key, value in frozen.items():
            if key not in ('adapter', 'split'):
                assert getattr(run.cfg, key) == value
        assert len(run.partitions['validation']) == frozen['split']['validation_size']
        close(run)
        return 'time_budget'
    monkeypatch.setattr(V4Run, 'run', run_once)
    from rag_eval.optimizer.v4.agent_profiles import binding_digest
    expected = binding_digest(binding, dict(analyst_profile='pi-flash', implementer_profile='pi-flash', reviewer_profile='pi-flash'))
    code = cli.main(['run', '--from-proposal', str(path / 'proposal.json'), '--iteration', tree['id'],
                     '--actor', 'alice', '--run-id', 'test-run', '--state-dir', str(path), '--no-test-set',
                     '--expected-agent-profiles-sha256', expected])
    if change_after_freeze:
        assert code == 2 and len(settings_seen) == 2
        assert not (path / 'state.json').exists()
        return
    assert code == 0
    frozen = session._load(path / session.RUN_CONFIG_FILE)
    for key, value in frozen['config']['adapter'].items():
        assert settings_seen[-1][key] == value
    assert adapter.calls['subset:probe'] == measured['subset:probe']
    assert cli.main(['proposal', 'set', 'b=2', '--state-dir', str(path)]) == 2


def test_public_heatmap_preserves_missing_attempt_column(calibration_env):
    bundle = measure(calibration_env)
    probe = bundle['results']['probe']
    task = next(iter(probe['attempt_scores'][0]))
    original = probe['attempt_scores'][0].pop(task)
    public = session.public_summary(bundle)
    missing = next(row for row in public['heatmap'] if row['scores'][0] is None)
    assert missing['scores'] == [None, *[a[task] for a in probe['attempt_scores'][1:]]]
    assert 'qasper:' not in json.dumps(public)


def test_public_config_includes_frozen_timeouts_split_and_sources_without_private_values(calibration_env):
    from rag_eval.optimizer.v4.run_view import project
    service, tree, _, root = calibration_env
    measure(calibration_env)
    frozen, bundle = session.freeze_run(service, tree['id'], 'alice', root)
    resolved = session.runtime_config(frozen, bundle)
    resolved['adapter']['server_password'] = 'must-not-project'
    view = project({}, identity=dict(config_resolved=resolved), ledger=dict(frozen_config=frozen))
    for name in ('agent_timeout', 'execution_timeout_seconds', 'infra_retry'):
        key = 'adapter.' + name
        assert view['config'][key] == frozen['config']['adapter'][name]
        assert view['parameter_sources'][key] == frozen['sources'][key]
    assert view['config']['validation_size'] == frozen['config']['split']['validation_size']
    assert view['parameter_sources']['validation_size'] == frozen['sources']['validation_size']
    assert 'must-not-project' not in json.dumps(view)
    assert all(task not in json.dumps(view) for task in bundle['probe'])
