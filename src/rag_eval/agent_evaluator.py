"""Read-only orchestration for offline Agent and DAG evaluation artifacts."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

from .agent_diagnostics import compare_modes, diagnose_trace
from .agent_trace import AgentTraceEnvelope
from .artifacts import save_json, save_jsonl
from .starter_runner import read_rows


def _record_parts(row: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    product = row.get("product_record")
    product = dict(product) if isinstance(product, Mapping) else {}
    case = dict(row)
    for key, value in product.items():
        case.setdefault(key, value)
    output = dict(product)
    output.update({key: row[key] for key in ("prediction", "answer", "actual_output", "output_available") if key in row})
    return case, output


def _available(record: Mapping[str, Any], output: Mapping[str, Any], key: str) -> bool:
    if isinstance(record.get(key), bool):
        return record[key]
    if isinstance(output.get(key), bool):
        return output[key]
    if key == "context_available":
        return isinstance(output.get("retrieval_context"), list) and bool(output["retrieval_context"])
    if key == "citations_available":
        return bool(output.get("anchor_documents") or output.get("citations"))
    return False


def _envelope(row: Mapping[str, Any], case: Mapping[str, Any], output: Mapping[str, Any]) -> AgentTraceEnvelope:
    raw = row.get("trace")
    if raw is None:
        raw = output.get("trace")
    observed = {
        "trace": raw,
        "status": row.get("status") or output.get("status") or "unknown",
        "answer": row.get("prediction") or row.get("answer") or output.get("answer") or "",
        "context_available": _available(case, output, "context_available"),
        "citations_available": _available(case, output, "citations_available"),
    }
    return AgentTraceEnvelope.from_record(
        observed,
        case_id=str(row.get("case_id") or case.get("case_id") or "unknown"),
        mode=str(row.get("mode") or case.get("mode") or "unknown"),
        final_output_available=bool(row.get("output_available") or observed["answer"]),
    )


def _score_dag(test_case: Any, envelope: AgentTraceEnvelope, diagnostics: Mapping[str, Any], *, model: object | None) -> dict[str, Any]:
    if envelope.completeness != "complete":
        return {
            "metric": "evidence_path_dag",
            "status": "not_applicable",
            "score": None,
            "reason": f"trace_not_complete:{envelope.completeness_reason}",
        }
    from .agent_dag import build_evidence_path_metric, dag_metadata

    metric = build_evidence_path_metric(model=model, threshold=None)
    test_case.metadata = {**(test_case.metadata or {}), **dag_metadata(diagnostics)}
    try:
        metric.measure(test_case)
        return {
            "metric": "evidence_path_dag",
            "status": "scored",
            "score": float(metric.score),
            "reason": getattr(metric, "reason", None),
        }
    except Exception as exc:
        return {
            "metric": "evidence_path_dag",
            "status": "error",
            "score": None,
            "reason": "DAG metric failed",
            "details": {"error_type": type(exc).__name__, "error": str(exc)},
        }


def evaluate_run(
    run_dir: str | Path,
    output_dir: str | Path,
    *,
    judge: bool = False,
    metrics: Iterable[str] | None = None,
    dag: bool = False,
    model: object | None = None,
) -> dict[str, Any]:
    """Evaluate one existing run without mutating it."""
    run = Path(run_dir).resolve()
    output = Path(output_dir).resolve()
    if not (run / "outputs.jsonl").is_file():
        raise FileNotFoundError(f"Missing run outputs: {run / 'outputs.jsonl'}")
    if output == run or output.is_relative_to(run):
        raise ValueError("Agent output directory must be separate from the immutable run")
    if output.exists():
        raise FileExistsError(f"Agent output directory already exists: {output}")
    if dag and not judge:
        raise ValueError("--dag requires --judge")
    selected_metrics = tuple(metrics or ())
    if judge:
        from .agent_deepeval import available_agent_metrics

        allowed = set(available_agent_metrics())
        unknown = sorted(set(selected_metrics) - allowed)
        if unknown:
            raise ValueError("Unknown Agent metric(s): " + ", ".join(unknown))
        if not selected_metrics:
            selected_metrics = available_agent_metrics()

    output.mkdir(parents=True, exist_ok=False)
    traces, diagnostics_rows, scores = [], [], []
    comparison_rows = []
    for row in read_rows(run / "outputs.jsonl"):
        case, observed = _record_parts(row)
        envelope = _envelope(row, case, observed)
        diagnostics = diagnose_trace(envelope)
        traces.append({"case_id": envelope.case_id, "mode": envelope.mode, "trace": envelope.to_dict()})
        diagnostics_row = {
            "case_id": envelope.case_id,
            "sample_id": row.get("sample_id"),
            "suite": row.get("suite") or case.get("suite"),
            "task": row.get("task") or case.get("task"),
            "mode": envelope.mode,
            "diagnostics": diagnostics,
        }
        diagnostics_rows.append(diagnostics_row)
        comparison_rows.append({"case_id": envelope.case_id, "mode": envelope.mode, "diagnostics": diagnostics})
        if not judge:
            continue
        from .agent_deepeval import build_test_case, evaluate_trajectory

        test_case = build_test_case(case, observed, envelope, diagnostics)
        for score in evaluate_trajectory(test_case, envelope, metrics=selected_metrics, model=model):
            scores.append({
                "case_id": envelope.case_id,
                "sample_id": row.get("sample_id"),
                "suite": row.get("suite") or case.get("suite"),
                "task": row.get("task") or case.get("task"),
                "mode": envelope.mode,
                **score,
            })
        if dag:
            scores.append({
                "case_id": envelope.case_id,
                "sample_id": row.get("sample_id"),
                "suite": row.get("suite") or case.get("suite"),
                "task": row.get("task") or case.get("task"),
                "mode": envelope.mode,
                **_score_dag(test_case, envelope, diagnostics, model=model),
            })

    save_jsonl(output / "agent-traces.jsonl", traces)
    save_jsonl(output / "agent-diagnostics.jsonl", diagnostics_rows)
    save_jsonl(output / "agent-scores.jsonl", scores)
    completeness_counts = Counter(row["trace"]["completeness"] for row in traces)
    score_counts = Counter(row["status"] for row in scores)
    summary = {
        "format": "sn-agent-evaluation-v1",
        "run_dir": str(run),
        "record_count": len(traces),
        "judge_enabled": judge,
        "dag_enabled": dag,
        "metrics": list(selected_metrics),
        "completeness_counts": {key: completeness_counts.get(key, 0) for key in ("none", "partial", "complete")},
        "score_status_counts": {key: score_counts.get(key, 0) for key in ("scored", "not_applicable", "error")},
        "mode_comparison": compare_modes(comparison_rows),
        "release_gate": False,
        "notes": [
            "Agent and DAG scores are advisory and are not merged into benchmark primary scores.",
            "Incomplete traces are not scored by trajectory metrics.",
        ],
    }
    save_json(output / "agent-summary.json", summary)
    return summary
