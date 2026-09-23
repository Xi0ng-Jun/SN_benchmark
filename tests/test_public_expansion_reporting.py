from rag_eval.starter_results import planned_result, result_record, summarize


def _case(task="college_biology"):
    return {
        "suite": "mmlu", "task": task, "case_id": "mmlu-1", "sample_id": "mmlu-1",
        "scorer": "deepeval.mmlu", "product_review": {
            "status": "not_applicable", "reason": "Native-only in this run"
        },
    }


def test_native_and_product_scores_remain_separate_groups():
    native = planned_result(_case(), run_id="n", protocol_id="pn", track="N")
    product = planned_result(_case(), run_id="r", protocol_id="pr", track="R", mode="chunk",
                             scorer="product.mmlu")
    rows = summarize(
        [native, product],
        [result_record(native, status="scored", score=1.0, output_available=True),
         result_record(product, status="not_applicable", reason="adapter unavailable")],
    )
    assert {(row["track"], row["mode"]) for row in rows} == {("N", None), ("R", "chunk")}
    assert next(row for row in rows if row["track"] == "N")["scored"] == 1
    assert next(row for row in rows if row["track"] == "R")["non_applicable"] == 1


def test_missing_trace_suppresses_agent_metrics_and_is_counted():
    planned = planned_result(_case(), run_id="n", protocol_id="pn", track="N")
    row = summarize([planned], [result_record(planned, status="scored", score=1.0,
                                               output_available=True)])[0]
    assert row["trace_completeness"] == {"none": 1, "partial": 0, "complete": 0}
    assert row["agent_metrics_suppressed"] is True
