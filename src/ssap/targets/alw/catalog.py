"""Read-only catalog of the 40 AnalogLayoutWorld (ALW) paper-v1 tasks.

Every value is read from files of an ALW checkout (``configs/paper-v1/{tasks,sources}.json``,
``src/analoglayoutworld/data/paper-v1.json`` and ``tasks/<task_id>/...``). Nothing of the
checkout is imported or executed.

Circuit scale is measured on ``tasks/<tid>/environment/task/circuit.spice`` (the agent-visible
copy; identical to ``tests/task/circuit.spice`` for all 40 tasks at commit d5236c8) with a
quote-aware hierarchical ``.subckt`` flattener:

* ``devices``: flattened primitive instances of the top subcircuit. A primitive is an ``M/R/C/Q/D``
  element or an ``X`` instance of a subcircuit not defined in the file (a PDK cell). Multiplicity
  parameters (``m``, ``mult``, ``nf``) are not applied.
* ``nets``: distinct nets attached to primitives, excluding the task substrate net (from
  ``tests/task/constraints.json``) and conventional supply / ground names (see ``SUPPLY``). This is
  an approximation; only the substrate net is designated by the benchmark.
"""
from __future__ import annotations

import json
import re
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

PRIMITIVE_LETTERS = "mrcqd"
PIN_COUNT = {"m": 4, "q": 3, "r": 2, "c": 2, "d": 2}
SUPPLY = re.compile(
    r"^(0|gnd[a-z0-9_]*|vss[a-z0-9_]*|vdd[a-z0-9_]*|vcc[a-z0-9_]*|avdd|avss|dvdd|dvss|"
    r"vpwr|vgnd|vpb|vnb|sub|vsub)$")
NETGEN_MARKER = re.compile(r"(?m)^\s*export\s+ALB_NETGEN_BIN=")
_TOKEN = re.compile(r"""'[^']*'|"[^"]*"|\{[^}]*\}|[^\s'"{]+""")


# --------------------------------------------------------------------------------------------
# SPICE parsing
# --------------------------------------------------------------------------------------------

def logical_lines(text: str) -> list[str]:
    """Comment-free logical lines with ``+`` continuations joined (case preserved).

    ``*`` full-line comments and `` $`` / `` ;`` inline comments are removed, but only outside
    quoted (``'...'``, ``"..."``) and braced (``{...}``) expressions, which may contain spaces.
    """
    out: list[str] = []
    for raw in text.splitlines():
        line = _strip_inline_comment(raw).strip()
        if not line or line.startswith("*"):
            continue
        if line.startswith("+"):
            if out:
                out[-1] += " " + line[1:].strip()
            continue
        out.append(line)
    return out


def _strip_inline_comment(line: str) -> str:
    depth_brace, quote = 0, ""
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = ""
        elif ch in "'\"":
            quote = ch
        elif ch == "{":
            depth_brace += 1
        elif ch == "}":
            depth_brace = max(0, depth_brace - 1)
        elif ch in "$;" and depth_brace == 0 and (i == 0 or line[i - 1].isspace()):
            return line[:i]
    return line


def tokenize(line: str) -> list[str]:
    """Whitespace tokens with quoted / braced expressions kept whole and ``a = b`` joined to
    ``a=b`` (so parameter assignments are always single tokens containing ``=``)."""
    raw = _TOKEN.findall(line)
    out: list[str] = []
    i = 0
    while i < len(raw):
        tok = raw[i]
        if tok == "=" and out and i + 1 < len(raw):
            out[-1] = out[-1] + "=" + raw[i + 1]
            i += 2
            continue
        if tok.endswith("=") and len(tok) > 1 and i + 1 < len(raw):
            out.append(tok + raw[i + 1])
            i += 2
            continue
        if tok.startswith("=") and len(tok) > 1 and out:
            out[-1] = out[-1] + tok
            i += 1
            continue
        if tok.startswith(("'", '"', "{")) and out and out[-1].endswith("="):
            out[-1] += tok
            i += 1
            continue
        out.append(tok)
        i += 1
    return out


@dataclass(frozen=True)
class Subckt:
    name: str
    ports: tuple[str, ...]
    body: tuple[tuple[str, ...], ...]      # tokenized element lines


def parse_subckts(text: str) -> dict[str, Subckt]:
    """``lower(name) -> Subckt``. Nested definitions are registered globally; their lines do not
    belong to the enclosing body."""
    defs: dict[str, Subckt] = {}
    stack: list[tuple[str, list[str], list[tuple[str, ...]]]] = []
    for line in logical_lines(text):
        tok = tokenize(line)
        head = tok[0].lower()
        if head == ".subckt":
            ports = [t for t in tok[2:] if "=" not in t and t.lower() != "params:"]
            stack.append((tok[1], ports, []))
        elif head == ".ends":
            if stack:
                name, ports, body = stack.pop()
                defs[name.lower()] = Subckt(name, tuple(ports), tuple(body))
        elif stack and not head.startswith("."):
            stack[-1][2].append(tuple(tok))
    return defs


def instantiated(defs: dict[str, Subckt]) -> set[str]:
    """Lower-case names of defined subcircuits that are instantiated by another definition."""
    used: set[str] = set()
    for sub in defs.values():
        for tok in sub.body:
            if tok[0][0].lower() == "x":
                pins = [t for t in tok[1:] if "=" not in t]
                if pins and pins[-1].lower() in defs:
                    used.add(pins[-1].lower())
    return used


def flatten(text: str, top: str) -> list[tuple[str, tuple[str, ...]]]:
    """Primitives of the flattened *top* subcircuit as ``(model, nets)`` (nets lower-cased;
    internal nets carry the hierarchical instance path as prefix)."""
    defs = parse_subckts(text)
    if top.lower() not in defs:
        raise ValueError(f"subcircuit {top!r} not found")
    out: list[tuple[str, tuple[str, ...]]] = []

    def walk(name: str, bind: dict[str, str], prefix: str, depth: int) -> None:
        if depth > 32:
            raise ValueError("subcircuit nesting too deep (recursive definition?)")
        for tok in defs[name].body:
            positional = [t for t in tok if "=" not in t]
            kind = positional[0][0].lower()
            if kind == "x":
                model, pins = positional[-1].lower(), positional[1:-1]
            elif kind in PRIMITIVE_LETTERS:
                model, pins = kind, positional[1:1 + PIN_COUNT[kind]]
            else:
                continue
            resolved = tuple(bind.get(p.lower(), p.lower() if p == "0" else prefix + p.lower())
                             for p in pins)
            if kind == "x" and model in defs:
                child = defs[model]
                walk(model, dict(zip((p.lower() for p in child.ports), resolved)),
                     f"{prefix}{positional[0].lower()}.", depth + 1)
            else:
                out.append((model, resolved))

    root = defs[top.lower()]
    walk(top.lower(), {p.lower(): p.lower() for p in root.ports}, "", 0)
    return out


def circuit_scale(text: str, top: str, substrate: str | None = None) -> tuple[int, int]:
    """``(devices, nets)`` of the flattened top subcircuit (see module docstring)."""
    prims = flatten(text, top)
    supply = {substrate.lower()} if substrate else set()
    nets = {n for _, pins in prims for n in pins}
    return len(prims), len({n for n in nets if n not in supply and not SUPPLY.match(n)})


def child_subckts(text: str, top: str) -> list[str]:
    """Names (original case) of non-PDK subcircuits defined in the file other than *top*."""
    return sorted(s.name for k, s in parse_subckts(text).items() if k != top.lower())


def root_subckts(text: str) -> list[str]:
    defs = parse_subckts(text)
    used = instantiated(defs)
    return sorted(s.name for k, s in defs.items() if k not in used)


# --------------------------------------------------------------------------------------------
# Catalog
# --------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class TaskInfo:
    number: int
    task_id: str
    title: str
    source: str                   # adb | internet | loadbench | local (sources.json source_group)
    source_label: str             # tasks.json `source` as written
    upstream_repo: str | None     # non-ADB upstream git repository (owner/name), if any
    upstream_dataset: str | None  # non-ADB upstream dataset (DOI), if any
    runtime_profile: str          # binned | continuous
    lvs_backend: str              # klayout | magic_netgen
    q_max: float
    u_target: float
    u_min: float
    top: str
    ports: list[str]
    substrate: str
    subckts: list[str]            # all subcircuits defined in circuit.spice
    child_subckts: list[str]      # defined subcircuits other than top (non-PDK children)
    devices: int
    nets: int
    agent_timeout_sec: float
    verifier_timeout_sec: float
    cpus: int
    memory_mb: int
    extra: dict[str, Any] = field(default_factory=dict)


def _upstream_repo(row: dict) -> str | None:
    repo = row.get("repository")
    if row.get("source_group") == "adb" or not isinstance(repo, str) or not repo:
        return None
    return re.sub(r"^https?://(www\.)?github\.com/", "", repo).rstrip("/")


def _upstream_dataset(row: dict) -> str | None:
    if row.get("source_group") == "adb":
        return None
    for key in ("dataset_doi", "dataset"):
        if isinstance(row.get(key), str) and row[key]:
            return row[key]
    return None


def load_catalog(repo: str | Path) -> list[TaskInfo]:
    repo = Path(repo)
    listed = json.loads((repo / "configs/paper-v1/tasks.json").read_text())["tasks"]
    sources = {r["task_id"]: r for r in json.loads(
        (repo / "configs/paper-v1/sources.json").read_text())["sources"]}
    params = json.loads(
        (repo / "src/analoglayoutworld/data/paper-v1.json").read_text())["task_parameters"]
    out: list[TaskInfo] = []
    for row in sorted(listed, key=lambda r: r["number"]):
        tid = row["task_id"]
        base = repo / "tasks" / tid
        toml = tomllib.loads((base / "task.toml").read_text())
        cons = json.loads((base / "tests/task/constraints.json").read_text())
        env_sh = (base / "environment/eda_env.sh").read_text()
        text = (base / "environment/task/circuit.spice").read_text()
        devices, nets = circuit_scale(text, cons["top"], cons.get("substrate"))
        src = sources.get(tid, {})
        backend = "magic_netgen" if NETGEN_MARKER.search(env_sh) else "klayout"
        if cons.get("lvs_backend", backend) != backend:
            raise ValueError(f"{tid}: eda_env.sh backend {backend} != constraints.json "
                             f"{cons.get('lvs_backend')}")
        p = params[tid]
        out.append(TaskInfo(
            number=int(row["number"]), task_id=tid, title=row.get("title", ""),
            source=str(src.get("source_group") or row.get("source", "")).lower(),
            source_label=row.get("source", ""), upstream_repo=_upstream_repo(src),
            upstream_dataset=_upstream_dataset(src),
            runtime_profile=row.get("runtime_profile", ""), lvs_backend=backend,
            q_max=float(p["q_max"]), u_target=float(p["u_target"]), u_min=float(p["u_min"]),
            top=cons["top"], ports=list(cons.get("ports", [])), substrate=cons.get("substrate", ""),
            subckts=[s.name for s in parse_subckts(text).values()],
            child_subckts=child_subckts(text, cons["top"]),
            devices=devices, nets=nets,
            agent_timeout_sec=float(toml["agent"]["timeout_sec"]),
            verifier_timeout_sec=float(toml["verifier"]["timeout_sec"]),
            cpus=int(toml["environment"]["cpus"]), memory_mb=int(toml["environment"]["memory_mb"])))
    return out


def repo_commit(repo: str | Path) -> str | None:
    """HEAD commit of a git checkout, read from its files (nothing is executed)."""
    git = Path(repo) / ".git"
    try:
        if git.is_file():           # worktree / submodule pointer
            git = (git.parent / git.read_text().split(":", 1)[1].strip()).resolve()
        head = (git / "HEAD").read_text().strip()
        if not head.startswith("ref:"):
            return head
        ref = head.split(None, 1)[1]
        for root in (git, git / "commondir"):
            if root.name == "commondir" and root.is_file():
                root = (git / root.read_text().strip()).resolve()
            if (root / ref).is_file():
                return (root / ref).read_text().strip()
            packed = root / "packed-refs"
            if packed.is_file():
                for line in packed.read_text().splitlines():
                    if line.endswith(" " + ref):
                        return line.split()[0]
    except OSError:
        return None
    return None


def catalog_document(tasks: Iterable[TaskInfo], commit: str | None) -> dict:
    return {
        "schema_version": 1,
        "benchmark": "AnalogLayoutWorld",
        "protocol": "paper-v1",
        "commit": commit,
        "netlist": "tasks/<task_id>/environment/task/circuit.spice",
        "counting": "devices = flattened primitive instances of the top .subckt (M/R/C/Q/D elements "
                    "and X instances of subcircuits not defined in the file), multiplicity not "
                    "applied; nets = distinct primitive nets without the substrate net and "
                    "conventional supply names (approximate)",
        "tasks": [asdict(t) for t in tasks],
    }


def read_catalog(path: str | Path) -> dict:
    return json.loads(Path(path).read_text())


def tasks_of(catalog_data: dict) -> list[dict]:
    return list(catalog_data["tasks"])


def export_proposer_catalog(catalog_data: dict, split_data: dict,
                            blind_clusters: Iterable[str] = ()) -> dict:
    """Catalog view for proposing / implementing agents (Disposition C1).

    Keeps only ``dev`` tasks outside the internal blind clusters. ``val`` / ``test`` task ids and
    parameters, and every blind-cluster task, are removed. The result carries no list of the
    removed ids, only aggregate counts.
    """
    blind = set(blind_clusters)
    dev = {int(n) for n in split_data["splits"]["dev"]}
    cluster_of = {int(k): v for k, v in split_data.get("cluster_of", {}).items()}
    unknown = blind - set(cluster_of.values())
    if unknown:
        raise ValueError(f"unknown blind clusters: {sorted(unknown)}")
    keep = [t for t in catalog_data["tasks"]
            if int(t["number"]) in dev and cluster_of.get(int(t["number"])) not in blind]
    out = {k: v for k, v in catalog_data.items() if k != "tasks"}
    out.update(
        view="proposer",
        tasks=keep,
        withheld={"dev_blind_tasks": len(dev) - len(keep),
                  "dev_blind_clusters": len(blind),
                  "non_dev_tasks": len(catalog_data["tasks"]) - len(dev)})
    return out
