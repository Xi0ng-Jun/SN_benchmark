"""Sealed ``test`` runs for the final table: ``H_raw`` and ``H_base`` live, ``H_star`` by replay.

Every automated mutation of the incumbent so far is terminal-only (Path 1), so ``H_star`` and
``H_base`` run the same Phase A. ``H_star`` is therefore evaluated on the ``H_base`` test snapshots
by rerunning the stateless Phase B with the ``H_star`` harness, as the dev Path 1 replay does, with
one repair per snapshot (the semantics of one live run). The replay refuses a ``H_star`` whose
trajectory mirror differs from ``H_base``.

``H_base`` is staged from the clean source the search started from (``STATE/loop/h_base_src``),
so ``H_base -> H_star`` differs exactly by the promoted mutations.

Layout::

    STATE/test/manifest.json          H_star version and tree hash, seeds, protocol deviations
    STATE/test/replay/                gate views, repair workspaces, replay.jsonl
    STATE/jobs/test-<arm>[-retry-*]/  Harbor jobs of the live arms (one paired retry per infra slot)
    STATE/runs/test_runs.jsonl        H_raw, H_base and H_star records read by ``alw-report``

Scores are never printed here: ``alw-report`` is the single unblinding step.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from . import auto_loop as al
from . import proposer_sandbox as ps

LIVE_ARMS = ("H_base", "H_raw")
R_MIN_PROTOCOL = 3
POOL_TAG = "test-pool"


def test_dir(state: str | Path) -> Path:
    return Path(state) / "test"


def runs_path(state: str | Path) -> Path:
    return Path(state) / "runs" / "test_runs.jsonl"


def hbase_source(state: str | Path, repo_root: str | Path) -> Path:
    """The clean ``H_base`` source the search started from (staged once)."""
    base = al.loop_dir(Path(state)) / "h_base_src"
    if not (base / "container" / "alw" / "settings.json").is_file():
        ps.stage_clean_hbase_source(repo_root, base)
    return base


def star_source(state: str | Path) -> Path:
    from ..cli import default_h_inc_harness
    p = default_h_inc_harness(Path(state))
    if p is None:
        raise FileNotFoundError("no promoted incumbent (STATE/loop/h_inc_harness): H_star is H_base")
    return p


def _git_commit(root: str | Path) -> str | None:
    try:
        p = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True,
                           text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return p.stdout.strip() or None


def write_manifest(cfg: Mapping, state: str | Path, *, seeds: int, n_tasks: int) -> dict:
    """Pin ``H_star`` / ``H_base`` before the first holdout run; a later call with a different
    setup is refused (the holdout is run for one fixed selection only)."""
    star, base = star_source(state), hbase_source(state, cfg["_root"])
    h = al.load_h_inc(Path(state))
    doc = {"schema_version": 1, "split": "test", "h_star_version": h.get("version_id"),
           "h_star_tree_sha256": ps.tree_hash(star), "h_base_tree_sha256": ps.tree_hash(base),
           "repo_commit": _git_commit(cfg["_root"]), "seeds": int(seeds), "n_tasks": int(n_tasks),
           "arms": {"H_raw": "live", "H_base": "live, clean H_base source",
                    "H_star": "stateless Phase B replay on the H_base test snapshots"},
           "protocol_deviations": [
               "val split not used: H_star fixed to the dev incumbent before any holdout run (no C1)",
               f"{int(seeds)} seed(s) per arm (protocol: >= {R_MIN_PROTOCOL})",
               "honest baselines (manual instructions, token-matched Best-of-k) not run"]}
    p = test_dir(state) / "manifest.json"
    if p.is_file():
        old = json.loads(p.read_text())
        for k in ("h_star_tree_sha256", "h_base_tree_sha256", "seeds", "n_tasks"):
            if old.get(k) != doc[k]:
                raise ValueError(f"test manifest mismatch on {k}: the holdout was started with "
                                 f"{old.get(k)!r}, now {doc[k]!r}")
        return old
    al.save_json(p, doc | {"created_unix": round(time.time(), 3)})
    return doc


# ---------------------------------------------------------------------------------------------
# live arms
# ---------------------------------------------------------------------------------------------

def _tag(arm: str, retry: str | None = None) -> str:
    return f"test-{arm}" + (f"-retry-{retry}" if retry else "")


def _launched(state: Path, tag: str) -> bool:
    return (state / "jobs" / tag / "launch_results.json").is_file()


def _default_harvest(cfg: Mapping) -> Callable[[Path], list]:
    from ..targets.alw.harvest import harvest_job
    theta = float((cfg.get("gateway") or {}).get("theta_gw_max", 0.35))
    return lambda d: harvest_job(d, theta_gw_max=theta)[0]


def _launch_pool(cfg: Mapping, state: Path, by_tag: Mapping[str, list[dict]], harbor_cmd: str,
                 launch_fn, pool_tag: str) -> int:
    """All jobs of *by_tag* in one pool of ``run.parallel``; results are filed per tag."""
    from ..targets.alw.harvest import atomic_write_text
    jobs = [j for js in by_tag.values() for j in js]
    if not jobs:
        return 0
    (state / "jobs" / pool_tag).mkdir(parents=True, exist_ok=True)
    res = launch_fn(cfg, jobs, harbor_cmd, state, pool_tag)
    owner = {j["job_name"]: t for t, js in by_tag.items() for j in js}
    filed: dict[str, list[dict]] = {t: [] for t in by_tag}
    for r in res:
        filed[owner[r["job_name"]]].append(r)
    for t, rows in filed.items():
        atomic_write_text(state / "jobs" / t / "launch_results.json",
                          json.dumps(rows, indent=1, default=str))
    return len(jobs)


def harvest_live(state: str | Path, arm: str, harvest_fn) -> list:
    """Harvested runs of *arm*, main job first, then its retries (a later record of a slot wins)."""
    jobs = Path(state) / "jobs"
    dirs = [jobs / _tag(arm)] + sorted(jobs.glob(f"{_tag(arm)}-retry-*"))
    out: list = []
    for d in dirs:
        if (d / "launch_results.json").is_file():
            out += list(harvest_fn(d))
    return out


def final_slots(hs: Iterable) -> dict[tuple[str, int], Any]:
    out: dict[tuple[str, int], Any] = {}
    for h in hs:
        out[(str(h.run["task_id"]), int(h.run.get("seed", 0)))] = h
    return out


def launch_live(cfg: Mapping, state: str | Path, *, tasks: list[str], seeds: list[int],
                harbor_cmd: str | None, dry_run: bool = False, stage_fn=None, launch_fn=None,
                harvest_fn=None) -> dict:
    """``H_base`` and ``H_raw`` on the test tasks in one pool, then one paired retry of every
    ``infra_error`` slot. Arms whose main job already has results are not relaunched."""
    state = Path(state)
    if stage_fn is None or launch_fn is None:
        from ..cli import _launch, _stage_jobs
        stage_fn, launch_fn = stage_fn or _stage_jobs, launch_fn or _launch
    harvest_fn = harvest_fn or _default_harvest(cfg)
    roots = {"H_base": hbase_source(state, cfg["_root"]), "H_raw": None}
    by_tag, skipped = {}, []
    for arm in LIVE_ARMS:
        t = _tag(arm)
        if _launched(state, t):
            skipped.append(t)
            continue
        by_tag[t] = stage_fn(cfg, state, arm=arm, tasks=list(tasks), seeds=list(seeds), tag=t,
                             timeout_s=None, harness_root=roots[arm])
    out = {"staged": {t: len(js) for t, js in by_tag.items()}, "already_launched": skipped}
    if dry_run:
        return out | {"dry_run": True}
    if not harbor_cmd:
        raise ValueError("live test runs need --harbor-cmd (verified runbook template)")
    out["launched"] = _launch_pool(cfg, state, by_tag, harbor_cmd, launch_fn, POOL_TAG)
    retry = {}
    for arm in LIVE_ARMS:
        for (task, seed), h in final_slots(harvest_live(state, arm, harvest_fn)).items():
            t = _tag(arm, f"{task[:48]}-s{seed}")
            if h.run.get("infra_error") and not _launched(state, t):
                retry[t] = stage_fn(cfg, state, arm=arm, tasks=[task], seeds=[seed], tag=t,
                                    timeout_s=None, harness_root=roots[arm])
    out["retried"] = _launch_pool(cfg, state, retry, harbor_cmd, launch_fn, f"{POOL_TAG}-retry")
    return out


# ---------------------------------------------------------------------------------------------
# H_star replay
# ---------------------------------------------------------------------------------------------

def _reading_fields(reading) -> dict:
    meta = dict(getattr(reading, "meta", None) or {})
    return {"Y": float(reading.Y_star), "C_star": getattr(reading, "C_star", None),
            "d_star": dict(getattr(reading, "d_star", None) or {}), "status": meta.get("status"),
            "evaluation_error": meta.get("evaluation_error") is True}


def _base_fields(h, y: float) -> dict:
    return {"Y": float(y), "C_star": h.run.get("C_star"), "d_star": dict(h.run.get("d_star") or {}),
            "status": h.run.get("status"), "evaluation_error": bool(h.run.get("evaluation_error"))}


def replay_h_star(cfg: Mapping, state: str | Path, base_hs: Iterable, *, verify_cmd: str | None = None,
                  alw_repo: str | None = None, parallel: int = 1, gate_fn=None, repair_fn=None,
                  verifier_fn=None) -> list[dict]:
    """One replay verdict per ``H_base`` slot (see module doc); runs slots concurrently."""
    from ..targets.alw.harvest import archive_record
    from .fork_eval import granted_budget
    state = Path(state)
    base_src, star_src = hbase_source(state, cfg["_root"]), star_source(state)
    work = test_dir(state) / "replay"
    work.mkdir(parents=True, exist_ok=True)
    inc_reg = al.host_registry(ps.registry_specs(base_src))
    cand_reg = al.host_registry(ps.registry_specs(star_src))
    if inc_reg.evaluation_path(cand_reg) != "path1":
        raise ValueError("H_star changes a trajectory mirror checker: Phase A differs, the replay "
                         "is invalid; run H_star live")
    if gate_fn is None:
        bi = al._bundle(base_src, work / "inc_bundle", cfg)
        bc = al._bundle(star_src, work / "cand_bundle", cfg)
        image = al.gate_image(cfg)

        def gate_fn(which, t):
            return al.gate_view(bi if which == "inc" else bc, t,
                                work / "gate_tmp" / f"{which}-{t['task_id']}-s{int(t.get('seed', 0))}",
                                harness_src=base_src if which == "inc" else star_src, image=image)
    if verifier_fn is None:
        if not verify_cmd:
            raise ValueError("the H_star replay needs --verify-cmd")
        from ..bank.phase2 import make_alw_verifier_fn
        verifier_fn = make_alw_verifier_fn(verify_cmd, alw_repo, dict(os.environ))
    readings: dict[str, Any] = {}

    def verify(task, sub, out):
        r = verifier_fn(task, sub, out)
        readings[str(Path(out))] = r
        return r
    cand_gates: dict[tuple[str, int], dict] = {}
    ctx = None
    if repair_fn is None:
        ctx = ps._proxy_from_config(cfg)
        proxy = ctx.__enter__()
        repair_fn = al.make_phase_b_repair_fn(
            cfg, cand_bundle=work / "cand_bundle", cand_gates=cand_gates, work=work / "repairs",
            verifier_fn=verify, proxy=proxy, rows=[], image=ps.proposer_image(cfg))

    def pre_score(h, t, key) -> dict:
        """Score of the archived pre-repair submission (``H_star`` kept it)."""
        if t.get("Y_pre") is not None:
            return _base_fields(h, t["Y_pre"])
        sub = Path(t["snapshot_dir"]) / "pre_repair" / "workspace" / "submission"
        r = verify(key[0], sub, work / "y_pre" / f"{key[0]}__s{key[1]}")
        return {} if r.Y_star is None else _reading_fields(r)

    def one(h) -> dict:
        key = (str(h.run["task_id"]), int(h.run.get("seed", 0)))
        rec: dict[str, Any] = {"task_id": key[0], "seed": key[1], "base_run_id": h.run.get("run_id")}
        if not h.scored:
            return rec | {"verdict": "base_unscored"}
        try:
            t = archive_record(h, inc_reg)
        except ValueError as exc:
            return rec | {"verdict": "replay_unavailable", "detail": str(exc)[:300]}
        try:
            iv, cv = gate_fn("inc", t), gate_fn("cand", t)
        except Exception as exc:  # noqa: BLE001 - a gate fault is an infra fault of this slot
            return rec | {"verdict": "gate_infra_error", "detail": f"{type(exc).__name__}: {exc}"[:300]}
        isig, csig = al.gate_signature(iv), al.gate_signature(cv)
        rec |= {"inc_triggered": isig[0], "cand_triggered": csig[0]}
        if csig == isig:
            return rec | {"verdict": "identical", "source": "H_base_final"} | _base_fields(h, t["Y_inc_final"])
        if not csig[0]:
            got = pre_score(h, t, key)
            return rec | ({"verdict": "not_triggered", "source": "pre_repair"} | got if got
                          else {"verdict": "pre_verify_infra_error"})
        cand_gates[key] = cv["gate"]
        res: dict = {}
        for _ in range(2):                       # one retry of an infra fault
            res = dict(repair_fn(t, 0, granted_budget(t)))
            if not res.get("infra_error"):
                break
        cost = {"llm_calls": res.get("llm_calls"), "proxy": res.get("proxy")}
        if res.get("infra_error"):
            return rec | {"verdict": "replay_infra_error", "repair": res}
        reading = readings.get(str(work / "repairs" / f"{key[0]}__s{key[1]}__r0" / "verify"))
        if reading is not None and reading.Y_star is not None:
            got = _reading_fields(reading)
        elif res.get("Y_post") is not None:      # submission unchanged: the pre-repair score
            got = _base_fields(h, res["Y_post"])
        else:
            got = pre_score(h, t, key)
        if not got:
            return rec | {"verdict": "pre_verify_infra_error", "replay_cost": cost}
        return rec | {"verdict": "replayed", "source": "H_star_phase_b",
                      "ratchet_promoted": bool(res.get("ratchet_promoted")),
                      "promote_reason": res.get("promote_reason"), "replay_cost": cost} | got

    slots = list(final_slots(base_hs).values())
    try:
        with ThreadPoolExecutor(max_workers=max(1, int(parallel))) as ex:
            verdicts = list(ex.map(one, slots))
    finally:
        if ctx is not None:
            ctx.__exit__(None, None, None)
    al.save_json(work / "replay_summary.json", {"n_slots": len(verdicts), "by_verdict": _count(verdicts)})
    with open(work / "replay.jsonl", "w") as fh:
        for v in verdicts:
            fh.write(json.dumps(v, sort_keys=True, default=str) + "\n")
    return verdicts


def _count(verdicts: Iterable[Mapping]) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in verdicts:
        out[v["verdict"]] = out.get(v["verdict"], 0) + 1
    return out


def star_record(h, v: Mapping) -> dict:
    """``H_star`` run record of one ``H_base`` slot from its replay verdict."""
    r = dict(h.run) | {"arm": "H_star", "run_id": f"{h.run.get('run_id')}::H_star",
                       "replay_verdict": v["verdict"], "m": None}
    if v["verdict"] == "base_unscored":          # same outcome as the paired H_base slot
        return r | {"reused_from": "H_base"}
    if "Y" not in v:                             # replay could not be completed: infra fault
        return r | {"Y_star": None, "C_star": None, "container_error": True,
                    "infra_error": True, "infra_category": "setup_crash",
                    "infra_reasons": [v["verdict"]]}
    r |= {"Y_star": v["Y"], "C_star": v.get("C_star"), "d_star": v.get("d_star") or {},
          "status": v.get("status"), "evaluation_error": bool(v.get("evaluation_error")),
          "replay_source": v.get("source")}
    proxy = (v.get("replay_cost") or {}).get("proxy") or {}
    if proxy:
        cost = dict(r.get("cost") or {})
        for k in ("input_tokens", "output_tokens"):
            cost[k] = (cost.get(k) or 0) + (proxy.get(k) or 0)
        r |= {"cost": cost, "cost_note": "H_base run (incl. its own Phase B) + H_star Phase B replay"}
    return r


def write_test_runs(state: str | Path, raw_hs: list, base_hs: list, star_recs: list[dict]) -> Path:
    from ..targets.alw.harvest import atomic_write_text
    rows = [h.run for h in raw_hs] + [h.run for h in base_hs] + list(star_recs)
    return atomic_write_text(runs_path(state),
                             "".join(json.dumps(r, sort_keys=True, default=str) + "\n" for r in rows))


def run_holdout(cfg: Mapping, state: str | Path, *, tasks: list[str], seeds: list[int],
                step: str = "all", harbor_cmd: str | None = None, verify_cmd: str | None = None,
                alw_repo: str | None = None, dry_run: bool = False, **fns) -> dict:
    """``launch`` (live arms), ``replay`` (``H_star`` + ``test_runs.jsonl``) or ``all``.
    The returned summary carries counts only."""
    state = Path(state)
    out: dict[str, Any] = {"manifest": write_manifest(cfg, state, seeds=len(seeds), n_tasks=len(tasks))}
    harvest_fn = fns.get("harvest_fn") or _default_harvest(cfg)
    if step in ("all", "launch"):
        out["launch"] = launch_live(cfg, state, tasks=tasks, seeds=seeds, harbor_cmd=harbor_cmd,
                                    dry_run=dry_run, stage_fn=fns.get("stage_fn"),
                                    launch_fn=fns.get("launch_fn"), harvest_fn=harvest_fn)
    if dry_run or step not in ("all", "replay"):
        return out
    raw_hs, base_hs = harvest_live(state, "H_raw", harvest_fn), harvest_live(state, "H_base", harvest_fn)
    want = {(t, s) for t in tasks for s in seeds}
    missing = {a: len(want - set(final_slots(hs))) for a, hs in (("H_raw", raw_hs), ("H_base", base_hs))}
    if any(missing.values()):
        raise ValueError(f"live arms incomplete (missing slots {missing}); run --step launch first")
    parallel = int((cfg.get("run") or {}).get("parallel") or 1)
    verdicts = replay_h_star(cfg, state, base_hs, verify_cmd=verify_cmd, alw_repo=alw_repo,
                             parallel=parallel, gate_fn=fns.get("gate_fn"),
                             repair_fn=fns.get("repair_fn"), verifier_fn=fns.get("verifier_fn"))
    by = {(v["task_id"], v["seed"]): v for v in verdicts}
    slots = final_slots(base_hs)
    stars = [star_record(slots[k], by[k]) for k in sorted(slots)]
    p = write_test_runs(state, raw_hs, base_hs, stars)
    infra = lambda hs: sum(bool(h.run.get("infra_error")) for h in final_slots(hs).values())  # noqa: E731
    out["replay"] = {"by_verdict": _count(verdicts), "runs": str(p),
                     "slots": {"H_raw": len(final_slots(raw_hs)), "H_base": len(slots), "H_star": len(stars)},
                     "infra_slots": {"H_raw": infra(raw_hs), "H_base": infra(base_hs),
                                     "H_star": sum(bool(s.get("infra_error")) for s in stars)}}
    return out
