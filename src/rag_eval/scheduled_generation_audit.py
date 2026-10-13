"""Pure run-level audit of saved scheduled-generation journal rows.

The audit consumes only JSON object rows already parsed by the run reader and
the reader's file facts (``journal_present``, ``truncated_tail``). It does not
read paths, import SN, inspect hosts/config/credentials/environment, contact
networks or models, run scorers or Jobs, keep a store or ledger, guess
``case_id``, or change comparison identity.

Two producer kinds are recognized: ``model_service_submission_attempt`` and
``model_service_future_terminal``. Pairing is by ``call_id`` alone, so journal
interleaving and out-of-order terminals do not affect the result. Attempt and
terminal ``service``/``context``/``snapshot_sha256`` must match exactly.
Duplicate attempts/terminals, orphan terminals, unknown ``model_service_*``
schemas and illegal phase/status reject the whole audit with
:class:`ScheduledGenerationAuditError` instead of silently hiding loss or
joining the wrong generation. Recognized rows must match the producer's closed
schemas exactly, including the full service fact and context objects; a changed
or extended shape is unknown schema. An attempt without a terminal is a visible
``pending`` fact. ``truncated_tail`` marks even an otherwise complete parsed
prefix as incomplete. ``history_exhaustive`` is always false: a fully paired
saved prefix never proves that the execution lost no pair before the journal
began or between saved rows. Legacy ``event=started|completed|failed`` rows are
only counted, so the reader's existing ``unfinished_model_calls`` meaning is
untouched.

``pairs`` and ``unfinished`` are ordered by ``call_id``; ``attempt_row`` and
``terminal_row`` are zero-based journal positions. The ``invalid`` pair state is
reserved: structural ambiguity raises instead of returning a run-level
projection.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import math
import re
from typing import Any

__all__ = [
    "ATTEMPT_KIND",
    "FORMAT",
    "LEGACY_EVENTS",
    "TERMINAL_KIND",
    "ScheduledGenerationAuditError",
    "audit_scheduled_generation",
]

FORMAT = "sn-scheduled-generation-audit-v1"

ATTEMPT_KIND = "model_service_submission_attempt"
TERMINAL_KIND = "model_service_future_terminal"
LEGACY_EVENTS = ("started", "completed", "failed")

_SCHEDULED_KINDS = (ATTEMPT_KIND, TERMINAL_KIND)
_TERMINAL_STATUSES = ("completed", "error", "cancelled")
_PRIORITIES = ("interactive", "report", "background")
_SERVICE_KINDS = ("chat", "embedding", "rerank")
_PROTOCOLS = ("openai", "dashscope")

_ATTEMPT_KEYS = frozenset({
    "kind", "call_id", "service", "context", "snapshot_sha256", "phase", "recorded_at",
})
_TERMINAL_KEYS = _ATTEMPT_KEYS | frozenset({"status", "error_type"})
_SERVICE_KEYS = frozenset({
    "service_id", "kind", "protocol", "model", "endpoint_sha256",
    "credential_variable_name", "credential_variable_configured",
    "max_concurrency", "top_p", "generation",
})
_CONTEXT_KEYS = frozenset({"workload_id", "support_id", "priority"})

_ID = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_ENV = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_HASH = re.compile(r"[a-f0-9]{64}\Z")
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.@+:/-]{0,127}\Z")
_SECRET = re.compile(r"(?i)(?:(?:sk|rk|pk|ak)-|ghp_|hf_|xox|AIza)[A-Za-z0-9_-]{8,}")
_SUPPORT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_ERROR_TYPE = re.compile(r"[A-Za-z_]\w{0,127}\Z", re.ASCII)


class ScheduledGenerationAuditError(ValueError):
    """Saved scheduled-generation rows are structurally undecidable."""


def _reject(message: str):
    raise ScheduledGenerationAuditError(message)


def _hash(value, label: str, index: int) -> str:
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        _reject(f"Invalid {label} at journal row {index}")
    return value


def _call_id(row, index: int) -> int:
    value = row.get("call_id")
    if type(value) is not int or value < 1:
        _reject(f"Invalid scheduled generation call_id at journal row {index}")
    return value


def _recorded_at(row, index: int) -> None:
    value = row.get("recorded_at")
    if not isinstance(value, str) or not value:
        _reject(f"Invalid recorded_at at journal row {index}")


def _service(row, index: int) -> dict:
    service = row.get("service")
    if not isinstance(service, Mapping) or set(service) != _SERVICE_KEYS:
        _reject(f"Invalid scheduled generation service schema at journal row {index}")
    service = dict(service)
    service_id = service["service_id"]
    if not isinstance(service_id, str) or not _ID.fullmatch(service_id):
        _reject(f"Invalid service_id at journal row {index}")
    if service["kind"] not in _SERVICE_KINDS or service["protocol"] not in _PROTOCOLS:
        _reject(f"Invalid service kind/protocol at journal row {index}")
    if service["kind"] == "chat" and service["protocol"] != "openai":
        _reject(f"Invalid chat service protocol at journal row {index}")
    model = service["model"]
    if (not isinstance(model, str) or not _MODEL.fullmatch(model) or "://" in model
            or "//" in model or ".." in model or _SECRET.search(model)):
        _reject(f"Invalid service model at journal row {index}")
    _hash(service["endpoint_sha256"], "service endpoint hash", index)
    _hash(service["generation"], "service generation", index)
    variable = service["credential_variable_name"]
    if variable is not None and (not isinstance(variable, str) or not _ENV.fullmatch(variable)):
        _reject(f"Invalid credential variable name at journal row {index}")
    if (type(service["credential_variable_configured"]) is not bool
            or service["credential_variable_configured"] != (variable is not None)):
        _reject(f"Inconsistent credential variable presence at journal row {index}")
    if type(service["max_concurrency"]) is not int or service["max_concurrency"] < 1:
        _reject(f"Invalid service concurrency at journal row {index}")
    top_p = service["top_p"]
    if top_p is not None and (service["kind"] != "chat" or type(top_p) not in (int, float)
                              or not math.isfinite(top_p) or not 0 <= top_p <= 1):
        _reject(f"Invalid service top_p at journal row {index}")
    return service


def _context(row, index: int) -> dict:
    context = row.get("context")
    if not isinstance(context, Mapping) or set(context) != _CONTEXT_KEYS:
        _reject(f"Invalid scheduled generation context schema at journal row {index}")
    context = dict(context)
    workload_id = context["workload_id"]
    if not isinstance(workload_id, str) or not _ID.fullmatch(workload_id):
        _reject(f"Invalid workload_id at journal row {index}")
    support_id = context["support_id"]
    if not isinstance(support_id, str) or not _SUPPORT.fullmatch(support_id):
        _reject(f"Invalid support_id at journal row {index}")
    if context["priority"] not in _PRIORITIES:
        _reject(f"Invalid context priority at journal row {index}")
    return context


def _snapshot(row, index: int) -> dict:
    service = _service(row, index)
    context = _context(row, index)
    return {
        "service": service,
        "context": context,
        "service_id": service["service_id"],
        "generation": service["generation"],
        "workload_id": context["workload_id"],
        "support_id": context["support_id"],
        "priority": context["priority"],
        "snapshot_sha256": _hash(row.get("snapshot_sha256"), "snapshot_sha256", index),
    }


def _parse_attempt(row, index: int):
    if set(row) != _ATTEMPT_KEYS:
        _reject(f"Invalid {ATTEMPT_KIND} schema at journal row {index}")
    call_id = _call_id(row, index)
    if row.get("phase") != "submission":
        _reject(f"Invalid {ATTEMPT_KIND} phase at journal row {index}")
    _recorded_at(row, index)
    return call_id, _snapshot(row, index)


def _safe_error_type(value) -> bool:
    return isinstance(value, str) and _ERROR_TYPE.fullmatch(value) is not None


def _parse_terminal(row, index: int):
    if set(row) != _TERMINAL_KEYS:
        _reject(f"Invalid {TERMINAL_KIND} schema at journal row {index}")
    call_id = _call_id(row, index)
    _recorded_at(row, index)
    phase, status, error_type = row.get("phase"), row.get("status"), row.get("error_type")
    if phase == "future":
        if status not in _TERMINAL_STATUSES:
            _reject(f"Invalid {TERMINAL_KIND} status at journal row {index}")
        if status == "error":
            if not _safe_error_type(error_type):
                _reject(f"Invalid terminal error_type at journal row {index}")
        elif error_type is not None:
            _reject(f"Invalid terminal error_type at journal row {index}")
        terminal_kind = "returned_future"
    elif phase == "submission":
        if status != "error" or not _safe_error_type(error_type):
            _reject(f"Invalid {TERMINAL_KIND} phase/status at journal row {index}")
        terminal_kind = "sync_refusal"
    else:
        _reject(f"Invalid {TERMINAL_KIND} phase at journal row {index}")
    return call_id, terminal_kind, status, _snapshot(row, index)


def _result(*, observation_state, attempt_count, terminal_count, paired_count, unfinished,
            pairs, pair_state, legacy_row_count, truncated_tail, warnings):
    return {
        "format": FORMAT,
        "observation_state": observation_state,
        "attempt_count": attempt_count,
        "terminal_count": terminal_count,
        "paired_count": paired_count,
        "unfinished": unfinished,
        "pairs": pairs,
        "completeness": {
            "pair_state": pair_state,
            "unpaired_attempts": len(unfinished),
            "orphan_terminals": 0,
            "truncated_tail": truncated_tail,
            "history_exhaustive": False,
            "scope": "observed_journal_only",
        },
        "warnings": warnings,
        "legacy_row_count": legacy_row_count,
    }


def audit_scheduled_generation(rows: Sequence[Mapping[str, Any]], *,
                               journal_present: bool, truncated_tail: bool = False) -> dict:
    """Project saved scheduled-generation rows into pure run-level facts.

    ``rows`` are already-parsed JSON objects in journal order. ``journal_present``
    distinguishes a missing historical journal from a present but empty one, and
    ``truncated_tail`` is the reader's explicit fact that the final JSON line was
    dropped. Structural ambiguity rejects with :class:`ScheduledGenerationAuditError`;
    observation gaps return warnings instead. No ``case_id`` is guessed and no raw
    row, answer, response, endpoint, credential, scorer or Job fact is projected.
    """
    if type(journal_present) is not bool or type(truncated_tail) is not bool:
        _reject("Scheduled generation audit requires boolean reader facts")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes, bytearray)):
        _reject("Scheduled generation audit requires parsed object rows")
    if not journal_present:
        if len(rows):
            _reject("Scheduled generation rows require a present journal")
        return _result(observation_state="no_observation", attempt_count=0, terminal_count=0,
                       paired_count=0, unfinished=[], pairs=[], pair_state="not_observed",
                       legacy_row_count=0, truncated_tail=truncated_tail, warnings=[])

    attempts, terminals, legacy_row_count = {}, {}, 0
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            _reject(f"Scheduled generation journal row {index} is not an object")
        kind = row.get("kind")
        if kind in _SCHEDULED_KINDS:
            if kind == ATTEMPT_KIND:
                call_id, snapshot = _parse_attempt(row, index)
                if call_id in attempts:
                    _reject(f"Duplicate scheduled generation attempt for call_id {call_id}")
                attempts[call_id] = {"row": index, "snapshot": snapshot}
            else:
                call_id, terminal_kind, status, snapshot = _parse_terminal(row, index)
                if call_id in terminals:
                    _reject(f"Duplicate scheduled generation terminal for call_id {call_id}")
                terminals[call_id] = {"row": index, "terminal_kind": terminal_kind,
                                      "status": status, "snapshot": snapshot}
        elif isinstance(kind, str) and kind.startswith("model_service_"):
            _reject(f"Unknown model_service_* journal kind: {kind}")
        elif row.get("event") in LEGACY_EVENTS:
            legacy_row_count += 1

    orphans = sorted(set(terminals) - set(attempts))
    if orphans:
        _reject(f"Scheduled generation terminal without attempt for call_id {orphans[0]}")

    pairs, unfinished = [], []
    for call_id in sorted(attempts):
        attempt = attempts[call_id]
        terminal = terminals.get(call_id)
        if terminal is None:
            unfinished.append({"call_id": call_id, "state": "pending"})
            continue
        left, right = attempt["snapshot"], terminal["snapshot"]
        mismatch = [name for name in ("service", "context", "snapshot_sha256")
                    if left[name] != right[name]]
        if mismatch:
            _reject(f"Scheduled generation pair {call_id} identity mismatch: {', '.join(mismatch)}")
        pairs.append({
            "call_id": call_id,
            "terminal_kind": terminal["terminal_kind"],
            "status": terminal["status"],
            "workload_id": left["workload_id"],
            "support_id": left["support_id"],
            "priority": left["priority"],
            "service_id": left["service_id"],
            "generation": left["generation"],
            "snapshot_sha256": left["snapshot_sha256"],
            "attempt_row": attempt["row"],
            "terminal_row": terminal["row"],
        })

    scheduled_observed = bool(attempts or terminals)
    if scheduled_observed:
        observation_state = "scheduled_observed"
    elif legacy_row_count:
        observation_state = "legacy_only"
    else:
        observation_state = "observed_empty"
    if truncated_tail:
        pair_state = "incomplete"
    elif not scheduled_observed:
        pair_state = "empty"
    elif unfinished:
        pair_state = "incomplete"
    else:
        pair_state = "complete"

    warnings = []
    if observation_state == "observed_empty" and not truncated_tail:
        warnings.append("model-events journal is present but empty; scheduled generation was not observed")
    if unfinished:
        ids = ", ".join(str(item["call_id"]) for item in unfinished)
        warnings.append(f"Scheduled generation attempts without a terminal: {ids}")
    if truncated_tail:
        warnings.append("model-events journal tail is truncated; parsed rows may be incomplete")

    return _result(observation_state=observation_state, attempt_count=len(attempts),
                   terminal_count=len(terminals), paired_count=len(pairs), unfinished=unfinished,
                   pairs=pairs, pair_state=pair_state, legacy_row_count=legacy_row_count,
                   truncated_tail=truncated_tail, warnings=warnings)
