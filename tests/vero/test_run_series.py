"""experiments/vero/run_series.sh on the offline fake VeRO: resume, the stuck-method end, endpoint, next run."""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
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
    return dict(os.environ, VERO_ENV_LOCAL="/dev/null", VERO_ROOT=str(tmp_path), VERO_STATE=str(world["root"].parent),
                VERO_PYTHON=str(wrapper), VERO_TARGET_API_BASE="http://127.0.0.1:9/v1", VERO_TARGET_API_KEY="x",
                VERO_SKIP_SERVICE_CHECK="1", V41_CONFIG=str(cfg), RESUME_WAIT="0", MAX_RESUMES="1",
                PYTHONPATH=str(REPO / "src"), **models, **extra)


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
