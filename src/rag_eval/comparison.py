"""Auditable summaries and paired comparisons for public benchmark runs."""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import random
from statistics import mean
from typing import Any

from .quality_metrics import PRIMARY_METRICS


def _load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if line.strip():
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError(f"{path}:{number} is not a JSON object")
                rows.append(value)
    return rows


def _primary_outputs(run: Path) -> list[dict[str, Any]]:
    return [row for row in _load_jsonl(run / "outputs.jsonl") if row.get("repeat", 0) == 0]


def _key(row: dict[str, Any]) -> tuple[str, str, str]:
    return str(row["dataset"]), str(row["id"]), str(row["mode"])


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _within_run_observations(outputs, scores):
    output_map = {_key(row): row for row in outputs}
    score_map = {(*_key(row), str(row["metric"])): row for row in scores}
    metric_names = sorted({str(row["metric"]) for row in scores})
    paired = []
    buckets = sorted({(str(row["dataset"]), str(row.get("split", "unknown"))) for row in outputs})
    for dataset, split in buckets:
        identifiers = sorted({str(row["id"]) for row in outputs if str(row["dataset"]) == dataset
                              and str(row.get("split", "unknown")) == split})
        chunk_outputs = {identifier: output_map.get((dataset, identifier, "chunk")) for identifier in identifiers}
        reasoning_outputs = {identifier: output_map.get((dataset, identifier, "reasoning")) for identifier in identifiers}
        metrics = {}
        for metric in metric_names:
            applicable_identifiers = identifiers
            if metric.startswith("Contextual "):
                applicable_identifiers = [identifier for identifier in identifiers
                                          if bool((chunk_outputs[identifier] or reasoning_outputs[identifier]).get("diagnostic"))]
            deltas, excluded_nonvalid, excluded_missing = [], 0, 0
            for identifier in applicable_identifiers:
                left = score_map.get((dataset, identifier, "chunk", metric))
                right = score_map.get((dataset, identifier, "reasoning", metric))
                if left is None or right is None:
                    excluded_missing += 1
                elif (left.get("status") != "valid" or right.get("status") != "valid"
                      or left.get("score") is None or right.get("score") is None):
                    excluded_nonvalid += 1
                else:
                    group = str((chunk_outputs[identifier] or reasoning_outputs[identifier]).get("group", identifier))
                    deltas.append((group, float(right["score"]) - float(left["score"])))
            seed = int(hashlib.sha256(f"within:{dataset}:{split}:{metric}".encode()).hexdigest()[:8], 16)
            metrics[metric] = {"applicable": len(applicable_identifiers), "valid_both": len(deltas), "excluded_missing": excluded_missing,
                               "excluded_error_or_skipped": excluded_nonvalid,
                               "reasoning_minus_chunk": mean(value for _, value in deltas) if deltas else None,
                               "interval": _bootstrap_interval(deltas, seed)}
        latency_deltas, token_deltas = [], []
        for identifier in identifiers:
            chunk, reasoning = chunk_outputs[identifier], reasoning_outputs[identifier]
            if chunk and reasoning and chunk.get("latency_seconds") is not None and reasoning.get("latency_seconds") is not None:
                latency_deltas.append(float(reasoning["latency_seconds"]) - float(chunk["latency_seconds"]))
            chunk_tokens = ((chunk or {}).get("usage") or {}).get("total_tokens")
            reasoning_tokens = ((reasoning or {}).get("usage") or {}).get("total_tokens")
            if chunk_tokens is not None and reasoning_tokens is not None:
                token_deltas.append(float(reasoning_tokens) - float(chunk_tokens))
        paired.append({"dataset": dataset, "split": split, "questions": len(identifiers),
                       "both_outputs": sum(bool(chunk_outputs[item] and reasoning_outputs[item]) for item in identifiers),
                       "missing_chunk": sum(chunk_outputs[item] is None for item in identifiers),
                       "missing_reasoning": sum(reasoning_outputs[item] is None for item in identifiers),
                       "metrics": metrics,
                       "latency_seconds": {"valid_both": len(latency_deltas),
                                           "reasoning_minus_chunk_mean": mean(latency_deltas) if latency_deltas else None},
                       "total_tokens": {"valid_both": len(token_deltas),
                                        "reasoning_minus_chunk_mean": mean(token_deltas) if token_deltas else None}})
    usage = {}
    for dataset, mode in sorted({(str(row["dataset"]), str(row["mode"])) for row in outputs}):
        selected = [row for row in outputs if str(row["dataset"]) == dataset and str(row["mode"]) == mode]
        observed = [row.get("usage") for row in selected if isinstance(row.get("usage"), dict)]
        usage[f"{dataset}|{mode}"] = {
            "outputs": len(selected), "outputs_with_observation": len(observed),
            "call_count": sum(int(value.get("call_count", 0) or 0) for value in observed),
            "calls_with_usage": sum(int(value.get("calls_with_usage", 0) or 0) for value in observed),
            **{field: (sum(float(value[field]) for value in observed if value.get(field) is not None)
                       if any(value.get(field) is not None for value in observed) else None)
               for field in ("prompt_tokens", "completion_tokens", "total_tokens")},
        }
    span_support = {}
    for dataset, mode in sorted({(str(row["dataset"]), str(row["mode"])) for row in outputs if row.get("dataset") == "squad"}):
        observations = [row.get("squad_span_evidence") or {} for row in outputs
                        if str(row["dataset"]) == dataset and str(row["mode"]) == mode]
        applicable = [value for value in observations if value.get("status") in ("supported", "unsupported")]
        supported = sum(value.get("span_supported") is True for value in applicable)
        span_support[f"{dataset}|{mode}"] = {"applicable": len(applicable), "supported": supported,
                                             "rate": supported / len(applicable) if applicable else None}
    return paired, usage, span_support


def summarize_run(run_dir: str | Path) -> dict[str, Any]:
    run = Path(run_dir)
    manifest = _load_json(run / "manifest.json")
    outputs = _primary_outputs(run)
    scores = [row for row in _load_jsonl(run / "scores.jsonl") if row.get("repeat", 0) == 0]
    grouped_outputs: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in outputs:
        grouped_outputs[(str(row["dataset"]), str(row["mode"]), str(row.get("split", "unknown")))].append(row)
    questions_path = run / "questions.jsonl"
    expected_by_group: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    if questions_path.exists():
        modes = manifest.get("comparison_identity", {}).get("modes") or ["chunk", "reasoning"]
        questions = _load_jsonl(questions_path)
        planned_rows = manifest.get("planned_output_keys")
        planned = ({(str(row["dataset"]), str(row["id"]), str(row["mode"])) for row in planned_rows}
                   if isinstance(planned_rows, list) else None)
        for question in questions:
            for mode in modes:
                if planned is not None and (str(question["dataset"]), str(question["id"]), str(mode)) not in planned:
                    continue
                expected_by_group[(str(question["dataset"]), str(mode), str(question.get("split", "unknown")))].append(question)
    else:
        expected_by_group = grouped_outputs
    grouped_scores: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    output_by_key = {_key(row): row for row in outputs}
    for row in scores:
        output = output_by_key.get(_key(row))
        if output is not None:
            grouped_scores[(str(row["dataset"]), str(row["mode"]), str(output.get("split", "unknown")))].append(row)

    groups: dict[str, Any] = {}
    for group_key in sorted(set(grouped_outputs) | set(expected_by_group)):
        group_outputs = grouped_outputs.get(group_key, [])
        expected_outputs = expected_by_group.get(group_key, group_outputs)
        label = "|".join(group_key)
        expected_keys = {(str(row["dataset"]), str(row["id"]), group_key[1]) for row in expected_outputs}
        metric_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in grouped_scores.get(group_key, []):
            metric_rows[str(row["metric"])].append(row)
        metrics = {}
        expected_metrics = set(PRIMARY_METRICS) | set(metric_rows)
        if any(row.get("diagnostic") for row in expected_outputs):
            expected_metrics |= {"Contextual Recall", "Contextual Precision", "Contextual Relevancy"}
        for metric in sorted(expected_metrics):
            rows = metric_rows.get(metric, [])
            statuses = Counter(str(row.get("status", "error")) for row in rows)
            valid = [float(row["score"]) for row in rows if row.get("status") == "valid" and row.get("score") is not None]
            represented = {_key(row) for row in rows}
            applicable_keys = expected_keys
            if metric.startswith("Contextual "):
                applicable_keys = {(str(row["dataset"]), str(row["id"]), group_key[1])
                                   for row in expected_outputs if row.get("diagnostic")}
            metrics[metric] = {
                "attempted": len(rows), "valid": len(valid), "error": statuses["error"],
                "skipped": statuses["skipped"], "missing": len(applicable_keys - represented),
                "mean": mean(valid) if valid else None,
            }
        latencies = [float(row["latency_seconds"]) for row in group_outputs if row.get("latency_seconds") is not None]
        deterministic = [row.get("deterministic") or {} for row in group_outputs]
        hits = [bool(value["evidence_hit"]) for value in deterministic if value.get("evidence_hit") is not None]
        citation_valid = sum(int(value.get("citation_valid", value.get("citation_valid_count", 0)) or 0) for value in deterministic)
        citation_total = sum(int(value.get("citation_total", value.get("citation_count", 0)) or 0) for value in deterministic)
        groups[label] = {
            "planned_outputs": len(expected_outputs), "outputs": len(group_outputs),
            "missing_outputs": len(expected_keys - {_key(row) for row in group_outputs}),
            "output_status": dict(Counter(str(row.get("status", "unknown")) for row in group_outputs)),
            "latency_seconds": {"p50": _quantile(latencies, .5), "p90": _quantile(latencies, .9), "p95": _quantile(latencies, .95)},
            "deterministic": {
                "evidence_hit_valid": len(hits),
                "evidence_hit_rate": mean(hits) if hits else None,
                "citation_valid": citation_valid,
                "citation_total": citation_total,
                "citation_valid_rate": citation_valid / citation_total if citation_total else None,
            },
            "metrics": metrics,
        }
    answer_type_groups = {}
    for dataset, mode, answer_type in sorted({(str(row["dataset"]), group_key[1], str(row.get("answer_type", "unknown")))
                                               for group_key, values in expected_by_group.items() for row in values}):
        expected_ids = {str(row["id"]) for group_key, values in expected_by_group.items() if group_key[0] == dataset and group_key[1] == mode
                        for row in values if str(row.get("answer_type", "unknown")) == answer_type}
        actual = [row for row in outputs if str(row["dataset"]) == dataset and str(row["mode"]) == mode
                  and str(row.get("answer_type", "unknown")) == answer_type]
        metric_values = defaultdict(list)
        for row in scores:
            if str(row["dataset"]) == dataset and str(row["mode"]) == mode and str(row["id"]) in expected_ids:
                if row.get("status") == "valid" and row.get("score") is not None:
                    metric_values[str(row["metric"])].append(float(row["score"]))
        answer_type_groups[f"{dataset}|{mode}|{answer_type}"] = {
            "planned_outputs": len(expected_ids), "outputs": len(actual),
            "missing_outputs": len(expected_ids - {str(row["id"]) for row in actual}),
            "output_status": dict(Counter(str(row.get("status", "unknown")) for row in actual)),
            "metric_means": {name: mean(values) for name, values in sorted(metric_values.items())},
        }
    paired, usage, span_support = _within_run_observations(outputs, scores)
    return {"comparison_identity": manifest.get("comparison_identity"), "groups": groups,
            "answer_type_groups": answer_type_groups, "paired_modes": paired, "usage": usage,
            "squad_span_support": span_support, "calibration_status": "pending_manual_review"}


def _score_map(run: Path) -> dict[tuple[str, str, str, str], dict[str, Any]]:
    rows = [row for row in _load_jsonl(run / "scores.jsonl") if row.get("repeat", 0) == 0]
    result = {}
    for row in rows:
        key = (*_key(row), str(row["metric"]))
        if key in result:
            raise ValueError(f"duplicate primary score: {key}")
        result[key] = row
    return result


def _planned_keys(run: Path, manifest: dict[str, Any], actual: set[tuple[str, str, str]]) -> set[tuple[str, str, str]]:
    explicit = manifest.get("planned_output_keys")
    if isinstance(explicit, list):
        if not isinstance(manifest.get("planned_scope"), str):
            raise ValueError("planned_output_keys require explicit planned_scope")
        return {(str(row["dataset"]), str(row["id"]), str(row["mode"])) for row in explicit}
    questions = run / "questions.jsonl"
    if questions.exists() and manifest.get("planned_output_count") is not None:
        modes = manifest.get("comparison_identity", {}).get("modes") or ["chunk", "reasoning"]
        inferred = {(str(row["dataset"]), str(row["id"]), str(mode))
                    for row in _load_jsonl(questions) for mode in modes}
        if len(inferred) != int(manifest["planned_output_count"]):
            raise ValueError("legacy planned_output_count does not match frozen questions")
        return inferred
    return actual


def _bootstrap_interval(pairs: list[tuple[str, float]], seed: int, iterations: int = 2000) -> list[float] | None:
    groups: dict[str, list[float]] = defaultdict(list)
    for group, delta in pairs:
        groups[group].append(delta)
    names = sorted(groups)
    if len(names) < 5 or len(pairs) < 10:
        return None
    rng = random.Random(seed)
    estimates = []
    for _ in range(iterations):
        sampled = [rng.choice(names) for _ in names]
        estimates.append(mean(delta for name in sampled for delta in groups[name]))
    estimates.sort()
    return [_quantile(estimates, .025), _quantile(estimates, .975)]


def compare_runs(current_dir: str | Path, baseline_dir: str | Path) -> dict[str, Any]:
    current, baseline = Path(current_dir), Path(baseline_dir)
    current_manifest, baseline_manifest = _load_json(current / "manifest.json"), _load_json(baseline / "manifest.json")
    current_identity = current_manifest.get("comparison_identity")
    baseline_identity = baseline_manifest.get("comparison_identity")
    if not isinstance(current_identity, dict) or current_identity != baseline_identity:
        raise ValueError("comparison_identity mismatch")
    current_outputs = {_key(row): row for row in _primary_outputs(current)}
    baseline_outputs = {_key(row): row for row in _primary_outputs(baseline)}
    if (not isinstance(current_manifest.get("planned_output_keys"), list)
            and not (current / "questions.jsonl").exists()
            and current_manifest.get("planned_output_count") is None):
        planned = set(baseline_outputs)
    else:
        planned = _planned_keys(current, current_manifest, set(current_outputs))
    lost = sorted(planned - set(current_outputs))
    added = sorted(set(current_outputs) - planned)
    baseline_missing = sorted(planned - set(baseline_outputs))
    if lost or added or baseline_missing:
        raise ValueError(f"primary sample loss/addition: lost={lost}, added={added}, baseline_missing={baseline_missing}")
    current_outputs = {key: current_outputs[key] for key in planned}
    baseline_outputs = {key: baseline_outputs[key] for key in planned}
    current_scores, baseline_scores = _score_map(current), _score_map(baseline)
    metric_names = sorted(set(PRIMARY_METRICS) | {key[3] for key in current_scores} | {key[3] for key in baseline_scores})
    rows = []
    sample_keys = sorted(current_outputs)
    for dataset in sorted({key[0] for key in sample_keys}):
        for metric in metric_names:
            comparable = []
            missing_current = missing_baseline = 0
            for sample_key in [key for key in sample_keys if key[0] == dataset]:
                key = (*sample_key, metric)
                left, right = current_scores.get(key), baseline_scores.get(key)
                if left is None:
                    missing_current += 1
                if right is None:
                    missing_baseline += 1
                if (left and right and left.get("status") == right.get("status") == "valid"
                        and left.get("score") is not None and right.get("score") is not None):
                    group = str(current_outputs[sample_key].get("group", sample_key[1]))
                    comparable.append((group, float(left["score"]) - float(right["score"])))
            seed = int(hashlib.sha256(f"{dataset}:{metric}".encode()).hexdigest()[:8], 16)
            rows.append({
                "dataset": dataset, "metric": metric, "comparable": len(comparable),
                "mean_delta": mean(delta for _, delta in comparable) if comparable else None,
                "missing_current": missing_current, "missing_baseline": missing_baseline,
                "interval": _bootstrap_interval(comparable, seed),
            })
    buckets = []
    bucket_names = sorted({(key[0], key[2], str(current_outputs[key].get("split", "unknown"))) for key in sample_keys})
    for dataset, mode, split in bucket_names:
        members = [key for key in sample_keys if key[0] == dataset and key[2] == mode
                   and str(current_outputs[key].get("split", "unknown")) == split]
        for metric in metric_names:
            deltas = []
            grouped_deltas = []
            for sample_key in members:
                left, right = current_scores.get((*sample_key, metric)), baseline_scores.get((*sample_key, metric))
                if (left and right and left.get("status") == right.get("status") == "valid"
                        and left.get("score") is not None and right.get("score") is not None):
                    delta = float(left["score"]) - float(right["score"])
                    deltas.append(delta)
                    grouped_deltas.append((str(current_outputs[sample_key].get("group", sample_key[1])), delta))
            buckets.append({"dataset": dataset, "mode": mode, "split": split, "metric": metric,
                            "comparable": len(deltas), "mean_delta": mean(deltas) if deltas else None,
                            "interval": _bootstrap_interval(grouped_deltas, int(hashlib.sha256(
                                f"{dataset}:{mode}:{split}:{metric}".encode()).hexdigest()[:8], 16))})
    paired_modes = []
    for dataset in sorted({key[0] for key in sample_keys}):
        identifiers = sorted({key[1] for key in sample_keys if key[0] == dataset})
        for metric in metric_names:
            deltas = []
            for identifier in identifiers:
                chunk = current_scores.get((dataset, identifier, "chunk", metric))
                reasoning = current_scores.get((dataset, identifier, "reasoning", metric))
                if (chunk and reasoning and chunk.get("status") == reasoning.get("status") == "valid"
                        and chunk.get("score") is not None and reasoning.get("score") is not None):
                    deltas.append(float(reasoning["score"]) - float(chunk["score"]))
            paired_modes.append({"dataset": dataset, "metric": metric, "comparable": len(deltas),
                                 "reasoning_minus_chunk": mean(deltas) if deltas else None})
    return {"comparison_identity": current_identity, "samples": len(sample_keys), "metrics": rows,
            "buckets": buckets, "paired_modes_current": paired_modes}
