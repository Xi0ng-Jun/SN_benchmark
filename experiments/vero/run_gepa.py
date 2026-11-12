"""GEPA baseline on VeRO-Bench (paper-v1), exactly as the paper's run_benchmark.py calls it, except the model.

Run with the vero-benchmarking venv after ``source experiments/vero/env.sh`` (LiteLLM proxy and
Wikipedia proxy up):

    $VERO_ROOT/vero-benchmarking/.venv/bin/python experiments/vero/run_gepa.py --task simpleqa --run run1

The paper calls ``run_gepa(task_name, model)`` with the function's defaults (budget 8 x train + 8 x
validation sample executions, reflection minibatch 32, GEPA seed 0, initial and final test
evaluation). The only change: the reflection model is the proxy alias ``optimizer``
(``VERO_OPTIMIZER_MODEL``), the model our analyst and implementer use, and the VeRO checkout carries
``experiments/vero/patches/gepa-paper-v1.patch`` (paper-v1's gepa.py calls three vero functions with
signatures the tag no longer has, and its default logging hook needs a package web_search_agent lacks).
Output: ``--out/<task>/<run>/`` (GEPA run dir, ``summary.json``, ``gepa.log``); an unfinished attempt is
moved to ``<run>.failed-<time>`` and the run restarts from scratch.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--task", required=True, choices=("simpleqa", "tau-bench"))
    p.add_argument("--run", required=True, help="run label, e.g. run1")
    p.add_argument("--model-alias", default="optimizer", help="proxy model name used as GEPA's reflection model")
    p.add_argument("--out", default=str(Path(os.environ.get("VERO_STATE") or Path(__file__).resolve().parents[2]
                                             / ".vero-state") / "gepa"))
    args = p.parse_args()

    out = Path(args.out) / args.task / args.run
    if (out / "summary.json").exists():
        print(f"{out} already finished: {(out / 'summary.json').read_text()}")
        return 0
    if out.exists() and any(out.iterdir()):     # an unfinished attempt: GEPA restarts from scratch, keep its logs apart
        out.rename(out.with_name(f"{out.name}.failed-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"))
    out.mkdir(parents=True, exist_ok=True)
    os.chdir(out)                       # GEPA writes results/<run_name>/ relative to the cwd
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=[logging.StreamHandler(sys.stderr), logging.FileHandler(out / "gepa.log")], force=True)

    import vero_benchmarking.runner as runner
    from vero_benchmarking.gepa import run_gepa
    from vero_benchmarking.tasks import load_task

    runner.MODELS["deepseek"] = args.model_alias
    task = load_task(args.task)
    started = time.time()
    result = run_gepa(task_name=args.task, model="deepseek", enable_wandb=False)
    summary = dict(task=args.task, run=args.run, final_test_score=result["final_score"],
                   gepa_run_name=result["run_name"], vero_session_id=result["session_id"],
                   reflection_model=args.model_alias,
                   models={r: os.environ.get(f"VERO_{r.upper()}_MODEL") for r in ("target", "grader", "optimizer")},
                   train_budget=task.train_budget,
                   validation_budget=task.validation_budget, wall_h=(time.time() - started) / 3600,
                   finished_at=datetime.now(timezone.utc).isoformat())
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
