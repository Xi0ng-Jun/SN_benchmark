"""Phase 2 referee-side synthetic bank builder and the Stage 0 ``H_base`` concordance audit.

Pipeline (referee only; never run by proposing / implementing agents):

1. ``select_mutation_sources``: at most one terminal ``dev`` layout per cluster, preferring
   ``S_plus`` (``C* == 1``), then ``S_plus_lvs``, and keeping at least one proposer-visible
   cluster with a passing DRC or GDS stage when one exists.
2. ``synthesize_bank_variants``: 0-LLM-token GDS variants (``synthesize.synthesize_all``) of each
   source, labelled by the offline ``alw verify`` (``verifier_fn``) and certified by the inner
   ``mcr_check`` (``inner_cert_fn``), admitted into ``B_dev`` as ``synthetic_pos`` /
   ``synthetic_neg`` samples. Verifier faults are infra skips, never labels.
3. ``screen_hbase_on_bank``: Class I screens of the six incumbent mirror checkers on the samples
   that carry an incumbent certificate, the Class II ``advisory_aspect_gt_3`` probe, ``eps_fill``
   and bank coverage.

Every returned summary passes ``BlindBoundary.guard``: blind clusters appear as counts only.
"""
from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Mapping

from ..checkers.alw.area_fill import RHO_MIN
from . import synthesize
from .blind_clusters import BlindBoundary, BlindSet
from .concordance import calibrate_epsilon, screen_class1, screen_class2
from .referee_bank import BOTTOM, MIRROR_ORDER, BankSample, RefereeBank

VerifierFn = Callable[[str, Path, Path], Any]          # (task_id, submission_dir, out_dir) -> MetricsReading
InnerCertFn = Callable[[str, str, Path, Path], dict]   # (task_id, top, gds_path, work_dir) -> cert dict
LABEL_KEYS = ("gds", "drc", "lvs", "shape", "fill", "morph")
CLASS1_SCREENS = (("m_gds", True), ("m_lvs", False), ("m_shape", False), ("m_fill", True),
                  ("m_morph", True), ("m_drc", True))


# ---------------------------------------------------------------------------------------------
# source selection
# ---------------------------------------------------------------------------------------------

def _rank(s: BankSample) -> tuple:
    return (s.C_star == 1, s.d("lvs") == 1, s.d("drc") == 1, float(s.Y_star or 0.0))


def select_mutation_sources(bank: RefereeBank, blind: BlindSet, *,
                            max_sources: int = 3) -> list[BankSample]:
    """Best terminal sample per cluster (existing artifact only), then up to ``max_sources``
    clusters: one ``S_plus`` cluster, a reserved proposer-visible cluster with ``d*_drc == 1`` or
    ``d*_gds == 1``, then ``S_plus_lvs`` clusters, then the remaining clusters by rank."""
    best: dict[str, BankSample] = {}
    for s in sorted(bank, key=lambda s: s.sample_id):
        if s.source_kind != "terminal" or not s.artifact_path or not Path(s.artifact_path).is_file():
            continue
        cur = best.get(s.cluster_id)
        if cur is None or _rank(s) > _rank(cur):
            best[s.cluster_id] = s
    ordered = sorted(sorted(best.values(), key=lambda s: s.cluster_id), key=_rank, reverse=True)
    chosen: list[BankSample] = []

    def take(pred: Callable[[BankSample], bool], limit: int | None = None) -> None:
        n = 0
        for s in ordered:
            if len(chosen) >= max_sources or (limit is not None and n >= limit):
                return
            if s not in chosen and pred(s):
                chosen.append(s)
                n += 1

    def vis_ok(s: BankSample) -> bool:
        return not blind.is_blind(s.cluster_id) and (s.d("drc") == 1 or s.d("gds") == 1)

    take(lambda s: s.C_star == 1, limit=1)
    if not any(vis_ok(s) for s in chosen):
        take(vis_ok, limit=1)
    take(lambda s: s.d("lvs") == 1)
    take(lambda s: True)
    return chosen


def effective_area_from_sample(sample: BankSample, data: bytes, top: str) -> float | None:
    """``A_eff = (raw_u_fill + 0.10) * A_bbox`` for an LVS-passing source (``raw_u = rho - 0.10``)."""
    raw = sample.raw_u("m_fill")
    if sample.d("lvs") != 1 or raw is None:
        return None
    x1, y1, x2, y2 = synthesize.bbox_um(data, top)
    return (float(raw) + RHO_MIN) * (x2 - x1) * (y2 - y1)


# ---------------------------------------------------------------------------------------------
# variant synthesis, verification and admission
# ---------------------------------------------------------------------------------------------

def _expected_labels(v, src: BankSample, q_max) -> dict:
    if v.expected.get("inherit"):
        return {k: src.d(k) for k in LABEL_KEYS if src.d(k) != BOTTOM}
    if v.advisory_only_on_q3 and q_max is not None and float(q_max) == 3.0:
        return {k: 0 for k in v.expected}        # q in (3.2, 3.8) fails a q_max = 3.0 task
    return dict(v.expected)


def _agreement(expected: Mapping, d_star: Mapping) -> str:
    got = [d_star.get(k, BOTTOM) for k in expected]
    if not expected or any(g == BOTTOM for g in got):
        return "bottom"
    return "agree" if all(g == expected[k] for g, k in zip(got, expected)) else "disagree"


def _tag(i: int, sample_id: str, name: str) -> str:
    return f"{i:02d}_{hashlib.sha256(sample_id.encode()).hexdigest()[:12]}_{name}"


def _blind_records(boundary: BlindBoundary, records) -> list:
    return boundary.split(list(records))[1]


def synthesize_bank_variants(bank: RefereeBank, catalog_tasks_by_id: Mapping[str, Mapping],
                             blind: BlindSet, *, work_dir: str | Path, max_sources: int = 3,
                             verifier_fn: VerifierFn | None = None,
                             inner_cert_fn: InnerCertFn | None = None, parallel: int = 1,
                             dry_run: bool = False) -> dict:
    """Synthesize, (optionally) verify and admit 0-token variants; returns a blind-safe summary
    (counts only). Variants live under ``<work_dir>/variants/<tag>/submission/layout.gds``."""
    boundary = BlindBoundary(blind)
    work = Path(work_dir)
    sources = select_mutation_sources(bank, blind, max_sources=max_sources)
    by_name: dict[str, Counter] = {}
    jobs: list[dict] = []
    n_source_errors = n_existing = 0
    for i, src in enumerate(sources):
        task = catalog_tasks_by_id.get(src.task_id) or {}
        top = task.get("top")
        try:
            if not top:
                raise ValueError("task has no top cell in the catalog")
            data = Path(src.artifact_path).read_bytes()
            eff = effective_area_from_sample(src, data, top)
            variants = synthesize.synthesize_all(data, top, ports=task.get("ports") or (),
                                                 effective_area_um2=eff)
        except Exception:                                   # noqa: BLE001 - unreadable source
            n_source_errors += 1
            continue
        for v in variants:
            c = by_name.setdefault(v.name, Counter())
            c["planned"] += 1
            sid = f"{src.sample_id}:{v.name}"
            try:
                bank.get(sid)
                n_existing += 1
                c["existing"] += 1
                continue
            except KeyError:
                pass
            vdir = work / "variants" / _tag(i, src.sample_id, v.name)
            gds = vdir / "submission" / "layout.gds"
            if not dry_run:
                v.write(gds)
            jobs.append({"src": src, "v": v, "sid": sid, "top": top, "q_max": task.get("q_max"),
                         "vdir": vdir, "gds": gds})

    def evaluate(job: dict) -> tuple:
        try:
            reading = verifier_fn(job["src"].task_id, job["vdir"] / "submission", job["vdir"] / "verify")
        except Exception:                                   # noqa: BLE001 - verifier fault = infra
            return None, {}, False
        cert, cert_ok = {}, inner_cert_fn is None
        if inner_cert_fn is not None and reading is not None and reading.Y_star is not None:
            try:
                cert, cert_ok = dict(inner_cert_fn(job["src"].task_id, job["top"], job["gds"],
                                                   job["vdir"] / "inner") or {}), True
            except Exception:                               # noqa: BLE001 - advisory only
                cert = {}
        return reading, cert, cert_ok

    counts = Counter()
    run = verifier_fn is not None and not dry_run
    if run and jobs:
        with ThreadPoolExecutor(max_workers=max(1, int(parallel))) as pool:
            results = list(pool.map(evaluate, jobs))
        for job, (reading, cert, cert_ok) in zip(jobs, results):
            v, src, c = job["v"], job["src"], by_name[job["v"].name]
            if reading is None or reading.Y_star is None:
                counts["skipped_infra"] += 1
                c["skipped_infra"] += 1
                continue
            counts["verified"] += 1
            c["verified"] += 1
            counts["inner_cert"] += bool(cert) and cert_ok
            c[_agreement(_expected_labels(v, src, job["q_max"]), reading.d_star)] += 1
            sample = BankSample(
                sample_id=job["sid"], task_id=src.task_id, cluster_id=src.cluster_id,
                source_kind=v.source_kind, Y_star=reading.Y_star, C_star=reading.C_star,
                d_star=dict(reading.d_star), cert_dict=cert, artifact_path=str(job["gds"]),
                meta={"variant": v.name, "checker": v.checker, "polarity": v.polarity,
                      "q_max": job["q_max"], "advisory_only_on_q3": v.advisory_only_on_q3})
            if bank.add(sample):
                counts["added"] += 1
                c["added"] += 1
    summary = {
        "dry_run": bool(dry_run), "verifier": verifier_fn is not None,
        "inner_cert": inner_cert_fn is not None,
        "n_sources": len(sources),
        "n_sources_blind_clusters": sum(blind.is_blind(s.cluster_id) for s in sources),
        "n_source_errors": n_source_errors,
        "n_variants_planned": sum(c["planned"] for c in by_name.values()),
        "n_existing": n_existing, "n_written": 0 if dry_run else len(jobs),
        "n_verified": counts["verified"], "n_added": counts["added"],
        "n_skipped_infra": counts["skipped_infra"], "n_inner_cert": counts["inner_cert"],
        "by_variant_name": {k: dict(sorted(v.items())) for k, v in sorted(by_name.items())},
        "bank_size": len(bank),
    }
    boundary.guard(summary, blind_records=_blind_records(boundary, list(bank) + sources))
    return summary


# ---------------------------------------------------------------------------------------------
# Stage 0 H_base audit on B_dev
# ---------------------------------------------------------------------------------------------

def _sub_bank(bank: RefereeBank, keep: Callable[[BankSample], bool]) -> RefereeBank:
    sub = RefereeBank(dev_clusters=bank.dev_clusters)
    for s in bank:
        if keep(s):
            sub.add(s)
    return sub


def _aspect_flag(s: BankSample) -> bool:
    return bool((s.cert_dict.get("advisory_flags") or {}).get("advisory_aspect_gt_3"))


def screen_hbase_on_bank(bank: RefereeBank, blind: BlindSet) -> dict:
    """Class I screens of the incumbent mirror checkers (only on samples whose stored certificate
    carries ``m_k``: an unchecked sample is not an ``m_k != 1``), the Class II
    ``advisory_aspect_gt_3`` probe, ``eps_fill`` and coverage. Blind clusters: aggregates only."""
    boundary = BlindBoundary(blind)
    class1 = {}
    for ck, bidi in CLASS1_SCREENS:
        sub = _sub_bank(bank, lambda s, c=ck: s.m(c) is not None)
        rep = screen_class1(ck, sub, lambda s, c=ck: s.m(c), bidirectional=bidi, boundary=boundary)
        class1[ck] = {**rep.as_dict(), "n_checked": len(sub), "n_unchecked": len(bank) - len(sub)}
    adv = screen_class2("advisory_aspect_gt_3", bank, _aspect_flag, boundary=boundary,
                        negative_key="shape")
    eps_fill = calibrate_epsilon("m_fill", [{"raw_u": s.raw_u("m_fill"), "d_star_k": s.d("m_fill")}
                                            for s in bank])
    kinds = Counter(s.source_kind for s in bank)
    report = {
        "class1": class1,
        "class1_ok": all(r["ok"] for r in class1.values()),
        "class2": {"advisory_aspect_gt_3": adv.as_dict()},
        "class2_ok": adv.ok,
        "eps": {"m_fill": eps_fill},
        "coverage": {"n_total": len(bank), "by_source_kind": dict(sorted(kinds.items())),
                     "n_S_plus": len(bank.s_plus()), "S_plus": bank.coverage(bank.s_plus()),
                     "per_criterion": {ck: bank.coverage_report(ck) for ck in MIRROR_ORDER}},
    }
    boundary.guard(report, blind_records=_blind_records(boundary, bank))
    return report


# ---------------------------------------------------------------------------------------------
# live helpers (CLI): offline `alw verify` and the inner mcr_check in a network-less container
# ---------------------------------------------------------------------------------------------

def _infra_reading(reason: str):
    from ..targets.alw.metrics_reader import D_KEYS, MetricsReading
    return MetricsReading(None, None, {k: BOTTOM for k in D_KEYS},
                          {"status": "error", "infra_error": True, "infra_reason": reason,
                           "task_failure": False})


def make_alw_verifier_fn(verify_cmd_template: str, alw_repo: str | Path | None,
                         env: Mapping[str, str] | None = None, *,
                         timeout_sec: float = 3600.0) -> VerifierFn:
    """``verify_cmd_template`` placeholders: ``{alw_repo}``, ``{task_id}``, ``{submission_dir}``,
    ``{out_dir}``. ``alw verify`` exits ``0`` (design passed) or ``1`` (design failed): both are
    valid verdicts read from ``<out_dir>/verifier/evaluation/metrics.json``; any other exit code
    or a timeout is an infra fault."""
    from ..targets.alw.metrics_reader import read_metrics

    def fn(task_id: str, submission_dir: Path, out_dir: Path):
        out = Path(out_dir)
        if out.exists() or out.is_symlink():
            shutil.rmtree(out, ignore_errors=True)
            if out.exists() or out.is_symlink():
                out.unlink()
        out.parent.mkdir(parents=True, exist_ok=True)
        fields = {"alw_repo": str(alw_repo or ""), "task_id": task_id,
                  "submission_dir": str(Path(submission_dir).resolve()), "out_dir": str(out.resolve())}
        cmd = shlex.split(verify_cmd_template.format(**{k: shlex.quote(v) for k, v in fields.items()}))
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec,
                               env=dict(env) if env is not None else None,
                               cwd=str(alw_repo) if alw_repo and Path(alw_repo).is_dir() else None)
        except subprocess.TimeoutExpired:
            return _infra_reading("verify_timeout")
        except OSError:
            return _infra_reading("verify_spawn_error")
        log_path = (out / "verify.log") if out.is_dir() else out.with_name(f"{out.name}.verify.log")
        log_path.write_text(f"rc={p.returncode}\n{p.stdout[-4000:]}\n{p.stderr[-4000:]}")
        if p.returncode not in (0, 1):
            return _infra_reading(f"verify_rc_{p.returncode}")
        return read_metrics(out / "verifier" / "evaluation" / "metrics.json")
    return fn


DOCKER_PY = 'P=/opt/analog-layout/python/bin/python3.12; [ -x "$P" ] || P=python3; exec "$P" "$@"'


def _stage_inner_workspace(ws: Path, env_src: Path, top: str, gds_path: Path) -> Path:
    if ws.exists():
        shutil.rmtree(ws)
    trusted = ws / ".mcr" / "trusted_task"
    for dst in (trusted, ws):
        shutil.copytree(env_src / "task", dst / "task")
        shutil.copy2(env_src / "eda_env.sh", dst / "eda_env.sh")
    (ws / ".mcr" / "task_prompt.txt").write_text(f"Design the layout of `{top}`.\n")
    cand = ws / ".cand" / "layout.gds"
    cand.parent.mkdir(parents=True)
    shutil.copy2(gds_path, cand)
    for p in trusted.rglob("*"):                 # files read-only; the mount is ``:ro`` as well
        if p.is_file():
            os.chmod(p, 0o444)
    return trusted


def make_docker_inner_cert_fn(cfg: Mapping | None, root: str | Path, alw_repo: str | Path | None,
                              work_dir: str | Path, image: str | None, *,
                              timeout_sec: float = 1800.0,
                              check_timeout_sec: float = 900.0) -> InnerCertFn | None:
    """Inner ``mcr_check --gds`` certificate inside ``docker run --network none`` (``None`` when
    the image or the ALW checkout is unavailable). ``/opt/harness`` is staged once; the frozen
    ``.mcr/trusted_task/{task,eda_env.sh}`` comes from ``<alw_repo>/tasks/<task_id>/environment``
    and is mounted read-only."""
    from ..targets.alw.agent_wrapper import HARNESS_MOUNT, stage_harness_bundle
    from ..targets.alw.harvest import CERT_KEYS
    if not image or not alw_repo or not Path(alw_repo).is_dir() or shutil.which("docker") is None:
        return None
    work = Path(work_dir)
    harness = work / "harness"
    stage_harness_bundle(root, harness, watermarks=(cfg or {}).get("watermarks"),
                         check_timeout_sec=check_timeout_sec)

    def fn(task_id: str, top: str, gds_path: Path, check_work_dir: Path) -> dict:
        base = Path(check_work_dir)
        ws = base / "alb-workspace"
        trusted = _stage_inner_workspace(ws, Path(alw_repo) / "tasks" / task_id / "environment",
                                         top, Path(gds_path))
        cmd = ["docker", "run", "--rm", "--network", "none", "--cpus", "2", "--memory", "4g",
               "--entrypoint", "/bin/sh", "-v", f"{harness.resolve()}:{HARNESS_MOUNT}:ro",
               "-v", f"{ws.resolve()}:/alb-workspace",
               "-v", f"{trusted.resolve()}:/alb-workspace/.mcr/trusted_task:ro",
               "-w", "/alb-workspace", "-e", f"PYTHONPATH={HARNESS_MOUNT}/python",
               "-e", f"MCR_HARNESS_PYTHON={HARNESS_MOUNT}/python",
               "-e", "MCR_WORKSPACE=/alb-workspace",
               "-e", f"MCR_CHECK_TIMEOUT_SEC={int(check_timeout_sec)}",
               image, "-c", DOCKER_PY, "sh", f"{HARNESS_MOUNT}/bin/mcr_check",
               "--gds", "/alb-workspace/.cand/layout.gds"]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec,
                           env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                                "HOME": os.environ.get("HOME", str(base))})
        (base / "mcr_check.log").write_text(f"rc={p.returncode}\n{p.stderr[-4000:]}")
        out = json.loads(p.stdout)
        cert = out.get("cert") or {}
        res = {k: cert[k] for k in CERT_KEYS if k in cert}
        if out.get("mirror_names"):
            res["mirror_names"] = list(out["mirror_names"])
        return res
    return fn
