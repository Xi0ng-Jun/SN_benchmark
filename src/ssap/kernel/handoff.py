"""Structured light-weight handoff record ``.mcr/handoff.json`` (``schema_version = 1``).

The agent keeps it current at milestones and at ``T_warn``; the stateless repair sub-invocation
reads it instead of any session memory. The serialized record is capped at ``MAX_BYTES``; longer
records are truncated deterministically (string and list limits are halved until the record
fits; dropped list items are counted in ``_truncated``).
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
MAX_BYTES = 4096
STATUSES = ("iterating", "ready_for_signoff", "blocked")
FIELDS = {
    "schema_version": int, "task_id": str, "top_cell": str, "status": str,
    "draft_gds_path": str, "draft_src_dir": str, "best_cert_summary": dict,
    "last_checks": dict, "known_blockers": list, "next_steps": list, "updated_at_utc": str,
}


def validate(record: Any) -> list[str]:
    """Problems with *record* (empty list = valid). Unknown keys are allowed."""
    errs: list[str] = []
    if not isinstance(record, dict):
        return ["handoff must be a JSON object"]
    if record.get("schema_version") != SCHEMA_VERSION:
        errs.append(f"schema_version must be {SCHEMA_VERSION}")
    for key, typ in FIELDS.items():
        if key in record and record[key] is not None and not isinstance(record[key], typ):
            errs.append(f"{key} must be {typ.__name__}")
    if "status" in record and record["status"] not in STATUSES:
        errs.append(f"status must be one of {STATUSES}")
    for key in ("known_blockers", "next_steps"):
        if isinstance(record.get(key), list) and not all(isinstance(x, str) for x in record[key]):
            errs.append(f"{key} must be a list of strings")
    if len(_dump(record).encode()) > MAX_BYTES:
        errs.append(f"handoff exceeds {MAX_BYTES} bytes")
    return errs


def _dump(record: Any) -> str:
    return json.dumps(record, indent=1, sort_keys=True, ensure_ascii=False, default=str)


def _shrink(value: Any, max_str: int, max_list: int, dropped: list[int]) -> Any:
    if isinstance(value, str):
        return value if len(value) <= max_str else value[:max(0, max_str - 3)] + "..."
    if isinstance(value, list):
        if len(value) > max_list:
            dropped[0] += len(value) - max_list
        return [_shrink(v, max_str, max_list, dropped) for v in value[:max_list]]
    if isinstance(value, dict):
        return {k: _shrink(v, max_str, max_list, dropped) for k, v in sorted(value.items())}
    return value


def truncate(record: dict, max_bytes: int = MAX_BYTES) -> dict:
    """Deterministic truncation of *record* to at most *max_bytes* serialized bytes."""
    if len(_dump(record).encode()) <= max_bytes:
        return record
    max_str, max_list = 512, 16
    while True:
        dropped = [0]
        out = _shrink(record, max_str, max_list, dropped)
        out["_truncated"] = {"max_str": max_str, "max_list": max_list, "dropped_items": dropped[0]}
        if len(_dump(out).encode()) <= max_bytes or (max_str <= 8 and max_list <= 1):
            break
        max_str, max_list = max(8, max_str // 2), max(1, max_list // 2)
    if len(_dump(out).encode()) > max_bytes:
        keep = {k: out[k] for k in ("schema_version", "status", "top_cell", "task_id") if k in out}
        out = {**keep, "_truncated": {"reason": "record too large", "max_bytes": max_bytes}}
    return out


def write(path: str | Path, record: dict) -> dict:
    record = {"schema_version": SCHEMA_VERSION, **record}
    record = truncate(record)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.tmp.{uuid.uuid4().hex}")
    tmp.write_text(_dump(record))
    os.replace(tmp, p)
    return record


def read(path: str | Path) -> dict | None:
    """The record, truncated to the cap, or ``None`` if missing / unparsable."""
    p = Path(path)
    try:
        if p.is_symlink() or not p.is_file():
            return None
        record = json.loads(p.read_text(errors="replace"))
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict):
        return None
    return truncate(record)
