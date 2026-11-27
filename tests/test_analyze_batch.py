"""scripts/analyze_batch.py with a stub agent command (no model calls, no containers)."""
from __future__ import annotations

import importlib.util
import stat
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("analyze_batch", ROOT / "scripts" / "analyze_batch.py")
ab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ab)

STUB = """#!/bin/sh
# Stub agent: record argv, read the prompt from stdin, reply with a report or fail on request.
printf '%s\\n' "$@" > "$STUB_DIR/argv.txt"
prompt=$(cat)
case "$prompt" in
  *run-bad*) echo "boom" >&2; exit 3 ;;
  *任务\\ B*) printf '# 汇总报告：pilot\\n'; head -c 300 /dev/zero | tr '\\0' 's' ;;
  *) printf '# 单次运行报告\\n'; head -c 300 /dev/zero | tr '\\0' 'x' ;;
esac
"""


def make(tmp_path: Path, monkeypatch, runs=("run-a", "run-b")) -> tuple[Path, str]:
    state = tmp_path / "state"
    for r in runs:
        d = state / "jobs" / "test-H_raw" / "harbor" / r / f"{r}__abc" / "agent"
        d.mkdir(parents=True)
        (d / "trajectory.json").write_text("{}")
    stub = tmp_path / "stub.sh"
    stub.write_text(STUB)
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("STUB_DIR", str(tmp_path))
    return state, str(stub)


def test_run_writes_reports_and_summary_and_skips_done(tmp_path, monkeypatch, capsys):
    state, stub = make(tmp_path, monkeypatch)
    base = ["--state", str(state), "--claude", stub, "--jobs", "1"]
    assert ab.main(["run", *base, "--summary", "pilot"]) == 0
    out = state / "analysis" / "auto"
    assert sorted(p.name for p in out.glob("*.md")) == ["run_run-a.md", "run_run-b.md", "summary_pilot.md"]
    argv = (tmp_path / "argv.txt").read_text().split("\n")
    assert argv[0] == "-p" and "Bash(python3 scripts/traj_digest.py:*)" in argv and "Write" not in argv
    capsys.readouterr()
    assert ab.main(["run", *base]) == 0
    assert "0 to analyse" in capsys.readouterr().out
    assert ab.main(["bundle", *base]) == 0
    assert "<<<<< run_run-a.md >>>>>" in (out / "bundle.txt").read_text()


def test_failed_session_leaves_no_report_and_blocks_summary(tmp_path, monkeypatch, capsys):
    state, stub = make(tmp_path, monkeypatch, runs=("run-a", "run-bad"))
    assert ab.main(["run", "--state", str(state), "--claude", stub, "--summary", "s"]) == 1
    out = state / "analysis" / "auto"
    assert [p.name for p in out.glob("*.md")] == ["run_run-a.md"]
    assert "boom" in (out / "logs" / "run_run-bad.log").read_text()
    assert "summary skipped" in capsys.readouterr().out
    ab.main(["list", "--state", str(state)])
    listing = capsys.readouterr().out
    assert "done\trun-a" in listing and "todo\trun-bad" in listing
