"""Container EDA runtime for the ALW mirror checkers: tool paths and bounded subprocesses.

Paths come from the image manifest ``/opt/analog-layout/runtime.json`` (``paths``), then from the
exports of the T=0 frozen ``eda_env.sh``, then from the documented defaults in the task guide.
Nothing is read from the benchmark's private verifier inputs.
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

IMAGE_ROOT = Path("/opt/analog-layout")
DEFAULTS = {
    "klayout_bin": "klayout",
    "drc_deck": str(IMAGE_ROOT / "pdks/sky130/klayout/sky130A_mr.drc"),
    "lvs_deck": str(IMAGE_ROOT / "pdks/sky130/klayout/sky130.lvs"),
    "magic_bin": str(IMAGE_ROOT / "tools/magic/bin/magic"),
    "magic_rcfile": str(IMAGE_ROOT / "pdks/sky130/sky130A/libs.tech/magic/sky130A.magicrc"),
    "netgen_bin": str(IMAGE_ROOT / "tools/netgen/bin/netgen"),
    "netgen_setup": str(IMAGE_ROOT / "pdks/sky130/sky130A/libs.tech/netgen/sky130A_setup.tcl"),
}
_ENV_KEYS = {"ALB_MAGIC_BIN": "magic_bin", "ALB_MAGIC_RC": "magic_rcfile",
             "ALB_NETGEN_BIN": "netgen_bin", "ALB_NETGEN_SETUP": "netgen_setup"}
_EXPORT = re.compile(r"^\s*export\s+([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


class ToolTimeout(RuntimeError):
    """An EDA tool exceeded its budget: the check has no decision (never a false ``m = 0``)."""


class ToolUnavailable(RuntimeError):
    """A required EDA tool or deck is not installed."""


def eda_env_exports(eda_env_path: str | Path | None) -> dict[str, str]:
    """Literal ``export NAME=value`` lines of ``eda_env.sh`` (no shell evaluation)."""
    out: dict[str, str] = {}
    if eda_env_path is None:
        return out
    p = Path(eda_env_path)
    if not p.is_file():
        return out
    for line in p.read_text(errors="replace").splitlines():
        m = _EXPORT.match(line)
        if m:
            value = m.group(2).strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            out[m.group(1)] = value
    return out


def load_runtime(eda_env_path: str | Path | None = None,
                 manifest: str | Path | None = None) -> dict[str, str]:
    rt = dict(DEFAULTS)
    for key, name in _ENV_KEYS.items():
        v = eda_env_exports(eda_env_path).get(key)
        if v and "$" not in v:
            rt[name] = v
    mpath = Path(manifest) if manifest else IMAGE_ROOT / "runtime.json"
    try:
        data = json.loads(mpath.read_text())
        for k, v in (data.get("paths") or {}).items():
            if isinstance(v, str) and v:
                rt[k] = v
    except (OSError, ValueError):
        pass
    return rt


def tool_env(out_dir: Path) -> dict[str, str]:
    env = dict(os.environ)
    for key in ("PYTHONHOME", "PYTHONPATH", "LD_PRELOAD", "DISPLAY", "LD_LIBRARY_PATH",
                "QT_PLUGIN_PATH", "KLAYOUT_PYTHONHOME", "KLAYOUT_PYTHONPATH", "RUBYLIB",
                "TCL_LIBRARY", "TK_LIBRARY", "CAD_ROOT"):
        env.pop(key, None)
    home = out_dir / ".home"
    home.mkdir(parents=True, exist_ok=True)
    env.update(HOME=str(home), KLAYOUT_HOME=str(home / "klayout"), QT_QPA_PLATFORM="offscreen",
               TMPDIR=str(home))
    return env


@dataclass
class Execution:
    returncode: int | None
    seconds: float
    log_path: Path


def run(argv: list[str], *, cwd: Path, timeout: float, env: dict | None = None,
        log_name: str = "command.log") -> Execution:
    """Run in a new process group; on timeout kill the group and raise ``ToolTimeout``."""
    cwd.mkdir(parents=True, exist_ok=True)
    log_path = cwd / log_name
    started = time.monotonic()
    with log_path.open("w") as log:
        try:
            proc = subprocess.Popen([str(a) for a in argv], cwd=cwd, env=env or tool_env(cwd),
                                    stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                    start_new_session=True)
        except FileNotFoundError as exc:
            raise ToolUnavailable(f"tool not found: {argv[0]}") from exc
        try:
            rc = proc.wait(timeout=max(0.001, timeout))
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
            raise ToolTimeout(f"{Path(argv[0]).name} exceeded {timeout:.0f}s")
    return Execution(rc, time.monotonic() - started, log_path)


def require_file(path: str | Path, what: str) -> Path:
    p = Path(path)
    if not p.is_file():
        raise ToolUnavailable(f"missing {what}: {p}")
    return p
