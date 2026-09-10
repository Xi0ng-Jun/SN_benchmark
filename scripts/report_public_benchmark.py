#!/usr/bin/env python3
"""Create auditable summaries, blind-review templates, and optional comparisons."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rag_eval.artifacts import save_json, save_jsonl
from rag_eval.comparison import compare_runs, summarize_run


def load_jsonl(path):
    with Path(path).open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def blind_items(outputs):
    selected = [row for row in outputs if row.get("calibration") is True and row.get("repeat", 0) == 0]
    selected.sort(key=lambda row: hashlib.sha256(f"{row['dataset']}:{row['id']}:{row['mode']}".encode()).hexdigest())
    return selected


def write_review(run, outputs):
    selected = blind_items(outputs)
    labels = []
    lines = ["# Blind human review", "", "Calibration status: pending manual review.",
             "Assign labels before consulting judge reasons. Allowed labels: meets, partial, does_not_meet, cannot_determine.", ""]
    for row in selected:
        identity = f"{row['dataset']}:{row['id']}:{row['mode']}"
        blind_id = "review-" + hashlib.sha256(identity.encode()).hexdigest()[:16]
        lines += [f"## {blind_id}", "", f"Question: {row.get('question', '')}", "",
                  f"Official references: {json.dumps(row.get('references', []), ensure_ascii=False)}", "",
                  f"Answer: {row.get('answer', '')}", "",
                  f"Actual context: {json.dumps(row.get('retrieval_context', []), ensure_ascii=False)}", "",
                  f"Citations: {json.dumps((row.get('response') or {}).get('citations', []), ensure_ascii=False)}", "",
                  f"All synthesis captures: {json.dumps(row.get('captures', []), ensure_ascii=False)}", "",
                  "Correctness: [ ]  Faithfulness: [ ]  Citation support: [ ]", "", "Notes:", ""]
        labels.append({"blind_id": blind_id, "correctness": None, "faithfulness": None,
                       "citation_support": None, "notes": None, "reviewer": None,
                       "status": "pending", "dataset": row.get("dataset"), "id": row.get("id"),
                       "mode": row.get("mode")})
    (run / "review.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    labels_path = run / "human-labels.jsonl"
    existing = {row["blind_id"]: row for row in load_jsonl(labels_path)} if labels_path.exists() else {}
    merged = [existing.get(row["blind_id"], row) for row in labels]
    selected_ids = {row["blind_id"] for row in labels}
    merged.extend(row for blind_id, row in existing.items() if blind_id not in selected_ids)
    save_jsonl(labels_path, merged)
    return len(selected)


def repeat_summaries(run, outputs):
    primary = {(row["dataset"], row["id"], row["mode"]): row for row in outputs if row.get("repeat", 0) == 0}
    product_path = run / "product-repeats.jsonl"
    product = load_jsonl(product_path) if product_path.exists() else []
    product_rows = []
    for row in product:
        original = primary.get((row.get("dataset"), row.get("id"), row.get("mode")))
        if original is None:
            continue
        both_success = (original.get("status") == row.get("status") == "success"
                        and bool(str(original.get("answer", "")).strip())
                        and bool(str(row.get("answer", "")).strip()))
        contexts_comparable = bool(original.get("context_supported") and row.get("context_supported"))
        product_rows.append({"dataset": row["dataset"], "id": row["id"], "mode": row["mode"],
                             "repeat": row.get("repeat"),
                             "both_success": both_success, "answer_comparable": both_success,
                             "answer_exact_match": original.get("answer") == row.get("answer") if both_success else None,
                             "context_comparable": contexts_comparable,
                             "context_exact_match": (original.get("retrieval_context") == row.get("retrieval_context")
                                                     if contexts_comparable else None)})
    scores_path = run / "scores.jsonl"
    scores = load_jsonl(scores_path) if scores_path.exists() else []
    main_scores = {(row["dataset"], row["id"], row["mode"], row["metric"]): row
                   for row in scores if row.get("repeat", 0) == 0}
    judge_rows = []
    for row in scores:
        if row.get("repeat", 0) <= 0:
            continue
        original = main_scores.get((row["dataset"], row["id"], row["mode"], row["metric"]))
        comparable = bool(original and original.get("status") == row.get("status") == "valid"
                          and original.get("score") is not None and row.get("score") is not None)
        judge_rows.append({"dataset": row["dataset"], "id": row["id"], "mode": row["mode"],
                           "metric": row["metric"], "repeat": row.get("repeat"), "comparable": comparable,
                           "score_delta": float(row["score"]) - float(original["score"]) if comparable else None})
    product_groups = []
    for dataset, mode in sorted({(row["dataset"], row["mode"]) for row in product_rows}):
        selected = [row for row in product_rows if row["dataset"] == dataset and row["mode"] == mode]
        answers = [row for row in selected if row["answer_comparable"]]
        contexts = [row for row in selected if row["context_comparable"]]
        product_groups.append({"dataset": dataset, "mode": mode, "attempted": len(selected),
                               "answer_comparable": len(answers),
                               "answer_exact_matches": sum(row["answer_exact_match"] is True for row in answers),
                               "context_comparable": len(contexts),
                               "context_exact_matches": sum(row["context_exact_match"] is True for row in contexts)})
    judge_groups = []
    for dataset, mode, metric in sorted({(row["dataset"], row["mode"], row["metric"]) for row in judge_rows}):
        selected = [row for row in judge_rows if row["dataset"] == dataset and row["mode"] == mode
                    and row["metric"] == metric]
        deltas = [row["score_delta"] for row in selected if row["comparable"]]
        judge_groups.append({"dataset": dataset, "mode": mode, "metric": metric,
                             "attempted": len(selected), "comparable": len(deltas),
                             "mean_delta": sum(deltas) / len(deltas) if deltas else None,
                             "mean_absolute_delta": sum(abs(value) for value in deltas) / len(deltas) if deltas else None})
    return {"product_repeat_agreement": {"attempted": len(product_rows), "groups": product_groups, "rows": product_rows},
            "judge_repeat_variability": {"attempted": len(judge_rows), "groups": judge_groups, "rows": judge_rows}}


def render_markdown(summary, comparison=None, artifact_references=None):
    lines = ["# Public benchmark evaluation", "", "Calibration: **pending manual review**. No human labels or release thresholds are inferred.", ""]
    for name, group in summary["groups"].items():
        lines += [f"## {name}", "", f"Outputs: {group['outputs']}/{group['planned_outputs']}; missing: {group['missing_outputs']}; statuses: `{json.dumps(group['output_status'], sort_keys=True)}`.", "",
                  "| Metric | Attempted | Valid | Error | Skipped | Missing | Mean |", "|---|---:|---:|---:|---:|---:|---:|"]
        for metric, value in group["metrics"].items():
            score = "n/a" if value["mean"] is None else f"{value['mean']:.4f}"
            lines.append(f"| {metric} | {value['attempted']} | {value['valid']} | {value['error']} | {value['skipped']} | {value['missing']} | {score} |")
        latency = group["latency_seconds"]
        lines += ["", f"Latency seconds: p50={latency['p50']}, p90={latency['p90']}, p95={latency['p95']}.", ""]
        deterministic = group["deterministic"]
        lines += [f"Evidence hit: {deterministic['evidence_hit_rate']} across {deterministic['evidence_hit_valid']} applicable outputs. ",
                  f"Citation validity: {deterministic['citation_valid']}/{deterministic['citation_total']} ({deterministic['citation_valid_rate']}).", ""]
    lines += ["## Answer types", "", "| Dataset / mode / type | Outputs | Missing | Metric means |",
              "|---|---:|---:|---|"]
    for name, group in summary.get("answer_type_groups", {}).items():
        lines.append(f"| {name.replace('|', ' / ')} | {group['outputs']}/{group['planned_outputs']} | {group['missing_outputs']} | {json.dumps(group['metric_means'], sort_keys=True)} |")
    lines.append("")
    lines += ["## Chunk / reasoning pairs", ""]
    for bucket in summary.get("paired_modes", []):
        lines += [f"### {bucket['dataset']} / {bucket['split']}", "",
                  f"Both outputs: {bucket['both_outputs']}/{bucket['questions']}; missing chunk: {bucket['missing_chunk']}; missing reasoning: {bucket['missing_reasoning']}.", "",
                  "| Metric | Valid both | Missing | Error/skipped | Reasoning - chunk | 95% grouped bootstrap interval |",
                  "|---|---:|---:|---:|---:|---|"]
        for metric, value in bucket["metrics"].items():
            delta = "n/a" if value["reasoning_minus_chunk"] is None else f"{value['reasoning_minus_chunk']:.4f}"
            interval = "insufficient comparable groups" if value["interval"] is None else json.dumps(value["interval"])
            lines.append(f"| {metric} | {value['valid_both']} | {value['excluded_missing']} | {value['excluded_error_or_skipped']} | {delta} | {interval} |")
        latency, tokens = bucket["latency_seconds"], bucket["total_tokens"]
        lines += ["", f"Paired latency difference (reasoning - chunk): {latency['reasoning_minus_chunk_mean']} seconds across {latency['valid_both']} pairs.",
                  f"Paired logged-token difference: {tokens['reasoning_minus_chunk_mean']} across {tokens['valid_both']} pairs; unavailable usage is excluded.", ""]
    lines += ["## Observed usage", "", "Token totals cover provider events observed by the chat interaction logger. Embedding and rerank token usage is unavailable in that channel; scheduler call counts are reported separately in operational-summary.json and must not be added to raw chat calls.", "",
              "| Dataset / mode | Outputs observed | Logged calls with usage / calls | Prompt tokens | Completion tokens | Total tokens |",
              "|---|---:|---:|---:|---:|---:|"]
    for name, value in summary.get("usage", {}).items():
        lines.append(f"| {name.replace('|', ' / ')} | {value['outputs_with_observation']}/{value['outputs']} | {value['calls_with_usage']}/{value['call_count']} | {value['prompt_tokens']} | {value['completion_tokens']} | {value['total_tokens']} |")
    lines += ["", "## SQuAD annotated-span support", "",
              "This checks whether an original annotated SQuAD answer span appears in mapped synthesis context. It is not a complete evidence-recall claim.", "",
              "| Dataset / mode | Applicable | Supported | Rate |", "|---|---:|---:|---:|"]
    for name, value in summary.get("squad_span_support", {}).items():
        lines.append(f"| {name.replace('|', ' / ')} | {value['applicable']} | {value['supported']} | {value['rate']} |")
    lines.append("")
    if comparison:
        lines += ["## Paired comparison", "", "The first table is an auxiliary dataset-level rollup across modes and splits; use the following bucket table for conclusions.", f"Comparable primary outputs: {comparison['samples']}.", "",
                  "| Dataset | Metric | Pairs | Mean delta | 95% grouped bootstrap interval | Missing current | Missing baseline |",
                  "|---|---|---:|---:|---|---:|---:|"]
        for row in comparison["metrics"]:
            delta = "n/a" if row["mean_delta"] is None else f"{row['mean_delta']:.4f}"
            interval = "insufficient comparable groups" if row["interval"] is None else f"[{row['interval'][0]:.4f}, {row['interval'][1]:.4f}]"
            lines.append(f"| {row['dataset']} | {row['metric']} | {row['comparable']} | {delta} | {interval} | {row['missing_current']} | {row['missing_baseline']} |")
        lines.append("")
        lines += ["### Dataset / mode / split buckets", "",
                  "| Dataset | Mode | Split | Metric | Pairs | Mean delta | Interval |",
                  "|---|---|---|---|---:|---:|---|"]
        for row in comparison.get("buckets", []):
            delta = "n/a" if row["mean_delta"] is None else f"{row['mean_delta']:.4f}"
            interval = "insufficient groups" if row["interval"] is None else json.dumps(row["interval"])
            lines.append(f"| {row['dataset']} | {row['mode']} | {row['split']} | {row['metric']} | {row['comparable']} | {delta} | {interval} |")
        lines.append("")
    product = summary.get("product_repeat_agreement", {})
    judge = summary.get("judge_repeat_variability", {})
    product_rows = product.get("rows", [])
    answer_rows = [row for row in product_rows if row["answer_comparable"]]
    context_rows = [row for row in product_rows if row["context_comparable"]]
    answer_matches = sum(row["answer_exact_match"] is True for row in answer_rows)
    context_matches = sum(row["context_exact_match"] is True for row in context_rows)
    judge_rows = [row for row in judge.get("rows", []) if row["comparable"]]
    lines += ["## Repeatability observations", "",
              f"Product reruns: {len(product_rows)} attempted pairs; answer comparable {len(answer_rows)}, exact {answer_matches}; context comparable {len(context_rows)}, exact {context_matches}.",
              f"Judge rescoring: {judge.get('attempted', len(judge.get('rows', [])))} attempted metric pairs; {len(judge_rows)} comparable. Per-item deltas remain in summary.json and are not product-generation stability.", "",
              "| Dataset | Mode | Metric | Attempted | Comparable | Mean delta | Mean absolute delta |",
              "|---|---|---|---:|---:|---:|---:|"]
    for row in judge.get("groups", []):
        lines.append(f"| {row['dataset']} | {row['mode']} | {row['metric']} | {row['attempted']} | {row['comparable']} | {row['mean_delta']} | {row['mean_absolute_delta']} |")
    lines.append("")
    if artifact_references:
        lines += ["## Diagnostics and provenance", ""]
        lines += [f"- `{name}`" for name in artifact_references]
        lines.append("")
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--compare", type=Path)
    args = parser.parse_args(argv)
    run = args.run_dir.resolve()
    summary = summarize_run(run)
    comparison = compare_runs(run, args.compare.resolve()) if args.compare else None
    if comparison is not None:
        summary["comparison"] = comparison
    outputs = load_jsonl(run / "outputs.jsonl")
    summary.update(repeat_summaries(run, outputs))
    summary["blind_review_items"] = write_review(run, outputs)
    references = [path.name for path in sorted(run.glob('*.json'))
                  if path.name == 'failure-diagnostics.json' or 'provenance' in path.name]
    for folder in ('preask-provenance', 'persistence-correction', 'judge-identity-finalization'):
        references.extend(str(path.relative_to(run)) for path in sorted((run / folder).glob('*.json')))
    summary["artifact_references"] = references
    save_json(run / "summary.json", summary)
    (run / "report.md").write_text(render_markdown(summary, comparison, references), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
