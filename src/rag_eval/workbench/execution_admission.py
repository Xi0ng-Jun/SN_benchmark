"""Explicit optimisation-run admission, independent of machine-wide work.

A dispatch returning is not completion. Keep its receipt charged until the
execution store reports a terminal state. No background waiter or polling
thread owns the run; callers cooperatively check their runtime while waiting.
"""
from contextlib import contextmanager
import hashlib
import threading
import time


class ExecutionAdmission:
    def __init__(self, limit, check, *, reports=None):
        self.limit = limit
        self.check = check
        self._lock = threading.Lock()
        self._dispatching = 0
        self._owned = {}  # execution id -> (execution store, task id)
        self._cancel_actor = None
        self.cancellation_report = reports if reports is not None else []

    def track(self, executions, task_id, record):
        with self._lock:
            self._owned[record['execution_id']] = (executions, task_id)
            actor = self._cancel_actor
        # Cancellation can race a blocking launch. A receipt published after
        # cancel() took its snapshot must still receive the physical request.
        if actor is not None:
            self._cancel_one(record['execution_id'], executions, task_id, actor)

    def _running(self):
        # Called under the admission lock. Store read failure must fail closed:
        # without a terminal receipt, freeing the slot could oversubscribe.
        for eid, (store, task) in list(self._owned.items()):
            if store.get(task, eid)['terminal']:
                del self._owned[eid]
        return len(self._owned)

    @contextmanager
    def dispatch(self):
        acquired = False
        try:
            while not acquired:
                self.check()
                with self._lock:
                    if self._running() + self._dispatching < self.limit:
                        self._dispatching += 1
                        acquired = True
                if not acquired:
                    time.sleep(.02)
            self.check()
            yield self.track
        finally:
            if acquired:
                with self._lock:
                    self._dispatching -= 1

    def cancel(self, *, actor):
        with self._lock:
            self._cancel_actor = actor
            owned = dict(self._owned)
        for execution_id, (store, task_id) in owned.items():
            self._cancel_one(execution_id, store, task_id, actor)
        return self.cancellation_report

    def _cancel_one(self, execution_id, store, task_id, actor):
        try:
            record = store.get(task_id, execution_id)
            if not record['terminal'] or record['state'] == 'cancelled':
                # Stable local receipt; replay also retries physical stop.
                key = 'optimizer-cancel:' + hashlib.sha256(execution_id.encode()).hexdigest()
                record = store.cancel(task_id, execution_id, actor=actor, request_key=key)
            outcome = dict(execution_id=execution_id, state=record['state'],
                           stop_requested=record['state'] == 'cancelled', stop_confirmed=False)
        except Exception as exc:
            outcome = dict(execution_id=execution_id, status='cancel_unavailable', error=type(exc).__name__)
        with self._lock:
            self.cancellation_report.append(outcome)
