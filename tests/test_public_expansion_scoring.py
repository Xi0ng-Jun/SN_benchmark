from rag_eval.public_expansion_scoring import (
    normalize_gsm8k_number,
    product_applicability,
    score_gsm8k,
    score_mmlu,
    score_truthfulqa,
)


def test_mmlu_recovery_matching_is_diagnostic_only():
    result = score_mmlu("The answer is C.\nFinal answer: C", ["C"])
    assert result["score"] is None
    assert result["status"] == "diagnostic"
    assert result["diagnostic_score"] == 1.0
    assert score_mmlu("Final answer: D", ["C"])["diagnostic_score"] == 0.0
    assert score_mmlu("maybe A or B", ["A"])["diagnostic_score"] is None


def test_gsm8k_numeric_recovery_never_yields_native_score():
    value = "999999999999999999999999999999999999"
    assert normalize_gsm8k_number("#### " + value) == value
    result = score_gsm8k("work #### " + value, [value])
    assert result["score"] is None
    assert result["diagnostic_score"] == 1.0
    assert score_gsm8k("1200", ["1,200"])["diagnostic_score"] == 1.0


def test_truthfulqa_reference_membership_cannot_claim_correctness():
    result = score_truthfulqa("answer", ["answer"], behavior_label="refusal", evidence=["span-1"])
    assert result["score"] is None
    assert result["status"] == "not_applicable"
    assert result["behavior_label"] == "refusal"
    assert result["evidence"] == ["span-1"]
    assert result["annotation_status"] == "caller_supplied_unverified"
    assert "semantic truthfulness" in result["applicability"]


def test_all_expansion_product_suites_are_not_applicable():
    for suite in ("mmlu", "gsm8k", "truthfulqa", "hellaswag", "bbh"):
        result = product_applicability(suite)
        assert result["status"] == "not_applicable"
        assert result["applicable"] is False
        assert result["reason"]
        assert result["product_candidate"] is (suite in {"mmlu", "gsm8k", "truthfulqa"})
