"""method-v4.1 on VeRO-Bench (paper-v1): set up a target, evaluate commits, run the loop, test.

    python -m rag_eval.vero.cli init   --state-root S --preset simple_qa --vero-root V --agent-python P ...
    python -m rag_eval.vero.cli eval   --state-root S --split test [--commit SHA] [--k 1] [--limit N]
    python -m rag_eval.vero.cli run    --state-root S --run-dir S/runs/run1 --v4-config cfg.json
    python -m rag_eval.vero.cli status --run-dir S/runs/run1
    python -m rag_eval.vero.cli test   --state-root S --run-dir S/runs/run1 [--k 1]

One state root = one target (preset, VeRO checkout, agent venv, dataset, task params, models, agent
commands; ``setup.json``) and its git repo. Each run directory is one independent optimisation
run with its own evaluation cache (N=3 runs = three run directories).
"""
from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import signal
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from rag_eval.optimizer import ledger_io
from rag_eval.optimizer.v4 import decide
from rag_eval.optimizer.v4.config import ConfigError, load
from rag_eval.optimizer.v4.loop import FAILURE_HALTS, STATE_FILE, RunStopRequested, V4Run, final_version

from .adapter import PRESETS, VeROAdapter, digest_settings, split_ids
from .evaluation import describe
from .target import TargetRepo

SETUP_FILE, IDENTITY_FILE, ENDPOINT_FILE = "setup.json", "identity.json", "endpoint.json"
DEFAULT_AGENT_CMD = "pi -p --no-session"
MODEL_ENV = dict(target="VERO_TARGET_MODEL", grader="VERO_GRADER_MODEL", optimizer="VERO_OPTIMIZER_MODEL")
EXIT_OK, EXIT_USAGE, EXIT_HALTED = 0, 2, 3


class UsageError(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read(path: Path) -> dict:
    if not path.exists():
        raise UsageError(f"{path} does not exist")
    return json.loads(path.read_text())


def _logging(path: Path | None) -> None:
    handlers = [logging.StreamHandler(sys.stderr)]
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(path))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=handlers, force=True)


@contextlib.contextmanager
def stop_on_sigterm():
    def handler(signum, frame):
        raise RunStopRequested()
    previous = signal.signal(signal.SIGTERM, handler)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def _setup(state_root: Path) -> dict:
    return _read(state_root / SETUP_FILE)


def current_models() -> dict:
    """Upstream models behind the proxy aliases VeRO uses, as exported by experiments/vero/env.sh."""
    return {role: os.environ.get(var) or None for role, var in MODEL_ENV.items()}


def _check_models(setup: dict) -> None:
    """The evaluation cache is keyed by commit, not by model: one state root serves one target and grader."""
    recorded, now = setup.get("models"), current_models()
    if not recorded:
        return
    changed = {r: (recorded.get(r), now[r]) for r in ("target", "grader") if recorded.get(r) != now[r]}
    if changed:
        raise UsageError("this state root was initialised with other models "
                         + ", ".join(f"{r}: {a} -> {b}" for r, (a, b) in changed.items())
                         + "; source the matching environment or use a new --state-root")


def _adapter(state_root: Path, run_dir: Path) -> tuple[VeROAdapter, dict]:
    setup = _setup(state_root)
    _check_models(setup)
    return VeROAdapter(setup, state_root, run_dir), setup


# ---------------------------------------------------------------------------

def cmd_init(args) -> int:
    root = Path(args.state_root).resolve()
    preset = PRESETS[args.preset]
    vero = Path(args.vero_root).resolve()
    source = vero / preset.agent_subdir
    if not source.is_dir():
        raise UsageError(f"{source} is not a directory (is --vero-root a VeRO paper-v1 checkout?)")
    dataset = Path(args.dataset_path or vero / "vero-benchmarking" / "datasets" / preset.dataset).resolve()
    vero_commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=vero, capture_output=True, text=True).stdout.strip()
    extra = [str(Path(x).resolve()) for x in args.extra_sys_path or []]
    sizes = describe(args.agent_python, str(dataset), tuple(extra))
    missing = [s for s in ("train", "validation", "test") if s not in sizes]
    if missing:
        raise UsageError(f"dataset {dataset} lacks split(s) {missing}: {sizes}")
    settings = dict(preset=preset.name, vero_root=str(vero), vero_commit=vero_commit,
                    agent_python=args.agent_python, dataset_path=str(dataset),
                    task_params=json.loads(args.task_params) if args.task_params else {},
                    analyst_cmd=args.analyst_cmd, implementer_cmd=args.implementer_cmd,
                    agent_timeout=args.agent_timeout, sample_concurrency=args.sample_concurrency,
                    sample_timeout=args.sample_timeout, call_timeout=args.call_timeout, infra_retry=1,
                    show_gold=not args.hide_gold, extra_sys_path=extra, splits=sizes, models=current_models())
    path = root / SETUP_FILE
    if path.exists() and not args.force:
        old = _read(path)
        if digest_settings(old) != digest_settings(settings):
            raise UsageError(f"{path} exists with other settings; use a new --state-root (or --force)")
    root.mkdir(parents=True, exist_ok=True)
    _, seed = TargetRepo.create(root / "target", source,
                                f"seed: VeRO {preset.agent_subdir} @ {vero_commit[:12]}")
    ledger_io.atomic_write_json(path, settings)
    print(f"target repo: {root / 'target' / 'repo'}\nseed: {seed}\nsplits: {sizes}\nmodels: {settings['models']}")
    return EXIT_OK


def cmd_eval(args) -> int:
    root = Path(args.state_root).resolve()
    _logging(None)
    adapter, setup = _adapter(root, root / "adhoc")
    ids = split_ids(setup["splits"])[args.split][:args.limit or None]
    version = adapter.version_for(args.commit or adapter.seed_sha)
    adapter.evaluator.set_parallel(args.parallel)
    result = adapter.evaluate(version, ids, args.k, purpose="baseline")
    _print_result(version.content_hash, ids, result, args.k)
    return EXIT_OK


def _print_result(sha, ids, result, k) -> None:
    scores = result.per_task_scores
    answered = [s["answered"] for stubs in result.trajectories.values() for s in stubs]
    mean = sum(scores.values()) / len(scores) if scores else None
    print(json.dumps(dict(commit=sha, tasks=len(ids), scored=len(scores), k=k, mean_score=mean,
                          answered_rate=sum(answered) / len(answered) if answered else None,
                          per_attempt=[(sum(a.values()) / len(a) if a else None) for a in result.attempt_scores]),
                     indent=1))


def cmd_run(args) -> int:
    root, run_dir = Path(args.state_root).resolve(), Path(args.run_dir).resolve()
    try:
        loaded = load(args.v4_config)
    except ConfigError as exc:
        raise UsageError(str(exc)) from None
    run_dir.mkdir(parents=True, exist_ok=True)
    _logging(run_dir / "run.log")
    adapter, setup = _adapter(root, run_dir)
    ident_path = run_dir / IDENTITY_FILE
    sizes = setup["splits"]
    budget = args.max_executions if args.max_executions is not None else vero_budget(sizes)
    if ident_path.exists():
        ident = _read(ident_path)
        if ident.get("max_executions") != budget:
            raise UsageError(f"--max-executions {budget} differs from the run's {ident.get('max_executions')}")
        if ident["config_sha256"] != loaded.sha256:
            raise UsageError("the v4 config changed since the run started (pre-registered, read-only)")
        if ident["setup_sha256"] != digest_settings(setup) or ident["seed"] != adapter.seed_sha:
            raise UsageError("setup.json or the seed changed since the run started")
        st = _read(run_dir / STATE_FILE) if (run_dir / STATE_FILE).exists() else {}
        if st.get("halt_reason") and st["halt_reason"] not in FAILURE_HALTS:
            raise UsageError(f"the run already stopped ({st['halt_reason']}); next: test")
    else:
        (run_dir / "v4-config.json").write_bytes(Path(args.v4_config).read_bytes())
        ledger_io.atomic_write_json(ident_path, dict(
            method="v4.1", target="vero", preset=setup["preset"], seed=adapter.seed_sha,
            config_sha256=loaded.sha256, config_resolved=loaded.resolved, setup_sha256=digest_settings(setup),
            max_executions=budget, budget_rule=("vero: 8 x (train + validation)" if args.max_executions is None
                                                else "explicit"),
            created_at=_now()))
    ids = split_ids(sizes)
    adapter.evaluator.set_budget(budget)
    cfg = loaded.config
    missing = sorted(set(cfg.safeguards) - set(adapter.observable_safeguards()))
    if missing:
        raise UsageError(f"the v4 config names safeguard(s) {missing} the VeRO adapter cannot observe")
    print(f"run {run_dir.name}: preset={setup['preset']} search={len(ids['train'])} "
          f"validation={len(ids['validation'])} test={len(ids['test'])} b={cfg.b} budget={cfg.time_budget_hours} h, "
          f"{budget} executions ({adapter.evaluator.spent()} used)")
    run = V4Run(adapter, adapter.seed(), cfg, search=ids["train"], validation=ids["validation"],
                test_size=len(ids["test"]), state_dir=run_dir)
    with stop_on_sigterm():
        reason = run.run()
    print(f"halt reason: {reason}")
    print(f"rounds: {len(run.rounds)}; adopted nodes: {len(run.lineage) - 1}; final: {run.final_version()[:12]}")
    print(f"evaluator: {adapter.evaluator.calls} runner call(s), {adapter.evaluator.executions} sample execution(s) "
          f"this session; budget {adapter.evaluator.spent()} / {budget}")
    if run.failure_detail:
        print(f"failure: {run.failure_detail}")
    return EXIT_HALTED if reason in FAILURE_HALTS else EXIT_OK


def vero_budget(sizes: dict) -> int:
    """VeRO paper-v1 / GEPA budget in sample executions: B=8 full evaluations on train and on validation."""
    return 8 * (sizes["train"] + sizes["validation"])


def cmd_status(args) -> int:
    run_dir = Path(args.run_dir)
    st = _read(run_dir / STATE_FILE)
    cal = st.get("calibration") or {}
    print(f"calibration: rerun_var={cal.get('rerun_var')}")
    for rd in st.get("rounds", []) + ([st["current_round"]] if st.get("current_round") else []):
        stages: dict = {}
        for rec in (rd.get("records") or {}).values():
            stages[rec["stage"]] = stages.get(rec["stage"], 0) + 1
        adopted = (rd.get("decision") or {}).get("adopted")
        print(f"  round {rd['round']}: cards {len(rd.get('cards') or [])} stages {stages} adopted {(adopted or '-')[:12]}")
    print("lineage: " + " -> ".join((n["version"] or "")[:12] + f"({n.get('kind')})" for n in st.get("lineage", [])))
    print(f"elapsed: {st.get('elapsed_s', 0) / 3600:.2f} h; halt reason: {st.get('halt_reason') or '-'}")
    print(f"executions so far: {executions(run_dir / 'evals')}")
    if (run_dir / ENDPOINT_FILE).exists():
        print("endpoint: " + json.dumps(_read(run_dir / ENDPOINT_FILE)["summary"]))
    return EXIT_OK


def executions(cache: Path) -> dict:
    """Cached sample executions per split (every version and attempt)."""
    out: dict = {}
    if cache.exists():
        for f in cache.glob("*/*/a*.json"):
            split = f.parent.name.rpartition("-")[0]
            out[split] = out.get(split, 0) + 1
    return out


def cmd_test(args) -> int:
    root, run_dir = Path(args.state_root).resolve(), Path(args.run_dir).resolve()
    _logging(run_dir / "test.log")
    st = _read(run_dir / STATE_FILE)
    if not st.get("halt_reason"):
        raise UsageError("the run has not stopped; the test split is evaluated once, after the run")
    if (run_dir / ENDPOINT_FILE).exists() and not args.force:
        print(json.dumps(_read(run_dir / ENDPOINT_FILE)["summary"], indent=1))
        return EXIT_OK
    used = executions(run_dir / "evals")          # before the test split is touched
    adapter, setup = _adapter(root, run_dir)
    adapter.evaluator.set_parallel(args.parallel)
    ids = split_ids(setup["splits"])["test"]
    seed, final = adapter.seed(), adapter.version_for(final_version(st["lineage"], st.get("ablation")))
    s_res = adapter.evaluate(seed, ids, args.k, purpose="sealed")
    f_res = s_res if final.content_hash == seed.content_hash else adapter.evaluate(final, ids, args.k, purpose="sealed")
    d = decide.paired_delta(f_res, s_res, ids)
    mean = lambda r: sum(r.per_task_scores.values()) / len(r.per_task_scores) if r.per_task_scores else None  # noqa: E731
    sizes = setup["splits"]
    summary = dict(
        seed=seed.content_hash, final=final.content_hash, k=args.k, test_tasks=len(ids),
        seed_score=mean(s_res), final_score=mean(f_res), delta=d["delta"], se=d["se"],
        ci95=[d["delta"] - 1.96 * d["se"], d["delta"] + 1.96 * d["se"]] if d["se"] is not None else None,
        paired=d["n"], dropped=d["dropped"], halt_reason=st.get("halt_reason"), rounds=len(st.get("rounds", [])),
        adopted=len(st.get("lineage", [])) - 1, elapsed_h=st.get("elapsed_s", 0) / 3600,
        optimisation_executions=used,
        full_eval_equivalents={s: round(n / sizes[s], 2) for s, n in used.items() if sizes.get(s)})
    rep = dict(summary=summary, per_task=dict(seed=s_res.per_task_scores, final=f_res.per_task_scores),
               attempts=dict(seed=s_res.attempt_scores, final=f_res.attempt_scores), at=_now())
    ledger_io.atomic_write_json(run_dir / ENDPOINT_FILE, rep)
    print(json.dumps(summary, indent=1))
    return EXIT_OK


# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="rag_eval.vero.cli", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="create the target repo and setup.json")
    s.add_argument("--state-root", required=True)
    s.add_argument("--preset", required=True, choices=sorted(PRESETS))
    s.add_argument("--vero-root", required=True, help="VeRO checkout at the paper-v1 tag")
    s.add_argument("--agent-python", required=True, help="python of the agent project's venv (has scale-vero)")
    s.add_argument("--dataset-path", help="built dataset (default: <vero>/vero-benchmarking/datasets/<name>)")
    s.add_argument("--task-params", help='JSON passed to the task, e.g. {"model": "my-model"}')
    s.add_argument("--analyst-cmd", default=DEFAULT_AGENT_CMD)
    s.add_argument("--implementer-cmd", default=DEFAULT_AGENT_CMD)
    s.add_argument("--agent-timeout", type=float, default=1800)
    s.add_argument("--sample-concurrency", type=int, default=8)
    s.add_argument("--sample-timeout", type=int, default=300)
    s.add_argument("--call-timeout", type=float, default=3600)
    s.add_argument("--extra-sys-path", action="append", help="extra import path for the runner (tests, vendored vero)")
    s.add_argument("--hide-gold", action="store_true", help="do not show training reference answers to the analyst")
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("eval", help="evaluate one commit on a split (baseline check)")
    s.add_argument("--state-root", required=True)
    s.add_argument("--split", required=True, choices=("train", "validation", "test"))
    s.add_argument("--commit")
    s.add_argument("--k", type=int, default=1)
    s.add_argument("--limit", type=int)
    s.add_argument("--parallel", type=int, default=3)
    s.set_defaults(func=cmd_eval)

    s = sub.add_parser("run", help="run (or resume) method-v4.1")
    s.add_argument("--state-root", required=True)
    s.add_argument("--run-dir", required=True)
    s.add_argument("--v4-config", required=True)
    s.add_argument("--max-executions", type=int, help="sample executions on train+validation (default: VeRO's "
                   "8 x (train + validation), the budget GEPA gets)")
    s.set_defaults(func=cmd_run)

    s = sub.add_parser("status")
    s.add_argument("--run-dir", required=True)
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("test", help="seed vs final on the test split, after the run stopped")
    s.add_argument("--state-root", required=True)
    s.add_argument("--run-dir", required=True)
    s.add_argument("--k", type=int, default=1)
    s.add_argument("--parallel", type=int, default=3)
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_test)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except UsageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
