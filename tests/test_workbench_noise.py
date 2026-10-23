import math

import pytest

from rag_eval.workbench import noise


def test_rerun_noise_is_hand_computable_and_preserves_missing_scores():
    candidate = dict(attempts=[
        dict(execution_id='e1', attempt=1, rows=[dict(case_id='a', family='p', value=0), dict(case_id='b', family='q', value=1)]),
        dict(execution_id='e2', attempt=2, rows=[dict(case_id='a', family='p', value=1), dict(case_id='b', family='q', value=1)]),
    ])
    result = noise.estimate(candidate)
    assert result['eta'] == pytest.approx(.25)
    assert result['n_eff'] == 2 and result['k_below_base'] is False
    assert result['cases']['a']['p'] == .5
    assert result['cases']['a']['half_width'] == pytest.approx(1.6448536269514722 * math.sqrt(.25 / 2))
    assert result['pairs'][0]['deltas'] == {'a': 1, 'b': 0}
    candidate['attempts'].pop()
    assert noise.estimate(candidate)['k_below_base'] is True
    candidate['attempts'][0]['rows'][0]['value'] = None
    assert noise.estimate(candidate)['cases']['a']['p'] is None


def test_family_icc_and_effective_size():
    assert noise.icc_from_families({'p': [0, 0], 'q': [1, 1]}) == 1
    assert noise.n_eff(4, 2, 1) == 2
    assert noise.icc_from_families({'p': [0, 1], 'q': [0, 1]}) == 0
