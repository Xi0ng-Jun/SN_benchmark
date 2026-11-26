"""Append-only record of everything the outside program does (rule 3).

One JSON object per line in ``ledger.jsonl``. Each entry carries the hash of the
previous one and its own hash; ``head.json`` holds the count and the last hash, so
editing, deleting, reordering or cutting off entries is found by ``verify``.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


class LedgerError(Exception):
    """The ledger does not verify."""


def _canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _entry_hash(entry: dict) -> str:
    body = {k: v for k, v in entry.items() if k != "hash"}
    return hashlib.sha256(_canonical(body).encode()).hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Ledger:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "ledger.jsonl"
        self.head_path = self.root / "head.json"
        self.lock_path = self.root / "lock"

    @contextmanager
    def _locked(self):
        with open(self.lock_path, "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _head(self) -> dict:
        try:
            return json.loads(self.head_path.read_text())
        except FileNotFoundError:
            return {"count": 0, "hash": ""}

    def append(self, kind: str, **data) -> dict:
        """Add one entry and return it. ``data`` must be JSON-serialisable."""
        with self._locked():
            head = self._head()
            entry = {"seq": head["count"], "time": now(), "kind": kind, "data": data,
                     "prev": head["hash"]}
            entry["hash"] = _entry_hash(entry)
            line = _canonical(entry) + "\n"
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line)
                f.flush()
                os.fsync(f.fileno())
            tmp = self.head_path.with_suffix(".tmp")
            tmp.write_text(_canonical({"count": head["count"] + 1, "hash": entry["hash"]}))
            os.replace(tmp, self.head_path)
        return entry

    def entries(self, kind: str | None = None) -> list[dict]:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        out = [json.loads(line) for line in lines if line.strip()]
        return [e for e in out if kind is None or e["kind"] == kind]

    def last(self, kind: str) -> dict | None:
        found = self.entries(kind)
        return found[-1] if found else None

    def verify(self) -> list[str]:
        """Problems found; an empty list means the ledger is intact."""
        problems = []
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            lines = []
        prev, count = "", 0
        for i, line in enumerate(lines):
            try:
                e = json.loads(line)
            except ValueError:
                problems.append(f"line {i + 1}: not JSON")
                prev = None
                continue
            if not isinstance(e, dict):
                problems.append(f"line {i + 1}: not an entry")
                continue
            if e.get("seq") != i:
                problems.append(f"line {i + 1}: seq {e.get('seq')} != {i}")
            if prev is not None and e.get("prev") != prev:
                problems.append(f"line {i + 1}: does not follow the previous entry")
            if e.get("hash") != _entry_hash(e):
                problems.append(f"line {i + 1}: content does not match its hash")
            prev, count = e.get("hash"), i + 1
        head = self._head()
        if head != {"count": count, "hash": prev or ""}:
            problems.append(f"head says {head['count']} entries ending {head['hash'][:12]}, "
                            f"file has {count} ending {(prev or '')[:12]}")
        return problems

    def check(self) -> None:
        problems = self.verify()
        if problems:
            raise LedgerError(f"{self.path}: " + "; ".join(problems))
