"""Workbench server with automatic runs in every state, for tests/auto-run-browser.mjs.

Every run is a real ``run_optimization`` over MockAdapter; only the "running" and
"interrupted" variants edit state.json afterwards, to show a round still in flight.

    PYTHONPATH=src:. python apps/sn-workbench/tests/auto_run_fixture.py --port 8097 --root /tmp/auto-fixture
"""
import argparse
import dataclasses
import json
import math
import shutil
from pathlib import Path

import uvicorn

from rag_eval.optimizer import ProtocolConfig, run_optimization, run_view
from rag_eval.optimizer.adapter import BudgetExhausted, Predicate
from rag_eval.optimizer.ledger_io import atomic_write_json
from rag_eval.workbench import auto_runs
from rag_eval.workbench.api import create_app
from rag_eval.workbench.repository import WorkbenchRepository
from tests.optimizer.mock_adapter import MockAdapter

NOW = '2026-11-02T00:00:00+00:00'
POOL = [f't{i}' for i in range(12)]
BATCHES = [[f'{name}{i}' for i in range(12)] for name in ('f', 'g', 'h')]
ALL = POOL + [t for b in BATCHES for t in b]


def wave(level, spread=0.04):
    return lambda i: level + spread * math.sin(1.7 * i)


def deltas(pool, f=None, g=None, h=None):
    """Per-task change: *pool* on the validation pool, the rest on each fresh batch."""
    out = {t: wave(pool)(i) for i, t in enumerate(POOL)}
    for level, batch in zip((f, g, h), BATCHES):
        out.update({t: wave(pool if level is None else level)(i) for i, t in enumerate(batch)})
    return out


class Scripted(MockAdapter):
    """Predicates read a per-version flag; optionally runs out of budget from a given round."""

    def __init__(self, script, budget_from=None):
        super().__init__(script=script, n_tasks=12, base_scores={t: 0.34 + 0.05 * math.sin(i) for i, t in enumerate(POOL)})
        self._scores[self._seed_hash].update({t: 0.34 + 0.05 * math.sin(i) for i, t in enumerate(ALL)})
        self.budget_from = budget_from

    def analyse(self, trajectories, n_cards, *, round_id=None, **kw):
        if self.budget_from is not None and round_id is not None and round_id >= self.budget_from:
            self.fail_on = {'screen': BudgetExhausted('case-run budget exhausted')}
        cards = super().analyse(trajectories, n_cards, round_id=round_id, **kw)
        return [dataclasses.replace(c, predicate=self._card_specs[c.ref].get('predicate')) for c in cards]

    def evaluate(self, version, task_ids, k, *, purpose='screen'):
        result = super().evaluate(version, task_ids, k, purpose=purpose)
        for runs in result.trajectories.values():
            for run in runs:
                run['v'] = version.content_hash
        return result

    def features(self, trajectories, predicate):
        """The predicted behaviour shows on every other seed run and never after a change."""
        if predicate is None:
            return {}
        return {t: traj[0]['v'] == self._seed_hash and sum(map(ord, t)) % 2 == 0 for t, traj in trajectories.items() if traj}


PREDICATE = Predicate({'field': 'trace.n_empty_retrievals', 'kind': 'range', 'min': 1}, 'decrease')
ROUNDS = [
    dict(cards=[dict(name='复述问题时丢掉了限定词', deltas=deltas(-0.15)),
                dict(name='反思步骤提前结束检索', deltas=deltas(0.1), smoke_pass=False),
                dict(name='首次检索为空时没有回退查询', deltas=deltas(0.13, f=0.09), predicate=PREDICATE)]),
    dict(cards=[dict(name='引用块缺少所属文档标题', deltas=deltas(0.07, f=0.07, g=0.02))]),
    dict(cards=[dict(name='表格内容被切块打散', deltas=deltas(0.07, f=0.07, g=-0.03))]),
    dict(cards=[dict(name='数字比较题没有走计算路径', deltas=deltas(0.06, f=0.06, g=0.06))]),
]
LAST_ROUND = dict(cards=[dict(name='缩写没有展开就进入检索', deltas=deltas(-0.2), smoke_pass=False),
                         dict(name='多跳问题只检索第一跳', deltas=deltas(0.05)),
                         dict(name='澄清判断过于保守', deltas=deltas(0.05))])
CONFIG = dict(mde=0.03, sigma_d=0.05, ladder_c=1.0, K_max=40, T=0.1, n_fresh=12, b_cards=1, analyst_cards=3, k_screen=2,
              n_smoke_sample=3, predicate_coverage_floor=0.6, attribution_n_min=3)


def real_run(run_dir, script, *, config=None, budget_from=None, max_rounds=4):
    run_dir.mkdir(parents=True)
    atomic_write_json(run_dir / 'cli-run.json', dict(run_id=run_dir.name, iteration_id=run_dir.name, actor='operator',
                                                     protocol_sha256='a1b2c3d4e5f60718293a4b5c6d7e8f90', created_at=NOW))
    adapter = Scripted(script, budget_from=budget_from)
    run_optimization(adapter, adapter.seed, ProtocolConfig(**dict(CONFIG, **(config or {}))), search_set_ids=POOL, val_pool_ids=POOL,
                     fresh_batch_ids=BATCHES, smoke_sample_ids=POOL[:3], max_rounds=max_rounds, state_dir=run_dir)
    return json.loads((run_dir / 'state.json').read_text())


def still_running(state):
    """A round in flight, as state.json holds it while workers run: no halt, nothing recorded for them."""
    state['halt_reason'] = None
    current = state['current_round']
    current['round']['halt_reason'] = None
    current['round']['candidates'] = [c for c in current['round']['candidates'] if not c.get('halted')]
    return state


# The sealed run: round 5 adopts two cards (the second rebased onto the first), so the curve has
# two points in one round, a rolled-back branch off round 1 and a final pointer in round 5.
SEALED_ROUNDS = ROUNDS + [dict(cards=[dict(name='多跳问题只检索第一跳', deltas=deltas(0.05)),
                                      dict(name='澄清判断过于保守', deltas=deltas(0.05), rebase=True),
                                      dict(name='缩写没有展开就进入检索', deltas=deltas(-0.2), smoke_pass=False)])]
SEALED_DELTAS = [0.0, 0.094, 0.021, 0.018, 0.071, 0.083, 0.088]


def sealed_curve(state):
    """What cli sealed --confirm writes (sn_sealed._curve over v2 lineage_curve), with scripted numbers."""
    points, m, half = [], len(state['lineage']) - 1, 0.041
    for i, node in enumerate(state['lineage']):
        delta = SEALED_DELTAS[min(i, len(SEALED_DELTAS) - 1)]
        points.append(dict(index=i, version_hash=node['version_hash'], candidate_id=state['versions'][node['version_hash']].get('candidate_id'),
                           state=node['state'], style={'certified': 'solid', 'provisional': 'hollow', 'rolled_back': 'dashed'}[node['state']],
                           k=2, status='measured', score=0.34 + delta, se=0.012, n=40, delta_vs_seed=delta, se_delta=0.0 if i == 0 else 0.016,
                           lower=delta - (0 if i == 0 else 0.026), upper=delta + (0 if i == 0 else 0.026),
                           lower_adj=delta - (0 if i == 0 else half), upper_adj=delta + (0 if i == 0 else half),
                           p=None if i == 0 else 0.001 if delta > 0.05 else 0.1,
                           p_holm=None if i == 0 else 0.004 if delta > 0.05 else 0.4, holds_holm=None if i == 0 else delta > 0.05))
    return dict(kind='v3-sealed-lineage', status='finished', metric='answer_f1', n=40, k=2, alpha=0.05, z=1.645, z_adj=2.64, m=m,
                correction='holm-p+bonferroni-interval', points=points)


def sealed_run(run_dir):
    """A halted run with its sealed curve written, as after cli sealed --confirm (also the sample for cli curve)."""
    state = real_run(Path(run_dir), SEALED_ROUNDS, config=dict(b_cards=2), max_rounds=9)
    atomic_write_json(Path(run_dir) / 'sealed-curve.json', sealed_curve(state))


def seed_iteration(repository, workspace_id, iteration_id, title):
    task_id = 'task-' + iteration_id
    root = 'root-' + iteration_id
    with repository.connect(write=True) as conn:
        conn.execute('INSERT INTO tasks(id,workspace_id,title,goal,created_at) VALUES(?,?,?,?,?)', (task_id, workspace_id, title, 'g', NOW))
        conn.execute('INSERT INTO iterations(id,task_id,origin,title,root_candidate_id,unknown_json,created_by,created_at) '
                     'VALUES(?,?,?,?,?,?,?,?)', (iteration_id, task_id, 'native', title, root, '{}', 'fixture', NOW))
        conn.execute('INSERT INTO candidates(id,iteration_id,origin,status,integrated_from_json,inspired_by_json,unknown_json,'
                     'created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                     (root, iteration_id, 'root', 'frozen', '[]', '[]', '{}', 'fixture', NOW, NOW))


def v4_run(run_dir, script, *, endpoint=False, interrupt=False, **config):
    """A real method-v4 run over the v4 mock adapter, its cli-run.json, and optionally a test-set endpoint."""
    from rag_eval.optimizer.v4 import endpoint as v4_endpoint
    from rag_eval.optimizer.v4.config import V4Config
    from rag_eval.optimizer.v4.loop import V4Run
    from tests.optimizer.v4_mock import V4Mock
    mock = V4Mock(script)
    cfg = V4Config(**dict(dict(time_budget_hours=12.0, b=4, minibatch=True, smoke=True, merge=True, ablation=True, max_consecutive_run_failures=1), **config))
    run = V4Run(mock, mock.seed, cfg, search=mock.search, validation=mock.validation, test_size=60,
                families={t: f'paper{i // 4}' for i, t in enumerate(mock.validation)}, state_dir=run_dir)
    # Browser fixture stops after its finite script; production uses the budget.
    run._stop_reason = lambda: 'time_budget' if len(run.rounds) >= len([r for r in script if r]) else None
    run.run()
    atomic_write_json(run_dir / 'cli-run.json', dict(method='v4', run_id=run_dir.name, iteration_id=run_dir.name,
        actor='operator', created_at=NOW, config_sha256='0' * 64, config_resolved=dataclasses.asdict(cfg), test_set=True))
    if endpoint:
        nodes = v4_endpoint.node_set(run.lineage, run.versions, cfg.endpoint_nodes, final=run.final_version())
        curve = [dict(candidate_id=n['candidate_id'], score=.42 + .03 * i, delta_vs_seed=.03 * i, se_delta=.012 if i else 0.,
                      boot_lower=(.03 * i - .022) if i else None, boot_upper=(.03 * i + .024) if i else None,
                      boot_units=15 if i else None, boot_unit='family' if i else None) for i, n in enumerate(nodes)]
        rep_ = v4_endpoint.report(dict(status='finished', metric='str_em', n=60, k=1, curve=curve), nodes, run.lineage,
                                  k_val=1, ablation=run.ablation)
        atomic_write_json(run_dir / 'endpoint.json', rep_)
    if interrupt:
        import json as _json
        state = _json.loads((run_dir / 'state.json').read_text())
        last = state['rounds'].pop()
        last.pop('finished_at', None)
        last['decision'] = None
        for r in last['records'].values():
            if r['stage'] == 'measured':
                r.update(stage='minibatch_pass', mechanism=None, qualification=None, label=None)
        state['lineage'] = [n for n in state['lineage'] if n.get('round') != last['round']]
        state.update(current_round=last, halt_reason=None, stop_reason=None, ablation=None)
        atomic_write_json(run_dir / 'state.json', state)


V4_SCRIPT = [[dict(name='检索为空时重试一次扩展查询', delta=.10, effect=True), dict(name='答案引用对齐段落', delta=.06),
              dict(name='缩短综合提示', delta=-.2), dict(name='拒答阈值调整', implement_error=True)],
             [dict(name='澄清歧义问题', delta=.003, effect=True), dict(name='合并重复检索', delta=-.01)], []]


def add_v41_samples(app, runs):
    from fastapi.responses import JSONResponse
    from rag_eval.optimizer.v4 import proposal, calibrate
    from rag_eval.optimizer.v4.simulate import CalibrationSummary, simulate
    from tests.optimizer.v4_mock import V4Mock
    mock = V4Mock([])
    measured = mock._result(mock.seed, mock.validation, 3)
    aa = calibrate._aa(measured)
    summary = CalibrationSummary(rerun_var=aa['rerun_var'], n_probe=40, k=3, seed_fail_rate=.4,
        constant_fraction=0, exec_overhead_s=10, per_task_s=2, p95_exec_s=120, infra_fail_rate=.01,
        tokens_per_task=None, safeguard_var=dict(tokens_per_task_ratio=.01, answer_produced_rate=.002),
        cold_wall_s=100, warm_wall_s=80, pool_size=160, n_diagnosis=20, remaining_paper_sizes=(10,) * 10)
    proposed = proposal.rule_defaults(summary, dict(time_budget_hours=8, calibration_k=3))
    proposed.notes['advisor'] = dict(status='fallback', message='校准顾问未给出建议：离线浏览器夹具')
    rows = sorted(([a[t] for a in measured.attempt_scores] for t in mock.validation), key=lambda v: sum(v) / len(v))
    detail = dict(complete=True, alive=False, inputs=proposed.inputs, summary=summary.canonical(),
        heatmap=[dict(row=i+1, scores=v, mean=sum(v)/len(v)) for i, v in enumerate(rows)],
        attempt_means=[sum(a.values()) / len(a) for a in measured.attempt_scores],
        aa_differences=[v[0] - sum(v[1:])/2 for v in rows], aa_se=aa['aa_se'], assumptions=['synthetic browser fixture'])
    sample = dict(calibration=detail, proposal=dict(proposal=proposed.to_dict(), forecast=simulate(proposed.params(), summary), frozen=False))
    async def samples():
        return JSONResponse(sample)
    app.add_api_route('/api/test-v41', samples)
    # create_app mounts the SPA last; keep this fixture-only endpoint ahead of it.
    app.router.routes.insert(0, app.router.routes.pop())
    # Feed the same anonymous calibration to the ordinary completed run projection.
    from rag_eval.optimizer.v4 import calibration_session as session
    bundle = dict(kind='method-v4.1-calibration', complete=True, identity=dict(user_inputs=proposed.inputs),
        results=dict(probe=dataclasses.asdict(measured)), summary=summary.canonical(), calibration=dict(validation=aa), assumptions=detail['assumptions'])
    session._save(runs / 'v4-done' / 'calibration.json', bundle)
    state = json.loads((runs / 'v4-done' / 'state.json').read_text())
    state['forecast'] = sample['proposal']['forecast']
    state['rounds'][0]['records']['0']['flags'] = ['needs_human_review']
    atomic_write_json(runs / 'v4-done' / 'state.json', state)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8097)
    parser.add_argument('--root', required=True)
    parser.add_argument('--static', default='apps/sn-workbench/dist')
    args = parser.parse_args()
    root = Path(args.root)
    shutil.rmtree(root, ignore_errors=True)
    runs = root / 'runs'

    real_run(runs / 'auto-done', ROUNDS)
    sealed_run(runs / 'auto-sealed')
    in_flight = dict(config=dict(b_cards=2), budget_from=5, max_rounds=9)
    real_run(runs / 'auto-budget', ROUNDS + [LAST_ROUND], **in_flight)
    for name in ('auto-running', 'auto-interrupted'):
        state = real_run(runs / name, ROUNDS + [LAST_ROUND], **in_flight)
        atomic_write_json(runs / name / 'state.json', still_running(state))
    v4_run(runs / 'v4-done', V4_SCRIPT, endpoint=True)
    v4_run(runs / 'v4-interrupted', V4_SCRIPT[:1] + [[dict(name='澄清歧义问题', delta=.05)]], interrupt=True,
           max_consecutive_run_failures=1)
    (runs / 'auto-new').mkdir()
    atomic_write_json(runs / 'auto-new' / 'cli-run.json', dict(run_id='auto-new', iteration_id='auto-new', actor='operator', created_at=NOW))

    config = dict(workspace=dict(title='SN', location=str(root / 'sn')))
    app = create_app(root / 'state', config, actor='fixture', access_token='test-access', static_directory=args.static)
    repository = WorkbenchRepository(root / 'state')
    with repository.connect() as conn:
        workspace_id = conn.execute('SELECT id FROM workspaces').fetchone()['id']
    seed_iteration(repository, workspace_id, 'manual', '手动迭代')
    titles = dict(zip(('auto-new', 'auto-budget', 'auto-interrupted', 'auto-sealed', 'auto-done', 'auto-running'),
                      ('自动 · 未开始', '自动 · 预算用尽', '自动 · 已中断', '自动 · 封存集已评', '自动 · 已结束', '自动 · 运行中')))
    titles.update({'v41-wizard': 'v4.1 · 待校准', 'v4-done': 'v4 · 已结束', 'v4-interrupted': 'v4 · 已中断'})
    for name, title in titles.items():
        seed_iteration(repository, workspace_id, name, title)
        auto_runs.register(repository, name, run_id=name, state_dir=str(runs / name), actor='operator', method='method-v4' if name.startswith(('v4-', 'v41-')) else 'method-v3')
    add_v41_samples(app, runs)
    with run_view.hold_run_lock(runs / 'auto-running'):
        uvicorn.run(app, host='127.0.0.1', port=args.port, log_level='warning')


if __name__ == '__main__':
    main()
