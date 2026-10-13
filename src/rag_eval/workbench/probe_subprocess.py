"""Private stable handles for fixed, bounded, isolated substitute processes."""
from __future__ import annotations

import hashlib
import errno
from dataclasses import dataclass
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import threading
import time
import uuid

from .evaluation_jobs import JobRejected


MODES = ('normal', 'nonzero', 'wait_cancel', 'ignore_cancel')
TTL_SECONDS = 3
_HELPER = r'''
import json,os,select,signal,sys,time
mode,probe,owner,token,digest=sys.argv[1:]
if mode=='ignore_cancel': signal.signal(signal.SIGTERM,signal.SIG_IGN)
identity=dict(probe_id=probe,owner_id=owner,token=token,helper_hash=digest,pid=os.getpid())
with open('started.json','x') as stream: json.dump(identity,stream)
print(json.dumps(identity),flush=True)
deadline=time.monotonic()+(.15 if mode in ('normal','nonzero') else 3)
control=True
while time.monotonic()<deadline:
    if control and select.select([sys.stdin],[],[],.02)[0]:
        command=os.read(sys.stdin.fileno(),64)
        if command==b'cancel\n': os.kill(os.getpid(),signal.SIGTERM)
        if not command: control=False
    elif not control: time.sleep(.02)
sys.exit(7 if mode=='nonzero' else 0)
'''
HELPER_HASH = hashlib.sha256(_HELPER.encode()).hexdigest()


class ProbeStartFailed(Exception):
    """The fixed Popen call rejected creation; no helper handle was created."""


class ProbeStartUncertain(Exception):
    """A created helper did not supply a verified startup handshake."""


class ProbeAdapterRejected(JobRejected):
    """Invalid choices for the fixed substitute adapter."""


@dataclass
class _Handle:
    process: subprocess.Popen
    identity: dict
    # This cache accepts only status returned by our OS waitpid, never Popen's
    # returncode (which may be an ECHILD fallback). Loss is sticky.
    exit_code: int | None = None
    status_lost: bool = False
    cancel_sent: bool = False


class ProbeSubprocess:
    """One private owner; never controls a process selected by persisted PID.

    OS observations and control writes are serialized. Other host code may
    reap the child, so Popen's cached status is never evidence. Cancellation
    uses the original helper's private pipe, never a numeric PID signal. EOF
    after owner loss is ignored by the helper, which still has a fixed TTL.
    """

    def __init__(self):
        self._owner_id = uuid.uuid4().hex
        self.directory = Path(tempfile.mkdtemp(prefix='sn-workbench-probe-', dir='/tmp'))
        self.directory.chmod(0o700)
        self.lock = threading.RLock()
        self._handles = {}

    @property
    def owner_id(self):
        return self._owner_id

    @property
    def helper_hash(self):
        return HELPER_HASH

    def start(self, probe_id, mode):
        if type(probe_id) is not str or not probe_id or type(mode) is not str or mode not in MODES:
            raise ProbeAdapterRejected('Invalid fixed helper choice')
        if hashlib.sha256(_HELPER.encode()).hexdigest() != HELPER_HASH:
            raise ProbeAdapterRejected('Fixed helper bytes changed')
        with self.lock:
            if probe_id in self._handles:
                raise ProbeAdapterRejected('Helper was already created')
            token = uuid.uuid4().hex
            directory = self.directory / token
            directory.mkdir(mode=0o700)
            try:
                process = subprocess.Popen(
                    [sys.executable, '-I', '-S', '-B', '-c', _HELPER, mode,
                     probe_id, self.owner_id, token, self.helper_hash],
                    cwd=directory, env={'LANG': 'C.UTF-8'}, stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, close_fds=True,
                    bufsize=0)
            except OSError as exc:
                raise ProbeStartFailed('Fixed helper creation failed') from exc
            identity = dict(probe_id=probe_id, owner_id=self.owner_id, token=token,
                            helper_hash=self.helper_hash, pid=process.pid)
            self._handles[probe_id] = _Handle(process, identity)
            if not select.select([process.stdout], [], [], 1)[0]:
                raise ProbeStartUncertain('Fixed helper handshake unavailable')
            line = process.stdout.readline()
            try:
                handshake = json.loads(line)
            except json.JSONDecodeError:
                raise ProbeStartUncertain('Invalid fixed helper handshake') from None
            if handshake != identity:
                raise ProbeStartUncertain('Fixed helper identity mismatch')
            return dict(identity)

    def observe(self, probe_id, identity, *, cancel=False, wait=False):
        with self.lock:
            handle = self._handles.get(probe_id)
            if handle is None:
                return None
            process, actual = handle.process, handle.identity
            if identity is not None and identity != actual:
                return None
            deadline = time.monotonic() + TTL_SECONDS + 1
            while True:
                if handle.status_lost:
                    return None
                if handle.exit_code is not None:
                    return dict(identity=dict(actual), returncode=handle.exit_code)
                try:
                    pid, status = os.waitpid(process.pid, os.WNOHANG)
                except ChildProcessError as exc:
                    if exc.errno != errno.ECHILD:
                        raise
                    handle.status_lost = True
                    process.stdin.close()
                    return None
                if pid == process.pid and (os.WIFEXITED(status) or os.WIFSIGNALED(status)):
                    handle.exit_code = os.waitstatus_to_exitcode(status)
                    process.returncode = handle.exit_code
                    process.stdin.close()
                    continue
                if cancel and not handle.cancel_sent:
                    # A concurrent reaper may have run since waitpid. The pipe
                    # still targets only this helper; EPIPE cannot target a
                    # replacement process. Only the helper signals itself.
                    try:
                        os.write(process.stdin.fileno(), b'cancel\n')
                    except BrokenPipeError:
                        pass
                    handle.cancel_sent = True
                if not wait:
                    return dict(identity=dict(actual), returncode=None)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                time.sleep(min(.01, remaining))
