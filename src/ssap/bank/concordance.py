"""Stage 0 concordance screening, guard-band calibration and the offline container smoke.

Stage 0 contracts (``dev`` bank only; blind clusters contribute aggregates only):

* Class I (mirror checker, overwrite authority): directional ``d*_k == 1 => m_k == 1`` on
  ``S_plus_k`` (a true solution is never de-certified); bidirectional additionally
  ``d*_k == 0 => m_k == 0`` on ``S_minus_k``. ``bottom`` labels are never used; the count of
  ``m_k == 1 and d*_k == bottom and lvs_pass == 1`` is reported separately (unverifiable
  certifications behind a passing LVS).
* Class II (advisory probe): ``k_FP == 0`` on ``S_plus`` (never flags a true solution),
  ``k_TP > 0`` on blind clusters (generalises beyond the proposer-visible clusters), and zero
  overwrite authority.
* Class III (diagnostic transform): every root-cause line kept, output ``<= 2048`` bytes.

``calibrate_epsilon``: ``eps_k = max{raw_u : raw_u >= 0 and d*_k == 0} + 1e-6`` (``0.0`` if no
such sample). ``m_shape`` (``eps_3``) is never calibrated on ``q_max = 3.0`` tasks: it needs a
sample set that is strictly ``q_max == 4.0``.
"""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .blind_clusters import BlindBoundary, aggregate
from .referee_bank import BOTTOM, BankSample, RefereeBank

EPS_MARGIN = 1e-6
CLASS3_MAX_BYTES = 2048


def _get(rec: Any, key: str, default=None):
    return rec.get(key, default) if isinstance(rec, Mapping) else getattr(rec, key, default)


# ---------------------------------------------------------------------------------------------
# guard-band calibration
# ---------------------------------------------------------------------------------------------

def calibrate_epsilon(checker_name: str, samples: Iterable[Any]) -> float:
    """Samples carry ``raw_u``, ``d_star_k`` and (for ``m_shape``) ``q_max``."""
    samples = list(samples)
    if checker_name == "m_shape":
        qs = [_get(s, "q_max") for s in samples]
        if any(q is None for q in qs) or any(float(q) != 4.0 for q in qs):
            raise ValueError("eps_3 (m_shape) may only be calibrated on a strictly q_max == 4.0 "
                             "sample set; q_max == 3.0 mismatches belong to advisory_aspect_gt_3")
    vals = []
    for s in samples:
        r, d = _get(s, "raw_u"), _get(s, "d_star_k")
        if d == 0 and isinstance(r, (int, float)) and math.isfinite(r) and r >= 0:
            vals.append(float(r))
    return max(vals) + EPS_MARGIN if vals else 0.0


# ---------------------------------------------------------------------------------------------
# Stage 0
# ---------------------------------------------------------------------------------------------

@dataclass
class ScreenReport:
    klass: int
    checker: str
    ok: bool
    checks: dict = field(default_factory=dict)
    visible: dict = field(default_factory=dict)
    blind_aggregate: dict | None = None

    def as_dict(self) -> dict:
        return {"class": self.klass, "checker": self.checker, "ok": self.ok, "checks": self.checks,
                "visible": self.visible, "blind_aggregate": self.blind_aggregate}


def _lvs_pass(s: BankSample) -> bool:
    return s.d_star.get("lvs") == 1


def screen_class1(checker: str, bank: RefereeBank, m_of: Callable[[BankSample], int | None], *,
                  bidirectional: bool = False, boundary: BlindBoundary | None = None) -> ScreenReport:
    """``m_of(sample)`` = the candidate checker's ``m_k`` on the sample's artifact."""
    plus, minus = bank.s_plus_k(checker), bank.s_minus_k(checker)
    fn = [s for s in plus if m_of(s) != 1]                     # de-certified true solutions
    fp = [s for s in minus if m_of(s) == 1] if bidirectional else []
    bottom_cert = [s for s in bank.bottom_k(checker) if m_of(s) == 1 and _lvs_pass(s)]
    records = [{"sample_id": s.sample_id, "task_id": s.task_id, "cluster_id": s.cluster_id,
                "artifact_path": s.artifact_path, "flagged": m_of(s) != 1, "label": s.d(checker)}
               for s in plus + (minus if bidirectional else [])]
    ok = not fn and not fp
    checks = {"directional_violations": len(fn), "bidirectional_violations": len(fp),
              "n_S_plus_k": len(plus), "n_S_minus_k": len(minus) if bidirectional else None,
              "certified_on_bottom_with_lvs_pass": len(bottom_cert),
              "coverage_S_plus_k": bank.coverage(plus)["clusters_covered"]}
    rep = ScreenReport(1, checker, ok, checks)
    _attach(rep, records, boundary, [s.sample_id for s in fn + fp])
    return rep


def screen_class2(probe: str, bank: RefereeBank, flag_of: Callable[[BankSample], bool], *,
                  boundary: BlindBoundary, overwrite_authority: bool = False,
                  negative_key: str | None = None) -> ScreenReport:
    """``k_FP == 0`` on ``S_plus``; ``k_TP > 0`` on blind negatives (``C* == 0``, or
    ``d*[negative_key] == 0`` when given); no overwrite authority."""
    def label(s: BankSample):
        if negative_key is not None:
            d = s.d(negative_key)
            return None if d == BOTTOM else d
        return s.C_star

    records = [{"sample_id": s.sample_id, "task_id": s.task_id, "cluster_id": s.cluster_id,
                "artifact_path": s.artifact_path, "flagged": bool(flag_of(s)), "label": label(s)}
               for s in bank]
    k_fp = sum(1 for s in bank.s_plus() if flag_of(s))
    _, blind = boundary.split(records)
    agg = aggregate(blind)
    checks = {"k_FP_S_plus": k_fp, "k_TP_blind": agg["n_tp"],
              "overwrite_authority": bool(overwrite_authority), "n_S_plus": len(bank.s_plus())}
    ok = k_fp == 0 and agg["n_tp"] > 0 and not overwrite_authority
    rep = ScreenReport(2, probe, ok, checks)
    _attach(rep, records, boundary,
            [r["sample_id"] for r in records if r["flagged"] and r["label"] == 1])
    return rep


def screen_class3(name: str, transform: Callable[[str], str],
                  cases: Iterable[tuple[str, Iterable[str]]],
                  max_bytes: int = CLASS3_MAX_BYTES) -> ScreenReport:
    """``cases = [(raw_text, root_cause_lines)]``; every root-cause line must survive verbatim."""
    lost, over, n = 0, 0, 0
    sizes = []
    for raw, lines in cases:
        n += 1
        out = transform(raw)
        size = len(out.encode())
        sizes.append(size)
        over += size > max_bytes
        lost += sum(1 for line in lines if line.strip() and line.strip() not in out)
    checks = {"cases": n, "root_cause_lines_lost": lost, "over_byte_cap": over,
              "max_output_bytes": max(sizes) if sizes else 0, "byte_cap": max_bytes}
    return ScreenReport(3, name, n > 0 and lost == 0 and over == 0, checks)


def _attach(rep: ScreenReport, records: list[dict], boundary: BlindBoundary | None,
            violating_ids: list[str]) -> None:
    if boundary is None:
        rep.visible = {"violating_sample_ids": sorted(violating_ids)}
        return
    vis, blind = boundary.split(records)
    vis_ids = {r["sample_id"] for r in vis}
    rep.visible = {"violating_sample_ids": sorted(i for i in violating_ids if i in vis_ids),
                   "n_samples": len(vis)}
    rep.blind_aggregate = aggregate(blind)
    boundary.guard(rep.as_dict(), blind_records=blind)


# ---------------------------------------------------------------------------------------------
# offline container smoke
# ---------------------------------------------------------------------------------------------

TOY_SPICE = """* synthetic smoke circuit (not an ALW task)
.subckt smoke_amp inp out vdd vss
M1 out inp vss vss sky130_fd_pr__nfet_01v8 w=1 l=0.15
M2 out out vdd vdd sky130_fd_pr__pfet_01v8 w=1 l=0.15
.ends smoke_amp
"""
TOY_TOP = "smoke_amp"


def _toy_workspace(ws: Path, gds_dir: Path) -> dict[str, Path]:
    from ..checkers.alw.gds_geom import write_gds
    for d in ("task", ".mcr/trusted_task/task", "work/draft", "submission"):
        (ws / d).mkdir(parents=True, exist_ok=True)
    for d in (ws / "task", ws / ".mcr" / "trusted_task" / "task"):
        (d / "circuit.spice").write_text(TOY_SPICE)
    for p in (ws / "eda_env.sh", ws / ".mcr" / "trusted_task" / "eda_env.sh"):
        p.write_text("# synthetic smoke: no EDA exports\n")
    (ws / ".mcr" / "task_prompt.txt").write_text(f"Design the layout of `{TOY_TOP}`.\n")
    gds_dir.mkdir(parents=True, exist_ok=True)
    good = write_gds(gds_dir / "good.gds", {TOY_TOP: {"boxes": [(65, 20, 0, 0, 20, 10),
                                                                (68, 20, 0, 0, 2, 2)]}})
    bad = write_gds(gds_dir / "bad.gds", {TOY_TOP: {"boxes": [(65, 20, 0, 0, 50, 10)]}})   # q = 5
    junk = gds_dir / "junk.gds"
    junk.write_bytes(b"not a gds stream")
    return {"good": good, "bad": bad, "junk": junk}


def _run_tool(cmd: list[str], env: dict, cwd: Path, timeout: float) -> dict:
    p = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=cwd, timeout=timeout)
    try:
        return {"rc": p.returncode, "out": json.loads(p.stdout)}
    except ValueError:
        return {"rc": p.returncode, "out": None, "stderr": p.stderr[-2000:]}


def _docker_image_present(image: str) -> bool:
    if not image or shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(["docker", "image", "inspect", image], capture_output=True,
                              timeout=20).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def offline_smoke_container(cfg: Mapping | None = None, *, root: str | Path | None = None,
                            work_dir: str | Path, image: str | None = None,
                            use_docker: bool | None = None, timeout_sec: float = 300.0) -> dict:
    """Stage the ``/opt/harness`` bundle and exercise ``mcr_promote`` / ``mcr_check`` on a
    synthetic toy task: good layout promotes (bootstrap), a ratchet-worse and a corrupt layout
    do not overwrite it, the gate report is produced, and ``ssap`` resolves from the bundle.
    With ``use_docker`` (``None`` = only if the image exists) the same steps run inside
    ``docker run --network none`` with the bundle mounted read-only."""
    from ..targets.alw.agent_wrapper import HARNESS_MOUNT, stage_harness_bundle
    root = Path(root or (cfg or {}).get("_root") or Path(__file__).resolve().parents[3])
    work = Path(work_dir)
    if work.exists():
        shutil.rmtree(work)
    harness = work / "harness"
    manifest = stage_harness_bundle(root, harness, watermarks=(cfg or {}).get("watermarks"),
                                    check_timeout_sec=60)
    report: dict = {"harness_files": len(manifest["files"]), "local": None, "docker": None}
    report["local"] = _smoke_local(harness, work / "local", timeout_sec)
    image = image if image is not None else ((cfg or {}).get("images") or {}).get("agent_image") or ""
    if use_docker is None:
        use_docker = _docker_image_present(image)
    if use_docker:
        report["docker"] = _smoke_docker(harness, work / "docker", image, timeout_sec, HARNESS_MOUNT)
    report["ok"] = bool(report["local"]["ok"] and (report["docker"] is None or report["docker"]["ok"]))
    return report


def _expectations(steps: dict, good_sha: str) -> dict:
    g, b, j, gate = steps["promote_good"], steps["promote_bad"], steps["promote_junk"], steps["gate"]
    best = lambda r: ((r.get("out") or {}).get("best_cert") or {}).get("artifact_sha256")  # noqa: E731
    return {
        "good_promoted": bool((g.get("out") or {}).get("promoted")),
        "bad_rejected": (b.get("out") or {}).get("promoted") is False and best(b) == good_sha,
        "junk_rejected": (j.get("out") or {}).get("promoted") is False and best(j) == good_sha,
        "gate_report": isinstance((gate.get("out") or {}).get("trigger_reasons"), list),
    }


def _smoke_local(harness: Path, base: Path, timeout: float) -> dict:
    from ..kernel.promote import sha256_file
    ws = base / "alb-workspace"
    files = _toy_workspace(ws, base / "gds")
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(base),
           "MCR_HARNESS_PYTHON": str(harness / "python"), "PYTHONPATH": str(harness / "python"),
           "MCR_WORKSPACE": str(ws), "MCR_CONFIG": str(harness / "mcr_config.json"),
           "MCR_CHECK_TIMEOUT_SEC": "60"}
    py = [sys.executable, "-I"]
    origin = subprocess.run(py + ["-c", "import sys; sys.path.insert(0, sys.argv[1]); import ssap; "
                                  "print(ssap.__file__)", str(harness / "python")],
                            capture_output=True, text=True, env=env, timeout=60).stdout.strip()
    steps = {}
    for name, key in (("promote_good", "good"), ("promote_bad", "bad"), ("promote_junk", "junk")):
        steps[name] = _run_tool(py + [str(harness / "bin" / "mcr_promote"), "--gds", str(files[key])],
                                env, ws, timeout)
    steps["gate"] = _run_tool(py + [str(harness / "bin" / "mcr_check"), "--gate", "--elapsed", "100"],
                              env, ws, timeout)
    good_sha = sha256_file(files["good"])
    exp = _expectations(steps, good_sha)
    exp["submission_is_good"] = sha256_file(ws / "submission" / "layout.gds") == good_sha
    exp["ssap_from_bundle"] = origin.startswith(str(harness / "python"))
    return {"ok": all(exp.values()), "expectations": exp, "ssap_origin": origin,
            "steps": {k: {"rc": v["rc"], "promoted": (v.get("out") or {}).get("promoted"),
                          "reason": (v.get("out") or {}).get("reason")} for k, v in steps.items()}}


def _smoke_docker(harness: Path, base: Path, image: str, timeout: float, mount: str) -> dict:
    from ..kernel.promote import sha256_file
    ws = base / "alb-workspace"
    files = _toy_workspace(ws, ws / ".smoke_gds")
    py = 'P=/opt/analog-layout/python/bin/python3.12; [ -x "$P" ] || P=python3; exec "$P" "$@"'
    steps = {}
    for name, args in (("promote_good", ["mcr_promote", "--gds", "/alb-workspace/.smoke_gds/good.gds"]),
                       ("promote_bad", ["mcr_promote", "--gds", "/alb-workspace/.smoke_gds/bad.gds"]),
                       ("promote_junk", ["mcr_promote", "--gds", "/alb-workspace/.smoke_gds/junk.gds"]),
                       ("gate", ["mcr_check", "--gate", "--elapsed", "100"])):
        cmd = ["docker", "run", "--rm", "--network", "none", "--cpus", "2", "--memory", "4g",
               "--entrypoint", "/bin/sh", "-v", f"{harness.resolve()}:{mount}:ro",
               "-v", f"{ws.resolve()}:/alb-workspace", "-w", "/alb-workspace",
               "-e", f"PYTHONPATH={mount}/python", "-e", f"MCR_HARNESS_PYTHON={mount}/python",
               "-e", "MCR_WORKSPACE=/alb-workspace", "-e", "MCR_CHECK_TIMEOUT_SEC=120",
               image, "-c", py, "sh", f"{mount}/bin/{args[0]}", *args[1:]]
        try:
            steps[name] = _run_tool(cmd, {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                                          "HOME": os.environ.get("HOME", str(base))}, base, timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    good_sha = sha256_file(files["good"])
    exp = _expectations(steps, good_sha)
    exp["submission_is_good"] = sha256_file(ws / "submission" / "layout.gds") == good_sha
    return {"ok": all(exp.values()), "expectations": exp, "image": image,
            "steps": {k: {"rc": v["rc"], "promoted": (v.get("out") or {}).get("promoted"),
                          "reason": (v.get("out") or {}).get("reason")} for k, v in steps.items()}}
