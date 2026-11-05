"""method-v4 on SN, one command per step (binding plan W6).

    python -m rag_eval.optimizer.cli v4 run    --v4-config run.json ...   start or resume
    python -m rag_eval.optimizer.cli v4 status --state-dir DIR
    python -m rag_eval.optimizer.cli v4 test   --state-dir DIR [--confirm --budget budget.json]
    python -m rag_eval.optimizer.cli v4 figure --state-dir DIR --out curve.svg

``run`` calibrates, prints the feasibility card and runs rounds until a stop
rule fires. ``test`` without ``--confirm`` only prints the node set and the
number of test tasks; with ``--confirm`` (a human decision) it claims the
iteration's sealed selection once and writes ``endpoint.json``.

Exit codes: 0 ok, 1 unexpected error, 2 usage / validation error, 3 halted
with process_failure or budget_exhausted (rerun, or ``--retry-failed``).
"""
from __future__ import annotations

import argparse
import os
import sys
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .. import ledger_io, run_view
from ..adapter import EvaluationUnavailable, Version
from . import calibrate, endpoint
from .config import ConfigError, load
from .loop import FAILURE_HALTS, LEDGER_FILE, STATE_FILE, V4Run, final_version

EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_HALTED = 0, 1, 2, 3
IDENTITY_FILE = "cli-run.json"
CONFIG_COPY_FILE = "v4-config.json"
EVIDENCE_DIR = "evidence"
ENDPOINT_FILE = "endpoint.json"


class UsageError(Exception):
    pass


@dataclass
class Services:
    adapter: Any
    iteration_id: str
    task_id: str
    root_candidate_id: str
    writable: bool
    repository: Any = None


def services_from_operations(ops, config: dict, iteration_id: str, *, actor: str, run_id: Optional[str],
                             adapter_settings: dict, where: str = "the workbench state") -> Services:
    from rag_eval.workbench.repository import UnknownIdentity
    from .sn_adapter_v4 import SNAdapterV4
    try:
        record = ops.service.iteration_record(iteration_id)
    except UnknownIdentity:
        raise UsageError(f"unknown iteration {iteration_id!r} in {where}") from None
    if not record.get("root_candidate_id"):
        raise UsageError(f"iteration {iteration_id!r} has no root candidate")
    adapter = SNAdapterV4(dict(config, iteration_id=iteration_id, task_id=record["task_id"], run_id=run_id,
                               actor=actor), repository=ops.repository, run_views=ops.api.views,
                          round_operations=ops, hypotheses=ops.api.hypotheses, **adapter_settings)
    return Services(adapter, iteration_id, record["task_id"], record["root_candidate_id"], bool(ops.api.writable),
                    ops.repository)


def build_services(config_path, state_root, iteration_id, *, actor, run_id, adapter_settings) -> Services:
    """As the workbench server builds them (see optimizer.cli.build_services); tests replace this."""
    from rag_eval.workbench import env_guard
    from rag_eval.workbench.api import create_app
    from rag_eval.workbench.configuration import load_config
    config = load_config(Path(config_path))
    env_guard.install(Path(state_root) / "env-guard.jsonl")
    app = create_app(str(state_root), config, actor=actor)
    return services_from_operations(app.state.round_service.operations, config, iteration_id, actor=actor,
                                    run_id=run_id, adapter_settings=adapter_settings,
                                    where=f"workbench state {state_root}")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read(path: Path, what: str) -> Any:
    try:
        return ledger_io.read_json(path)
    except FileNotFoundError:
        raise UsageError(f"{what} not found: {path}") from None
    except (OSError, ValueError) as exc:
        raise UsageError(f"cannot read {what} {path}: {exc}") from None


def _require(args, *names):
    missing = [f"--{n.replace('_', '-')}" for n in names if not getattr(args, n, None)]
    if missing:
        raise UsageError(f"v4 {args.command} requires {', '.join(missing)}")


def _settings(adapter_settings: dict) -> dict:
    """SNAdapterV4 keyword arguments from the config's adapter section."""
    return {k: v for k, v in adapter_settings.items()}


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------

def cmd_run(args) -> int:
    _require(args, "iteration", "actor", "run_id", "state_dir", "v4_config")
    try:
        loaded = load(args.v4_config)
    except ConfigError as exc:
        raise UsageError(str(exc)) from None
    state_dir = Path(args.state_dir)
    identity_path = state_dir / IDENTITY_FILE
    identity = _read(identity_path, "run identity") if identity_path.exists() else None
    st = _read(state_dir / STATE_FILE, "run state") if (state_dir / STATE_FILE).exists() else None
    if identity is None and st is not None:
        raise UsageError(f"{state_dir} holds a state not started by this CLI; use a new --state-dir")
    if identity is not None:
        if identity["config_sha256"] != loaded.sha256:
            raise UsageError("the v4 config file changed since the run started; it is pre-registered and read-only")
        for key, flag, value in (("iteration_id", "--iteration", args.iteration), ("run_id", "--run-id", args.run_id),
                                 ("actor", "--actor", args.actor)):
            if identity.get(key) != value:
                raise UsageError(f"{flag} {value!r} differs from the run's {identity.get(key)!r}")
    key_generation = None
    if args.retry_failed:
        if st is None or st.get("halt_reason") != "process_failure":
            raise UsageError("--retry-failed is only allowed after a process_failure halt")
        key_generation = int(st.get("key_generation", 0)) + 1
    if st is not None and st.get("halt_reason") and st["halt_reason"] not in FAILURE_HALTS:
        raise UsageError(f"the run already stopped ({st['halt_reason']}); next step: v4 test")

    services = build_services(args.config, args.state, args.iteration, actor=args.actor, run_id=args.run_id,
                              adapter_settings=_settings(loaded.adapter_settings))
    adapter = services.adapter
    if not services.writable:
        raise UsageError("workbench config does not set task_worktrees.create_candidates=true")
    parts = adapter.partitions()
    cfg = loaded.config
    if parts["k_base"] != cfg.calibration_k:
        raise UsageError(f"the iteration's k_base={parts['k_base']} must equal calibration_k={cfg.calibration_k} "
                         "(search-set diagnosis runs at k_base; binding plan §2.2: create the iteration with k_base=2)")
    if not parts.get("safeguards"):
        raise UsageError("the iteration protocol has no pre-registered safeguards")
    search, validation = parts["search"], parts["validation"]
    if not search or len(validation) < 2:
        raise UsageError("the iteration needs a search split and at least 2 validation cases")
    test_ids = (adapter._protocol().get("splits") or {}).get("sealed") or []
    if parts["fresh_batches"]:
        print(f"note: the iteration has {len(parts['fresh_batches'])} fresh batch(es); method-v4 does not use them")
    if st is None:
        if not test_ids:
            if not args.no_test_set:
                raise UsageError("the iteration has no sealed selection (the v4 test set); pass --no-test-set "
                                 "for a rehearsal without an endpoint")
        elif endpoint.study_used(adapter):
            if not args.no_test_set:
                raise UsageError("the iteration's sealed selection was already claimed: register a selection "
                                 "that was never evaluated (or --no-test-set for a rehearsal)")
            print("warning: the sealed selection was already used; this run has no endpoint")
    seed = adapter.version_for(services.root_candidate_id)
    state_dir.mkdir(parents=True, exist_ok=True)
    adapter.set_evidence_dir(state_dir / EVIDENCE_DIR)
    print(f"{'resume' if st else 'new run'}: search={len(search)} validation={len(validation)} "
          f"test={len(test_ids)} k_base={parts['k_base']} b={cfg.b} budget={cfg.time_budget_hours} h")
    if cfg.variants():
        print("method variants: " + ", ".join(f"{k}={v['value']!r} (default {v['default']!r})"
                                              for k, v in cfg.variants().items()))
    try:
        with run_view.hold_run_lock(state_dir):
            return _run_locked(args, loaded, services, seed, search, validation, test_ids, state_dir,
                               identity, key_generation)
    except run_view.RunLocked as exc:
        raise UsageError(str(exc)) from None


def _run_locked(args, loaded, services, seed, search, validation, test_ids, state_dir, identity,
                key_generation) -> int:
    from rag_eval.workbench import auto_runs
    from rag_eval.workbench.repository import Conflict
    if services.repository is not None:
        try:
            auto_runs.register(services.repository, args.iteration, run_id=args.run_id,
                               state_dir=str(state_dir.resolve()), actor=args.actor)
        except Conflict as exc:
            raise UsageError(str(exc)) from None
    if identity is None:
        (state_dir / CONFIG_COPY_FILE).write_bytes(Path(args.v4_config).read_bytes())
        ledger_io.atomic_write_json(state_dir / IDENTITY_FILE, dict(
            method="v4", iteration_id=args.iteration, run_id=args.run_id, actor=args.actor,
            task_id=services.task_id, root_candidate_id=services.root_candidate_id, seed=seed.content_hash,
            config_file=str(Path(args.v4_config).resolve()), config_sha256=loaded.sha256,
            config_resolved=loaded.resolved, test_set=bool(test_ids) and not args.no_test_set,
            created_at=_now()))
    run = V4Run(services.adapter, seed, loaded.config, search=search, validation=validation,
                test_size=len(test_ids) or None, state_dir=state_dir, key_generation=key_generation)
    reason = run.run()
    if run.feasibility is not None and run.calibration is not None:
        print(calibrate.render(run.feasibility, run.calibration))
    print(f"halt reason: {reason}")
    print(f"rounds: {len(run.rounds)}; adopted nodes: {len(run.lineage) - 1}")
    print(f"ledger: {state_dir / LEDGER_FILE}")
    if reason == "process_failure":
        print("process failure: fix the cause, then rerun (or --retry-failed for new measurement keys)")
    elif reason not in FAILURE_HALTS:
        print("next: v4 test (preview), then v4 test --confirm --budget <file> with the user's consent")
    return EXIT_HALTED if reason in FAILURE_HALTS else EXIT_OK


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

def cmd_status(args) -> int:
    _require(args, "state_dir")
    state_dir = Path(args.state_dir)
    st = _read(state_dir / STATE_FILE, "run state")
    ident = _read(state_dir / IDENTITY_FILE, "run identity") if (state_dir / IDENTITY_FILE).exists() else {}
    print(f"run: {ident.get('run_id')} iteration: {ident.get('iteration_id')} config sha256: "
          f"{ident.get('config_sha256')}")
    cal = st.get("calibration")
    print("calibration: " + (f"k_val={cal['k_val']} rerun_var={cal['rerun_var']:.4g}" if cal else "-"))
    parts = st.get("partitions") or {}
    print(f"partitions: search={len(parts.get('search', []))} minibatch={len(parts.get('minibatch', []))} "
          f"rest={len(parts.get('rest', []))} test={parts.get('test_size')}")
    cur = st.get("current_round")
    print(f"rounds finished: {len(st.get('rounds', []))}; open: {cur['round'] if cur else '-'}")
    for rd in st.get("rounds", []) + ([cur] if cur else []):
        stages: dict = {}
        for rec in (rd.get("records") or {}).values():
            stages[rec["stage"]] = stages.get(rec["stage"], 0) + 1
        adopted = (rd.get("decision") or {}).get("adopted")
        print(f"  round {rd['round']}: cards {len(rd.get('cards') or [])} stages {stages} adopted "
              f"{(adopted or '-')[:24]}")
    print(f"lineage: {len(st.get('lineage', []))} node(s): " + " -> ".join(
        (n["version"] or "")[:12] for n in st.get("lineage", [])))
    ab = st.get("ablation")
    if ab:
        print(f"ablation: {ab.get('status')} pruned={ab.get('pruned')} accepted={ab.get('accepted')} "
              f"{ab.get('reason') or ''}".rstrip())
    print(f"elapsed: {st.get('elapsed_s', 0) / 3600:.2f} h; no-adoption rounds: {st.get('no_adoption')}")
    print(f"halt reason: {st.get('halt_reason') or '-'}; key_generation: {st.get('key_generation', 0)}")
    if (state_dir / ENDPOINT_FILE).exists():
        rep = _read(state_dir / ENDPOINT_FILE, "endpoint")
        print(f"endpoint: Δ_test {rep.get('delta_test')} 95% CI {rep.get('ci95')} ({rep.get('conclusion')})")
    return EXIT_OK


# ---------------------------------------------------------------------------
# test (endpoint)
# ---------------------------------------------------------------------------

def cmd_test(args) -> int:
    _require(args, "state_dir")
    state_dir = Path(args.state_dir)
    st = _read(state_dir / STATE_FILE, "run state")
    ident = _read(state_dir / IDENTITY_FILE, "run identity")
    halt = st.get("halt_reason")
    if not halt:
        raise UsageError("the run has not stopped; the test set is evaluated once, after the run stops")
    if halt in FAILURE_HALTS and not args.allow_failure_halt:
        raise UsageError(f"the run halted with {halt}, which is retryable; pass --allow-failure-halt to end it here")
    if not ident.get("test_set"):
        raise UsageError("this run was started without a test set (--no-test-set)")
    resolved = ident["config_resolved"]
    versions = {h: Version(content_hash=h, metadata=m) for h, m in st["versions"].items()}
    final = final_version(st["lineage"], st.get("ablation"))
    nodes = endpoint.node_set(st["lineage"], versions, resolved["endpoint_nodes"], final=final)
    print(f"test set: {st['partitions'].get('test_size')} task(s); node set ({resolved['endpoint_nodes']}):")
    for n in nodes:
        print(f"  {n['index']}. {n['candidate_id']} round {n['round']} ({n['kind']})")
    if (state_dir / ENDPOINT_FILE).exists():
        print(endpoint.render(_read(state_dir / ENDPOINT_FILE, "endpoint")))
        return EXIT_OK
    if not args.confirm:
        print("not confirmed: nothing claimed. The test set is evaluated once; with the user's consent rerun "
              "with --confirm --budget <file>.")
        return EXIT_USAGE
    _require(args, "budget")
    budget = _read(Path(args.budget), "budget file")
    if not isinstance(budget, dict):
        raise UsageError("budget file must hold a JSON object")
    args.iteration, args.run_id, args.actor = ident["iteration_id"], ident["run_id"], args.actor or ident["actor"]
    settings = _settings(resolved["adapter"])
    services = build_services(args.config, args.state, args.iteration, actor=args.actor, run_id=args.run_id,
                              adapter_settings=settings)
    try:
        result = endpoint.evaluate(services.adapter, nodes, budget=budget, actor=args.actor)
    except EvaluationUnavailable as exc:
        print(f"test evaluation unavailable: {exc}", file=sys.stderr)
        return EXIT_HALTED
    except ValueError as exc:
        raise UsageError(str(exc)) from None
    rep = endpoint.report(result, nodes, st["lineage"], k_val=(st.get("calibration") or {}).get("k_val"),
                          ablation=st.get("ablation"))
    ledger_io.atomic_write_json(state_dir / ENDPOINT_FILE, rep)
    print(endpoint.render(rep))
    return EXIT_OK


# ---------------------------------------------------------------------------

class _Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(EXIT_USAGE, f"{self.prog}: error: {message}\n")


def build_parser() -> argparse.ArgumentParser:
    local = Path("configs/sn-workbench-local.json").is_file()
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default=os.environ.get(
        "SN_WORKBENCH_CONFIG", "configs/sn-workbench-local.json" if local else "var/sn-workbench-server/config.json"))
    common.add_argument("--state", default=os.environ.get(
        "SN_WORKBENCH_STATE", "var/sn-workbench" if local else "var/sn-workbench-server/state"))
    common.add_argument("--iteration")
    common.add_argument("--actor")
    common.add_argument("--run-id")
    common.add_argument("--state-dir")
    parser = _Parser(prog="python -m rag_eval.optimizer.cli v4", description="method-v4 on SN")
    sub = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)
    run = sub.add_parser("run", parents=[common], help="calibrate and run rounds (start or resume)")
    run.add_argument("--v4-config", help="pre-registered v4 config JSON")
    run.add_argument("--retry-failed", action="store_true", help="after process_failure: new measurement keys")
    run.add_argument("--no-test-set", action="store_true", help="rehearsal: run without an endpoint")
    sub.add_parser("status", parents=[common], help="print the run's state")
    test = sub.add_parser("test", parents=[common], help="endpoint: preview; --confirm evaluates the test set once")
    test.add_argument("--confirm", action="store_true", help="human confirmation: claim the test set")
    test.add_argument("--budget", help="JSON object passed to v2 run_lineage(budget=...)")
    test.add_argument("--allow-failure-halt", action="store_true")
    fig = sub.add_parser("figure", parents=[common], help="figure: wall clock x test score of the endpoint nodes")
    fig.add_argument("--out", help="output file: .svg, .pdf or .png")
    fig.add_argument("--lang", choices=("zh", "en"), default="zh")
    fig.add_argument("--font", help="font file for the labels")
    fig.add_argument("--width", type=float, default=3.4)
    fig.add_argument("--height", type=float, default=2.5)
    return parser


def cmd_figure(args) -> int:
    _require(args, "state_dir", "out")
    if Path(args.out).suffix.lower() not in (".svg", ".pdf", ".png"):
        raise UsageError("--out must end in .svg, .pdf or .png")
    rep = _read(Path(args.state_dir) / ENDPOINT_FILE, "endpoint (run v4 test --confirm first)")
    try:
        lang = endpoint.figure(rep, args.out, lang=args.lang, font=args.font, width=args.width, height=args.height)
    except ImportError as exc:
        raise UsageError(f"the figure needs matplotlib: {exc}") from None
    except ValueError as exc:
        raise UsageError(str(exc)) from None
    print(f"figure: {args.out} ({len(rep['points'])} node(s), labels {lang})")
    return EXIT_OK


COMMANDS = {"run": cmd_run, "status": cmd_status, "test": cmd_test, "figure": cmd_figure}


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return COMMANDS[args.command](args)
    except UsageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
