"""Iterations driven by an automatic method run (method-v3).

Once a run is bound to an iteration the machine owns its lineage: the page is
read-only and people cannot derive, run, decide or message Agents through HTTP.
The lock sits on the human routes and the Agent command line, never on the
services, because the run itself calls those services.

The binding is one row: which run directory holds the iteration's state.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from .repository import Conflict

# What an Agent of an automatic iteration may run: exactly the commands the v3
# briefs name (briefs.V3_ANALYST_COMMANDS / V3_IMPLEMENTER_COMMANDS). Everything
# else reads scores (method-v3 §2.3: the analyst sees none; ``evidence`` prints
# search scores, the evidence file replaces it) or requests actions.
from .briefs import V3_ANALYST_COMMANDS, V3_IMPLEMENTER_COMMANDS
ANALYST_ALLOWED = frozenset(V3_ANALYST_COMMANDS)
IMPLEMENTER_ALLOWED = frozenset(V3_IMPLEMENTER_COMMANDS)


def _has_table(conn):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='auto_runs'").fetchone() is not None


def register(repository, iteration_id, *, run_id, state_dir, actor):
    """Bind *iteration_id* to a run; idempotent for the same run, refused for any other."""
    with repository.connect(write=True) as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS auto_runs(iteration_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, '
                     'state_dir TEXT NOT NULL, actor TEXT NOT NULL, registered_at TEXT NOT NULL)')
        row = conn.execute('SELECT * FROM auto_runs WHERE iteration_id=?', (iteration_id,)).fetchone()
        if row is not None:
            if row['run_id'] != run_id or row['state_dir'] != state_dir:
                raise Conflict(f"iteration {iteration_id} is bound to run {row['run_id']} in {row['state_dir']}")
            return dict(row)
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='iteration_round_invocations'").fetchone():
            from .loop import TERMINAL
            for (record,) in conn.execute('SELECT record FROM iteration_round_invocations WHERE iteration_id=?', (iteration_id,)):
                if json.loads(record).get('status') not in TERMINAL:
                    raise Conflict(f'iteration {iteration_id} has an active manual round')
        binding = dict(iteration_id=iteration_id, run_id=run_id, state_dir=state_dir, actor=actor,
                       registered_at=datetime.now(timezone.utc).isoformat())
        conn.execute('INSERT INTO auto_runs(iteration_id,run_id,state_dir,actor,registered_at) '
                     'VALUES(:iteration_id,:run_id,:state_dir,:actor,:registered_at)', binding)
        return binding


def binding(repository, iteration_id):
    with repository.connect() as conn:
        if not _has_table(conn):
            return None
        row = conn.execute('SELECT * FROM auto_runs WHERE iteration_id=?', (iteration_id,)).fetchone()
        return dict(row) if row else None


def bindings(repository):
    """Iteration id → binding, for the iteration list."""
    with repository.connect() as conn:
        if not _has_table(conn):
            return {}
        return {row['iteration_id']: dict(row) for row in conn.execute('SELECT * FROM auto_runs')}


def task_binding(repository, task_id):
    with repository.connect() as conn:
        if not _has_table(conn):
            return None
        row = conn.execute('SELECT a.* FROM auto_runs a JOIN iterations i ON i.id=a.iteration_id WHERE i.task_id=?',
                           (task_id,)).fetchone()
        return dict(row) if row else None


def _refusal(run_id):
    return Conflict(f'此迭代由自动运行 {run_id} 驱动，不接受手动操作')


def refuse_manual(repository, iteration_id):
    bound = binding(repository, iteration_id)
    if bound is not None:
        raise _refusal(bound['run_id'])


def refuse_manual_task(repository, task_id):
    bound = task_binding(repository, task_id)
    if bound is not None:
        raise _refusal(bound['run_id'])


def agent_refusal(repository, task_id, role, command):
    """The message refusing *command* for an Agent of an automatic iteration, or None."""
    bound = task_binding(repository, task_id)
    if bound is None:
        return None
    allowed = IMPLEMENTER_ALLOWED if role == 'implementer' else ANALYST_ALLOWED
    if command in allowed:
        return None
    return (f"this iteration is driven by automatic run {bound['run_id']}: a {role} may only use "
            + ', '.join(sorted(allowed)))
