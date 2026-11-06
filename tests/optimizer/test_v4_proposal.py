"""method-v4.1 §3.4 rule defaults, §3.6 guard rails, §3.7 user edits and freeze."""
import math

import pytest

from rag_eval.optimizer.v4 import proposal as P
from rag_eval.optimizer.v4.simulate import CalibrationSummary, History, simulate

CONTRACT = {'calibration_k', 'smoke', 'smoke_size', 'minibatch', 'minibatch_fraction', 'minibatch_min', 'b',
            'cards_per_analysis', 'max_concurrent_executions', 'diagnose_size', 'remeasure', 'safeguard_z',
            'safeguards', 'adapter.agent_timeout', 'adapter.execution_timeout_seconds', 'adapter.infra_retry',
            'validation_size'}


def cal(**changes):
    base = dict(rerun_var=0.03, n_probe=40, k=3, seed_fail_rate=0.35, constant_fraction=0.5, exec_overhead_s=240.0,
                per_task_s=6.0, p95_exec_s=900.0, infra_fail_rate=0.05, tokens_per_task=8000.0,
                safeguard_var={'tokens_per_task_ratio': 0.04, 'answer_produced_rate': 0.01}, pool_size=300, test_size=0)
    return CalibrationSummary(**dict(base, **changes))


INPUTS = dict(time_budget_hours=12)


def test_every_contract_key_has_value_source_reason_range_and_rule_sim():
    p = P.rule_defaults(cal(), INPUTS)
    assert set(p.items) == CONTRACT
    for key, item in p.items.items():
        assert item.source in ('rule', 'constant') and item.reason and item.confidence in P.CONFIDENCE
        assert P.check_range(item.value, item.range) is None, key
        assert item.sim_id == (p.sim_id if key in P.SIM_KEYS else None)
    assert p.sim_id == simulate(p.params(), cal())['sim_id']
    assert p.items['calibration_k'].value == 3 and p.items['safeguards'].value == P.DEFAULT_SAFEGUARDS
    assert p.items['safeguard_z'].source == 'constant' and p.items['remeasure'].value is True


def test_rules_on_a_synthetic_calibration():
    p = P.rule_defaults(cal(), INPUTS).values()
    assert p['diagnose_size'] == math.ceil(15 / 0.35 * 1.5)                 # 65
    assert p['validation_size'] == 60                                         # 240 + 60·6 = 600 = 0.5·1200
    assert p['minibatch'] is False and p['smoke'] is False                    # 0.3·492 ≤ 240; 0.1·600 ≤ 252
    assert p['max_concurrent_executions'] == 3
    assert p['b'] == 9 and p['cards_per_analysis'] == 11                      # 3·(1200+600)/600
    assert p['adapter.agent_timeout'] == 1800.0
    assert p['adapter.execution_timeout_seconds'] == 2250.0
    assert p['adapter.infra_retry'] == 1                                      # 0.05² < 1%


def test_bounds_probe_history_and_switches():
    # A fast per-task time would ask for more than the pool: capped at pool − |D|.
    fast = P.rule_defaults(cal(per_task_s=0.5, pool_size=120), INPUTS).values()
    assert fast['validation_size'] + fast['diagnose_size'] == 120
    # A slow system cannot meet ρ·T_agent: raised to |P|.
    slow = P.rule_defaults(cal(per_task_s=60.0), INPUTS)
    assert slow.items['validation_size'].value == 40 and '|P|' in slow.items['validation_size'].reason
    # Concurrency probe: stop when the gain falls under 20%.
    probe = P.rule_defaults(cal(concurrency_probe=((1, 1.0), (2, 1.9), (4, 3.0), (8, 3.3))), INPUTS)
    assert probe.items['max_concurrent_executions'].value == 4
    # History: agent timeout from p90, minibatch on when eliminations are frequent and overhead small.
    hist = History(agent_s=[600.0] * 9 + [1500.0], reached=dict(minibatch=40), eliminated=dict(minibatch=30))
    h = P.rule_defaults(cal(exec_overhead_s=20.0, per_task_s=2.0), INPUTS, hist)
    assert h.items['adapter.agent_timeout'].value == pytest.approx(max(900.0, 2 * 690.0))
    assert h.items['minibatch'].value is True and 'history' in h.items['minibatch'].reason
    # Few validation tasks: minibatch off (below 10).
    tiny = P.rule_defaults(cal(n_probe=20, per_task_s=60.0), INPUTS)
    assert tiny.items['minibatch'].value is False and 'below 10' in tiny.items['minibatch'].reason
    # Retries capped at 3; no failures → 0.
    assert P._retries(0.0) == 0 and P._retries(0.5) == 3


def test_b_is_lowered_until_six_rounds_fit():
    short = P.rule_defaults(cal(), dict(time_budget_hours=2))
    b = short.items['b'].value
    assert b < 9 and 'lowered' in short.items['b'].reason
    assert simulate(short.params(), cal())['rounds']['mid'] >= P.MIN_ROUNDS or b == 1


def test_user_inputs_and_delta_report():
    p = P.rule_defaults(cal(), dict(time_budget_hours=12, calibration_k=5, delta=0.02,
                                    safeguards={'tokens_per_task_ratio': 0.3}))
    assert p.items['calibration_k'].value == 5 and p.items['calibration_k'].source == 'user'
    assert p.items['safeguards'].source == 'user'
    assert p.notes['delta']['detectable_ok'] is False and 'below' in p.notes['delta']['message']
    assert set(p.notes['safeguard_noise_tolerance']) == {'answer_produced_rate', 'tokens_per_task_ratio'}
    tol = p.notes['safeguard_noise_tolerance']['tokens_per_task_ratio']['tolerance']
    assert tol == pytest.approx(2 * math.sqrt(2 * 0.04 / 60))
    for bad in (dict(time_budget_hours=12, calibration_k=1), dict(time_budget_hours=12, calibration_k=6),
                dict(calibration_k=3), dict(time_budget_hours=12, nope=1)):
        with pytest.raises(ValueError):
            P.rule_defaults(cal(), bad)


def test_user_edit_marks_source_and_keeps_rule_value():
    p = P.rule_defaults(cal(), INPUTS)
    edited = P.apply_user_edit(p, 'b', 5)
    assert edited.items['b'].value == 5 and edited.items['b'].source == 'user' and edited.items['b'].rule_value == 9
    assert p.items['b'].value == 9  # the original is untouched
    with pytest.raises(ValueError, match='outside'):
        P.apply_user_edit(p, 'b', 99)
    with pytest.raises(KeyError):
        P.apply_user_edit(p, 'k_val', 2)
    result = P.validate(edited, cal())
    assert result['ok'] and not result['rejected']
    frozen = P.freeze(edited, cal())
    assert frozen['config']['b'] == 5 and frozen['sources']['b'] == 'user' and frozen['rule_values']['b'] == 9
    assert frozen['config']['split'] == dict(validation_size=60)
    assert frozen['config']['adapter'] == {'agent_timeout': 1800.0, 'execution_timeout_seconds': 2250.0, 'infra_retry': 1}
    assert P.freeze(P.Proposal.from_dict(edited.to_dict()), cal()) == frozen


def test_validate_rejects_out_of_range_and_mismatched_sim_ids_per_item():
    c = cal()
    p = P.rule_defaults(c, INPUTS)
    good_params = dict(p.params(), b=6, cards_per_analysis=8)
    good = simulate(good_params, c)
    agent = P.apply_agent_values(p, {
        'b': dict(value=6, sim_id=good['sim_id'], sim_params=good_params, reason='fewer slots', confidence='medium'),
        'cards_per_analysis': dict(value=8, sim_id=good['sim_id'], sim_params=good_params, reason='b+2'),
        'max_concurrent_executions': dict(value=40, sim_id=good['sim_id'], sim_params=good_params, reason='more'),
        'smoke': dict(value=True, sim_id='sim-forged', sim_params=dict(good_params, smoke=True), reason='x'),
        'minibatch': dict(value=True, sim_id=good['sim_id'], sim_params=good_params, reason='value not simulated'),
        'adapter.infra_retry': dict(value=2, reason='no citation'),
    })
    result = P.validate(agent, c)
    reasons = {r['key']: r['reason'] for r in result['rejected']}
    assert set(reasons) == {'max_concurrent_executions', 'smoke', 'minibatch', 'adapter.infra_retry'}
    assert 'outside' in reasons['max_concurrent_executions'] and 'sim_id' in reasons['smoke']
    assert 'differs' in reasons['minibatch'] and 'cite' in reasons['adapter.infra_retry']
    out = result['proposal']
    assert result['ok'] and out.items['b'].value == 6 and out.items['b'].source == 'agent'
    for key in reasons:  # rejected agent values fall back to the rule value
        assert out.items[key].source == 'rule' and out.items[key].value == p.items[key].value
    assert out.notes['rejected'] == result['rejected']
    # A rule item validated against another calibration no longer matches its sim_id.
    stale = P.validate(p, cal(rerun_var=0.05))
    assert not stale['ok'] and all('sim_id' in r['reason'] for r in stale['fatal'])


def test_validate_cross_constraints():
    p = P.rule_defaults(cal(), INPUTS)
    crowded = P.apply_user_edit(p, 'validation_size', 290)
    result = P.validate(crowded, cal())
    assert not result['ok'] and {r['key'] for r in result['fatal']} >= {'validation_size'}
    few_cards = P.apply_user_edit(p, 'cards_per_analysis', 2)
    assert {r['key'] for r in P.validate(few_cards, cal())['fatal']} == {'cards_per_analysis'}


@pytest.mark.parametrize('key', sorted(CONTRACT))
def test_validate_requires_every_contract_key(key):
    p = P.rule_defaults(cal(), INPUTS)
    del p.items[key]
    assert not P.validate(p, cal())['ok']


def test_validate_binds_rule_values_and_rebuilds_untrusted_fallback():
    p = P.rule_defaults(cal(), INPUTS)
    p.items['b'].value = 5
    assert not P.validate(p, cal())['ok']
    p = P.rule_defaults(cal(), INPUTS)
    p.items['smoke_size'].rule_value = 999
    p.items['smoke_size'].range = dict(type='int', min=1, max=999)
    agent = P.apply_agent_values(p, {'smoke_size': dict(value=999)})
    result = P.validate(agent, cal())
    assert result['ok'] and result['proposal'].items['smoke_size'].value == P.SMOKE_SIZE
    assert result['proposal'].items['smoke_size'].range == P.allowed_ranges(cal(), INPUTS)['smoke_size']


@pytest.mark.parametrize('key,value', [('calibration_k', 4), ('safeguards', {'x': 1}),
                                      ('safeguard_z', 3), ('remeasure', False), ('minibatch_min', 11)])
def test_advisor_cannot_edit_user_inputs_or_method_constants(key, value):
    p = P.rule_defaults(cal(), INPUTS)
    params = dict(p.params(), **{P.SIM_KEYS.get(key, key): value})
    params = {k: v for k, v in params.items() if k not in ('safeguards', 'safeguard_z')}
    agent = P.apply_agent_values(p, {key: dict(value=value, sim_params=params,
                                             sim_id=simulate(params, cal())['sim_id'])})
    result = P.validate(agent, cal())
    assert result['ok'] and any(r['key'] == key for r in result['rejected'])
    assert result['proposal'].items[key].value == p.items[key].value


def test_freeze_revalidates_cross_constraints_and_simulation():
    p = P.rule_defaults(cal(), INPUTS)
    with pytest.raises(ValueError):
        P.freeze(P.apply_user_edit(p, 'validation_size', 290), cal())
    p.items['b'].sim_id = 'sim-forged'
    with pytest.raises(ValueError):
        P.freeze(p, cal())


def test_rule_b_is_bounded_by_measurement_spend():
    p = P.rule_defaults(cal(), dict(INPUTS, spend_budget=40, price_per_1k_tokens=0.01))
    assert p.items['b'].value < 9
    assert simulate(p.params(), cal())['rounds']['mid'] >= P.MIN_ROUNDS or p.items['b'].value == 1


def test_explicit_user_k_validates():
    p = P.rule_defaults(cal(k=5), dict(INPUTS, calibration_k=5))
    assert P.validate(p, cal(k=5))['ok']


def test_proposal_k_cannot_relabel_completed_calibration_measurements():
    p = P.rule_defaults(cal(), INPUTS)
    edited = P.apply_user_edit(p, 'calibration_k', 4)
    result = P.validate(edited, cal())
    assert not result['ok'] and any(r['key'] == 'calibration_k' and 'recalibrat' in r['reason']
                                   for r in result['fatal'])
    with pytest.raises(ValueError):
        P.freeze(edited, cal())
    assert not P.validate(P.rule_defaults(cal(), dict(INPUTS, calibration_k=5)), cal())['ok']


def test_protected_user_edits_survive_advisor_attempts():
    p = P.rule_defaults(cal(), INPUTS)
    p = P.apply_user_edit(p, 'safeguard_z', 3)
    agent = P.apply_agent_values(p, {'safeguard_z': dict(value=4)})
    result = P.validate(agent, cal())
    assert result['ok'] and result['proposal'].items['safeguard_z'].value == 3


def test_cross_constraint_fallback_does_not_blame_unchanged_rule_neighbor():
    p = P.rule_defaults(cal(), INPUTS)
    params = dict(p.params(), validation_size=290)
    agent = P.apply_agent_values(p, {'validation_size': dict(value=290, sim_params=params,
                                                            reason='more validation coverage',
                                                            sim_id=simulate(params, cal())['sim_id'])})
    result = P.validate(agent, cal())
    assert result['ok'] and result['proposal'].items['validation_size'].value == 60
    assert {r['key'] for r in result['rejected']} == {'validation_size'}


def test_serialized_advisor_cannot_bypass_protected_key_validation():
    p = P.rule_defaults(cal(), INPUTS)
    p.items['safeguards'].source = 'agent'
    p.items['safeguards'].value = {'x': 4}
    result = P.validate(p, cal())
    assert result['ok'] and result['proposal'].items['safeguards'].value == P.DEFAULT_SAFEGUARDS
