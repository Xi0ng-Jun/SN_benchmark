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

Signals during ``run``: SIGINT stops polling and keeps remote work (resumable);
SIGTERM raises ``RunStopRequested``: executions are cancelled, Agents archived,
receipts written and the run halts ``stopped`` (final; next step ``v4 test``).
"""
from __future__ import annotations

import argparse
import copy
import contextlib
import json
import os
import signal
import sys
import traceback
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .. import ledger_io, run_view
from ..adapter import EvaluationUnavailable, Version
from . import calibrate, endpoint
from .config import ConfigError, load
from .loop import FAILURE_HALTS, LEDGER_FILE, STATE_FILE, STOPPED, RunStopRequested, V4Run, final_version

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


@contextlib.contextmanager
def stop_on_sigterm():
    """SIGTERM -> RunStopRequested in the main thread, once; later SIGTERMs are ignored while stopping."""
    def handler(signum, frame):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        raise RunStopRequested("SIGTERM")
    previous = signal.signal(signal.SIGTERM, handler)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def _settings(adapter_settings: dict) -> dict:
    """SNAdapterV4 keyword arguments from the config's adapter section."""
    return {k: v for k, v in adapter_settings.items()}


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------

def cmd_run(args) -> int:
    if getattr(args, 'from_proposal', None):
        return cmd_calibrated_run(args)
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
        if identity.get('frozen_config_sha256') and not getattr(args, '_frozen_config', None):
            raise UsageError('this calibrated run must resume with --from-proposal')
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
        if st.get('failure_kind') == 'agent_request':
            raise UsageError('Agent failure does not need new measurement keys; inspect/recover the Agent request first')
        key_generation = int(st.get("key_generation", 0)) + 1
    if st is not None and st.get("halt_reason") and st["halt_reason"] not in FAILURE_HALTS:
        raise UsageError(f"the run already stopped ({st['halt_reason']}); next step: v4 test")

    services = build_services(args.config, args.state, args.iteration, actor=args.actor, run_id=args.run_id,
                              adapter_settings=_settings(loaded.adapter_settings))
    from .startup import preflight
    preflight(services, args, loaded.adapter_settings, ('analyst', 'implementer', 'reviewer'))
    adapter = services.adapter
    if not services.writable:
        raise UsageError("workbench config does not set task_worktrees.create_candidates=true")
    parts = adapter.partitions()
    cfg = loaded.config
    # method-v4.1: safeguards come from the v4 config; the protocol need not register any.
    observable = set(adapter.observable_safeguards())
    missing = sorted(set(cfg.safeguards) - observable)
    if missing:
        raise UsageError(f"the v4 config names safeguard(s) {missing} that SN cannot observe "
                         f"(observable: {sorted(observable)})")
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
                               state_dir=str(state_dir.resolve()), actor=args.actor, method="method-v4")
        except Conflict as exc:
            raise UsageError(str(exc)) from None
    if identity is None:
        (state_dir / CONFIG_COPY_FILE).write_bytes(Path(args.v4_config).read_bytes())
        ledger_io.atomic_write_json(state_dir / IDENTITY_FILE, dict(
            method="v4", iteration_id=args.iteration, run_id=args.run_id, actor=args.actor,
            task_id=services.task_id, root_candidate_id=services.root_candidate_id, seed=seed.content_hash,
            config_file=str(Path(args.v4_config).resolve()), config_sha256=loaded.sha256,
            config_resolved=loaded.resolved, test_set=bool(test_ids) and not args.no_test_set,
            frozen_config_sha256=(getattr(args, '_frozen_config', None) or {}).get('payload_sha256'),
            calibration_sha256=(getattr(args, '_calibration_bundle', None) or {}).get('payload_sha256'),
            created_at=_now()))
    run = V4Run(services.adapter, seed, loaded.config, search=search, validation=validation,
                test_size=len(test_ids) or None, state_dir=state_dir, key_generation=key_generation,
                calibration_bundle=getattr(args, '_calibration_bundle', None),
                frozen_config=getattr(args, '_frozen_config', None))
    with stop_on_sigterm():
        reason = run.run()
    if run.feasibility is not None and run.calibration is not None:
        print(calibrate.render(run.feasibility, run.calibration))
    print(f"halt reason: {reason}")
    print(f"rounds: {len(run.rounds)}; adopted nodes: {len(run.lineage) - 1}")
    print(f"ledger: {state_dir / LEDGER_FILE}")
    if run.failure_detail:
        print(f"failure: {run.failure_detail}")
    if (run.cumulative or {}).get("warning"):
        print("WARNING: cumulative check: the final version breaches safeguard(s) "
              f"{run.cumulative['safeguards']['breached']} against the seed (no rollback)")
    if reason == STOPPED:
        print("stopped by SIGTERM: executions cancelled, Agents archived (see the session's cancellation receipts)")
    if reason == "process_failure":
        print("Agent failure: inspect the request; recover-agent-creation only supports confirmed pre-dispatch refusals"
              if run.failure_kind == 'agent_request' else
              "process failure: fix the cause, then rerun (or --retry-failed for new measurement keys)")
    elif reason not in FAILURE_HALTS:
        print("next: v4 test (preview), then v4 test --confirm --budget <file> with the user's consent")
    return EXIT_HALTED if reason in FAILURE_HALTS else EXIT_OK


def _bind(services, args):
    from rag_eval.workbench import auto_runs
    if not services.writable:
        raise UsageError('workbench config does not enable candidate creation')
    if services.repository is not None:
        auto_runs.register(services.repository, args.iteration, run_id=args.run_id,
            state_dir=str(Path(args.state_dir).resolve()), actor=args.actor, method='method-v4')


def cmd_calibrate(args):
    from . import calibration_session as session
    from .simulate import read_history
    _require(args, 'iteration', 'actor', 'run_id', 'state_dir', 'v4_config')
    loaded = load(args.v4_config)
    inputs = dict(time_budget_hours=loaded.config.time_budget_hours,
                  calibration_k=args.calibration_k or loaded.config.calibration_k,
                  safeguards=loaded.config.safeguards, delta=loaded.config.min_meaningful_improvement)
    for key in ('spend_budget', 'price_per_1k_tokens'):
        if getattr(args, key, None) is not None:
            inputs[key] = getattr(args, key)
    services = build_services(args.config, args.state, args.iteration, actor=args.actor, run_id=args.run_id,
                              adapter_settings=loaded.adapter_settings)
    if not args.no_advisor or args.expected_agent_profiles_sha256:
        from .startup import preflight
        preflight(services, args, loaded.adapter_settings)
    root = Path(args.state_dir)
    root.mkdir(parents=True, exist_ok=True)
    with run_view.hold_run_lock(root):
        from .calibration_import import ImportRefused, require_complete
        try:
            require_complete(root)
        except ImportRefused as exc:
            raise UsageError(str(exc)) from None
        _bind(services, args)
        adapter = services.adapter
        adapter.set_evidence_dir(root / EVIDENCE_DIR)
        from .calibration_recovery import configure
        recovery_timeout = configure(root, args.recovery_execution_timeout_seconds, write=False)
        adapter._recovery_execution_timeout_seconds = recovery_timeout
        adapter._eval_timeout = max(adapter._eval_timeout,
            recovery_timeout or loaded.adapter_settings['execution_timeout_seconds']) + 60
        concurrency = session.calibration_concurrency(root,
            args.calibration_concurrency if args.calibration_concurrency is not None else loaded.calibration_concurrency)
        # Calibration has few jobs; avoid a 30-second idle gap after each short warm execution.
        adapter._eval_poll_interval = min(adapter._eval_poll_interval, 5.)
        started = time.monotonic()
        adapter.configure_runtime(remaining=lambda: inputs['time_budget_hours'] * 3600 - (time.monotonic() - started),
                                  max_concurrent_executions=concurrency)
        try:
            with stop_on_sigterm():
                bundle = session.run_calibration(adapter, adapter.version_for(services.root_candidate_id),
                    adapter._round_ops.service, args.iteration, args.actor, root, inputs,
                    loaded.adapter_settings, read_history(args.history or []), concurrency=concurrency,
                    recovery_execution_timeout_seconds=recovery_timeout)
                if not args.no_advisor:
                    from . import advisor
                    advisor.run(adapter, adapter.version_for(services.root_candidate_id), root,
                                profile=adapter._calibrator_profile)
        except RunStopRequested:
            adapter.cancel_pending()
            raise
        except BaseException:
            adapter.stop_polling()
            raise
    print(json.dumps(session.public_summary(bundle), ensure_ascii=False))
    return EXIT_OK


def cmd_proposal(args):
    from . import calibration_session as session, proposal
    _require(args, 'state_dir')
    root = Path(args.state_dir)
    with run_view.hold_run_lock(root):
        bundle = session.load_bundle(root / session.CALIBRATION_FILE)
        proposed = proposal.Proposal.from_dict(_read(root / session.PROPOSAL_FILE, 'proposal'))
        if args.action == 'set':
            if (root / session.RUN_CONFIG_FILE).exists():
                raise UsageError('run configuration is frozen; proposal cannot be changed')
            if not args.assignment or '=' not in args.assignment:
                raise UsageError('proposal set requires KEY=JSON')
            key, raw = args.assignment.split('=', 1)
            proposed = proposal.apply_user_edit(proposed, key, json.loads(raw))
        checked = proposal.validate(proposed, bundle['summary'], bundle['history'])
        if not checked['ok']:
            raise UsageError(str(checked['fatal']))
        if args.action == 'set':
            ledger_io.atomic_write_json(root / session.PROPOSAL_FILE, checked['proposal'].to_dict())
    print(json.dumps(checked['proposal'].to_dict(), ensure_ascii=False, indent=2))
    return EXIT_OK


def cmd_calibrated_run(args):
    from . import calibration_session as session, proposal
    _require(args, 'iteration', 'actor', 'run_id', 'state_dir')
    root = Path(args.state_dir)
    if Path(args.from_proposal).resolve() != (root / session.PROPOSAL_FILE).resolve():
        raise UsageError('--from-proposal must name proposal.json in this --state-dir')
    from .calibration_import import ImportRefused, require_complete
    try:
        require_complete(root)
    except ImportRefused as exc:
        raise UsageError(str(exc)) from None
    bundle = session.load_bundle(root / session.CALIBRATION_FILE)
    settings = bundle['identity']['adapter_settings']
    services = build_services(args.config, args.state, args.iteration, actor=args.actor, run_id=args.run_id,
                              adapter_settings=settings)
    from .startup import preflight
    preflight(services, args, settings, ('analyst', 'implementer', 'reviewer'))
    with run_view.hold_run_lock(root):
        _bind(services, args)
        seed = services.adapter.version_for(services.root_candidate_id)
        if seed.content_hash != bundle['identity']['seed']:
            raise UsageError('calibration seed differs from current frozen root')
        frozen, bundle = session.freeze_run(services.adapter._round_ops.service, args.iteration, args.actor, root)
        runtime = session.runtime_config(frozen, bundle)
        path = root / 'resolved-config.json'
        if path.exists() and _read(path, 'resolved config') != runtime:
            raise UsageError('resolved run configuration changed')
        if not path.exists():
            ledger_io.atomic_write_json(path, runtime)
    resolved = copy.copy(args)
    resolved.from_proposal = None
    resolved.v4_config = str(path)
    resolved._calibration_bundle, resolved._frozen_config = bundle, frozen
    return cmd_run(resolved)


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
    print("calibration: " + (f"candidates k=1, rerun_var={cal['rerun_var']:.4g}" if cal else "-"))
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
    rep = endpoint.report(result, nodes, st["lineage"], k_val=1,
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
    common.add_argument('--expected-agent-profiles-sha256', help=argparse.SUPPRESS)
    parser = _Parser(prog="python -m rag_eval.optimizer.cli v4", description="method-v4 on SN")
    sub = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)
    run = sub.add_parser("run", parents=[common], help="calibrate and run rounds (start or resume)")
    run.add_argument("--v4-config", help="pre-registered v4 config JSON")
    run.add_argument('--from-proposal', help='freeze a completed calibration proposal and run without recalibrating')
    run.add_argument("--retry-failed", action="store_true", help="after process_failure: new measurement keys")
    run.add_argument("--no-test-set", action="store_true", help="rehearsal: run without an endpoint")
    recovery = sub.add_parser('recover-agent-creation', parents=[common],
        help='preview recovery of the current analyst pre-dispatch refusal; never creates an Agent')
    recovery.add_argument('--intent-id', required=True)
    recovery.add_argument('--confirm', action='store_true', help='prepare one linked replacement; resume separately')
    imported = sub.add_parser('import-calibration', parents=[common],
        help='reuse a completed calibration of another iteration (same seed, input and pool); preview by default')
    imported.add_argument('--from-iteration', required=True, help='the iteration whose calibration is reused')
    imported.add_argument('--confirm', metavar='PLAN_SHA256', help='perform the previewed plan')
    calibration = sub.add_parser('calibrate', parents=[common], help='measure seed P/D on a deferred iteration')
    calibration.add_argument('--v4-config', required=True)
    calibration.add_argument('--calibration-k', type=int, choices=range(2, 6))
    calibration.add_argument('--calibration-concurrency', type=int, choices=range(1, 17),
                             help='calibration execution cap (default from config: 2; 1 is serial)')
    calibration.add_argument('--history', action='append')
    calibration.add_argument('--spend-budget', type=float)
    calibration.add_argument('--price-per-1k-tokens', type=float)
    calibration.add_argument('--no-advisor', action='store_true', help='use rule values only')
    calibration.add_argument('--recovery-execution-timeout-seconds', type=int,
        help='existing incomplete calibration: longer timeout for new executions, saved once for resume')
    proposed = sub.add_parser('proposal', parents=[common], help='show or edit the calibration proposal')
    proposed.add_argument('action', choices=('show', 'set'))
    proposed.add_argument('assignment', nargs='?')
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


def cmd_import_calibration(args):
    """Preview (default) or perform reuse of another iteration's completed calibration."""
    from . import calibration_import as imp
    _require(args, 'iteration', 'actor', 'from_iteration')
    services = build_services(args.config, args.state, args.iteration, actor=args.actor, run_id=None,
                              adapter_settings={})
    root = Path(args.state_dir) if args.state_dir else imp.default_dir(args.state, args.iteration)
    service = services.adapter._round_ops.service
    try:
        planned = imp.plan(service, services.repository, source_iteration=args.from_iteration,
                           target_iteration=args.iteration, actor=args.actor, target_dir=root)
        if args.confirm is None:
            print(json.dumps(planned, ensure_ascii=False, indent=2))
            if not planned['ok']:
                return EXIT_USAGE
            print(f"next: rerun with --confirm {planned['plan_sha256']}", file=sys.stderr)
            return EXIT_OK
        receipt = imp.apply(service, services.repository, planned, confirm=args.confirm)
    except (imp.ImportRefused, run_view.RunLocked) as exc:
        raise UsageError(str(exc)) from None
    print(json.dumps(dict(receipt, state_dir=str(root)), ensure_ascii=False, indent=2))
    return EXIT_OK


def cmd_recover_agent_creation(args):
    from .agent_recovery import command
    return command(args)


COMMANDS = {"run": cmd_run, "status": cmd_status, "test": cmd_test, "figure": cmd_figure,
            'recover-agent-creation': cmd_recover_agent_creation,
            'calibrate': cmd_calibrate, 'proposal': cmd_proposal, 'import-calibration': cmd_import_calibration}


def main(argv: Optional[list[str]] = None) -> int:
    from rag_eval.workbench.agent_creations import CreationProfileUnavailable
    from rag_eval.workbench.agent_preflight import AgentPreflightError
    args = build_parser().parse_args(argv)
    try:
        return COMMANDS[args.command](args)
    except (UsageError, CreationProfileUnavailable, AgentPreflightError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
