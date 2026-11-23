"""Host-side preparation of the ALW agent container: harness bundle, compose overlay, and the
offline stateless Phase B invocation (Path 1 replay).

* ``stage_harness_bundle`` builds the read-only ``/opt/harness`` tree::

      <dest>/settings.json                hooks for H_base / H*
      <dest>/bin/{mcr_entrypoint.py, mcr_hook.py, mcr_common.py, mcr_check, mcr_promote}  (0o755)
      <dest>/python/ssap/{__init__.py, config.py, kernel/, checkers/__init__.py, checkers/alw/}
      <dest>/mcr_config.json              watermarks / check timeout (optional)
      <dest>/bundle_manifest.json         relative path -> sha256

  Inside the container ``PYTHONPATH=/opt/harness/python`` imports ``ssap`` without
  ``pip install``. ``targets/``, ``bank/`` and the CLI are never shipped.
* ``write_compose_overlay`` writes the Harbor ``environment.extra_docker_compose`` overlay
  (host gateway, optional harness mount, optional agent image build argument). ``H_raw`` gets no
  harness mount. The verifier image is never touched by the overlay.
* ``build_stateless_repair_invocation`` rebuilds the exact Phase B command of the live wrapper
  from an archived ``pre_repair`` snapshot (``fork_meta.json`` / ``gate_report.json``).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping

from ...checkers.alw import repair as _repair
from ...kernel.artifact_summary import REDACTED

HARNESS_MOUNT = "/opt/harness"
CONTAINER_WORKSPACE = "/alb-workspace"
REAL_CLAUDE_IN_IMAGE = "/opt/claude-code/bin/claude"
COMPOSE_SERVICE = "main"
LOOPBACK_NO_PROXY = "host.docker.internal,172.17.0.1,localhost,127.0.0.1,::1,0.0.0.0"
MODEL_SLOTS = (
    "ANTHROPIC_MODEL", "ANTHROPIC_SMALL_FAST_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL", "CLAUDE_CODE_SUBAGENT_MODEL",
    "CLAUDE_CODE_AUTO_MODE_MODEL", "CLAUDE_CODE_BG_CLASSIFIER_MODEL",
)
FIXED_MODEL_ENV = {
    "DISABLE_NON_ESSENTIAL_MODEL_CALLS": "1",
    "CLAUDE_CODE_TWO_STAGE_CLASSIFIER": "false",
    "API_TIMEOUT_MS": "3000000",
}
# Source files shipped into the container (relative to src/ssap).
SHIPPED_PY = ("__init__.py", "config.py", "kernel", "checkers/__init__.py", "checkers/alw")


# ---------------------------------------------------------------------------------------------
# model homogeneity
# ---------------------------------------------------------------------------------------------

def model_env(model: str, cfg: Mapping | None = None) -> dict[str, str]:
    """All Claude Code model slots pinned to *model*, plus the fixed homogeneity settings."""
    if not model or not str(model).strip():
        raise ValueError("a non-empty model id is required (8-slot model homogeneity)")
    mh = (cfg or {}).get("model_homogeneity") or {}
    slots = tuple(mh.get("slots") or MODEL_SLOTS)
    missing = set(MODEL_SLOTS) - set(slots)
    if missing:
        raise ValueError(f"model_homogeneity.slots lacks {sorted(missing)}")
    env = {s: str(model) for s in slots}
    env.update({k: str(v) for k, v in (mh.get("fixed") or FIXED_MODEL_ENV).items()})
    for k, v in FIXED_MODEL_ENV.items():
        env.setdefault(k, v)
    return env


def assert_model_homogeneous(env: Mapping[str, str], model: str) -> None:
    bad = {s: env.get(s) for s in MODEL_SLOTS if env.get(s) != model}
    if bad:
        raise ValueError(f"model slots not pinned to {model!r}: {sorted(bad)}")


# ---------------------------------------------------------------------------------------------
# harness bundle
# ---------------------------------------------------------------------------------------------

def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _copy_py_tree(src: Path, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    for p in sorted(src.iterdir()):
        if p.name == "__pycache__" or p.is_symlink():
            continue
        if p.is_dir():
            _copy_py_tree(p, dst / p.name)
        elif p.suffix == ".py":
            shutil.copy2(p, dst / p.name)


def stage_harness_bundle(root: str | Path, dest_dir: str | Path, *,
                         watermarks: Mapping | None = None,
                         check_timeout_sec: float | None = None) -> dict:
    """Stage the ``/opt/harness`` tree into *dest_dir* (replaced if present); returns the manifest."""
    root, dest = Path(root), Path(dest_dir)
    cont = root / "container" / "alw"
    pkg = root / "src" / "ssap"
    if not (cont / "settings.json").is_file() or not (pkg / "kernel").is_dir():
        raise FileNotFoundError(f"{root} is not an ssap checkout (container/alw, src/ssap)")
    if dest.exists():
        shutil.rmtree(dest)
    (dest / "bin").mkdir(parents=True)
    shutil.copy2(cont / "settings.json", dest / "settings.json")
    for p in sorted((cont / "bin").iterdir()):
        if p.is_file() and not p.is_symlink() and p.name != "__pycache__":
            shutil.copy2(p, dest / "bin" / p.name)
            os.chmod(dest / "bin" / p.name, 0o755)
    out_pkg = dest / "python" / "ssap"
    for rel in SHIPPED_PY:
        src = pkg / rel
        if src.is_dir():
            _copy_py_tree(src, out_pkg / rel)
        else:
            (out_pkg / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, out_pkg / rel)
    if watermarks is not None or check_timeout_sec is not None:
        conf: dict = {"schema_version": 1}
        if watermarks is not None:
            conf["watermarks"] = dict(watermarks)
        if check_timeout_sec is not None:
            conf["check_timeout_sec"] = float(check_timeout_sec)
        (dest / "mcr_config.json").write_text(json.dumps(conf, indent=1, sort_keys=True))
    files = {p.relative_to(dest).as_posix(): _sha(p)
             for p in sorted(dest.rglob("*")) if p.is_file()}
    manifest = {"schema_version": 1, "mount": HARNESS_MOUNT, "files": files}
    (dest / "bundle_manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True))
    return manifest


# ---------------------------------------------------------------------------------------------
# compose overlay
# ---------------------------------------------------------------------------------------------

def _q(s: str) -> str:
    return json.dumps(str(s))          # a JSON string is a valid YAML double-quoted scalar


def write_compose_overlay(dest_path: str | Path, *, harness_dir: str | Path | None = None,
                          agent_image: str | None = None, service: str = COMPOSE_SERVICE,
                          no_proxy: str = LOOPBACK_NO_PROXY) -> Path:
    """Harbor ``extra_docker_compose`` overlay for the agent service only.

    ``harness_dir=None`` (``H_raw``) mounts no harness; an empty ``agent_image`` keeps the task's
    own build (``ALW_BASE_IMAGE`` untouched)."""
    lines = ["# generated by ssap.targets.alw.agent_wrapper; agent service only", "services:",
             f"  {service}:", "    extra_hosts:", f"      - {_q('host.docker.internal:host-gateway')}",
             "    environment:", f"      NO_PROXY: {_q(no_proxy)}", f"      no_proxy: {_q(no_proxy)}"]
    if harness_dir is not None:
        hd = Path(harness_dir).resolve()
        lines += ["    volumes:", f"      - {_q(f'{hd}:{HARNESS_MOUNT}:ro')}"]
    if agent_image:
        lines += ["    build:", "      args:", f"        ALW_BASE_IMAGE: {_q(agent_image)}"]
    p = Path(dest_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines) + "\n")
    return p


# ---------------------------------------------------------------------------------------------
# offline stateless Phase B (Path 1 replay)
# ---------------------------------------------------------------------------------------------

def _first_file(cands: Iterable[Path]) -> Path | None:
    return next((c for c in cands if c.is_file()), None)


def find_fork_meta(snapshot_pre_dir: Path, gate_report_path: Path | None = None) -> Path | None:
    s = Path(snapshot_pre_dir)
    c = [s / "fork_meta.json", s / ".mcr" / "fork_meta.json", s / "workspace" / ".mcr" / "fork_meta.json"]
    if gate_report_path is not None:
        c.insert(0, Path(gate_report_path).parent / "fork_meta.json")
    return _first_file(c)


@dataclass
class RepairInvocation:
    argv: list[str]
    env: dict[str, str]
    prompt: str
    timeout_sec: float
    max_turns: int
    cwd: str
    snapshot_workspace: str
    fork_meta: dict = field(default_factory=dict)
    gate: dict = field(default_factory=dict)
    secret_values: tuple[str, ...] = ()

    @property
    def gate_triggered(self) -> bool:
        return bool(self.gate.get("gate_triggered"))

    def as_dict(self, redact: bool = True) -> dict:
        def r(v: str) -> str:
            if not redact:
                return v
            for s in self.secret_values:
                if s:
                    v = v.replace(s, REDACTED)
            return v
        return {"argv": [r(a) for a in self.argv], "env": {k: r(v) for k, v in self.env.items()},
                "prompt_sha256": hashlib.sha256(self.prompt.encode()).hexdigest(),
                "timeout_sec": self.timeout_sec, "max_turns": self.max_turns, "cwd": self.cwd,
                "snapshot_workspace": self.snapshot_workspace,
                "granted_repair_budget_sec": self.fork_meta.get("granted_repair_budget_sec"),
                "phase_a_elapsed_sec": self.fork_meta.get("phase_a_elapsed_sec"),
                "gate_triggered": self.gate_triggered}


def build_stateless_repair_invocation(snapshot_pre_dir: str | Path, gate_report_path: str | Path, *,
                                      model: str, proxy_url: str, trial_token: str,
                                      max_turns: int = 15,
                                      claude_bin: str = REAL_CLAUDE_IN_IMAGE,
                                      workspace: str = CONTAINER_WORKSPACE,
                                      config_dir: str | None = None,
                                      base_argv: Iterable[str] | None = None,
                                      cfg: Mapping | None = None) -> RepairInvocation:
    """Phase B command / env for an archived Phase A snapshot.

    The budget is ``granted_repair_budget_sec`` of the archived ``fork_meta.json`` (fallback:
    ``gate_report.json``); it is never recomputed from replay wall-clock. Phase A arguments are
    taken from ``fork_meta.phase_a_argv`` when archived, else *base_argv*, else the Harbor default
    stream-json arguments. The session directory is a fresh isolated ``CLAUDE_CONFIG_DIR``."""
    if not trial_token:
        raise ValueError("a per-trial proxy token is required")
    if not proxy_url.startswith(("http://", "https://")):
        raise ValueError("proxy_url must be an http(s) URL of the metering proxy")
    snap = Path(snapshot_pre_dir)
    gpath = Path(gate_report_path)
    gate = json.loads(gpath.read_text())
    fpath = find_fork_meta(snap, gpath)
    fork = json.loads(fpath.read_text()) if fpath else {}
    granted = fork.get("granted_repair_budget_sec", gate.get("granted_repair_budget_sec"))
    if granted is None:
        raise ValueError("neither fork_meta.json nor gate_report.json records granted_repair_budget_sec")
    fork_view = {"granted_repair_budget_sec": float(granted),
                 "granted_repair_turns": int(max_turns),
                 "phase_a_elapsed_sec": fork.get("phase_a_elapsed_sec", gate.get("phase_a_elapsed_sec"))}
    ws_dir = snap / "workspace" if (snap / "workspace").is_dir() else snap
    handoff_path = ws_dir / ".mcr" / "handoff.json"
    handoff_rec = None
    if handoff_path.is_file():
        try:
            handoff_rec = json.loads(handoff_path.read_text())
        except ValueError:
            handoff_rec = None
    prompt = _repair.repair_prompt(gate, fork_view, handoff_rec, workspace)
    base = list(fork.get("phase_a_argv") or base_argv or ["--verbose", "--output-format=stream-json"])
    argv = [claude_bin] + _repair.phase_b_args(base, max_turns)
    if "--model" not in argv and not any(a.startswith("--model=") for a in argv):
        argv[1:1] = ["--model", model]
    env = model_env(model, cfg)
    env.update({
        "ANTHROPIC_BASE_URL": proxy_url, "ANTHROPIC_API_KEY": trial_token,
        "MCR_PHASE": "B", "CLAUDE_CONFIG_DIR": config_dir or f"{workspace}/.mcr/phase_b_sessions",
        "MCR_WORKSPACE": workspace, "MCR_HARNESS_PYTHON": f"{HARNESS_MOUNT}/python",
        "PYTHONPATH": f"{HARNESS_MOUNT}/python:/opt/analog-layout/pdks/sky130/python",
        "PATH": (f"{HARNESS_MOUNT}/bin:/opt/analog-layout/python/bin:/opt/analog-layout/tools/bin:"
                 "/opt/analog-layout/agent/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"),
        "NO_PROXY": LOOPBACK_NO_PROXY, "no_proxy": LOOPBACK_NO_PROXY,
    })
    assert_model_homogeneous(env, model)
    return RepairInvocation(argv=argv, env=env, prompt=prompt, timeout_sec=float(granted),
                            max_turns=int(max_turns), cwd=workspace, snapshot_workspace=str(ws_dir),
                            fork_meta=fork, gate=gate, secret_values=(trial_token,))
