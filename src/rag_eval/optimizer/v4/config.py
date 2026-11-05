"""method-v4 run configuration: user inputs (§2.1) and method constants (§2.2).

One JSON file holds everything; unknown keys are refused, every value is part
of the pre-registration (its sha256 is recorded and a resume must match).
Changing a method constant from its default is a "method variant" and is
listed as such in the report (``variants``).
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

# §2.1 default safeguard tolerances; other protocol safeguards use their own pre-registered margin.
DEFAULT_TOLERANCES = {"infrastructure_failure_rate": 0.05, "cost_per_task": 0.5}


@dataclass(frozen=True)
class V4Config:
    # --- user inputs (§2.1) ---
    time_budget_hours: float                 # wall clock of the optimisation (endpoint excluded)
    min_meaningful_improvement: Optional[float] = None   # feasibility card only, never a decision input
    # --- method constants (§2.2) ---
    b: int = 4                               # parallel implementations per round
    cards_per_analysis: Optional[int] = None  # None: 2b
    smoke_size: int = 3
    smoke_repair_attempts: int = 1
    smoke_behaviour_check: bool = True
    minibatch_fraction: float = 0.25
    minibatch_min: int = 10
    c: float = 1.0                           # adoption threshold, mechanism not confirmed
    c_mech: float = 0.5                      # adoption threshold, mechanism confirmed
    minibatch_kill: float = 1.0              # stage 1: Δ_mb < -kill·SE_mb is eliminated
    review: bool = True
    review_pairs: int = 6                    # candidate/parent pairs shown to the reviewer
    review_controls: int = 2                 # parent/parent (A/A) control pairs
    review_min_effect: int = 4               # of review_pairs judged "took effect" → effective
    review_max_control_effect: int = 1       # more control pairs judged "took effect" → untrusted
    no_adoption_rounds: int = 3              # R: halt after R consecutive rounds without adoption
    max_consecutive_run_failures: int = 3
    merge: bool = True
    ablation: bool = True                    # §4.2 pruning before the endpoint (skipped when time is short)
    ablation_kill: float = 1.0               # removing a change costs < ablation_kill·SE → pruned
    tie_fraction: float = 0.5                # Δ within tie_fraction·SE of the best: smaller diff wins
    calibration_k: int = 2                   # A/A repetitions (also k of search-set diagnosis)
    k_val: Optional[int] = None              # None: calibration decides (1 or 2)
    max_rounds: int = 50
    round_timebox_hours: Optional[float] = None
    safeguard_tolerances: dict = field(default_factory=lambda: dict(DEFAULT_TOLERANCES))
    unobserved_safeguard_blocks: bool = True  # an unobserved safeguard blocks adoption (never kills a minibatch)
    endpoint_nodes: str = "seed_final"       # or "lineage": every adopted node
    # Analyst case selection (fixed accountant rule, as in v3).
    analyst_failure_threshold: float = 0.5
    analyst_max_failures: int = 12
    analyst_n_controls: int = 3
    # Feasibility card: assumed Agent time of one candidate (analysis share + implementation).
    agent_estimate_minutes: float = 45.0

    @property
    def n_cards(self) -> int:
        return self.cards_per_analysis or 2 * self.b

    def variants(self) -> dict:
        """Method constants that differ from their defaults (§2.2: each is a method variant)."""
        user = {"time_budget_hours", "min_meaningful_improvement", "max_rounds", "agent_estimate_minutes"}
        out = {}
        for f in dataclasses.fields(self):
            if f.name in user:
                continue
            default = f.default if f.default is not dataclasses.MISSING else f.default_factory()
            if getattr(self, f.name) != default:
                out[f.name] = dict(value=getattr(self, f.name), default=default)
        return out


# Adapter settings (SN): part of the same file under "adapter".
ADAPTER_DEFAULTS: dict[str, Any] = dict(
    analyst_profile="pi-flash", implementer_profile="pi-flash", reviewer_profile=None,
    agent_poll_interval=10.0, agent_timeout=3600.0, worktree_timeout=600.0,
    analyst_view_budget=12000, reviewer_view_budget=30000,
    eval_poll_interval=30.0, eval_timeout=7200.0, infra_retry=1, execution_timeout_seconds=7200,
)


class ConfigError(ValueError):
    pass


def _check(name: str, value: Any, default: Any, kind: Any) -> None:
    def bad(what):
        return ConfigError(f"v4 config key {name!r} must be {what}, got {value!r}")
    if kind in (bool, "bool"):
        if type(value) is not bool:
            raise bad("true or false")
    elif kind in (int, "int"):
        if type(value) is not int:
            raise bad("an integer")
    elif kind in (float, "float"):
        if type(value) not in (int, float):
            raise bad("a number")
    elif kind in (str, "str"):
        if type(value) is not str or not value:
            raise bad("a non-empty string")
    elif kind in (dict, "dict"):
        if not isinstance(value, dict):
            raise bad("an object")
    elif kind in ("Optional[int]",):
        if value is not None and type(value) is not int:
            raise bad("an integer or null")
    elif kind in ("Optional[float]",):
        if value is not None and type(value) not in (int, float):
            raise bad("a number or null")
    elif kind in ("Optional[str]",):
        if value is not None and (type(value) is not str or not value):
            raise bad("a string or null")


def validate(cfg: V4Config) -> V4Config:
    positive = ("b", "smoke_size", "minibatch_min", "review_pairs", "no_adoption_rounds",
                "max_consecutive_run_failures", "calibration_k", "max_rounds")
    for name in positive:
        if getattr(cfg, name) < 1:
            raise ConfigError(f"{name} must be >= 1")
    if cfg.time_budget_hours <= 0:
        raise ConfigError("time_budget_hours must be > 0")
    if not 0 < cfg.minibatch_fraction < 1:
        raise ConfigError("minibatch_fraction must be in (0, 1)")
    for name in ("c", "c_mech", "minibatch_kill", "ablation_kill", "tie_fraction", "smoke_repair_attempts",
                 "review_controls"):
        if getattr(cfg, name) < 0:
            raise ConfigError(f"{name} must be >= 0")
    if cfg.c_mech > cfg.c:
        raise ConfigError("c_mech must not exceed c (mechanism evidence only relaxes the threshold)")
    if cfg.k_val is not None and cfg.k_val not in (1, 2):
        raise ConfigError("k_val must be 1, 2 or null (calibration decides)")
    if cfg.cards_per_analysis is not None and cfg.cards_per_analysis < 1:
        raise ConfigError("cards_per_analysis must be >= 1 or null")
    if not 0 < cfg.review_min_effect <= cfg.review_pairs:
        raise ConfigError("review_min_effect must be in [1, review_pairs]")
    if cfg.endpoint_nodes not in ("seed_final", "lineage"):
        raise ConfigError("endpoint_nodes must be 'seed_final' or 'lineage'")
    for key, tol in cfg.safeguard_tolerances.items():
        if type(tol) not in (int, float) or tol < 0:
            raise ConfigError(f"safeguard tolerance {key!r} must be a number >= 0")
    return cfg


@dataclass(frozen=True)
class LoadedConfig:
    config: V4Config
    adapter_settings: dict
    sha256: str
    resolved: dict


def load(path: str | Path) -> LoadedConfig:
    path = Path(path)
    try:
        raw = path.read_bytes()
        data = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigError(f"cannot read v4 config {path}: {exc}") from None
    if not isinstance(data, dict):
        raise ConfigError("v4 config must hold a JSON object")
    adapter = data.pop("adapter", {})
    if not isinstance(adapter, dict):
        raise ConfigError("'adapter' must be an object")
    fields = {f.name: f for f in dataclasses.fields(V4Config)}
    unknown = sorted(set(data) - set(fields)) + [f"adapter.{k}" for k in sorted(set(adapter) - set(ADAPTER_DEFAULTS))]
    if unknown:
        raise ConfigError(f"unknown v4 config key(s): {', '.join(unknown)}")
    if "time_budget_hours" not in data:
        raise ConfigError("missing v4 config key: time_budget_hours")
    for name, value in data.items():
        f = fields[name]
        default = f.default if f.default is not dataclasses.MISSING else (
            f.default_factory() if f.default_factory is not dataclasses.MISSING else None)
        _check(name, value, default, f.type)
    if "safeguard_tolerances" in data:
        data["safeguard_tolerances"] = dict(DEFAULT_TOLERANCES, **data["safeguard_tolerances"])
    for name, value in adapter.items():
        default = ADAPTER_DEFAULTS[name]
        kind = "Optional[str]" if default is None else type(default)
        _check(f"adapter.{name}", value, default, kind)
    cfg = validate(V4Config(**data))
    settings = dict(ADAPTER_DEFAULTS, **adapter)
    resolved = dict(dataclasses.asdict(cfg), adapter=settings)
    return LoadedConfig(cfg, settings, hashlib.sha256(raw).hexdigest(), resolved)


def digest(cfg: V4Config) -> str:
    return hashlib.sha256(json.dumps(dataclasses.asdict(cfg), sort_keys=True).encode()).hexdigest()
