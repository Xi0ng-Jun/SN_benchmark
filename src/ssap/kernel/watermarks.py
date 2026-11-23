"""Wall-clock watermarks and the deterministic terminal gate.

``T_warn`` (reminder), ``T_soft`` (Phase A stop), ``T_max`` (host hard limit), repair window
``Delta_T_rep`` / ``Delta_S_rep`` (seconds / turns), ``min_remaining_for_repair_sec``,
``tail_buffer_sec`` and ``kill_grace_sec``. ``fork_meta`` depends only on
``phase_a_elapsed_sec``, so live Phase B and offline Path 1 replay use identical trigger and
budget inputs::

    remaining = T_max - elapsed
    granted   = max(0, min(Delta_T_rep, T_max - elapsed - tail_buffer))
    eligible  = remaining >= min_remaining_for_repair and granted > 0
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Iterable, Sequence


@dataclass(frozen=True)
class Watermarks:
    t_max_sec: float = 10800.0
    t_warn_sec: float = 9000.0
    t_soft_sec: float = 9900.0
    delta_t_rep_sec: float = 900.0
    delta_s_rep_turns: int = 15
    min_remaining_for_repair_sec: float = 180.0
    tail_buffer_sec: float = 120.0
    kill_grace_sec: float = 5.0

    def __post_init__(self):
        if not (0 < self.t_warn_sec <= self.t_soft_sec <= self.t_max_sec):
            raise ValueError("watermarks need 0 < T_warn <= T_soft <= T_max")
        for name in ("delta_t_rep_sec", "min_remaining_for_repair_sec", "tail_buffer_sec",
                     "kill_grace_sec"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be >= 0")
        if self.delta_s_rep_turns < 1:
            raise ValueError("delta_s_rep_turns must be >= 1")

    @classmethod
    def from_mapping(cls, cfg: dict | None) -> "Watermarks":
        cfg = cfg or {}
        kw = {}
        for f in cls.__dataclass_fields__:
            if f in cfg and cfg[f] not in (None, ""):
                kw[f] = int(cfg[f]) if f == "delta_s_rep_turns" else float(cfg[f])
        return cls(**kw)

    @classmethod
    def from_json(cls, text: str) -> "Watermarks":
        return cls.from_mapping(json.loads(text))

    def as_dict(self) -> dict:
        return asdict(self)

    def fork_meta(self, phase_a_elapsed_sec: float) -> dict:
        e = round(max(0.0, float(phase_a_elapsed_sec)), 3)
        remaining = round(self.t_max_sec - e, 3)
        granted = round(max(0.0, min(self.delta_t_rep_sec, self.t_max_sec - e - self.tail_buffer_sec)), 3)
        return {"schema_version": 1, "phase_a_elapsed_sec": e, "remaining_sec": remaining,
                "granted_repair_budget_sec": granted, "granted_repair_turns": self.delta_s_rep_turns,
                "repair_eligible": bool(remaining >= self.min_remaining_for_repair_sec and granted > 0),
                "watermarks": self.as_dict()}

    def warn_due(self, elapsed_sec: float) -> bool:
        return elapsed_sec >= self.t_warn_sec

    def phase_a_timeout(self, elapsed_at_start_sec: float = 0.0) -> float:
        return max(0.0, self.t_soft_sec - elapsed_at_start_sec)


def compute_gate(fork_meta: dict, m_best: Sequence[int] | None, *, unevaluated_draft: bool,
                 advisory_triggers: Iterable[str] = (), terminal_m: Sequence[int] | None = None) -> dict:
    """``gate_report`` content: trigger reasons are (a) ``m(w_best) != 1`` (including registered
    terminal-only Class-1 gates), (b) an unevaluated new draft, (c) activated Class-2 triggers."""
    reasons: list[str] = []
    if m_best is None or not len(m_best) or any(v != 1 for v in m_best):
        reasons.append("m_best_not_all_certified")
    if terminal_m is not None and any(v != 1 for v in terminal_m):
        reasons.append("terminal_gate_not_certified")
    if unevaluated_draft:
        reasons.append("unevaluated_draft")
    reasons += [f"advisory:{a}" for a in sorted(set(advisory_triggers))]
    eligible = bool(fork_meta.get("repair_eligible"))
    return {"schema_version": 1, "gate_triggered": bool(reasons) and eligible,
            "trigger_reasons": reasons, "repair_eligible": eligible,
            "m_best": list(m_best) if m_best is not None else None,
            "phase_a_elapsed_sec": fork_meta.get("phase_a_elapsed_sec"),
            "granted_repair_budget_sec": fork_meta.get("granted_repair_budget_sec"),
            "granted_repair_turns": fork_meta.get("granted_repair_turns")}
