"""Cross-process cap on concurrent model requests.

Partition processes run in parallel and SN schedules its own concurrency
inside each one, so the product of the two can exceed what the model service
accepts and turn into a 429 storm.  This gate wraps the same synchronous
transports as the budget guard: every outgoing request first takes one of N
slots, held until the response body is read. The gate changes only timing;
requests, retries and responses are untouched.

The N slots are one System V counting semaphore per (directory, N) shared by
every process of the server. A waiter blocks in the kernel (no polling) and
waiters are served in arrival order. Every take and give back uses
``SEM_UNDO``, so the kernel returns the slots of a process that dies, however
it dies. The semaphore key is kept in ``semaphore-<N>.json`` under the
directory; creating it is serialized by ``gate.lock``. A semaphore outlives
its processes (until reboot or ``remove_gates``); a reboot recreates it.
``gate_status`` (``scripts/model_gate_status.py``) shows how full it is now.
"""
from __future__ import annotations

from contextlib import contextmanager
import ctypes
import errno
import fcntl
import json
import os
from pathlib import Path
import secrets
import threading
import time

# Linux values (asm-generic, the same on every architecture).
IPC_CREAT, IPC_EXCL, IPC_RMID = 0o1000, 0o2000, 0
GETVAL, GETNCNT, SETVAL = 12, 14, 16
SEM_UNDO = 0x1000


class _SemBuf(ctypes.Structure):
    _fields_ = [('sem_num', ctypes.c_ushort), ('sem_op', ctypes.c_short), ('sem_flg', ctypes.c_short)]


class _TimeSpec(ctypes.Structure):
    _fields_ = [('tv_sec', ctypes.c_long), ('tv_nsec', ctypes.c_long)]


def _libc():
    libc = ctypes.CDLL(None, use_errno=True)
    try:
        libc.semget.argtypes = (ctypes.c_int, ctypes.c_int, ctypes.c_int)
        libc.semtimedop.argtypes = (ctypes.c_int, ctypes.POINTER(_SemBuf), ctypes.c_size_t,
                                    ctypes.POINTER(_TimeSpec))
    except AttributeError:
        raise OSError(errno.ENOSYS, 'The model gate needs System V semaphores (Linux)') from None
    return libc


def _check(result):
    if result == -1:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))
    return result


def _semctl(libc, semid, command, value=0):
    return _check(libc.semctl(semid, 0, command, ctypes.c_ulong(value)))


def _status(libc, semid, slots):
    return dict(slots=slots, in_flight=slots - _semctl(libc, semid, GETVAL), waiting=_semctl(libc, semid, GETNCNT))


def _recorded(directory):
    for record in sorted(Path(directory).glob('semaphore-*.json')):
        try:
            saved = json.loads(record.read_text(encoding='utf-8'))
            yield record, saved['key'], saved['slots']
        except (OSError, ValueError, KeyError, TypeError):
            continue


def gate_status(directory):
    """Server-wide state of every gate recorded under ``directory``; never creates one."""
    libc, result = _libc(), []
    for record, key, slots in _recorded(directory):
        semid = libc.semget(key, 1, 0o600)
        result.append(dict(_status(libc, semid, slots), record=record.name, state='live') if semid != -1
                      else dict(slots=slots, record=record.name, state='absent'))
    return result


def remove_gates(directory):
    """Delete every gate recorded under ``directory`` (tests, or retiring a server's gate)."""
    libc = _libc()
    for record, key, _ in _recorded(directory):
        semid = libc.semget(key, 1, 0o600)
        if semid != -1:
            try:
                _semctl(libc, semid, IPC_RMID)
            except OSError as exc:
                if exc.errno not in (errno.EIDRM, errno.EINVAL):
                    raise
        record.unlink(missing_ok=True)


class ModelGate:
    def __init__(self, directory, slots):
        if type(slots) is not int or not 1 <= slots <= 32767:
            raise ValueError('Model gate needs a positive slot count')
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.slots = slots
        self.waited_seconds, self.waited_max, self.requests = 0.0, 0.0, 0
        self._stats = threading.Lock()
        self._libc = _libc()
        self._record = self.directory / f'semaphore-{slots}.json'
        self._semid = self._open()

    def _open(self):
        """The semaphore named by the record; created (holding N slots) when missing."""
        with open(self.directory / 'gate.lock', 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                key = json.loads(self._record.read_text(encoding='utf-8'))['key']
            except (OSError, ValueError, KeyError, TypeError):
                key = None
            if key is not None:
                semid = self._libc.semget(key, 1, 0o600)
                if semid != -1:
                    return semid
                if ctypes.get_errno() != errno.ENOENT:
                    _check(-1)
                # Gone (reboot or removal): recreate under the same key if it is free.
            while True:
                key = key or secrets.randbelow(2**31 - 2) + 1
                semid = self._libc.semget(key, 1, IPC_CREAT | IPC_EXCL | 0o600)
                if semid != -1:
                    break
                if ctypes.get_errno() != errno.EEXIST:
                    _check(-1)
                key = None
            _semctl(self._libc, semid, SETVAL, self.slots)
            temporary = self._record.with_suffix('.tmp')
            temporary.write_text(json.dumps(dict(key=key, slots=self.slots)) + '\n', encoding='utf-8')
            temporary.replace(self._record)
            return semid

    def _op(self, semid, delta, timeout=None):
        operation = _SemBuf(0, delta, SEM_UNDO)
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            limit = None
            if deadline is not None:
                left = max(0.0, deadline - time.monotonic())
                limit = _TimeSpec(int(left), int((left % 1) * 1e9))
            if self._libc.semtimedop(semid, ctypes.byref(operation), 1,
                                     ctypes.byref(limit) if limit is not None else None) == 0:
                return
            code = ctypes.get_errno()
            if code == errno.EINTR:
                continue  # Python runs signal handlers (e.g. KeyboardInterrupt) between attempts
            if code == errno.EAGAIN:
                raise TimeoutError(f'No model gate slot within {timeout} s')
            raise OSError(code, os.strerror(code))

    def acquire(self, timeout=None):
        """Wait (in arrival order) for a slot; return the token ``release`` takes."""
        started = time.monotonic()
        try:
            semid = self._semid
            self._op(semid, -1, timeout)
        except OSError as exc:
            if exc.errno not in (errno.EIDRM, errno.EINVAL):
                raise
            # Removed while waiting: reopen (recreated with N slots) and wait there.
            self._semid = semid = self._open()
            self._op(semid, -1, timeout)
        waited = time.monotonic() - started
        with self._stats:
            self.waited_seconds += waited
            self.waited_max = max(self.waited_max, waited)
            self.requests += 1
        return semid

    def release(self, token):
        try:
            self._op(token, 1)
        except OSError as exc:
            if exc.errno not in (errno.EIDRM, errno.EINVAL):
                raise  # a removed semaphore has no slot to give back

    def status(self):
        """Server-wide, now: slots, requests holding one, and waiters queued."""
        return _status(self._libc, self._semid, self.slots)


@contextmanager
def install_model_gate(directory, slots, *, observer=None):
    gate = ModelGate(directory, slots)
    if observer is not None and observer.gate is None:
        observer.gate = gate
    with install_request_hooks(gate=gate, observer=observer):
        yield gate


@contextmanager
def install_request_hooks(*, gate=None, observer=None):
    """Wrap the sync transports with an optional gate and an optional speed observer.

    The observer (``speed_diagnostics.SpeedObserver``) times the gate wait and
    the service (until the body is read or a streamed response is closed).
    """
    import httpx
    import requests
    original_http = httpx.Client.send
    original_requests = requests.Session.send

    def enter(request_path):
        started = observer.now() if observer is not None else None
        phase = observer.current() if observer is not None else None
        fd = gate.acquire() if gate is not None else None
        acquired = observer.now() if observer is not None else None
        done = []

        def leave(outcome):
            if done:
                return
            done.append(True)
            if fd is not None:
                gate.release(fd)
            if observer is not None:
                observer.record(phase, request_path, started, acquired - started, observer.now() - acquired, outcome)
        return leave

    def http_send(client, request, **kwargs):
        leave = enter(request.url.path)
        try:
            response = original_http(client, request, **kwargs)
        except BaseException as exc:
            leave(type(exc).__name__)
            raise
        if not kwargs.get('stream', False):
            leave(response.status_code)
            return response
        # A streamed body is still being produced by the service: hold the
        # slot until the caller closes the response.
        close = response.close

        def close_and_release():
            try:
                close()
            finally:
                leave(response.status_code)
        response.close = close_and_release
        return response

    def request_send(session, request, **kwargs):
        leave = enter(request.path_url)
        outcome = 'error'
        try:
            response = original_requests(session, request, **kwargs)
            outcome = response.status_code
            return response
        except BaseException as exc:
            outcome = type(exc).__name__
            raise
        finally:
            leave(outcome)

    httpx.Client.send, requests.Session.send = http_send, request_send
    try:
        yield
    finally:
        httpx.Client.send, requests.Session.send = original_http, original_requests
