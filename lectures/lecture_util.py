"""Small rendering helpers for the executable DeepEval lectures.

The lectures can run as ordinary Python programs. If edtrace is installed,
its text primitive can be plugged in later without changing lecture content.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    from edtrace import text as _edtrace_text
    _EDTRACE_ACTIVE = any(name.startswith("edtrace") for name in sys.modules)
except ImportError:
    _edtrace_text = None
    _EDTRACE_ACTIVE = False


def text(value: str) -> None:
    if _EDTRACE_ACTIVE and _edtrace_text:
        _edtrace_text(value)
    else:
        print(value)


def heading(value: str) -> None:
    text(f"## {value}")


def code(title: str, value: object) -> None:
    text(f"**{title}**\n\n```text\n{value!r}\n```")


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_jsonl(path: Path, limit: int | None = None) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
                if limit and len(rows) >= limit:
                    break
    return rows


def run(main) -> None:
    try:
        main()
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
