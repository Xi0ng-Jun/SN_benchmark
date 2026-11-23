"""Shared glue for the container-side MCR tools (``mcr_entrypoint.py``, ``mcr_hook.py``,
``mcr_check``, ``mcr_promote``).

The harness package is mounted read-only at ``/opt/harness/python`` (override:
``MCR_HARNESS_PYTHON``). Paths: workspace ``MCR_WORKSPACE`` (``/alb-workspace``), snapshot
export ``MCR_SNAPSHOT_DIR`` (``/logs/agent/mcr_snapshot``), configuration ``MCR_CONFIG``
(``/opt/harness/mcr_config.json``; key ``watermarks``), per-field overrides ``MCR_<FIELD>``
(e.g. ``MCR_T_SOFT_SEC``).
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

HARNESS_PYTHON = os.environ.get("MCR_HARNESS_PYTHON", "/opt/harness/python")
if HARNESS_PYTHON not in sys.path:
    sys.path.insert(0, HARNESS_PYTHON)

from ssap.checkers.alw import AlwContext, AlwSuite, alw_workspace  # noqa: E402
from ssap.kernel.watermarks import Watermarks  # noqa: E402

WORKSPACE = Path(os.environ.get("MCR_WORKSPACE", "/alb-workspace"))
SNAPSHOT_DIR = Path(os.environ.get("MCR_SNAPSHOT_DIR", "/logs/agent/mcr_snapshot"))
CONFIG_PATH = Path(os.environ.get("MCR_CONFIG", "/opt/harness/mcr_config.json"))


def load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text())
    except (OSError, ValueError):
        return {}


def watermarks() -> Watermarks:
    cfg = dict(load_config().get("watermarks") or {})
    for name in Watermarks.__dataclass_fields__:
        v = os.environ.get("MCR_" + name.upper())
        if v:
            cfg[name] = v
    return Watermarks.from_mapping(cfg)


def check_timeout_sec() -> float:
    v = os.environ.get("MCR_CHECK_TIMEOUT_SEC") or load_config().get("check_timeout_sec")
    return float(v) if v else 900.0


def workspace():
    return alw_workspace(WORKSPACE)


def suite() -> AlwSuite:
    return AlwSuite(AlwContext.for_workspace(WORKSPACE, timeout_sec=check_timeout_sec()))


def mcr_dir() -> Path:
    return WORKSPACE / ".mcr"


def clock_path() -> Path:
    return mcr_dir() / "run_clock.json"


def start_clock() -> float:
    p = clock_path()
    try:
        return float(json.loads(p.read_text())["t0_unix"])
    except (OSError, ValueError, KeyError):
        t0 = time.time()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"schema_version": 1, "t0_unix": t0}))
        return t0


def elapsed_sec() -> float | None:
    try:
        return time.time() - float(json.loads(clock_path().read_text())["t0_unix"])
    except (OSError, ValueError, KeyError):
        return None


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(obj, indent=1, sort_keys=True, default=str))
    os.replace(tmp, path)


def cert_view(cert: dict | None) -> dict | None:
    if not cert:
        return None
    keep = ("file_present", "m", "u", "raw_u", "eps", "check_timed_out", "advisory_flags",
            "artifact_sha256")
    out = {k: cert.get(k) for k in keep}
    d = cert.get("details") or {}
    out["notes"] = {k: d[k] for k in ("geometry_error", "contract_error", "lvs", "lvs_error",
                                      "fill", "fill_error", "morphology_error", "drc_error",
                                      "aspect_q", "run_dir") if k in d}
    return out
