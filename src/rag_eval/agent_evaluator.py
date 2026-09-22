"""Read-only deterministic diagnostics for saved Agent execution records."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from .agent_diagnostics import compare_modes, diagnose_execution, diagnose_trace
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
        **({'execution_trace': output['execution_trace']} if 'execution_trace' in output else {}),
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


def _write_report(output: Path, summary: Mapping[str, Any]) -> None:
    def cell(value: object) -> str:
        return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")

    lines = [
        "# Agent 执行阶段报告", "",
        f"记录数：{summary['record_count']}；只读确定性诊断。", "",
        "按已保存的请求、意图预览和响应定位阶段；未知保留 unknown，不推断澄清是否合理。",
        "缺少 reasoning trace 不等于未进入 Ask，也不等于没有答案。阶段统计不是质量分数。", "",
    ]
    for title, counts in (("输出状态", summary["output_status_counts"]),
                          ("可观察终止阶段", summary["execution_stage_counts"]),
                          ("澄清记录中的理由（同题可有多个）", summary["clarification_reason_counts"])):
        lines.extend([f"## {title}", "", "| 项目 | 记录数 |", "| --- | ---: |"])
        lines.extend(f"| {cell(key)} | {value} |" for key, value in counts.items())
        if not counts:
            lines.append("| 未记录 | — |")
        lines.append("")
    lines.extend(["逐题实际请求、原题、澄清内容及阶段证据见 [agent-diagnostics.jsonl](agent-diagnostics.jsonl)。",
        "完整性见 [agent-traces.jsonl](agent-traces.jsonl)。保存的执行轨迹按原样报告；旧摘要不会被补造为完整轨迹。",
        "complete 仅表示已声明的同步 native Ask 范围被完整记录，不代表回答正确；澄清路径也可完整。", ""])
    (output / "agent-report.md").write_text("\n".join(lines), encoding="utf-8")


def evaluate_run(
    run_dir: str | Path,
    output_dir: str | Path,
) -> dict[str, Any]:
    """Build historical execution diagnostics without mutating the saved run."""
    run = Path(run_dir).resolve()
    output = Path(output_dir).resolve()
    if not (run / "outputs.jsonl").is_file():
        raise FileNotFoundError(f"Missing run outputs: {run / 'outputs.jsonl'}")
    if output == run or output.is_relative_to(run):
        raise ValueError("Agent output directory must be separate from the immutable run")
    if output.exists():
        raise FileExistsError(f"Agent output directory already exists: {output}")
    output.mkdir(parents=True, exist_ok=False)
    traces, diagnostics_rows = [], []
    comparison_rows = []
    for row in read_rows(run / "outputs.jsonl"):
        case, observed = _record_parts(row)
        envelope = _envelope(row, case, observed)
        diagnostics = diagnose_trace(envelope)
        execution = diagnose_execution(observed, envelope)
        traces.append({"case_id": envelope.case_id, "mode": envelope.mode, "trace": envelope.to_dict()})
        diagnostics_row = {
            "case_id": envelope.case_id,
            "sample_id": row.get("sample_id"),
            "suite": row.get("suite") or case.get("suite"),
            "task": row.get("task") or case.get("task"),
            "mode": envelope.mode,
            "diagnostics": diagnostics,
            "execution": execution,
        }
        diagnostics_rows.append(diagnostics_row)
        comparison_rows.append({"case_id": envelope.case_id, "mode": envelope.mode, "diagnostics": diagnostics})
    save_jsonl(output / "agent-traces.jsonl", traces)
    save_jsonl(output / "agent-diagnostics.jsonl", diagnostics_rows)
    completeness_counts = Counter(row["trace"]["completeness"] for row in traces)
    status_counts = Counter(row["diagnostics"]["status"] for row in diagnostics_rows)
    stage_counts = Counter(row["execution"]["termination_phase"] for row in diagnostics_rows)
    clarification_reasons = Counter(reason for row in diagnostics_rows
                                    if row["diagnostics"]["status"] == "clarification"
                                    for reason in row["execution"]["clarification_reasons"])
    summary = {
        "format": "sn-agent-diagnostics-v1",
        "run_dir": str(run),
        "record_count": len(traces),
        "completeness_counts": {key: completeness_counts.get(key, 0) for key in ("none", "partial", "complete")},
        "output_status_counts": dict(sorted(status_counts.items())),
        "execution_stage_counts": dict(sorted(stage_counts.items())),
        "clarification_reason_counts": dict(sorted(clarification_reasons.items())),
        "mode_comparison": compare_modes(comparison_rows),
        "release_gate": False,
        "notes": [
            "This historical report contains deterministic diagnostics only.",
            "No Agent judge or DAG score is produced by this command.",
        ],
    }
    save_json(output / "agent-summary.json", summary)
    _write_report(output, summary)
    return summary
