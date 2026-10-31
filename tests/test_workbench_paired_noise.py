import math
import statistics

import pytest

from rag_eval.workbench.noise import estimate, paired_delta, paired_means


def attempts(values_by_case, k, family=None):
    """values_by_case: {case_id: [score per attempt]}."""
    return [dict(execution_id=f'e{i}', rows=[dict(case_id=c, family=(family or {}).get(c, c), value=v[i])
                                             for c, v in values_by_case.items()]) for i in range(k)]


def test_raw_scores_are_paired_without_thresholding():
    ref = {f'c{i}': [1 / 3, 1 / 3] for i in range(20)}
    sub = {f'c{i}': [.45, .45] for i in range(20)}
    sub['c0'] = [.4, .5]
    out = paired_delta(attempts(ref, 2), attempts(sub, 2), z=1.65)
    # Nothing crosses .5 on average, yet the raw paired gain is measured.
    assert out['statistic'] == 'paired_raw' and out['k'] == 2 and out['n'] == 20
    assert out['delta'] == pytest.approx(.45 - 1 / 3)
    binarized = [estimate(dict(attempts=attempts(v, 2)))['cases']['c1']['p'] for v in (ref, sub)]
    assert binarized == [0, 0]


def test_iid_se_is_sd_over_root_n_and_bounds_use_caller_z():
    ref = {f'c{i}': [0.] for i in range(6)}
    gains = [.1, .3, -.2, .5, 0., .4]
    sub = {f'c{i}': [g if g > 0 else 0.] for i, g in enumerate(gains)}
    ref['c2'] = [.2]
    d = [max(g, 0.) for g in gains]
    d[2] = -.2
    out = paired_delta(attempts(ref, 1), attempts(sub, 1), z=.84)
    assert out['se_method'] == 'iid' and out['clusters'] == 6
    assert out['delta'] == pytest.approx(statistics.mean(d))
    assert out['se'] == pytest.approx(statistics.stdev(d) / math.sqrt(6))
    assert out['n_eff'] == pytest.approx(6) and out['icc'] == 0
    assert out['lower'] == pytest.approx(out['delta'] - .84 * out['se'])
    assert out['upper'] == pytest.approx(out['delta'] + .84 * out['se'])
    assert out['deltas']['c2'] == pytest.approx(-.2)


def test_family_cluster_se_equals_between_family_spread_for_equal_families():
    # Four families of three tasks; within-family gains are correlated.
    gains = {'A': [.5, .6, .4], 'B': [0., .1, -.1], 'C': [.3, .3, .3], 'D': [-.2, -.1, -.3]}
    ref, sub, family = {}, {}, {}
    for f, values in gains.items():
        for j, g in enumerate(values):
            cid = f'{f}{j}'
            ref[cid], sub[cid], family[cid] = [.3], [.3 + g], f
    out = paired_means({c: v[0] for c, v in ref.items()}, {c: v[0] for c, v in sub.items()}, z=1.65, families=family)
    means = [statistics.mean(v) for v in gains.values()]
    assert out['se_method'] == 'cr1-family' and out['clusters'] == 4 and out['n'] == 12
    assert out['se'] == pytest.approx(statistics.stdev(means) / math.sqrt(4))
    iid = statistics.stdev([g for v in gains.values() for g in v]) / math.sqrt(12)
    assert out['se'] > iid and out['n_eff'] < 12 and out['icc'] > .5
    # Families default to the rows' own family labels.
    assert paired_delta(attempts(ref, 1, family), attempts(sub, 1, family), z=1.65)['se'] == pytest.approx(out['se'])


def test_k_averaging_unknowns_unpaired_and_single_family():
    ref = attempts({'a': [0., 1.], 'b': [.5, .5], 'c': [.2, .2]}, 2, {'a': 'f', 'b': 'f', 'c': 'f'})
    sub = attempts({'a': [1., 1.], 'b': [1., None], 'x': [1., 1.]}, 2, {'a': 'f', 'b': 'f', 'x': 'f'})
    out = paired_delta(ref, sub, z=1.65)
    # a: .5 -> 1, b: .5 -> 1 (the unknown attempt is ignored); c and x are unpaired.
    assert out['n'] == 2 and out['deltas'] == {'a': .5, 'b': .5}
    assert out['se_method'] == 'iid' and out['se'] == 0 and out['lower'] == .5
    single = paired_means({'a': .1}, {'a': .3}, z=1.65)
    assert single['n'] == 1 and single['se'] is None and single['lower'] is None
    assert paired_means({}, {}, z=1.65)['delta'] is None
    with pytest.raises(ValueError):
        paired_means({'a': 1.5}, {'a': .3}, z=1.65)
