import pytest

pytest.importorskip("deepeval")

from deepeval.models import DeepEvalBaseLLM  # noqa: E402
from rag_eval.agent_dag import build_evidence_path_metric, dag_metadata  # noqa: E402


class FakeJudge(DeepEvalBaseLLM):
    def load_model(self):
        return self

    def generate(self, *args, **kwargs):
        return "{}"

    async def a_generate(self, *args, **kwargs):
        return "{}"

    def get_model_name(self):
        return "fake-judge"


def test_dag_metadata_is_deterministic_and_explicit():
    metadata = dag_metadata({
        "retrieval_count": 2,
        "context_available": True,
        "anchor_count": 1,
        "completeness": "complete",
    })

    assert metadata == {
        "retrieval_observed": True,
        "context_available": True,
        "citation_observed": True,
        "anchor_count": 1,
        "trace_completeness": "complete",
    }


def test_evidence_path_metric_builds_a_valid_dag():
    metric = build_evidence_path_metric(model=FakeJudge(), threshold=None)

    assert metric.name == "SN Evidence Path"
    assert len(metric.dag.root_nodes) == 1
    assert metric.threshold is None
