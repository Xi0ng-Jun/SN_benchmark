"""Cross-process cap on concurrent model requests.

Partition processes run in parallel and SN schedules its own concurrency
inside each one, so the product of the two can exceed what the model service
accepts and turn into a 429 storm.  This gate wraps the same synchronous
transports as the budget guard: every outgoing request first takes one of N
slots, held until the response body is read. The gate changes only timing;
requests, retries and responses are untouched.

One gate directory is one server-wide cap: a System V semaphore set of two
values, the free slots and the capacity N. A waiter blocks in the kernel (no
polling) and waiters are served in arrival order. Every take and give back
uses ``SEM_UNDO``, so the kernel returns the slots of a process that dies,
however it dies.

A process asking for a different N does not get a gate of its own (two caps
would add up). The capacity follows a new N only while the gate is idle (no
request holds or waits for a slot); both values then change by the same
difference in one atomic operation. Otherwise the process uses the current
capacity (``ModelGate.slots``; ``requested_slots`` keeps what it asked for).

The semaphore's key is recorded in ``semaphore.json``; creation and removal
are serialized by ``gate.lock``. A new semaphore always gets a fresh random
key and is recorded only once both values are set, so a crash while creating
leaves an unrecorded orphan, never a recorded gate with no slots. It outlives
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
IPC_CREAT, IPC_EXCL, IPC_NOWAIT, IPC_RMID = 0o1000, 0o2000, 0o4000, 0
GETVAL, GETNCNT, SETVAL = 12, 14, 16
SEM_UNDO = 0x1000
FREE, CAPACITY = 0, 1  # semaphore numbers in the set
RECORD = 'semaphore.json'


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


def _semctl(libc, semid, number, command, value=0):
    return _check(libc.semctl(semid, number, command, ctypes.c_ulong(value)))


def _status(libc, semid):
    capacity = _semctl(libc, semid, CAPACITY, GETVAL)
    return dict(slots=capacity, in_flight=capacity - _semctl(libc, semid, FREE, GETVAL),
                waiting=_semctl(libc, semid, FREE, GETNCNT))


@contextmanager
def _locked(directory):
    with open(Path(directory) / 'gate.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def _recorded_key(directory):
    try:
        key = json.loads((Path(directory) / RECORD).read_text(encoding='utf-8'))['key']
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return key if type(key) is int else None


def _existing(libc, key):
    """The recorded set, or None when it is gone (reboot, removal, or not ours)."""
    if key is None:
        return None
    semid = libc.semget(key, 2, 0o600)
    if semid != -1:
        return semid
    if ctypes.get_errno() not in (errno.ENOENT, errno.EINVAL):
        _check(-1)
    return None


def gate_status(directory):
    """Server-wide state of the gate recorded under ``directory``; never creates one."""
    libc = _libc()
    if not (Path(directory) / RECORD).exists():
        return dict(state='absent')
    semid = _existing(libc, _recorded_key(directory))
    return dict(_status(libc, semid), state='live') if semid is not None else dict(state='absent')


def remove_gates(directory):
    """Delete the gate recorded under ``directory`` (tests, or retiring a server's gate)."""
    directory = Path(directory)
    if not directory.is_dir():
        return
    libc = _libc()
    with _locked(directory):
        semid = _existing(libc, _recorded_key(directory))
        if semid is not None:
            try:
                _semctl(libc, semid, 0, IPC_RMID)
            except OSError as exc:
                if exc.errno not in (errno.EIDRM, errno.EINVAL):
                    raise
        (directory / RECORD).unlink(missing_ok=True)


class ModelGate:
    def __init__(self, directory, slots):
        if type(slots) is not int or not 1 <= slots <= 32767:
            raise ValueError('Model gate needs a positive slot count')
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.requested_slots = slots
        self.waited_seconds, self.waited_max, self.requests = 0.0, 0.0, 0
        self._stats = threading.Lock()
        self._libc = _libc()
        self._semid = self._open()

    def _create(self):
        while True:
            key = secrets.randbelow(2**31 - 2) + 1
            semid = self._libc.semget(key, 2, IPC_CREAT | IPC_EXCL | 0o600)
            if semid != -1:
                break
            if ctypes.get_errno() != errno.EEXIST:
                _check(-1)
        _semctl(self._libc, semid, CAPACITY, SETVAL, self.requested_slots)
        _semctl(self._libc, semid, FREE, SETVAL, self.requested_slots)
        temporary = self.directory / (RECORD + '.tmp')
        temporary.write_text(json.dumps(dict(key=key)) + '\n', encoding='utf-8')
        temporary.replace(self.directory / RECORD)
        return semid

    def _resize_if_idle(self, semid):
        status = _status(self._libc, semid)
        change = self.requested_slots - status['slots']
        if not change or status['in_flight'] or status['waiting']:
            return
        operations = (_SemBuf * 2)(_SemBuf(FREE, change, IPC_NOWAIT), _SemBuf(CAPACITY, change, IPC_NOWAIT))
        # Atomic for both values; fails harmlessly if a request took a slot meanwhile.
        if self._libc.semtimedop(semid, operations, 2, None) == -1 and ctypes.get_errno() != errno.EAGAIN:
            _check(-1)

    def _open(self):
        """The recorded gate (resized to the requested count while idle), created when missing."""
        with _locked(self.directory):
            semid = _existing(self._libc, _recorded_key(self.directory))
            if semid is None:
                semid = self._create()
            else:
                self._resize_if_idle(semid)
            self.slots = _semctl(self._libc, semid, CAPACITY, GETVAL)
            return semid

    def _op(self, semid, delta, timeout=None):
        operation = _SemBuf(FREE, delta, SEM_UNDO)
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
            # Removed while waiting: reopen (recreated if needed) and wait there.
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
        """Server-wide, now: capacity, requests holding a slot, and waiters queued."""
        return _status(self._libc, self._semid)


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
            # Ended before the slot is given back: the next holder starts after it.
            ended = observer.now() if observer is not None else None
            if fd is not None:
                gate.release(fd)
            if observer is not None:
                observer.record(phase, request_path, started, acquired, ended, outcome)
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
