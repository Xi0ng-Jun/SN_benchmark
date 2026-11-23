#!/usr/bin/env python3
"""Transparent ``claude`` wrapper: Phase A, terminal gate, stateless Phase B, snapshots.

The host agent runner invokes ``printf "%s" "$instr" | claude --verbose --output-format=stream-json
[--settings F] ... --print``. Without ``--print`` / ``-p`` (``--version``, ``-v``, interactive use)
the real binary (``MCR_REAL_CLAUDE``, default ``/opt/claude-code/bin/claude``) is exec'd directly.

With ``--print``:
 1. capture stdin to ``.mcr/task_prompt.txt``; freeze the T=0 task inputs (``task/``,
    ``eda_env.sh``) into read-only ``.mcr/trusted_task/`` with ``input_hashes.json``;
 2. Phase A: the real ``claude`` with the original arguments and prompt, stdout inherited, stopped
    at ``T_soft`` (SIGTERM to its process group, SIGKILL after ``kill_grace_sec``);
 3. terminate stray processes of the Phase A session / process tree; promote an unevaluated
    draft; rescue a direct submission write;
 4. ``fork_meta.json`` + ``gate_report.json``; archive the full workspace to ``pre_repair/``;
 5. if the gate triggers: a fresh ``claude -p --no-session-persistence`` (isolated
    ``CLAUDE_CONFIG_DIR``, ``MCR_PHASE=B``, ``--max-turns Delta_S_rep``, ``granted`` seconds) with
    stdout to ``phase_b_stream.jsonl``; cleanup, promote, ``repair_trace.json``, ``post_repair/``;
 6. ``artifact_summary.{json,md}``; exit 0.
"""
from __future__ import annotations

import json
import os
import resource
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mcr_common as mc  # noqa: E402
from ssap.kernel import artifact_summary as asum  # noqa: E402
from ssap.kernel import handoff, snapshot  # noqa: E402
from ssap.kernel.promote import (draft_needs_evaluation, handle_direct_submission_write,  # noqa: E402
                                 load_best, promote, sha256_file, tree_sha256)
from ssap.kernel.watermarks import compute_gate  # noqa: E402
from ssap.checkers.alw import repair as _repair  # noqa: E402

REAL_CLAUDE = os.environ.get("MCR_REAL_CLAUDE", "/opt/claude-code/bin/claude")


def log(msg: str) -> None:
    sys.stderr.write(f"[mcr] {msg}\n")
    sys.stderr.flush()


# ---------------------------------------------------------------------------------------------
# processes
# ---------------------------------------------------------------------------------------------

def _set_subreaper() -> None:
    try:
        import ctypes
        ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0)   # PR_SET_CHILD_SUBREAPER
    except Exception:  # noqa: BLE001
        pass


def _proc_stat(pid: int) -> tuple[int, int, int] | None:
    """``(ppid, pgrp, session)``."""
    try:
        data = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    rest = data[data.rfind(")") + 2:].split()
    try:
        return int(rest[1]), int(rest[2]), int(rest[3])
    except (IndexError, ValueError):
        return None


def _all_pids() -> list[int]:
    try:
        return [int(p) for p in os.listdir("/proc") if p.isdigit()]
    except OSError:
        return []


def _targets(sessions: set[int]) -> list[int]:
    me = os.getpid()
    stats = {pid: _proc_stat(pid) for pid in _all_pids()}
    stats = {k: v for k, v in stats.items() if v}
    ancestors, p = set(), me
    while p in stats and p not in ancestors:
        ancestors.add(p)
        p = stats[p][0]
    children: dict[int, list[int]] = {}
    for pid, (ppid, _, _) in stats.items():
        children.setdefault(ppid, []).append(pid)
    descendants, todo = set(), [me]
    while todo:
        for c in children.get(todo.pop(), []):
            if c not in descendants:
                descendants.add(c)
                todo.append(c)
    out = []
    for pid, (_, _, sid) in stats.items():
        if pid in ancestors:
            continue
        if pid in descendants or sid in sessions:
            out.append(pid)
    return out


def terminate_strays(sessions: set[int], grace: float) -> list[int]:
    victims = _targets(sessions)
    for pid in victims:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline and any(_alive(p) for p in victims):
        _reap()
        time.sleep(0.1)
    for pid in _targets(sessions):
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    time.sleep(0.05)
    _reap()
    return victims


def _alive(pid: int) -> bool:
    st = Path(f"/proc/{pid}/stat")
    try:
        return st.read_text()[st.read_text().rfind(")") + 2] != "Z"
    except (OSError, IndexError):
        return False


def _reap() -> None:
    while True:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        if pid == 0:
            return


def run_bounded(cmd: list[str], *, prompt: str, timeout: float, env: dict, grace: float,
                stdout=None, stderr=None) -> dict:
    started = time.monotonic()
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=stdout, stderr=stderr, env=env,
                            cwd=str(mc.WORKSPACE), start_new_session=True)
    def feed():
        try:
            proc.stdin.write(prompt.encode())
            proc.stdin.close()
        except (BrokenPipeError, OSError, ValueError):
            pass

    threading.Thread(target=feed, daemon=True).start()
    timed_out = False
    try:
        rc = proc.wait(timeout=max(0.0, timeout))
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except OSError:
            pass
        try:
            rc = proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
            rc = proc.wait()
    return {"returncode": rc, "timed_out": timed_out, "session": proc.pid,
            "seconds": round(time.monotonic() - started, 3)}


# ---------------------------------------------------------------------------------------------
# workspace setup
# ---------------------------------------------------------------------------------------------

def freeze_trusted_inputs() -> dict:
    mcr = mc.mcr_dir()
    trusted = mcr / "trusted_task"
    if trusted.exists():
        try:
            return json.loads((mcr / "input_hashes.json").read_text())
        except (OSError, ValueError):
            return {}
    staging = mcr / "trusted_task.tmp"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    task = mc.WORKSPACE / "task"
    if task.is_dir():
        shutil.copytree(task, staging / "task", symlinks=True)
    env = mc.WORKSPACE / "eda_env.sh"
    if env.is_file():
        shutil.copy2(env, staging / "eda_env.sh")
    hashes = {}
    for p in sorted(staging.rglob("*")):
        if p.is_file() and not p.is_symlink():
            hashes[p.relative_to(staging).as_posix()] = sha256_file(p)
    for p in sorted(staging.rglob("*"), reverse=True):
        if not p.is_symlink():
            p.chmod(0o555 if p.is_dir() else 0o444)
    os.rename(staging, trusted)
    trusted.chmod(0o555)
    doc = {"schema_version": 1, "files": hashes, "frozen_at_unix": round(time.time(), 3)}
    mc.write_json(mcr / "input_hashes.json", doc)
    return doc


def phase_b_args(argv: list[str], turns: int) -> list[str]:
    return _repair.phase_b_args(argv, turns)


def repair_prompt(gate: dict, fork: dict) -> str:
    return _repair.repair_prompt(gate, fork, handoff.read(mc.mcr_dir() / "handoff.json"))


# ---------------------------------------------------------------------------------------------
# main flow
# ---------------------------------------------------------------------------------------------

def _promote_pending(ws, suite) -> dict | None:
    res = None
    if draft_needs_evaluation(ws):
        r = promote(ws, ws.draft_artifact, ws.draft_aux if ws.draft_aux.is_dir() else None,
                    suite.evaluate, suite.tier_spec, validate=suite.validate)
        res = {"promoted": r.promoted, "reason": r.reason}
    rescue = handle_direct_submission_write(ws, suite.evaluate, suite.tier_spec, validate=suite.validate)
    if rescue:
        res = (res or {}) | {"direct_write": {k: rescue.get(k) for k in
                                              ("promoted", "reason", "restored", "snapshot_dir")}}
    return res


def _cert_fields(ws, suite) -> dict:
    best = load_best(ws)
    if best is None:
        return {"m": None, "u": None, "raw_u": None}
    d = best.as_dict(suite.tier_spec)
    return {"m": d["m"], "u": d["u"], "raw_u": d["raw_u"]}


def run_print_mode(argv: list[str]) -> int:
    _set_subreaper()
    prompt = sys.stdin.read()
    ws_root = mc.WORKSPACE
    mcr = mc.mcr_dir()
    mcr.mkdir(parents=True, exist_ok=True)
    (mcr / "task_prompt.txt").write_text(prompt)
    t0 = mc.start_clock()
    wm = mc.watermarks()
    freeze_trusted_inputs()
    for d in ("submission", "work/draft"):
        (ws_root / d).mkdir(parents=True, exist_ok=True)
    snap = mc.SNAPSHOT_DIR
    snap.mkdir(parents=True, exist_ok=True)

    bin_dir = os.path.dirname(os.path.abspath(__file__))
    py_dir = str(mc.HARNESS_PYTHON)
    base_path = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
    merged_path = f"{bin_dir}:{base_path}" if bin_dir not in base_path.split(":") else base_path
    base_py = os.environ.get("PYTHONPATH", "/opt/analog-layout/pdks/sky130/python")
    merged_py = f"{py_dir}:{base_py}" if py_dir not in base_py.split(":") else base_py
    env_a = dict(os.environ, MCR_PHASE="A", PATH=merged_path, PYTHONPATH=merged_py)
    phase_a = run_bounded([REAL_CLAUDE] + argv, prompt=prompt,
                          timeout=wm.t_soft_sec - (time.time() - t0), env=env_a,
                          grace=wm.kill_grace_sec)
    killed = terminate_strays({phase_a["session"]}, wm.kill_grace_sec)
    elapsed = time.time() - t0
    ws, suite = mc.workspace(), mc.suite()
    pending_a = _promote_pending(ws, suite)
    fork = wm.fork_meta(elapsed)
    best = load_best(ws)
    m_pre = list(best.m(suite.tier_spec)) if best else None
    gate = compute_gate(fork, m_pre, unevaluated_draft=draft_needs_evaluation(ws),
                        advisory_triggers=suite.gate_advisories(best))
    fork |= {"phase_a": phase_a | {"stray_processes_terminated": len(killed)},
             "pending_promotion": pending_a,
             "phase_a_argv": [asum.scrub_text(a, asum.literal_secrets_from_env()) for a in argv]}
    mc.write_json(mcr / "fork_meta.json", fork)
    mc.write_json(mcr / "gate_report.json", gate)
    pre_fields = _cert_fields(ws, suite)
    pre_sha = sha256_file(ws.submission_artifact)
    snapshot.archive_pre_repair(ws_root, snap / "pre_repair",
                                exclude_paths=[mcr / "staging", mcr / "phase_b_sessions", snap])

    repair_ran, ratchet_promoted, promote_reason, post_fields, post_sha = False, False, None, {}, None
    if gate["gate_triggered"]:
        repair_ran = True
        env_b = dict(os.environ, MCR_PHASE="B", PATH=merged_path, PYTHONPATH=merged_py,
                     CLAUDE_CONFIG_DIR=str(mcr / "phase_b_sessions"))
        (mcr / "phase_b_sessions").mkdir(parents=True, exist_ok=True)
        best_before = best.artifact_sha256 if best else None
        with open(snap / "phase_b_stream.jsonl", "wb") as out, open(snap / "phase_b_stderr.log", "wb") as err:
            phase_b = run_bounded([REAL_CLAUDE] + phase_b_args(argv, fork["granted_repair_turns"]),
                                  prompt=repair_prompt(gate, fork),
                                  timeout=fork["granted_repair_budget_sec"], env=env_b,
                                  grace=wm.kill_grace_sec, stdout=out, stderr=err)
        killed_b = terminate_strays({phase_b["session"]}, wm.kill_grace_sec)
        pending_b = _promote_pending(ws, suite)
        best_after = load_best(ws)
        ratchet_promoted = bool(best_after and best_after.artifact_sha256 != best_before)
        promote_reason = (pending_b or {}).get("reason") if pending_b else (
            "promoted_during_repair" if ratchet_promoted else "no_new_candidate")
        post_fields = _cert_fields(ws, suite)
        post_sha = sha256_file(ws.submission_artifact)
        trace = {"schema_version": 1, "phase_b": phase_b | {"stray_processes_terminated": len(killed_b)},
                 "pending_promotion": pending_b, "ratchet_promoted": ratchet_promoted,
                 "best_sha256_before": best_before,
                 "best_sha256_after": best_after.artifact_sha256 if best_after else None}
        mc.write_json(snap / "repair_trace.json", trace)
        snapshot.archive_post_repair(snap / "post_repair", best_dir=ws.best_dir,
                                     submission_dir=ws.submission_dir,
                                     files=[snap / "phase_b_stream.jsonl", snap / "repair_trace.json"])

    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    secrets = asum.literal_secrets_from_env() + [v for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
                                                 if (v := os.environ.get(k))]
    summary = asum.build_summary(
        task_id=os.environ.get("MCR_TASK_ID"), seed=os.environ.get("MCR_SEED"),
        version_id=os.environ.get("MCR_VERSION_ID"), model_id=os.environ.get("ANTHROPIC_MODEL"),
        key_slot=os.environ.get("MCR_KEY_SLOT"), agent_image=os.environ.get("MCR_AGENT_IMAGE"),
        watermarks=wm.as_dict(), pre_repair_gds_sha256=pre_sha, post_repair_gds_sha256=post_sha,
        submission_gds_sha256=sha256_file(ws.submission_artifact),
        submission_src_tree_sha256=tree_sha256(ws.submission_aux),
        draft_latest_gds_sha256=sha256_file(ws.draft_artifact),
        m_pre=pre_fields["m"], u_pre=pre_fields["u"], raw_u_pre=pre_fields["raw_u"],
        m_post=post_fields.get("m"), u_post=post_fields.get("u"), raw_u_post=post_fields.get("raw_u"),
        gate_triggered=gate["gate_triggered"], trigger_reasons=gate["trigger_reasons"],
        repair_ran=repair_ran, ratchet_promoted=ratchet_promoted, promote_reason=promote_reason,
        phase_a_reused=False, phase_b_invoked=repair_ran, model_request_diverged=None,
        submission_artifact_diverged=None, infra_error=False, infra_reason=None, outer_metrics=None,
        cost_ledger={"wall_sec": round(time.time() - t0, 3),
                     "tool_cpu_sec": round(usage.ru_utime + usage.ru_stime, 3),
                     "peak_ram_mb": round(usage.ru_maxrss / 1024.0, 1)},
        secrets=secrets)
    asum.write_summary(snap, summary, secrets)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if any(a in ("--version", "-v") for a in argv) or not any(a in ("--print", "-p") for a in argv):
        os.execv(REAL_CLAUDE, [REAL_CLAUDE] + argv)
    try:
        return run_print_mode(argv)
    except Exception as exc:  # noqa: BLE001 - never hand the host a non-zero agent exit
        log(f"entrypoint error: {type(exc).__name__}: {exc}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
