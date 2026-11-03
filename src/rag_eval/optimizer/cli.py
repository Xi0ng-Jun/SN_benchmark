"""One-command entry point for method-v3 on SN (binding plan W7).

    python -m rag_eval.optimizer.cli <subcommand> [options]

Subcommands
-----------
run          Pre-registered run, or resume of one (``--retry-failed`` after a
             process-failure halt issues new measurement keys).
analyse-only Server validation stage 1: one analyst dispatch on the root's
             search failures; nothing is derived, implemented, frozen or
             measured on fresh / sealed data.
status       Read ``state.json`` / ``ledger.json`` of a run directory.
sealed       End step: one sealed evaluation of seed + every lineage node.

Services are built exactly as the workbench server builds them: the same
``load_config`` and ``create_app`` (``rag_eval.workbench.api``); the v2
``RoundOperations`` instance is taken from the app, never assembled a second
way. The app's lifespan (execution dispatch, round manager, recovery) is not
started: those stay owned by the running server. This process launches the
executions it requests and supervises them while it runs, like the server's
own round loop; the server reconciles them if this process dies.

Exit codes: 0 success, 1 unexpected error, 2 usage / validation error,
3 the run halted with process_failure or budget_exhausted.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import sys
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from . import ledger_io, run_view
from .adapter import EvaluationUnavailable, Version
from .ledger import Lineage, NodeState
from .loop import ProtocolConfig, run_optimization, select_analysis_cases

EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_HALTED = 0, 1, 2, 3
FAILURE_HALTS = ("process_failure", "budget_exhausted")

CLI_RUN_FILE = "cli-run.json"
PROTOCOL_COPY_FILE = "protocol.json"
SMOKE_FILE = "smoke-sample.json"
MESSAGE_FILE = "analyse-only-message.txt"   # the analyst message analyse-only sent (or tried to)
EVIDENCE_DIR = "analyst-evidence"            # evidence files the analyst messages name
SEALED_CURVE_FILE = "sealed-curve.json"
SMOKE_RULE = "first smoke_sample_size search ids ordered by sha256(case_id) hex digest"

# Run-level keys of the protocol file (besides ProtocolConfig fields).
RUN_KEYS = {"max_rounds": int, "smoke_sample_size": int}
REQUIRED_RUN_KEYS = ("max_rounds", "smoke_sample_size")
# SNAdapter constructor settings; values are part of the pre-registration.
# Defaults follow binding plan §2.5 (30 s polling, 2 h evaluation, 90 min agents).
ADAPTER_DEFAULTS: dict[str, Any] = dict(
    analyst_profile="pi-flash",
    implementer_profile="pi-flash",
    agent_poll_interval=10.0,
    agent_timeout=5400.0,
    worktree_timeout=600.0,
    analyst_view_budget=12000,
    eval_poll_interval=30.0,
    eval_timeout=7200.0,
    infra_retry=1,
    execution_timeout_seconds=7200,
)
_ADAPTER_TYPES = {k: (str if isinstance(v, str) else int if isinstance(v, int) else float)
                  for k, v in ADAPTER_DEFAULTS.items()}
# ProtocolConfig keys not accepted in the file (one source per quantity).
_REPLACED = {"n_smoke_sample": "smoke_sample_size"}


class UsageError(Exception):
    """Invalid arguments, protocol file or run directory (exit 2)."""


# ---------------------------------------------------------------------------
# Protocol file
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Protocol:
    config: ProtocolConfig
    max_rounds: int
    smoke_sample_size: int
    adapter_settings: dict
    sha256: str           # of the file's bytes
    resolved: dict        # every effective value, defaults filled in


def _config_fields() -> dict[str, dataclasses.Field]:
    return {f.name: f for f in dataclasses.fields(ProtocolConfig)}


def _check_type(key: str, value: Any, kind: str) -> None:
    def bad(expected: str) -> UsageError:
        return UsageError(f"protocol key {key!r} must be {expected}, got {value!r}")
    if kind in ("int", int):
        if type(value) is not int:
            raise bad("an integer")
    elif kind in ("float", float):
        if type(value) not in (int, float):
            raise bad("a number")
    elif kind in ("str", str):
        if type(value) is not str or not value:
            raise bad("a non-empty string")
    elif kind == "dict":
        if not isinstance(value, dict):
            raise bad("an object")
    elif kind == "Optional[int]":
        if value is not None and type(value) is not int:
            raise bad("an integer or null")
    else:  # pragma: no cover - a new ProtocolConfig field type
        raise UsageError(f"protocol key {key!r} has an unsupported type {kind!r}")


def load_protocol(path: str | Path) -> Protocol:
    """Parse and strictly validate a pre-registered protocol file."""
    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise UsageError(f"cannot read protocol file {path}: {exc}") from None
    try:
        data = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UsageError(f"protocol file {path} is not valid JSON: {exc}") from None
    if not isinstance(data, dict):
        raise UsageError("protocol file must hold a JSON object")
    fields = _config_fields()
    for key, replacement in _REPLACED.items():
        if key in data:
            raise UsageError(f"protocol key {key!r} is not accepted; use {replacement!r}")
    allowed = (set(fields) - set(_REPLACED)) | set(RUN_KEYS) | set(ADAPTER_DEFAULTS)
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise UsageError(f"unknown protocol key(s): {', '.join(unknown)}")
    required = [n for n, f in fields.items()
                if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING]
    missing = [k for k in [*required, *REQUIRED_RUN_KEYS] if k not in data]
    if missing:
        raise UsageError(f"missing protocol key(s): {', '.join(missing)}")
    for key, value in data.items():
        if key in fields:
            _check_type(key, value, fields[key].type)
        elif key in RUN_KEYS:
            _check_type(key, value, RUN_KEYS[key])
        else:
            _check_type(key, value, _ADAPTER_TYPES[key])
    cfg_values = {k: v for k, v in data.items() if k in fields}
    if cfg_values.get("k_smoke", 1) != 1:
        raise UsageError("k_smoke must be 1 on SN (a smoke run is one attempt per side)")
    for key in ("max_rounds", "smoke_sample_size"):
        if data[key] < 1:
            raise UsageError(f"{key} must be >= 1")
    for key in ("b_cards", "k_screen", "K_max", "n_fresh"):
        if key in cfg_values and cfg_values[key] < 1:
            raise UsageError(f"{key} must be >= 1")
    if cfg_values.get("analyst_cards") is not None and cfg_values["analyst_cards"] < 1:
        raise UsageError("analyst_cards must be >= 1 or null")
    from .sn_adapter import MAX_ANALYST_VIEW_BUDGET
    budget = data.get("analyst_view_budget", ADAPTER_DEFAULTS["analyst_view_budget"])
    if not 0 < budget <= MAX_ANALYST_VIEW_BUDGET:
        raise UsageError(f"analyst_view_budget must be in (0, {MAX_ANALYST_VIEW_BUDGET}] characters")
    config = ProtocolConfig(n_smoke_sample=data["smoke_sample_size"], **cfg_values)
    settings = {k: data.get(k, v) for k, v in ADAPTER_DEFAULTS.items()}
    resolved = dict(dataclasses.asdict(config), max_rounds=data["max_rounds"],
                    smoke_sample_size=data["smoke_sample_size"], **settings)
    return Protocol(config=config, max_rounds=data["max_rounds"],
                    smoke_sample_size=data["smoke_sample_size"], adapter_settings=settings,
                    sha256=hashlib.sha256(raw).hexdigest(), resolved=resolved)


def default_analysis_config() -> ProtocolConfig:
    """analyse-only without --protocol: only the analyst selection fields matter."""
    return ProtocolConfig(mde=0.0, sigma_d=1.0, ladder_c=1.0, K_max=1, T=0.0, n_fresh=1)


def smoke_sample(search_ids: list[str], size: int) -> list[str]:
    """Pre-registered smoke rule: first *size* search ids ordered by sha256(case_id)."""
    if size > len(search_ids):
        raise UsageError(f"smoke_sample_size={size} exceeds the search set ({len(search_ids)} cases)")
    return sorted(search_ids, key=lambda c: (hashlib.sha256(c.encode()).hexdigest(), c))[:size]


# ---------------------------------------------------------------------------
# Service construction (the one boundary tests replace)
# ---------------------------------------------------------------------------

@dataclass
class Services:
    adapter: Any              # SNAdapter
    iteration_id: str
    task_id: str
    root_candidate_id: str
    writable: bool            # workbench may create candidates (task_worktrees.create_candidates)
    repository: Any = None    # workbench repository: binds the run to the iteration


def build_services(config_path: str | Path, state_root: str | Path, iteration_id: str, *,
                   actor: str, run_id: Optional[str], adapter_settings: dict) -> Services:
    """Build the SN adapter from the same objects the workbench server builds.

    ``serve`` does ``load_config(config)`` then ``create_app(state, config,
    actor=actor)``; RoundOperations is the instance create_app wires into its
    RoundService. Its lifespan (env guard, execution recovery, dispatch, round
    manager) is not entered; only the credential snapshot is installed here,
    as the server does at startup, so executions launched from this process
    inherit the same SYSTEM_* credentials.
    """
    from rag_eval.workbench import env_guard
    from rag_eval.workbench.api import create_app
    from rag_eval.workbench.configuration import load_config

    config = load_config(Path(config_path))
    env_guard.install(Path(state_root) / "env-guard.jsonl")
    app = create_app(str(state_root), config, actor=actor)
    return services_from_operations(app.state.round_service.operations, config, iteration_id, actor=actor,
                                    run_id=run_id, adapter_settings=adapter_settings,
                                    where=f"workbench state {state_root}")


def services_from_operations(ops, config: dict, iteration_id: str, *, actor: str, run_id: Optional[str],
                             adapter_settings: dict, where: str = "the workbench state") -> Services:
    """SNAdapter and run identity over an existing v2 ``RoundOperations`` (the app's, or a test's)."""
    from rag_eval.workbench.repository import UnknownIdentity
    from .sn_adapter import SNAdapter
    try:
        record = ops.service.iteration_record(iteration_id)
    except UnknownIdentity:
        raise UsageError(f"unknown iteration {iteration_id!r} in {where}") from None
    if not record.get("root_candidate_id"):
        raise UsageError(f"iteration {iteration_id!r} has no root candidate")
    adapter_config = dict(config, iteration_id=iteration_id, task_id=record["task_id"],
                          run_id=run_id, actor=actor)
    adapter = SNAdapter(adapter_config, repository=ops.repository, run_views=ops.api.views,
                        round_operations=ops, hypotheses=ops.api.hypotheses, **adapter_settings)
    return Services(adapter=adapter, iteration_id=iteration_id, task_id=record["task_id"],
                    root_candidate_id=record["root_candidate_id"], writable=bool(ops.api.writable),
                    repository=ops.repository)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path, what: str) -> Any:
    try:
        return ledger_io.read_json(path)
    except FileNotFoundError:
        raise UsageError(f"{what} not found: {path}") from None
    except (OSError, json.JSONDecodeError) as exc:
        raise UsageError(f"cannot read {what} {path}: {exc}") from None


def _state(state_dir: Path) -> dict:
    return _read_json(state_dir / ledger_io.STATE_FILE, "run state")


def _require(args: argparse.Namespace, *names: str) -> None:
    missing = [f"--{n.replace('_', '-')}" for n in names if not getattr(args, n, None)]
    if missing:
        raise UsageError(f"{args.command} requires {', '.join(missing)}")


def _short(h: Optional[str]) -> str:
    return (h or "-")[:12]


def _versions(st: dict) -> dict[str, Version]:
    return {h: Version(content_hash=h, metadata=meta) for h, meta in st.get("versions", {}).items()}


def _services(args: argparse.Namespace, settings: dict) -> Services:
    return build_services(args.config, args.state, args.iteration, actor=args.actor,
                          run_id=args.run_id, adapter_settings=settings)


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------

def cmd_run(args: argparse.Namespace) -> int:
    _require(args, "iteration", "actor", "run_id", "state_dir", "protocol")
    protocol = load_protocol(args.protocol)
    state_dir = Path(args.state_dir)
    identity_path = state_dir / CLI_RUN_FILE
    state_path = state_dir / ledger_io.STATE_FILE
    identity = _read_json(identity_path, "run identity") if identity_path.exists() else None
    st = _state(state_dir) if state_path.exists() else None

    # --- refusals that need no services ---
    if identity is None and st is not None:
        raise UsageError(f"{state_dir} holds a loop state not started by this CLI; use a new --state-dir")
    if identity is not None:
        if identity["protocol_sha256"] != protocol.sha256:
            raise UsageError(
                f"protocol file changed since the run started (sha256 {protocol.sha256} != recorded "
                f"{identity['protocol_sha256']}); pre-registered parameters are read-only (method-v3 §3 step 0)")
        for key, flag, value in (("iteration_id", "--iteration", args.iteration),
                                 ("run_id", "--run-id", args.run_id), ("actor", "--actor", args.actor)):
            if identity.get(key) != value:
                raise UsageError(f"{flag} {value!r} differs from the run's {identity.get(key)!r} "
                                 "(v2 replays request keys only for the same identity)")
    key_generation = None
    if args.retry_failed:
        if st is None:
            raise UsageError("--retry-failed needs an existing run that halted with process_failure")
        if st.get("halt_reason") != "process_failure":
            raise UsageError(f"--retry-failed is only allowed after a process_failure halt "
                             f"(saved halt reason: {st.get('halt_reason')!r})")
        key_generation = int(st.get("key_generation", 0)) + 1

    if protocol.config.safeguard_bounds:
        raise UsageError("safeguard_bounds is not accepted on SN: the safeguards are the iteration protocol's "
                         "pre-registered v2 safeguards (one source per quantity)")

    # --- services and pre-registration checks against v2 ---
    services = _services(args, protocol.adapter_settings)
    adapter = services.adapter
    if not services.writable:
        raise UsageError("workbench config does not set task_worktrees.create_candidates=true; "
                         "v3 cannot derive candidates")
    parts = adapter.partitions()
    if protocol.config.k_screen != parts["k_base"]:
        raise UsageError(f"k_screen={protocol.config.k_screen} differs from the iteration's "
                         f"k_base={parts['k_base']} (binding plan §2.2: k comes from the v2 protocol)")
    if not parts.get("safeguards"):
        raise UsageError("the iteration protocol has no pre-registered safeguards (an lcd-v2 iteration has "
                         "them); method-v3 §1 requires them for every paired decision")
    search, validation = list(parts["search"]), list(parts["validation"])
    fresh = [list(b["case_ids"]) for b in parts["fresh_batches"]]
    if st is None:
        if parts["completed_fresh_batch_ids"]:
            raise UsageError(f"fresh batch(es) {parts['completed_fresh_batch_ids']} of this iteration are "
                             "already used by v2; a v3 run needs an iteration with unused fresh batches")
        if not fresh:
            raise UsageError("the iteration protocol has no fresh batches: no milestone can be confirmed")
        if not search or not validation:
            raise UsageError("the iteration protocol needs non-empty search and validation splits")
    smoke = smoke_sample(search, protocol.smoke_sample_size)
    if identity is not None:
        recorded = _read_json(state_dir / SMOKE_FILE, "smoke sample")
        if recorded.get("case_ids") != smoke:
            raise UsageError("the smoke sample derived from the search split differs from the recorded one")
    seed = adapter.version_for(services.root_candidate_id)
    if st is not None:
        if st.get("seed") != seed.content_hash:
            raise UsageError(f"root candidate version {seed.content_hash!r} differs from the run's seed {st.get('seed')!r}")
        expected = dict(search=search, initial_pool=validation, fresh_batches=fresh, smoke=smoke)
        if st.get("partitions") != expected:
            raise UsageError("search/validation/fresh/smoke ids from the v2 protocol differ from the ones "
                             "this run was started with")
        if st.get("config_sha256") != ledger_io.config_digest(protocol.config)[1]:
            raise UsageError("ProtocolConfig differs from the one this run was started with")

    _print_feasibility(protocol, parts, search, validation, fresh, smoke, resumed=st is not None)
    state_dir.mkdir(parents=True, exist_ok=True)
    adapter.set_evidence_dir(state_dir / EVIDENCE_DIR)
    try:
        with run_view.hold_run_lock(state_dir):
            return _run_locked(args, protocol, services, adapter, state_dir, identity, identity_path,
                               seed, search, validation, fresh, smoke, key_generation)
    except run_view.RunLocked as exc:
        raise UsageError(str(exc)) from None


def _run_locked(args, protocol, services, adapter, state_dir, identity, identity_path,
                seed, search, validation, fresh, smoke, key_generation) -> int:
    if services.repository is not None:
        from rag_eval.workbench import auto_runs
        from rag_eval.workbench.repository import Conflict
        try:
            auto_runs.register(services.repository, args.iteration, run_id=args.run_id,
                               state_dir=str(state_dir.resolve()), actor=args.actor)
        except Conflict as exc:
            raise UsageError(str(exc)) from None
    if identity is None:
        ledger_io.atomic_write_json(state_dir / SMOKE_FILE, dict(rule=SMOKE_RULE, size=len(smoke), case_ids=smoke))
        (state_dir / PROTOCOL_COPY_FILE).write_bytes(Path(args.protocol).read_bytes())
        ledger_io.atomic_write_json(identity_path, dict(
            iteration_id=args.iteration, run_id=args.run_id, actor=args.actor, task_id=services.task_id,
            root_candidate_id=services.root_candidate_id, seed=seed.content_hash,
            protocol_file=str(Path(args.protocol).resolve()), protocol_sha256=protocol.sha256,
            protocol_resolved=protocol.resolved, smoke_rule=SMOKE_RULE, created_at=_now()))
    if key_generation is not None:
        print(f"retry: key_generation {key_generation - 1} -> {key_generation}")

    result = run_optimization(adapter, seed, protocol.config, search_set_ids=search, val_pool_ids=validation,
                              fresh_batch_ids=fresh, smoke_sample_ids=smoke, max_rounds=protocol.max_rounds,
                              state_dir=state_dir, key_generation=key_generation)
    certified = [n for n in result.lineage.all_nodes() if n.state is NodeState.CERTIFIED]
    print(f"halt reason: {result.halt_reason}")
    print(f"rounds: {len(result.rounds)}")
    print(f"certified nodes: {len(certified)} ({', '.join(_short(n.version_hash) for n in certified)})")
    print(f"lineage nodes: {len(result.lineage.all_nodes())}")
    print(f"ledger: {state_dir / ledger_io.LEDGER_FILE}")
    if result.halt_reason == "process_failure":
        print("process failure: fix the cause, then rerun with --retry-failed")
    return EXIT_HALTED if result.halt_reason in FAILURE_HALTS else EXIT_OK


def _print_feasibility(protocol: Protocol, parts: dict, search: list, validation: list, fresh: list,
                       smoke: list, *, resumed: bool) -> None:
    cfg = protocol.config
    sizes = [len(b) for b in fresh]
    print(f"{'resume' if resumed else 'new run'}: k_base={parts['k_base']} search={len(search)} "
          f"validation={len(validation)} smoke={len(smoke)} fresh_batches={len(fresh)} sizes={sizes}")
    print(f"feasibility: at most {min(len(fresh), cfg.max_confirmations)} milestone confirmation(s) "
          f"(fresh batches {len(fresh)}, max_confirmations {cfg.max_confirmations}); "
          f"b={cfg.b_cards} max_rounds={protocol.max_rounds}")
    print("safeguards (iteration protocol): " + ", ".join(
        f"{s['key']} {s['direction']} margin {s['margin']}" for s in parts.get("safeguards") or []))
    if any(s != cfg.n_fresh for s in sizes):
        print(f"warning: pre-registered n_fresh={cfg.n_fresh} but fresh batch sizes are {sizes}")


# ---------------------------------------------------------------------------
# analyse-only (server validation stage 1)
# ---------------------------------------------------------------------------

def cmd_analyse_only(args: argparse.Namespace) -> int:
    _require(args, "iteration", "actor", "run_id", "state_dir")
    if args.n_cards is not None and args.n_cards < 1:
        raise UsageError("--n-cards must be >= 1")
    if args.protocol:
        protocol = load_protocol(args.protocol)
        cfg, settings = protocol.config, protocol.adapter_settings
    else:
        cfg, settings = default_analysis_config(), dict(ADAPTER_DEFAULTS)
    state_dir = Path(args.state_dir)
    services = _services(args, settings)
    adapter = services.adapter
    adapter.set_evidence_dir(state_dir / EVIDENCE_DIR)
    parts = adapter.partitions()
    root = adapter.version_for(services.root_candidate_id)
    search_eval = adapter.evaluate(root, list(parts["search"]), parts["k_base"], purpose="diagnose")
    selected, roles = select_analysis_cases(search_eval, cfg)
    n_cards = args.n_cards or cfg.analyst_cards or cfg.b_cards
    n_fail = sum(r == "failure" for r in roles.values())
    print(f"root {services.root_candidate_id} ({_short(root.content_hash)}): search={len(parts['search'])} "
          f"scored={len(search_eval.per_task_scores)} k={parts['k_base']}")
    print(f"selected: {len(selected)} (failures {n_fail}, controls {len(roles) - n_fail}); requesting {n_cards} card(s)")
    try:
        cards = adapter.analyse(selected, n_cards, case_roles=roles, parent=root, round_id=0, history=[])
    finally:
        text = getattr(adapter, "last_analyst_text", None)
        if text is not None:
            state_dir.mkdir(parents=True, exist_ok=True)
            ledger_io.atomic_text(state_dir / MESSAGE_FILE, text)
            print(f"analyst message: {state_dir / MESSAGE_FILE} ({len(text)} chars)")
        stats = getattr(adapter, "last_analysis", {}) or {}
        if stats.get("evidence_path"):
            print(f"analyst evidence: {stats['evidence_path']} sha256 {stats.get('evidence_sha256')}"
                  + (" (existing file kept)" if stats.get("evidence_reused") else ""))
    print("last_analysis: " + " ".join(
        f"{k}={stats.get(k)}" for k in ("selected", "with_view", "shown", "omitted", "evidence_chars",
                                         "budget", "message_bytes")))
    print(f"cards: {len(cards)}")
    from .sn_adapter import _signature_fields
    for i, card in enumerate(cards, 1):
        pred = card.predicate
        if pred is None:
            detail = "predicate: none"
        else:
            expr = pred.boolean_expression
            fields = sorted(_signature_fields(expr)) if isinstance(expr, dict) else []
            detail = f"predicate: yes ({pred.predicted_direction}) fields: {', '.join(fields) or '-'}"
        print(f"  {i}. [{card.ref}] {card.failure_mode_desc} | {detail}")
    return EXIT_OK


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

def cmd_status(args: argparse.Namespace) -> int:
    _require(args, "state_dir")
    state_dir = Path(args.state_dir)
    st = _state(state_dir)
    identity_path = state_dir / CLI_RUN_FILE
    if identity_path.exists():
        ident = _read_json(identity_path, "run identity")
        print(f"run: {ident.get('run_id')} iteration: {ident.get('iteration_id')} "
              f"protocol sha256: {ident.get('protocol_sha256')}")
    lineage = Lineage.from_records(st.get("lineage", []))
    versions = _versions(st)
    open_round = (st.get("current_round") or {}).get("round", {}).get("round_id")
    print(f"rounds: {st.get('round_counter', 0)} (finished {len(st.get('rounds', []))}, "
          f"open {open_round if open_round is not None else '-'})")
    pointer = lineage.current_parent()
    if pointer is not None:
        cid = (versions.get(pointer.version_hash) or Version(pointer.version_hash)).metadata.get("candidate_id")
        print(f"pointer: {_short(pointer.version_hash)} candidate {cid} ({pointer.state.value})")
    else:
        print("pointer: -")
    certified = [n for n in lineage.all_nodes() if n.state is NodeState.CERTIFIED]
    print(f"certified nodes: {len(certified)}" + "".join(
        f"\n  {_short(n.version_hash)} candidate "
        f"{(versions.get(n.version_hash) or Version(n.version_hash)).metadata.get('candidate_id')}"
        for n in certified))
    print(f"lineage nodes: {len(lineage.all_nodes())}")
    print(f"halt reason: {st.get('halt_reason') or '-'}")
    print(f"key_generation: {st.get('key_generation', 0)}")
    print(f"validation pool: {len(st.get('validation_pool', []))}")
    print(f"m: {st.get('ladder_m')}")
    best = (st.get("milestone") or {}).get("best_score")
    print(f"best score: {best}")
    total = len((st.get("partitions") or {}).get("fresh_batches", []))
    print(f"fresh batches used: {st.get('fresh_batches_used', 0)}/{total}")
    ledger = state_dir / ledger_io.LEDGER_FILE
    print(f"ledger: {ledger if ledger.exists() else '-'}")
    curve = state_dir / SEALED_CURVE_FILE
    if curve.exists():
        print(f"sealed curve: {curve}")
    return EXIT_OK


# ---------------------------------------------------------------------------
# sealed
# ---------------------------------------------------------------------------

def cmd_sealed(args: argparse.Namespace) -> int:
    _require(args, "iteration", "actor", "state_dir", "budget")
    budget = _read_json(Path(args.budget), "budget file")
    if not isinstance(budget, dict):
        raise UsageError("budget file must hold a JSON object")
    if args.k is not None and args.k < 1:
        raise UsageError("--k must be >= 1")
    state_dir = Path(args.state_dir)
    st = _state(state_dir)
    identity_path = state_dir / CLI_RUN_FILE
    # Adapter settings (poll cadence, eval timeout, ...) are the run's pre-registered ones.
    settings = dict(ADAPTER_DEFAULTS)
    if identity_path.exists():
        ident = _read_json(identity_path, "run identity")
        if ident.get("iteration_id") != args.iteration:
            raise UsageError(f"--iteration {args.iteration!r} differs from the run's {ident.get('iteration_id')!r}")
        args.run_id = args.run_id or ident.get("run_id")
        resolved = ident.get("protocol_resolved") or {}
        settings = {k: resolved.get(k, v) for k, v in ADAPTER_DEFAULTS.items()}
    halt = st.get("halt_reason")
    if not halt:
        raise UsageError("the run has not halted; the sealed end step runs once, after the run halts")
    if halt == "process_failure" and not args.allow_process_failure_halt:
        raise UsageError("the run halted with process_failure, which is retryable (run --retry-failed); "
                         "pass --allow-process-failure-halt to end it here")
    from .sn_sealed import evaluate_lineage_sealed, lineage_node_set
    from rag_eval.workbench.sealed import node_set_hash
    lineage, versions = Lineage.from_records(st["lineage"]), _versions(st)
    try:
        nodes = lineage_node_set(lineage, versions)
    except ValueError as exc:
        raise UsageError(str(exc)) from None
    digest = node_set_hash([n["candidate_id"] for n in nodes])
    print(f"sealed node set: {len(nodes)} node(s), node_set_hash {digest} (halt reason {halt})")
    for n in nodes:
        print(f"  {n['index']}. {n['candidate_id']} {_short(n['version_hash'])} {n['state']}")
    if not args.confirm:
        print("not confirmed: nothing claimed. The sealed split is evaluated once; rerun with --confirm.")
        return EXIT_USAGE
    services = _services(args, settings)
    try:
        curve = evaluate_lineage_sealed(services.adapter, lineage, versions, confirmed=True,
                                        budget=budget, k=args.k, actor=args.actor)
    except EvaluationUnavailable as exc:
        print(f"sealed evaluation unavailable: {exc}", file=sys.stderr)
        return EXIT_HALTED
    ledger_io.atomic_write_json(state_dir / SEALED_CURVE_FILE, curve)
    print(f"status: {curve['status']} k={curve['k']} n={curve.get('n')} correction={curve['correction']}")
    print(f"curve: {state_dir / SEALED_CURVE_FILE}")
    return EXIT_OK


# ---------------------------------------------------------------------------
# argument parsing
# ---------------------------------------------------------------------------

class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # argparse exits 2 already; keep its usage text
        self.print_usage(sys.stderr)
        self.exit(EXIT_USAGE, f"{self.prog}: error: {message}\n")


def build_parser() -> argparse.ArgumentParser:
    local = Path("configs/sn-workbench-local.json").is_file()
    common = argparse.ArgumentParser(add_help=False)
    # Same defaults as the server scripts (serve_workbench.sh, sn-eval).
    common.add_argument("--config", default=os.environ.get(
        "SN_WORKBENCH_CONFIG", "configs/sn-workbench-local.json" if local else "var/sn-workbench-server/config.json"),
        help="v2 workbench config (as the server uses)")
    common.add_argument("--state", default=os.environ.get(
        "SN_WORKBENCH_STATE", "var/sn-workbench" if local else "var/sn-workbench-server/state"),
        help="v2 workbench state root (as the server uses)")
    common.add_argument("--iteration", help="v2 iteration id")
    common.add_argument("--actor", help="v2 actor recorded on approvals, derives, freezes and requests")
    common.add_argument("--run-id", help="v3 run id; every v2 request key derives from it")
    common.add_argument("--state-dir", help="v3 run directory (state.json, ledger.json, ...)")
    parser = _Parser(prog="python -m rag_eval.optimizer.cli", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)
    run = sub.add_parser("run", parents=[common], help="start or resume a pre-registered run")
    run.add_argument("--protocol", help="pre-registered protocol JSON")
    run.add_argument("--retry-failed", action="store_true",
                     help="after a process_failure halt: bump key_generation by one and resume")
    analyse = sub.add_parser("analyse-only", parents=[common], help="validation stage 1: one analyst dispatch")
    analyse.add_argument("--protocol", help="optional protocol JSON (analyst selection and adapter settings)")
    analyse.add_argument("--n-cards", type=int)
    sub.add_parser("status", parents=[common], help="print a run directory's state")
    sealed = sub.add_parser("sealed", parents=[common], help="end step: sealed evaluation of the lineage")
    sealed.add_argument("--budget", help="JSON object passed to v2 run_lineage(budget=...)")
    sealed.add_argument("--confirm", action="store_true", help="human confirmation: claim the sealed split")
    sealed.add_argument("--k", type=int, help="repetitions per node (default: the protocol's k_sealed)")
    sealed.add_argument("--allow-process-failure-halt", action="store_true",
                        help="end a run that halted with process_failure instead of retrying it")
    return parser


COMMANDS = {"run": cmd_run, "analyse-only": cmd_analyse_only, "status": cmd_status, "sealed": cmd_sealed}


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return COMMANDS[args.command](args)
    except UsageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except Exception:  # unexpected: full traceback for the operator
        traceback.print_exc()
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
