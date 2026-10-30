"""Model-service credentials that executions inherit from the server process.

Partition processes read ``SYSTEM_*`` credentials from their inherited
environment. On the evaluation server, executions dispatched during a round
lost them while the server's start environment still had them, and no code
path that removes them is known. The server therefore snapshots the credential
names and values once at startup, gives every execution the snapshot back for
names missing at dispatch, and records names only (never values) when its own
environment changes, with the stacks that changed it.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import threading
import traceback

PREFIX = 'SYSTEM_'
_lock = threading.Lock()
_boot = None
_log_path = None
_seen = None
_hooked = False


def _names():
    return sorted(k for k in os.environ if k.startswith(PREFIX))


def _write(event, **fields):
    if _log_path is None:
        return
    line = json.dumps(dict(at=datetime.now(timezone.utc).isoformat(), event=event, pid=os.getpid(), **fields),
                      ensure_ascii=False)
    try:
        with open(_log_path, 'a', encoding='utf-8') as stream:
            stream.write(line + '\n')
    except OSError:
        pass


def _thread_stacks():
    names = {t.ident: t.name for t in threading.enumerate()}
    return {names.get(ident, str(ident)): traceback.format_stack(frame)[-25:]
            for ident, frame in sys._current_frames().items()}


def _audit(event, args):
    # Audit hooks must never raise: any exception here would surface in the
    # code that changed the environment.
    try:
        if event not in ('os.putenv', 'os.unsetenv') or not args:
            return
        key = args[0]
        key = key.decode('utf-8', 'replace') if isinstance(key, bytes) else str(key)
        if key.startswith(PREFIX):
            _write(event, key=key, thread=threading.current_thread().name,
                   stack=traceback.format_stack()[:-1][-25:])
    except Exception:
        pass


def install(log_path):
    """Snapshot credentials and start auditing; repeated calls keep the first snapshot."""
    global _boot, _log_path, _seen, _hooked
    with _lock:
        _log_path = Path(log_path)
        if _boot is not None:
            return
        _boot = {k: os.environ[k] for k in _names()}
        _seen = (tuple(sorted(_boot)), type(os.environ).__name__)
        if not _hooked:  # Audit hooks cannot be removed; one per process.
            sys.addaudithook(_audit)
            _hooked = True
    _write('installed', keys=sorted(_boot))


def check():
    """Record names and all thread stacks when the server's credential set changes."""
    global _seen
    if _boot is None:
        return
    current = (tuple(_names()), type(os.environ).__name__)
    with _lock:
        if current == _seen:
            return
        _seen = current
    _write('changed', keys=list(current[0]), environ_type=current[1],
           missing=sorted(set(_boot) - set(current[0])), stacks=_thread_stacks())


def child_env():
    """The current environment, with snapshot credentials restored; returns (env, restored names)."""
    env = dict(os.environ)
    restored = sorted(k for k, v in (_boot or {}).items() if not env.get(k))
    for key in restored:
        env[key] = _boot[key]
    return env, restored


def credential_names(model_config):
    """Every ``api_key_env`` name declared by a model-services TOML."""
    import tomllib
    found = set()
    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == 'api_key_env' and isinstance(item, str) and item:
                    found.add(item)
                else:
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    if model_config:
        walk(tomllib.loads(Path(model_config).read_text(encoding='utf-8')))
    return sorted(found)


def missing(model_config):
    """Declared credential names an execution dispatched from this process now would not inherit."""
    if not model_config or not Path(model_config).is_file():
        return []
    env, _ = child_env()
    return [k for k in credential_names(model_config) if not env.get(k)]


def presence(required=()):
    """Credential names in this process and its ancestry; written by execution children, never values."""
    chain, pid = [], os.getpid()
    while pid > 1 and len(chain) < 12:
        try:
            stat = Path(f'/proc/{pid}/stat').read_text()
            cmd = Path(f'/proc/{pid}/cmdline').read_bytes().replace(b'\0', b' ').decode('utf-8', 'replace')
        except OSError:
            break
        chain.append(dict(pid=pid, cmd=cmd[:160]))
        pid = int(stat.rsplit(')', 1)[1].split()[1])
    names = _names()
    return dict(pid=os.getpid(), ppid=os.getppid(), chain=chain, keys=names,
                required=sorted(required), missing=sorted(k for k in required if not os.environ.get(k)))
