"""Zero-parameter mirror-certified ratchet order ``w_new > w_best`` (``docs/mcr-formal.tex`` Rules 1-2).

Inputs are certificates (``Cert``) produced by registered Class-1 mirror checkers:

* ``file_present``: Tier-0 bootstrap bit (regular, non-symlink, non-empty primary artifact);
* ``raw_u``: raw margins ``tilde u_j`` per mirror component (``None`` = not evaluated, e.g. a
  prerequisite failed); ``m`` and ``u`` are derived here from ``raw_u`` and the guard bands
  ``eps``: ``m_j = 1[raw_u_j - eps_j >= 0]`` and ``u_j = min(0, raw_u_j - eps_j)``, i.e. margins
  saturate at ``0.0`` once certified and stay continuous (``< 0``) while uncertified;
* ``y_hat_mir``: optional concordance-verified quality mirror;
* ``advisory_flags``: Class-2 output, carried for reporting only. It never enters ``m`` or ``u``
  and no function in this module reads it (zero overwrite authority).

Order (``TierSpec.tiers``, e.g. ``[[0], [1], [2, 3, 4, 5]]``):

0. Tier 0: against an empty ``best`` any present candidate promotes; an absent candidate never
   replaces a present ``best``.
1. Tiers 1..K in order: a singleton tier compares lexicographically; a multi-index tier is a
   Pareto order (all ``>=`` and one ``>``). A strict improvement promotes, a regression or an
   incomparable pair keeps ``best``; equality falls through.
2. Same ``m``: (a) a strictly higher ``y_hat_mir`` promotes (a thinner certified raw margin is
   flagged); (b) otherwise (unavailable or tied) promote iff ``u_new >= u_best`` component-wise
   and either every component is certified or some ``u`` strictly improves (Rule 2(b) ``>=``).

A candidate whose check timed out is never a decision (``check_timeout_no_decision``).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Sequence

NEG_INF = float("-inf")


@dataclass(frozen=True)
class TierSpec:
    tiers: tuple[tuple[int, ...], ...]
    eps: tuple[float, ...]
    names: tuple[str, ...] = ()

    def __post_init__(self):
        flat = [i for t in self.tiers for i in t]
        if sorted(flat) != list(range(len(self.eps))):
            raise ValueError("tiers must partition the mirror indices 0..M-1 exactly once")
        if any(not math.isfinite(e) or e < 0 for e in self.eps):
            raise ValueError("guard bands eps_j must be finite and >= 0")
        if self.names and len(self.names) != len(self.eps):
            raise ValueError("names must match the mirror vector length")

    @property
    def size(self) -> int:
        return len(self.eps)

    @classmethod
    def from_lists(cls, tiers: Sequence[Sequence[int]], eps: Sequence[float],
                   names: Sequence[str] = ()) -> "TierSpec":
        return cls(tuple(tuple(t) for t in tiers), tuple(float(e) for e in eps), tuple(names))

    def as_dict(self) -> dict:
        return {"tiers": [list(t) for t in self.tiers], "eps": list(self.eps), "names": list(self.names)}


def certify(raw_u: Sequence[float | None], eps: Sequence[float]) -> tuple[tuple[int, ...], tuple[float, ...]]:
    """``(m, u)`` from raw margins and guard bands."""
    if len(raw_u) != len(eps):
        raise ValueError("raw_u and eps lengths differ")
    m, u = [], []
    for r, e in zip(raw_u, eps):
        if r is None or not isinstance(r, (int, float)) or math.isnan(r):
            m.append(0)
            u.append(NEG_INF)
            continue
        d = float(r) - e
        m.append(1 if d >= 0 else 0)
        u.append(min(0.0, d))
    return tuple(m), tuple(u)


@dataclass
class Cert:
    file_present: bool
    raw_u: tuple[float | None, ...] = ()
    y_hat_mir: float | None = None
    check_timed_out: bool = False
    advisory_flags: dict[str, Any] = field(default_factory=dict)
    artifact_sha256: str | None = None
    aux_tree_sha256: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    eps: tuple[float, ...] = ()

    @classmethod
    def build(cls, file_present: bool, raw_u: Sequence[float | None], tier_spec: TierSpec,
              **kw) -> "Cert":
        return cls(file_present=bool(file_present), raw_u=tuple(raw_u), eps=tier_spec.eps, **kw)

    def _eps(self, tier_spec: TierSpec | None = None) -> tuple[float, ...]:
        if tier_spec is not None:
            return tier_spec.eps
        return self.eps or tuple(0.0 for _ in self.raw_u)

    def m(self, tier_spec: TierSpec | None = None) -> tuple[int, ...]:
        if not self.file_present:
            return tuple(0 for _ in self.raw_u)
        return certify(self.raw_u, self._eps(tier_spec))[0]

    def u(self, tier_spec: TierSpec | None = None) -> tuple[float, ...]:
        if not self.file_present:
            return tuple(NEG_INF for _ in self.raw_u)
        return certify(self.raw_u, self._eps(tier_spec))[1]

    def as_dict(self, tier_spec: TierSpec | None = None) -> dict:
        def num(x):
            return None if x is None or not math.isfinite(x) else x
        return {"schema_version": 1, "file_present": self.file_present,
                "m": list(self.m(tier_spec)), "u": [num(x) for x in self.u(tier_spec)],
                "raw_u": [num(x) for x in self.raw_u], "eps": list(self._eps(tier_spec)),
                "y_hat_mir": self.y_hat_mir, "check_timed_out": self.check_timed_out,
                "advisory_flags": dict(self.advisory_flags),
                "artifact_sha256": self.artifact_sha256, "aux_tree_sha256": self.aux_tree_sha256,
                "details": self.details}

    @classmethod
    def from_dict(cls, d: dict) -> "Cert":
        return cls(file_present=bool(d.get("file_present")),
                   raw_u=tuple(d.get("raw_u") or ()), y_hat_mir=d.get("y_hat_mir"),
                   check_timed_out=bool(d.get("check_timed_out")),
                   advisory_flags=dict(d.get("advisory_flags") or {}),
                   artifact_sha256=d.get("artifact_sha256"),
                   aux_tree_sha256=d.get("aux_tree_sha256"),
                   details=dict(d.get("details") or {}), eps=tuple(d.get("eps") or ()))


@dataclass(frozen=True)
class Decision:
    promote: bool
    reason: str
    thin_margin: bool = False
    tier: int | None = None

    def __iter__(self):            # ``promote, reason = decide(...)``
        yield self.promote
        yield self.reason

    def as_dict(self) -> dict:
        return {"promote": self.promote, "reason": self.reason, "thin_margin": self.thin_margin,
                "tier": self.tier}


def _compare_tier(c: Sequence[int], b: Sequence[int]) -> int | None:
    """``1`` strictly better, ``-1`` strictly worse, ``0`` equal, ``None`` incomparable."""
    ge = all(x >= y for x, y in zip(c, b))
    le = all(x <= y for x, y in zip(c, b))
    if ge and le:
        return 0
    if ge:
        return 1
    if le:
        return -1
    return None


def decide(cand: Cert, best: Cert | None, tier_spec: TierSpec) -> Decision:
    if cand.check_timed_out:
        return Decision(False, "check_timeout_no_decision")
    if best is None or not best.file_present:
        if cand.file_present:
            return Decision(True, "tier0_bootstrap", tier=0)
        return Decision(False, "tier0_no_artifact", tier=0)
    if not cand.file_present:
        return Decision(False, "tier0_regression", tier=0)
    for c in (cand, best):
        if len(c.raw_u) != tier_spec.size:
            raise ValueError("certificate length differs from the tier spec")
    mc, mb = cand.m(tier_spec), best.m(tier_spec)
    for k, tier in enumerate(tier_spec.tiers, 1):
        cmp = _compare_tier([mc[i] for i in tier], [mb[i] for i in tier])
        if cmp == 1:
            return Decision(True, "tier_lex" if len(tier) == 1 else "tier_pareto", tier=k)
        if cmp == -1:
            return Decision(False, "tier_regression", tier=k)
        if cmp is None:
            return Decision(False, "tier_incomparable", tier=k)
    uc, ub = cand.u(tier_spec), best.u(tier_spec)
    certified = [j for j, v in enumerate(mc) if v == 1]
    thin = any(_lt(cand.raw_u[j], best.raw_u[j]) for j in certified)
    if cand.y_hat_mir is not None and best.y_hat_mir is not None:
        if cand.y_hat_mir > best.y_hat_mir and all(uc[j] >= 0 for j in certified):
            return Decision(True, "same_tier_quality_mirror", thin_margin=thin)
        if cand.y_hat_mir < best.y_hat_mir:
            return Decision(False, "same_tier_quality_lower")
    if not all(a >= b for a, b in zip(uc, ub)):
        return Decision(False, "same_tier_margin_regression")
    if all(v == 1 for v in mc) or any(a > b for a, b in zip(uc, ub)):
        return Decision(True, "same_tier_margin", thin_margin=thin)
    return Decision(False, "same_tier_no_improvement")


def _lt(a, b) -> bool:
    return a is not None and b is not None and float(a) < float(b)


def is_strictly_better(cand: Cert, best: Cert | None, tier_spec: TierSpec) -> tuple[bool, str]:
    d = decide(cand, best, tier_spec)
    return d.promote, d.reason
