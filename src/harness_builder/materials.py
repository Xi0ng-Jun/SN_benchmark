"""The materials folder and the question channel (rule 5).

Every addition becomes its own numbered, read-only subdirectory ``NNNN-<name>``
whose tree hash is on the ledger; nothing is ever changed or merged, so ``verify``
can find any edit, removal or unrecorded addition. Questions from the developer
agent are archived as they came; an answer is added as a new material.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from .harness_repo import make_read_only, tree_hash
from .ledger import Ledger, now


class MaterialsError(Exception):
    pass


def _slug(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-.")
    return s[:60] or "material"


class Materials:
    def __init__(self, root: str | Path, ledger: Ledger):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.ledger = ledger

    def _new_dir(self, name: str) -> Path:
        n = sum(1 for _ in self.root.iterdir()) + 1
        while True:
            d = self.root / f"{n:04d}-{_slug(name)}"
            try:
                d.mkdir()
                return d
            except FileExistsError:
                n += 1

    def _record(self, d: Path, note: str, source: str) -> dict:
        make_read_only(d)
        entry = {"material": d.name, "hash": tree_hash(d), "note": note, "source": source}
        self.ledger.append("material_added", **entry)
        return entry

    def add(self, src: str | Path, note: str = "", name: str | None = None) -> dict:
        """Add a copy of a file or directory as a new material."""
        src = Path(src)
        if not src.exists():
            raise MaterialsError(f"{src}: not found")
        d = self._new_dir(name or src.name)
        if src.is_dir():
            shutil.copytree(src, d, symlinks=True, dirs_exist_ok=True)
        else:
            shutil.copy2(src, d / src.name)
        return self._record(d, note, str(src.absolute()))

    def add_text(self, name: str, filename: str, text: str, note: str = "") -> dict:
        d = self._new_dir(name)
        (d / filename).write_text(text)
        return self._record(d, note, "text")

    def entries(self) -> list[dict]:
        return [e["data"] for e in self.ledger.entries("material_added")]

    def verify(self) -> list[str]:
        """Problems found; empty means every material is exactly as recorded."""
        problems = []
        recorded = {e["material"]: e["hash"] for e in self.entries()}
        for name, digest in recorded.items():
            d = self.root / name
            if not d.is_dir():
                problems.append(f"{name}: missing")
            elif tree_hash(d) != digest:
                problems.append(f"{name}: changed since it was added")
        for d in self.root.iterdir():
            if d.name not in recorded:
                problems.append(f"{d.name}: not on record")
        return problems


class Questions:
    def __init__(self, root: str | Path, ledger: Ledger, materials: Materials):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.ledger = ledger
        self.materials = materials

    def ask(self, question: str, why: str, assumption: str) -> dict:
        fields = {"question": question, "why": why, "assumption": assumption}
        for k, v in fields.items():
            if not isinstance(v, str) or not v.strip():
                raise MaterialsError(f"a question needs a non-empty {k!r}")
        n = sum(1 for _ in self.root.glob("q*.json")) + 1
        while True:
            qid = f"q{n:04d}"
            try:
                with open(self.root / f"{qid}.json", "x") as f:
                    q = {"id": qid, **fields, "asked": now()}
                    json.dump(q, f, indent=1)
                break
            except FileExistsError:
                n += 1
        self.ledger.append("question", **q)
        return q

    def answer(self, qid: str, text: str, by: str = "researcher") -> dict:
        path = self.root / f"{qid}.json"
        if not re.fullmatch(r"q\d+", qid) or not path.is_file():
            raise MaterialsError(f"no question {qid!r}")
        q = json.loads(path.read_text())
        body = (f"# Answer to {qid}\n\nQuestion: {q['question']}\n\n"
                f"Answered by: {by}\n\n{text.rstrip()}\n")
        m = self.materials.add_text(f"answer-{qid}", "answer.md", body,
                                    note=f"answer to {qid} by {by}")
        self.ledger.append("answer", question=qid, material=m["material"], by=by)
        return m

    def list(self) -> list[dict]:
        answers: dict[str, list[str]] = {}
        for e in self.ledger.entries("answer"):
            answers.setdefault(e["data"]["question"], []).append(e["data"]["material"])
        out = []
        for e in self.ledger.entries("question"):
            q = dict(e["data"])
            q["answers"] = answers.get(q["id"], [])
            out.append(q)
        return out
