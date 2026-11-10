"""Reuse a completed calibration in a new deferred iteration (real services, synthetic executions)."""
import hashlib
import json

import pytest

from rag_eval.optimizer.v4 import calibration_import as imp, calibration_session as session, cli as v4cli
from rag_eval.optimizer.v4.loop import V4Run
from test_workbench_iterations import iteration
from test_workbench_loop_services import SAFEGUARDS
from .test_v4_rehearsal import rig4, env, eight_papers  # noqa: F401


def _deferred(rig4, env, key):
    return iteration(env, request_key=key, case_ids=rig4.case_ids, split_mode='deferred',
                     gate_rule='lcd-v2', safeguards=SAFEGUARDS, k_base=2)


def _use(rig4, tree, run_dir):
    rig4.tree, rig4.protocol, rig4.task_id, rig4.run_dir = tree, tree['protocol'], tree['task_id'], run_dir
    rig4.role = {task: 'search' for task in rig4.case_ids}


def _cli(rig4, command, iteration_id, *extra, run_id=None, state_dir=None):
    argv = [command, '--config', 'unused.json', '--state', str(rig4.tmp / 'state'), '--iteration', iteration_id,
            '--actor', 'operator']
    if run_id:
        argv += ['--run-id', run_id]
    if state_dir:
        argv += ['--state-dir', str(state_dir)]
    return v4cli.main(argv + list(extra))


def _hashes(directory):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(directory.glob('*.json'))}


@pytest.fixture
def calibrated(rig4, env, monkeypatch):
    """A source iteration: calibration, then a run (this rig's validation set is exactly P)."""
    _use(rig4, _deferred(rig4, env, 'source'), rig4.tmp / 'source-run')
    with rig4:
        assert rig4.v4('calibrate', '--v4-config', str(rig4.v4_config), '--calibration-k', '2', '--no-advisor') == 0
    rig4.one_round = V4Run._stop_reason   # the rig's script: stop once a round has committed
    monkeypatch.setattr(V4Run, '_stop_reason', lambda run: 'time_budget')
    with rig4:
        assert rig4.v4('run', '--from-proposal', str(rig4.run_dir / 'proposal.json')) == 0
    return rig4.tree, rig4.run_dir


def test_import_reuses_calibration_without_measuring_the_seed(rig4, env, calibrated, capsys, monkeypatch):
    source, source_dir = calibrated
    old_root = rig4.api.service.candidate(source['root_candidate_id'])
    old_attempts, old_files = old_root['attempts'], _hashes(source_dir)
    target = _deferred(rig4, env, 'target')
    target_dir = imp.default_dir(rig4.tmp / 'state', target['id'])
    with pytest.raises(Exception):  # nothing measured yet: the new root is not evaluated
        rig4.api.service.draft_proposal(target['root_candidate_id'], {}, author='agent:analyst')

    assert _cli(rig4, 'import-calibration', target['id'], '--from-iteration', source['id']) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview['ok'] and preview['links'] and not target_dir.exists()   # the preview writes nothing
    assert rig4.api.service.candidate(target['root_candidate_id'])['links'] == []
    assert _cli(rig4, 'import-calibration', target['id'], '--from-iteration', source['id'],
                '--confirm', 'wrong') == v4cli.EXIT_USAGE
    assert _cli(rig4, 'import-calibration', target['id'], '--from-iteration', source['id'],
                '--confirm', preview['plan_sha256']) == 0
    capsys.readouterr()
    # Replaying the confirmation converges on the same receipt.
    assert _cli(rig4, 'import-calibration', target['id'], '--from-iteration', source['id'],
                '--confirm', preview['plan_sha256']) == 0
    receipt = json.loads((target_dir / imp.IMPORT_FILE).read_text())
    assert receipt['status'] == 'complete' and receipt['source']['iteration_id'] == source['id']

    root = rig4.api.service.candidate(target['root_candidate_id'])
    assert root['state'] == 'evaluated' and root['attempts'] == []
    assert {l['basis']['kind'] for l in root['links']} == {'calibration_import'}
    bundle = session.load_bundle(target_dir / session.CALIBRATION_FILE)
    assert bundle['identity']['iteration_id'] == target['id'] and bundle['imported_from']['iteration_id'] == source['id']
    proposed = json.loads((target_dir / session.PROPOSAL_FILE).read_text())
    assert proposed['notes']['imported_from']['iteration_id'] == source['id']

    _use(rig4, rig4.api.service.iteration(target['id']), target_dir)
    monkeypatch.setattr(V4Run, '_stop_reason', rig4.one_round)   # a full first round on the imported root
    from .test_sn_rehearsal import GOOD, card
    from .test_v4_rehearsal import first_round_only
    rig4.effects = {'Fix empty retrievals': GOOD}
    rig4.analyst_cards = first_round_only([card('Fix empty retrievals')])
    with rig4:
        assert _cli(rig4, 'run', target['id'], '--from-proposal', str(target_dir / 'proposal.json'),
                    run_id='v41-' + target['id'], state_dir=target_dir) == 0
    seeds = [a['batch_id'] for a in rig4.api.service.candidate(target['root_candidate_id'])['attempts']]
    assert not [s for s in seeds if s.startswith(('subset:probe', 'subset:diag', 'subset:rest'))], seeds
    # Round 1 ran on the imported root: its analyst was created and drafted lcd-v2 cards there.
    state = json.loads((target_dir / 'state.json').read_text())
    assert state['rounds'] and state['rounds'][0]['cards'], state.get('failure_detail')
    analysts = rig4.intents('analyst')   # rig4.task_id is the target task now
    assert analysts and analysts[0]['candidate_id'] == target['root_candidate_id']
    drafted = [h for h in rig4.api.hypotheses.list(target['task_id']) if h.get('parent_candidate_id') == target['root_candidate_id']]
    assert drafted and all('slice_predicate' in (h['card'].get('contract') or {}) for h in drafted)
    # The source iteration and its run directory are only read.
    assert rig4.api.service.candidate(source['root_candidate_id'])['attempts'] == old_attempts
    assert _hashes(source_dir) == old_files


def test_import_refuses_identity_mismatches_and_writes_nothing(rig4, env, calibrated, capsys):
    source, _ = calibrated
    other_mode = iteration(env, request_key='other-mode', case_ids=rig4.case_ids, split_mode='deferred',
                           gate_rule='lcd-v2', safeguards=SAFEGUARDS, k_base=2, mode='reasoning')
    other_seed = iteration(env, request_key='other-seed', case_ids=rig4.case_ids, split_mode='deferred',
                           gate_rule='lcd-v2', safeguards=SAFEGUARDS, k_base=2, split_seed=7)
    for tree, check in ((other_mode, 'protocol mode'), (other_seed, 'split seed')):
        assert _cli(rig4, 'import-calibration', tree['id'], '--from-iteration', source['id']) == v4cli.EXIT_USAGE
        failed = [c['check'] for c in json.loads(capsys.readouterr().out)['checks'] if not c['ok']]
        assert check in failed
        assert not imp.default_dir(rig4.tmp / 'state', tree['id']).exists()
        assert rig4.api.service.candidate(tree['root_candidate_id'])['links'] == []
    target = _deferred(rig4, env, 'other-actor')
    assert v4cli.main(['import-calibration', '--config', 'unused.json', '--state', str(rig4.tmp / 'state'),
                       '--iteration', target['id'], '--actor', 'someone-else',
                       '--from-iteration', source['id']]) == v4cli.EXIT_USAGE
    assert 'actor is the calibration actor' in [c['check'] for c in json.loads(capsys.readouterr().out)['checks']
                                                if not c['ok']]


def test_an_incomplete_import_blocks_calibration_and_run(rig4, env, calibrated, monkeypatch, capsys):
    source, _ = calibrated
    target = _deferred(rig4, env, 'crash')
    target_dir = imp.default_dir(rig4.tmp / 'state', target['id'])
    assert _cli(rig4, 'import-calibration', target['id'], '--from-iteration', source['id']) == 0
    digest = json.loads(capsys.readouterr().out)['plan_sha256']
    service = rig4.api.service

    def crash(*args, **kwargs):
        raise RuntimeError('killed after linking')
    from rag_eval.workbench.iterations import IterationService
    with monkeypatch.context() as patched:
        patched.setattr(IterationService, 'link_calibration_import', crash)
        assert _cli(rig4, 'import-calibration', target['id'], '--from-iteration', source['id'],
                    '--confirm', digest) == v4cli.EXIT_ERROR
    assert json.loads((target_dir / imp.IMPORT_FILE).read_text())['status'] == 'intent'
    _use(rig4, service.iteration(target['id']), target_dir)
    with rig4:
        assert _cli(rig4, 'calibrate', target['id'], '--v4-config', str(rig4.v4_config), '--no-advisor',
                    run_id='v41-' + target['id'], state_dir=target_dir) == v4cli.EXIT_USAGE
        assert _cli(rig4, 'run', target['id'], '--from-proposal', str(target_dir / 'proposal.json'),
                    run_id='v41-' + target['id'], state_dir=target_dir) == v4cli.EXIT_USAGE
    assert _cli(rig4, 'import-calibration', target['id'], '--from-iteration', source['id'], '--confirm', digest) == 0
    assert json.loads((target_dir / imp.IMPORT_FILE).read_text())['status'] == 'complete'
    assert service.candidate(target['root_candidate_id'])['state'] == 'evaluated'


# -- validation-remainder baseline: reused only for the very same tasks -----------

class _Remainder:
    def __init__(self):
        self.measured = []

    def evaluate_subset(self, seed, reference, subset, tasks, k):
        from rag_eval.optimizer.adapter import EvalResult
        self.measured.append(list(tasks))
        return EvalResult({t: .25 for t in tasks}, {t: [] for t in tasks}, {}, {t: None for t in tasks}), None


def _imported_bundle(tmp_path, remainder_tasks):
    from rag_eval.optimizer.adapter import EvalResult, Version
    import dataclasses
    probe = EvalResult({'p1': .5, 'p2': .5}, {'p1': [], 'p2': []}, {}, {'p1': None, 'p2': None})
    diag = EvalResult({'d1': 0.}, {'d1': []}, {}, {'d1': None})
    bundle = dict(identity=dict(seed='S', calibration_k=2), probe=['p1', 'p2'], diagnosis=['d1'],
                  results=dict(probe=dataclasses.asdict(probe), diagnosis=dataclasses.asdict(diag)),
                  payload_sha256='NEW', imported_from=dict(calibration_sha256='OLD', iteration_id='I-old'))
    session._save(tmp_path / imp.REMAINDER_IMPORT_FILE, dict(
        source=dict(iteration_id='I-old'), identity=dict(seed='S', tasks=remainder_tasks, calibration='OLD'),
        result=dataclasses.asdict(EvalResult({t: 1. for t in remainder_tasks}, {t: [] for t in remainder_tasks}, {},
                                             {t: None for t in remainder_tasks}))))
    cfg = type('Cfg', (), dict(calibration_k=2))()
    partitions = dict(validation=['p1', 'p2', 'r1', 'r2'], search=['d1'], seed_minibatch=['p1'],
                      seed_rest=['p2', 'r1', 'r2'])
    return Version('S'), cfg, partitions, bundle


def test_imported_remainder_is_adopted_for_the_same_tasks(tmp_path):
    seed, cfg, partitions, bundle = _imported_bundle(tmp_path, ['r1', 'r2'])
    adapter = _Remainder()
    out = session.import_baseline(adapter, seed, cfg, partitions, bundle, tmp_path)
    assert adapter.measured == [] and out['rest'].per_task_scores['r1'] == 1.
    saved = session._load(tmp_path / imp.REMAINDER_FILE)
    assert saved['identity'] == dict(seed='S', tasks=['r1', 'r2'], calibration='NEW')
    assert saved['provenance'] == dict(iteration_id='I-old')


def test_imported_remainder_for_other_tasks_is_measured_again(tmp_path):
    seed, cfg, partitions, bundle = _imported_bundle(tmp_path, ['r1'])
    adapter = _Remainder()
    out = session.import_baseline(adapter, seed, cfg, partitions, bundle, tmp_path)
    assert adapter.measured == [['r1', 'r2']] and out['rest'].per_task_scores['r1'] == .25
    assert 'provenance' not in session._load(tmp_path / imp.REMAINDER_FILE)


def test_workbench_import_lists_sources_and_needs_a_fresh_confirmation(rig4, env, calibrated, tmp_path):
    from types import SimpleNamespace
    from rag_eval.workbench.auto_run_control import AutoRunControl, ImportAction
    from rag_eval.workbench.repository import Conflict
    source, _ = calibrated
    target = _deferred(rig4, env, 'web-target')
    service = rig4.api.service
    api = SimpleNamespace(repository=service.repository, service=service, actor='operator',
                          _scope=lambda **kw: None, _write=lambda: None)
    ctl = AutoRunControl(api, dict(_config_path=str(tmp_path / 'unused.json')))
    listed = ctl.calibration_sources(target['id'])['sources']
    assert [(s['iteration_id'], s['ok']) for s in listed] == [(source['id'], True)]
    preview = ctl.import_calibration(target['id'], ImportAction(source_iteration_id=source['id']))
    assert preview['ok'] and preview['incurs_cost'] is False and 'qasper:' not in json.dumps(preview)
    assert service.candidate(target['root_candidate_id'])['links'] == []
    with pytest.raises(Conflict):
        ctl.import_calibration(target['id'], ImportAction(source_iteration_id=source['id'], confirmation_token='forged'))
    token = preview['confirmation_token']
    done = ctl.import_calibration(target['id'], ImportAction(source_iteration_id=source['id'], confirmation_token=token))
    assert done['accepted'] and done['status'] == 'complete'
    with pytest.raises(Conflict):   # a token is used once
        ctl.import_calibration(target['id'], ImportAction(source_iteration_id=source['id'], confirmation_token=token))
    assert service.candidate(target['root_candidate_id'])['state'] == 'evaluated'
    assert ctl.calibration(target['id'])['complete'] is True
    assert ctl.read_proposal(target['id'])['proposal']['notes']['imported_from']['iteration_id'] == source['id']


# -- validation extension and the holdout curve ---------------------------------

def test_import_extends_validation_then_holdout_measures_the_lineage(rig4, env, calibrated, capsys, monkeypatch):
    source, source_dir = calibrated
    source_validation = rig4.api.service.iteration(source['id'])['protocol']['splits']['validation']
    target = _deferred(rig4, env, 'extended')
    target_dir = imp.default_dir(rig4.tmp / 'state', target['id'])

    assert _cli(rig4, 'import-calibration', target['id'], '--from-iteration', source['id'],
                '--extend-validation', '3') == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview['ok'] and preview['extension']['added'] == 3 and 'extension_cases' not in preview
    assert rig4.api.service.iteration(target['id'])['protocol']['splits']['pool'] == sorted(rig4.case_ids)
    assert _cli(rig4, 'import-calibration', target['id'], '--from-iteration', source['id'],
                '--extend-validation', '3', '--confirm', preview['plan_sha256']) == 0
    capsys.readouterr()
    # Replaying the confirmed extension converges on the same receipt.
    assert _cli(rig4, 'import-calibration', target['id'], '--from-iteration', source['id'],
                '--extend-validation', '3', '--confirm', preview['plan_sha256']) == 0
    capsys.readouterr()
    split = rig4.api.service.iteration(target['id'])['protocol']['splits']
    added = sorted(set(split['pool']) - set(rig4.case_ids))
    assert split['status'] == 'pending' and len(added) == 3 and len({c.rsplit('q', 1)[0] for c in added}) == 1
    bundle = session.load_bundle(target_dir / session.CALIBRATION_FILE)
    assert bundle['extension']['cases'] == added and bundle['summary']['pool_size'] == len(split['pool'])
    proposed = json.loads((target_dir / session.PROPOSAL_FILE).read_text())
    assert proposed['items']['validation_size']['value'] == len(source_validation) + 3

    _use(rig4, rig4.api.service.iteration(target['id']), target_dir)
    rig4.role.update({c: 'validation' for c in added})
    monkeypatch.setattr(V4Run, '_stop_reason', rig4.one_round)
    from .test_sn_rehearsal import GOOD, PAPERS, QUESTIONS, card
    from .test_v4_rehearsal import first_round_only
    rig4.effects = {'Fix empty retrievals': GOOD}
    rig4.analyst_cards = first_round_only([card('Fix empty retrievals')])
    with rig4:
        assert _cli(rig4, 'run', target['id'], '--from-proposal', str(target_dir / 'proposal.json'),
                    run_id='v41-' + target['id'], state_dir=target_dir) == 0
    frozen = rig4.api.service.iteration(target['id'])['protocol']['splits']
    assert frozen['validation'] == sorted(set(source_validation) | set(added))
    assert frozen['search'] == rig4.api.service.iteration(source['id'])['protocol']['splits']['search']
    root = rig4.api.service.candidate(target['root_candidate_id'])
    seed_scopes = [a['batch_id'] for a in root['attempts']]
    assert not [s for s in seed_scopes if s.startswith(('subset:probe', 'subset:diag'))], seed_scopes
    assert (target_dir / session.EXTENSION_FILE).exists()
    state = json.loads((target_dir / 'state.json').read_text())
    assert len(state['lineage']) == 2, state.get('failure_detail')

    # Holdout: questions outside the extended pool and the sealed paper, seed and adopted node.
    capsys.readouterr()
    assert _cli(rig4, 'holdout', target['id'], run_id='v41-' + target['id'], state_dir=target_dir) == 0
    out = capsys.readouterr().out
    assert 'holdout: 3 questions outside the pool; 2 node(s)' in out
    assert not (target_dir / 'holdout').exists()
    holdout_ids = sorted(c for c in [f'qasper:p{p}q{q}' for p in range(PAPERS) for q in range(QUESTIONS)] if c not in frozen['search'] + frozen['validation']
                         and c not in frozen['sealed'])
    rig4.role.update({c: 'fresh' for c in holdout_ids})
    with rig4:
        assert _cli(rig4, 'holdout', target['id'], '--confirm', run_id='v41-' + target['id'],
                    state_dir=target_dir) == 0
    report = json.loads((target_dir / 'holdout' / 'holdout.json').read_text())
    seed_point, node = report['points']
    assert report['n_cases'] == 3 and seed_point['delta'] is None
    assert node['delta'] == pytest.approx(GOOD['fresh'] * (1 + .1 * 1), abs=.05) and node['n'] == 3
    assert set(report['per_case'][node['candidate_id']]) == set(holdout_ids)
    assert (target_dir / 'holdout' / 'holdout.md').exists()
    # Replaying the holdout reuses the finished measurements.
    executions = len(rig4.api.service.candidate(target['root_candidate_id'])['attempts'])
    with rig4:
        assert _cli(rig4, 'holdout', target['id'], '--confirm', run_id='v41-' + target['id'],
                    state_dir=target_dir) == 0
    assert len(rig4.api.service.candidate(target['root_candidate_id'])['attempts']) == executions


def test_holdout_scope_refuses_pool_and_sealed_questions(rig4, calibrated):
    from rag_eval.workbench.execution_scopes import subset_scope_id
    protocol = rig4.api.service.iteration(calibrated[0]['id'])['protocol']
    with pytest.raises(ValueError):
        subset_scope_id(protocol, 'holdout', [protocol['splits']['validation'][0]])
    with pytest.raises(ValueError):
        subset_scope_id(protocol, 'holdout', [protocol['splits']['sealed'][0]])
