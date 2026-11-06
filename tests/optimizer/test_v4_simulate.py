"""method-v4.1 §3.5 simulator: intervals, reproducible sim_id, queueing and history."""
import dataclasses
import json
import math

import pytest

from rag_eval.optimizer.v4.simulate import (CalibrationSummary, History, minibatch_size, noise_adoption_probability,
                                            read_history, resolution_se, simulate)


def cal(**changes):
    base = dict(rerun_var=0.03, n_probe=40, k=3, seed_fail_rate=0.35, constant_fraction=0.5, exec_overhead_s=240.0,
                per_task_s=6.0, p95_exec_s=900.0, infra_fail_rate=0.05, tokens_per_task=8000.0,
                safeguard_var={'tokens_per_task_ratio': 0.04}, pool_size=300, test_size=0)
    return CalibrationSummary(**dict(base, **changes))


PARAMS = dict(validation_size=60, b=4, max_concurrent_executions=3, time_budget_hours=12)


def test_outputs_are_ordered_intervals_and_list_assumptions():
    out = simulate(PARAMS, cal())
    assert set(out) >= {'sim_id', 'per_round_s', 'candidates', 'rounds', 'resolution', 'spend', 'bottleneck', 'assumptions'}
    for key in ('per_round_s', 'candidates', 'rounds'):
        assert out[key]['low'] <= out[key]['mid'] <= out[key]['high']
    for key in ('se', 'detectable'):
        assert out['resolution'][key]['low'] <= out['resolution'][key]['mid'] <= out['resolution'][key]['high']
    assert out['candidates']['mid'] == out['rounds']['mid'] * 4
    assert out['bottleneck'] in ('agent', 'execution', 'wait')
    names = {a['name']: a['source'] for a in out['assumptions']}
    assert names == dict(agent_s_per_card='assumed', analysis_s_per_round='assumed', sigma2_het='assumed')


def test_sim_id_is_reproducible_and_input_sensitive():
    a = simulate(PARAMS, cal())
    assert simulate(dict(PARAMS), cal()) == a
    assert simulate(PARAMS, dataclasses.asdict(cal()))['sim_id'] == a['sim_id']  # dict calibration
    assert simulate(dict(PARAMS, b=5), cal())['sim_id'] != a['sim_id']
    assert simulate(PARAMS, cal(rerun_var=0.031))['sim_id'] != a['sim_id']
    assert simulate(PARAMS, cal(), History(agent_s=[900.0] * 6))['sim_id'] != a['sim_id']
    with pytest.raises(ValueError, match='unknown'):
        simulate(dict(PARAMS, bogus=1), cal())
    with pytest.raises(ValueError, match='time_budget'):
        simulate(dict(PARAMS, time_budget_hours=0), cal())


def test_resolution_follows_the_se_formula():
    c = cal()
    out = simulate(dict(PARAMS, calibration_k=3), c)
    se = resolution_se(c, 60, 0.02, 3)
    assert math.isclose(se, math.sqrt((0.02 + 0.03 * (1 + 1 / 3)) / 60))
    assert math.isclose(out['resolution']['se']['mid'], se)
    assert math.isclose(out['resolution']['detectable']['mid'], 1.84 * se)
    assert simulate(dict(PARAMS, validation_size=120), c)['resolution']['se']['mid'] < se


def test_queueing_round_waits_for_the_slowest_card_and_execution_slots():
    c = cal(exec_overhead_s=0.0, per_task_s=10.0)  # T_meas(60) = 600 s
    fast_agents = History(agent_s=[100.0] * 10, analysis_s=[60.0] * 10)
    one = simulate(dict(PARAMS, b=1, max_concurrent_executions=1, remeasure=False), c, fast_agents)
    assert one['per_round_s']['mid'] == pytest.approx(60 + 100 + 600 + 30)
    # Eight cards on one execution slot queue their measurements.
    queued = simulate(dict(PARAMS, b=8, max_concurrent_executions=1, remeasure=False), c, fast_agents)
    assert queued['per_round_s']['mid'] == pytest.approx(60 + 100 + 8 * 600 + 30)
    assert queued['bottleneck'] == 'execution'
    # Slow agents and plenty of slots: the agent is the bottleneck.
    slow = History(agent_s=[3000.0] * 10, analysis_s=[60.0] * 10)
    assert simulate(dict(PARAMS, b=2, max_concurrent_executions=8, remeasure=False), c, slow)['bottleneck'] == 'agent'
    # The re-measure adds one full validation measurement per round.
    with_rm = simulate(dict(PARAMS, b=1, max_concurrent_executions=1), c, fast_agents)
    assert with_rm['per_round_s']['mid'] == pytest.approx(one['per_round_s']['mid'] + 600)


def test_stages_eliminate_work_and_minibatch_needs_ten_tasks():
    assert minibatch_size(60, 0.3, 10) == 18 and minibatch_size(30, 0.3, 10) == 0
    c = cal(exec_overhead_s=0.0, per_task_s=10.0)
    hist = History(agent_s=[100.0] * 10, analysis_s=[60.0] * 10, reached=dict(minibatch=100), eliminated=dict(minibatch=50))
    base = simulate(dict(PARAMS, b=8, max_concurrent_executions=1, remeasure=False), c, hist)
    mb = simulate(dict(PARAMS, b=8, max_concurrent_executions=1, remeasure=False, minibatch=True), c, hist)
    assert mb['minibatch_size'] == 18 and mb['per_round_s']['mid'] < base['per_round_s']['mid']
    assert {a['name']: a['source'] for a in mb['assumptions']}['minibatch_elimination_rate'] == 'history'


def test_spend_and_noise_adoptions():
    out = simulate(dict(PARAMS, price_per_1k_tokens=0.01, spend_budget=1e9), cal())
    assert out['spend']['cost']['mid'] == pytest.approx(out['spend']['tokens']['mid'] * 0.01 / 1000)
    assert out['spend']['within_budget'] is True
    single, double = noise_adoption_probability(1.0, False), noise_adoption_probability(1.0, True)
    assert single == pytest.approx(0.1587, abs=1e-3) and 0 < double < single
    assert out['noise_adoptions']['expected']['mid'] == pytest.approx(out['candidates']['mid'] * double)


def test_partial_execution_wave_cannot_finish_in_fractional_slots():
    hist = History(agent_s=[100.0] * 10, analysis_s=[60.0] * 10)
    c = cal(exec_overhead_s=0.0, per_task_s=10.0)
    for b, slots in ((4, 3), (7, 3), (3, 8)):
        out = simulate(dict(PARAMS, b=b, max_concurrent_executions=slots, remeasure=False), c, hist)
        assert out['per_round_s'] == dict.fromkeys(('low', 'mid', 'high'), 60 + 100 + math.ceil(b / slots) * 600 + 30)


def test_binding_spend_budget_caps_whole_rounds_and_marks_cost_scope():
    params = dict(PARAMS, price_per_1k_tokens=0.01)
    normal = simulate(params, cal())
    bound = simulate(dict(params, spend_budget=10), cal())
    assert bound['candidates']['high'] < normal['candidates']['low']
    assert bound['spend']['cost']['high'] <= 10
    assert bound['spend']['scope'] == 'measurement_only'
    assert bound['spend']['agent_cost_included'] is False
    tiny = simulate(dict(params, spend_budget=0.01), cal())
    assert tiny['rounds'] == dict.fromkeys(('low', 'mid', 'high'), 0)
    assert tiny['spend']['within_budget'] is False  # even the seed top-up cannot fit


def test_spend_limit_requires_a_price():
    with pytest.raises(ValueError, match='price'):
        simulate(dict(PARAMS, spend_budget=10), cal())


def test_read_history_uses_stable_ledger_fields_and_skips_garbage(tmp_path):
    ledger = dict(kind='method-v4-ledger', rounds=[
        dict(round=1, analysis_s=400.0, records={
            'a': dict(stage='measured', timings=dict(agent_s=800.0, smoke_s=10.0, minibatch_s=50.0)),
            'b': dict(stage='smoke_fail', timings=dict(agent_s=1000.0, smoke_s=10.0)),
            'c': dict(stage='minibatch_fail', timings=dict(agent_s=1200.0, smoke_s=10.0, minibatch_s=50.0)),
            'd': 'garbage'}),
        'garbage'])
    (tmp_path / 'ledger.json').write_text(json.dumps(ledger))
    (tmp_path / 'bad.json').write_text('{not json')
    hist = read_history([tmp_path / 'ledger.json', tmp_path / 'bad.json', tmp_path / 'missing.json'])
    assert hist.ledgers == 1 and sorted(hist.agent_s) == [800.0, 1000.0, 1200.0] and hist.analysis_s == [400.0]
    assert hist.reached == dict(smoke=3, minibatch=2) and hist.eliminated == dict(smoke=1, minibatch=1)
    # Too few observations: the defaults stay, marked assumed.
    assert hist.agent_interval()[1] == 'assumed' and hist.elimination('smoke')[1] == 'assumed'
    many = History(agent_s=[float(x) for x in range(100, 1100, 100)])
    (lo, mid, hi), source = many.agent_interval()
    assert source == 'history' and lo < mid < hi and many.agent_p90() == pytest.approx(910.0)
