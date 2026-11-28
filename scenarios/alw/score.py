#!/usr/bin/env python3
"""ALW score command: the benchmark's own verifier on ``<product>/submission``.

Runs ``alw verify`` exactly as the benchmark documents it; its whole output goes to
``HB_OUT/verify``. The official score is ``verify/verifier/evaluation/metrics.json`` ->
``score``. ``alw verify`` ends with 0 (design passed) or 1 (design failed): both are
completed scorings, so both end this command with 0.
"""
import subprocess
import sys
from pathlib import Path

import common as c

s = c.site()
task, product, out = c.need("HB_TASK"), Path(c.need("HB_PRODUCT")), Path(c.need("HB_OUT"))
c.task_dir(s, task)
sub = product / "submission"
if not sub.is_dir():  # nothing was handed in: the verifier is given an empty folder
    sub = out / "no_submission"
    sub.mkdir()
cmd = [s["alw"], "--repository", s["alw_repo"], "verify", "--task", task,
       "--submission", str(sub.resolve()), "--out", str(out / "verify"),
       "--cpus", str(s["verify_cpus"]), "--memory", str(s["verify_memory"])]
print("score:", " ".join(cmd), flush=True)
rc = subprocess.run(cmd, cwd=s["alw_repo"], stdin=subprocess.DEVNULL).returncode
c.chown_back(s, out)
print(f"score: alw verify ended with {rc}")
sys.exit(0 if rc in (0, 1) else (rc if rc > 0 else 3))
