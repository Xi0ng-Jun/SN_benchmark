"""experiments/vero/run_series.sh on the offline fake VeRO: resume, the stuck-method end, endpoint, next run."""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

from .test_vero_adapter import world  # noqa: F401  (fixture)

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "experiments" / "vero" / "run_series.sh"


def _env(world, tmp_path, **extra) -> dict:  # noqa: F811
    # "python -m rag_eval.vero.cli" of the script goes to fake_cli.py; everything else to the real python.
    wrapper = tmp_path / "python"
    wrapper.write_text(f'#!/bin/sh\nif [ "$1" = "-m" ] && [ "$2" = "rag_eval.vero.cli" ]; then shift 2; '
                       f'exec "{sys.executable}" "{REPO / "tests" / "vero" / "fake_cli.py"}" "$@"; fi\n'
                       f'exec "{sys.executable}" "$@"\n')
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)
    models = dict(VERO_TARGET_MODEL="openai/t", VERO_GRADER_MODEL="openai/g", VERO_OPTIMIZER_MODEL="openai/o")
    setup = json.loads((world["root"] / "setup.json").read_text())
    setup["models"] = dict(target="openai/t", grader="openai/g", optimizer="openai/o")
    (world["root"] / "setup.json").write_text(json.dumps(setup))
    cfg = tmp_path / "v4.json"
    cfg.write_text(json.dumps(dict(time_budget_hours=1, calibration_k=2, b=1, cards_per_analysis=1, diagnose_size=0,
                                   agent_estimate_minutes=1, max_consecutive_run_failures=2, review=False,
                                   speculative_prefetches=0, speculative_analysis=False)))
    env = dict(os.environ, VERO_ENV_LOCAL="/dev/null", VERO_ROOT=str(tmp_path), VERO_STATE=str(world["root"].parent),
                VERO_PYTHON=str(wrapper), VERO_TARGET_API_BASE="http://127.0.0.1:9/v1", VERO_TARGET_API_KEY="x",
                VERO_SKIP_SERVICE_CHECK="1", V41_CONFIG=str(cfg), RESUME_WAIT="0", RETRY_WAIT="0", MAX_RESUMES="1",
                MAX_AGENT_FAILURES="1", GATE="0", GATE_FLOOR="0", PYTHONPATH=str(REPO / "src"), **models)
    env.update(extra)
    return env


def test_series_resumes_ends_a_stuck_run_and_tests_each_run(world, tmp_path):  # noqa: F811
    # The fake analyst repeats its card: after the adoption every run halts with process_failure (stuck method).
    out = subprocess.run([str(SCRIPT), "v41", world["root"].name, "run1", "run2"], env=_env(world, tmp_path),
                         capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, out.stdout[-3000:] + out.stderr[-3000:]
    for run in ("run1", "run2"):
        summary = json.loads((world["root"] / "runs" / run / "endpoint.json").read_text())["summary"]
        assert summary["halt_reason"] == "process_failure" and summary["adopted"] >= 1 and summary["delta"] > 0.3
    assert out.stdout.count("the method is stuck after 1 resumes") == 2 and "series finished" in out.stdout
    # A finished series is skipped as a whole.
    again = subprocess.run([str(SCRIPT), "v41", world["root"].name, "run1", "run2"], env=_env(world, tmp_path),
                           capture_output=True, text=True, timeout=60)
    assert again.returncode == 0 and again.stdout.count("already finished") == 2


def test_series_stops_on_an_agent_outage(world, tmp_path):  # noqa: F811
    setup = json.loads((world["root"] / "setup.json").read_text())
    setup.update(agent_retries=0, agent_retry_wait=0)
    (world["root"] / "setup.json").write_text(json.dumps(setup))
    Path(setup["analyst_cmd"].split()[-1]).write_text("import sys\nsys.exit(1)\n")
    out = subprocess.run([str(SCRIPT), "v41", world["root"].name, "run1", "run2"], env=_env(world, tmp_path),
                         capture_output=True, text=True, timeout=300)
    assert out.returncode == 1 and "series stopped" in out.stdout, out.stdout[-3000:] + out.stderr[-3000:]
    assert not (world["root"] / "runs" / "run1" / "endpoint.json").exists()
    assert not (world["root"] / "runs" / "run2").exists()                    # the next run is not started


def test_invalid_measurements_are_archived_not_tested(world, tmp_path):  # noqa: F811
    # A seed calibration below the floor means the services were down while the seed was measured: the run
    # must be archived (with invalid.json) and restarted, never tested.
    out = subprocess.run([str(SCRIPT), "v41", world["root"].name, "run1"],
                         env=_env(world, tmp_path, GATE_FLOOR="0.9", MAX_RESTARTS="1"),
                         capture_output=True, text=True, timeout=300)
    assert out.returncode == 1, out.stdout[-3000:]
    assert "measurements invalid" in out.stdout and "series stopped" in out.stdout
    assert not (world["root"] / "runs" / "run1" / "endpoint.json").exists()
    archived = sorted((world["root"].parent / "archive").glob("*"))
    assert archived and all((d / "invalid.json").exists() for d in archived)


def test_gepa_invalid_attempt_is_archived_and_retried(world, tmp_path):  # noqa: F811
    # run_gepa.py exits 3 when its measurements are invalid; the series archives that attempt, retries, and
    # stops after MAX_RESTARTS instead of treating the attempt as a result.
    stub = tmp_path / "vero-benchmarking" / ".venv" / "bin" / "python"
    stub.parent.mkdir(parents=True)
    script = tmp_path / "gepa_stub.py"
    script.write_text(textwrap.dedent('''
        import json, os, sys
        args = dict(zip(sys.argv[1::2], sys.argv[2::2]))
        out = os.path.join(os.environ["VERO_STATE"], "gepa", args["--task"], args["--run"])
        os.makedirs(out, exist_ok=True)
        json.dump(dict(reason="seed scored 0.000 on the test split"), open(os.path.join(out, "invalid.json"), "w"))
        sys.exit(3)
    '''))
    stub.write_text(f"#!/bin/sh\nshift\nexec '{sys.executable}' '{script}' \"$@\"\n")   # drop run_gepa.py
    stub.chmod(0o755)
    out = subprocess.run([str(SCRIPT), "gepa", "simpleqa", "run1"],
                         env=_env(world, tmp_path, MAX_RESTARTS="2"), capture_output=True, text=True, timeout=120)
    assert out.returncode == 1, out.stdout[-3000:] + out.stderr[-1000:]
    assert out.stdout.count("measurements invalid") == 2 and "series stopped" in out.stdout
    assert not (world["root"].parent / "gepa" / "simpleqa" / "run1" / "summary.json").exists()


def test_queue_stops_starting_series_after_its_deadline(world, tmp_path):  # noqa: F811
    # The queue gives up on the remaining series once its deadline passed, and still writes the report.
    out = subprocess.run([str(REPO / "experiments" / "vero" / "run_queue.sh")],
                         env=_env(world, tmp_path, QUEUE_DEADLINE_H="0"), capture_output=True, text=True, timeout=120)
    assert out.returncode == 1, out.stdout[-2000:] + out.stderr[-1000:]
    assert out.stdout.count("skipped, the queue deadline") == 4
    assert (world["root"].parent / "report.md").exists()
