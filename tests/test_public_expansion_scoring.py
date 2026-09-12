from rag_eval.public_expansion_scoring import (
    normalize_gsm8k_number,
    product_applicability,
    score_gsm8k,
    score_mmlu,
    score_truthfulqa,
)


def test_mmlu_label_matching_is_deterministic():
    assert score_mmlu("The answer is C.\nFinal answer: C", ["C"])["score"] == 1.0
    assert score_mmlu("Final answer: D", ["C"])["score"] == 0.0
    assert score_mmlu("maybe A or B", ["A"])["status"] == "unparsed"


def test_gsm8k_uses_decimal_strings_for_large_numbers():
    value = "999999999999999999999999999999999999"
    assert normalize_gsm8k_number("#### " + value) == value
    assert score_gsm8k("work #### " + value, [value])["score"] == 1.0


def test_truthfulqa_keeps_behavior_and_evidence_separate():
    result = score_truthfulqa("answer", ["answer"], behavior_label="refusal", evidence=["span-1"])
    assert result["score"] == 1.0
    assert result["behavior_label"] == "refusal"
    assert result["evidence"] == ["span-1"]
    assert "semantic truthfulness" in result["applicability"]


def test_unsupported_product_suite_is_not_applicable():
    result = product_applicability("mmlu")
    assert result == {
        "status": "not_applicable",
        "applicable": False,
        "reason": "Product adapter is not implemented for mmlu; Native-only scoring",
    }
