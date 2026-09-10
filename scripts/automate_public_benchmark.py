#!/usr/bin/env python3
"""Run a fresh local public benchmark through prepare, Ask, judge, report, and audit."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from rag_eval.artifacts import save_json


def baseline_from(path):
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    target = value.get("run_dir") if isinstance(value, dict) else value
    return Path(target).expanduser().resolve() if target else None


def invoke(command, state, name):
    state["stages"].append({"name": name, "command": command, "status": "running"})
    save_json(Path(state["run_dir"]) / "automation.json", state)
    result = subprocess.run(command, cwd=ROOT)
    state["stages"][-1].update(status="completed" if result.returncode == 0 else "failed",
                               exit_code=result.returncode)
    save_json(Path(state["run_dir"]) / "automation.json", state)
    if result.returncode:
        raise SystemExit(result.returncode)


def persist_failure(state, error):
    state["status"] = "failed"
    state["failure"] = {"error_type": type(error).__name__, "message": str(error)}
    save_json(Path(state["run_dir"]) / "automation.json", state)


def verify_baseline(run, baseline, state):
    if baseline is None:
        state["comparison"] = {"status": "not_requested"}
        save_json(run / "automation.json", state)
        return
    required = (baseline / "manifest.json", baseline / "outputs.jsonl",
                baseline / "scores.jsonl", baseline / "audit.json")
    if not all(path.is_file() for path in required):
        raise ValueError(f"baseline is incomplete: {baseline}")
    current_identity = json.loads((run / "manifest.json").read_text(encoding="utf-8"))["comparison_identity"]
    baseline_manifest = json.loads(required[0].read_text(encoding="utf-8"))
    baseline_identity = baseline_manifest["comparison_identity"]
    if current_identity != baseline_identity:
        raise ValueError("baseline comparison_identity is incompatible with this run")
    if json.loads(required[3].read_text(encoding="utf-8")).get("status") != "passed":
        raise ValueError("baseline integrity audit has not passed")
    outputs = [json.loads(line) for line in required[1].read_text(encoding="utf-8").splitlines()
               if line.strip()]
    actual = {(str(row.get("dataset")), str(row.get("id")), str(row.get("mode")))
              for row in outputs if row.get("repeat", 0) == 0}
    planned_rows = baseline_manifest.get("planned_output_keys")
    if isinstance(planned_rows, list):
        planned = {(str(row.get("dataset")), str(row.get("id")), str(row.get("mode")))
                   for row in planned_rows}
        complete = len(planned) == len(planned_rows) == baseline_manifest.get("planned_output_count")
    else:
        planned = actual
        complete = len(actual) == baseline_manifest.get("planned_output_count")
    if not complete or actual != planned or len(outputs) != len(actual):
        raise ValueError("baseline primary outputs do not match its immutable plan")
    state["comparison"] = {"status": "requested_compatible_identity", "baseline": str(baseline)}
    save_json(run / "automation.json", state)


def run_stages(args, run, baseline, state):
    python = str(ROOT / ".venv/bin/python")
    runner = [python, str(ROOT / "scripts/run_public_benchmark.py"), "--run-dir", str(run),
              "--bundle", str(args.bundle.resolve()), "--project-root", str(args.project_root.resolve()),
              "--jobs", str(args.jobs)]
    invoke(runner + ["--stage", "prepare", "--scope", args.scope], state, "prepare")
    verify_baseline(run, baseline, state)
    invoke(runner + ["--stage", "ask", "--scope", args.scope], state, "ask")
    invoke(runner + ["--stage", "judge", "--scope", args.scope], state, "judge")
    report = [python, str(ROOT / "scripts/report_public_benchmark.py"), str(run)]
    if baseline:
        report += ["--compare", str(baseline)]
    invoke(report, state, "report")
    invoke([python, str(ROOT / "scripts/audit_public_benchmark.py"), str(run),
            "--bundle", str(args.bundle.resolve())], state, "audit")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", choices=("regression", "smoke"), default="regression")
    parser.add_argument("--runs-root", type=Path, default=ROOT / "var/public-benchmark")
    parser.add_argument("--bundle", type=Path, default=ROOT / "data/public-benchmark-v1")
    parser.add_argument("--project-root", type=Path, default=ROOT.parent / "project")
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--baseline-pointer", type=Path,
                        default=ROOT / "var/public-benchmark/baseline.json")
    parser.add_argument("--jobs", type=int, choices=(1, 2), default=2)
    args = parser.parse_args(argv)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run = args.runs_root.resolve() / f"{stamp}-{args.scope}-{uuid4().hex[:8]}"
    run.mkdir(parents=True)
    state = {"run_dir": str(run), "scope": args.scope, "baseline": None,
             "outer_timeout": None, "stages": []}
    save_json(run / "automation.json", state)
    lock_path = args.runs_root.resolve() / ".automation.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with lock_path.open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            baseline = (args.baseline.resolve() if args.baseline
                        else baseline_from(args.baseline_pointer.resolve()))
            state["baseline"] = str(baseline) if baseline else None
            save_json(run / "automation.json", state)
            run_stages(args, run, baseline, state)
    except BaseException as exc:
        persist_failure(state, exc)
        raise
    state["status"] = "completed"
    save_json(run / "automation.json", state)
    print(run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
