"""Phase 3 proposer sandbox: clean ``H_base`` staging, visible-only evidence export, the
hint-free proposer task, the air-gapped proposer container and the Stage 0 validation screen.

Layout of one proposer workspace (mounted at ``/proposer_ws``)::

    harness_src/      read-only reference copy of the incumbent source (container/alw, src/ssap)
    candidate_src/    editable copy; the proposal is the diff candidate_src - incumbent
    evidence/         dev-only, visible-cluster evidence (mounted read-only)
    PROPOSER_TASK.md  formal MCR v2 rules (stdin of the proposer)
    proposal.json     written by the proposer

The incumbent used for diffs and screens is always the host copy outside the workspace, so an
edit of ``harness_src/`` inside the sandbox has no effect. Blind-cluster tasks are never read;
every exported JSON passes ``BlindBoundary.guard``.
"""
from __future__ import annotations

import ast
import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from ..bank.blind_clusters import BlindBoundary, BlindSet, PrivacyViolation, freeze_blind_clusters
from ..kernel import artifact_summary as asum

HBASE_MIRRORS = ("m_gds", "m_lvs", "m_shape", "m_fill", "m_morph", "m_drc")
HBASE_ADVISORIES = ("advisory_aspect_gt_3", "advisory_missing_src_py", "advisory_task_modified")
STAGED_PY = ("__init__.py", "config.py", "kernel", "checkers/__init__.py", "checkers/alw")
EXCLUDED_FROM_STAGE = ("checkers/alw/pex.py",)
TEXT_CAP = 64 * 1024
TAIL_CAP = 16 * 1024
GDS_CAP = 4 * 1024 * 1024
TEXT_SUFFIXES = (".txt", ".json", ".jsonl", ".spice", ".sp", ".cir", ".net", ".report", ".rpt",
                 ".log", ".sh", ".tcl", ".md", ".csv", ".lvs", ".drc", ".out", "")
BINARY_SKIP = (".gds", ".oas", ".gz", ".pyc")
PROPOSER_IMAGE_DEFAULT = "ssap-alw-agent"
PROPOSER_CONFIG_DIR = "/tmp/proposer_sessions"
REAL_CLAUDE = "/opt/claude-code/bin/claude"
_USER_PATH = re.compile(r"(/(?:home|localdata|Users|scratch)/)([^/\s\"'<>]+)")


# ---------------------------------------------------------------------------------------------
# scrubbing
# ---------------------------------------------------------------------------------------------

def user_scrub_text(text: str, secrets: Iterable[str] = ()) -> str:
    """Secrets, URL credentials and ``/home|/localdata/<user>`` path components -> placeholders."""
    text = asum.scrub_text(text, list(secrets))
    text = _USER_PATH.sub(lambda m: m.group(1) + "<USER>", text)
    user = os.environ.get("USER") or ""
    if len(user) >= 3:
        text = re.sub(rf"(?<![A-Za-z0-9_]){re.escape(user)}(?![A-Za-z0-9_])", "<USER>", text)
    return text


def user_scrub(obj: Any, secrets: Iterable[str] = ()) -> Any:
    secrets = list(secrets)
    if isinstance(obj, str):
        return user_scrub_text(obj, secrets)
    if isinstance(obj, list):
        return [user_scrub(v, secrets) for v in obj]
    if isinstance(obj, dict):
        return {k: user_scrub(v, secrets) for k, v in obj.items()}
    return obj


def _secrets() -> list[str]:
    return asum.literal_secrets_from_env() + [v for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
                                              if (v := os.environ.get(k))]


# ---------------------------------------------------------------------------------------------
# clean H_base staging
# ---------------------------------------------------------------------------------------------

def _copy_tree(src: Path, dst: Path, *, skip: Callable[[Path], bool] = lambda p: False) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    for p in sorted(src.iterdir()):
        if p.name == "__pycache__" or p.is_symlink() or skip(p):
            continue
        if p.is_dir():
            _copy_tree(p, dst / p.name, skip=skip)
        else:
            shutil.copy2(p, dst / p.name)


def strip_registry_spec(text: str, name: str) -> str:
    """Remove the ``CheckerSpec("<name>", ...)`` element (with its trailing comma) by AST span."""
    tree = ast.parse(text)
    lines = text.splitlines(keepends=True)
    spans = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "CheckerSpec"
                and node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == name):
            spans.append((node.lineno, node.end_lineno))
    if not spans:
        return text
    for start, end in sorted(spans, reverse=True):
        del lines[start - 1:end]
    out = "".join(lines)
    ast.parse(out)
    return out


def stage_clean_hbase_source(repo_root: str | Path, dst: str | Path) -> dict:
    """Copy ``container/alw`` and ``src/ssap/{__init__.py, config.py, kernel/, checkers/}`` of
    *repo_root* into *dst* (replaced), without ``checkers/alw/pex.py`` and without the
    ``m_pex`` spec in the staged ``registry.py``. The checkout itself is never modified."""
    root, dst = Path(repo_root), Path(dst)
    if not (root / "container" / "alw" / "settings.json").is_file():
        raise FileNotFoundError(f"{root}: not an ssap checkout")
    if dst.exists():
        shutil.rmtree(dst)
    _copy_tree(root / "container" / "alw", dst / "container" / "alw")
    pkg, out = root / "src" / "ssap", dst / "src" / "ssap"
    excluded = {(pkg / e).resolve() for e in EXCLUDED_FROM_STAGE}
    for rel in STAGED_PY:
        s = pkg / rel
        if s.is_dir():
            _copy_tree(s, out / rel, skip=lambda p: p.resolve() in excluded)
        else:
            (out / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(s, out / rel)
    reg = out / "checkers" / "alw" / "registry.py"
    reg.write_text(strip_registry_spec(reg.read_text(), "m_pex"))
    return {"dst": str(dst), "kernel_sha256": kernel_hashes(dst),
            "files": sum(1 for p in dst.rglob("*") if p.is_file())}


def kernel_hashes(harness_src: str | Path) -> dict[str, str]:
    """``{relative path: sha256}`` of ``src/ssap/kernel/`` (``__pycache__`` ignored)."""
    base = Path(harness_src) / "src" / "ssap" / "kernel"
    if not base.is_dir():
        return {}
    return {p.relative_to(base).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(base.rglob("*")) if p.is_file() and "__pycache__" not in p.parts}


def tree_hash(root: str | Path) -> str:
    h = hashlib.sha256()
    root = Path(root)
    for p in sorted(root.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts:
            h.update(p.relative_to(root).as_posix().encode() + b"\0")
            h.update(hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest()


REGISTRY_DUMP = r"""
import json, sys, dataclasses
sys.path.insert(0, sys.argv[1])
from ssap.checkers.alw.registry import alw_registry
reg = alw_registry()
out = []
for s in reg.specs:
    d = dataclasses.asdict(s)
    d["depends_on"] = list(d.get("depends_on") or ())
    out.append(d)
print(json.dumps(out))
"""


def registry_specs(harness_src: str | Path, timeout: float = 120.0) -> list[dict]:
    """``alw_registry()`` of *harness_src* as spec dicts, loaded in an isolated ``python -I``."""
    p = subprocess.run([sys.executable, "-I", "-c", REGISTRY_DUMP, str(Path(harness_src) / "src")],
                       capture_output=True, text=True, timeout=timeout,
                       env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")})
    if p.returncode != 0:
        raise RuntimeError(f"alw_registry() failed to load: {p.stderr.strip()[-600:]}")
    return json.loads(p.stdout)


# ---------------------------------------------------------------------------------------------
# visible-only evidence export
# ---------------------------------------------------------------------------------------------

def load_blind_set(state: str | Path, split: Mapping) -> BlindSet:
    """Blind set frozen at ``STATE/bank/blind_clusters.json`` (must equal the derivation) or
    derived from the split."""
    b = freeze_blind_clusters(split)
    p = Path(state) / "bank" / "blind_clusters.json"
    if p.is_file():
        rec = json.loads(p.read_text())
        frozen = rec.get("blind_clusters", rec) if isinstance(rec, dict) else rec
        if list(frozen) != list(b.blind):
            raise ValueError("bank/blind_clusters.json differs from the split derivation")
    return b


def dev_cluster_of(split: Mapping) -> dict[str, str]:
    ids = split["task_ids"]["dev"]
    return {t: split["cluster_of"][str(n)] for t, n in zip(ids, split["splits"]["dev"])}


def _clip_text(data: bytes, cap: int = TEXT_CAP) -> str:
    text = data.decode("utf-8", errors="replace")
    raw = text.encode()
    if len(raw) <= cap:
        return text
    half = cap // 2
    head = raw[:half].decode("utf-8", errors="ignore")
    tail = raw[-half:].decode("utf-8", errors="ignore")
    return f"{head}\n...[{len(raw) - 2 * half} bytes omitted]...\n{tail}"


class _Exporter:
    def __init__(self, out: Path, boundary: BlindBoundary, secrets: list[str]):
        self.out, self.boundary, self.secrets = out, boundary, secrets
        self.n_files = 0
        self.n_withheld = 0

    def json(self, rel: str, obj: Any) -> None:
        obj = user_scrub(obj, self.secrets)
        self.boundary.guard({"path": rel, "payload": obj})          # raises on a blind identifier
        p = self.out / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(obj, indent=1, sort_keys=True, default=str))
        self.n_files += 1

    def text(self, rel: str, data: bytes, cap: int = TEXT_CAP) -> None:
        text = user_scrub_text(_clip_text(data, cap), self.secrets)
        try:
            self.boundary.guard({"path": rel, "text": text})
        except PrivacyViolation:
            self.n_withheld += 1
            return
        p = self.out / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        self.n_files += 1

    def file(self, rel: str, src: Path) -> None:
        if not src.is_file() or src.is_symlink() or "verifier" in src.parts:
            return
        if src.suffix.lower() == ".json":
            try:
                self.json(rel, json.loads(src.read_text()))
                return
            except ValueError:
                pass
        self.text(rel, src.read_bytes())

    def binary(self, rel: str, src: Path) -> None:
        p = self.out / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, p)
        self.n_files += 1


def _run_summary(run: Mapping) -> dict:
    keep = ("task_id", "seed", "arm", "status", "Y_star", "C_star", "d_star", "m", "gate_triggered",
            "ratchet_promoted", "t_soft_stop", "wall_sec", "infra_error", "infra_category",
            "evaluation_error", "score_is_lower_bound")
    return {k: run.get(k) for k in keep if k in run}


def _tail(paths: Iterable[Path], cap: int = TAIL_CAP) -> bytes:
    chunks = []
    for p in paths:
        if p.is_file():
            with open(p, "rb") as fh:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                fh.seek(max(0, size - cap))
                chunks.append(f"===== {p.name} (last {min(cap, size)} of {size} bytes) =====\n".encode()
                              + fh.read())
    return b"\n".join(chunks)


def _export_run(ex: _Exporter, prefix: str, snap: Path, run: Mapping | None) -> None:
    ws = snap / "pre_repair" / "workspace"
    mcr = ws / ".mcr"
    if run is not None:
        ex.json(f"{prefix}/run_summary.json", _run_summary(run))
    ex.file(f"{prefix}/artifact_summary.json", snap / "artifact_summary.json")
    for rel in ("gate_report.json", "handoff.json", "best/cert.json", "task_prompt.txt",
                "trusted_task/eda_env.sh", "trusted_task/task/circuit.spice"):
        ex.file(f"{prefix}/.mcr/{rel}", mcr / rel)
    tb = mcr / "trusted_task" / "task" / "testbench"
    if tb.is_dir():
        for p in sorted(tb.rglob("*")):
            if p.is_file() and p.suffix.lower() not in BINARY_SKIP:
                ex.file(f"{prefix}/.mcr/trusted_task/task/testbench/{p.relative_to(tb).as_posix()}", p)
    ex.file(f"{prefix}/post_repair/best/cert.json", snap / "post_repair" / "best" / "cert.json")
    trace = snap / "post_repair" / "repair_trace.json"
    ex.file(f"{prefix}/post_repair/repair_trace.json", trace if trace.is_file() else snap / "repair_trace.json")
    checks = mcr / "checks"
    if checks.is_dir():
        runs = sorted((p for p in checks.iterdir() if p.is_dir()), key=lambda p: p.name)[-2:]
        for d in runs:
            for p in sorted(d.rglob("*")):
                if p.is_file() and not p.is_symlink() and p.suffix.lower() not in BINARY_SKIP \
                        and p.suffix.lower() in TEXT_SUFFIXES:
                    ex.file(f"{prefix}/.mcr/checks/{d.name}/{p.relative_to(d).as_posix()}", p)
    for rel in ("submission/layout.gds", "work/draft/layout.gds"):
        g = ws / rel
        if g.is_file() and g.stat().st_size <= GDS_CAP:
            ex.binary(f"{prefix}/sample_layout.gds", g)
            break
    trial = snap.parents[1] if len(snap.parents) >= 2 else snap
    tail = _tail([trial / "agent" / "claude-code.txt", snap / "phase_b_stream.jsonl"])
    if tail:
        ex.text(f"{prefix}/agent_tail.txt", tail, cap=2 * TAIL_CAP + 512)


def _visible_diagnostics(diag: Mapping, visible: set[str], blind_ids: set[str]) -> dict:
    tax = diag.get("taxonomy") or {}
    runs = [r for r in tax.get("runs") or [] if r.get("task_id") in visible]
    b_runs = [r for r in tax.get("runs") or [] if r.get("task_id") in blind_ids]
    gaps = [g for g in (diag.get("checkpoint_gap") or {}).get("runs") or [] if g.get("task_id") in visible]
    infra = [r for r in diag.get("infra_errors") or [] if r.get("task_id") in visible]
    cap = (diag.get("module_switches") or {}).get("capability_bound_tasks") or []
    counts: dict[str, int] = {}
    for r in runs:
        counts[r["class"]] = counts.get(r["class"], 0) + 1
    b_counts: dict[str, int] = {}
    for r in b_runs:
        b_counts[r["class"]] = b_counts.get(r["class"], 0) + 1
    return {"split": "dev", "visible_only": True,
            "taxonomy": {"counts": counts, "runs": runs},
            "checkpoint_gap": {"runs": gaps}, "infra_errors": infra,
            "capability_bound_tasks": sorted(t for t in cap if t in visible),
            "blind_aggregate": {"n_not_passed_runs": len(b_runs), "class_counts": b_counts,
                                "n_infra_errors": sum(1 for r in diag.get("infra_errors") or []
                                                      if r.get("task_id") in blind_ids),
                                "n_capability_bound_tasks": sum(1 for t in cap if t in blind_ids)}}


def _assert_visible(task_id: str, cluster_of: Mapping[str, str], blind: BlindSet) -> None:
    c = cluster_of.get(task_id)
    if c is None or blind.is_blind(c) or task_id not in blind.visible_task_ids:
        raise PrivacyViolation("refusing to read a non-visible trial directory")


def h_inc_archive_path(state: str | Path, round_id: int) -> Path:
    loop = Path(state) / "loop"
    p = loop / f"round_{round_id}" / "archive.json"
    return p if p.is_file() else loop / "round_1" / "archive.json"


def h_inc_runs_path(state: str | Path) -> Path:
    loop = Path(state) / "loop" / "h_inc_runs.jsonl"
    return loop if loop.is_file() else Path(state) / "runs" / "dev_H_base.jsonl"


def _read_jsonl(p: Path) -> list[dict]:
    if not p.is_file():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def export_visible_evidence(state: str | Path, out_dir: str | Path, *, catalog: Mapping,
                            split: Mapping, round_id: int = 1, blind: BlindSet | None = None,
                            archive: list[dict] | None = None, runs: list[dict] | None = None,
                            diagnostics: Mapping | None = None) -> dict:
    """Dev evidence of the visible clusters only (see module doc); returns a count summary."""
    state, out = Path(state), Path(out_dir)
    blind = blind or load_blind_set(state, split)
    boundary = BlindBoundary(blind)
    cluster_of = dev_cluster_of(split)
    visible = set(blind.visible_task_ids)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    ex = _Exporter(out, boundary, _secrets())
    if archive is None:
        ap = h_inc_archive_path(state, round_id)
        archive = json.loads(ap.read_text()) if ap.is_file() else []
    runs = runs if runs is not None else _read_jsonl(h_inc_runs_path(state))
    run_of = {(str(r.get("task_id")), int(r.get("seed", 0))): r for r in runs}
    exported, n_blind_skipped = [], 0
    for t in archive:
        tid, seed = str(t.get("task_id")), int(t.get("seed", 0))
        c = cluster_of.get(tid)
        if c is None or blind.is_blind(c):          # decided before any trial path is touched
            n_blind_skipped += c is not None
            continue
        _assert_visible(tid, cluster_of, blind)
        snap = Path(t["snapshot_dir"]) if t.get("snapshot_dir") else None
        prefix = f"runs/{tid}__s{seed}"
        if snap is None or not snap.is_dir():
            ex.json(f"{prefix}/run_summary.json", _run_summary(run_of.get((tid, seed), t)))
            exported.append(prefix)
            continue
        _export_run(ex, prefix, snap, run_of.get((tid, seed)))
        exported.append(prefix)
    if diagnostics is None:
        dp = state / "diagnostics" / "dev_diagnostics.json"
        diagnostics = json.loads(dp.read_text()) if dp.is_file() else None
    if diagnostics is not None:
        ex.json("diagnostics_summary.json",
                _visible_diagnostics(diagnostics, visible, set(blind.blind_task_ids)))
    from ..targets.alw.catalog import export_proposer_catalog
    ex.json("visible_catalog.json", export_proposer_catalog(dict(catalog), dict(split), blind.blind))
    history = []
    for g in range(1, int(round_id)):
        p = state / "loop" / f"round_{g}" / "round_report_redacted.json"
        if p.is_file():
            history.append(json.loads(p.read_text()))
    ex.json("round_history.json", {"rounds": history})
    summary = {"round": round_id, "n_visible_runs": len(exported),
               "n_visible_tasks": len({p.split("/")[1].rsplit("__s", 1)[0] for p in exported}),
               "blind_aggregate": {"n_runs_withheld": n_blind_skipped},
               "n_files": ex.n_files, "n_text_files_withheld": ex.n_withheld}
    ex.json("export_summary.json", summary)
    return summary


# ---------------------------------------------------------------------------------------------
# proposer task (formal rules only; no optimisation hints)
# ---------------------------------------------------------------------------------------------

FORBIDDEN_HINT_WORDS = ("report.lvsdb", "ports.json", "lyrdb", "comp.json", "postsim", "m_pex",
                        "pex.py")

PROPOSER_RULES = """\
# MCR v2 proposer task (round {round_id}, attempt {attempt})

You propose exactly one atomic change to the incumbent harness `{inc_version}`.

## Workspace
- `/proposer_ws/harness_src/`: read-only reference copy of the incumbent source.
- `/proposer_ws/candidate_src/`: editable copy. Your change is the diff between the two trees.
- `/proposer_ws/evidence/`: read-only evidence from `dev`-split executions of the incumbent on the
  clusters visible to you. Withheld clusters appear only as aggregate counts.
- Write `/proposer_ws/proposal.json` when done (schema below). No network access is available
  besides the model endpoint.

## Mutation classes (choose exactly one)
1. Class 1, mirror checker (`overwrite_authority = True`): add or change one concordance-verified
   checker whose result enters the certificate vector `m`. It must never de-certify a sample whose
   verifier sub-criterion label is 1 (directional screen on `S+_k`); declare `"bidirectional":
   true` only if it must also reject every label-0 sample.
   A Class-1 checker with `scope="terminal_only"` is evaluated only in the terminal gate; it must
   report its result as `"terminal_m": {{"<name>": 0 or 1}}` in the JSON printed by
   `mcr_check --gds` (certificate or top level) or by `mcr_check --gate`.
2. Class 2, advisory probe (`overwrite_authority = False`): may raise a notice or request the
   ratchet-protected terminal repair; it never changes `m`, never promotes, demotes or vetoes.
   Screen: zero flags on true solutions, at least one true flag on the withheld clusters.
3. Class 3, deterministic observation / output mapper (`overwrite_authority = False`): output of
   at most 2048 bytes; every root-cause error token of the input is preserved verbatim; `m` and `u`
   are unchanged. Supply `class3_entry` and `class3_self_test`.
4. Class 4, stateless Phase B repair refinement: the repair sub-invocation stays stateless
   (`-p`, `--no-session-persistence`, no `--resume`, no `--continue`) and must not alter Phase A.

## Evaluation paths
- Path 1 (terminal only: the agent trajectory up to the Phase A exit is unchanged): replayed on
  archived `dev` fork points; identical gate decisions cost nothing; divergent trials run three
  stateless repair seeds.
- Path 2 (anything executed during Phase A changes): paired fresh `dev` runs from step 0.
The path is classified objectively from the files you change; a declared path is not trusted.

## Invariants (a violation rejects the proposal before evaluation)
- `src/ssap/kernel/` is immutable (SHA-256 checked).
- The six incumbent Class-1 mirror checkers stay present, enabled and Class 1; at most one
  registry entry changes for Class 1/2 and none for Class 3/4.
- No task identifiers, no circuit or subcircuit names, no access to any `tests/` path, no import of
  benchmark, verifier or runner packages and no new third-party imports.
- No per-task layout recipes and no prompt tutorials; no forced rollback of submitted artifacts:
  canonical artifacts change only through the certified ratchet.
- Only files under `candidate_src/container/alw/` and `candidate_src/src/ssap/` may change.

## proposal.json
```json
{{"candidate_id": "<short-slug>", "mutation_class": 1, "checker": "<registry name or null>",
  "d_star_key": "<verifier sub-criterion for Class 1 or null>", "bidirectional": false,
  "evaluation_path": "path1|path2", "rationale": "<= 600 characters",
  "class3_entry": "<module:function for Class 3 or null>",
  "class3_self_test": [{{"raw": "<input text>", "root_cause_lines": ["<token>"]}}]}}
```
"""


def build_proposer_prompt(round_id: int, *, inc_version: str = "H_base", attempt: int = 1,
                          feedback: Mapping | None = None) -> str:
    """The proposer's stdin: formal rules plus (on a retry) the redacted Stage 0 feedback."""
    text = PROPOSER_RULES.format(round_id=int(round_id), attempt=int(attempt),
                                 inc_version=str(inc_version))
    if feedback:
        text += ("\n## Previous attempt (Stage 0 feedback, counts only)\n```json\n"
                 + json.dumps(feedback, indent=1, sort_keys=True, default=str) + "\n```\n")
    return text


# ---------------------------------------------------------------------------------------------
# proposer container
# ---------------------------------------------------------------------------------------------

def _proxy_from_config(cfg: Mapping):
    from ..targets.alw.harbor_runner import MeteringProxy
    return MeteringProxy.from_config(cfg)


def proposer_image(cfg: Mapping) -> str:
    return ((cfg.get("images") or {}).get("agent_image") or "").strip() or PROPOSER_IMAGE_DEFAULT


def _model(cfg: Mapping) -> str:
    return str((cfg.get("agent") or {}).get("model_name")
               or (cfg.get("model_homogeneity") or {}).get("pin_to") or "")


def append_offline_cost(path: str | Path | None, row: Mapping) -> None:
    if path is None:
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as fh:
        fh.write(json.dumps(dict(row), sort_keys=True, default=str) + "\n")


def _chown_back(image: str, mount: Path, dest: str) -> None:
    """Best effort: hand files written by a root container back to the invoking user."""
    if not hasattr(os, "getuid") or shutil.which("docker") is None:
        return
    try:
        subprocess.run(["docker", "run", "--rm", "--network", "none", "--entrypoint", "chown",
                        "-v", f"{mount.resolve()}:{dest}", image, "-R",
                        f"{os.getuid()}:{os.getgid()}", dest], capture_output=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired):
        pass


CONTAINER_PATH = ("/opt/analog-layout/python/bin:/opt/analog-layout/tools/bin:/opt/analog-layout/agent/bin:"
                  "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
CONTAINER_PYTHONPATH = "/proposer_ws/candidate_src/src:/opt/analog-layout/pdks/sky130/python"


def proposer_command(proposer_ws: Path, *, image: str, model: str, max_turns: int, name: str,
                     env_names: Iterable[str]) -> list[str]:
    ws = Path(proposer_ws).resolve()
    cmd = ["docker", "run", "--rm", "-i", "--name", name, "--cpus", "4", "--memory", "16g",
           "--add-host", "host.docker.internal:host-gateway",
           "-v", f"{ws}:/proposer_ws", "-v", f"{ws / 'evidence'}:/proposer_ws/evidence:ro",
           "-w", "/proposer_ws", "--entrypoint", REAL_CLAUDE,
           # non-secret container paths travel as K=V: the client env keeps the host PATH (docker)
           "-e", f"PATH={CONTAINER_PATH}", "-e", f"PYTHONPATH={CONTAINER_PYTHONPATH}"]
    for k in env_names:                       # values travel in the client env, never in argv
        if k not in ("PATH", "PYTHONPATH"):
            cmd += ["-e", k]
    cmd += [image, "--model", model, "--verbose", "--output-format=stream-json",
            "--permission-mode=bypassPermissions", "-p", "--no-session-persistence",
            "--max-turns", str(int(max_turns))]
    return cmd


def run_proposer_docker(proposer_ws: str | Path, cfg: Mapping, *, out_dir: str | Path,
                        max_turns: int = 80, timeout_sec: float = 2400.0,
                        offline_cost_path: str | Path | None = None,
                        cost_meta: Mapping | None = None) -> dict:
    """One proposer session (``PROPOSER_TASK.md`` on stdin) behind the metering proxy."""
    from ..targets.alw.agent_wrapper import LOOPBACK_NO_PROXY, assert_model_homogeneous, model_env
    ws, out = Path(proposer_ws), Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    task = (ws / "PROPOSER_TASK.md").read_text()
    model, image = _model(cfg), proposer_image(cfg)
    name = f"ssap-proposer-{os.getpid()}-{int(time.time() * 1000) % 10**9}"
    started = time.monotonic()
    rc, timed_out, stats = None, False, {}
    with _proxy_from_config(cfg) as proxy:
        token = proxy.new_trial(name)
        try:
            env = model_env(model, cfg)
            env.update({"ANTHROPIC_BASE_URL": f"http://host.docker.internal:{proxy.port}",
                        "ANTHROPIC_API_KEY": token, "IS_SANDBOX": "1",
                        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                        "CLAUDE_CONFIG_DIR": PROPOSER_CONFIG_DIR,
                        "NO_PROXY": LOOPBACK_NO_PROXY, "no_proxy": LOOPBACK_NO_PROXY})
            assert_model_homogeneous(env, model)
            cmd = proposer_command(ws, image=image, model=model, max_turns=max_turns, name=name,
                                   env_names=sorted(env))
            client_env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                          "HOME": os.environ.get("HOME", "/tmp"), **env}
            for k in ("DOCKER_HOST", "DOCKER_CONFIG"):
                if os.environ.get(k):
                    client_env[k] = os.environ[k]
            with open(out / "proposer_stream.jsonl", "wb") as so, \
                    open(out / "proposer_stderr.log", "wb") as se:
                try:
                    rc = subprocess.run(cmd, input=task.encode(), stdout=so, stderr=se,
                                        env=client_env, timeout=timeout_sec).returncode
                except subprocess.TimeoutExpired:
                    timed_out = True
                    subprocess.run(["docker", "kill", name], capture_output=True)
            (out / "proposer_command.json").write_text(json.dumps(
                user_scrub(cmd, [token] + _secrets()), indent=1))
        finally:
            proxy.revoke(token)
            stats = proxy.stats(name)
    _chown_back(image, ws, "/proposer_ws")
    wall = round(time.monotonic() - started, 3)
    row = {"kind": "proposer", **dict(cost_meta or {}), "wall_sec": wall, "returncode": rc,
           "timed_out": timed_out, "model": model,
           **{k: stats.get(k) for k in ("proxy_requests", "input_tokens", "output_tokens",
                                        "cached_tokens", "cache_creation_tokens",
                                        "unrecovered_gateway_error", "model_swap")}}
    append_offline_cost(offline_cost_path, row)
    res = row | {"proposal_present": (ws / "proposal.json").is_file()}
    (out / "proposer_result.json").write_text(json.dumps(res, indent=1, sort_keys=True, default=str))
    return res


# ---------------------------------------------------------------------------------------------
# Stage 0: diff, scope, static rules, objective evaluation path
# ---------------------------------------------------------------------------------------------

ALLOWED_PREFIXES = ("container/alw/", "src/ssap/__init__.py", "src/ssap/config.py",
                    "src/ssap/kernel/", "src/ssap/checkers/")
PHASE_A_FILES = frozenset({
    "container/alw/settings.json", "container/alw/bin/mcr_hook.py", "container/alw/bin/mcr_promote",
    "container/alw/bin/mcr_common.py", "src/ssap/__init__.py", "src/ssap/config.py",
    "src/ssap/checkers/__init__.py", "src/ssap/checkers/alw/__init__.py",
    "src/ssap/checkers/alw/gds_geom.py", "src/ssap/checkers/alw/lvs.py",
    "src/ssap/checkers/alw/area_fill.py", "src/ssap/checkers/alw/morphology.py",
    "src/ssap/checkers/alw/drc.py", "src/ssap/checkers/alw/spice.py",
    "src/ssap/checkers/alw/runtime.py"})
# files executed in Phase A only partly: changes confined to these functions keep Path 1 valid
TERMINAL_ONLY_PARTS = {
    "container/alw/bin/mcr_check": ("gate_report",),
    "container/alw/bin/mcr_entrypoint.py": ("repair_prompt",),
    "src/ssap/checkers/alw/registry.py": ("alw_registry", "gate_advisories"),
}
DROPPED_PHASE_B = ("--resume", "-r", "--session-id", "--continue", "-c", "--fork-session")


def _files(root: Path) -> dict[str, Path]:
    return {p.relative_to(root).as_posix(): p for p in sorted(root.rglob("*"))
            if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"}


def diff_trees(inc_src: str | Path, cand_src: str | Path) -> tuple[str, list[str]]:
    """``(unified diff, changed relative paths)``; added / removed files included."""
    a, b = _files(Path(inc_src)), _files(Path(cand_src))
    changed, chunks = [], []
    for rel in sorted(set(a) | set(b)):
        da = a[rel].read_bytes() if rel in a else b""
        db = b[rel].read_bytes() if rel in b else b""
        if da == db and rel in a and rel in b:
            continue
        changed.append(rel)
        try:
            ta, tb = da.decode(), db.decode()
        except UnicodeDecodeError:
            chunks.append(f"Binary files a/{rel} and b/{rel} differ\n")
            continue
        chunks.extend(difflib.unified_diff(ta.splitlines(keepends=True), tb.splitlines(keepends=True),
                                           fromfile=f"a/{rel}" if rel in a else "/dev/null",
                                           tofile=f"b/{rel}" if rel in b else "/dev/null"))
    return "".join(c if c.endswith("\n") else c + "\n" for c in chunks), changed


def _added_lines(patch: str) -> list[str]:
    return [l[1:] for l in patch.splitlines() if l.startswith("+") and not l.startswith("+++")]


def _non_py_added_lines(patch: str, cand_src: Path) -> list[str]:
    """Added lines of the diff that belong to non-Python files of the candidate."""
    out, keep = [], False
    for l in patch.splitlines():
        if l.startswith("+++ "):
            rel = l[6:] if l.startswith("+++ b/") else ""
            p = Path(cand_src) / rel
            keep = bool(rel) and p.is_file() and not _py_like(p)
        elif keep and l.startswith("+"):
            out.append(l[1:])
    return out


class _StripTerminal(ast.NodeTransformer):
    def __init__(self, names: Iterable[str]):
        self.names = set(names)

    def visit_FunctionDef(self, node):
        return None if node.name in self.names else self.generic_visit(node)

    def visit_If(self, node):                    # mcr_check: the ``if args.gate:`` branch
        t = node.test
        if isinstance(t, ast.Attribute) and t.attr == "gate":
            return None
        return self.generic_visit(node)


def _outside_dump(text: str, names: Iterable[str]) -> str:
    tree = _StripTerminal(names).visit(ast.parse(text))
    return ast.dump(tree, include_attributes=False)


def phase_a_changed_files(inc_src: Path, cand_src: Path, changed: Iterable[str]) -> list[str]:
    """Changed files whose change reaches Phase A execution (objective, never self-declared)."""
    out = []
    for rel in changed:
        if rel in PHASE_A_FILES or (rel.startswith("container/alw/") and rel not in TERMINAL_ONLY_PARTS):
            out.append(rel)
            continue
        if rel in TERMINAL_ONLY_PARTS:
            a, b = inc_src / rel, cand_src / rel
            if not (a.is_file() and b.is_file()):
                out.append(rel)
                continue
            try:
                if _outside_dump(a.read_text(), TERMINAL_ONLY_PARTS[rel]) != \
                        _outside_dump(b.read_text(), TERMINAL_ONLY_PARTS[rel]):
                    out.append(rel)
            except SyntaxError:
                out.append(rel)
    return out


def _mirror_sig(specs: list[dict]) -> list[tuple]:
    return [(s["name"], s.get("eps")) for s in specs if s.get("klass") == 1
            and s.get("enabled_in_hbase", True) and s.get("scope", "trajectory") == "trajectory"]


def registry_mutation_names(inc_specs: list[dict], cand_specs: list[dict]) -> list[str]:
    a, b = {s["name"]: s for s in inc_specs}, {s["name"]: s for s in cand_specs}
    return sorted(n for n in set(a) | set(b) if a.get(n) != b.get(n))


def classify_evaluation_path(inc_src: Path, cand_src: Path, changed: Iterable[str],
                             inc_specs: list[dict], cand_specs: list[dict]) -> tuple[str, list[str]]:
    phase_a = phase_a_changed_files(Path(inc_src), Path(cand_src), changed)
    if _mirror_sig(inc_specs) != _mirror_sig(cand_specs):
        phase_a.append("registry:trajectory_mirror")
    return ("path2" if phase_a else "path1"), phase_a


def _imports(text: str) -> set[str]:
    out = set()
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Import):
            out |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            out.add(node.module.split(".")[0])
    return out


def _py_like(p: Path) -> bool:
    if p.suffix == ".py":
        return True
    try:
        return p.suffix == "" and p.read_text(errors="ignore").startswith("#!/usr/bin/env python")
    except OSError:
        return False


GENERIC_CIRCUIT_WORDS = frozenset({"comparator", "bandgap"})


def _string_literals(text: str) -> set[str]:
    """String constants of a module, docstrings excluded."""
    tree = ast.parse(text)
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.body and isinstance(node.body[0], ast.Expr) \
                and isinstance(node.body[0].value, ast.Constant) and isinstance(node.body[0].value.value, str):
            docs.add(id(node.body[0].value))
    return {n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs}


def _circuit_names(catalog: Mapping) -> set[str]:
    names = set()
    for t in catalog["tasks"]:
        names |= {str(n).lower() for n in [t.get("top"), *(t.get("child_subckts") or [])] if n}
    return {n for n in names if len(n) >= 6 and n not in GENERIC_CIRCUIT_WORDS}


def _names_in(text: str, names: set[str]) -> bool:
    low = text.lower()
    return any(re.search(rf"(?<![a-z0-9_]){re.escape(n)}(?![a-z0-9_])", low) for n in names)


def static_scan(inc_src: Path, cand_src: Path, patch: str, catalog: Mapping,
                changed: Iterable[str] | None = None) -> dict:
    """R2 / R3 on the candidate tree plus name / new-import rules on the change. Circuit names
    are matched in new Python string literals (comments and docstrings ignored) and in added
    lines of non-Python files; generic single-word category names are not circuit names."""
    from ..targets.alw.splits import scan_code
    ids = [t["task_id"] for t in catalog["tasks"]]
    scan = scan_code(cand_src, ids, visible_roots=("src/ssap/kernel", "src/ssap/checkers", "container/alw"),
                     import_roots=("src/ssap",))
    viol = [dict(v) for v in scan["violations"]]
    names = _circuit_names(catalog)
    if changed is None:
        changed = [l[6:] for l in patch.splitlines() if l.startswith("+++ b/")]
    for rel in changed:
        p, q = cand_src / rel, inc_src / rel
        if not p.is_file():
            continue
        if _py_like(p):
            try:
                new = _string_literals(p.read_text()) - (_string_literals(q.read_text()) if q.is_file() else set())
            except (SyntaxError, UnicodeDecodeError):
                continue                      # reported below as syntax_error
            if any(_names_in(s, names) for s in new):
                viol.append({"kind": "circuit_name_literal", "path": rel,
                             "detail": "string literal names a circuit"})
    for line in _non_py_added_lines(patch, cand_src):
        if _names_in(line, names):
            viol.append({"kind": "circuit_name_literal", "detail": "added line names a circuit"})
    inc_imports: set[str] = set()
    for p in _files(inc_src).values():
        if _py_like(p):
            try:
                inc_imports |= _imports(p.read_text())
            except (SyntaxError, UnicodeDecodeError):
                pass
    allowed = set(sys.stdlib_module_names) | {"ssap", "mcr_common", "__future__"} | inc_imports
    for rel, p in _files(cand_src).items():
        if not _py_like(p):
            continue
        try:
            new = _imports(p.read_text()) - allowed
        except SyntaxError as exc:
            viol.append({"kind": "syntax_error", "path": rel, "detail": str(exc)[:200]})
            continue
        viol += [{"kind": "new_external_import", "path": rel, "detail": m} for m in sorted(new)]
    return {"ok": not viol, "violations": viol, "scanned_files": scan["scanned_files"]}


# ---------------------------------------------------------------------------------------------
# Stage 0: isolated probes of the candidate source and class-specific bank screens
# ---------------------------------------------------------------------------------------------

PHASE_B_PROBE = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
from ssap.checkers.alw.repair import phase_b_args
argv = ["--verbose", "--output-format=stream-json", "--resume", "abc", "--continue",
        "--session-id", "s1", "--fork-session", "-p"]
print(json.dumps(phase_b_args(argv, 80)))
"""
CLASS3_PROBE = r"""
import importlib, json, sys
sys.path.insert(0, sys.argv[1])
mod, fn = sys.argv[2].split(":", 1)
f = getattr(importlib.import_module(mod), fn)
print(json.dumps([f(x) for x in json.loads(sys.stdin.read())]))
"""


def _isolated(script: str, args: list[str], stdin: str | None = None, timeout: float = 120.0):
    p = subprocess.run([sys.executable, "-I", "-c", script, *args], input=stdin, capture_output=True,
                       text=True, timeout=timeout, env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")})
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip()[-400:])
    return json.loads(p.stdout)


def phase_b_stateless_ok(cand_src: Path) -> dict:
    try:
        out = _isolated(PHASE_B_PROBE, [str(Path(cand_src) / "src")])
    except (RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": type(exc).__name__}
    ok = (("-p" in out or "--print" in out) and "--no-session-persistence" in out
          and not any(a in out for a in DROPPED_PHASE_B) and "--max-turns" in out)
    return {"ok": ok, "argv": out}


def run_class3_transform(cand_src: Path, entry: str, raws: list[str]) -> list[str]:
    if not re.fullmatch(r"ssap(\.[A-Za-z_][A-Za-z0-9_]*)+:[A-Za-z_][A-Za-z0-9_]*", entry or ""):
        raise ValueError("class3_entry must be 'ssap.<module>:<function>'")
    out = _isolated(CLASS3_PROBE, [str(Path(cand_src) / "src"), entry], stdin=json.dumps(raws))
    if not isinstance(out, list) or len(out) != len(raws) or not all(isinstance(x, str) for x in out):
        raise ValueError("class3 transform must map each input string to a string")
    return out


def _note_strings(obj: Any, under_note: bool = False) -> Iterable[str]:
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _note_strings(v, under_note or "note" in str(k) or "excerpt" in str(k))
    elif isinstance(obj, list):
        for v in obj:
            yield from _note_strings(v, under_note)
    elif isinstance(obj, str) and under_note:
        yield obj


Evaluator = Callable[[Path, Any], dict]


class CertCache:
    """``(harness tree hash, sample_id) -> cert`` on disk; evaluation failures are not cached."""

    def __init__(self, evaluator: Evaluator, cache_dir: Path, parallel: int = 1):
        self.evaluator, self.dir, self.parallel = evaluator, Path(cache_dir), max(1, int(parallel))
        self._hash: dict[str, str] = {}

    def _path(self, root: Path, sample_id: str) -> Path:
        h = self._hash.setdefault(str(root), tree_hash(root))
        return self.dir / h[:16] / (hashlib.sha256(sample_id.encode()).hexdigest()[:24] + ".json")

    def fetch(self, root: Path, samples: list) -> tuple[dict[str, dict], int]:
        from concurrent.futures import ThreadPoolExecutor
        out, todo = {}, []
        for s in samples:
            p = self._path(root, s.sample_id)
            if p.is_file():
                out[s.sample_id] = json.loads(p.read_text())
            else:
                todo.append(s)

        def one(s):
            try:
                return s, self.evaluator(root, s)
            except Exception:  # noqa: BLE001 - an evaluation fault is an infra fault, never a label
                return s, None
        failures = 0
        with ThreadPoolExecutor(max_workers=self.parallel) as ex:
            for s, cert in ex.map(one, todo):
                if not isinstance(cert, dict):
                    failures += 1
                    continue
                p = self._path(root, s.sample_id)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(json.dumps(cert, sort_keys=True, default=str))
                out[s.sample_id] = cert
        return out, failures


def _m_of(cert: Mapping | None, checker: str):
    if not cert:
        return None
    names = list(cert.get("mirror_names") or HBASE_MIRRORS)
    m = cert.get("m")
    if isinstance(m, list) and checker in names and names.index(checker) < len(m):
        return m[names.index(checker)]
    return (cert.get("terminal_m") or {}).get(checker)


def make_docker_evaluator(cfg: Mapping, alw_repo: str | Path | None, catalog: Mapping,
                          work_dir: Path, *, terminal_fallback: bool = False) -> Evaluator | None:
    """Inner ``mcr_check --gds`` of a harness source tree on a bank sample (network-less Docker).
    ``terminal_fallback``: read ``terminal_m`` from ``mcr_check --gate`` when ``--gds`` lacks it."""
    from ..bank.phase2 import make_docker_inner_cert_fn
    image = proposer_image(cfg)
    tops = {t["task_id"]: t["top"] for t in catalog["tasks"]}
    fns: dict[str, Any] = {}
    lock = threading.Lock()
    if not alw_repo or shutil.which("docker") is None:
        return None

    def ev(root: Path, s) -> dict:
        key = tree_hash(root)[:16]
        with lock:                            # stage each harness once, never concurrently
            if key not in fns:
                fns[key] = make_docker_inner_cert_fn(cfg, root, alw_repo, Path(work_dir) / key, image,
                                                     terminal_fallback=terminal_fallback)
        if fns[key] is None or not s.artifact_path:
            raise RuntimeError("inner certificate unavailable")
        tag = hashlib.sha256(s.sample_id.encode()).hexdigest()[:16]
        return fns[key](s.task_id, tops[s.task_id], Path(s.artifact_path), Path(work_dir) / key / tag)
    return ev


def _screen_class(klass: int, proposal: Mapping, *, bank, boundary: BlindBoundary, cache: CertCache,
                  inc_src: Path, cand_src: Path, cand_specs: list[dict], objective_path: str) -> dict:
    from ..bank.concordance import screen_class1, screen_class2, screen_class3
    from ..bank.referee_bank import CHECKER_D_KEY, D_KEYS
    spec = {s["name"]: s for s in cand_specs}
    checker = proposal.get("checker")
    if klass == 4:
        pb = phase_b_stateless_ok(cand_src)
        return {"ok": objective_path == "path1" and pb["ok"], "objective_path": objective_path,
                "phase_b_stateless": pb["ok"]}
    if klass == 1:
        key = proposal.get("d_star_key") or CHECKER_D_KEY.get(str(checker))
        if not checker or spec.get(checker, {}).get("klass") != 1 or key not in D_KEYS:
            return {"ok": False, "reason": "class1_checker_or_d_star_key_invalid"}
        bidir = bool(proposal.get("bidirectional"))
        need = bank.s_plus_k(key) + (bank.s_minus_k(key) if bidir else []) + bank.bottom_k(key)
        certs, fail = cache.fetch(cand_src, list({s.sample_id: s for s in need}.values()))
        if fail:
            return {"ok": False, "reason": "stage0_infra_error", "n_eval_failures": fail}
        rep = screen_class1(key, bank, lambda s: _m_of(certs.get(s.sample_id), checker),
                            bidirectional=bidir, boundary=boundary).as_dict()
        return {"ok": rep["ok"], "screen": rep}
    samples = list(bank)
    inc, f1 = cache.fetch(inc_src, samples)
    cand, f2 = cache.fetch(cand_src, samples)
    if f1 or f2:
        return {"ok": False, "reason": "stage0_infra_error", "n_eval_failures": f1 + f2}
    m_changed = sum(1 for s in samples if (inc[s.sample_id].get("m") != cand[s.sample_id].get("m")))
    if klass == 2:
        if not checker or spec.get(checker, {}).get("klass") != 2:
            return {"ok": False, "reason": "class2_probe_not_registered_as_class2"}
        rep = screen_class2(checker, bank,
                            lambda s: bool((cand[s.sample_id].get("advisory_flags") or {}).get(checker)),
                            boundary=boundary, overwrite_authority=m_changed > 0).as_dict()
        return {"ok": rep["ok"] and m_changed == 0, "m_changed_samples": m_changed, "screen": rep}
    # Class 3
    u_changed = sum(1 for s in samples if inc[s.sample_id].get("u") != cand[s.sample_id].get("u"))
    over = sum(1 for s in samples for t in _note_strings(cand[s.sample_id]) if len(t.encode()) > 2048)
    cases = [(str(c.get("raw", "")), [str(x) for x in c.get("root_cause_lines") or []])
             for c in proposal.get("class3_self_test") or [] if isinstance(c, Mapping)]
    try:
        raws = [r for r, _ in cases]
        out1 = run_class3_transform(cand_src, str(proposal.get("class3_entry") or ""), raws)
        out2 = run_class3_transform(cand_src, str(proposal.get("class3_entry") or ""), raws)
    except (RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "reason": "class3_transform_error", "error": type(exc).__name__}
    table = dict(zip(raws, out1))
    rep = screen_class3(str(checker or proposal.get("class3_entry")), lambda r: table[r], cases).as_dict()
    deterministic = out1 == out2
    ok = rep["ok"] and deterministic and m_changed == 0 and u_changed == 0 and over == 0
    return {"ok": ok, "m_changed_samples": m_changed, "u_changed_samples": u_changed,
            "notes_over_cap": over, "deterministic": deterministic, "screen": rep}


def _proposal(path: Path) -> tuple[dict | None, str | None]:
    try:
        p = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None, "proposal_missing_or_invalid_json"
    if not isinstance(p, dict) or p.get("mutation_class") not in (1, 2, 3, 4):
        return None, "proposal_mutation_class_invalid"
    cid = str(p.get("candidate_id") or "")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,47}", cid):
        return None, "proposal_candidate_id_invalid"
    return p, None


def validate_and_screen_proposal(inc_src: str | Path, cand_src: str | Path, proposal_path: str | Path,
                                 *, bank_path: str | Path, blind: BlindSet, cfg: Mapping,
                                 alw_repo: str | Path | None, work_dir: str | Path,
                                 use_docker: bool | None = None, catalog: Mapping | None = None,
                                 evaluator: Evaluator | None = None,
                                 smoke_fn: Callable[..., dict] | None = None,
                                 cache_dir: str | Path | None = None) -> dict:
    """Stage 0 of one proposal; the report is ``BlindBoundary``-guarded (aggregates only)."""
    from ..bank.referee_bank import RefereeBank
    inc_src, cand_src, work = Path(inc_src), Path(cand_src), Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    boundary = BlindBoundary(blind)
    if catalog is None:
        catalog = json.loads((Path(cfg["_root"]) / "configs" / "alw" / "catalog.json").read_text())
    rep: dict = {"ok": False, "stage": None, "reason": None, "checks": {}}

    def fail(stage: str, reason: str) -> dict:
        rep.update(stage=stage, reason=reason)
        return _finish(rep, work, boundary, bank_path)

    proposal, err = _proposal(Path(proposal_path))
    if err:
        return fail("proposal", err)
    klass = int(proposal["mutation_class"])
    rep.update(candidate_id=proposal["candidate_id"], mutation_class=klass,
               declared_path=proposal.get("evaluation_path"), checker=proposal.get("checker"))
    patch, changed = diff_trees(inc_src, cand_src)
    (work / "candidate.patch").write_text(patch)
    rep["checks"]["n_changed_files"] = len(changed)
    if not changed:
        return fail("diff", "empty_diff")
    out_of_scope = [r for r in changed if not r.startswith(ALLOWED_PREFIXES)]
    if out_of_scope:
        rep["checks"]["n_out_of_scope_files"] = len(out_of_scope)
        return fail("scope", "file_outside_harness_scope")
    if kernel_hashes(cand_src) != kernel_hashes(inc_src):
        return fail("kernel", "kernel_sha256_changed")
    scan = static_scan(inc_src, cand_src, patch, catalog, changed)
    rep["checks"]["static_violation_kinds"] = sorted({v["kind"] for v in scan["violations"]})
    if not scan["ok"]:
        return fail("static", "r2_r3_violation")
    try:
        inc_specs, cand_specs = registry_specs(inc_src), registry_specs(cand_src)
    except (RuntimeError, ValueError, subprocess.TimeoutExpired):
        return fail("registry", "candidate_registry_does_not_load")
    cs = {s["name"]: s for s in cand_specs}
    intact = all(cs.get(n, {}).get("klass") == 1 and cs[n].get("enabled_in_hbase", True)
                 and cs[n].get("scope", "trajectory") == "trajectory" for n in HBASE_MIRRORS)
    eps_shape = cs.get("m_shape", {}).get("eps")
    muts = registry_mutation_names(inc_specs, cand_specs)
    rep["checks"].update(hbase_mirrors_intact=intact, eps_shape=eps_shape,
                         n_registry_mutations=len(muts))
    if not intact or eps_shape != 0.0:
        return fail("registry", "hbase_mirror_checkers_not_intact")
    if len(muts) > (1 if klass in (1, 2) else 0):
        return fail("registry", "single_mutation_discipline")
    path, phase_a = classify_evaluation_path(inc_src, cand_src, changed, inc_specs, cand_specs)
    rep.update(evaluation_path=path)
    rep["checks"]["n_phase_a_files_changed"] = len(phase_a)
    smoke = (smoke_fn or _default_smoke)(cfg, root=cand_src, work_dir=work / "smoke", use_docker=use_docker)
    rep["checks"]["offline_smoke_ok"] = bool(smoke.get("ok"))
    if not smoke.get("ok"):
        return fail("smoke", "offline_smoke_failed")
    if evaluator is None and klass != 4:
        terminal = any(s.get("klass") == 1 and s.get("scope") == "terminal_only"
                       and s.get("enabled_in_hbase", True) for s in cand_specs)
        evaluator = make_docker_evaluator(cfg, alw_repo, catalog, work / "inner",
                                          terminal_fallback=terminal) \
            if use_docker is not False else None
        if evaluator is None:
            return fail("screen", "stage0_evaluator_unavailable")
    bank = RefereeBank(bank_path)
    cache = CertCache(evaluator or (lambda r, s: {}), Path(cache_dir or work / "cert_cache"),
                      parallel=int((cfg.get("run") or {}).get("parallel") or 1))
    scr = _screen_class(klass, proposal, bank=bank, boundary=boundary, cache=cache, inc_src=inc_src,
                        cand_src=cand_src, cand_specs=cand_specs, objective_path=path)
    rep["screen"] = scr
    if not scr.get("ok"):
        return fail("screen", scr.get("reason") or f"class{klass}_screen_failed")
    rep.update(ok=True, stage="passed", reason="stage0_passed")
    return _finish(rep, work, boundary, bank_path)


def _default_smoke(cfg, *, root, work_dir, use_docker):
    from ..bank.concordance import offline_smoke_container
    return offline_smoke_container(cfg, root=root, work_dir=work_dir, use_docker=use_docker)


def _finish(rep: dict, work: Path, boundary: BlindBoundary, bank_path) -> dict:
    from ..bank.referee_bank import RefereeBank
    blind_recs = [s for s in RefereeBank(bank_path) if boundary.blind.is_blind(s.cluster_id)] \
        if Path(bank_path).is_file() else []
    rep = user_scrub(rep, _secrets())
    boundary.guard(rep, blind_records=blind_recs)
    (work / "stage0_report.json").write_text(json.dumps(rep, indent=1, sort_keys=True, default=str))
    return rep


def stage0_feedback(rep: Mapping) -> dict:
    """Counts-only view of a Stage 0 report for the next proposer attempt."""
    scr = rep.get("screen") or {}
    inner = scr.get("screen") or {}
    return {"stage": rep.get("stage"), "reason": rep.get("reason"),
            "mutation_class": rep.get("mutation_class"), "checks": rep.get("checks") or {},
            "screen_checks": inner.get("checks") or {k: v for k, v in scr.items()
                                                     if isinstance(v, (int, float, bool))},
            "blind_aggregate": inner.get("blind_aggregate")}
