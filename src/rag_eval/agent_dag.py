"""DAGMetric definitions for product-specific Agent behavior."""
from __future__ import annotations

from typing import Any, Mapping


def dag_metadata(diagnostics: Mapping[str, Any]) -> dict[str, Any]:
    """Convert deterministic observations into the DAG's explicit inputs."""
    retrieval_count = diagnostics.get("retrieval_count")
    anchor_count = diagnostics.get("anchor_count")
    return {
        "retrieval_observed": isinstance(retrieval_count, (int, float)) and not isinstance(retrieval_count, bool) and retrieval_count > 0,
        "context_available": diagnostics.get("context_available") is True,
        "citation_observed": isinstance(anchor_count, (int, float)) and not isinstance(anchor_count, bool) and anchor_count > 0,
        "anchor_count": int(anchor_count) if isinstance(anchor_count, (int, float)) and not isinstance(anchor_count, bool) and anchor_count >= 0 else 0,
        "trace_completeness": str(diagnostics.get("completeness") or "none"),
    }


def build_evidence_path_metric(*, model: object | None = None, threshold: float | None = 0.5):
    """Build the first product DAG without executing it.

    The first three nodes act as gates.  Terminal scores are deliberately
    coarse and fixed. BinaryJudgementNode uses an LLM at every node, including
    metadata gates; the deterministic observations are supplied by our adapter.
    """
    try:
        from deepeval.metrics import DAGMetric
        from deepeval.metrics.dag import BinaryJudgementNode, DeepAcyclicGraph
        from deepeval.test_case import SingleTurnParams
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("DeepEval is required for DAG scoring; install .[deepeval]") from exc

    retrieval = BinaryJudgementNode(
        criteria="Does the evaluation metadata show that the agent performed at least one retrieval action?",
        evaluation_params=[SingleTurnParams.METADATA],
        label="retrieval observed",
    )
    context = BinaryJudgementNode(
        criteria="Does the evaluation metadata show that usable context was available to the final answer?",
        evaluation_params=[SingleTurnParams.METADATA],
        label="context available",
    )
    citation = BinaryJudgementNode(
        criteria="Does the evaluation metadata show at least one observed citation or evidence anchor?",
        evaluation_params=[SingleTurnParams.METADATA],
        label="citation observed",
    )
    support = BinaryJudgementNode(
        criteria="Is the actual answer supported by the input task and the available evidence?",
        evaluation_params=[
            SingleTurnParams.INPUT,
            SingleTurnParams.ACTUAL_OUTPUT,
            SingleTurnParams.RETRIEVAL_CONTEXT,
            SingleTurnParams.EXPECTED_OUTPUT,
            SingleTurnParams.METADATA,
        ],
        label="answer supported",
    )

    retrieval.add_verdict(verdict=False, score=0)
    retrieval.add_verdict(verdict=True, then=context)
    context.add_verdict(verdict=False, score=2)
    context.add_verdict(verdict=True, then=citation)
    citation.add_verdict(verdict=False, score=5)
    citation.add_verdict(verdict=True, then=support)
    support.add_verdict(verdict=False, score=5)
    support.add_verdict(verdict=True, score=10)

    kwargs = {"model": model} if model is not None else {}
    return DAGMetric(
        name="SN Evidence Path",
        dag=DeepAcyclicGraph(root_nodes=[retrieval]),
        threshold=threshold,
        **kwargs,
    )
