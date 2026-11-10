"""method-v4.1 §3.4 / §3.6 / §3.7: rule defaults, the proposal, its guard rails and the freeze.

``rule_defaults(calibration, user_inputs, history)`` computes every parameter
from the calibration by the rules of §3.4. Each entry carries its value,
source (``rule`` / ``agent`` / ``user`` / ``constant``), the ``sim_id`` it cites,
a one-line reason, a confidence and its allowed range. The calibration advisor
(D3) may add agent values (``apply_agent_values``); ``validate`` re-checks every
entry against the range whitelist and recomputes every cited ``sim_id``,
replacing rejected agent values by the rule value. ``apply_user_edit`` marks an
entry ``user``. ``freeze`` maps the proposal to the run-config mapping with the
per-key source.

Parameter names follow the shared V4Config naming contract; ``validation_size``
is split-only (it decides the freeze of the deferred split). This module keeps
its own schema and does not import ``config.py``.
"""
from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import math
from typing import Any, Optional

from .simulate import (C_ADOPT, DEFAULT_SIGMA2_HET, POWER_Z, CalibrationSummary, History, _calibration,
                       _history, minibatch_size, simulate)

SOURCES = ("rule", "agent", "user", "constant")
CONFIDENCE = ("high", "medium", "low")
RHO = 0.5                    # T_meas ≤ ρ·T_agent (§2.3)
SAFEGUARD_Z = 2.0            # z (§2.3)
MIN_ROUNDS = 6               # b is bounded so about this many rounds fit the budget
B_MAX = 16
DIAG_FAILURES, DIAG_CONTROLS, DIAG_FACTOR = 12, 3, 1.5
MINIBATCH_FRACTION, MINIBATCH_MIN = 0.3, 10
SMOKE_SIZE = 2
DEFAULT_CONCURRENCY = 3
DEFAULT_SAFEGUARDS = {"tokens_per_task_ratio": 0.5, "answer_produced_rate": 0.02}
DEFAULT_AGENT_TIMEOUT_S, MIN_AGENT_TIMEOUT_S = 1800.0, 900.0
DEFAULT_AGENT_S = 1200.0     # T_agent without history (assumed)

# Proposal key → simulator parameter (keys absent here do not change the simulation).
SIM_KEYS = {k: k for k in ("validation_size", "calibration_k", "smoke", "smoke_size", "minibatch",
                           "minibatch_fraction", "minibatch_min", "b", "cards_per_analysis",
                           "max_concurrent_executions", "remeasure")}
SIM_KEYS["adapter.agent_timeout"] = "agent_timeout"
INPUT_SIM_KEYS = ("time_budget_hours", "spend_budget", "price_per_1k_tokens")
USER_INPUTS = ("time_budget_hours", "spend_budget", "price_per_1k_tokens", "calibration_k", "safeguards", "delta")


# ---------------------------------------------------------------- schema
@dataclasses.dataclass
class Item:
    value: Any
    source: str
    reason: str
    confidence: str
    range: dict
    sim_id: Optional[str] = None
    rule_value: Any = None
    agent_value: Any = None           # retain an accepted advisor suggestion after a user override
    sim_params: Optional[dict] = None   # the parameters the cited simulation used (agent items)

    def __post_init__(self):
        if self.source not in SOURCES:
            raise ValueError(f"unknown parameter source {self.source!r}")
        if self.confidence not in CONFIDENCE:
            raise ValueError(f"unknown confidence {self.confidence!r}")


@dataclasses.dataclass
class Proposal:
    items: dict
    inputs: dict
    sim_id: Optional[str] = None        # the simulation of the rule values
    rule_params: Optional[dict] = None  # its parameters (rule items cite it)
    notes: dict = dataclasses.field(default_factory=dict)

    def values(self) -> dict:
        return {k: item.value for k, item in self.items.items()}

    def params(self) -> dict:
        """Simulator parameters of the current values."""
        out = {SIM_KEYS[k]: item.value for k, item in self.items.items() if k in SIM_KEYS}
        out.update({k: self.inputs[k] for k in INPUT_SIM_KEYS if self.inputs.get(k) is not None})
        return out

    def to_dict(self) -> dict:
        return dict(kind="method-v4.1-proposal", items={k: dataclasses.asdict(v) for k, v in self.items.items()},
                    inputs=self.inputs, sim_id=self.sim_id, rule_params=self.rule_params, notes=self.notes)

    @classmethod
    def from_dict(cls, data: dict) -> "Proposal":
        if data.get("kind") != "method-v4.1-proposal":
            raise ValueError("not a method-v4.1 proposal")
        return cls(items={k: Item(**v) for k, v in data["items"].items()}, inputs=dict(data["inputs"]),
                   sim_id=data.get("sim_id"), rule_params=data.get("rule_params"), notes=dict(data.get("notes") or {}))


def _int(lo, hi):
    return dict(type="int", min=int(lo), max=int(hi))


def _float(lo, hi):
    return dict(type="float", min=float(lo), max=float(hi))


BOOL = dict(type="bool")
TOLERANCES = dict(type="tolerances", min=0.0, max=10.0)

# Method variants (§2.3 constants a run may deliberately change). They are absent from the rule
# defaults, so earlier proposals stay valid; only the user sets them, ``freeze`` copies them into
# the run config and ``V4Config.variants()`` reports them as method variants.
VARIANT_RANGES = {
    "c": _float(0, 2), "c_mech": _float(0, 2), "minibatch_kill": _float(0, 3),
    "speculative_analysis": BOOL,
    "remeasure_mode": dict(type="enum", values=["merged", "independent"]),
    "remeasure_parallel": BOOL,
    "carry_over": _int(0, 4), "rotate_diagnosis": BOOL,
    "explore_after": _int(0, 10), "explore_margin": _float(0, 0.1),
    "adapter.analyst_edits": BOOL,
    "adapter.implementer_profile": dict(type="str"),
}


def allowed_ranges(calibration: CalibrationSummary, inputs: dict) -> dict:
    """The whitelist (§3.6); ranges that depend on the pool or the budget come from the inputs."""
    available = max(1, calibration.pool_size - calibration.test_size)
    budget_s = float(inputs["time_budget_hours"]) * 3600
    return {
        "calibration_k": _int(2, 5),
        "validation_size": _int(min(calibration.n_probe, available - 1), max(calibration.n_probe, available - 1)),
        "diagnose_size": _int(1, max(1, available - calibration.n_probe)),
        "smoke": BOOL, "smoke_size": _int(1, 3),
        "minibatch": BOOL, "minibatch_fraction": _float(0.25, 0.30), "minibatch_min": _int(10, 100),
        "b": _int(1, B_MAX), "cards_per_analysis": _int(1, 2 * B_MAX),
        "max_concurrent_executions": _int(1, 16),
        "remeasure": BOOL,
        "safeguard_z": _float(0, 5),
        "safeguards": TOLERANCES,
        "adapter.agent_timeout": _float(300, 4 * 3600),
        "adapter.execution_timeout_seconds": _int(1, min(86400, max(1, budget_s))),
        "adapter.infra_retry": _int(0, 3),
    }


def check_range(value: Any, spec: dict) -> Optional[str]:
    """None when the value fits the range, else the reason."""
    kind = spec["type"]
    if kind == "bool":
        return None if type(value) is bool else "must be a boolean"
    if kind == "str":
        return None if isinstance(value, str) and value.strip() and len(value) <= 200 else "must be a non-empty string"
    if kind == "enum":
        return None if value in spec["values"] else f"must be one of {spec['values']}"
    if kind == "int":
        if type(value) is not int:
            return "must be an integer"
    elif kind == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return "must be a number"
    elif kind == "tolerances":
        if not isinstance(value, dict) or not value:
            return "must be a non-empty mapping of safeguard → tolerance"
        bad = [k for k, v in value.items() if type(k) is not str or isinstance(v, bool)
               or not isinstance(v, (int, float)) or not spec["min"] <= v <= spec["max"]]
        return f"tolerance out of [{spec['min']}, {spec['max']}] for {bad}" if bad else None
    if not spec["min"] <= value <= spec["max"]:
        return f"{value} is outside [{spec['min']}, {spec['max']}]"
    return None


def _cross(values: dict, calibration: CalibrationSummary, *, include_dependencies=False) -> dict:
    """Constraint errors by field; include all participants for advisor rollback."""
    out = {}
    available = calibration.pool_size - calibration.test_size
    if values.get("validation_size", 0) + values.get("diagnose_size", 0) > available:
        out["validation_size"] = out["diagnose_size"] = \
            f"validation_size + diagnose_size exceeds the {available} tasks of the pool"
    if values.get("cards_per_analysis", 0) < values.get("b", 0):
        out["cards_per_analysis"] = "cards_per_analysis must be at least b"
        if include_dependencies:
            out["b"] = out["cards_per_analysis"]
    if values.get("c_mech", 0.5) > values.get("c", 1.0):
        for key in ("c", "c_mech"):
            if key in values:
                out[key] = "c_mech must not exceed c (mechanism evidence only relaxes the threshold)"
    if values.get("diagnose_size", 0) < calibration.n_diagnosis:
        out['diagnose_size'] = 'diagnose_size cannot shrink the already observed whole-paper D'
    if calibration.remaining_paper_sizes is not None:
        reachable = {0}
        for size in calibration.remaining_paper_sizes:
            reachable |= {n + size for n in tuple(reachable)}
        feasible = sorted(calibration.n_probe + n for n in reachable)
        requested = values.get('validation_size', 0)
        if requested not in feasible:
            nearest = sorted(feasible, key=lambda n: (abs(n - requested), n))[:2]
            out['validation_size'] = f'whole-paper validation_size required; nearest feasible sizes: {nearest}'
    return out


# ---------------------------------------------------------------- inputs
def normalize_inputs(user_inputs: dict) -> dict:
    unknown = set(user_inputs) - set(USER_INPUTS)
    if unknown:
        raise ValueError(f"unknown user inputs: {sorted(unknown)}")
    out = dict(user_inputs)
    if not isinstance(out.get("time_budget_hours"), (int, float)) or out["time_budget_hours"] <= 0:
        raise ValueError("time_budget_hours is a required positive number")
    k = out.get("calibration_k", 3)
    if type(k) is not int or not 2 <= k <= 5:
        raise ValueError("calibration_k is an integer between 2 and 5")
    out["calibration_k"] = k
    if out.get("safeguards") is not None and check_range(out["safeguards"], TOLERANCES):
        raise ValueError("safeguards: " + check_range(out["safeguards"], TOLERANCES))
    if out.get("delta") is not None and (not isinstance(out["delta"], (int, float)) or out["delta"] <= 0):
        raise ValueError("delta is a positive number or null")
    return out


# ---------------------------------------------------------------- rules (§3.4)
def _concurrency(probe) -> tuple[int, str, str]:
    if not probe:
        return (DEFAULT_CONCURRENCY, f"no concurrency probe: default {DEFAULT_CONCURRENCY}; "
                "confirm the target system sustains it", "low")
    points = sorted((int(m), float(tp)) for m, tp in probe)
    chosen, rate = points[0]
    for m, tp in points[1:]:
        if tp < 1.2 * rate:
            break
        chosen, rate = m, tp
    return chosen, f"concurrency probe: throughput gain stops reaching 20% beyond {chosen}", "high"


def _retries(fail: float) -> int:
    for r in range(4):
        if fail ** (r + 1) < 0.01:
            return r
    return 3


def safeguard_noise(calibration: CalibrationSummary, n_val: int, z: float = SAFEGUARD_Z) -> dict:
    """§4.5 noise tolerance z·SE per safeguard at n_val, one measurement per side (ratio metrics: log scale)."""
    return {key: dict(se=math.sqrt(2 * var / max(1, n_val)), tolerance=z * math.sqrt(2 * var / max(1, n_val)))
            for key, var in sorted((calibration.safeguard_var or {}).items())
            if isinstance(var, (int, float)) and var >= 0}


def rule_defaults(calibration: Any, user_inputs: dict, history: Any = None) -> Proposal:
    cal: CalibrationSummary = _calibration(calibration)
    hist: History = _history(history)
    inputs = normalize_inputs(user_inputs)
    ranges = allowed_ranges(cal, inputs)
    budget_s = inputs["time_budget_hours"] * 3600
    available = cal.pool_size - cal.test_size
    if available < cal.n_probe + 1:
        raise ValueError("the pool leaves no task for the search set after the probe sample")
    items: dict[str, Item] = {}

    def put(key, value, source, reason, confidence="medium"):
        items[key] = Item(value=value, source=source, reason=reason, confidence=confidence, range=ranges[key],
                          rule_value=value if source in ("rule", "constant") else None)

    # User inputs that are parameters.
    put("calibration_k", inputs["calibration_k"], "user" if "calibration_k" in user_inputs else "constant",
        "user input (§2.1 #7)" if "calibration_k" in user_inputs else "default 3 (§3.3)", "high")
    safeguards = inputs.get("safeguards") or dict(DEFAULT_SAFEGUARDS)
    put("safeguards", safeguards, "user" if inputs.get("safeguards") else "constant",
        "product tolerances given by the user" if inputs.get("safeguards") else "default safeguard set (§4.5)", "high")
    put("safeguard_z", SAFEGUARD_Z, "constant", "method constant z (§2.3)", "high")
    put("remeasure", True, "constant", "re-measure before adoption is on (§2.3)", "high")

    # T_agent: history median, else assumed.
    (agent_iv, agent_src) = hist.agent_interval()
    t_agent = agent_iv[1] if agent_src == "history" else DEFAULT_AGENT_S
    agent_note = f"T_agent {t_agent:.0f} s ({'history median' if agent_src == 'history' else 'assumed'})"

    # |D| = ⌈(failures + controls) / seed fail rate × 1.5⌉.
    room = available - cal.n_probe
    fail = cal.seed_fail_rate
    if fail > 0:
        want = math.ceil((DIAG_FAILURES + DIAG_CONTROLS) / fail * DIAG_FACTOR)
        diag = max(1, min(want, room - 1 if room > 1 else room))
        reason = (f"⌈({DIAG_FAILURES}+{DIAG_CONTROLS})/{fail:.3f}×{DIAG_FACTOR}⌉ = {want}"
                  + (f", bounded by the pool to {diag}" if diag != want else ""))
    else:
        diag = max(1, min(room - 1, DIAG_FAILURES + DIAG_CONTROLS) if room > 1 else room)
        reason = f"the seed failed no probe task: {diag} tasks (failures cannot be targeted)"
    if cal.n_diagnosis:
        diag = cal.n_diagnosis
        reason = "already measured whole-paper D; exposed diagnosis never shrinks"
    put("diagnose_size", diag, "rule", reason, "medium" if fail > 0 else "low")

    # n_val: largest n with o + n·t ≤ ρ·T_agent, in [|P|, pool − test − |D|].
    upper = max(cal.n_probe, available - diag)
    if cal.per_task_s > 0:
        n_fit = int(math.floor((RHO * t_agent - cal.exec_overhead_s) / cal.per_task_s))
    else:
        n_fit = upper
    n_val = max(cal.n_probe, min(upper, n_fit))
    bound = " (raised to |P|)" if n_fit < cal.n_probe else " (capped by the pool)" if n_fit > upper else ""
    if cal.remaining_paper_sizes is not None:
        reachable = {0}
        for size in cal.remaining_paper_sizes:
            reachable |= {n + size for n in tuple(reachable)}
        feasible = [cal.n_probe + n for n in reachable if cal.n_probe + n <= n_val]
        n_val = max(feasible)
        bound += "; rounded down to a realizable whole-paper count"
    put("validation_size", n_val, "rule",
        f"largest n with {cal.exec_overhead_s:.0f}+n·{cal.per_task_s:.1f} s ≤ {RHO}·{agent_note}: {n_fit}{bound}",
        "medium" if agent_src == "history" else "low")

    # Minibatch: 30% of validation, ≥ 10, on when q·T_meas(rest) > o (minibatch results are reused).
    m = minibatch_size(n_val, MINIBATCH_FRACTION, MINIBATCH_MIN)
    (q_mb_iv, mb_src) = hist.elimination("minibatch")
    q_mb = q_mb_iv[1]
    if not m:
        mb_on, reason = False, f"{MINIBATCH_FRACTION:.0%} of {n_val} validation tasks is below {MINIBATCH_MIN}"
    else:
        saved, cost = cal.t_meas(n_val - m), cal.exec_overhead_s
        mb_on = q_mb * saved > cost
        reason = (f"q={q_mb:.2f} ({mb_src}) × saved {saved:.0f} s {'>' if mb_on else '≤'} extra execution "
                  f"overhead {cost:.0f} s (minibatch tasks are reused by the full validation)")
    put("minibatch", mb_on, "rule", reason, "medium" if mb_src == "history" else "low")
    put("minibatch_fraction", MINIBATCH_FRACTION, "rule", "25–30% of the validation set (§3.4)")
    put("minibatch_min", MINIBATCH_MIN, "constant", "minibatch needs at least 10 tasks (§3.4)", "high")

    # Smoke: on when q·S > C, S = the downstream measurement it saves, C = its own execution.
    (q_s_iv, smoke_src) = hist.elimination("smoke")
    q_s = q_s_iv[1]
    downstream = (cal.t_meas(m) + (1 - q_mb) * cal.t_meas(n_val - m)) if mb_on else cal.t_meas(n_val)
    smoke_cost = cal.t_meas(SMOKE_SIZE)
    smoke_on = q_s * downstream > smoke_cost
    put("smoke", smoke_on, "rule", f"q={q_s:.2f} ({smoke_src}) × saved {downstream:.0f} s "
        f"{'>' if smoke_on else '≤'} smoke cost {smoke_cost:.0f} s", "medium" if smoke_src == "history" else "low")
    put("smoke_size", SMOKE_SIZE, "rule", "1–3 search tasks, only checks that a trajectory is produced (§3.4)")

    # E: concurrency probe, else 3.
    e, reason, conf = _concurrency(cal.concurrency_probe)
    put("max_concurrent_executions", e, "rule", reason, conf)

    # Adapter: agent timeout, execution timeout, retries.
    p90 = hist.agent_p90()
    timeout = max(MIN_AGENT_TIMEOUT_S, 2 * p90) if p90 is not None else DEFAULT_AGENT_TIMEOUT_S
    put("adapter.agent_timeout", float(timeout), "rule",
        f"max(15 min, 2 × history p90 {p90:.0f} s)" if p90 is not None else "no history: 30 min",
        "medium" if p90 is not None else "low")
    exec_timeout = min(max(60, math.ceil(2.5 * cal.p95_exec_s)), max(1, int(budget_s)), 86400)
    put("adapter.execution_timeout_seconds", exec_timeout, "rule",
        f"2.5 × p95 {cal.p95_exec_s:.0f} s, bounded by the budget", "medium")
    retries = _retries(cal.infra_fail_rate)
    put("adapter.infra_retry", retries, "rule",
        f"smallest r with {cal.infra_fail_rate:.3f}^(r+1) < 1% (≤ 3)", "medium")

    # b ≈ E·(T_agent + T_meas)/T_meas, then lowered until ≥ ~6 rounds fit (mid estimate).
    t_meas = cal.t_meas(n_val)
    b0 = max(1, min(B_MAX, round(e * (t_agent + t_meas) / t_meas))) if t_meas > 0 else B_MAX
    b = b0

    def params_for(bb):
        values = {k: it.value for k, it in items.items()}
        values.update(b=bb, cards_per_analysis=bb + 2)
        out = {SIM_KEYS[k]: v for k, v in values.items() if k in SIM_KEYS}
        out.update({k: inputs[k] for k in INPUT_SIM_KEYS if inputs.get(k) is not None})
        return out
    while b > 1 and simulate(params_for(b), cal, hist)["rounds"]["mid"] < MIN_ROUNDS:
        b -= 1
    put("b", b, "rule", f"E·(T_agent+T_meas)/T_meas = {e}·({t_agent:.0f}+{t_meas:.0f})/{t_meas:.0f} → {b0}"
        + (f", lowered to {b} so ≥{MIN_ROUNDS} rounds fit time and measurement-spend budgets" if b != b0 else ""),
        "medium" if agent_src == "history" else "low")
    put("cards_per_analysis", b + 2, "rule", "b + 2 (spares fill freed slots)")

    rule_params = params_for(b)
    sim = simulate(rule_params, cal, hist)
    for key in SIM_KEYS:
        items[key].sim_id = sim["sim_id"]
    proposal = Proposal(items=items, inputs=inputs, sim_id=sim["sim_id"], rule_params=rule_params)
    proposal.notes = _notes(proposal, cal, hist, sim)
    return proposal


def _notes(proposal: Proposal, cal: CalibrationSummary, hist: History, sim: dict) -> dict:
    n_val = proposal.items["validation_size"].value
    k = proposal.items["calibration_k"].value
    notes = dict(
        forecast={key: sim[key] for key in ("per_round_s", "candidates", "rounds", "bottleneck", "spend")},
        resolution=sim["resolution"], noise_adoptions=sim["noise_adoptions"], assumptions=sim["assumptions"],
        safeguard_noise_tolerance=safeguard_noise(cal, n_val, proposal.items["safeguard_z"].value),
    )
    delta = proposal.inputs.get("delta")
    if delta is not None:
        detectable = sim["resolution"]["detectable"]["mid"]
        var = DEFAULT_SIGMA2_HET[1] + cal.rerun_var * (1 + 1 / k)
        needed = math.ceil((C_ADOPT + POWER_Z) ** 2 * var / delta ** 2)
        notes["delta"] = dict(delta=delta, detectable=detectable, detectable_ok=delta >= detectable,
                              validation_tasks_needed=needed,
                              available=cal.pool_size - cal.test_size - proposal.items["diagnose_size"].value,
                              message=None if delta >= detectable else
                              f"an improvement of {delta:g} is below what this budget can detect ({detectable:.3f}); "
                              f"about {needed} validation tasks would be needed, each candidate measurement "
                              f"then takes ~{cal.t_meas(needed):.0f} s")
    return notes


# ---------------------------------------------------------------- edits, guard rails, freeze
def apply_user_edit(proposal: Proposal, key: str, value: Any) -> Proposal:
    """A copy with ``key`` set by the user (source ``user``); refuses unknown keys and out-of-range values."""
    if key not in proposal.items and key not in VARIANT_RANGES:
        raise KeyError(f"unknown proposal parameter {key!r}")
    item = proposal.items.get(key) or Item(value=None, source="user", reason="", confidence="high",
                                           range=copy.deepcopy(VARIANT_RANGES[key]))
    problem = check_range(value, item.range)
    if problem:
        raise ValueError(f"{key}: {problem}")
    out = copy.deepcopy(proposal)
    out.items[key] = dataclasses.replace(item, value=copy.deepcopy(value), source="user", sim_id=None,
                                         sim_params=None, reason="set by the user", confidence="high")
    return out


def apply_agent_values(proposal: Proposal, values: dict) -> Proposal:
    """A copy with the calibration advisor's values (D3): key → dict(value, sim_id, sim_params, reason, confidence).

    Preserve user-owned inputs and method constants immediately; ``validate``
    also checks serialized proposals that bypass this helper.
    """
    out = copy.deepcopy(proposal)
    for key, row in values.items():
        if key not in out.items:
            out.notes.setdefault("rejected", []).append(dict(key=key, reason="unknown parameter"))
            continue
        if key in ADVISOR_READ_ONLY:
            out.notes.setdefault("rejected", []).append(
                dict(key=key, source="agent", reason="advisor cannot change user inputs or method constants"))
            continue
        item = out.items[key]
        out.items[key] = dataclasses.replace(
            item, value=row.get("value"), agent_value=row.get("value"), source="agent", sim_id=row.get("sim_id"), sim_params=row.get("sim_params"),
            reason=str(row.get("reason") or ""), confidence=row.get("confidence") if row.get("confidence") in CONFIDENCE else "low")
    return out


# User-owned inputs and method constants are never advisor decision variables.
ADVISOR_READ_ONLY = frozenset({"calibration_k", "safeguards", "safeguard_z", "remeasure", "minibatch_min"})


def validate(proposal: Proposal, calibration: Any, history: Any = None) -> dict:
    """Validate the complete canonical contract, rebuilding rejected advisor values.

    Serialized ranges, rule_params and rule_value are untrusted. Recompute the
    baseline from calibration, user inputs and history, and validate the final
    values again after fallback. Non-advisor errors are fatal.
    """
    cal, hist = _calibration(calibration), _history(history)
    out = copy.deepcopy(proposal)
    rejected, fatal, sims = copy.deepcopy(out.notes.get("rejected", [])), [], {}
    try:
        baseline = rule_defaults(cal, proposal.inputs, hist)
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        return dict(ok=False, rejected=[], fatal=[dict(key="inputs", source=None, reason=str(exc))], proposal=out)
    ranges = allowed_ranges(cal, baseline.inputs)

    def row(key, reason, item=None):
        return dict(key=key, source=item.source if item else None, reason=reason)

    for key in sorted(set(ranges) - set(out.items)):
        fatal.append(row(key, "missing mandatory parameter"))
    for key in sorted(set(out.items) - set(ranges) - set(VARIANT_RANGES)):
        fatal.append(row(key, "unknown parameter", out.items[key]))
    if proposal.sim_id != baseline.sim_id or proposal.rule_params != baseline.rule_params:
        fatal.append(row("sim_id", "rule sim_id or parameters do not match a recomputation"))

    def sim_id_for(params):
        try:
            blob = json.dumps(params, sort_keys=True, allow_nan=False)
            if blob not in sims:
                sims[blob] = simulate(params, cal, hist)["sim_id"]
            return sims[blob]
        except (ValueError, TypeError, KeyError, OverflowError, ZeroDivisionError):
            return None

    def problem(key, item):
        if key in VARIANT_RANGES:
            reason = check_range(item.value, VARIANT_RANGES[key])
            return reason or (None if item.source == "user" else "a method variant is set only by the user")
        reason = check_range(item.value, ranges[key])
        if reason:
            return reason
        if key == "calibration_k" and item.value != cal.k:
            return "calibration_k differs from the measured calibration; recalibration is required"
        if item.source not in SOURCES or item.confidence not in CONFIDENCE or not item.reason:
            return "invalid source, confidence or empty reason"
        expected = baseline.items[key]
        if item.source == "agent":
            if key in ADVISOR_READ_ONLY:
                return "advisor cannot change user inputs or method constants"
            if not item.sim_id or not isinstance(item.sim_params, dict):
                return "an agent value must cite a sim_id and the parameters it simulated"
            if key in SIM_KEYS and item.sim_params.get(SIM_KEYS[key]) != item.value:
                return "the value differs from the simulated parameters"
            if sim_id_for(item.sim_params) != item.sim_id:
                return "the cited sim_id does not match a recomputation"
        elif item.source in ("rule", "constant"):
            if key in SIM_KEYS and item.sim_id != baseline.sim_id:
                return "the cited sim_id does not match a recomputation"
            if key not in SIM_KEYS and item.sim_id is not None:
                return "parameter does not have a rule sim_id"
            if item.value != expected.value:
                return "the value differs from the recomputed rule value"
            if item.source == "constant" and key not in ADVISOR_READ_ONLY:
                return "parameter is not a method constant"
        elif item.sim_params is not None or (item.sim_id is not None and
                (key not in SIM_KEYS or item.sim_id != baseline.sim_id or item.value != expected.value)):
            return "user values must not carry a stale simulation citation"
        return None

    def reject_or_restore(key, reason):
        item = out.items[key]
        entry = row(key, reason, item)
        rejected.append(entry)
        if item.source == "agent":
            out.items[key] = copy.deepcopy(baseline.items[key])
            out.items[key].reason = f"agent value rejected ({reason}); canonical baseline kept"
        else:
            fatal.append(entry)

    for key in sorted(set(out.items) & set(ranges)):
        item = out.items[key]
        reason = problem(key, item)
        if reason:
            reject_or_restore(key, reason)
        out.items[key].range = copy.deepcopy(ranges[key])
        out.items[key].rule_value = copy.deepcopy(baseline.items[key].rule_value)
    for key in sorted(set(out.items) & set(VARIANT_RANGES)):
        reason = problem(key, out.items[key])
        if reason:
            fatal.append(row(key, reason, out.items[key]))
        out.items[key].range = copy.deepcopy(VARIANT_RANGES[key])
    if not fatal:
        for key, reason in _cross(out.values(), cal, include_dependencies=True).items():
            if out.items[key].source == "agent":
                reject_or_restore(key, reason)
        for key, item in out.items.items():
            reason = problem(key, item)
            if reason:
                fatal.append(row(key, reason, item))
        for key, reason in _cross(out.values(), cal).items():
            if not any(r["key"] == key for r in fatal):
                fatal.append(row(key, reason, out.items[key]))
    if rejected:
        out.notes = dict(out.notes, rejected=rejected)
    return dict(ok=not fatal, rejected=rejected, fatal=fatal, proposal=out)


def freeze(proposal: Proposal, calibration: Any, history: Any = None) -> dict:
    """The run-config mapping (adapter keys nested, ``validation_size`` under ``split``) with per-key sources."""
    result = validate(proposal, calibration, history)
    if not result["ok"]:
        raise ValueError(f"cannot freeze invalid proposal: {result['fatal']}")
    proposal = result["proposal"]
    config: dict = {}
    for key, item in sorted(proposal.items.items()):
        if key.startswith("adapter."):
            config.setdefault("adapter", {})[key.split(".", 1)[1]] = item.value
        elif key == "validation_size":
            config.setdefault("split", {})[key] = item.value
        else:
            config[key] = item.value
    sources = {key: item.source for key, item in sorted(proposal.items.items())}
    blob = json.dumps(config, sort_keys=True, separators=(",", ":"))
    return dict(kind="method-v4.1-run-config", config=config, sources=sources,
                rule_values={k: it.rule_value for k, it in sorted(proposal.items.items())},
                proposal_sim_id=proposal.sim_id, inputs=proposal.inputs,
                config_sha256=hashlib.sha256(blob.encode()).hexdigest())
