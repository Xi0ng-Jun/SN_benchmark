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


def sealed_curve(state):
    points = []
    for i, node in enumerate(state['lineage']):
        delta = None if i == 0 else [None, 0.094, 0.021, 0.018, 0.071][min(i, 4)]
        points.append(dict(index=i, version_hash=node['version_hash'], candidate_id=state['versions'][node['version_hash']].get('candidate_id'),
                           state=node['state'], style={'certified': 'solid', 'provisional': 'hollow', 'rolled_back': 'dashed'}[node['state']],
                           status='finished', score=0.34 + (delta or 0), se=0.012, delta_vs_seed=delta,
                           lower_adj=None if i == 0 else delta - 0.041, upper_adj=None if i == 0 else delta + 0.041,
                           p_holm=None if i == 0 else 0.004 if delta > 0.05 else 0.4, holds_holm=None if i == 0 else delta > 0.05))
    return dict(kind='v3-sealed-lineage', status='finished', metric='answer_f1', n=40, k=2, correction='holm-p+bonferroni-interval', points=points)


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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8097)
    parser.add_argument('--root', required=True)
    parser.add_argument('--static', default='apps/sn-workbench/dist')
    args = parser.parse_args()
    root = Path(args.root)
    shutil.rmtree(root, ignore_errors=True)
    runs = root / 'runs'

    finished = real_run(runs / 'auto-done', ROUNDS)
    real_run(runs / 'auto-sealed', ROUNDS)
    atomic_write_json(runs / 'auto-sealed' / 'sealed-curve.json', sealed_curve(finished))
    in_flight = dict(config=dict(b_cards=2), budget_from=5, max_rounds=9)
    real_run(runs / 'auto-budget', ROUNDS + [LAST_ROUND], **in_flight)
    for name in ('auto-running', 'auto-interrupted'):
        state = real_run(runs / name, ROUNDS + [LAST_ROUND], **in_flight)
        atomic_write_json(runs / name / 'state.json', still_running(state))
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
    for name, title in titles.items():
        seed_iteration(repository, workspace_id, name, title)
        auto_runs.register(repository, name, run_id=name, state_dir=str(runs / name), actor='operator')
    with run_view.hold_run_lock(runs / 'auto-running'):
        uvicorn.run(app, host='127.0.0.1', port=args.port, log_level='warning')


if __name__ == '__main__':
    main()
