"""Harvest a staged / launched Harbor job directory into analysis inputs (``ssap alw-harvest``).

Input: ``<job_dir>/launch_results.json`` (``launch_plan.json`` when the launch results are absent:
no proxy statistics, no exit codes) and, per job, the Harbor tree ``<job_dir>/harbor/<job_name>/``:

* ``**/agent/mcr_snapshot/artifact_summary.json`` (container-side summary and cost);
* ``mcr_snapshot/pre_repair/workspace/.mcr/{fork_meta.json, gate_report.json, best/cert.json}``;
* ``mcr_snapshot/post_repair/best/cert.json`` (only when Phase B ran);
* ``**/verifier/evaluation/metrics.json`` (outer verifier; ``metrics_reader``).

Outputs (all optional, pure functions here; the CLI decides what is written):

* ``runs`` records for ``diagnose_dev_runs`` / ``build_sealed_test_report`` / ``infra_ledger``;
* Path 1 archive records (scored ``dev`` runs, never an ``infra_error`` run);
* ``B_dev`` ``BankSample`` records (``source_kind = "terminal"``, ``dev`` runs with ``Y_star``);
* ``artifact_summary_merged.{json,md}`` next to the container summary, with ``outer_metrics``,
  token cost and the host-side ``infra_error`` filled in (scrubbed).

``t_soft_stop`` = ``fork_meta.phase_a.timed_out``: the intentional ``T_soft`` stop is a normal end
of Phase A, never an OOM / infra fault.
"""
from __future__ import annotations

import json
import math
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from ...kernel import artifact_summary as asum
from .metrics_reader import read_metrics

TOKEN_KEYS = ("input_tokens", "output_tokens", "cached_tokens")
CONTAINER_COST_KEYS = ("tool_cpu_sec", "peak_ram_mb", "wall_sec")
CERT_KEYS = ("file_present", "m", "u", "raw_u", "eps", "advisory_flags", "artifact_sha256",
             "aux_tree_sha256", "y_hat_mir", "check_timed_out")
MERGED_NAME = "artifact_summary_merged"


def _read_json(p: Path | None) -> Any:
    if p is None or not p.is_file():
        return None
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def _num(v: Any) -> float | None:
    return float(v) if type(v) in (int, float) and math.isfinite(v) else None


def _first(root: Path, pattern: str) -> Path | None:
    hits = sorted(root.glob(pattern)) if root.is_dir() else []
    return hits[0] if hits else None


def atomic_write_text(path: str | Path, text: str) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{uuid.uuid4().hex}")
    tmp.write_text(text)
    os.replace(tmp, p)
    return p


def load_launch_records(job_dir: str | Path) -> tuple[list[dict], str]:
    """``(records, source_file_name)``; ``launch_results.json`` wins over ``launch_plan.json``."""
    job_dir = Path(job_dir)
    for name in ("launch_results.json", "launch_plan.json"):
        p = job_dir / name
        if p.is_file():
            data = json.loads(p.read_text())
            if not isinstance(data, list):
                raise ValueError(f"{name}: expected a JSON list of job records")
            return data, name
    raise FileNotFoundError(f"{job_dir}: neither launch_results.json nor launch_plan.json")


@dataclass
class Harvested:
    run: dict
    job_root: Path
    snapshot_dir: Path | None = None
    summary: dict | None = None
    fork_meta: dict | None = None
    gate: dict | None = None
    pre_cert: dict | None = None
    post_cert: dict | None = None
    metrics_meta: dict = field(default_factory=dict)

    @property
    def scored(self) -> bool:
        """Scored task outcome (task failures included): no infra fault and a ``Y_star``."""
        return not self.run.get("infra_error") and self.run.get("Y_star") is not None

    def final_artifact(self) -> Path | None:
        if self.snapshot_dir is None:
            return None
        for rel in ("post_repair/submission/layout.gds", "pre_repair/workspace/submission/layout.gds"):
            p = self.snapshot_dir / rel
            if p.is_file():
                return p
        return None


def _snapshot_dir(root: Path) -> Path | None:
    s = _first(root, "**/agent/mcr_snapshot/artifact_summary.json")
    if s is not None:
        return s.parent
    return _first(root, "**/agent/mcr_snapshot")         # summary lost (e.g. SIGKILL)


def harvest_trial(job_dir: str | Path, rec: Mapping, *, theta_gw_max: float = 0.35) -> Harvested:
    from ...eval.infra_ledger import classify_trial
    job_dir = Path(job_dir)
    name = str(rec["job_name"])
    root = job_dir / "harbor" / name
    snap = _snapshot_dir(root)
    mcr = snap / "pre_repair" / "workspace" / ".mcr" if snap else None
    summary = _read_json(snap / "artifact_summary.json") if snap else None
    fork = _read_json(mcr / "fork_meta.json") if mcr else None
    gate = _read_json(mcr / "gate_report.json") if mcr else None
    pre_cert = _read_json(mcr / "best" / "cert.json") if mcr else None
    post_cert = _read_json(snap / "post_repair" / "best" / "cert.json") if snap else None
    trace = _read_json(snap / "repair_trace.json") if snap else None
    mpath = _first(root, "**/verifier/evaluation/metrics.json")
    reading = read_metrics(mpath if mpath else root / "verifier" / "evaluation" / "metrics.json")
    s = summary or {}
    promoted = bool(s.get("ratchet_promoted", (trace or {}).get("ratchet_promoted", False)))
    m_pre = s.get("m_pre") if s.get("m_pre") is not None else (pre_cert or {}).get("m")
    m_post = s.get("m_post") if s.get("m_post") is not None else (post_cert or {}).get("m")
    m = (m_post if promoted else m_pre) or m_post or m_pre
    sc = s.get("cost_ledger") or {}
    proxy = rec.get("proxy") if isinstance(rec.get("proxy"), Mapping) else None
    cost = {k: (proxy or {}).get(k) for k in TOKEN_KEYS} | {"usd": None} | {
        k: _num(sc.get(k)) for k in CONTAINER_COST_KEYS}
    meta = reading.meta
    run: dict[str, Any] = {
        "run_id": name, "arm": rec.get("arm"), "task_id": rec.get("task_id"),
        "seed": int(rec.get("seed", 0)), "status": meta.get("status"),
        "Y_star": reading.Y_star, "C_star": reading.C_star, "d_star": dict(reading.d_star),
        "m": list(m) if isinstance(m, (list, tuple)) else None,
        "exit_code": rec.get("exit_code"),
        "t_soft_stop": bool(((fork or {}).get("phase_a") or {}).get("timed_out")),
        "wall_sec": _num(sc.get("wall_sec")),
        "evaluation_error": meta.get("evaluation_error") is True,
        "score_is_lower_bound": meta.get("score_is_lower_bound") is True,
        "cost": cost,
        "gate_triggered": bool((gate or {}).get("gate_triggered", s.get("gate_triggered", False))),
        "ratchet_promoted": promoted,
    }
    if rec.get("arm") != "H_raw":  # MCR hook active: a certificate or a gate decision was produced
        run["hook_triggered"] = bool(pre_cert is not None or post_cert is not None
                                     or (gate or {}).get("gate_triggered"))
    if proxy is not None:          # unmetered records (launch plan only) carry no proxy keys
        run |= {"proxy_requests": int(proxy.get("proxy_requests") or 0),
                "unrecovered_gateway_error": bool(proxy.get("unrecovered_gateway_error")),
                "gateway_wait_seconds": _num(proxy.get("gateway_wait_seconds")) or 0.0,
                "model_swap": proxy.get("model_swap"),
                "client_disconnects": proxy.get("client_disconnects")}
    if not root.is_dir() or not any(p.is_dir() for p in root.iterdir()):
        run["no_trial_dir"] = True
    v = classify_trial(run, theta_gw_max=theta_gw_max)
    run |= {"infra_error": v.infra_error, "infra_category": v.category, "infra_reasons": list(v.reasons)}
    return Harvested(run=run, job_root=root, snapshot_dir=snap, summary=summary, fork_meta=fork,
                     gate=gate, pre_cert=pre_cert, post_cert=post_cert, metrics_meta=dict(meta))


def harvest_job(job_dir: str | Path, *, theta_gw_max: float = 0.35) -> tuple[list[Harvested], str]:
    recs, source = load_launch_records(job_dir)
    return [harvest_trial(job_dir, r, theta_gw_max=theta_gw_max) for r in recs], source


# ---------------------------------------------------------------------------------------------
# derived records
# ---------------------------------------------------------------------------------------------

def archive_record(h: Harvested, registry=None) -> dict:
    """Path 1 archive record of one scored run (``evaluate_path1_fork`` trial keys). The incumbent
    gate decision is recomputed zero-LLM from the archived exit state with *registry* (default:
    the ``H_base`` registry), exactly as the Path 1 replay recomputes the candidate's."""
    from ...checkers.alw import alw_registry
    from ...eval.fork_eval import archived_gate_decision
    if not h.scored:
        raise ValueError(f"{h.run['run_id']}: only scored runs enter the Path 1 archive")
    if h.snapshot_dir is None or not h.fork_meta:
        raise ValueError(f"{h.run['run_id']}: no archived pre_repair fork_meta.json")
    gate = h.gate or {}
    m_best = (h.pre_cert or {}).get("m")
    if m_best is None:
        m_best = (h.summary or {}).get("m_pre")
    y = float(h.run["Y_star"])
    t = {"task_id": h.run["task_id"], "seed": h.run["seed"], "snapshot_dir": str(h.snapshot_dir),
         "fork_meta": h.fork_meta, "m_best": m_best,
         "unevaluated_draft": "unevaluated_draft" in (gate.get("trigger_reasons") or []),
         "advisory_flags": dict((h.pre_cert or {}).get("advisory_flags") or {}),
         "Y_inc_final": y, "Y_pre": None if h.run.get("ratchet_promoted") else y}
    trig, sig = archived_gate_decision(t, registry if registry is not None else alw_registry())
    return t | {"inc_triggered": trig, "inc_gate_signature": list(sig)}


def bank_sample(h: Harvested, *, cluster_id: str, q_max: float | None = None) -> dict:
    """``BankSample`` dict (``source_kind = "terminal"``) of the final submission of one run."""
    from ...bank.referee_bank import BankSample
    if h.run.get("Y_star") is None:
        raise ValueError(f"{h.run['run_id']}: no verifier score, not a bank sample")
    cert = h.post_cert or h.pre_cert or {}
    art = h.final_artifact()
    s = BankSample(sample_id=f"{h.run['run_id']}:terminal", task_id=str(h.run["task_id"]),
                   cluster_id=str(cluster_id), source_kind="terminal",
                   Y_star=float(h.run["Y_star"]), C_star=h.run.get("C_star"),
                   d_star=dict(h.run.get("d_star") or {}),
                   cert_dict={k: cert[k] for k in CERT_KEYS if k in cert},
                   artifact_path=str(art) if art else None,
                   meta={"run_id": h.run["run_id"], "arm": h.run.get("arm"), "seed": h.run["seed"],
                         "status": h.run.get("status"), "q_max": q_max,
                         "score_is_lower_bound": h.run.get("score_is_lower_bound"),
                         "ratchet_promoted": h.run.get("ratchet_promoted")})
    return s.as_dict()


def merged_summary(h: Harvested, secrets: Iterable[str] = ()) -> dict:
    """Container summary with the host-side ``outer_metrics``, token cost and ``infra_error``."""
    base = dict(h.summary or {})
    r = h.run
    fields = {k: base.get(k) for k in asum.FIELDS}
    fields["task_id"] = fields["task_id"] or r.get("task_id")
    fields["seed"] = fields["seed"] if fields["seed"] is not None else str(r.get("seed"))
    fields["version_id"] = fields["version_id"] or r.get("arm")
    fields["outer_metrics"] = dict(base.get("outer_metrics") or {}) | {
        "Y_star": r.get("Y_star"), "C_star": r.get("C_star"), "d_star": r.get("d_star"),
        "score_is_lower_bound": r.get("score_is_lower_bound"),
        "evaluation_error": r.get("evaluation_error"), "status": r.get("status")}
    fields["cost_ledger"] = dict(base.get("cost_ledger") or {}) | {
        k: (r.get("cost") or {}).get(k) for k in TOKEN_KEYS}
    fields["infra_error"] = bool(r.get("infra_error"))
    fields["infra_reason"] = ",".join(r.get("infra_reasons") or []) or None
    if "extra" in base:
        fields["extra"] = base["extra"]
    return asum.build_summary(secrets=secrets, **fields)


def write_merged_summary(h: Harvested, secrets: Iterable[str] = ()) -> list[Path]:
    if h.snapshot_dir is None:
        return []
    secrets = list(secrets)
    s = merged_summary(h, secrets)
    return [atomic_write_text(h.snapshot_dir / f"{MERGED_NAME}.json",
                              json.dumps(s, indent=1, default=str)),
            atomic_write_text(h.snapshot_dir / f"{MERGED_NAME}.md", asum.render_markdown(s, secrets))]


def brief(h: Harvested) -> dict:
    r = h.run
    return {k: r.get(k) for k in ("run_id", "arm", "task_id", "seed", "status", "Y_star", "C_star",
                                  "m", "t_soft_stop", "gate_triggered", "ratchet_promoted",
                                  "infra_error", "infra_reasons", "proxy_requests")}


def harvest_summary(hs: list[Harvested], written: Iterable[str] = (), source: str | None = None,
                    secrets: Iterable[str] = ()) -> dict:
    ys = [float(h.run["Y_star"]) for h in hs if h.scored]
    out = {"source": source, "n_trials": len(hs), "n_scored": len(ys),
           "n_infra_error": sum(1 for h in hs if h.run.get("infra_error")),
           "mean_Y_star": math.fsum(ys) / len(ys) if ys else None,
           "trials_brief": [brief(h) for h in hs], "written_files": list(written)}
    return asum.scrub(out, list(secrets))
