"""Verdicts on whether a run measured the code or only the health of its services.

GEPA logs, the v4.1 calibration state and ``cli eval`` JSON are checked for the ways a broken
model endpoint or Wikipedia outlet fakes a baseline: no or partial evaluations, and a seed that
scores near zero on the validation/test split. Pure functions over text and dicts: no IO, no
network, no subprocesses, no environment.
"""
from __future__ import annotations

import ast
import re

MARKER = "Task completed with metrics:"
METRICS_LINE = re.compile(r"Task completed with metrics: (\{.*\}|None)")
INITIAL_SCORE = re.compile(r"Initial test score:\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)")


def _literal(text: str) -> dict | None:
    try:
        value = ast.literal_eval(text)
    except (SyntaxError, ValueError):
        return None
    return value if isinstance(value, dict) or value is None else None


def _closed(text: str) -> bool:
    """True once the braces opened so far are closed (quotes and escapes respected)."""
    depth, quote, escape = 0, "", False
    for ch in text:
        if escape:
            escape = False
        elif quote:
            if ch == "\\":
                escape = True
            elif ch == quote:
                quote = ""
        elif ch in "'\"":
            quote = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
    return depth <= 0


def parse_metrics(text: str) -> list[dict | None]:
    """The payload of every ``Task completed with metrics: ...`` line, in order.

    VeRO logs a Python dict literal (``<evaluator>: Task completed with metrics: {...}``) or
    ``None``. A dict may span lines; parsing failures and ``None`` become ``None``.
    """
    records: list[dict | None] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        m = METRICS_LINE.search(line)
        if m:
            records.append(_literal(m.group(1)))
            continue
        _, found, body = line.partition(MARKER)
        if not found:
            continue
        body = body.strip()
        if body.startswith("{"):
            while i < len(lines) and not _closed(body) and MARKER not in lines[i]:
                body += "\n" + lines[i]
                i += 1
        records.append(_literal(body))
    return records


def gepa_report(log_text: str) -> dict:
    """Initial/final test evaluations from a GEPA run: seed score, count and metrics records."""
    metrics = parse_metrics(log_text)
    scores = INITIAL_SCORE.findall(log_text)
    return dict(seed_score=float(scores[-1]) if scores else None, evaluations=len(metrics),
                initial=metrics[0] if metrics else None,
                final=metrics[-1] if len(metrics) > 1 else None)


def gepa_verdict(log_text: str, *, floor=0.25, max_error_rate=0.2, min_sample_fraction=0.9) -> dict:
    """Whether a GEPA log shows a healthy initial and final test evaluation."""
    report = gepa_report(log_text)
    seed_score, final, initial = report["seed_score"], report["final"], report["initial"]
    reasons: list[str] = []
    error_rate = None
    if report["evaluations"] == 0:
        reasons.append("no evaluation completed")
    if seed_score is None:
        reasons.append("no initial test score")
    elif seed_score < floor:
        reasons.append(f"seed scored {seed_score:.3f} on the test split (floor {floor:.2f}): "
                       "the services were not healthy")
    if report["evaluations"] < 2 or final is None:
        reasons.append("final test evaluation missing")
    if isinstance(final, dict):
        errors, samples = final.get("num_errors"), final.get("num_samples")
        if isinstance(errors, (int, float)) and isinstance(samples, (int, float)) and samples:
            error_rate = errors / samples
            if error_rate > max_error_rate:
                reasons.append(f"final evaluation: {errors} of {samples} samples errored")
        if isinstance(initial, dict):
            total = initial.get("num_samples")
            if (isinstance(total, (int, float)) and isinstance(samples, (int, float))
                    and samples < min_sample_fraction * total):
                reasons.append(f"final evaluation covered only {samples} of {total} samples")
    return dict(valid=not reasons, reasons=reasons, report=report, error_rate=error_rate,
                seed_score=seed_score)


def run_verdict(state: dict, *, floor=0.25) -> dict:
    """Whether a v4.1 state shows a seed that scored credibly on the validation split."""
    calibration = state.get("calibration") or {}
    validation = calibration.get("validation") or {}
    seed_val = validation.get("mean")
    rerun_var = calibration.get("rerun_var")
    if seed_val is None:
        return dict(valid=False, reasons=["no seed calibration"], seed_val=None, rerun_var=rerun_var)
    reasons = []
    if seed_val < floor:
        reasons.append(f"seed scored {seed_val:.3f} on validation (floor {floor:.2f}): "
                       "the services were not healthy")
    return dict(valid=not reasons, reasons=reasons, seed_val=seed_val, rerun_var=rerun_var)


def probe_verdict(payload: dict, *, floor=0.25, answered_floor=0.0) -> dict:
    """Whether ``cli eval`` JSON shows a complete, healthy probe of the services."""
    tasks, scored = payload.get("tasks"), payload.get("scored")
    mean_score, answered_rate = payload.get("mean_score"), payload.get("answered_rate")
    reasons = []
    if scored is not None and tasks is not None and scored < tasks:
        reasons.append(f"only {scored} of {tasks} samples were scored")
    if mean_score is None:
        reasons.append("no score")
    elif mean_score < floor:
        reasons.append(f"probe scored {mean_score:.3f} (floor {floor:.2f}): "
                       "the services were not healthy")
    if answered_rate is not None and answered_rate < answered_floor:
        reasons.append(f"answered rate {answered_rate:.2f} (floor {answered_floor:.2f})")
    return dict(valid=not reasons, reasons=reasons, mean_score=mean_score,
                answered_rate=answered_rate, scored=scored, tasks=tasks)
