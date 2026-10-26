"""Local Linux process identity and exclusive supervision across service restarts.

Neither a PID alone nor an argv match proves ownership. Boot identity and the
kernel start counter bind a saved launch to a process; pidfds keep subsequent
signals bound to that process even if the numeric PID is reused. Unsupported
or unreadable kernel facilities deliberately provide no recovery proof.
"""
from datetime import datetime
import fcntl
import json
import os
from pathlib import Path
import select
import signal
import stat
import subprocess
import threading
import time


def claim_supervisor(root, execution_id):
    directory = Path(root) / 'execution-supervisors'
    directory.mkdir(mode=0o700, exist_ok=True)
    if directory.is_symlink():
        raise OSError('Execution supervisor directory cannot be a symlink')
    # Never unlink these files: replacing their inode would split the lock.
    fd = os.open(directory / (execution_id + '.lock'),
                 os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    handle = os.fdopen(fd, 'r+')
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    return handle


def process_identity(pid, *, allow_exited=False):
    if type(pid) is not int or pid <= 0:
        return None
    try:
        fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        if fields[0] in ('Z', 'X') and not allow_exited:
            return None
        return dict(pid=pid, start_ticks=fields[19], pgrp=int(fields[2]), session=int(fields[3]),
                    boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip())
    except (OSError, ValueError, IndexError):
        return None


def valid_identity(identity):
    return (type(identity) is dict
            and set(identity) == {'pid', 'start_ticks', 'boot_id', 'pgrp', 'session'}
            and type(identity['pid']) is int and identity['pid'] > 0
            and type(identity['start_ticks']) is str and identity['start_ticks'].isdigit()
            and type(identity['boot_id']) is str and bool(identity['boot_id'])
            and type(identity['pgrp']) is int and identity['pgrp'] == identity['pid']
            and type(identity['session']) is int and identity['session'] == identity['pid'])


def completed_state(record, identity):
    """Accept only the bound run's regular, post-launch, explicit final state.

    This is local artifact provenance, not authentication against a malicious
    local writer. An exit status cannot be reconstructed from a state file.
    """
    if not valid_identity(identity):
        return None
    run = Path(record['run_dir'])
    path = run / 'state.json'
    try:
        if run.is_symlink() or path.is_symlink():
            return None
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd) as stream:
            metadata = os.fstat(stream.fileno())
            if (not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_mtime < datetime.fromisoformat(record['started_at']).timestamp()):
                return None
            state = json.load(stream)
    except (OSError, ValueError, TypeError):
        return None
    if (type(state) is not dict or state.get('terminal') is not True
            or state.get('phase') not in {'finished', 'finished_with_errors', 'failed', 'interrupted'}):
        return None
    return state


class RecoveredProcess:
    """A non-child process has no waitpid exit status; retain only liveness proof."""

    def __init__(self, identity, fd):
        self.identity = identity
        self.pid = identity['pid']
        self._fd = fd
        self._lock = threading.RLock()
        self._targets = []
        self._stopping = False

    @classmethod
    def attach(cls, identity):
        if (not valid_identity(identity) or not hasattr(os, 'pidfd_open')
                or not hasattr(signal, 'pidfd_send_signal')):
            return None
        try:
            fd = os.pidfd_open(identity['pid'])
        except (OSError, AttributeError):
            return None
        if process_identity(identity['pid']) != identity:
            os.close(fd)
            return None
        return cls(identity, fd)

    def poll(self):
        with self._lock:
            return 0 if self._fd is None or select.select([self._fd], [], [], 0)[0] else None

    def wait(self, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        while self.poll() is None:
            if deadline is not None and time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired([], timeout)
            time.sleep(.05)

    def signal_group(self, signum):
        # Pin every observed member while the original session leader is still
        # provably alive. We never send a signal using a recovered numeric PID
        # or killpg, including after the leader exits during the grace period.
        with self._lock:
            if self._stopping or self.poll() is not None or process_identity(self.pid) != self.identity:
                return False
            self._stopping = True
            for entry in Path('/proc').iterdir():
                if not entry.name.isdigit():
                    continue
                identity = process_identity(int(entry.name))
                if (identity is None or identity['session'] != self.pid
                        or identity['pgrp'] != self.pid
                        or int(identity['start_ticks']) < int(self.identity['start_ticks'])):
                    continue
                try:
                    fd = os.pidfd_open(identity['pid'])
                except OSError:
                    continue
                if (process_identity(identity['pid']) != identity
                        or process_identity(self.pid) != self.identity):
                    os.close(fd)
                    continue
                self._targets.append(fd)
            self._signal_targets(signum)
            return True

    def _signal_targets(self, signum):
        for fd in self._targets:
            try:
                signal.pidfd_send_signal(fd, signum)
            except ProcessLookupError:
                pass

    def stop_after_grace(self):
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if all(select.select([fd], [], [], 0)[0] for fd in self._targets):
                    return
                time.sleep(.05)
            self._signal_targets(signal.SIGKILL)
        finally:
            for fd in self._targets:
                os.close(fd)
            self._targets = []

    def close(self):
        with self._lock:
            if self._fd is not None:
                os.close(self._fd)
                self._fd = None
