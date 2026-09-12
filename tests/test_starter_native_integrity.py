import pytest


def test_expansion_request_cannot_override_frozen_expected_output():
    from rag_eval.starter_native import score_prediction

    case = {"suite": "mmlu", "case_id": "mmlu-1", "references": ["C"]}
    request = {"suite": "mmlu", "case_id": "mmlu-1", "expected_output": "D"}

    with pytest.raises(ValueError, match="expected output differs"):
        score_prediction(case, request, "C", manifest={})
