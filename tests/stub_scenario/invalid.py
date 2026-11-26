"""Stub invalid-run check: a run is invalid when the agent log says the model was unreachable.

Exit 0: valid. Exit 10 with the reason on stdout: invalid.
"""
import os
import sys

try:
    with open(os.path.join(os.environ["HB_RUN"], "out", "agent.log")) as f:
        log = f.read()
except OSError:
    sys.exit(0)
if "model unreachable" in log:
    print("the model service was unreachable during the run")
    sys.exit(10)
