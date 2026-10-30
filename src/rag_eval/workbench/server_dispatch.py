"""Server-owned dispatch of agent execution requests and settlement of orphans.

Agent CLIs are short-lived and started with a minimal environment. While a
writable server owns a state root (it holds ``server-dispatch.lock``), a CLI
only reserves and confirms a budgeted run request; this worker launches it, so
executions inherit the server's credentials and its supervision. Executions
whose dispatcher exited anyway (no server at the time, or a crash) are settled
by periodic reconciliation, not only at the next restart.
"""
from __future__ import annotations

import fcntl
import logging
import os
from pathlib import Path
import threading
import time

from .action_requests import HANDOFF_NOTE, ActionRequestStore, auto_execute

_LOG = logging.getLogger(__name__)
LOCK = 'server-dispatch.lock'


def serving(root):
    """True while a workbench server dispatches for this state root."""
    try:
        fd = os.open(Path(root) / LOCK, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        return False
    with os.fdopen(fd) as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
    return False


class ServerDispatch:
    def __init__(self, service, executions, workspace_id, *, dispatching=True, poll_seconds=2,
                 reconcile_seconds=5, retry_seconds=60):
        self.service, self.executions, self.workspace_id = service, executions, workspace_id
        self.requests = ActionRequestStore(service.repository)
        self.dispatching = dispatching
        self.poll_seconds, self.reconcile_seconds, self.retry_seconds = poll_seconds, reconcile_seconds, retry_seconds
        self.closed = threading.Event()
        self.lock = None
        self.thread = None
        self.workers, self.retry_at = {}, {}

    def hold(self):
        """Own dispatch for this state root; another live server keeps it instead."""
        if self.lock is not None or not self.dispatching:
            return self.lock is not None
        fd = os.open(Path(self.service.repository.root) / LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        handle = os.fdopen(fd, 'r+')
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            return False
        self.lock = handle
        return True

    def start(self):
        if self.thread is not None and self.thread.is_alive():
            return
        self.hold()
        self.closed.clear()
        self.thread = threading.Thread(target=self._run, name='execution-dispatch', daemon=True)
        self.thread.start()

    def close(self):
        self.closed.set()
        if self.thread is not None:
            self.thread.join(timeout=5)
        if self.lock is not None:
            self.lock.close()
            self.lock = None

    def _run(self):
        reconciled = 0.0
        while not self.closed.is_set():
            try:
                self.dispatch()
            except Exception as exc:
                _LOG.error('Request dispatch failed (%s)', type(exc).__name__)
            if time.monotonic() - reconciled >= self.reconcile_seconds:
                reconciled = time.monotonic()
                try:
                    self.executions.reconcile()
                except Exception as exc:
                    _LOG.error('Execution reconciliation failed (%s)', type(exc).__name__)
            self.closed.wait(self.poll_seconds)

    def dispatch(self):
        if self.lock is None and not self.hold():
            return
        self.workers = {key: worker for key, worker in self.workers.items() if worker.is_alive()}
        for record in self.requests.list(workspace_id=self.workspace_id, status='confirmed'):
            rid = record['id']
            if (record['decided_by'] != 'budget' or record['decision_note'] != HANDOFF_NOTE
                    or rid in self.workers or self.retry_at.get(rid, 0) > time.monotonic()):
                continue
            worker = threading.Thread(target=self._execute, args=(record,), name='execution-dispatch-' + rid, daemon=True)
            self.workers[rid] = worker
            worker.start()

    def join(self, timeout=None):
        for worker in list(self.workers.values()):
            worker.join(timeout)

    def _execute(self, record):
        from .loop import transient
        try:
            outcome = auto_execute(self.service, record, workspace_id=self.workspace_id)
        except Exception as exc:
            # No exception text in logs: dispatch errors may include private paths.
            _LOG.error('Handed-off request %s dispatch failed (%s)', record['id'], type(exc).__name__)
            if transient(exc):
                self.retry_at[record['id']] = time.monotonic() + self.retry_seconds
            else:
                self.requests.finish(record['id'], error=type(exc).__name__ + ': dispatch failed')
            return
        if outcome['status'] == 'confirmed':
            # Refused (for example, missing credentials); retried after a pause.
            self.retry_at[record['id']] = time.monotonic() + self.retry_seconds
