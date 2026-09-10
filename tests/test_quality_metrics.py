from deepeval.metrics import GEval, FaithfulnessMetric, AnswerRelevancyMetric
from deepeval.models import DeepEvalBaseLLM

from rag_eval.quality_metrics import build_quality_metrics, expected_answer, metric_availability


class Judge(DeepEvalBaseLLM):
    def __init__(self):
        super().__init__(model="test-judge")

    def load_model(self):
        return self

    def get_model_name(self):
        return "test-judge"

    def generate(self, prompt, schema=None):
        raise AssertionError("constructing metrics must not call the judge")

    async def a_generate(self, prompt, schema=None):
        return self.generate(prompt, schema)


def test_primary_metrics_preserve_alternative_references_and_skip_unsupported_context():
    record = {
        "dataset": "drop",
        "references": ["12", "twelve"],
        "context_supported": False,
        "retrieval_context": [],
    }

    metrics = build_quality_metrics(record, Judge())

    assert [type(metric) for metric in metrics] == [GEval, AnswerRelevancyMetric]
    assert expected_answer(record) == '["12", "twelve"]'
    assert metrics[0].criteria is None
    assert metrics[0].evaluation_steps is not None
    availability = metric_availability(record)
    assert availability["Faithfulness"]["status"] == "skipped"
    assert "context" in availability["Faithfulness"]["reason"].lower()


def test_debug_records_add_contextual_diagnostics_when_capture_is_supported():
    record = {
        "dataset": "squad",
        "references": ["Ada"],
        "context_supported": True,
        "retrieval_context": ["Ada wrote the notes."],
        "deterministic": {"ranking_available": True},
        "split": "debug",
    }

    metrics = build_quality_metrics(record, Judge(), diagnostic=True)

    assert isinstance(metrics[0], GEval)
    assert isinstance(metrics[1], FaithfulnessMetric)
    assert isinstance(metrics[2], AnswerRelevancyMetric)
    assert [metric.__class__.__name__ for metric in metrics[3:]] == [
        "ContextualRecallMetric",
        "ContextualPrecisionMetric",
        "ContextualRelevancyMetric",
    ]
