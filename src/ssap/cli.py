"""``ssap`` command line (ALW instantiation).

``alw-check-split``  split / leak invariants R1-R5
``alw-concordance``  offline container smoke, B_dev build, 0-token mutations, blind freeze, eps_k
``alw-diagnose``     the four zero-LLM dev diagnostics on archived H_base runs
``alw-harvest``      Harbor job directory -> run records, Path 1 archive, B_dev samples, summaries
``alw-eval``         smoke-10m | subset | baseline (Harbor jobs), fork-path1 (Path 1), c1-val
``alw-loop``         round-by-round Path 1 outer loop with K_idle pruning
``alw-report``       sealed test report (single unblind, R >= 3 seeds)

Commands that would start containers or model calls stage their job configs and plans under
``$SSAP_STATE``; ``--dry-run`` stops there. A live Harbor launch needs an explicit
``--harbor-cmd`` template: the Harbor command line is taken from the server's verified runbook,
never guessed here.
"""
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .config import load_config, resolve

DEFAULT_CONFIG = "configs/alw/config.server.json"
EVAL_MODES = ("smoke-10m", "subset", "baseline", "fork-path1", "c1-val")
SMOKE_T_MAX = 600.0


# ---------------------------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------------------------

def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--config", default=DEFAULT_CONFIG)
    p.add_argument("--state", default=None, help="state directory (default: config state / $SSAP_STATE)")
    g = p.add_argument_group("run overrides")
    g.add_argument("--model", default=None)
    g.add_argument("--parallel", type=int, default=None)
    g.add_argument("--timeout-s", type=float, default=None, help="T_max / agent timeout (s)")
    g.add_argument("--t-warn", type=float, default=None)
    g.add_argument("--t-soft", type=float, default=None)
    g.add_argument("--delta-t-rep", type=float, default=None)
    g.add_argument("--delta-s-rep", type=int, default=None, help="Delta_S_rep repair turn limit")
    g.add_argument("--theta-gw-max", type=float, default=None)
    g.add_argument("--agent-image", default=None)
    g.add_argument("--dry-run", action="store_true", help="plan / stage only; no containers or model calls")


def _cfg(args: argparse.Namespace) -> dict:
    from .targets.alw.harbor_runner import apply_cli_overrides
    cfg = load_config(args.config)
    return apply_cli_overrides(cfg, model=args.model, parallel=args.parallel, t_warn=args.t_warn,
                               t_soft=args.t_soft, delta_t_rep=args.delta_t_rep,
                               theta_gw_max=args.theta_gw_max, agent_image=args.agent_image,
                               timeout_s=args.timeout_s, delta_s_rep=args.delta_s_rep)


def _state(args: argparse.Namespace, cfg: dict) -> Path:
    s = args.state or cfg.get("state") or "var/state"
    return resolve(cfg, s)


def _theta(cfg: dict) -> float:
    return float((cfg.get("gateway") or {}).get("theta_gw_max", 0.35))


def _alw_data(cfg: dict) -> tuple[dict, dict, dict]:
    alw = cfg.get("alw", {})
    read = lambda k, d: json.loads(resolve(cfg, alw.get(k, d)).read_text())  # noqa: E731
    return (read("catalog", "configs/alw/catalog.json"), read("clusters", "configs/alw/clusters.json"),
            read("split", "configs/alw/split.json"))


def _split_ids(split: dict, name: str) -> list[str]:
    return list(split["task_ids"][name])


def _jsonl(path: str | Path) -> list[dict]:
    return [json.loads(l) for l in Path(path).read_text().splitlines() if l.strip()]


def _load_json_or_jsonl(path: str | Path):
    p = Path(path)
    return _jsonl(p) if p.suffix == ".jsonl" else json.loads(p.read_text())


def _emit(obj) -> None:
    print(json.dumps(obj, indent=1, sort_keys=True, default=str))


# ---------------------------------------------------------------------------------------------
# alw-check-split
# ---------------------------------------------------------------------------------------------

def _check_split(args: argparse.Namespace) -> int:
    from .targets.alw import catalog as cat_mod
    from .targets.alw.splits import check_split_invariants

    cfg = _cfg(args)
    alw = cfg.get("alw", {})
    catalog_data, clusters, split_data = _alw_data(cfg)
    report = check_split_invariants(catalog_data, clusters, split_data, code_root=cfg["_root"],
                                    seed=split_data.get("seed", ""))
    repo = args.alw_repo or alw.get("repo") or ""
    if repo:
        fresh = cat_mod.catalog_document(cat_mod.load_catalog(repo), cat_mod.repo_commit(repo))
        same = fresh["tasks"] == catalog_data["tasks"]
        report.add("R1", "catalog.json equals the catalog re-read from the ALW checkout", same,
                   None if same else "regenerate with scripts/make_catalog.py")
        pinned = catalog_data.get("commit")
        report.add("R1", "ALW checkout at the catalog commit",
                   pinned is None or fresh["commit"] in (None, pinned),
                   {"catalog": pinned, "checkout": fresh["commit"]})
    if args.json:
        print(json.dumps(report.as_dict(), indent=1))
    else:
        for c in report.checks:
            print(f"[{'ok' if c.ok else 'FAIL'}] {c.rule} {c.name}")
            if not c.ok and c.detail is not None:
                print(f"       {json.dumps(c.detail)[:400]}")
        s = split_data["stats"]
        print(f"split seed={split_data['seed']} dev={s['dev']['tasks']} val={s['val']['tasks']} "
              f"test={s['test']['tasks']} G_dev={split_data['G_dev']} "
              f"blind={len(split_data['blind_clusters'])}")
        print("ALL INVARIANTS HOLD" if report.ok else f"{len(report.failures())} INVARIANT(S) VIOLATED")
    return 0 if report.ok else 1


# ---------------------------------------------------------------------------------------------
# alw-concordance
# ---------------------------------------------------------------------------------------------

def _concordance(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    state = _state(args, cfg)
    _, _, split = _alw_data(cfg)
    actions = [a for a in ("offline_smoke_container", "build_bank", "mutate", "freeze_blind",
                           "calibrate_eps") if getattr(args, a)]
    if not actions:
        print("alw-concordance: choose at least one of --offline-smoke-container, --build-bank, "
              "--mutate, --freeze-blind, --calibrate-eps", file=sys.stderr)
        return 2
    bank_path = Path(args.bank) if args.bank else state / "bank" / "b_dev.jsonl"
    out: dict = {"actions": actions, "dry_run": args.dry_run}
    ok = True

    if args.freeze_blind:
        from .bank.blind_clusters import freeze_blind_clusters
        b = freeze_blind_clusters(split)
        out["freeze_blind"] = {"blind_clusters": list(b.blind), "G_dev": b.g_dev,
                               "n_blind_tasks": len(b.blind_task_ids),
                               "n_visible_tasks": len(b.visible_task_ids)}

    if args.build_bank:
        from .bank.referee_bank import BankSample, RefereeBank
        if not args.samples:
            print("--build-bank needs --samples (JSONL of dev BankSample records)", file=sys.stderr)
            return 2
        dev_clusters = sorted({split["cluster_of"][str(n)] for n in split["splits"]["dev"]})
        samples = [BankSample.from_dict(d) for d in _jsonl(args.samples)]
        dev_ids = set(_split_ids(split, "dev"))
        foreign = sorted({s.task_id for s in samples} - dev_ids)
        if foreign:
            print(f"B_dev admits dev executions only; non-dev tasks: {foreign}", file=sys.stderr)
            return 2
        if args.dry_run:
            out["build_bank"] = {"bank": str(bank_path), "n_samples": len(samples), "written": 0}
        else:
            bank = RefereeBank(bank_path, dev_clusters=dev_clusters)
            added = sum(bank.add(s) for s in samples)
            out["build_bank"] = {"bank": str(bank_path), "n_samples": len(bank), "written": added,
                                 "S_plus": bank.coverage(bank.s_plus())}

    if args.mutate:
        from .bank.synthesize import synthesize_all
        if not (args.gds and args.top):
            print("--mutate needs --gds and --top", file=sys.stderr)
            return 2
        data = Path(args.gds).read_bytes()
        variants = synthesize_all(data, args.top, ports=args.ports or (),
                                  effective_area_um2=args.effective_area_um2)
        mdir = Path(args.mutate_out) if args.mutate_out else state / "bank" / "synthetic"
        rows = []
        for v in variants:
            row = {"name": v.name, "checker": v.checker, "polarity": v.polarity,
                   "expected": v.expected, "source_kind": v.source_kind}
            if not args.dry_run:
                row["path"] = str(v.write(mdir / f"{v.name}.gds"))
            rows.append(row)
        out["mutate"] = {"n_variants": len(rows), "variants": rows}

    if args.calibrate_eps:
        from .bank.concordance import calibrate_epsilon
        from .bank.referee_bank import RefereeBank
        bank = RefereeBank(bank_path)
        k = args.checker
        samples = [{"raw_u": s.raw_u(k), "d_star_k": s.d(k), "q_max": s.meta.get("q_max")}
                   for s in bank]
        try:
            out["calibrate_eps"] = {"checker": k, "n_samples": len(samples),
                                    "eps": calibrate_epsilon(k, samples)}
        except ValueError as exc:
            out["calibrate_eps"] = {"checker": k, "error": str(exc)}
            ok = False

    if args.offline_smoke_container:
        if args.dry_run:
            out["offline_smoke_container"] = {"planned": True, "work_dir": args.work_dir}
        else:
            from .bank.concordance import offline_smoke_container
            use_docker = {"auto": None, "yes": True, "no": False}[args.docker]
            wd = args.work_dir or str(state / "smoke")
            rep = offline_smoke_container(cfg, work_dir=wd, image=args.image, use_docker=use_docker)
            out["offline_smoke_container"] = rep
            ok = ok and bool(rep["ok"])
    out["ok"] = ok
    _emit(out)
    return 0 if ok else 1


# ---------------------------------------------------------------------------------------------
# alw-diagnose
# ---------------------------------------------------------------------------------------------

def _diagnose(args: argparse.Namespace) -> int:
    from .eval.fork_eval import diagnose_dev_runs
    if args.split != "dev":
        print("alw-diagnose reads dev executions only (val / test are sealed)", file=sys.stderr)
        return 2
    cfg = _cfg(args)
    state = _state(args, cfg)
    catalog, _, split = _alw_data(cfg)
    runs_path = Path(args.runs) if args.runs else state / "runs" / "dev_H_base.jsonl"
    out_dir = Path(args.out_dir) if args.out_dir else state / "diagnostics"
    if args.dry_run:
        _emit({"dry_run": True, "runs": str(runs_path), "out_dir": str(out_dir),
               "exists": runs_path.is_file()})
        return 0
    profile = {t["task_id"]: t.get("runtime_profile") for t in catalog["tasks"]}
    reps = _jsonl(args.repeat) if args.repeat else []
    try:
        rep = diagnose_dev_runs(_jsonl(runs_path), dev_task_ids=_split_ids(split, "dev"),
                                profile_of=profile, repeat_verifications=reps, out_dir=out_dir,
                                k0_min=args.k0, theta_gw_max=_theta(cfg))
    except ValueError as exc:
        print(f"alw-diagnose: {exc}", file=sys.stderr)
        return 2
    _emit({"out_dir": str(out_dir), "taxonomy": rep["taxonomy"]["counts"],
           "module_switches": rep["module_switches"],
           "sigma_seed": rep["seed_noise_floor"]["sigma_seed"]})
    return 0


# ---------------------------------------------------------------------------------------------
# alw-harvest
# ---------------------------------------------------------------------------------------------

def _harvest(args: argparse.Namespace) -> int:
    from .kernel.artifact_summary import literal_secrets_from_env
    from .targets.alw import harvest as hv
    cfg = _cfg(args)
    state = _state(args, cfg)
    if bool(args.tag) == bool(args.job_dir):
        print("alw-harvest needs exactly one of --tag or --job-dir", file=sys.stderr)
        return 2
    job_dir = Path(args.job_dir) if args.job_dir else state / "jobs" / args.tag
    catalog, _, split = _alw_data(cfg)
    try:
        hs, source = hv.harvest_job(job_dir, theta_gw_max=_theta(cfg))
    except (OSError, ValueError, KeyError) as exc:
        print(f"alw-harvest: {exc}", file=sys.stderr)
        return 2
    dev_ids = _split_ids(split, "dev")
    if args.out_archive or args.out_samples:
        foreign = sorted({str(h.run["task_id"]) for h in hs} - set(dev_ids))
        if foreign:
            print(f"Path 1 archives and B_dev samples are built from dev executions only; "
                  f"non-dev tasks: {foreign}", file=sys.stderr)
            return 2
    secrets = literal_secrets_from_env()
    cluster = {t: split["cluster_of"][str(n)] for t, n in zip(dev_ids, split["splits"]["dev"])}
    q_max = {t["task_id"]: t.get("q_max") for t in catalog["tasks"]}
    written: list[str] = []
    skipped: list[dict] = []
    if not args.dry_run:
        if args.out_runs:
            text = "".join(json.dumps(h.run, sort_keys=True, default=str) + "\n" for h in hs)
            written.append(str(hv.atomic_write_text(args.out_runs, text)))
        if args.out_archive:
            arch = []
            for h in hs:
                if not h.scored:
                    skipped.append({"run_id": h.run["run_id"], "archive": "not_scored"})
                    continue
                try:
                    arch.append(hv.archive_record(h))
                except ValueError as exc:
                    skipped.append({"run_id": h.run["run_id"], "archive": str(exc)})
            written.append(str(hv.atomic_write_text(args.out_archive,
                                                    json.dumps(arch, indent=1, default=str))))
        if args.out_samples:
            rows = [hv.bank_sample(h, cluster_id=cluster[str(h.run["task_id"])],
                                   q_max=q_max.get(h.run["task_id"]))
                    for h in hs if h.run.get("Y_star") is not None]
            text = "".join(json.dumps(r, sort_keys=True, default=str) + "\n" for r in rows)
            written.append(str(hv.atomic_write_text(args.out_samples, text)))
        if args.backfill_summaries:
            for h in hs:
                written += [str(p) for p in hv.write_merged_summary(h, secrets)]
    out = hv.harvest_summary(hs, written, source, secrets)
    out |= {"job_dir": str(job_dir), "dry_run": args.dry_run, "skipped": skipped}
    _emit(out)
    return 0


# ---------------------------------------------------------------------------------------------
# alw-eval
# ---------------------------------------------------------------------------------------------

def _task_dirs(cfg: dict, task_ids: list[str]) -> list[str]:
    repo = (cfg.get("alw") or {}).get("repo") or ""
    return [str(Path(repo) / "tasks" / t) if repo else f"tasks/{t}" for t in task_ids]


H_BASE_INSTRUCTIONS = (
    "MCR H_base workflow: iterate freely in `/alb-workspace/work/draft/` (`layout.gds` and `src/`). "
    "Whenever you produce a candidate layout, run `mcr_promote --gds work/draft/layout.gds --src work/draft/src` "
    "to evaluate mirror certification (`m_1..m_6`) and atomically promote improvements to `/alb-workspace/submission/`, "
    "and keep `/alb-workspace/.mcr/handoff.json` concise and up to date."
)


def _stage_jobs(cfg: dict, state: Path, *, arm: str, tasks: list[str], seeds: list[int],
                tag: str, timeout_s: float | None) -> list[dict]:
    from .targets.alw.agent_wrapper import stage_harness_bundle, write_compose_overlay
    from .targets.alw.harbor_runner import build_harbor_job_config, persist_job_config
    base = state / "jobs" / tag
    harness = None
    if arm != "H_raw":
        harness = base / "harness"
        stage_harness_bundle(cfg["_root"], harness, watermarks=cfg.get("watermarks"))
    agent_image = ((cfg.get("images") or {}).get("agent_image") or "").strip() or None
    mcr_overlay = write_compose_overlay(base / "compose.mcr.yaml",
                                        harness_dir=harness, agent_image=agent_image)
    base_overlays = [str(resolve(cfg, p))
                     for p in (cfg.get("environment") or {}).get("extra_docker_compose", [])]
    overlays = [*base_overlays, str(mcr_overlay)]
    extra_instr = [H_BASE_INSTRUCTIONS] if arm != "H_raw" else None
    model = (cfg.get("agent") or {}).get("model_name") or (cfg.get("model_homogeneity") or {}).get("pin_to")
    jobs = []
    for task, tdir in zip(tasks, _task_dirs(cfg, tasks)):
        for s in seeds:
            name = f"{tag}-{arm}-{task}-s{s}"
            meta = {"task_id": task, "seed": str(s), "arm": arm, "version_id": arm}
            if agent_image:
                meta["agent_image"] = agent_image
            job = build_harbor_job_config(cfg, arm=arm, task_paths=[tdir], job_name=name,
                                          jobs_dir=base / "harbor", harness_dir=harness,
                                          compose_overlays=overlays,
                                          extra_instructions=extra_instr,
                                          trial_meta=meta,
                                          timeout_s=timeout_s)
            path = persist_job_config(job, base / "configs" / f"{name}.json")
            jobs.append({"job_name": name, "task_id": task, "seed": s, "arm": arm,
                         "model": model, "agent_image": agent_image, "job_config": str(path)})
    (base / "launch_plan.json").parent.mkdir(parents=True, exist_ok=True)
    (base / "launch_plan.json").write_text(json.dumps(jobs, indent=1))
    return jobs


def _launch(cfg: dict, jobs: list[dict], template: str, state: Path, tag: str) -> list[dict]:
    """Run each staged job with ``template`` (``{job_config}`` placeholder) behind the metering
    proxy; per-trial proxy statistics are written next to the plan."""
    from .targets.alw.harbor_runner import MeteringProxy, build_host_env
    parallel = int((cfg.get("run") or {}).get("parallel") or 1)
    results: list[dict] = []
    with MeteringProxy.from_config(cfg) as proxy:
        def one(i_job):
            i, j = i_job
            token = proxy.new_trial(j["job_name"], i)
            env = build_host_env(cfg, {"SSAP_TRIAL_TOKEN": token})
            argv = shlex.split(template.format(job_config=j["job_config"]))
            try:
                rc = subprocess.run(argv, env=env).returncode
            finally:
                proxy.revoke(token)
            return {**j, "exit_code": rc, "proxy": proxy.stats(j["job_name"])}
        with ThreadPoolExecutor(max_workers=parallel) as ex:
            results = list(ex.map(one, enumerate(jobs)))
    (state / "jobs" / tag / "launch_results.json").write_text(json.dumps(results, indent=1, default=str))
    return results


def _registry_from(h_inc: dict):
    from .checkers.alw import alw_registry
    reg = alw_registry()
    for name, change in (h_inc.get("changes") or {}).items():
        reg = reg.with_change(name, **change)
    return reg


def _repair_lookup(rows: list[dict]):
    table = {(str(r["task_id"]), int(r.get("seed", 0)), int(r["rep_seed"])): r for r in rows}

    def repair(trial, rep_seed, budget):
        key = (str(trial["task_id"]), int(trial.get("seed", 0)), int(rep_seed))
        if key not in table:
            raise KeyError(f"no executed stateless repair result for {key}")
        return table[key]
    return repair


def _path1(archive: list[dict], h_inc: dict, cand: dict, *, round_id: int, ledger, infra,
           repair_rows: list[dict] | None, dry_run: bool) -> dict:
    from .eval.fork_eval import R_REP, archived_gate_decision, evaluate_path1_fork
    inc_reg = _registry_from(h_inc)
    cand_reg = inc_reg.with_change(cand["gate"], **cand.get("change", {}))
    trials = []
    for t in archive:
        t = dict(t)
        if "inc_gate_signature" not in t:
            t["inc_triggered"], t["inc_gate_signature"] = archived_gate_decision(t, inc_reg)
        trials.append(t)
    if dry_run or repair_rows is None:
        plan = []

        def planned(trial, rep_seed, budget):
            plan.append({"task_id": trial["task_id"], "seed": trial.get("seed", 0),
                         "rep_seed": rep_seed, "granted_repair_budget_sec": budget})
            return {"Y_post": None, "ratchet_promoted": False, "llm_calls": 0, "verifier_calls": 0}
        from .eval.event_ledger import EventLedger
        res = evaluate_path1_fork([{**t, "Y_pre": t.get("Y_pre", t["Y_inc_final"])} for t in trials],
                                  inc_registry=inc_reg, cand_registry=cand_reg,
                                  cand_gate=lambda t: archived_gate_decision(t, cand_reg),
                                  repair=planned, candidate_id=cand["candidate_id"],
                                  round_id=round_id, ledger=EventLedger(), r_rep=R_REP,
                                  gate_ids=[cand["gate"]])
        return {"dry_run": True, "candidate_id": cand["candidate_id"], "path": "path1",
                "n_trials": res["n_trials"], "n_divergent": res["n_divergent"],
                "planned_repairs": plan, "llm_calls_for_identical_trials": 0}
    return evaluate_path1_fork(trials, inc_registry=inc_reg, cand_registry=cand_reg,
                               cand_gate=lambda t: archived_gate_decision(t, cand_reg),
                               repair=_repair_lookup(repair_rows), candidate_id=cand["candidate_id"],
                               round_id=round_id, ledger=ledger, infra_ledger=infra,
                               gate_ids=[cand["gate"]])


def _eval(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    state = _state(args, cfg)
    _, _, split = _alw_data(cfg)
    mode = args.mode
    want_split = "val" if mode == "c1-val" else "dev"
    sp = args.split or want_split
    if sp != want_split:
        print(f"alw-eval --mode {mode} runs on {want_split} only (got --split {sp})", file=sys.stderr)
        return 2
    ids = _split_ids(split, sp)

    if mode in ("smoke-10m", "subset", "baseline"):
        arm = args.arm or "H_base"
        seeds = list(range(args.seeds))
        if mode == "smoke-10m":
            tasks = args.tasks or [args.task or ids[0]]
            timeout = args.timeout_s or SMOKE_T_MAX
            seeds = seeds[:1]
        elif mode == "subset":
            tasks = args.tasks or ([args.task] if args.task else [])
            if not tasks:
                print("--mode subset needs --task or --tasks", file=sys.stderr)
                return 2
            timeout = args.timeout_s
        else:
            tasks, timeout = ids, args.timeout_s
        bad = sorted(set(tasks) - set(ids))
        if bad:
            print(f"tasks not in {sp}: {bad}", file=sys.stderr)
            return 2
        tag = args.tag or f"{mode}-{arm}"
        jobs = _stage_jobs(cfg, state, arm=arm, tasks=tasks, seeds=seeds, tag=tag, timeout_s=timeout)
        out = {"mode": mode, "arm": arm, "split": sp, "n_jobs": len(jobs), "dry_run": args.dry_run,
               "launch_plan": str(state / "jobs" / tag / "launch_plan.json")}
        if not args.dry_run:
            if not args.harbor_cmd:
                print("live launch needs --harbor-cmd '<verified harbor command> {job_config}'; "
                      "job configs are staged", file=sys.stderr)
                _emit(out)
                return 2
            res = _launch(cfg, jobs, args.harbor_cmd, state, tag)
            out["exit_codes"] = [r["exit_code"] for r in res]
        _emit(out)
        return 0

    from .eval.event_ledger import EventLedger
    from .eval.infra_ledger import InfraLedger
    ledgers = state / "ledgers"
    if mode == "fork-path1":
        if not (args.archive and args.candidate):
            print("--mode fork-path1 needs --archive and --candidate", file=sys.stderr)
            return 2
        archive = _load_json_or_jsonl(args.archive)
        bad = sorted({str(t["task_id"]) for t in archive} - set(ids))
        if bad:
            print(f"Path 1 replays dev archives only; non-dev tasks: {bad}", file=sys.stderr)
            return 2
        h_inc = json.loads(Path(args.h_inc).read_text()) if args.h_inc else \
            {"version_id": "H_base", "gates": [], "changes": {}}
        cand = json.loads(Path(args.candidate).read_text())
        rows = _jsonl(args.repair_results) if args.repair_results else None
        if not args.dry_run and rows is None:
            print("live Path 1 needs --repair-results (executed stateless repair seeds)", file=sys.stderr)
            return 2
        try:
            res = _path1(archive, h_inc, cand, round_id=args.round, dry_run=args.dry_run,
                         ledger=EventLedger(ledgers / "event_ledger.jsonl"),
                         infra=InfraLedger(ledgers / "infra_ledger.jsonl", theta_gw_max=_theta(cfg)),
                         repair_rows=rows)
        except ValueError as exc:
            print(f"alw-eval fork-path1: {exc}", file=sys.stderr)
            return 2
        _emit({k: v for k, v in res.items() if k != "trials"})
        return 0

    # c1-val
    from .eval.c1_protocol import HoldoutAudit, run_c1_validation
    if not (args.inc_scores and args.base_scores and args.inc_version):
        print("--mode c1-val needs --inc-scores, --base-scores and --inc-version", file=sys.stderr)
        return 2
    audit = HoldoutAudit(None if args.dry_run else ledgers / "holdout_audit.jsonl")
    try:
        res = run_c1_validation(json.loads(Path(args.inc_scores).read_text()),
                                json.loads(Path(args.base_scores).read_text()),
                                inc_version_id=args.inc_version, base_version_id=args.base_version,
                                val_task_ids=ids, audit=audit)
    except (ValueError, RuntimeError) as exc:
        print(f"alw-eval c1-val: {exc}", file=sys.stderr)
        return 2
    _emit({**res, "dry_run": args.dry_run})
    return 0


# ---------------------------------------------------------------------------------------------
# alw-loop
# ---------------------------------------------------------------------------------------------

def _loop(args: argparse.Namespace) -> int:
    """Round ``g`` reads ``loop/round_<g>/{archive.json, candidates.json, repair_results.jsonl}``
    (dev executions of the current ``H_inc``) and stops at the first round whose inputs are not
    there yet. State: ``loop/h_inc.json``, ``loop/idle_streaks.json``."""
    from .eval.event_ledger import EventLedger, K_IDLE, prune_h_inc, update_idle_streaks
    from .eval.fork_eval import record_incumbent_events
    from .eval.infra_ledger import InfraLedger
    cfg = _cfg(args)
    state = _state(args, cfg)
    _, _, split = _alw_data(cfg)
    dev = set(_split_ids(split, "dev"))
    loop = state / "loop"
    h_path, s_path = loop / "h_inc.json", loop / "idle_streaks.json"
    h_inc = json.loads(h_path.read_text()) if h_path.is_file() else \
        {"version_id": "H_base", "gates": [], "changes": {}, "round": 0}
    streaks = json.loads(s_path.read_text()) if s_path.is_file() else {}
    ledger = EventLedger(None if args.dry_run else state / "ledgers" / "event_ledger.jsonl")
    infra = InfraLedger(None if args.dry_run else state / "ledgers" / "infra_ledger.jsonl",
                        theta_gw_max=_theta(cfg))
    start = int(h_inc.get("round", 0)) + 1
    summary = []
    for g in range(start, start + args.rounds):
        rd = loop / f"round_{g}"
        if not (rd / "archive.json").is_file():
            summary.append({"round": g, "status": "waiting_for_dev_archive", "dir": str(rd)})
            break
        archive = json.loads((rd / "archive.json").read_text())
        bad = sorted({str(t["task_id"]) for t in archive} - dev)
        if bad:
            print(f"round {g}: non-dev tasks in archive: {bad}", file=sys.stderr)
            return 2
        cands = json.loads((rd / "candidates.json").read_text()) if (rd / "candidates.json").is_file() else []
        rr = rd / "repair_results.jsonl"
        rows = _jsonl(rr) if rr.is_file() else None
        results = []
        for c in cands:
            try:
                results.append(_path1(archive, h_inc, c, round_id=g, ledger=ledger, infra=infra,
                                      repair_rows=rows, dry_run=args.dry_run or rows is None))
            except ValueError as exc:
                results.append({"candidate_id": c.get("candidate_id"), "error": str(exc)})
        record_incumbent_events(archive, round_id=g, version_id=h_inc["version_id"], ledger=ledger)
        streaks, pruned = update_idle_streaks(streaks, round_id=g, active_gates=h_inc["gates"],
                                              ledger=ledger, k_idle=K_IDLE)
        new = prune_h_inc(h_inc, pruned)
        for p in pruned:
            new["changes"] = {k: v for k, v in new.get("changes", {}).items() if k != p}
        winners = sorted((r for r in results if r.get("promoted")),
                         key=lambda r: (-r["sum_delta_Y"], r["candidate_id"]))
        adopted = None
        if winners:
            w = winners[0]
            c = next(c for c in cands if c["candidate_id"] == w["candidate_id"])
            adopted = c["candidate_id"]
            new["changes"] = {**new.get("changes", {}), c["gate"]: c.get("change", {})}
            new["gates"] = sorted(set(new["gates"]) | {c["gate"]})
            new["version_id"] = f"{h_inc['version_id']}+{adopted}"
            streaks.setdefault(c["gate"], 0)
        new["round"] = g
        summary.append({"round": g, "adopted": adopted, "pruned": pruned,
                        "candidates": [{k: r.get(k) for k in ("candidate_id", "promoted", "reason",
                                                              "sum_delta_Y", "n_divergent", "error",
                                                              "dry_run")} for r in results],
                        "h_inc": new["version_id"]})
        if args.dry_run:
            break
        h_inc = new
        loop.mkdir(parents=True, exist_ok=True)
        h_path.write_text(json.dumps(h_inc, indent=1, sort_keys=True))
        s_path.write_text(json.dumps(streaks, indent=1, sort_keys=True))
    _emit({"dry_run": args.dry_run, "rounds": summary, "h_inc": h_inc})
    return 0


# ---------------------------------------------------------------------------------------------
# alw-report
# ---------------------------------------------------------------------------------------------

def _report(args: argparse.Namespace) -> int:
    from .eval.c1_protocol import R_MIN, build_sealed_test_report
    if args.split != "test":
        print("alw-report builds the sealed test report only (--split test)", file=sys.stderr)
        return 2
    if args.seeds < R_MIN:
        print(f"--seeds must be >= {R_MIN}", file=sys.stderr)
        return 2
    cfg = _cfg(args)
    state = _state(args, cfg)
    _, clusters, split = _alw_data(cfg)
    ids = _split_ids(split, "test")
    nums = split["splits"]["test"]
    cons = {ids[i]: split["cluster_of"][str(n)] for i, n in enumerate(nums)}
    hard = {ids[i]: clusters["hard_of"][str(n)] for i, n in enumerate(nums)}
    runs_path = Path(args.runs) if args.runs else state / "runs" / "test_runs.jsonl"
    out_dir = Path(args.out_dir) if args.out_dir else state / "report"
    if not args.selected_version:
        print("--selected-version is required (the C1 selection result)", file=sys.stderr)
        return 2
    runs = _jsonl(runs_path) if runs_path.is_file() else []
    if args.with_baselines and not any(r.get("arm") == "manual_instructions" for r in runs):
        print("--with-baselines: no manual_instructions runs in the test runs", file=sys.stderr)
        return 2
    if not args.with_baselines:
        runs = [r for r in runs if r.get("arm") in ("H_raw", "H_base", "H_star")]
    if args.dry_run:
        arms = {}
        for r in runs:
            arms.setdefault(r.get("arm"), set()).add(int(r.get("seed", 0)))
        _emit({"dry_run": True, "runs": str(runs_path), "n_runs": len(runs),
               "seeds_per_arm": {a: len(s) for a, s in arms.items()}, "out_dir": str(out_dir),
               "already_unblinded": (out_dir / "test_unblinded.lock").exists()})
        return 0
    off = json.loads(Path(args.offline_cost).read_text()) if args.offline_cost else None
    try:
        rep = build_sealed_test_report(runs, selected_version_id=args.selected_version,
                                       h_star_is_base=args.h_star_is_base, cluster_of=cons,
                                       hard_cluster_of=hard, test_task_ids=ids, out_dir=out_dir,
                                       r_min=args.seeds, offline_search_cost=off,
                                       theta_gw_max=_theta(cfg))
    except (ValueError, RuntimeError) as exc:
        print(f"alw-report: {exc}", file=sys.stderr)
        return 2
    _emit({"out_dir": str(out_dir), "three_stage_attribution": rep["three_stage_attribution"],
           "c_star_decomposition_exact": rep["c_star_decomposition"]["identity_exact"]})
    return 0


# ---------------------------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="ssap", description="MCR / SSAP tooling for AnalogLayoutWorld")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("alw-check-split", help="check split / leak invariants R1-R5")
    _add_common(p)
    p.add_argument("--alw-repo", default=None, help="ALW checkout (default: config alw.repo)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=_check_split)

    p = sub.add_parser("alw-concordance", help="Stage 0: smoke, B_dev, mutations, blind freeze, eps_k")
    _add_common(p)
    p.add_argument("--offline-smoke-container", action="store_true",
                   help="stage /opt/harness and run the offline mcr_promote smoke")
    p.add_argument("--work-dir", default=None, help="smoke scratch directory (replaced)")
    p.add_argument("--image", default=None, help="agent image for the smoke (default: images.agent_image)")
    p.add_argument("--docker", choices=("auto", "yes", "no"), default="auto")
    p.add_argument("--build-bank", action="store_true", help="admit dev samples into B_dev")
    p.add_argument("--samples", default=None, help="JSONL of BankSample records (dev only)")
    p.add_argument("--bank", default=None, help="B_dev JSONL (default: STATE/bank/b_dev.jsonl)")
    p.add_argument("--mutate", action="store_true", help="0-token synthetic variants of one layout")
    p.add_argument("--gds", default=None)
    p.add_argument("--top", default=None)
    p.add_argument("--ports", nargs="*", default=None)
    p.add_argument("--effective-area-um2", type=float, default=None)
    p.add_argument("--mutate-out", default=None)
    p.add_argument("--freeze-blind", action="store_true", help="re-derive and freeze the blind clusters")
    p.add_argument("--calibrate-eps", action="store_true", help="calibrate eps_k on B_dev")
    p.add_argument("--checker", default="m_fill")
    p.set_defaults(fn=_concordance)

    p = sub.add_parser("alw-diagnose", help="four zero-LLM dev diagnostics on H_base runs")
    _add_common(p)
    p.add_argument("--split", default="dev")
    p.add_argument("--runs", default=None, help="JSONL of H_base dev run records")
    p.add_argument("--repeat", default=None, help="JSONL of repeated verifier calls")
    p.add_argument("--out-dir", default=None)
    p.add_argument("--k0", type=int, default=2)
    p.set_defaults(fn=_diagnose)

    p = sub.add_parser("alw-harvest", help="Harbor job directory -> runs / Path 1 archive / B_dev samples")
    _add_common(p)
    p.add_argument("--tag", default=None, help="job tag (reads STATE/jobs/<tag>/)")
    p.add_argument("--job-dir", default=None, help="job directory (instead of --tag)")
    p.add_argument("--out-runs", default=None, help="JSONL of run records (alw-diagnose / alw-report)")
    p.add_argument("--out-archive", default=None, help="JSON Path 1 archive of scored dev runs")
    p.add_argument("--out-samples", default=None, help="JSONL of terminal B_dev BankSamples (dev only)")
    p.add_argument("--backfill-summaries", action="store_true",
                   help="write artifact_summary_merged.{json,md} into each mcr_snapshot/")
    p.set_defaults(fn=_harvest)

    p = sub.add_parser("alw-eval", help="Harbor runs, Path 1 fork replay or C1 validation")
    _add_common(p)
    p.add_argument("--mode", choices=EVAL_MODES, required=True)
    p.add_argument("--arm", choices=("H_raw", "H_base", "H_star"), default=None)
    p.add_argument("--task", default=None)
    p.add_argument("--tasks", nargs="*", default=None)
    p.add_argument("--split", default=None)
    p.add_argument("--seeds", type=int, default=1)
    p.add_argument("--tag", default=None)
    p.add_argument("--harbor-cmd", default=None,
                   help="live launch template with a {job_config} placeholder (verified runbook)")
    p.add_argument("--archive", default=None, help="fork-path1: archived H_inc dev trials (JSON/JSONL)")
    p.add_argument("--candidate", default=None, help="fork-path1: {candidate_id, gate, change}")
    p.add_argument("--h-inc", default=None, help="fork-path1: H_inc record (default H_base)")
    p.add_argument("--repair-results", default=None, help="fork-path1: executed repair seeds JSONL")
    p.add_argument("--round", type=int, default=1)
    p.add_argument("--inc-scores", default=None, help="c1-val: {val task: Y*} of H_inc")
    p.add_argument("--base-scores", default=None, help="c1-val: {val task: Y*} of H_base")
    p.add_argument("--inc-version", default=None)
    p.add_argument("--base-version", default="H_base")
    p.set_defaults(fn=_eval)

    p = sub.add_parser("alw-loop", help="round-by-round Path 1 outer loop with K_idle pruning")
    _add_common(p)
    p.add_argument("--rounds", type=int, default=1)
    p.set_defaults(fn=_loop)

    p = sub.add_parser("alw-report", help="sealed test report (single unblind, R >= 3 seeds)")
    _add_common(p)
    p.add_argument("--split", default="test")
    p.add_argument("--seeds", type=int, default=3)
    p.add_argument("--runs", default=None, help="JSONL of test run records")
    p.add_argument("--out-dir", default=None)
    p.add_argument("--selected-version", default=None)
    p.add_argument("--h-star-is-base", action="store_true")
    p.add_argument("--with-baselines", action="store_true")
    p.add_argument("--offline-cost", default=None, help="JSON cost ledger of the offline search")
    p.set_defaults(fn=_report)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
