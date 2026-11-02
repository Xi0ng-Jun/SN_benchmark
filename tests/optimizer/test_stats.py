"""Unit tests for rag_eval.optimizer.stats (method-v3 decision statistics)."""
from __future__ import annotations

import math
import statistics

import pytest

from rag_eval.optimizer.stats import (
    alpha_warning,
    attribution_label,
    confirm_decision,
    ladder_m,
    mean_delta,
    paired_differences,
    paired_se,
    required_n_fresh,
    screen_decision,
)


# ---------------------------------------------------------------------------
# paired_differences / mean_delta
# ---------------------------------------------------------------------------

def test_paired_differences_basic():
    result = paired_differences([0.8, 0.6, 0.9], [0.5, 0.4, 0.9])

    assert result == pytest.approx([0.3, 0.2, 0.0])


def test_paired_differences_length_mismatch_raises():
    with pytest.raises(ValueError):
        paired_differences([0.8, 0.6], [0.5])


def test_mean_delta_basic_average():
    assert mean_delta([1.0, 2.0, 3.0]) == pytest.approx(2.0)
    assert mean_delta([-0.5, 0.5]) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# paired_se: iid and CR1 paths
# ---------------------------------------------------------------------------

def test_paired_se_iid_matches_sample_std_over_sqrt_n():
    differences = [0.1, 0.4, -0.2, 0.3, 0.0]

    expected = statistics.stdev(differences) / math.sqrt(len(differences))

    assert paired_se(differences) == pytest.approx(expected)
    # Explicit None families must behave exactly like the iid path.
    assert paired_se(differences, families=None) == pytest.approx(expected)


def test_paired_se_cr1_is_larger_than_iid_for_unbalanced_clusters():
    # Three tasks in family "a" (all above the mean) and one in family "b"
    # (far below the mean) makes the clusters strongly unbalanced.
    differences = [1.0, 1.0, 1.0, -3.0]
    families = {0: "a", 1: "a", 2: "a", 3: "b"}

    iid = paired_se(differences)
    cr1 = paired_se(differences, families=families)

    assert isinstance(cr1, float)
    assert cr1 > 0.0
    assert cr1 > iid
    # With g=2 and this data: delta=0, score_a=3, score_b=-3, n=4
    # CR1 = sqrt(2/(2-1) * (9 + 9)) / 4 = 6/4 = 1.5
    assert cr1 == pytest.approx(1.5)
    assert iid == pytest.approx(1.0)


def test_paired_se_falls_back_to_iid_with_single_cluster():
    differences = [0.1, 0.4, -0.2, 0.3]
    families = {0: "only", 1: "only", 2: "only", 3: "only"}

    assert paired_se(differences, families=families) == pytest.approx(
        paired_se(differences)
    )


# ---------------------------------------------------------------------------
# ladder_m
# ---------------------------------------------------------------------------

def test_ladder_m_returns_floor_without_enough_data():
    assert ladder_m(sigma=1.0, K=1, n_val=100) == 0.02
    assert ladder_m(sigma=1.0, K=0, n_val=100) == 0.02
    assert ladder_m(sigma=1.0, K=10, n_val=1) == 0.02
    assert ladder_m(sigma=1.0, K=10, n_val=0) == 0.02


def test_ladder_m_normal_case_exceeds_floor():
    m = ladder_m(sigma=1.0, K=4, n_val=100)

    assert m > 0.02
    assert m == pytest.approx(math.sqrt(math.log(8) / 100))


def test_ladder_m_grows_with_K_and_shrinks_with_n_val():
    base = ladder_m(sigma=1.0, K=4, n_val=100)

    assert ladder_m(sigma=1.0, K=8, n_val=100) > base
    assert ladder_m(sigma=1.0, K=4, n_val=200) < base


# ---------------------------------------------------------------------------
# screen_decision
# ---------------------------------------------------------------------------

def test_screen_decision_unmeasurable_when_safeguards_fail():
    assert screen_decision(
        delta_val=10.0,
        se_val=0.1,
        z_s=1.0,
        best_score=0.0,
        candidate_score=10.0,
        m=0.0,
        safeguards_ok=False,
    ) == "unmeasurable"


def test_screen_decision_fails_when_lower_bound_not_positive():
    # lower = 0.1 - 1.0 * 0.1 = 0.0, so the screen must fail.
    assert screen_decision(
        delta_val=0.1,
        se_val=0.1,
        z_s=1.0,
        best_score=0.0,
        candidate_score=10.0,
        m=0.0,
        safeguards_ok=True,
    ) == "screen_fail"


def test_screen_decision_fails_when_candidate_does_not_beat_best_plus_m():
    # lower = 1.0 - 1.0 * 0.1 = 0.9 > 0, but candidate 0.5 <= best 0.5 + m 0.02.
    assert screen_decision(
        delta_val=1.0,
        se_val=0.1,
        z_s=1.0,
        best_score=0.5,
        candidate_score=0.5,
        m=0.02,
        safeguards_ok=True,
    ) == "screen_fail"


def test_screen_decision_passes_when_both_conditions_hold():
    assert screen_decision(
        delta_val=1.0,
        se_val=0.1,
        z_s=1.0,
        best_score=0.5,
        candidate_score=1.0,
        m=0.1,
        safeguards_ok=True,
    ) == "screen_pass"


# ---------------------------------------------------------------------------
# confirm_decision
# ---------------------------------------------------------------------------

def test_confirm_decision_confirmed_when_lower_bound_positive():
    assert confirm_decision(
        delta_fresh=1.0,
        se_fresh=0.1,
        z_c=1.0,
        safeguards_ok=True,
    ) == "confirmed"


def test_confirm_decision_rejected_when_upper_bound_non_positive():
    assert confirm_decision(
        delta_fresh=-1.0,
        se_fresh=0.1,
        z_c=1.0,
        safeguards_ok=True,
    ) == "rejected"
    # upper = 0.0 exactly still counts as rejected.
    assert confirm_decision(
        delta_fresh=-0.1,
        se_fresh=0.1,
        z_c=1.0,
        safeguards_ok=True,
    ) == "rejected"


def test_confirm_decision_insufficient_when_interval_straddles_zero():
    assert confirm_decision(
        delta_fresh=0.1,
        se_fresh=1.0,
        z_c=1.0,
        safeguards_ok=True,
    ) == "insufficient"


def test_confirm_decision_unmeasurable_when_safeguards_fail():
    assert confirm_decision(
        delta_fresh=10.0,
        se_fresh=0.1,
        z_c=1.0,
        safeguards_ok=False,
    ) == "unmeasurable"


# ---------------------------------------------------------------------------
# required_n_fresh
# ---------------------------------------------------------------------------

def test_required_n_fresh_returns_positive_int():
    n = required_n_fresh(sigma_d=0.5, mde=0.2)

    assert isinstance(n, int)
    assert n >= 1
    assert n == math.ceil((1.65 + 0.84) ** 2 * 0.5 ** 2 / 0.2 ** 2)


@pytest.mark.parametrize(
    "sigma_d,mde",
    [
        (0.0, 0.2),
        (0.5, 0.0),
        (-0.5, 0.2),
        (0.5, -0.2),
    ],
)
def test_required_n_fresh_rejects_non_positive_inputs(sigma_d, mde):
    with pytest.raises(ValueError):
        required_n_fresh(sigma_d=sigma_d, mde=mde)


# ---------------------------------------------------------------------------
# alpha_warning
# ---------------------------------------------------------------------------

def test_alpha_warning_single_confirmation_matches_alpha():
    result = alpha_warning(1, alpha=0.05)

    assert result["familywise_error"] == pytest.approx(0.05)
    assert result["per_test_alpha"] == pytest.approx(0.05)
    assert result["n_confirmations"] == 1


def test_alpha_warning_familywise_error_accumulates():
    one = alpha_warning(1, alpha=0.05)
    two = alpha_warning(2, alpha=0.05)

    assert two["familywise_error"] > one["familywise_error"]
    assert two["familywise_error"] == pytest.approx(1.0 - 0.95 ** 2)
    assert two["bonferroni_alpha"] == pytest.approx(0.025)


def test_alpha_warning_rejects_fewer_than_one_confirmation():
    with pytest.raises(ValueError):
        alpha_warning(0)
    with pytest.raises(ValueError):
        alpha_warning(-1)


# ---------------------------------------------------------------------------
# attribution_label
# ---------------------------------------------------------------------------

def test_attribution_requires_independent_parent_slice():
    with pytest.raises(ValueError):
        attribution_label(
            r_p=0.5,
            r_c=0.3,
            delta_S=0.2,
            delta_notS=0.1,
            predicted_direction="decrease",
            n_min=30,
            slice_size=100,
            independent_parent_slice=False,
        )


def test_attribution_insufficient_when_slice_too_small():
    assert attribution_label(
        r_p=0.5,
        r_c=0.3,
        delta_S=0.2,
        delta_notS=0.1,
        predicted_direction="decrease",
        n_min=30,
        slice_size=29,
        independent_parent_slice=True,
    ) == "insufficient"


def test_attribution_supported_when_prediction_moves_and_slice_gains_most():
    # Failure rate falls as predicted and the gain is concentrated in-slice.
    assert attribution_label(
        r_p=0.5,
        r_c=0.3,
        delta_S=0.2,
        delta_notS=0.1,
        predicted_direction="decrease",
        n_min=30,
        slice_size=100,
        independent_parent_slice=True,
    ) == "supported"


def test_attribution_supported_for_predicted_increase():
    assert attribution_label(
        r_p=0.3,
        r_c=0.5,
        delta_S=0.2,
        delta_notS=0.1,
        predicted_direction="increase",
        n_min=30,
        slice_size=100,
        independent_parent_slice=True,
    ) == "supported"


def test_attribution_refuted_when_prediction_does_not_move():
    # Predicted decrease, but the slice rate went up instead.
    assert attribution_label(
        r_p=0.3,
        r_c=0.5,
        delta_S=0.2,
        delta_notS=0.1,
        predicted_direction="decrease",
        n_min=30,
        slice_size=100,
        independent_parent_slice=True,
    ) == "refuted"


def test_attribution_refuted_when_slice_does_not_improve():
    # Prediction moved in the right direction, but the slice has no gain.
    assert attribution_label(
        r_p=0.5,
        r_c=0.3,
        delta_S=0.0,
        delta_notS=0.1,
        predicted_direction="decrease",
        n_min=30,
        slice_size=100,
        independent_parent_slice=True,
    ) == "refuted"


def test_attribution_refuted_when_gain_is_mostly_outside_slice():
    # Prediction moved and the slice improved, but not more than the rest.
    assert attribution_label(
        r_p=0.5,
        r_c=0.3,
        delta_S=0.1,
        delta_notS=0.2,
        predicted_direction="decrease",
        n_min=30,
        slice_size=100,
        independent_parent_slice=True,
    ) == "refuted"
