"""Phase 3 autonomous outer loop (server side): propose -> Stage 0 screen -> Stage 1 evaluation.

Round ``g`` lives in ``STATE/loop/round_<g>/``::

    attempt_<a>/proposer_ws/       sandbox of attempt a (see ``proposer_sandbox``)
    attempt_<a>/proposer/          stream, stderr, metered result of the proposer session
    attempt_<a>/screen/            candidate.patch, stage0_report.json
    candidate.json                 the Stage-0-passing candidate of the round
    stage1/                        Path 1 replay or Path 2 batches, repair_results.jsonl
    round_report_redacted.json     <USER>-scrubbed, BlindBoundary-guarded summary

The incumbent source is ``STATE/loop/h_inc_harness`` (after the first promotion) or the clean
``H_base`` staging ``STATE/loop/h_base_src``; ``STATE/loop/h_inc.json`` records the version.
Proposer and referee costs are appended to ``STATE/ledgers/offline_cost.jsonl``.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from ..bank.blind_clusters import BlindBoundary, BlindSet
from . import proposer_sandbox as ps

R_REP = 3
B1_SIZE = 2


# ---------------------------------------------------------------------------------------------
# state
# ---------------------------------------------------------------------------------------------

def loop_dir(state: Path) -> Path:
    return Path(state) / "loop"


def load_h_inc(state: Path) -> dict:
    p = loop_dir(state) / "h_inc.json"
    return json.loads(p.read_text()) if p.is_file() else \
        {"version_id": "H_base", "gates": [], "changes": {}, "round": 0}


def save_json(path: Path, obj: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(obj, indent=1, sort_keys=True, default=str))
    os.replace(tmp, path)
    return path


def inc_source(state: Path, repo_root: str | Path) -> Path:
    """Host copy of the incumbent source tree (never the sandbox copy)."""
    from ..cli import default_h_inc_harness
    h = default_h_inc_harness(state)
    if h is not None:
        return h
    base = loop_dir(state) / "h_base_src"
    if not (base / "container" / "alw" / "settings.json").is_file():
        ps.stage_clean_hbase_source(repo_root, base)
    return base


def current_round(state: Path, explicit: int | None = None) -> int:
    return int(explicit) if explicit is not None else int(load_h_inc(state).get("round", 0)) + 1


def round_dir(state: Path, g: int) -> Path:
    return loop_dir(state) / f"round_{g}"


def attempts(state: Path, g: int) -> list[int]:
    rd = round_dir(state, g)
    if not rd.is_dir():
        return []
    return sorted(int(p.name.split("_", 1)[1]) for p in rd.iterdir()
                  if p.is_dir() and p.name.startswith("attempt_") and p.name[8:].isdigit())


def offline_cost_path(state: Path) -> Path:
    return Path(state) / "ledgers" / "offline_cost.jsonl"


def _copy_src(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


# ---------------------------------------------------------------------------------------------
# propose and screen
# ---------------------------------------------------------------------------------------------

def run_propose(cfg: Mapping, state: Path, *, round_id: int, attempt: int, catalog: Mapping,
                split: Mapping, blind: BlindSet, feedback: Mapping | None = None,
                max_turns: int = 80, dry_run: bool = False,
                proposer_fn: Callable[..., dict] | None = None) -> dict:
    """Stage the sandbox of one attempt and run the proposer session in it."""
    ad = round_dir(state, round_id) / f"attempt_{attempt}"
    ws = ad / "proposer_ws"
    inc = inc_source(state, cfg["_root"])
    if ws.exists():
        shutil.rmtree(ws)
    ws.mkdir(parents=True)
    _copy_src(inc, ws / "harness_src")
    _copy_src(inc, ws / "candidate_src")
    ev = ps.export_visible_evidence(state, ws / "evidence", catalog=catalog, split=split,
                                    round_id=round_id, blind=blind)
    h_inc = load_h_inc(state)
    prompt = ps.build_proposer_prompt(round_id, inc_version=h_inc["version_id"], attempt=attempt,
                                      feedback=feedback)
    (ws / "PROPOSER_TASK.md").write_text(prompt)
    out = {"round": round_id, "attempt": attempt, "proposer_ws": str(ws), "evidence": ev,
           "inc_tree_sha256": ps.tree_hash(inc), "dry_run": dry_run}
    if dry_run:
        return out | {"proposer": {"planned": True}}
    res = (proposer_fn or ps.run_proposer_docker)(
        ws, cfg, out_dir=ad / "proposer", max_turns=max_turns,
        offline_cost_path=offline_cost_path(state),
        cost_meta={"round": round_id, "attempt": attempt})
    return out | {"proposer": res}


def run_screen(cfg: Mapping, state: Path, *, round_id: int, attempt: int, catalog: Mapping,
               blind: BlindSet, alw_repo: str | None, use_docker: bool | None = None,
               evaluator=None, smoke_fn=None) -> dict:
    ad = round_dir(state, round_id) / f"attempt_{attempt}"
    ws = ad / "proposer_ws"
    inc = inc_source(state, cfg["_root"])
    t0 = time.monotonic()
    rep = ps.validate_and_screen_proposal(
        inc, ws / "candidate_src", ws / "proposal.json", bank_path=Path(state) / "bank" / "b_dev.jsonl",
        blind=blind, cfg=cfg, alw_repo=alw_repo, work_dir=ad / "screen", use_docker=use_docker,
        catalog=catalog, evaluator=evaluator, smoke_fn=smoke_fn,
        cache_dir=loop_dir(state) / "cert_cache")
    ps.append_offline_cost(offline_cost_path(state), {
        "kind": "stage0_screen", "round": round_id, "attempt": attempt,
        "wall_sec": round(time.monotonic() - t0, 3), "ok": rep["ok"], "llm_calls": 0})
    if rep["ok"]:
        cand = {"round": round_id, "attempt": attempt, "candidate_id": rep["candidate_id"],
                "mutation_class": rep["mutation_class"], "evaluation_path": rep["evaluation_path"],
                "checker": rep.get("checker"), "cand_src": str(ws / "candidate_src"),
                "cand_tree_sha256": ps.tree_hash(ws / "candidate_src"),
                "inc_tree_sha256": ps.tree_hash(inc),
                "n_registry_mutations": rep["checks"].get("n_registry_mutations", 0)}
        save_json(round_dir(state, round_id) / "candidate.json", cand)
    return rep


# ---------------------------------------------------------------------------------------------
# Stage 1 Path 1: terminal gate views and stateless Phase B repairs
# ---------------------------------------------------------------------------------------------

PROMPT_SHA = r"""
import hashlib, json, sys
sys.path.insert(0, sys.argv[1])
from ssap.checkers.alw.repair import repair_prompt
d = json.loads(sys.stdin.read())
print(json.dumps(hashlib.sha256(repair_prompt(d["gate"], d["fork"], d["handoff"],
                                              "/alb-workspace").encode()).hexdigest()))
"""
DOCKER_PY = 'P=/opt/analog-layout/python/bin/python3.12; [ -x "$P" ] || P=python3; exec "$P" "$@"'
PHASE_B_DRIVER = r"""
import json, os, sys
sys.path.insert(0, "/opt/harness/bin")
import mcr_entrypoint as me
import mcr_common as mc
from ssap.kernel.promote import load_best, sha256_file
ws, suite = mc.workspace(), mc.suite()
mcr = mc.mcr_dir()
gate = json.loads((mcr / "gate_report.json").read_text())
fork = json.loads((mcr / "fork_meta.json").read_text())
budget = float(os.environ["MCR_PB_BUDGET"])
turns = int(os.environ["MCR_PB_TURNS"])
argv = list(fork.get("phase_a_argv") or ["--verbose", "--output-format=stream-json", "-p"])
before = load_best(ws)
b_sha = before.artifact_sha256 if before else None
s_sha = sha256_file(ws.submission_artifact)
(mcr / "phase_b_sessions").mkdir(parents=True, exist_ok=True)
with open(mcr / "phase_b_stream.jsonl", "wb") as out, open(mcr / "phase_b_stderr.log", "wb") as err:
    pb = me.run_bounded([me.REAL_CLAUDE] + me.phase_b_args(argv, turns), prompt=me.repair_prompt(gate, fork),
                        timeout=budget, env=dict(os.environ), grace=5.0, stdout=out, stderr=err)
me.terminate_strays({pb["session"]}, 5.0)
pending = me._promote_pending(ws, suite)
after = load_best(ws)
promoted = bool(after and after.artifact_sha256 != b_sha)
reason = (pending or {}).get("reason") if pending else ("promoted_during_repair" if promoted else "no_new_candidate")
print(json.dumps({"phase_b": pb, "pending_promotion": pending, "ratchet_promoted": promoted,
                  "promote_reason": reason, "submission_sha256_before": s_sha,
                  "submission_sha256_after": sha256_file(ws.submission_artifact)}))
"""


def _fork_meta(snap: Path) -> dict:
    from ..targets.alw.agent_wrapper import find_fork_meta
    p = find_fork_meta(snap / "pre_repair")
    return json.loads(p.read_text()) if p else {}


def _bundle(src: Path, dest: Path, cfg: Mapping) -> Path:
    from ..targets.alw.agent_wrapper import stage_harness_bundle
    stage_harness_bundle(src, dest, watermarks=cfg.get("watermarks"), check_timeout_sec=900)
    return dest


def _docker_gate(bundle: Path, ws: Path, elapsed: float, image: str, harness_python: str,
                 prompt_input: Callable[[dict], str | None]) -> tuple[dict, str | None]:
    """``mcr_check --gate`` and the prompt sha inside ``docker run --network none`` (EDA tools
    and the image's python live only in the agent image on the server)."""
    base = ["docker", "run", "--rm", "-i", "--network", "none", "--cpus", "2", "--memory", "4g",
            "--entrypoint", "/bin/sh", "-v", f"{bundle.resolve()}:/opt/harness:ro",
            "-v", f"{ws.resolve()}:/alb-workspace"]
    trusted = ws / ".mcr" / "trusted_task"
    if trusted.is_dir():
        base += ["-v", f"{trusted.resolve()}:/alb-workspace/.mcr/trusted_task:ro"]
    base += ["-w", "/alb-workspace", "-e", "PYTHONPATH=/opt/harness/python",
             "-e", "MCR_HARNESS_PYTHON=/opt/harness/python", "-e", "MCR_WORKSPACE=/alb-workspace",
             "-e", "MCR_CONFIG=/opt/harness/mcr_config.json", image, "-c", DOCKER_PY, "sh"]
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", "/tmp")}
    p = subprocess.run(base + ["/opt/harness/bin/mcr_check", "--gate", "--elapsed", str(float(elapsed))],
                       input="", capture_output=True, text=True, env=env, timeout=900)
    gate = json.loads(p.stdout)
    payload = prompt_input(gate)
    if payload is None:
        return gate, None
    q = subprocess.run(base + ["-c", PROMPT_SHA, harness_python], input=payload, capture_output=True,
                       text=True, env=env, timeout=300)
    if q.returncode != 0:
        raise RuntimeError(f"prompt sha failed in container: {q.stderr[-300:]}")
    return gate, json.loads(q.stdout)


def gate_view(bundle: Path, trial: Mapping, work: Path, *, harness_src: Path,
              image: str | None = None) -> dict:
    """Terminal gate of *bundle* on a copy of the archived ``pre_repair`` workspace plus the sha256
    of the bundle's repair prompt (zero LLM, zero verifier). With *image* both run inside the
    agent image (network-less); without it on the host interpreter."""
    import sys
    snap = Path(trial["snapshot_dir"])
    ws = work / "alb-workspace"
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    shutil.copytree(snap / "pre_repair" / "workspace", ws, symlinks=True)
    fork = _fork_meta(snap)
    elapsed = float(fork.get("phase_a_elapsed_sec") or 0.0)
    hp = ws / ".mcr" / "handoff.json"
    try:
        handoff = json.loads(hp.read_text()) if hp.is_file() else None
    except ValueError:
        handoff = None
    fork_view = {"granted_repair_budget_sec": fork.get("granted_repair_budget_sec"),
                 "granted_repair_turns": fork.get("granted_repair_turns"),
                 "phase_a_elapsed_sec": fork.get("phase_a_elapsed_sec")}

    def payload(gate: dict) -> str | None:
        if not gate.get("gate_triggered"):
            return None
        return json.dumps({"gate": gate, "fork": fork_view, "handoff": handoff}, default=str)
    if image:
        gate, sha = _docker_gate(bundle, ws, elapsed, image, "/opt/harness/python", payload)
    else:
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(work),
               "MCR_HARNESS_PYTHON": str(bundle / "python"), "PYTHONPATH": str(bundle / "python"),
               "MCR_WORKSPACE": str(ws), "MCR_CONFIG": str(bundle / "mcr_config.json")}
        p = subprocess.run([sys.executable, "-I", str(bundle / "bin" / "mcr_check"), "--gate",
                            "--elapsed", str(elapsed)], capture_output=True, text=True, env=env,
                           cwd=ws, timeout=600)
        gate = json.loads(p.stdout)
        data = payload(gate)
        sha = ps._isolated(PROMPT_SHA, [str(Path(harness_src) / "src")], stdin=data) if data else None
    shutil.rmtree(work, ignore_errors=True)
    return {"gate": gate, "triggered": bool(gate.get("gate_triggered")),
            "reasons": sorted(str(r) for r in gate.get("trigger_reasons") or []), "prompt_sha256": sha}


def local_gate_view(bundle: Path, trial: Mapping, work: Path, *, harness_src: Path) -> dict:
    return gate_view(bundle, trial, work, harness_src=harness_src, image=None)


def gate_image(cfg: Mapping) -> str | None:
    """Agent image for the terminal gate when Docker and the image are available, else ``None``."""
    from ..bank.concordance import _docker_image_present
    image = ps.proposer_image(cfg)
    return image if shutil.which("docker") is not None and _docker_image_present(image) else None


def gate_signature(view: Mapping) -> tuple[bool, list[str]]:
    sig = list(view["reasons"]) + ([f"prompt:{view['prompt_sha256']}"] if view.get("triggered") else [])
    return bool(view["triggered"]), sig


def make_phase_b_repair_fn(cfg: Mapping, *, cand_bundle: Path, cand_gates: Mapping, work: Path,
                           verifier_fn, proxy, rows: list, image: str):
    """``repair(trial, rep_seed, budget)``: one stateless Phase B container on a copy of the
    archived ``pre_repair`` workspace, then pending promotion as in ``mcr_entrypoint.py``."""
    from ..targets.alw.agent_wrapper import build_stateless_repair_invocation

    def repair(trial, rep_seed, budget):
        key = (str(trial["task_id"]), int(trial.get("seed", 0)))
        snap = Path(trial["snapshot_dir"])
        rd = work / f"{key[0]}__s{key[1]}__r{rep_seed}"
        if rd.exists():
            shutil.rmtree(rd)
        ws = rd / "alb-workspace"
        shutil.copytree(snap / "pre_repair" / "workspace", ws, symlinks=True)
        save_json(ws / ".mcr" / "gate_report.json", cand_gates[key])
        save_json(rd / "gate_report.json", cand_gates[key])
        name = f"pb-{key[0][:40]}-s{key[1]}-r{rep_seed}-{int(time.time() * 1000) % 10**8}"
        token = proxy.new_trial(name)
        try:
            inv = build_stateless_repair_invocation(
                snap / "pre_repair", rd / "gate_report.json", model=ps._model(cfg),
                proxy_url=f"http://host.docker.internal:{proxy.port}", trial_token=token, cfg=cfg)
            env = dict(inv.env) | {"MCR_PB_BUDGET": str(float(budget)),
                                   "MCR_PB_TURNS": str(inv.max_turns), "MCR_CHECK_TIMEOUT_SEC": "900"}
            cmd = ["docker", "run", "--rm", "-i", "--cpus", "4", "--memory", "16g",
                   "--add-host", "host.docker.internal:host-gateway",
                   "-v", f"{cand_bundle.resolve()}:/opt/harness:ro", "-v", f"{ws.resolve()}:/alb-workspace",
                   "-w", "/alb-workspace", "--entrypoint", "/bin/sh"]
            trusted = ws / ".mcr" / "trusted_task"
            if trusted.is_dir():
                cmd += ["-v", f"{trusted.resolve()}:/alb-workspace/.mcr/trusted_task:ro"]
            for k in sorted(env):
                cmd += ["-e", f"{k}={env[k]}"] if k in ("PATH", "PYTHONPATH") else ["-e", k]
            cmd += [image, "-c", DOCKER_PY, "sh", "-c", PHASE_B_DRIVER]
            client = {**env, "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                      "HOME": os.environ.get("HOME", "/tmp")}
            try:
                p = subprocess.run(cmd, input=b"", capture_output=True, env=client,
                                   timeout=float(budget) + 1800)
                rc, out = p.returncode, p.stdout.decode(errors="replace")
            except subprocess.TimeoutExpired:
                rc, out = None, ""
        finally:
            proxy.revoke(token)
            stats = proxy.stats(name)
        ps._chown_back(image, rd, "/w")
        drv = None
        for line in reversed(out.strip().splitlines()):
            try:
                drv = json.loads(line)
                break
            except ValueError:
                continue
        row = {"task_id": key[0], "seed": key[1], "rep_seed": rep_seed, "returncode": rc,
               "llm_calls": int(stats.get("proxy_requests") or 0), "verifier_calls": 0,
               "proxy": {k: stats.get(k) for k in ("proxy_requests", "input_tokens", "output_tokens",
                                                   "unrecovered_gateway_error")}}
        if rc != 0 or not isinstance(drv, dict) or stats.get("unrecovered_gateway_error"):
            row |= {"infra_error": True, "status": "tool_error",
                    "infra_record": {"proxy_requests": stats.get("proxy_requests"),
                                     "unrecovered_gateway_error": stats.get("unrecovered_gateway_error"),
                                     "exit_code": rc}}
            rows.append(row)
            return row
        row |= {"ratchet_promoted": drv["ratchet_promoted"], "promote_reason": drv["promote_reason"]}
        if drv["submission_sha256_after"] != drv["submission_sha256_before"]:
            reading = verifier_fn(key[0], ws / "submission", rd / "verify")
            row["verifier_calls"] = 1
            if reading.Y_star is None:
                row |= {"infra_error": True, "status": "verifier_fault",
                        "infra_record": {"verifier_infra": True}}
                rows.append(row)
                return row
            row["Y_post"] = float(reading.Y_star)
        else:
            row["Y_post"] = trial.get("Y_pre")
        rows.append(row)
        return row
    return repair


def host_registry(specs: list[dict]):
    from dataclasses import fields
    from ..checkers.alw.registry import CheckerSpec, Registry
    names = {f.name for f in fields(CheckerSpec)}
    return Registry([CheckerSpec(**{k: (tuple(v) if k == "depends_on" else v)
                                    for k, v in s.items() if k in names}) for s in specs])


def _dev_ids(split: Mapping) -> set[str]:
    return set(split["task_ids"]["dev"])


def run_stage1_path1(cfg: Mapping, state: Path, cand: Mapping, *, round_id: int, split: Mapping,
                     verify_cmd: str | None, alw_repo: str | None, gate_fn=None, repair_fn=None,
                     verifier_fn=None, dry_run: bool = False) -> dict:
    """Terminal-gate replay of the candidate on archived ``H_inc`` dev trials (see module doc)."""
    from .event_ledger import EventLedger
    from .fork_eval import R_REP as FR_REP, evaluate_path1_fork
    from .infra_ledger import InfraLedger
    if FR_REP != R_REP:
        raise ValueError("R_rep mismatch")
    inc_src, cand_src = inc_source(state, cfg["_root"]), Path(cand["cand_src"])
    work = round_dir(state, round_id) / "stage1"
    work.mkdir(parents=True, exist_ok=True)
    ap = ps.h_inc_archive_path(state, round_id)
    archive = json.loads(ap.read_text())
    bad = sorted({str(t["task_id"]) for t in archive} - _dev_ids(split))
    if bad:
        raise ValueError(f"Path 1 replays dev archives only; {len(bad)} non-dev task(s)")
    inc_specs, cand_specs = ps.registry_specs(inc_src), ps.registry_specs(cand_src)
    inc_reg, cand_reg = host_registry(inc_specs), host_registry(cand_specs)
    muts = ps.registry_mutation_names(inc_specs, cand_specs)
    extra = [] if len(muts) == 1 else [f"class{cand['mutation_class']}:{cand['candidate_id']}"]
    if gate_fn is None:
        bi, bc = _bundle(inc_src, work / "inc_bundle", cfg), _bundle(cand_src, work / "cand_bundle", cfg)
        image = gate_image(cfg)
        gate_fn = lambda which, t: gate_view(  # noqa: E731
            bi if which == "inc" else bc, t, work / "gate_tmp" / which,
            harness_src=inc_src if which == "inc" else cand_src, image=image)
    trials, cand_views = [], {}
    for t in archive:
        t = dict(t)
        key = (str(t["task_id"]), int(t.get("seed", 0)))
        iv, cv = gate_fn("inc", t), gate_fn("cand", t)
        t["inc_triggered"], t["inc_gate_signature"] = gate_signature(iv)
        cand_views[key] = cv
        trials.append(t)
    sig = lambda t: gate_signature(cand_views[(str(t["task_id"]), int(t.get("seed", 0)))])  # noqa: E731
    divergent = [t for t in trials if sig(t) != (bool(t["inc_triggered"]), list(t["inc_gate_signature"]))]
    out = {"path": "path1", "candidate_id": cand["candidate_id"], "n_trials": len(trials),
           "n_divergent": len(divergent), "archive": str(ap)}
    if dry_run:
        return out | {"dry_run": True, "promoted": False, "reason": "dry_run"}
    if divergent and not (verifier_fn or verify_cmd):
        raise ValueError("divergent trials need --verify-cmd (Y_pre / Y_post verification)")
    if verifier_fn is None and verify_cmd:
        from ..bank.phase2 import make_alw_verifier_fn
        verifier_fn = make_alw_verifier_fn(verify_cmd, alw_repo, dict(os.environ))
    unresolved_pre, keep = [], []
    for t in trials:
        if t in divergent and t.get("Y_pre") is None:
            snap = Path(t["snapshot_dir"])
            r = verifier_fn(str(t["task_id"]), snap / "pre_repair" / "workspace" / "submission",
                            work / "y_pre" / f"{t['task_id']}__s{int(t.get('seed', 0))}")
            if r.Y_star is None:
                unresolved_pre.append({"task_id": t["task_id"], "seed": int(t.get("seed", 0))})
                continue
            t["Y_pre"] = float(r.Y_star)
        keep.append(t)
    rows: list[dict] = []
    ledgers = Path(state) / "ledgers"
    infra = InfraLedger(ledgers / "infra_ledger.jsonl", theta_gw_max=float(
        (cfg.get("gateway") or {}).get("theta_gw_max", 0.35)))
    ctx = None
    if repair_fn is None and any(sig(t)[0] for t in divergent):
        ctx = ps._proxy_from_config(cfg)
        proxy = ctx.__enter__()
        repair_fn = make_phase_b_repair_fn(
            cfg, cand_bundle=work / "cand_bundle", work=work / "repairs", verifier_fn=verifier_fn,
            proxy=proxy, rows=rows, image=ps.proposer_image(cfg),
            cand_gates={k: v["gate"] for k, v in cand_views.items()})
    try:
        res = evaluate_path1_fork(keep, inc_registry=inc_reg, cand_registry=cand_reg, cand_gate=sig,
                                  repair=repair_fn or (lambda *a: {"infra_error": True}),
                                  candidate_id=cand["candidate_id"], round_id=round_id,
                                  ledger=EventLedger(ledgers / "event_ledger.jsonl"),
                                  infra_ledger=infra, r_rep=R_REP, extra_mutations=extra)
    finally:
        if ctx is not None:
            ctx.__exit__(None, None, None)
    if rows:
        (work / "repair_results.jsonl").write_text(
            "".join(json.dumps(r, sort_keys=True, default=str) + "\n" for r in rows))
    if unresolved_pre:
        res["unresolved"] = list(res.get("unresolved") or []) + unresolved_pre
        res["promoted"], res["reason"] = False, "unresolved_infra_error"
    out["next_archive"] = refreshed_archive(trials, res["trials"], sig, cand["candidate_id"])
    return out | {k: v for k, v in res.items() if k != "trials"} | {"trials": res["trials"]}


def refreshed_archive(trials: list[dict], rows: list[dict], cand_sig, candidate_id: str) -> list[dict]:
    """The Path 1 archive as seen by ``H_cand`` (written for round ``g+1`` only on promotion):
    divergent trials take ``Y_inc_final = Y_cand`` and the candidate's gate decision."""
    by = {(str(r["task_id"]), int(r.get("seed", 0))): r for r in rows}
    out = []
    for t in trials:
        t = dict(t)
        r = by.get((str(t["task_id"]), int(t.get("seed", 0))))
        if r is not None and r.get("divergent") and r.get("Y_cand") is not None:
            t["Y_inc_final"] = float(r["Y_cand"])
            t["inc_triggered"], t["inc_gate_signature"] = cand_sig(t)
            t["refreshed_by"] = candidate_id
        out.append(t)
    return out


def promote_candidate(state: Path, cand: Mapping, result: Mapping, *, round_id: int) -> dict:
    """``H_inc <- H_cand``: ``h_inc_harness`` replaced atomically, ``h_inc.json`` updated."""
    loop = loop_dir(state)
    h = load_h_inc(state)
    dst, tmp = loop / "h_inc_harness", loop / f".h_inc_harness.r{round_id}.tmp"
    _copy_src(Path(cand["cand_src"]), tmp)
    if dst.exists():
        prev = loop / "history" / f"h_inc_harness.before_r{round_id}"
        if prev.exists():
            shutil.rmtree(prev)
        prev.parent.mkdir(parents=True, exist_ok=True)
        os.replace(dst, prev)
    os.replace(tmp, dst)
    new = dict(h) | {"version_id": f"{h['version_id']}+{cand['candidate_id']}", "round": round_id,
                     "harness": "loop/h_inc_harness", "harness_tree_sha256": ps.tree_hash(dst)}
    new["auto_mutations"] = list(h.get("auto_mutations") or []) + [{
        "round": round_id, "candidate_id": cand["candidate_id"],
        "mutation_class": cand["mutation_class"], "evaluation_path": result.get("path"),
        "checker": cand.get("checker")}]
    save_json(loop / "h_inc.json", new)
    return new


# ---------------------------------------------------------------------------------------------
# Stage 1 Path 2: nested batches B_1 (2 visible dev tasks) then B_2 (remaining dev tasks)
# ---------------------------------------------------------------------------------------------

def _batch(cfg, state, tag, tasks, seeds, cand_src, harbor_cmd, stage_fn, launch_fn, harvest_fn):
    jobs = stage_fn(cfg, state, arm="H_star", tasks=tasks, seeds=seeds, tag=tag, timeout_s=None,
                    harness_root=cand_src)
    launch_fn(cfg, jobs, harbor_cmd, state, tag)
    return list(harvest_fn(Path(state) / "jobs" / tag))


def _with_retry(cfg, state, tag, tasks, seeds, cand_src, harbor_cmd, fns) -> tuple[list, list]:
    """Harvested runs of one batch with one paired retry of every ``infra_error`` slot."""
    hs = _batch(cfg, state, tag, tasks, seeds, cand_src, harbor_cmd, *fns)
    bad = [h for h in hs if h.run.get("infra_error")]
    retried = []
    for h in bad:
        t, s = str(h.run["task_id"]), int(h.run.get("seed", 0))
        r = _batch(cfg, state, f"{tag}-retry-{t[:48]}-s{s}", [t], [s], cand_src, harbor_cmd, *fns)
        retried += r
    keep = [h for h in hs if not h.run.get("infra_error")] + retried
    unresolved = [{"task_id": h.run["task_id"], "seed": h.run.get("seed")} for h in keep
                  if h.run.get("infra_error")]
    return keep, unresolved


def run_stage1_path2(cfg: Mapping, state: Path, cand: Mapping, *, round_id: int, split: Mapping,
                     blind: BlindSet, harbor_cmd: str | None, eval_seeds: int = 1,
                     inc_job_dir: str | None = None, stage_fn=None, launch_fn=None, harvest_fn=None,
                     dry_run: bool = False) -> dict:
    from .fork_eval import evaluate_path2_paired
    if stage_fn is None or launch_fn is None:
        from ..cli import _launch, _stage_jobs
        stage_fn, launch_fn = stage_fn or _stage_jobs, launch_fn or _launch
    theta = float((cfg.get("gateway") or {}).get("theta_gw_max", 0.35))
    if harvest_fn is None:
        from ..targets.alw.harvest import harvest_job
        harvest_fn = lambda d: harvest_job(d, theta_gw_max=theta)[0]  # noqa: E731
    dev = list(split["task_ids"]["dev"])
    visible = sorted(t for t in dev if t in blind.visible_task_ids)
    b1 = visible[:B1_SIZE]
    b2 = [t for t in dev if t not in b1]
    seeds = list(range(int(eval_seeds)))
    cand_src = Path(cand["cand_src"])
    tag = f"auto-r{round_id}-{cand['candidate_id']}"
    out = {"path": "path2", "candidate_id": cand["candidate_id"], "n_B1": len(b1), "n_B2": len(b2),
           "seeds": len(seeds)}
    if dry_run or not harbor_cmd:
        if not dry_run:
            raise ValueError("Path 2 needs --harbor-cmd (verified runbook template)")
        return out | {"dry_run": True, "promoted": False, "reason": "dry_run"}
    fns = (stage_fn, launch_fn, harvest_fn)
    hs1, un1 = _with_retry(cfg, state, f"{tag}-b1", b1, seeds, cand_src, harbor_cmd, fns)
    crash1 = [h for h in hs1 if not h.scored and not h.run.get("infra_error")
              and h.run.get("Y_star") is None]
    out["B1"] = {"n_runs": len(hs1), "unresolved_infra": len(un1), "crashed": len(crash1)}
    if un1 or crash1:
        return out | {"promoted": False, "reason": "b1_infra_or_crash"}
    hs2, un2 = _with_retry(cfg, state, f"{tag}-b2", b2, seeds, cand_src, harbor_cmd, fns)
    out["B2"] = {"n_runs": len(hs2), "unresolved_infra": len(un2)}
    cand_hs = hs1 + hs2
    cand_runs = [h.run for h in cand_hs]
    if inc_job_dir:
        inc_runs = [h.run for h in harvest_fn(Path(inc_job_dir))]
    else:
        inc_runs = ps._read_jsonl(ps.h_inc_runs_path(state))
    inc_runs = [dict(r) for r in inc_runs if str(r.get("task_id")) in set(dev)]
    for r in inc_runs + cand_runs:     # legacy records predate harvest's hook_triggered
        r.setdefault("hook_triggered", bool(r.get("m") is not None or r.get("gate_triggered")))
    res = evaluate_path2_paired(inc_runs, cand_runs, dev_task_ids=dev, n_mutations=1,
                                theta_gw_max=theta)
    if un2:
        res["promoted"], res["reason"] = False, "unresolved_infra_error"
    out |= res | {"unresolved": un1 + un2}
    if res.get("promoted"):
        _promote_path2_artifacts(state, cand_hs, round_id=round_id, split=split, cand_src=cand_src)
    return out


def _promote_path2_artifacts(state: Path, hs: list, *, round_id: int, split: Mapping,
                             cand_src: Path) -> None:
    from ..bank.referee_bank import BankSample, RefereeBank
    from ..targets.alw.harvest import archive_record, atomic_write_text, bank_sample
    loop = loop_dir(state)
    atomic_write_text(loop / "h_inc_runs.jsonl",
                      "".join(json.dumps(h.run, sort_keys=True, default=str) + "\n" for h in hs))
    reg = host_registry(ps.registry_specs(cand_src))
    arch = []
    for h in hs:
        if h.scored:
            try:
                arch.append(archive_record(h, reg))
            except ValueError:
                continue
    save_json(round_dir(state, round_id + 1) / "archive.json", arch)
    cluster = ps.dev_cluster_of(split)
    dev_clusters = sorted(set(cluster.values()))
    bank = RefereeBank(Path(state) / "bank" / "b_dev.jsonl", dev_clusters=dev_clusters)
    for h in hs:
        if h.run.get("Y_star") is not None:
            try:
                bank.add(BankSample.from_dict(bank_sample(h, cluster_id=cluster[str(h.run["task_id"])])))
            except ValueError:
                continue


# ---------------------------------------------------------------------------------------------
# round report, round driver, CLI entry
# ---------------------------------------------------------------------------------------------

def redacted_round_report(round_id: int, *, blind: BlindSet, h_inc_before: Mapping,
                          attempts_info: list[dict], cand: Mapping | None, result: Mapping | None,
                          h_inc_after: Mapping) -> dict:
    vis = blind.visible_task_ids
    ev = None
    if result is not None:
        ev = {k: result.get(k) for k in ("path", "promoted", "reason", "sum_delta_Y", "net_delta",
                                         "n_trials", "n_divergent", "llm_calls", "verifier_calls",
                                         "n_B1", "n_B2", "seeds", "dry_run", "reclassified_from")
              if k in result}
        trials = result.get("trials") or []
        ev["visible_trials"] = [{k: t.get(k) for k in ("task_id", "seed", "divergent", "inc_triggered",
                                                       "cand_triggered", "delta_Y", "verdict")}
                                for t in trials if t.get("task_id") in vis]
        bt = [t for t in trials if t.get("task_id") not in vis]
        dys = [t["delta_Y"] for t in bt if isinstance(t.get("delta_Y"), (int, float))]
        ev["blind_aggregate"] = {"n_trials": len(bt), "n_divergent": sum(bool(t.get("divergent")) for t in bt),
                                 "sum_delta_Y": math.fsum(dys) if dys else 0.0}
        ptd = result.get("per_task_delta") or {}
        if ptd:
            ev["visible_per_task_delta"] = {t: v for t, v in ptd.items() if t in vis}
            bv = [v for t, v in ptd.items() if t not in vis]
            ev["blind_aggregate"] |= {"n_tasks": len(bv),
                                      "mean_delta": math.fsum(bv) / len(bv) if bv else None}
        ev["n_unresolved"] = len(result.get("unresolved") or [])
    rep = {"schema_version": 1, "round": round_id, "h_inc_before": h_inc_before.get("version_id"),
           "h_inc_after": h_inc_after.get("version_id"),
           "attempts": [{k: a.get(k) for k in ("attempt", "stage", "reason", "mutation_class",
                                               "evaluation_path", "candidate_id")} for a in attempts_info],
           "candidate": None if cand is None else {k: cand.get(k) for k in (
               "candidate_id", "mutation_class", "evaluation_path", "checker")},
           "evaluation": ev}
    rep = ps.user_scrub(rep, ps._secrets())
    BlindBoundary(blind).guard(rep)
    return rep


def finish_round(state: Path, round_id: int, *, blind: BlindSet, h_inc_before: Mapping,
                 attempts_info: list[dict], cand: Mapping | None, result: Mapping | None,
                 dry_run: bool = False) -> dict:
    h_after = dict(h_inc_before)
    if not dry_run:
        if result is not None and result.get("promoted"):
            h_after = promote_candidate(state, cand, result, round_id=round_id)
            if result.get("path") == "path1" and result.get("next_archive") is not None:
                save_json(round_dir(state, round_id + 1) / "archive.json", result["next_archive"])
        else:                              # round closed without promotion (or without candidate)
            h_after = dict(load_h_inc(state)) | {"round": round_id}
            save_json(loop_dir(state) / "h_inc.json", h_after)
    rep = redacted_round_report(round_id, blind=blind, h_inc_before=h_inc_before,
                                attempts_info=attempts_info, cand=cand, result=result,
                                h_inc_after=h_after)
    if not dry_run:
        rd = round_dir(state, round_id)
        save_json(rd / "round_report_redacted.json", rep)
        if result is not None:            # referee-internal (never exported to proposers)
            save_json(rd / "stage1" / "stage1_result.json", result)
    return rep


def cli_auto(args, cfg: Mapping, state: Path, catalog: Mapping, split: Mapping) -> dict:
    """``alw-loop --auto | --propose | --screen-candidate | --eval-candidate``."""
    blind = ps.load_blind_set(state, split)
    g = current_round(state, args.round)
    alw_repo = getattr(args, "alw_repo", None) or (cfg.get("alw") or {}).get("repo") or None
    h_before = load_h_inc(state)
    out: dict = {"round": g, "dry_run": args.dry_run, "h_inc": h_before.get("version_id")}
    infos: list[dict] = []
    want_propose = args.auto or args.propose
    want_screen = args.auto or args.screen_candidate
    feedback = None
    n_try = max(1, int(args.max_propose_attempts)) if (want_propose and want_screen) else 1
    for _ in range(n_try if (want_propose or want_screen) else 0):
        done = attempts(state, g)
        a = (done[-1] + 1 if done else 1) if want_propose else (done[-1] if done else 1)
        if want_propose:
            out.setdefault("propose", []).append(run_propose(
                cfg, state, round_id=g, attempt=a, catalog=catalog, split=split, blind=blind,
                feedback=feedback, max_turns=args.proposer_max_turns, dry_run=args.dry_run))
            if args.dry_run:
                return out
        if not want_screen:
            break
        rep = run_screen(cfg, state, round_id=g, attempt=a, catalog=catalog, blind=blind,
                         alw_repo=alw_repo, use_docker=None)
        infos.append({"attempt": a, **{k: rep.get(k) for k in ("stage", "reason", "mutation_class",
                                                                "evaluation_path", "candidate_id")}})
        out.setdefault("screen", []).append(ps.stage0_feedback(rep) | {"ok": rep["ok"]})
        if rep["ok"]:
            break
        feedback = ps.stage0_feedback(rep)
    cand_path = round_dir(state, g) / "candidate.json"
    if not (args.auto or args.eval_candidate):
        return out
    if not infos:
        for a in attempts(state, g):
            sp = round_dir(state, g) / f"attempt_{a}" / "screen" / "stage0_report.json"
            if sp.is_file():
                r = json.loads(sp.read_text())
                infos.append({"attempt": a, **{k: r.get(k) for k in (
                    "stage", "reason", "mutation_class", "evaluation_path", "candidate_id")}})
    if not cand_path.is_file():
        if args.auto and infos:
            out["round_report"] = finish_round(state, g, blind=blind, h_inc_before=h_before,
                                               attempts_info=infos, cand=None, result=None)
        out["status"] = "no_stage0_candidate"
        return out
    cand = json.loads(cand_path.read_text())
    if cand.get("inc_tree_sha256") != ps.tree_hash(inc_source(state, cfg["_root"])):
        raise ValueError("candidate was screened against a different incumbent")
    if cand["evaluation_path"] == "path1":
        res = run_stage1_path1(cfg, state, cand, round_id=g, split=split, verify_cmd=args.verify_cmd,
                               alw_repo=alw_repo, dry_run=args.dry_run)
    else:
        res = run_stage1_path2(cfg, state, cand, round_id=g, split=split, blind=blind,
                               harbor_cmd=args.harbor_cmd, eval_seeds=args.eval_seeds,
                               inc_job_dir=args.inc_job_dir, dry_run=args.dry_run)
    out["round_report"] = finish_round(state, g, blind=blind, h_inc_before=h_before,
                                       attempts_info=infos, cand=cand, result=res, dry_run=args.dry_run)
    return out
