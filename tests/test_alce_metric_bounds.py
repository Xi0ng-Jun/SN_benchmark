"""Upstream percent metrics can exceed 100 by floating-point roundoff."""
import copy
import math
import pytest
from rag_eval.alce_official_cli import parse_metrics
from test_alce_official_cli import asqa_scores


@pytest.mark.parametrize('metric', [k for k in asqa_scores() if k != 'length'])
def test_all_bounded_percent_metrics_clamp_tiny_upper_roundoff_without_mutating_raw(metric):
    raw = asqa_scores()
    raw[metric] = 100 + 5e-10
    saved = copy.deepcopy(raw)
    metrics, diagnostics = parse_metrics('asqa', raw)
    assert metrics[metric] == 1.
    assert raw == saved
    assert diagnostics['length'] == 50


@pytest.mark.parametrize('metric', [k for k in asqa_scores() if k != 'length'])
@pytest.mark.parametrize('invalid', [100 + 2e-9, -1e-12, math.nan, math.inf, -math.inf])
def test_out_of_tolerance_negative_or_nonfinite_percent_values_remain_invalid(metric, invalid):
    raw = asqa_scores()
    raw[metric] = invalid
    with pytest.raises(ValueError):
        parse_metrics('asqa', raw)


def test_normalization_is_recorded_but_diagnostics_are_not_clamped():
    from rag_eval.alce_official_cli import metric_normalizations
    raw = asqa_scores()
    raw.update(mauve=100.00000000000004, length=1000.)
    metrics, diagnostics = parse_metrics('asqa', raw)
    changes = metric_normalizations(metrics, raw)
    assert changes == {'mauve': {'raw_percent': 100.00000000000004,
                                'normalized_percent': 100., 'reason': 'upper-bound floating-point roundoff'}}
    assert diagnostics['length'] == 1000.


@pytest.mark.parametrize('task', ['qampari', 'eli5'])
@pytest.mark.parametrize('key', ['str_em', 'str_hit'])
@pytest.mark.parametrize('value', [100 + 5e-10, 100 + 2e-9, -1e-12, math.nan, math.inf])
def test_bounded_diagnostics_follow_same_rule(task, key, value):
    raw = dict(length=10, str_em=0, str_hit=0, citation_rec=50, citation_prec=50)
    if task == 'qampari':
        raw.update(num_preds=1000, qampari_prec=50, qampari_rec=50,
                   qampari_rec_top5=50, qampari_f1=50, qampari_f1_top5=50)
    else:
        raw.update(rougeLsum=50, mauve=50, claims_nli=50)
    raw[key] = value
    if value == 100 + 5e-10:
        _, diagnostics = parse_metrics(task, raw)
        assert diagnostics[key] == 100
        assert raw[key] == value
    else:
        with pytest.raises(ValueError):
            parse_metrics(task, raw)
