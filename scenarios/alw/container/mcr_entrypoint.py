#!/usr/bin/env python3
"""Entry inside the task container: runs the harness's ``start`` in place of ``claude``.

The file name is the one the agent image's ``/usr/local/bin/claude`` dispatcher looks for
under ``/opt/harness/bin``. It belongs to the scenario's run command, not to the harness.

Harbor calls ``claude <arguments> --print`` with the task instruction on stdin. Then:

1. a call without ``--print`` / ``-p`` (``--version`` and the like) goes straight to the
   real program;
2. otherwise ``/opt/hb-harness/start`` gets the same arguments and the instruction on
   stdin, its stdout and stderr are ours (Harbor stores the stream), and its environment
   has HB_CLAUDE (how to launch Claude Code), HB_AGENT_SECONDS (its time), HB_HARNESS;
3. when the time in ``/opt/hb/fixed.json`` is up, ``start`` and everything it started in
   its session is stopped;
4. the workspace is copied to ``/logs/agent/hb/workspace`` and ``run.json`` is written
   next to it. The exit code is 0 whenever this was done.
"""
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

# the HB_*_DIR / HB_FIXED_FILE overrides are for tests; Harbor never sets them
FIXED = Path(os.environ.get("HB_FIXED_FILE", "/opt/hb/fixed.json"))
HARNESS = Path(os.environ.get("HB_HARNESS_DIR", "/opt/hb-harness"))
LOGS = Path(os.environ.get("HB_LOG_DIR", "/logs/agent/hb"))
WORKSPACE = Path(os.environ.get("HB_WORKSPACE_DIR", "/alb-workspace"))
GUARD = str(Path(__file__).resolve().parent / "claude")
GRACE = 10.0


def log(msg):
    sys.stderr.write(f"[hb] {msg}\n")
    sys.stderr.flush()


def session_pids(sid):
    out = []
    for p in os.listdir("/proc"):
        if not p.isdigit():
            continue
        try:
            stat = Path(f"/proc/{p}/stat").read_text()
            if int(stat[stat.rfind(")") + 2:].split()[3]) == sid:
                out.append(int(p))
        except (OSError, ValueError, IndexError):
            pass
    return out


def stop_session(proc, sig):
    for pid in session_pids(proc.pid) or [proc.pid]:
        try:
            os.kill(pid, sig)
        except OSError:
            pass


def copy_workspace(dst):
    errors = []

    def copy(src, d):
        try:
            shutil.copy2(src, d)
        except OSError as e:
            errors.append(f"{src}: {e}")

    try:
        shutil.copytree(WORKSPACE, dst, symlinks=True, copy_function=copy,
                        ignore_dangling_symlinks=True, dirs_exist_ok=True)
    except (shutil.Error, OSError) as e:
        errors.append(str(e)[:2000])
    return errors


def main():
    argv = sys.argv[1:]
    fixed = json.loads(FIXED.read_text()) if FIXED.is_file() else {}
    real = fixed.get("real_claude", "/opt/claude-code/bin/claude")
    start = HARNESS / "start"
    if not ({"-p", "--print"} & set(argv)) or not FIXED.is_file():
        os.execv(real, [real, *argv])
    prompt = sys.stdin.buffer.read()
    seconds = float(fixed["agent_seconds"])
    env = dict(os.environ, HB_CLAUDE=os.environ.get("HB_GUARD", GUARD), HB_AGENT_SECONDS=repr(seconds), HB_HARNESS=str(HARNESS))
    LOGS.mkdir(parents=True, exist_ok=True)
    record = {"argv": argv, "agent_seconds": seconds, "settings": fixed.get("settings", {}),
              "started_unix": round(time.time(), 3)}
    t0 = time.monotonic()
    try:
        proc = subprocess.Popen([str(start), *argv], stdin=subprocess.PIPE, env=env,
                                start_new_session=True)
    except OSError as e:
        log(f"cannot execute {start}: {e}")
        record.update(error=f"cannot execute start: {e}", returncode=None, timed_out=False)
        (LOGS / "run.json").write_text(json.dumps(record, indent=1))
        return 1

    def feed():
        try:
            proc.stdin.write(prompt)
            proc.stdin.close()
        except (BrokenPipeError, OSError, ValueError):
            pass

    threading.Thread(target=feed, daemon=True).start()
    timed_out = False
    try:
        rc = proc.wait(timeout=seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        log(f"time is up after {seconds:.0f} s; stopping the agent")
        stop_session(proc, signal.SIGTERM)
        try:
            rc = proc.wait(timeout=GRACE)
        except subprocess.TimeoutExpired:
            rc = None
    stop_session(proc, signal.SIGKILL)  # whatever is left of the agent's session
    if rc is None:
        rc = proc.wait()
    record.update(returncode=rc, timed_out=timed_out, seconds=round(time.monotonic() - t0, 3))
    record["workspace_copy_errors"] = copy_workspace(LOGS / "workspace")[:50]
    (LOGS / "run.json").write_text(json.dumps(record, indent=1))
    log(f"agent ended: returncode={rc} timed_out={timed_out} seconds={record['seconds']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
