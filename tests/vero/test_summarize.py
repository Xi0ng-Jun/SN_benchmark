"""experiments/vero/summarize.py: tables from finished runs only, numbers taken from the run files."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "experiments" / "vero" / "summarize.py"


def _v41(root: Path, run: str, seed: float, final: float, finished: bool = True) -> None:
    d = root / "runs" / run
    d.mkdir(parents=True)
    rec = lambda stage: dict(stage=stage)  # noqa: E731
    (d / "state.json").write_text(json.dumps(dict(
        halt_reason="budget_exhausted" if finished else None, current_round=None,
        rounds=[dict(records={"0": rec("measured"), "1": rec("error")}), dict(records={"0": rec("measured")})],
        lineage=[dict(kind="seed"), dict(kind="candidate", delta_val=0.1)])))
    (d / "identity.json").write_text(json.dumps(dict(max_executions=960)))
    if finished:
        (d / "endpoint.json").write_text(json.dumps(dict(summary=dict(
            seed_score=seed, final_score=final, delta=final - seed, se=0.03, rounds=2, adopted=1,
            halt_reason="budget_exhausted", elapsed_h=1.5, optimisation_executions=dict(train=130, validation=800)))))


def _gepa(root: Path, run: str, seed: float, final: float) -> None:
    d = root / run
    d.mkdir(parents=True)
    (d / "summary.json").write_text(json.dumps(dict(final_test_score=final, wall_h=2.0)))
    done = "INFO vero.evaluator: Task completed with metrics: {'num_samples': %d, 'num_errors': 0}"
    (d / "gepa.log").write_text("\n".join([
        done % 115, f"INFO vero_benchmarking.gepa: Initial test score: {seed:.4f}", done % 20, done % 32,
        "INFO vero.evaluator: Task completed with metrics: None", done % 32, done % 115]) + "\n")


def test_tables_use_finished_runs_only(tmp_path):
    retail = tmp_path / "retail"
    retail.mkdir()
    (retail / "setup.json").write_text(json.dumps(dict(splits=dict(train=100, validation=20, test=115),
                                                       models=dict(target="openai/m"))))
    _v41(retail, "run1", 0.50, 0.60)
    _v41(retail, "run2", 0.52, 0.56)
    _v41(retail, "run3", 0.50, 0.50, finished=False)
    _gepa(tmp_path / "gepa" / "tau-bench", "run1", 0.48, 0.55)
    (tmp_path / "gepa" / "tau-bench" / "run1.failed-20261112T000000Z").mkdir()
    (tmp_path / "gepa" / "tau-bench" / "run2").mkdir()
    out = subprocess.run([sys.executable, str(SCRIPT), "--state", str(tmp_path)], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    text = out.stdout
    row = lambda name: next(line for line in text.splitlines() if line.startswith(f"| {name}"))  # noqa: E731
    # seed: the three finished runs' own seed evaluations (0.50, 0.52, 0.48)
    assert "0.500 ± 0.020 (n=3)" in row("种子")
    assert "0.580 ± 0.028 (n=2)" in row("method-v4.1") and "+0.070 ± 0.042" in row("method-v4.1")
    assert "930 / 960" in row("method-v4.1")
    assert "0.550 (n=1)" in row("GEPA") and "+0.070" in row("GEPA") and "84 / 960" in row("GEPA")   # 20 + 32 + 0 + 32
    assert "| Retail | run1 | 2 | 3 | 2 | 1 | +0.100 | 0.500 → 0.600 | +0.100 ± 0.030 | 930 | 1.5 | budget_exhausted |" in text
    assert "method-v4.1 retail run3" in text and "GEPA tau-bench run2: not finished" in text and "failed-2026" not in text
