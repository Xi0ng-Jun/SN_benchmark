"""experiments/vero/gate.sh offline: services skipped, a stub CLI for the probe, a synthetic LiteLLM log."""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
GATE = REPO / "experiments" / "vero" / "gate.sh"
PROBE = dict(commit="abc", tasks=12, scored=12, k=1, mean_score=0.7, answered_rate=0.8, per_attempt=[0.7])


def _env(tmp_path: Path, payload: dict, **extra) -> dict:
    wrapper = tmp_path / "python"
    wrapper.write_text(
        '#!/bin/sh\n'
        'if [ "$1" = "-m" ] && [ "$2" = "rag_eval.vero.cli" ]; then printf \'%s\' "$FAKE_PROBE"; exit 0; fi\n'
        f'exec "{sys.executable}" "$@"\n')
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)
    root = tmp_path / "state"
    (root / "retail").mkdir(parents=True)
    (root / "retail" / "setup.json").write_text(json.dumps(dict(splits=dict(train=1, validation=2, test=3))))
    env = dict(os.environ, VERO_ENV_LOCAL="/dev/null", VERO_ROOT=str(tmp_path), VERO_STATE=str(root),
               VERO_PYTHON=str(wrapper), VERO_TARGET_MODEL="openai/t", VERO_TARGET_API_BASE="http://127.0.0.1:9/v1",
               VERO_TARGET_API_KEY="x", VERO_GRADER_MODEL="openai/g", VERO_OPTIMIZER_MODEL="openai/o",
               GATE_SKIP_SERVICES="1", GATE_PROBE_N="12", GATE_QUIET_MIN="15", GATE_FLOOR="0.25",
               FAKE_PROBE=json.dumps(payload), PYTHONPATH=str(REPO / "src"))
    env.update(extra)
    return env


def test_gate_passes_a_healthy_probe(tmp_path):
    out = subprocess.run([str(GATE), "retail"], env=_env(tmp_path, PROBE), capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "gate probe" in out.stdout and "12/12 scored" in out.stdout


def test_gate_refuses_a_dead_probe(tmp_path):
    out = subprocess.run([str(GATE), "retail"], env=_env(tmp_path, dict(PROBE, mean_score=0.0, answered_rate=0.0)),
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 1
    assert "the services were not healthy" in out.stdout


def test_gate_refuses_after_recent_upstream_errors(tmp_path):
    env = _env(tmp_path, PROBE)
    stamp = time.strftime("%H:%M:%S", time.localtime())   # LiteLLM logs in local time
    (tmp_path / "state" / "litellm.log").write_text(f"{stamp} - LiteLLM Proxy:ERROR:boom\n")
    out = subprocess.run([str(GATE), "retail"], env=env, capture_output=True, text=True, timeout=60)
    assert out.returncode == 1
    assert "upstream error(s)" in out.stdout
