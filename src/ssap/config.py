"""Server configuration loading with ``${VAR}`` / ``${VAR:-default}`` interpolation.

Configs hold only environment-variable references for anything host-specific or secret; the
resolved values are used in memory and never written back.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Mapping

_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def interpolate(value: Any, env: Mapping[str, str] | None = None) -> Any:
    env = os.environ if env is None else env

    def sub(m: re.Match) -> str:
        name, default = m.group(1), m.group(2)
        v = env.get(name)
        return v if v else (default if default is not None else "")

    if isinstance(value, str):
        return _VAR.sub(sub, value)
    if isinstance(value, list):
        return [interpolate(v, env) for v in value]
    if isinstance(value, dict):
        return {k: interpolate(v, env) for k, v in value.items()}
    return value


def project_root(start: Path) -> Path:
    for p in [start, *start.parents]:
        if (p / "pyproject.toml").is_file():
            return p
    return Path.cwd()


def load_config(path: str | Path, env: Mapping[str, str] | None = None) -> dict:
    path = Path(path).resolve()
    raw = json.loads(path.read_text())
    cfg = interpolate(raw, env)
    cfg["_root"] = str(project_root(path.parent))
    return cfg


def resolve(cfg: dict, rel: str) -> Path:
    p = Path(rel)
    return p if p.is_absolute() else Path(cfg["_root"]) / p
