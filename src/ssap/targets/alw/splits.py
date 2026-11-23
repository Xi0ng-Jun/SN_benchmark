"""Cluster-isolated dev / val / test split of the 40 ALW tasks and its invariant checks.

Clusters are derived from task metadata and netlists in the catalog (no task-number lists):

Hard clusters (35 at ALW commit d5236c8) merge tasks that share
  (H1) a non-ADB upstream git repository (``sources.json`` ``repository``);
  (H2) an internal non-PDK child ``.subckt`` name (a subcircuit defined in ``circuit.spice`` other
       than the top cell, compared case-insensitively);
  (H3) ``source == "adb"`` and an identical top ``.subckt`` name.
Conservative clusters (32) additionally merge
  (C1) tasks whose top ``.subckt`` name is identical across different sources;
  (C2) the ``source == "loadbench"`` batch (one dataset release).
The rationale for (H3) versus (C1): two ADB tasks with the same top cell are variants of one ADB
design family (same port contract, spec variants), whereas an equal textbook name across
sources (ADB ``folded_cascode_ota`` vs a team-provided ``folded_cascode_ota``) is only a naming
coincidence that is merged for caution. ``complementary_folded_cascode_ab_opamp`` and
``fd_two_stage_miller_opamp`` are different top names and therefore not merged.

The split deals the conservative clusters with a SHA-256-seeded stratified quota (see ``deal``).
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

SEED = "ssap-alw-v2-19"
SPLITS = ("dev", "val", "test")
SIZES = {"dev": 12, "val": 14, "test": 14}
BINNED_PER_SPLIT = {"dev": 3, "val": 3, "test": 3}
BLIND_MIN_DEV_CLUSTERS = 8
BLIND_COUNT = 3
BLIND_MAX_TASKS = 4
BATCH_SOURCES = ("loadbench",)
FORBIDDEN_IMPORTS = ("eda", "analoglayoutworld", "harbor")
# Code that is shipped into the task container or shown to proposing / implementing agents.
PROPOSER_VISIBLE_ROOTS = ("src/ssap/kernel", "src/ssap/checkers", "src/ssap/bank", "container/alw")
IMPORT_SCAN_ROOTS = ("src/ssap", "tests", "scripts")

# Display names of merged clusters, keyed by the evidence that merges them. Membership never
# depends on this table; an unknown evidence key gets a mechanically derived name.
CLUSTER_NAMES = {
    "child:ivd4a+nd2d1a": "cl_adb_cp_10mhz",
    "adb_top:sampling_feedback_ota": "cl_adb_sampling_fb_ota",
    "adb_top:two_stage_miller_opamp": "cl_adb_miller_opamp",
    "adb_top:gmr_degen": "cl_adb_gmr_degen",
    "cross_source_top:folded_cascode_ota": "cl_fc_ota_name",
    "batch:loadbench": "cl_loadbench_batch",
}


# --------------------------------------------------------------------------------------------
# Clusters
# --------------------------------------------------------------------------------------------

class _UnionFind:
    def __init__(self, items: Iterable[int]):
        self.parent = {i: i for i in items}

    def find(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)

    def groups(self) -> list[list[int]]:
        out: dict[int, list[int]] = {}
        for i in self.parent:
            out.setdefault(self.find(i), []).append(i)
        return [sorted(g) for g in sorted(out.values(), key=min)]


def _repo_slug(repo: str) -> str:
    base = repo.rstrip("/").split("/")[-1].lower()
    base = re.sub(r"^sky130_", "", base).replace("_ip__", "_")
    return "cl_" + re.sub(r"[^a-z0-9]+", "_", base).strip("_")


def _evidence(tasks: list[dict]) -> tuple[list[tuple[str, list[int]]], list[tuple[str, list[int]]]]:
    """``(hard_evidence, conservative_extra_evidence)`` as ``(key, task_numbers)`` with >= 2 tasks."""
    def collect(key_fn) -> dict[str, list[int]]:
        out: dict[str, list[int]] = {}
        for t in tasks:
            for key in key_fn(t):
                out.setdefault(key, []).append(int(t["number"]))
        return {k: sorted(v) for k, v in out.items() if len(v) >= 2}

    repo = collect(lambda t: [f"repo:{t['upstream_repo']}"] if t.get("upstream_repo") else [])
    child_single = collect(lambda t: [f"child:{c.lower()}" for c in t.get("child_subckts", [])])
    # Group child names that occur in exactly the same task set into one evidence item.
    by_members: dict[tuple[int, ...], list[str]] = {}
    for key, members in child_single.items():
        by_members.setdefault(tuple(members), []).append(key.split(":", 1)[1])
    child = {"child:" + "+".join(sorted(names)): list(m) for m, names in by_members.items()}
    adb_top = collect(lambda t: [f"adb_top:{t['top'].lower()}"] if t["source"] == "adb" else [])
    hard = sorted(repo.items()) + sorted(child.items()) + sorted(adb_top.items())

    tops: dict[str, list[dict]] = {}
    for t in tasks:
        tops.setdefault(t["top"].lower(), []).append(t)
    cross = {f"cross_source_top:{name}": sorted(int(t["number"]) for t in ts)
             for name, ts in tops.items() if len({t["source"] for t in ts}) >= 2}
    batch = collect(lambda t: [f"batch:{t['source']}"] if t["source"] in BATCH_SOURCES else [])
    return hard, sorted(cross.items()) + sorted(batch.items())


def _name(key: str) -> str:
    if key in CLUSTER_NAMES:
        return CLUSTER_NAMES[key]
    kind, value = key.split(":", 1)
    if kind == "repo":
        return _repo_slug(value)
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return {"child": f"cl_child_{slug}", "adb_top": f"cl_adb_{slug}",
            "cross_source_top": f"cl_{slug}_name", "batch": f"cl_{slug}_batch"}[kind]


def _build(tasks: list[dict], evidence: list[tuple[str, list[int]]], base: dict[str, dict] | None,
           singleton: str) -> dict[str, dict]:
    uf = _UnionFind(int(t["number"]) for t in tasks)
    if base:
        for c in base.values():
            for n in c["tasks"][1:]:
                uf.union(c["tasks"][0], n)
    for _, members in evidence:
        for n in members[1:]:
            uf.union(members[0], n)
    out: dict[str, dict] = {}
    for group in uf.groups():
        members = set(group)
        keys = [k for k, m in evidence if set(m) <= members]
        inherited = [cid for cid, c in (base or {}).items()
                     if set(c["tasks"]) == members and len(group) > 1]
        base_ev = [e for c in (base or {}).values() if set(c["tasks"]) <= members
                   for e in c["evidence"]]
        if inherited:                       # merged hard cluster, unchanged
            cid, ev = inherited[0], base_ev
        elif keys:
            cid, ev = _name(keys[0]), base_ev + keys
        else:
            cid, ev = f"{singleton}{group[0]:02d}", []
        if cid in out:
            raise ValueError(f"cluster name collision: {cid}")
        out[cid] = {"tasks": group, "evidence": list(dict.fromkeys(ev))}
    return dict(sorted(out.items()))


def derive_clusters(tasks: list[dict]) -> dict:
    """Hard and conservative cluster maps from catalog task records."""
    hard_ev, cons_ev = _evidence(tasks)
    hard = _build(tasks, hard_ev, None, "cl_hard_")
    cons = _build(tasks, cons_ev, hard, "cl_task_")
    # Singletons of the hard map that stay singletons are renamed cl_task_NN in the
    # conservative map; merged hard clusters keep their names.
    return {
        "schema_version": 1,
        "rules": {
            "hard": ["H1: shared non-ADB upstream git repository (sources.json repository)",
                     "H2: shared internal non-PDK child .subckt name (case-insensitive)",
                     "H3: source == 'adb' and identical top .subckt name"],
            "conservative": ["hard clusters, plus",
                             "C1: identical top .subckt name across different sources",
                             "C2: source == 'loadbench' batch (one dataset release)"],
            "singleton_names": {"hard": "cl_hard_NN", "conservative": "cl_task_NN"},
        },
        "hard": hard,
        "conservative": cons,
        "hard_of": {str(n): cid for cid, c in hard.items() for n in c["tasks"]},
        "conservative_of": {str(n): cid for cid, c in cons.items() for n in c["tasks"]},
        "counts": {"hard": len(hard), "conservative": len(cons)},
    }


# --------------------------------------------------------------------------------------------
# Split
# --------------------------------------------------------------------------------------------

def deal(clusters: dict[str, list[dict]], seed: str = SEED) -> dict[str, list[dict]]:
    """Stratified deterministic dealing of whole clusters into dev / val / test.

    Clusters with binned tasks go first (most binned first), then by cluster device-count tercile,
    then by ``sha256(seed:cluster_id)``. Each cluster goes to the split with the largest remaining
    binned share (binned clusters) or task share (others) among the splits that still fit it.
    """
    cl_list = []
    for cid, ts in sorted(clusters.items()):
        avg_dev = sum(t["devices"] for t in ts) / len(ts)
        n_b = sum(1 for t in ts if t["runtime_profile"] == "binned")
        cl_list.append((cid, ts, len(ts), n_b, avg_dev))
    cl_sorted_dev = sorted(cl_list, key=lambda x: (x[4], x[0]))
    terc = {c[0]: min(2, 3 * i // len(cl_sorted_dev)) for i, c in enumerate(cl_sorted_dev)}
    ord_try = sorted(
        cl_list,
        key=lambda x: (-x[3], terc[x[0]], hashlib.sha256(f"{seed}:{x[0]}".encode()).hexdigest()),
    )
    left_tasks = dict(SIZES)
    left_binned = dict(BINNED_PER_SPLIT)
    parts: dict[str, list[dict]] = {k: [] for k in SPLITS}
    for cid, ts, n_t, n_b, _ in ord_try:
        cands = [k for k in SPLITS if left_tasks[k] >= n_t and left_binned[k] >= n_b]
        if not cands:
            raise ValueError(f"cluster {cid} ({n_t} tasks, {n_b} binned) fits no split")
        if n_b > 0:
            pick = max(cands, key=lambda k: (left_binned[k] / 3.0, left_tasks[k] / SIZES[k]))
        else:
            pick = max(cands, key=lambda k: (left_tasks[k] / SIZES[k], -SPLITS.index(k)))
        parts[pick].extend(ts)
        left_tasks[pick] -= n_t
        left_binned[pick] -= n_b
    return parts


def blind_clusters(dev_cluster_sizes: dict[str, int], seed: str = SEED, count: int = BLIND_COUNT,
                   max_tasks: int = BLIND_MAX_TASKS) -> list[str]:
    """Internal blind dev clusters: walk the clusters in ``sha256(seed:blind:cluster)`` order and
    take each one that keeps the blind task total <= *max_tasks*, until *count* are taken
    (``docs/alw-mcr-instantiation.md`` §2.2: 3 clusters, about 3-4 tasks)."""
    ranked = sorted(dev_cluster_sizes,
                    key=lambda c: hashlib.sha256(f"{seed}:blind:{c}".encode()).hexdigest())
    picked: list[str] = []
    total = 0
    for c in ranked:
        if len(picked) == count:
            break
        if total + dev_cluster_sizes[c] <= max_tasks:
            picked.append(c)
            total += dev_cluster_sizes[c]
    if len(picked) != count:
        raise ValueError(f"cannot pick {count} blind clusters within {max_tasks} tasks")
    return sorted(picked)


def _median(xs: list[float]) -> float:
    s = sorted(xs)
    n = len(s)
    return float(s[n // 2]) if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def split_stats(tasks: list[dict], clusters: dict) -> dict:
    devs = [t["devices"] for t in tasks]
    nums = {str(t["number"]) for t in tasks}
    return {
        "tasks": len(tasks),
        "hard_clusters": len({clusters["hard_of"][n] for n in nums}),
        "conservative_clusters": len({clusters["conservative_of"][n] for n in nums}),
        "binned": sum(t["runtime_profile"] == "binned" for t in tasks),
        "continuous": sum(t["runtime_profile"] == "continuous" for t in tasks),
        "magic_netgen": sorted(t["number"] for t in tasks if t["lvs_backend"] == "magic_netgen"),
        "q_max_3": sum(t["q_max"] == 3.0 for t in tasks),
        "devices_median": _median(devs) if devs else None,
        "devices_range": [min(devs), max(devs)] if devs else None,
    }


def _dev_cluster_sizes(clusters: dict, dev: Iterable[int]) -> dict[str, int]:
    sizes: dict[str, int] = {}
    for n in dev:
        cid = clusters["conservative_of"][str(n)]
        sizes[cid] = sizes.get(cid, 0) + 1
    return dict(sorted(sizes.items()))


def make_split(catalog_data: dict, clusters: dict, seed: str = SEED) -> dict:
    tasks = {str(t["number"]): t for t in catalog_data["tasks"]}
    cons = {cid: [tasks[str(n)] for n in c["tasks"]] for cid, c in clusters["conservative"].items()}
    parts = deal(cons, seed)
    splits = {k: sorted(int(t["number"]) for t in v) for k, v in parts.items()}
    dev_sizes = _dev_cluster_sizes(clusters, splits["dev"])
    g_dev = len(dev_sizes)
    blind_mode = g_dev >= BLIND_MIN_DEV_CLUSTERS
    return {
        "schema_version": 1,
        "seed": seed,
        "unit": "conservative cluster",
        "sizes": dict(SIZES),
        "binned_per_split": dict(BINNED_PER_SPLIT),
        "splits": splits,
        "task_ids": {k: [tasks[str(n)]["task_id"] for n in v] for k, v in splits.items()},
        "cluster_of": {str(n): clusters["conservative_of"][str(n)] for n in sorted(map(int, tasks))},
        "stats": {k: split_stats([tasks[str(n)] for n in v], clusters) for k, v in splits.items()},
        "G_dev": g_dev,
        "blind_cluster_mode": blind_mode,
        "blind_clusters": blind_clusters(dev_sizes, seed) if blind_mode else [],
        "holdout_policy": {
            "dev": "search and Stage 0 (blind clusters: aggregate counts only)",
            "val": "sealed during search; one pure C1 paired winner comparison",
            "test": "sealed; evaluated once after validation, R >= 3 seeds",
        },
    }


# --------------------------------------------------------------------------------------------
# Invariants
# --------------------------------------------------------------------------------------------

@dataclass
class Check:
    rule: str
    name: str
    ok: bool
    detail: Any = None


@dataclass
class InvariantReport:
    checks: list[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)

    def add(self, rule: str, name: str, ok: bool, detail: Any = None) -> None:
        self.checks.append(Check(rule, name, bool(ok), detail))

    def failures(self) -> list[Check]:
        return [c for c in self.checks if not c.ok]

    def as_dict(self) -> dict:
        return {"ok": self.ok, "checks": [c.__dict__ for c in self.checks]}


def cross_split_overlap(split_data: dict, key_of: dict[int, Iterable[str]]) -> dict[str, list[str]]:
    """Keys that occur in more than one split: ``{key: [splits]}``."""
    where: dict[str, set[str]] = {}
    for name, nums in split_data["splits"].items():
        for n in nums:
            for key in key_of.get(int(n), ()):
                where.setdefault(key, set()).add(name)
    return {k: sorted(v) for k, v in sorted(where.items()) if len(v) > 1}


@dataclass(frozen=True)
class Violation:
    path: str
    line: int
    kind: str          # task_id_literal | tests_path | forbidden_import
    detail: str


def _py_violations(path: Path, rel: str, task_ids: frozenset[str], scan_literals: bool) -> list[Violation]:
    out: list[Violation] = []
    try:
        tree = ast.parse(path.read_text(), filename=str(path))
    except SyntaxError as exc:
        return [Violation(rel, exc.lineno or 0, "syntax_error", str(exc))]
    for node in ast.walk(tree):
        mods: list[str] = []
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            mods = [node.module]
        elif (isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant)
              and isinstance(node.args[0].value, str)
              and ((isinstance(node.func, ast.Name) and node.func.id == "__import__")
                   or (isinstance(node.func, ast.Attribute) and node.func.attr == "import_module"))):
            mods = [node.args[0].value]
        for m in mods:
            if m.split(".")[0] in FORBIDDEN_IMPORTS:
                out.append(Violation(rel, node.lineno, "forbidden_import", m))
        if scan_literals and isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.extend(_text_violations(node.value, rel, node.lineno, task_ids))
    return out


_TESTS_PATH = re.compile(r"(^|[/\\])tests[/\\]")


def _text_violations(text: str, rel: str, line: int, task_ids: frozenset[str]) -> list[Violation]:
    out = []
    for tid in sorted(task_ids):
        if tid in text:
            out.append(Violation(rel, line, "task_id_literal", tid))
    if _TESTS_PATH.search(text):
        out.append(Violation(rel, line, "tests_path", text[:80]))
    return out


def scan_code(root: str | Path, task_ids: Iterable[str],
              visible_roots: Iterable[str] = PROPOSER_VISIBLE_ROOTS,
              import_roots: Iterable[str] = IMPORT_SCAN_ROOTS) -> dict:
    """Static leak / import scan (R2, R3).

    * Under *visible_roots* (container-shipped and proposer-visible code): no string literal (or,
      for non-Python files, no line) may contain an ALW task id or a ``tests/`` path segment, and
      Python files may not import forbidden modules.
    * Under *import_roots*: no ``import eda`` / ``analoglayoutworld`` / ``harbor`` (including
      ``importlib.import_module`` and ``__import__`` with a literal name).
    """
    root = Path(root)
    ids = frozenset(task_ids)
    violations: list[Violation] = []
    scanned: dict[str, int] = {}
    visible = [root / r for r in visible_roots]
    seen: set[Path] = set()
    for base in visible:
        if not base.exists():
            scanned[str(base.relative_to(root))] = 0
            continue
        files = [p for p in sorted(base.rglob("*")) if p.is_file() and "__pycache__" not in p.parts]
        scanned[str(base.relative_to(root))] = len(files)
        for p in files:
            rel = str(p.relative_to(root))
            seen.add(p)
            if p.suffix == ".py":
                violations += _py_violations(p, rel, ids, scan_literals=True)
            else:
                try:
                    text = p.read_text()
                except (UnicodeDecodeError, OSError):
                    continue
                for i, line in enumerate(text.splitlines(), 1):
                    violations += _text_violations(line, rel, i, ids)
    for r in import_roots:
        base = root / r
        if not base.exists():
            continue
        files = [p for p in sorted(base.rglob("*.py")) if "__pycache__" not in p.parts and p not in seen]
        scanned[r] = scanned.get(r, 0) + len(files)
        for p in files:
            violations += _py_violations(p, str(p.relative_to(root)), ids, scan_literals=False)
    return {"scanned_files": scanned, "violations": [v.__dict__ for v in violations]}


def check_split_invariants(catalog_data: dict, clusters: dict, split_data: dict, *,
                           code_root: str | Path | None = None,
                           test_ledger: list | None = None,
                           seed: str = SEED) -> InvariantReport:
    """Check rules R1-R5 of ``docs/alw-mcr-instantiation.md`` §2.3 plus Disposition C1."""
    from .catalog import export_proposer_catalog

    rep = InvariantReport()
    tasks = {int(t["number"]): t for t in catalog_data["tasks"]}
    splits = {k: [int(n) for n in v] for k, v in split_data["splits"].items()}

    # ---- R1: coverage and zero cross-split overlap -----------------------------------------
    flat = [n for k in SPLITS for n in splits.get(k, [])]
    rep.add("R1", "40 tasks, each in exactly one split",
            len(tasks) == 40 and sorted(flat) == sorted(tasks) and len(flat) == len(set(flat)),
            {"tasks": len(tasks), "assigned": len(flat), "distinct": len(set(flat))})
    rep.add("R1", "split sizes 12/14/14", {k: len(v) for k, v in splits.items()} == SIZES,
            {k: len(v) for k, v in splits.items()})
    rederived = derive_clusters(list(tasks.values()))
    rep.add("R1", "clusters.json equals clusters re-derived from the catalog",
            rederived["hard"] == clusters["hard"] and rederived["conservative"] == clusters["conservative"])
    rep.add("R1", "35 hard / 32 conservative clusters",
            (len(clusters["hard"]), len(clusters["conservative"])) == (35, 32),
            {"hard": len(clusters["hard"]), "conservative": len(clusters["conservative"])})
    recomputed = make_split(catalog_data, clusters, seed)
    rep.add("R1", f"split.json equals the deterministic dealing for seed {seed!r}",
            split_data.get("seed") == seed and recomputed["splits"] == split_data["splits"],
            {"seed": split_data.get("seed")})
    keys = {
        "hard cluster": {n: [c] for n, c in ((int(k), v) for k, v in clusters["hard_of"].items())},
        "conservative cluster": {n: [c] for n, c in
                                 ((int(k), v) for k, v in clusters["conservative_of"].items())},
        "non-PDK .subckt name": {n: [s.lower() for s in t.get("child_subckts", [])] +
                                 [t["top"].lower()] for n, t in tasks.items()},
        "non-ADB upstream repository": {n: [t["upstream_repo"]] for n, t in tasks.items()
                                        if t.get("upstream_repo")},
        "non-ADB upstream dataset": {n: [t["upstream_dataset"]] for n, t in tasks.items()
                                     if t.get("upstream_dataset")},
    }
    for label, key_of in keys.items():
        over = cross_split_overlap({"splits": splits}, key_of)
        rep.add("R1", f"zero cross-split {label} overlap", not over, over or None)
    dev_clusters = sorted({clusters["conservative_of"][str(n)] for n in splits["dev"]})
    dev_hard = sorted({clusters["hard_of"][str(n)] for n in splits["dev"]})
    g_dev = len(dev_clusters)
    rep.add("R1", "G_dev >= 8 (blind-cluster mode)", g_dev >= BLIND_MIN_DEV_CLUSTERS and
            split_data.get("G_dev") == g_dev and split_data.get("blind_cluster_mode") is True,
            {"G_dev_conservative": g_dev, "G_dev_hard": len(dev_hard)})
    blind = list(split_data.get("blind_clusters", []))
    rep.add("R1", f"{BLIND_COUNT} of {g_dev} dev clusters frozen as internal blind clusters",
            len(blind) == BLIND_COUNT and set(blind) <= set(dev_clusters)
            and blind == blind_clusters(_dev_cluster_sizes(clusters, splits["dev"]), seed),
            {"blind": len(blind), "dev_clusters": g_dev,
             "blind_tasks": sum(clusters["conservative_of"][str(n)] in blind for n in splits["dev"])})

    # ---- R2 / R3: static code scan ------------------------------------------------------------
    if code_root is not None:
        ids = [t["task_id"] for t in tasks.values()]
        scan = scan_code(code_root, ids)
        v = scan["violations"]
        rep.add("R2", "no task_id literal in container-shipped / proposer-visible code",
                not [x for x in v if x["kind"] == "task_id_literal"],
                [x for x in v if x["kind"] == "task_id_literal"] or scan["scanned_files"])
        rep.add("R2", "no tests/ path access in container-shipped / proposer-visible code",
                not [x for x in v if x["kind"] == "tests_path"],
                [x for x in v if x["kind"] == "tests_path"] or None)
        rep.add("R3", "no import of eda / analoglayoutworld / harbor in src/ssap, tests, scripts",
                not [x for x in v if x["kind"] in ("forbidden_import", "syntax_error")],
                [x for x in v if x["kind"] in ("forbidden_import", "syntax_error")] or None)

    # ---- R3: proposer export ------------------------------------------------------------------
    export = export_proposer_catalog(catalog_data, split_data, blind)
    exported = {int(t["number"]) for t in export["tasks"]}
    sealed = set(splits["val"]) | set(splits["test"])
    blind_tasks = {n for n in splits["dev"] if clusters["conservative_of"][str(n)] in blind}
    text = json.dumps(export)
    leaked_ids = [tasks[n]["task_id"] for n in sealed | blind_tasks if tasks[n]["task_id"] in text]
    rep.add("R3", "proposer catalog = dev tasks outside blind clusters only",
            exported == set(splits["dev"]) - blind_tasks and not leaked_ids,
            {"exported": len(exported), "visible_dev_clusters": g_dev - len(blind),
             "leaked": len(leaked_ids)})

    # ---- R4: bank coverage unit ---------------------------------------------------------------
    rep.add("R4", "B_dev coverage is counted in independent dev clusters (<= G_dev)",
            g_dev == len(set(clusters["conservative_of"][str(n)] for n in splits["dev"])) <= 10,
            {"coverage_denominator": g_dev})

    # ---- R5: holdout policy -------------------------------------------------------------------
    policy = split_data.get("holdout_policy", {})
    rep.add("R5", "val / test sealed policy recorded", "sealed" in policy.get("val", "")
            and "once" in policy.get("test", ""), policy)
    if test_ledger is not None:
        rep.add("R5", "sealed test evaluated at most once", len(test_ledger) <= 1,
                {"test_evaluations": len(test_ledger)})
    return rep
