"""``infra_error`` classification, the infrastructure ledger and the ``delta_worst`` lower bound.

A trial outcome is either *scored* (task outcome, including task failure) or an *infrastructure
fault* (``infra_error``). Infrastructure faults are not task failures: they are re-run under the
paired rule (both arms of the slot), and residual unresolved slots are reported with the
``delta_worst`` sensitivity bound. Task failures are scored, never dropped:

* ``missing_submission`` / ``invalid_submission``  -> ``Y* = 0.0`` unless the verifier scored it;
* a ``T_soft`` intentional stop (``SIGTERM`` sent by the harness, possibly followed by the grace
  ``SIGKILL``) is a normal end of Phase A, not an OOM.

Infrastructure categories (first match wins):

``oom``                exit code 137 (or ``oom_killed``) not caused by the intentional ``T_soft`` stop
``setup_crash``        Docker / Harbor setup failure (``no_trial_dir`` / ``container_error``)
``verifier_fault``     ``evaluation_error`` or ``status in {tool_error, interrupted}`` (not a
                       submission failure), or no readable score
``gateway_error``      unrecovered upstream 5xx / 429
``no_model_traffic``   ``proxy_requests == 0`` on an LLM arm
``gateway_stall``      ``gateway_wait_seconds / wall_seconds > theta_gw_max`` (default 0.35)

Trial record keys (all optional): ``run_id, arm, task_id, seed, Y_star, C_star, status,
evaluation_error, exit_code, oom_killed, no_trial_dir, container_error, stop_reason, signal,
proxy_requests, unrecovered_gateway_error, gateway_wait_seconds, wall_sec, llm_arm``.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..targets.alw.harbor_runner import infra_reasons
from ..targets.alw.metrics_reader import INFRA_STATUSES, TASK_FAILURE_STATUSES

THETA_GW_MAX = 0.35
CATEGORIES = ("oom", "setup_crash", "verifier_fault", "gateway_error", "no_model_traffic",
              "gateway_stall")
INTENTIONAL_STOPS = ("t_soft", "T_soft", "phase_a_t_soft")
RERUN_STATUSES = ("pending", "resolved", "unresolved")
LLM_ARMS_DEFAULT = ("H_raw", "H_base", "H_star", "H_inc", "H_cand", "manual_instructions")


@dataclass
class InfraVerdict:
    infra_error: bool
    category: str | None
    reasons: list[str] = field(default_factory=list)
    score: float | None = None          # scored Y* (None iff infra_error)
    task_failure: bool = False
    intentional_stop: bool = False
    gateway_wait_ratio: float | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def _num(v: Any) -> float | None:
    return float(v) if type(v) in (int, float) and math.isfinite(v) else None


def intentional_stop(rec: Mapping) -> bool:
    return rec.get("stop_reason") in INTENTIONAL_STOPS or rec.get("t_soft_stop") is True


def gateway_wait_ratio(rec: Mapping) -> float | None:
    wall = _num(rec.get("wall_sec", rec.get("wall_seconds")))
    wait = _num(rec.get("gateway_wait_seconds"))
    if wall is None or wall <= 0 or wait is None:
        return None
    return wait / wall


def is_llm_arm(rec: Mapping, llm_arms: Iterable[str] = LLM_ARMS_DEFAULT) -> bool:
    if "llm_arm" in rec:
        return bool(rec["llm_arm"])
    return rec.get("arm") in tuple(llm_arms)


def classify_trial(rec: Mapping, *, theta_gw_max: float = THETA_GW_MAX,
                   llm_arms: Iterable[str] = LLM_ARMS_DEFAULT) -> InfraVerdict:
    """Classify one trial record (see module docstring)."""
    status = rec.get("status")
    task_failure = status in TASK_FAILURE_STATUSES
    stop = intentional_stop(rec)
    ratio = gateway_wait_ratio(rec)
    reasons: list[str] = []
    cats: list[str] = []

    exit_code = rec.get("exit_code")
    if rec.get("oom_killed") is True or (exit_code == 137 and not stop):
        cats.append("oom")
        reasons.append("oom_exit_137")
    if rec.get("no_trial_dir") is True or rec.get("container_error") is True:
        cats.append("setup_crash")
        reasons.append("no_trial_dir" if rec.get("no_trial_dir") else "container_error")
    if not task_failure:
        if rec.get("evaluation_error") is True:
            cats.append("verifier_fault")
            reasons.append("evaluation_error")
        elif status in INFRA_STATUSES:
            cats.append("verifier_fault")
            reasons.append(f"status_{status}")
    llm = is_llm_arm(rec, llm_arms)
    stats = {"unrecovered_gateway_error": rec.get("unrecovered_gateway_error"),
             # an unmetered record (no proxy_requests key) or a non-LLM arm expects no traffic
             "proxy_requests": rec.get("proxy_requests", 1) if llm else 1,
             "gateway_wait_seconds": rec.get("gateway_wait_seconds")}
    wall = _num(rec.get("wall_sec", rec.get("wall_seconds")))
    for r in infra_reasons(stats, wall_sec=wall, theta_gw_max=theta_gw_max):
        if r == "unrecovered_gateway_error":
            cats.append("gateway_error")
        elif r == "proxy_requests_zero":
            cats.append("no_model_traffic")
        elif r == "gateway_wait_fraction_exceeded":
            cats.append("gateway_stall")
        reasons.append(r)

    y = _num(rec.get("Y_star"))
    if not cats and y is None:
        if task_failure:
            y = 0.0                           # scored task failure, never dropped
        else:
            cats.append("verifier_fault")
            reasons.append("score_missing")
    if cats:
        return InfraVerdict(True, cats[0], reasons, None, task_failure, stop, ratio)
    return InfraVerdict(False, None, [], y, task_failure, stop, ratio)


def slot_key(rec: Mapping) -> tuple[str, int]:
    return str(rec.get("task_id")), int(rec.get("seed", 0))


class InfraLedger:
    """``infra_ledger.jsonl``: one line per infra event, plus paired-rerun status updates."""

    def __init__(self, path: str | Path | None = None, *, theta_gw_max: float = THETA_GW_MAX):
        self.path = Path(path) if path is not None else None
        self.theta_gw_max = float(theta_gw_max)
        self.entries: list[dict] = []
        if self.path is not None and self.path.is_file():
            self.entries = [json.loads(l) for l in self.path.read_text().splitlines() if l.strip()]

    def _append(self, entry: dict) -> dict:
        self.entries.append(entry)
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a") as fh:
                fh.write(json.dumps(entry, sort_keys=True) + "\n")
        return entry

    def observe(self, rec: Mapping) -> InfraVerdict:
        """Classify *rec*; an infra fault is recorded with ``paired_rerun_status = pending``."""
        v = classify_trial(rec, theta_gw_max=self.theta_gw_max)
        if v.infra_error:
            task, seed = slot_key(rec)
            self._append({"kind": "infra_error", "run_id": rec.get("run_id"), "arm": rec.get("arm"),
                          "task_id": task, "seed": seed, "category": v.category,
                          "reasons": v.reasons, "exit_code": rec.get("exit_code"),
                          "gateway_wait_ratio": v.gateway_wait_ratio,
                          "paired_rerun_status": "pending"})
        return v

    def mark_rerun(self, task_id: str, seed: int, status: str, *, run_ids: Iterable[str] = ()) -> dict:
        if status not in RERUN_STATUSES:
            raise ValueError(f"status must be one of {RERUN_STATUSES}")
        return self._append({"kind": "paired_rerun", "task_id": str(task_id), "seed": int(seed),
                             "paired_rerun_status": status, "run_ids": list(run_ids)})

    def slot_status(self) -> dict[tuple[str, int], str]:
        """Latest paired-rerun status per ``(task_id, seed)`` slot that ever had an infra fault."""
        out: dict[tuple[str, int], str] = {}
        for e in self.entries:                    # the latest entry of a slot wins
            out[(e["task_id"], int(e["seed"]))] = e["paired_rerun_status"]
        return out

    def unresolved_slots(self) -> set[tuple[str, int]]:
        return {k for k, s in self.slot_status().items() if s != "resolved"}

    def table(self) -> list[dict]:
        return [e for e in self.entries if e["kind"] == "infra_error"]


def paired_rerun_plan(records_by_arm: Mapping[str, Iterable[Mapping]], *,
                      theta_gw_max: float = THETA_GW_MAX) -> list[dict]:
    """Slots ``(task_id, seed)`` where any arm had an ``infra_error``: all arms of the slot are
    re-run under the same pre-registered rule."""
    bad: dict[tuple[str, int], set[str]] = {}
    arms = list(records_by_arm)
    for arm, recs in records_by_arm.items():
        for r in recs:
            if classify_trial(r, theta_gw_max=theta_gw_max).infra_error:
                bad.setdefault(slot_key(r), set()).add(arm)
    return [{"task_id": t, "seed": s, "faulted_arms": sorted(a), "rerun_arms": arms}
            for (t, s), a in sorted(bad.items())]


def delta_worst(cand: Mapping[tuple[str, int], float | None],
                base: Mapping[tuple[str, int], float | None], *,
                y_min: float = 0.0, y_max: float = 1.0) -> dict:
    """Paired observed difference (resolved slots) and its worst-case sensitivity lower bound:
    every slot with an unresolved ``infra_error`` in either arm counts as ``Y_cand = y_min`` and
    ``Y_base = y_max``. Means are over all slots of the union."""
    slots = sorted(set(cand) | set(base))
    if not slots:
        return {"n_slots": 0, "n_unresolved": 0, "delta_observed": None, "delta_worst": None,
                "unresolved_slots": []}
    obs, worst, unresolved = [], [], []
    for k in slots:
        c, b = cand.get(k), base.get(k)
        if c is None or b is None:
            unresolved.append(list(k))
            worst.append(y_min - y_max)
        else:
            obs.append(c - b)
            worst.append(c - b)
    return {"n_slots": len(slots), "n_unresolved": len(unresolved),
            "delta_observed": math.fsum(obs) / len(obs) if obs else None,
            "delta_worst": math.fsum(worst) / len(worst), "unresolved_slots": unresolved,
            "assumption": f"unresolved slot: candidate {y_min}, H_base {y_max}"}


def scores_by_slot(records: Iterable[Mapping], *, theta_gw_max: float = THETA_GW_MAX
                   ) -> dict[tuple[str, int], float | None]:
    """Scored ``Y*`` per slot (``None`` = infra fault). A later record of the same slot (a paired
    rerun) replaces an earlier one."""
    out: dict[tuple[str, int], float | None] = {}
    for r in records:
        out[slot_key(r)] = classify_trial(r, theta_gw_max=theta_gw_max).score
    return out
