"""Iteration-wide estimated spend, separate from optional model price guards.

A reservation is one dispatched candidate attempt, including a failed attempt.
A whole action's candidate/parent footprint is reserved in one SQLite writer.
The cold-start estimate is CNY 1 per selected case, an explicit planning prior,
not a price assertion or a hard provider charge bound. Complete terminal CNY
observations replace it; unknown/partial costs retain at least the reservation.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import json
import math
from pathlib import Path

from .repository import Conflict, _text, now, uid

COLD_START_CNY_PER_CASE = 1.0


def _amount(value, *, positive=False):
    return (type(value) in (int, float) and math.isfinite(value)
            and (value > 0 if positive else value >= 0))


def validate_policy(policy):
    if policy is None:
        return None
    if (type(policy) is not dict
            or set(policy) - {'limit_cny', 'max_case_runs', 'max_runs_per_candidate'}
            or not set(policy) & {'limit_cny', 'max_case_runs'}
            or ('limit_cny' in policy and not _amount(policy['limit_cny'], positive=True))
            or ('max_case_runs' in policy and (type(policy['max_case_runs']) is not int or policy['max_case_runs'] < 1))
            or type(policy.get('max_runs_per_candidate')) is not int
            or policy['max_runs_per_candidate'] < 1):
        raise ValueError('Invalid iteration budget policy')
    result = dict(policy)
    if 'limit_cny' in result:
        result['limit_cny'] = float(result['limit_cny'])
    return result


def validate_model_budget(budget, execution_config):
    """Price admission belongs before requests, reservations and dispatch."""
    if isinstance(budget, dict) and 'limit_cny' in budget:
        from ..model_budget import check_priced_models
        check_priced_models((execution_config or {}).get('model_config'))


def case_retries(iteration):
    """In-attempt re-asks allowed per case by the frozen protocol (0 for older iterations)."""
    return ((iteration.get('protocol') or {}).get('case_retry') or {}).get('max_retries_per_case') or 0


def exceeded_caps(policy, summary, steps, per_case, retries=0):
    """Check all present limits against only the unreserved attempt footprint.

    Each attempt reserves its worst case: every case asked once plus
    ``retries`` re-asks (see ``case_retries``), so a hard cap holds before any
    retry is observed; settlement keeps only the re-asks that happened.
    """
    counts = Counter(summary.get('subject_runs_by_candidate', summary['runs_by_candidate']))
    counts.update(step['candidate_id'] for step in steps if step.get('counts_toward_candidate_cap', True))
    added = sum(step['case_count'] * (1 + retries) for step in steps)
    reasons = []
    if 'limit_cny' in policy and summary['charged_cny'] + added * per_case > policy['limit_cny'] + 1e-12:
        reasons.append('limit_cny')
    if 'max_case_runs' in policy and summary['case_runs_used'] + added > policy['max_case_runs']:
        reasons.append('max_case_runs')
    if any(count > policy['max_runs_per_candidate'] for count in counts.values()):
        reasons.append('max_runs_per_candidate')
    return reasons


def subject_roles(conn, iteration_id):
    """Infer legacy footprints only from durable orchestration ownership."""
    roles = {}
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if {'action_dispatches', 'action_requests'} <= tables:
        rows = conn.execute('SELECT d.steps_json,a.target_id FROM action_dispatches d '
                            'JOIN action_requests a ON a.id=d.request_id WHERE d.iteration_id=?', (iteration_id,))
        for row in rows:
            for step in json.loads(row[0]):
                roles[(step['candidate_id'], step['request_key'])] = step['candidate_id'] == row[1]
    if 'round_measurements' in tables:
        for row in conn.execute("SELECT payload_json FROM round_measurements WHERE json_extract(payload_json,'$.iteration_id')=?", (iteration_id,)):
            state = json.loads(row[0])
            for generation in state.get('generations', []):
                for step in generation:
                    roles.setdefault((step['candidate_id'], step['request_key']), step['candidate_id'] == state['candidate_id'])
    return roles


def is_subject(step, roles):
    return step.get('counts_toward_candidate_cap', roles.get((step['candidate_id'], step['request_key']), True))


def exhausted(summary):
    policy = summary.get('policy') or {}
    return (('limit_cny' in policy and summary['charged_cny'] >= policy['limit_cny'])
            or ('max_case_runs' in policy and summary['case_runs_used'] >= policy['max_case_runs']))


def initialize(repository):
    with repository.connect(write=True) as conn:
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS iteration_budget_policies(
                iteration_id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
                policy_json TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS iteration_budget_reservations(
                id TEXT PRIMARY KEY, iteration_id TEXT NOT NULL REFERENCES iteration_budget_policies(iteration_id),
                candidate_id TEXT NOT NULL, request_key TEXT NOT NULL, case_count INTEGER NOT NULL,
                step_json TEXT NOT NULL, reserved_cny REAL NOT NULL, estimate_source TEXT NOT NULL,
                execution_id TEXT UNIQUE, state TEXT NOT NULL, actual_cny REAL,
                observed_cny REAL, charged_cny REAL NOT NULL, cost_source TEXT,
                created_at TEXT NOT NULL, settled_at TEXT,
                retry_case_count INTEGER,
                UNIQUE(iteration_id,candidate_id,request_key));
        ''')
        migrate(conn)


def _has_retry_column(conn):
    return 'retry_case_count' in {r['name'] for r in conn.execute('PRAGMA table_info(iteration_budget_reservations)')}


def migrate(conn):
    """Add retry_case_count to a ledger created before it (inside the caller's writer).

    retry_case_count: re-asks inside the attempt; the reserved worst case until
    settled, then the observed count. Older rows (NULL) had none. Readers never
    depend on it (``_retry_count``), so an unmigrated database stays readable.
    """
    if BudgetLedger._available(conn) and not _has_retry_column(conn):
        conn.execute('ALTER TABLE iteration_budget_reservations ADD COLUMN retry_case_count INTEGER')


def _retry_count(row):
    return (row['retry_case_count'] if 'retry_case_count' in row.keys() else None) or 0


def _count_retried_cases(record):
    """Total (case × retry-round) pairs run as retries in this execution.

    Reads only the execution's own retry-rounds.json; zero when absent or
    the run did not complete.  Never reads a symlink.
    """
    run_dir = record.get('run_dir')
    if not run_dir:
        return 0
    path = Path(run_dir) / 'retry-rounds.json'
    try:
        if path.is_symlink():
            return 0
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return 0
    if not isinstance(data, dict) or data.get('format') != 'sn-workbench-retry-rounds-v2':
        return 0
    total = 0
    for partition in data.get('partitions', {}).values():
        if not isinstance(partition, dict):
            continue
        for retry in partition.get('retries', []):
            if isinstance(retry, dict) and isinstance(retry.get('case_ids'), list):
                total += len(retry['case_ids'])
    return total


def execution_cost(record):
    """Read explicit complete CNY evidence or the opt-in request price ledger.

    Raw provider ``usage.cost`` has no validated currency/boundary here and is
    deliberately not converted. Empty ledgers do not demonstrate zero usage.
    """
    state = record.get('run_state') or {}
    observation = state.get('cost_observation') if isinstance(state, dict) else None
    if (isinstance(observation, dict) and observation.get('currency') == 'CNY'
            and type(observation.get('complete')) is bool
            and _amount(observation.get('total_cny'))):
        return dict(complete=observation['complete'], total_cny=observation['total_cny'],
                    source='terminal_cost_observation')
    path = Path(record['run_dir']) / 'invocation' / 'budget.json'
    try:
        if path.is_symlink():
            return None
        saved = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if not isinstance(saved, dict) or saved.get('format') != 'sn-model-budget-v1':
        return None
    requests = saved.get('requests')
    if not isinstance(requests, dict) or not requests:
        return None
    if any(not isinstance(r, dict) or not _amount(r.get('cost_cny')) for r in requests.values()):
        return None
    complete = all(r.get('status') == 'observed' for r in requests.values())
    total = sum(r['cost_cny'] for r in requests.values() if r.get('status') == 'observed')
    if not math.isfinite(total):
        return None
    return dict(complete=complete, total_cny=total, source='model_request_ledger')


def _reservation(row):
    result = dict(row)
    result['step'] = json.loads(result.pop('step_json'))
    return result


class BudgetLedger:
    def __init__(self, repository, *, initialize=True):
        self.repository = repository
        if initialize:
            globals()['initialize'](repository)

    @staticmethod
    def _available(conn):
        return conn.execute("SELECT 1 FROM sqlite_master WHERE name='iteration_budget_reservations'").fetchone() is not None

    def _summary(self, conn, iteration_id):
        policy = conn.execute('SELECT policy_json FROM iteration_budget_policies WHERE iteration_id=?',
                              (iteration_id,)).fetchone() if self._available(conn) else None
        if policy is None:
            # The first reservation materializes the ledger policy. Until then
            # report the already-frozen protocol without creating records.
            frozen = None
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='iterations'").fetchone():
                row = conn.execute('SELECT protocol_json FROM iterations WHERE id=?', (iteration_id,)).fetchone()
                if row and row[0]:
                    frozen = json.loads(row[0]).get('budget')
            return dict(iteration_id=iteration_id, policy=frozen, charged_cny=0, reserved_cny=0,
                        actual_cny=None, observed_cny=0, unknown_count=0, runs_by_candidate={}, subject_runs_by_candidate={}, case_runs_used=0)
        rows = conn.execute('SELECT * FROM iteration_budget_reservations WHERE iteration_id=?',
                            (iteration_id,)).fetchall()
        roles = subject_roles(conn, iteration_id)
        complete = bool(rows) and all(r['state'] == 'settled' for r in rows)
        return dict(iteration_id=iteration_id, policy=json.loads(policy['policy_json']),
                    case_runs_used=sum(_retry_count(r) + r['case_count'] for r in rows),
                    charged_cny=sum(r['charged_cny'] for r in rows),
                    reserved_cny=sum(r['reserved_cny'] for r in rows if r['state'] != 'settled'),
                    actual_cny=sum(r['actual_cny'] for r in rows) if complete else None,
                    observed_cny=sum(r['observed_cny'] or 0 for r in rows),
                    unknown_count=sum(r['state'] == 'unknown' for r in rows),
                    runs_by_candidate=dict(Counter(r['candidate_id'] for r in rows)),
                    subject_runs_by_candidate=dict(Counter(r['candidate_id'] for r in rows
                        if is_subject(json.loads(r['step_json']), roles))))

    def summary(self, iteration_id, *, reconcile=True):
        if reconcile:
            self.reconcile(iteration_id)
        with self.repository.connect() as conn:
            return self._summary(conn, iteration_id)

    def reconcile(self, iteration_id):
        """Recover a supervisor crash between terminal publication and settlement.

        Called outside other writer transactions. Only explicitly bound attempts
        are inspected; the ledger never scans unrelated runtime directories.
        """
        with self.repository.connect() as conn:
            if not self._available(conn):
                return
            rows = conn.execute("SELECT h.content FROM iteration_budget_reservations b JOIN history h "
                                "ON h.object_id=b.execution_id WHERE b.iteration_id=? AND b.state!='settled' "
                                "AND h.seq=(SELECT MAX(t.seq) FROM history t WHERE t.object_id=b.execution_id "
                                "AND t.kind IN ('job.execution_finished','job.execution_cancelled'))",
                                (iteration_id,)).fetchall()
        for row in rows:
            self.settle_execution(json.loads(row['content']))

    def _estimate(self, conn, iteration):
        # Keep the estimate within this iteration's frozen execution conditions.
        # The denominator is actual case-runs (base + observed retries), so the
        # per-case estimate reflects what a candidate really spends to answer.
        retries = 'COALESCE(retry_case_count,0)' if _has_retry_column(conn) else '0'
        row = conn.execute(f'SELECT SUM(actual_cny),SUM(case_count + {retries}) '
                           "FROM iteration_budget_reservations WHERE iteration_id=? AND state='settled'",
                           (iteration['id'],)).fetchone()
        if row[1] and row[0] > 0:
            return row[0] / row[1], 'historical_observed_per_case'
        return COLD_START_CNY_PER_CASE, 'cold_start_per_case'

    def reserve_many(self, iteration, steps, *, group_key, override=False, on_reserve=None):
        """Reserve a footprint and any orchestration claim in the same writer.

        ``on_reserve(conn)`` is an internal transaction participant. It runs
        only after admission (also on a prior successful receipt), must be
        idempotent, and must not dispatch work or open another writer. Its
        exception rolls back both the claim and this group's new reservations.
        """
        _text(group_key, 'budget group key', limit=200)
        if type(override) is not bool:
            raise ValueError('Budget override must be an explicit boolean')
        policy = validate_policy((iteration.get('protocol') or {}).get('budget'))
        if policy is None:
            return dict(status='unbudgeted', reservations=[], summary=None)
        if type(steps) is not list:
            raise ValueError('A budget footprint list is required')
        steps = deepcopy(steps)
        identities = set()
        for step in steps:
            if not isinstance(step, dict):
                raise ValueError('Invalid budget step')
            _text(step.get('candidate_id'), 'budget candidate', limit=200)
            _text(step.get('request_key'), 'budget request key', limit=200)
            if type(step.get('case_count')) is not int or step['case_count'] <= 0:
                raise ValueError('A positive selected case count is required')
            identity = (step['candidate_id'], step['request_key'])
            if identity in identities:
                raise ValueError('Duplicate attempt in budget footprint')
            identities.add(identity)
        payload = dict(iteration_id=iteration['id'], steps=steps)
        scope = 'iteration.budget.reserve:' + iteration['id']
        repo = self.repository
        self.reconcile(iteration['id'])
        with repo.connect(write=True) as conn:
            migrate(conn)
            saved = conn.execute('SELECT * FROM iteration_budget_policies WHERE iteration_id=?',
                                 (iteration['id'],)).fetchone()
            if saved is None:
                conn.execute('INSERT INTO iteration_budget_policies VALUES(?,?,?,?)',
                             (iteration['id'], iteration['task_id'], json.dumps(policy), now()))
            elif json.loads(saved['policy_json']) != policy or saved['task_id'] != iteration['task_id']:
                raise Conflict('Iteration budget policy changed after freezing')
            prior = repo._prior(conn, scope, group_key, payload)
            if prior is not None:
                if on_reserve is not None:
                    on_reserve(conn)
                return prior
            per_case, source = self._estimate(conn, iteration)
            retries = case_retries(iteration)
            reservations, missing = [], []
            for step in steps:
                saved = conn.execute('SELECT * FROM iteration_budget_reservations WHERE iteration_id=? '
                                     'AND candidate_id=? AND request_key=?',
                                     (iteration['id'], step['candidate_id'], step['request_key'])).fetchone()
                if saved is not None:
                    # Scope metadata is orchestration-owned; compare its whole frozen step.
                    if json.loads(saved['step_json']) != step:
                        raise Conflict('Budget attempt footprint changed')
                    reservations.append(_reservation(saved))
                else:
                    cost = per_case * step['case_count'] * (1 + retries)
                    if not math.isfinite(cost):
                        raise ValueError('Budget estimate overflow')
                    missing.append((step, cost))
            summary = self._summary(conn, iteration['id'])
            reasons = exceeded_caps(policy, summary, [step for step, _ in missing], per_case, retries)
            if reasons and not override and (missing or not steps):
                # Pending actions may be retried after settlement or trusted human
                # confirmation. Only successful reservations acquire a receipt.
                return dict(status='budget_exceeded', reasons=reasons, reservations=[], summary=summary)
            if on_reserve is not None:
                on_reserve(conn)
            for step, cost in missing:
                reservation = dict(id=uid(), iteration_id=iteration['id'],
                    candidate_id=step['candidate_id'], request_key=step['request_key'],
                    case_count=step['case_count'], step_json=json.dumps(step, sort_keys=True),
                    reserved_cny=cost, estimate_source=source, execution_id=None,
                    state='reserved', actual_cny=None, observed_cny=None, charged_cny=cost,
                    cost_source=None, created_at=now(), settled_at=None,
                    retry_case_count=step['case_count'] * retries)
                conn.execute('INSERT INTO iteration_budget_reservations VALUES('
                             ':id,:iteration_id,:candidate_id,:request_key,:case_count,:step_json,'
                             ':reserved_cny,:estimate_source,:execution_id,:state,:actual_cny,'
                             ':observed_cny,:charged_cny,:cost_source,:created_at,:settled_at,'
                             ':retry_case_count)', reservation)
                reservations.append(_reservation(reservation))
                repo._event(conn, iteration['task_id'], 'budget.reserved', reservation['id'],
                            dict(candidate_id=step['candidate_id'], request_key=step['request_key'],
                                 case_count=step['case_count'], reserved_cny=cost, override=override, estimate_source=source))
            result = dict(status='reserved', reservations=reservations,
                          summary=self._summary(conn, iteration['id']))
            repo._receipt(conn, scope, group_key, payload, result)
            return result

    def reserved_step(self, iteration_id, candidate_id, request_key, fallback):
        """Reuse trusted orchestration metadata; callers cannot grant exemptions."""
        with self.repository.connect() as conn:
            row = conn.execute('SELECT step_json FROM iteration_budget_reservations WHERE iteration_id=? '
                               'AND candidate_id=? AND request_key=?',
                               (iteration_id, candidate_id, request_key)).fetchone()
        if row is None:
            return fallback
        step = json.loads(row[0])
        if any(step.get(k) != v for k, v in fallback.items()):
            raise Conflict('Budget attempt footprint changed')
        return step

    def attach(self, iteration_id, candidate_id, request_key, execution_id):
        """Bind once, then recover terminal-before-attach races from durable history."""
        with self.repository.connect(write=True) as conn:
            row = conn.execute('SELECT * FROM iteration_budget_reservations WHERE iteration_id=? '
                               'AND candidate_id=? AND request_key=?',
                               (iteration_id, candidate_id, request_key)).fetchone()
            if row is None:
                raise Conflict('Execution requires a budget reservation')
            if row['execution_id'] is not None and row['execution_id'] != execution_id:
                raise Conflict('Budget reservation already belongs to another execution')
            other = conn.execute('SELECT id FROM iteration_budget_reservations WHERE execution_id=?',
                                 (execution_id,)).fetchone()
            if other is not None and other['id'] != row['id']:
                raise Conflict('Execution already belongs to another budget reservation')
            conn.execute('UPDATE iteration_budget_reservations SET execution_id=? WHERE id=?',
                         (execution_id, row['id']))
            terminal = conn.execute("SELECT content FROM history WHERE object_id=? AND kind IN "
                                    "('job.execution_finished','job.execution_cancelled') ORDER BY seq DESC LIMIT 1",
                                    (execution_id,)).fetchone()
        if terminal is not None:
            self.settle_execution(json.loads(terminal['content']))

    def settle_execution(self, record):
        if record.get('terminal') is not True:
            return
        # Legacy/unbudgeted Jobs never read runtime cost files here.
        with self.repository.connect() as conn:
            if not self._available(conn):
                return
            linked = conn.execute('SELECT state FROM iteration_budget_reservations WHERE execution_id=?',
                                  (record['execution_id'],)).fetchone()
            if linked is None or linked['state'] == 'settled':
                return
        observation = execution_cost(record)
        # Count retried cases for ledger accounting (visible, not hidden).
        retried = _count_retried_cases(record)
        with self.repository.connect(write=True) as conn:
            migrate(conn)
            if not self._available(conn):
                return
            row = conn.execute('SELECT * FROM iteration_budget_reservations WHERE execution_id=?',
                               (record['execution_id'],)).fetchone()
            if row is None or row['state'] == 'settled':
                return
            # Cancellation publication can precede process death. Preserve the
            # reservation while physical calls may still be in flight.
            complete = (observation is not None and observation['complete']
                        and record.get('state') != 'cancelled')
            observed = observation['total_cny'] if observation else None
            if not complete and row['observed_cny'] is not None:
                observed = max(row['observed_cny'], observed or 0)
            charged = observed if complete else max(row['charged_cny'], row['reserved_cny'], observed or 0)
            # Complete: the re-asks that happened. Otherwise keep the reserved worst case.
            retry_count = retried if complete else row['retry_case_count']
            conn.execute('UPDATE iteration_budget_reservations SET state=?,actual_cny=?,observed_cny=?,'
                         'charged_cny=?,cost_source=?,settled_at=?,retry_case_count=? WHERE id=?',
                         ('settled' if complete else 'unknown', observed if complete else None,
                          observed, charged, observation['source'] if observation else None, now(),
                          retry_count, row['id']))
