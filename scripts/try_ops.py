#!/usr/bin/env python3
"""Try one operation of a scenario by hand, outside any development session.

    PYTHONPATH=src python3 scripts/try_ops.py SCENARIO STATE enter TASK COMMAND...
    PYTHONPATH=src python3 scripts/try_ops.py SCENARIO STATE score TASK PRODUCT_DIR
    PYTHONPATH=src python3 scripts/try_ops.py SCENARIO STATE run   TASK [SECONDS]

For checking that a new scenario's commands work. Records go under ``try/`` of STATE and
model use to a pool of its own; use a state directory kept for this purpose, not the one
of a real development. ``run`` uses harness version v0 and practice tasks only.
"""
import json
import sys
from pathlib import Path

from harness_builder import scenario as sc
from harness_builder.gateway import Gateway
from harness_builder.runs import Runner
from harness_builder.state import open_state


def main(argv) -> int:
    if len(argv) < 4 or argv[2] not in ("enter", "score", "run"):
        sys.stderr.write(__doc__)
        return 2
    state = open_state(argv[1], sc.load(argv[0]))
    op, task, rest = argv[2], argv[3], argv[4:]
    practice = state.scenario.practice
    if op == "enter":
        work = state.root / "try" / "work"
        work.mkdir(parents=True, exist_ok=True)
        runner = Runner(state, "try", practice)
        rec = runner.enter(task, rest, work)
        d = runner.enter_dir(rec["enter"])
        sys.stdout.write((d / "enter.stdout").read_text(errors="replace"))
        sys.stderr.write((d / "enter.stderr").read_text(errors="replace"))
        print(json.dumps(rec["enter_result"], default=str))
        return 0
    if op == "score":
        runner = Runner(state, "try", practice)
        rec = runner.score(task, Path(rest[0]))
        d = runner.score_dir(rec["score"])
        print(json.dumps({"record": rec, "dir": str(d),
                          "score": state.scenario.score_value(d / "score")}, indent=1, default=str))
        return 0
    with Gateway.from_scenario(state.scenario, log_path=state.root / "model-requests.jsonl") as gw:
        runner = Runner(state, "try", practice, gateway=gw, pool="try")
        rec = runner.run(task, "v0", seconds=float(rest[0]) if rest else None)
    d = runner.run_dir(rec["run"])
    print(json.dumps({"record": rec, "dir": str(d),
                      "score": state.scenario.score_value(d / "score")}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
