"""Read ALW ``verifier/evaluation/metrics.json`` into ``(Y_star, C_star, d_star, meta)``.

Field provenance (ALW commit d5236c8): ``src/eda/evaluate.py`` (stages, status,
``evaluation_error``) and ``src/analoglayoutworld/_scoring_core.py::score_submission``
(``scoring_inputs``, ``score``, ``score_valid``, ``design_passed``, ``score_is_lower_bound``).
Semantics follow ``docs/alw-mcr-instantiation.md`` §1.1-1.2 and §3.5:

* ``Y_star`` = ``score`` when ``score_valid`` and not ``evaluation_error``. A task failure
  (``missing_submission`` / ``invalid_submission``, failed checks) is scored, never dropped;
  ``missing_submission`` is ``0.0``.
* ``C_star`` = ``design_passed`` (valid evaluations only).
* ``d_star`` = tri-state sub-criteria for ``(gds, drc, lvs, shape, fill, morph, pex, postsim)``
  with values ``1``, ``0`` or ``"bottom"`` (not evaluated: short-circuited, skipped, tool error or
  verifier budget exhausted). ``bottom`` items must be excluded from both S+ and S- sets.
* ``meta["infra_error"]``: verifier faults (``evaluation_error``, ``status in {tool_error,
  interrupted}`` not caused by the submission, unreadable / missing ``metrics.json``). Such runs
  have ``Y_star = None`` and are re-run under the paired rule; they are not task failures.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, NamedTuple

BOTTOM = "bottom"
D_KEYS = ("gds", "drc", "lvs", "shape", "fill", "morph", "pex", "postsim")
TASK_FAILURE_STATUSES = ("missing_submission", "invalid_submission")
INFRA_STATUSES = ("tool_error", "interrupted")
BUDGET_KINDS = ("verifier_budget",)


class MetricsReading(NamedTuple):
    Y_star: float | None
    C_star: int | None
    d_star: dict[str, int | str]
    meta: dict[str, Any]


def _num(v: Any) -> float | None:
    return float(v) if type(v) in (int, float) and math.isfinite(v) else None


def _stage(data: dict, name: str) -> dict:
    stages = data.get("stages") if isinstance(data.get("stages"), dict) else {}
    s = stages.get(name)
    return s if isinstance(s, dict) else {}


def _unevaluated(stage: dict) -> bool:
    """Stage that produced no verdict: absent, skipped, tool error, or budget-limited."""
    return (not stage or stage.get("status") in (None, "skipped", "error", "running")
            or stage.get("error_kind") in BUDGET_KINDS + ("verifier_error",))


def _pass_fail(stage: dict, passed: str, failed: tuple[str, ...]) -> int | str:
    if _unevaluated(stage):
        return BOTTOM
    status = stage.get("status")
    if status == passed:
        return 1
    if status in failed:
        return 0
    return BOTTOM


def d_star(data: dict) -> dict[str, int | str]:
    si = data.get("scoring_inputs") if isinstance(data.get("scoring_inputs"), dict) else {}
    status = data.get("status")
    present = si.get("submission_present", data.get("submission_present")) is True
    lvs_pass = si.get("lvs_pass") is True
    pex_success = si.get("pex_success") is True
    out: dict[str, int | str] = {}

    area = _stage(data, "area")
    geom = si.get("geometry_valid")
    # gds: 1 when frozen AND geometry_valid (matching Tier-1 m_gds); 0 when missing / invalid or
    # geometry_valid is False (e.g. truncated stream or wrong top cell, where Tier-0 file_present=1
    # still holds); bottom when the evaluation broke or stopped before geometry was checked.
    if present:
        if geom is True or ("geometry_valid" not in si and area.get("status") != "failed"):
            out["gds"] = 1
        elif geom is False or area.get("status") == "failed":
            out["gds"] = 0
        else:
            out["gds"] = BOTTOM
    elif status in TASK_FAILURE_STATUSES:
        out["gds"] = 0
    else:
        out["gds"] = BOTTOM

    out["drc"] = _pass_fail(_stage(data, "drc"), "passed", ("failed",))
    out["lvs"] = _pass_fail(_stage(data, "lvs"), "passed", ("failed",))

    if out["gds"] != 1 or _unevaluated(area):
        out["shape"] = BOTTOM
    elif area.get("status") == "measured" and geom is True:
        out["shape"] = 1 if si.get("shape_ok") is True else 0
    elif area.get("status") in ("failed", "measured"):
        out["shape"] = 0          # area failed on a valid stream or shape_ok=False
    else:
        out["shape"] = BOTTOM

    # fill / morph / pex are short-circuited (bottom) whenever LVS did not pass.
    low_fill = si.get("low_fill")
    out["fill"] = BOTTOM if not lvs_pass or not isinstance(low_fill, bool) else (0 if low_fill else 1)

    meaningless = si.get("meaningless")
    if not lvs_pass:
        out["morph"] = BOTTOM
    elif si.get("morphology_policy_exempt") is True:
        out["morph"] = 1
    elif isinstance(meaningless, bool):
        out["morph"] = 0 if meaningless else 1
    else:
        out["morph"] = BOTTOM

    out["pex"] = BOTTOM if not lvs_pass else _pass_fail(_stage(data, "pex"), "completed", ("failed",))

    # postsim: bottom without PEX, or when the verifier budget ended the run with every measured
    # case passing but coverage < 1 (unknown, not failed).
    cases = data.get("postsim_cases") if isinstance(data.get("postsim_cases"), dict) else {}
    coverage = _num(si.get("postsim_coverage"))
    if coverage is None:
        coverage = _num(cases.get("coverage"))
    all_passed = si.get("postsim_all_passed") is True
    failed_cases = cases.get("failed_case_count")
    if not isinstance(failed_cases, int):
        passed_n, done_n = cases.get("passed_case_count"), cases.get("completed_case_count")
        failed_cases = (done_n - passed_n) if isinstance(passed_n, int) and isinstance(done_n, int) else None
    lower_bound = data.get("score_is_lower_bound") is True or data.get("verifier_budget_exhausted") is True
    if not pex_success:
        out["postsim"] = BOTTOM
    elif coverage == 1.0 and all_passed:
        out["postsim"] = 1
    elif failed_cases is not None and failed_cases > 0:
        out["postsim"] = 0
    elif coverage is not None and coverage < 1.0 and (lower_bound or failed_cases in (0, None)):
        out["postsim"] = BOTTOM
    else:
        out["postsim"] = 0            # complete run that did not pass everything
    return out


def parse_metrics(data: Any) -> MetricsReading:
    if not isinstance(data, dict):
        return MetricsReading(None, None, {k: BOTTOM for k in D_KEYS},
                              {"status": "unreadable", "infra_error": True,
                               "infra_reason": "metrics_unreadable", "task_failure": False})
    status = data.get("status")
    evaluation_error = data.get("evaluation_error") is True
    task_failure = status in TASK_FAILURE_STATUSES
    infra_reason = None
    if evaluation_error:
        infra_reason = "evaluation_error"
    elif status in INFRA_STATUSES and not task_failure:
        infra_reason = f"status_{status}"
    infra = infra_reason is not None
    score = _num(data.get("score"))
    valid = data.get("score_valid") is True and not evaluation_error and score is not None
    if infra:
        y = None
    elif valid:
        y = score
    elif status == "missing_submission":
        y = 0.0
    else:
        y = None
        infra, infra_reason = True, "score_not_valid"
    c = (1 if data.get("design_passed") is True else 0) if y is not None else None
    si = data.get("scoring_inputs") if isinstance(data.get("scoring_inputs"), dict) else {}
    meta = {
        "status": status,
        "infra_error": infra,
        "infra_reason": infra_reason,
        "task_failure": task_failure,
        "evaluation_error": evaluation_error,
        "tool_error": data.get("tool_error") is True,
        "score_valid": data.get("score_valid") is True,
        "score_is_lower_bound": data.get("score_is_lower_bound") is True,
        "bonuses_eligible": data.get("bonuses_eligible") is True,
        "artifact_contract_complete": data.get("artifact_contract_complete") is True,
        "shape_ratio": _num(data.get("shape_ratio", si.get("shape_ratio"))),
        "raw_area_ratio": _num(data.get("raw_area_ratio", si.get("raw_area_ratio"))),
        "postsim_case_fraction": _num(si.get("postsim_case_fraction")),
        "postsim_coverage": _num(si.get("postsim_coverage")),
        "score_components": data.get("score_components") if isinstance(
            data.get("score_components"), dict) else {},
        "reason": data.get("reason"),
    }
    return MetricsReading(y, c, d_star(data), meta)


def read_metrics(path: str | Path) -> MetricsReading:
    """Read a ``metrics.json`` file. A missing or unreadable file is a verifier fault
    (``infra_error``), not a task failure."""
    try:
        return parse_metrics(json.loads(Path(path).read_text()))
    except FileNotFoundError:
        return MetricsReading(None, None, {k: BOTTOM for k in D_KEYS},
                              {"status": "missing", "infra_error": True,
                               "infra_reason": "metrics_missing", "task_failure": False})
    except (OSError, ValueError, UnicodeDecodeError):
        return parse_metrics(None)
