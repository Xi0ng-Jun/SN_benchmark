"""Persistent Active Referee Bank ``B_dev`` (append-only JSONL).

A sample is ``(sample_id, task_id, cluster_id, source_kind, Y_star, C_star, d_star, cert_dict,
artifact_path)`` from a ``dev`` execution (trajectory snapshot, terminal submission or a
0-token synthetic variant). Index sets per sub-criterion ``k``:

* ``S_plus``    = ``{C_star == 1}``;
* ``S_plus_k``  = ``{d_star[k] == 1}``;
* ``S_minus_k`` = ``{d_star[k] == 0}`` not flagged by the incumbent (residual negatives).

``d_star[k] == "bottom"`` (not evaluated) never enters ``S_plus_k`` or ``S_minus_k``. Coverage is
counted in distinct ``dev`` clusters (at most ``G_dev <= 10``), never in samples.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

BOTTOM = "bottom"
D_KEYS = ("gds", "drc", "lvs", "shape", "fill", "morph", "pex", "postsim")
SOURCE_KINDS = ("trajectory", "terminal", "reference", "synthetic_pos", "synthetic_neg")
# Mirror checker -> verifier sub-criterion, and its index in m (registry order).
CHECKER_D_KEY = {"m_gds": "gds", "m_lvs": "lvs", "m_shape": "shape", "m_fill": "fill",
                 "m_morph": "morph", "m_drc": "drc", "m_pex": "pex"}
MIRROR_ORDER = ("m_gds", "m_lvs", "m_shape", "m_fill", "m_morph", "m_drc")
G_DEV_MAX = 10


def d_key(k: str) -> str:
    key = CHECKER_D_KEY.get(k, k)
    if key not in D_KEYS:
        raise KeyError(f"unknown sub-criterion {k!r}")
    return key


@dataclass
class BankSample:
    sample_id: str
    task_id: str
    cluster_id: str
    source_kind: str
    Y_star: float | None
    C_star: int | None
    d_star: dict[str, Any]
    cert_dict: dict = field(default_factory=dict)
    artifact_path: str | None = None
    meta: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.source_kind not in SOURCE_KINDS:
            raise ValueError(f"source_kind must be one of {SOURCE_KINDS}")
        for k, v in self.d_star.items():
            if k not in D_KEYS or v not in (0, 1, BOTTOM):
                raise ValueError(f"d_star[{k!r}] = {v!r}: must be 0, 1 or {BOTTOM!r}")
        if self.C_star not in (None, 0, 1):
            raise ValueError("C_star must be 0, 1 or None")
        if self.Y_star is not None and not math.isfinite(float(self.Y_star)):
            raise ValueError("Y_star must be finite or None")

    def d(self, k: str) -> Any:
        return self.d_star.get(d_key(k), BOTTOM)

    def m(self, checker: str) -> int | None:
        """Incumbent certificate component ``m_k`` recorded in ``cert_dict`` (None if absent)."""
        m = self.cert_dict.get("m")
        names = self.cert_dict.get("mirror_names") or MIRROR_ORDER
        if not isinstance(m, list) or checker not in names:
            return None
        i = list(names).index(checker)
        return m[i] if i < len(m) else None

    def raw_u(self, checker: str) -> float | None:
        r = self.cert_dict.get("raw_u")
        names = self.cert_dict.get("mirror_names") or MIRROR_ORDER
        if not isinstance(r, list) or checker not in names:
            return None
        i = list(names).index(checker)
        return r[i] if i < len(r) else None

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Mapping) -> "BankSample":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


class RefereeBank:
    """``B_dev``. ``dev_clusters`` (if given) restricts admission to dev clusters."""

    def __init__(self, path: str | Path | None = None, *, dev_clusters: Iterable[str] | None = None):
        self.path = Path(path) if path is not None else None
        self.dev_clusters = tuple(dev_clusters) if dev_clusters is not None else None
        if self.dev_clusters is not None and len(set(self.dev_clusters)) > G_DEV_MAX:
            raise ValueError(f"G_dev must be <= {G_DEV_MAX}")
        self._samples: dict[str, BankSample] = {}
        if self.path is not None and self.path.is_file():
            for line in self.path.read_text().splitlines():
                if line.strip():
                    s = BankSample.from_dict(json.loads(line))
                    self._samples[s.sample_id] = s

    def __len__(self) -> int:
        return len(self._samples)

    def __iter__(self):
        return iter(self._samples.values())

    def get(self, sample_id: str) -> BankSample:
        return self._samples[sample_id]

    def add(self, sample: BankSample) -> bool:
        """Append *sample*; returns ``False`` for an identical duplicate, raises on a conflict."""
        if self.dev_clusters is not None and sample.cluster_id not in self.dev_clusters:
            raise ValueError(f"cluster {sample.cluster_id!r} is not a dev cluster (val/test sealed)")
        old = self._samples.get(sample.sample_id)
        if old is not None:
            if old.as_dict() == sample.as_dict():
                return False
            raise ValueError(f"sample {sample.sample_id!r} already stored with different content")
        self._samples[sample.sample_id] = sample
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a") as fh:
                fh.write(json.dumps(sample.as_dict(), sort_keys=True, default=str) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
        return True

    # -- index sets -----------------------------------------------------------------------------
    def s_plus(self) -> list[BankSample]:
        return [s for s in self if s.C_star == 1]

    def s_plus_k(self, k: str) -> list[BankSample]:
        return [s for s in self if s.d(k) == 1]

    def s_minus_k(self, k: str, incumbent_flags: Callable[[BankSample], bool] | Mapping | None = None
                  ) -> list[BankSample]:
        """``d*_k == 0`` and not flagged by the incumbent. Default incumbent flag: the stored
        certificate has ``m_k == 0`` for mirror checkers (unknown -> not flagged)."""
        def flagged(s: BankSample) -> bool:
            if incumbent_flags is None:
                return s.m(k) == 0 if k in MIRROR_ORDER else False
            if callable(incumbent_flags):
                return bool(incumbent_flags(s))
            return bool(incumbent_flags.get(s.sample_id))
        return [s for s in self if s.d(k) == 0 and not flagged(s)]

    def bottom_k(self, k: str) -> list[BankSample]:
        return [s for s in self if s.d(k) == BOTTOM]

    # -- coverage -------------------------------------------------------------------------------
    @staticmethod
    def clusters_of(samples: Iterable[BankSample]) -> set[str]:
        return {s.cluster_id for s in samples}

    def coverage(self, samples: Iterable[BankSample]) -> dict:
        cl = self.clusters_of(samples)
        if self.dev_clusters is not None:
            cl &= set(self.dev_clusters)
        denom = len(set(self.dev_clusters)) if self.dev_clusters is not None else G_DEV_MAX
        n = min(len(cl), denom, G_DEV_MAX)
        return {"clusters_covered": n, "G_dev": min(denom, G_DEV_MAX)}

    def coverage_report(self, k: str, incumbent_flags=None) -> dict:
        return {"S_plus": self.coverage(self.s_plus()),
                "S_plus_k": self.coverage(self.s_plus_k(k)),
                "S_minus_k": self.coverage(self.s_minus_k(k, incumbent_flags)),
                "n_S_plus_k": len(self.s_plus_k(k)),
                "n_S_minus_k": len(self.s_minus_k(k, incumbent_flags)),
                "n_bottom_k": len(self.bottom_k(k))}
