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


# ---------------------------------------------------------------------------
# Mini experiment: 20-case ALCE-ASQA exploration run (extended demo).
# Pool = 12 search + 8 val (20); 12 fresh batches × 10 cases (120).  Config
# mirrors an aggressive exploration protocol (ladder_c=0.3, z_screen=0.0,
# z_confirm=1.28, T=0.001, K_max=40, b_cards=1).  Every round's single card
# clears screening (pool delta ≈ 0.05 > m=0.02); T is tiny so each adoption
# triggers a milestone confirmation immediately.  10 batches confirm, 2
# reject → lineage has 13 nodes (seed + 10 certified + 2 rolled_back); the
# sealed curve carries 13 points.
# ---------------------------------------------------------------------------
MINI_POOL = [f'm{i}' for i in range(12)]   # search set (12)
MINI_VAL = [f'v{i}' for i in range(8)]      # initial val pool (8); total = 20
MINI_BATCHES = [[f'b{i}_{j}' for j in range(10)] for i in range(12)]
MINI_ALL = MINI_POOL + MINI_VAL + [t for b in MINI_BATCHES for t in b]
MINI_PREDICATE = Predicate({'field': 'trace.answer_language', 'kind': 'equals', 'value': 'zh'}, 'decrease')

# Per-batch fresh delta: positive → confirmed, negative → rejected (rollback).
MINI_BATCH_LEVELS = [0.04, 0.04, 0.04, -0.03, 0.04, 0.04, 0.04, 0.04,
                     0.04, -0.03, 0.04, 0.04]


def mini_deltas(pool_level=0.05, batch_levels=None):
    """Per-task delta for the mini pool (12+8) and 12 fresh batches of 10."""
    out = {t: wave(pool_level)(i) for i, t in enumerate(MINI_POOL + MINI_VAL)}
    levels = batch_levels if batch_levels is not None else MINI_BATCH_LEVELS
    for level, batch in zip(levels, MINI_BATCHES):
        out.update({t: wave(level)(i) for i, t in enumerate(batch)})
    return out


_MINI_NAMES = [
    '答案语言不随问题语言切换',
    '引用块跨段截断导致内容丢失',
    '多跳推理中间步骤未写入上下文',
    '检索结果排序未考虑问题类型',
    '否定问题答案极性反转',
    '数量范围题超出检索范围时未声明',
    '日期比较未统一时区基准',
    '列表题遗漏末尾限定词',
    '缩写展开后与上下文冲突',
    '表格数值单位不统一',
    '因果题混淆充分与必要条件',
    '开放题答案缺少来源支撑',
]

MINI_ROUNDS = [
    dict(cards=[dict(name=_MINI_NAMES[i], deltas=mini_deltas(), predicate=MINI_PREDICATE)])
    for i in range(12)
]

MINI_CONFIG = dict(
    mde=0.058, sigma_d=0.28, ladder_c=0.3, K_max=40, T=0.001, n_fresh=10,
    z_screen=0.0, z_confirm=1.28, b_cards=1, analyst_cards=2, k_screen=2,
    n_smoke_sample=3, predicate_coverage_floor=0.6, attribution_n_min=3,
)

MINI_SEALED_DELTAS = [0.0, 0.031, 0.052, 0.078, 0.065, 0.098, 0.124, 0.151,
                     0.178, 0.205, 0.192, 0.228, 0.255]


def mini_sealed_curve(state):
    """Scripted sealed curve for the mini experiment (13 lineage nodes)."""
    points, m, half = [], len(state['lineage']) - 1, 0.042
    for i, node in enumerate(state['lineage']):
        delta = MINI_SEALED_DELTAS[min(i, len(MINI_SEALED_DELTAS) - 1)]
        seed_score = 0.43
        holds = delta > 0.05
        points.append(dict(
            index=i, version_hash=node['version_hash'],
            candidate_id=state['versions'][node['version_hash']].get('candidate_id'),
            state=node['state'],
            style={'certified': 'solid', 'provisional': 'hollow', 'rolled_back': 'dashed'}[node['state']],
            k=2, status='measured', score=seed_score + delta, se=0.014, n=5,
            delta_vs_seed=delta, se_delta=0.0 if i == 0 else 0.018,
            lower=delta - (0 if i == 0 else 0.030), upper=delta + (0 if i == 0 else 0.030),
            lower_adj=delta - (0 if i == 0 else half), upper_adj=delta + (0 if i == 0 else half),
            p=None if i == 0 else 0.001 if holds else 0.04 if delta > 0 else 0.12,
            p_holm=None if i == 0 else 0.012 if holds else 0.48,
            holds_holm=None if i == 0 else holds,
        ))
    return dict(kind='v3-sealed-lineage', status='finished', metric='answer_f1', n=5, k=2,
                alpha=0.05, z=1.645, z_adj=2.64, m=m,
                correction='holm-p+bonferroni-interval', points=points)


class MiniScripted(MockAdapter):
    """Mini-experiment adapter: 20-task pool (12 search + 8 val) + 120 fresh (12×10)."""

    def __init__(self, script):
        pool_scores = {t: 0.43 + 0.05 * math.sin(i) for i, t in enumerate(MINI_POOL + MINI_VAL)}
        super().__init__(script=script, n_tasks=len(MINI_ALL),
                         base_scores={t: pool_scores.get(t, 0.43) for t in MINI_ALL})
        self._task_ids = list(MINI_ALL)
        self._scores[self._seed_hash] = {t: pool_scores.get(t, 0.43) for t in MINI_ALL}

    def analyse(self, trajectories, n_cards, *, round_id=None, **kw):
        cards = super().analyse(trajectories, n_cards, round_id=round_id, **kw)
        return [dataclasses.replace(c, predicate=self._card_specs[c.ref].get('predicate')) for c in cards]


def mini_run(run_dir):
    """A fresh-exhausted mini-experiment run with sealed curve attached."""
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True)
    atomic_write_json(run_dir / 'cli-run.json', dict(
        run_id=run_dir.name, iteration_id=run_dir.name, actor='v3-operator',
        protocol_sha256='b2c3d4e5f607182940a1b2c3d4e5f607', created_at=NOW,
    ))
    adapter = MiniScripted(MINI_ROUNDS)
    run_optimization(
        adapter, adapter.seed,
        ProtocolConfig(**MINI_CONFIG),
        search_set_ids=MINI_POOL,
        val_pool_ids=MINI_POOL + MINI_VAL,
        fresh_batch_ids=MINI_BATCHES,
        smoke_sample_ids=MINI_POOL[:3],
        max_rounds=15,
        state_dir=run_dir,
    )
    state = json.loads((run_dir / 'state.json').read_text())
    atomic_write_json(run_dir / 'sealed-curve.json', mini_sealed_curve(state))
    return state


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

    real_run(runs / 'auto-done', ROUNDS)
    sealed_run(runs / 'auto-sealed')
    mini_run(runs / 'mini-exp')
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
    titles = dict(zip(('auto-new', 'auto-budget', 'auto-interrupted', 'auto-sealed', 'auto-done', 'auto-running', 'mini-exp'),
                      ('自动 · 未开始', '自动 · 预算用尽', '自动 · 已中断', '自动 · 封存集已评', '自动 · 已结束', '自动 · 运行中',
                       'v3 方法 · ALCE-ASQA · 20 题 · 探索')))
    for name, title in titles.items():
        seed_iteration(repository, workspace_id, name, title)
        auto_runs.register(repository, name, run_id=name, state_dir=str(runs / name), actor='operator')
    with run_view.hold_run_lock(runs / 'auto-running'):
        uvicorn.run(app, host='127.0.0.1', port=args.port, log_level='warning')


if __name__ == '__main__':
    main()
