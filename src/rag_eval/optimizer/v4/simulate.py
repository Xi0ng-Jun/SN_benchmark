"""method-v4.1 §3.5 simulator: per-round time, candidates, rounds and resolution as intervals.

Pure: ``simulate(params, calibration, history)`` reads no files and runs nothing.
The same inputs always give the same ``sim_id`` (a hash of the canonical
inputs), so a proposal item that cites a ``sim_id`` can be checked by
recomputing it (§3.6 guard rails).

Model (one round under one parent):

* ``analysis`` (agent) → ``b`` implementation slots in parallel → each card's
  measurement cascade on ``E`` shared execution slots → one re-measure of the
  best qualifying candidate → decision.
* Measurement time of n tasks: ``T_meas(n) = o + n·t`` (warm cache; ``t`` is the
  calibrated per-task seconds, so it already contains the system's internal
  parallelism).
* Per-card execution work: optional smoke (search, eliminates ``q_smoke``),
  optional minibatch (eliminates ``q_mb``), then the rest of the validation set
  (minibatch results are reused, §4.2).
* Round length waits for the slowest card: implementation phase =
  max(slowest agent + its own cascade, typical agent + ceil(b/E)·W).
  This is a discrete-wave approximation; elimination contributes expected work.
* Intervals: the low / mid / high scenarios vary the assumed quantities (agent
  seconds per card, elimination rates, σ²_het, analysis seconds); each output
  is reported sorted as ``dict(low, mid, high)``.

Quantities calibration cannot measure come from the history of earlier v4
ledgers when available, else from defaults, and are listed under ``assumptions``.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable, Optional

SIM_VERSION = "v4.1-sim-2"

# Method constants (§2.3) used by the simulator.
C_ADOPT = 1.0           # c: adoption threshold (unconfirmed mechanism), Δ ≥ c·SE
POWER_Z = 0.84          # detectable = (c + 0.84)·SE ≈ 80% adoption probability
DECISION_S = 30.0       # end-of-round decision (bookkeeping, no executions)

# Assumed quantities: (low, mid, high). "low"/"high" are the optimistic /
# pessimistic ends for time; the scenario code maps them accordingly.
DEFAULT_AGENT_S = (600.0, 1200.0, 2400.0)          # per card implementation (agent) seconds
DEFAULT_ANALYSIS_S = (300.0, 600.0, 900.0)         # analysis agent seconds per round
DEFAULT_ELIMINATION = dict(smoke=(0.05, 0.10, 0.20), minibatch=(0.15, 0.30, 0.45))
DEFAULT_SIGMA2_HET = (0.005, 0.02, 0.05)           # per-task effect heterogeneity of one change
MIN_HISTORY = 5                                    # fewer observations than this: use the default


# ---------------------------------------------------------------- inputs
@dataclasses.dataclass(frozen=True)
class CalibrationSummary:
    """The calibration numbers both the simulator and the rule defaults read (§3.2).

    Times are seconds; variances are per task on the primary metric's scale.
    ``concurrency_probe`` is an optional list of ``(concurrent executions,
    tasks per second)`` pairs measured by the concurrency probe.
    """
    rerun_var: float
    n_probe: int
    k: int                       # calibration_k actually used
    seed_fail_rate: float
    constant_fraction: float
    exec_overhead_s: float       # o in T_meas(n) = o + n·t
    per_task_s: float            # t
    p95_exec_s: float
    infra_fail_rate: float
    tokens_per_task: Optional[float]
    safeguard_var: dict = dataclasses.field(default_factory=dict)
    cold_wall_s: Optional[float] = None
    warm_wall_s: Optional[float] = None
    concurrency_probe: Optional[tuple] = None
    pool_size: int = 0
    test_size: int = 0
    n_diagnosis: int = 0
    remaining_paper_sizes: Optional[tuple] = None

    def __post_init__(self):
        if self.n_probe < 1 or self.k < 1 or self.pool_size < 0 or self.test_size < 0:
            raise ValueError("calibration needs n_probe ≥ 1, k ≥ 1 and nonnegative pool / test sizes")
        for name in ("rerun_var", "exec_overhead_s", "per_task_s", "p95_exec_s", "tokens_per_task"):
            if name == 'tokens_per_task' and self.tokens_per_task is None:
                continue
            if type(getattr(self, name)) not in (int, float) or not math.isfinite(getattr(self, name)) or getattr(self, name) < 0:
                raise ValueError(f"calibration {name} must be a nonnegative number")
        for name in ("seed_fail_rate", "constant_fraction", "infra_fail_rate"):
            if not 0 <= getattr(self, name) <= 1:
                raise ValueError(f"calibration {name} must lie in [0, 1]")

    def t_meas(self, n: float) -> float:
        """Warm measurement seconds of n tasks; zero tasks cost nothing."""
        return 0.0 if n <= 0 else self.exec_overhead_s + n * self.per_task_s

    def canonical(self) -> dict:
        out = dataclasses.asdict(self)
        out["safeguard_var"] = dict(sorted((self.safeguard_var or {}).items()))
        if self.concurrency_probe is not None:
            out["concurrency_probe"] = [list(p) for p in self.concurrency_probe]
        if self.remaining_paper_sizes is not None:
            out['remaining_paper_sizes'] = list(self.remaining_paper_sizes)
        return out

    @classmethod
    def from_dict(cls, data: dict) -> "CalibrationSummary":
        fields = {f.name for f in dataclasses.fields(cls)}
        extra = set(data) - fields
        if extra:
            raise ValueError(f"unknown calibration fields: {sorted(extra)}")
        data = dict(data)
        if data.get("concurrency_probe") is not None:
            data["concurrency_probe"] = tuple(tuple(p) for p in data["concurrency_probe"])
        return cls(**data)


@dataclasses.dataclass
class History:
    """What earlier v4 ledgers tell about quantities calibration cannot measure.

    ``eliminated`` / ``reached`` count candidates per stage (smoke, minibatch).
    """
    agent_s: list = dataclasses.field(default_factory=list)
    analysis_s: list = dataclasses.field(default_factory=list)
    reached: dict = dataclasses.field(default_factory=dict)
    eliminated: dict = dataclasses.field(default_factory=dict)
    ledgers: int = 0

    def agent_interval(self) -> tuple[tuple[float, float, float], str]:
        if len(self.agent_s) >= MIN_HISTORY:
            xs = sorted(self.agent_s)
            return (quantile(xs, .25), quantile(xs, .5), quantile(xs, .9)), "history"
        return DEFAULT_AGENT_S, "assumed"

    def agent_p90(self) -> Optional[float]:
        return quantile(sorted(self.agent_s), .9) if len(self.agent_s) >= MIN_HISTORY else None

    def analysis_interval(self) -> tuple[tuple[float, float, float], str]:
        if len(self.analysis_s) >= MIN_HISTORY:
            xs = sorted(self.analysis_s)
            return (quantile(xs, .25), quantile(xs, .5), quantile(xs, .9)), "history"
        return DEFAULT_ANALYSIS_S, "assumed"

    def elimination(self, stage: str) -> tuple[tuple[float, float, float], str]:
        n, e = self.reached.get(stage, 0), self.eliminated.get(stage, 0)
        if n >= MIN_HISTORY:
            p = e / n
            half = 1.0 * math.sqrt(max(p * (1 - p), 1e-9) / n)   # ±1 binomial SE
            return (max(0.0, p - half), p, min(1.0, p + half)), "history"
        return DEFAULT_ELIMINATION[stage], "assumed"

    def summary(self) -> dict:
        """The derived values the simulator uses; part of the sim_id."""
        return dict(agent=self.agent_interval(), analysis=self.analysis_interval(),
                    smoke=self.elimination("smoke"), minibatch=self.elimination("minibatch"),
                    agent_p90=self.agent_p90())


def quantile(xs: list, q: float) -> float:
    """Linear-interpolation quantile of a sorted, non-empty list."""
    if not xs:
        raise ValueError("quantile of an empty list")
    pos = q * (len(xs) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(xs) - 1)
    return float(xs[lo] + (xs[hi] - xs[lo]) * (pos - lo))


def _number(value: Any) -> Optional[float]:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 else None


def read_history(paths: Iterable[Path | str]) -> History:
    """Read earlier v4 ``ledger.json`` files (stable fields only; anything malformed is skipped).

    Per card: ``rounds[].records{}.timings.agent_s``; per stage: a record that
    has ``timings.smoke_s`` / ``timings.minibatch_s`` reached that stage, and
    ``stage == smoke_fail`` / ``minibatch_fail`` was eliminated there. Per
    round: ``rounds[].analysis_s``.
    """
    history = History()
    for path in paths:
        try:
            ledger = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(ledger, dict) or not isinstance(ledger.get("rounds"), list):
            continue
        history.ledgers += 1
        for rd in ledger["rounds"]:
            if not isinstance(rd, dict):
                continue
            a = _number(rd.get("analysis_s"))
            if a:
                history.analysis_s.append(a)
            records = rd.get("records")
            records = records.values() if isinstance(records, dict) else records if isinstance(records, list) else []
            for rec in records:
                if not isinstance(rec, dict):
                    continue
                timings = rec.get("timings") if isinstance(rec.get("timings"), dict) else {}
                agent = _number(timings.get("agent_s"))
                if agent:
                    history.agent_s.append(agent)
                for stage, key, fail in (("smoke", "smoke_s", "smoke_fail"), ("minibatch", "minibatch_s", "minibatch_fail")):
                    if key in timings or rec.get("stage") == fail:
                        history.reached[stage] = history.reached.get(stage, 0) + 1
                        if rec.get("stage") == fail:
                            history.eliminated[stage] = history.eliminated.get(stage, 0) + 1
    return history


def _history(history: Any) -> History:
    if history is None:
        return History()
    if isinstance(history, History):
        return history
    if isinstance(history, dict):
        return History(**history)
    raise TypeError("history is a History, a dict of its fields, or None")


def _calibration(calibration: Any) -> CalibrationSummary:
    if isinstance(calibration, CalibrationSummary):
        return calibration
    if isinstance(calibration, dict):
        return CalibrationSummary.from_dict(calibration)
    raise TypeError("calibration is a CalibrationSummary or a dict of its fields")


# ---------------------------------------------------------------- parameters
# Simulator inputs and their defaults (names follow the shared V4Config contract).
PARAM_DEFAULTS = dict(
    validation_size=None,            # None: |P|
    calibration_k=3,
    smoke=False, smoke_size=2,
    minibatch=False, minibatch_fraction=0.3, minibatch_min=10,
    b=3, cards_per_analysis=None,
    max_concurrent_executions=3,
    remeasure=True,
    time_budget_hours=None,          # required
    spend_budget=None,               # optional, in the unit of price_per_1k_tokens
    price_per_1k_tokens=None,
    agent_timeout=None,              # seconds; caps the slowest card
)


def normalize_params(params: dict, calibration: CalibrationSummary) -> dict:
    unknown = set(params) - set(PARAM_DEFAULTS)
    if unknown:
        raise ValueError(f"unknown simulator parameters: {sorted(unknown)}")
    out = dict(PARAM_DEFAULTS, **params)
    if out["validation_size"] is None:
        out["validation_size"] = calibration.n_probe
    if not isinstance(out["time_budget_hours"], (int, float)) or out["time_budget_hours"] <= 0:
        raise ValueError("simulate needs a positive time_budget_hours")
    for key in ("validation_size", "b", "max_concurrent_executions", "smoke_size", "minibatch_min", "calibration_k"):
        if type(out[key]) is not int or out[key] < 1:
            raise ValueError(f"{key} must be a positive integer")
    if out["cards_per_analysis"] is None:
        out["cards_per_analysis"] = out["b"] + 2
    for key in ("spend_budget", "price_per_1k_tokens"):
        value = out[key]
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                  or not math.isfinite(value) or value < 0):
            raise ValueError(f"{key} must be a finite nonnegative number")
    if out["spend_budget"] is not None and out["price_per_1k_tokens"] is None:
        raise ValueError("spend_budget needs price_per_1k_tokens; unknown prices cannot bound spend")
    return out


def minibatch_size(n_val: int, fraction: float, minimum: int) -> int:
    """Minibatch tasks for a validation set of n_val; 0 when it would be below the minimum."""
    m = int(math.ceil(fraction * n_val))
    return m if minimum <= m < n_val else 0


def resolution_se(calibration: CalibrationSummary, n_val: int, sigma2_het: float, k_parent: int) -> float:
    """SE_val ≈ √((σ²_het + σ²_r·(1 + 1/k_p)) / n_val)  (§3.4)."""
    return math.sqrt((sigma2_het + calibration.rerun_var * (1 + 1 / k_parent)) / max(1, n_val))


def _phi(x: float) -> float:
    return math.exp(-x * x / 2) / math.sqrt(2 * math.pi)


def _tail(x: float) -> float:
    return 0.5 * math.erfc(x / math.sqrt(2))


def noise_adoption_probability(c: float = C_ADOPT, remeasure: bool = True) -> float:
    """P(a candidate with no real effect is adopted) under the point-estimate rule.

    Without re-measure: P(Z₁ ≥ c). With re-measure the mean of two independent
    measurements must also pass: P(Z₁ ≥ c, (Z₁+Z₂)/√2 ≥ c). Approximate (the
    within-round selection of the best candidate is ignored).
    """
    if not remeasure:
        return _tail(c)
    steps, top = 2000, c + 8.0
    h = (top - c) / steps
    total = 0.0
    for i in range(steps + 1):
        z = c + i * h
        w = 0.5 if i in (0, steps) else 1.0
        total += w * _phi(z) * _tail(math.sqrt(2) * c - z)
    return total * h


# ---------------------------------------------------------------- simulate
def _scenario(p: dict, cal: CalibrationSummary, agent: float, agent_hi: float, analysis: float,
              q_smoke: float, q_mb: float, sigma2_het: float, budget_s: float) -> dict:
    n_val, b, e = p["validation_size"], p["b"], p["max_concurrent_executions"]
    m = minibatch_size(n_val, p["minibatch_fraction"], p["minibatch_min"]) if p["minibatch"] else 0
    smoke_s = cal.t_meas(p["smoke_size"]) if p["smoke"] else 0.0
    mb_s = cal.t_meas(m) if m else 0.0
    rest_s = cal.t_meas(n_val - m)
    q_s = q_smoke if p["smoke"] else 0.0
    q_m = q_mb if m else 0.0
    # Expected execution work of one card, and the work of a card that runs the full cascade.
    work = smoke_s + (1 - q_s) * (mb_s + (1 - q_m) * rest_s)
    full = smoke_s + mb_s + rest_s
    tasks = (p["smoke_size"] if p["smoke"] else 0) + (1 - q_s) * (m + (1 - q_m) * (n_val - m))
    # Slowest of b agents: quantile b/(b+1) between the median (0.5) and the p90, capped by the timeout.
    q = b / (b + 1)
    slow = agent + (agent_hi - agent) * min(1.0, max(0.0, (q - 0.5) / 0.4))
    if p["agent_timeout"]:
        slow = min(slow, float(p["agent_timeout"]))
    critical = slow + full
    wave_work = math.ceil(b / e) * work
    queued = agent + wave_work
    impl = max(critical, queued)
    remeasure_s = cal.t_meas(n_val) if p["remeasure"] else 0.0
    per_round = analysis + impl + remeasure_s + DECISION_S
    # Before round 1 the seed is measured once on the validation tasks outside P (§3.7).
    top_up = cal.t_meas(max(0, n_val - cal.n_probe))
    available = max(0.0, budget_s - top_up)
    rounds = int(available // per_round) if per_round > 0 else 0
    top_up_tokens = (cal.tokens_per_task or 0) * max(0, n_val - cal.n_probe)
    round_tokens = (cal.tokens_per_task or 0) * (b * tasks + (n_val if p["remeasure"] else 0))
    if p["spend_budget"] is not None:
        if cal.tokens_per_task is None:
            raise ValueError('spend_budget needs measured token usage; missing usage cannot be zero')
        unit_price = p["price_per_1k_tokens"] / 1000
        remaining = p["spend_budget"] - top_up_tokens * unit_price
        round_cost = round_tokens * unit_price
        if remaining < 0:
            rounds = 0
        elif round_cost > 0:
            rounds = min(rounds, int(remaining // round_cost))
    candidates = rounds * b
    se = resolution_se(cal, n_val, sigma2_het, p["calibration_k"])
    tokens = top_up_tokens + rounds * round_tokens
    shares = dict(agent=agent, execution=wave_work, wait=max(0.0, slow - agent) + full)
    return dict(per_round_s=per_round, rounds=rounds, candidates=candidates, se=se,
                detectable=(C_ADOPT + POWER_Z) * se, tokens=tokens, bottleneck=max(shares, key=shares.get),
                shares=shares, minibatch_size=m,
                stage_s=dict(analysis=analysis, smoke=smoke_s, minibatch=mb_s, rest=rest_s,
                             remeasure=remeasure_s, decision=DECISION_S, seed_top_up=top_up))


def _interval(values: list) -> dict:
    """(optimistic, mid, pessimistic) scenario values → dict(low, mid, high) with low ≤ mid ≤ high."""
    return dict(low=min(values), mid=values[1], high=max(values))


def sim_inputs(params: dict, calibration: Any, history: Any = None) -> dict:
    """The canonical inputs hashed into the sim_id."""
    cal = _calibration(calibration)
    p = normalize_params(params, cal)
    return dict(version=SIM_VERSION, params=p, calibration=cal.canonical(), history=_history(history).summary())


def sim_id_of(inputs: dict) -> str:
    blob = json.dumps(inputs, sort_keys=True, separators=(",", ":"), default=list)
    return "sim-" + hashlib.sha256(blob.encode()).hexdigest()[:16]


def simulate(params: dict, calibration: Any, history: Any = None) -> dict:
    """Predict one run under ``params`` (§3.5). Intervals are ``dict(low, mid, high)``.

    Returns ``sim_id, per_round_s, candidates, rounds, resolution, spend,
    bottleneck, assumptions`` (plus ``stages`` and ``noise_adoptions`` for the report).
    """
    cal = _calibration(calibration)
    hist = _history(history)
    inputs = sim_inputs(params, cal, hist)
    p = inputs["params"]
    (agent, agent_src), (analysis, analysis_src) = hist.agent_interval(), hist.analysis_interval()
    (q_smoke, smoke_src), (q_mb, mb_src) = hist.elimination("smoke"), hist.elimination("minibatch")
    budget_s = p["time_budget_hours"] * 3600
    # Optimistic, mid, pessimistic: fast agents + high elimination + small heterogeneity, and the reverse.
    scen = [
        _scenario(p, cal, agent[0], agent[1], analysis[0], q_smoke[2], q_mb[2], DEFAULT_SIGMA2_HET[0], budget_s),
        _scenario(p, cal, agent[1], agent[2], analysis[1], q_smoke[1], q_mb[1], DEFAULT_SIGMA2_HET[1], budget_s),
        _scenario(p, cal, agent[2], agent[2] * agent[2] / max(agent[1], 1e-9), analysis[2], q_smoke[0], q_mb[0],
                  DEFAULT_SIGMA2_HET[2], budget_s),
    ]

    def iv(key):
        return _interval([s[key] for s in scen])
    se, detectable = iv("se"), iv("detectable")
    tokens = iv("tokens") if cal.tokens_per_task is not None else None
    spend = dict(tokens=tokens, scope="measurement_only", agent_cost_included=False,
                 includes=["seed_validation_top_up", "forecast_round_measurements"],
                 excludes=["prior_calibration", "implementation_agents", "analysis_agents"])
    if p["price_per_1k_tokens"] is not None and tokens is not None:
        spend["cost"] = {k: v * p["price_per_1k_tokens"] / 1000 for k, v in tokens.items()}
        if p["spend_budget"] is not None:
            spend["within_budget"] = spend["cost"]["high"] <= p["spend_budget"]
    per_candidate = noise_adoption_probability(C_ADOPT, p["remeasure"])
    candidates = iv("candidates")
    assumptions = [
        dict(name="agent_s_per_card", value=agent, source=agent_src),
        dict(name="analysis_s_per_round", value=analysis, source=analysis_src),
        dict(name="sigma2_het", value=DEFAULT_SIGMA2_HET, source="assumed"),
    ]
    if p["smoke"]:
        assumptions.append(dict(name="smoke_elimination_rate", value=q_smoke, source=smoke_src))
    if p["minibatch"]:
        assumptions.append(dict(name="minibatch_elimination_rate", value=q_mb, source=mb_src))
    return dict(
        sim_id=sim_id_of(inputs),
        per_round_s=iv("per_round_s"),
        candidates=candidates,
        rounds=iv("rounds"),
        resolution=dict(n_val=p["validation_size"], k_parent=p["calibration_k"], se=se, detectable=detectable,
                        rule=f"detectable = ({C_ADOPT:g}+{POWER_Z})·SE_val: ~80% adoption probability"),
        spend=spend,
        bottleneck=scen[1]["bottleneck"],
        bottleneck_shares_s=scen[1]["shares"],
        stages=scen[1]["stage_s"],
        minibatch_size=scen[1]["minibatch_size"],
        noise_adoptions=dict(per_candidate=per_candidate,
                             expected={k: v * per_candidate for k, v in candidates.items()}),
        assumptions=assumptions,
        params=p,
    )
