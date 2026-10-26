"""What happened to model calls while a partition's notebook was prepared.

SN's preparation pipeline swallows model failures: ``summarize`` returns
"N parsed text element(s). <text>" and paper metadata falls back silently, so
a notebook can look prepared while a large share of its sources never reached
the model. The model-service journal still records each call's terminal error
type; this module reads it (and the prepared database) without SN imports.

Two kinds of failure are separated:

* infrastructure: the call never got a fair answer from the service -- SN's
  scheduler refused or timed it out, the service was rate-limited, unreachable
  or unauthorised, the transport timed out, or our own budget guard stopped
  it. Preparation under these conditions is not the system being measured, so
  the partition fails preparation and its cases are infrastructure errors.
* content: the service answered and SN rejected the answer
  (``MalformedModelResponse``) or another provider error. That is SN's real
  product behaviour and stays part of the measurement.
"""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re
import sqlite3

FORMAT = 'sn-preparation-diagnostics-v1'

# Journal ``error_type`` values are exception class names (runtime_model_identity).
INFRASTRUCTURE_ERROR_TYPES = frozenset({
    # SN model_work.ModelSchedulingError family: the process-local scheduler refused or timed out.
    'ModelSchedulingError', 'ModelQueueFull', 'ModelQueueTimeout', 'ModelServiceUnavailable',
    # No binding for the workload: every call of it falls back.
    'ModelNotConfiguredError',
    # OpenAI SDK used by SN's client: 429, 5xx, auth, connection and timeout classes.
    'RateLimitError', 'InternalServerError', 'APIConnectionError', 'APITimeoutError',
    'AuthenticationError', 'PermissionDeniedError',
    # httpx transport failures.
    'TimeoutException', 'ConnectTimeout', 'ReadTimeout', 'WriteTimeout', 'PoolTimeout',
    'ConnectError', 'ReadError', 'WriteError', 'RemoteProtocolError', 'NetworkError', 'TransportError',
    # Built-in network/timeout errors.
    'TimeoutError', 'ConnectionError', 'ConnectionResetError', 'ConnectionRefusedError',
    'ConnectionAbortedError', 'BrokenPipeError',
    # The evaluator's own request budget stopped the call.
    'BudgetStopped',
})

# SN source_ingestion.summarize fallback when no model summary was obtained.
FALLBACK_SUMMARY = re.compile(r'\d+ parsed text element\(s\)\. ')


class PreparationInfrastructureError(RuntimeError):
    """Model calls during preparation failed for infrastructure reasons."""


def model_call_errors(run):
    """Terminal model-call errors recorded so far, by type and by type x workload."""
    by_type, by_workload = Counter(), Counter()
    path = Path(run) / 'model-events.jsonl'
    if not path.is_file():
        return {}, {}
    for line in path.read_text(encoding='utf-8').splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue  # a torn final line is not an observation
        if (not isinstance(event, dict) or event.get('kind') != 'model_service_future_terminal'
                or event.get('status') != 'error'):
            continue
        error_type = str(event.get('error_type') or 'Exception')
        workload = str((event.get('context') or {}).get('workload_id') or 'unknown')
        by_type[error_type] += 1
        by_workload[error_type + ':' + workload] += 1
    return dict(sorted(by_type.items())), dict(sorted(by_workload.items()))


def summary_counts(database, notebook):
    """(sources, sources whose summary is SN's no-model fallback); None when unreadable."""
    try:
        db = sqlite3.connect(Path(database).resolve().as_uri() + '?mode=ro', uri=True)
    except sqlite3.Error:
        return None
    try:
        columns = {row[1] for row in db.execute("PRAGMA table_info('sources')")}
        if 'summary' not in columns:
            return None
        where, params = ('WHERE notebook_id=?', (notebook,)) if 'notebook_id' in columns else ('', ())
        rows = [row[0] for row in db.execute(f'SELECT summary FROM sources {where}', params)]
    except sqlite3.Error:
        return None
    finally:
        db.close()
    return len(rows), sum(1 for summary in rows if isinstance(summary, str) and FALLBACK_SUMMARY.match(summary))


def preparation_diagnostics(run, notebook):
    run = Path(run)
    by_type, by_workload = model_call_errors(run)
    counts = summary_counts(run / 'runtime/database.db', notebook)
    return dict(format=FORMAT, metric_role='diagnostic',
                model_call_errors=by_type, model_call_errors_by_workload=by_workload,
                infrastructure_errors={k: v for k, v in by_type.items() if k in INFRASTRUCTURE_ERROR_TYPES},
                sources=None if counts is None else counts[0],
                fallback_summaries=None if counts is None else counts[1])
