#!/usr/bin/env python3
"""ALW invalid-run condition (exit 10 = invalid, 0 = valid). A run is invalid when

- the agent never started (the task environment or Harbor failed before it), or made no
  model request;
- the verifier itself failed: no readable metrics, ``evaluation_error``, or status
  ``tool_error`` / ``interrupted``; a missing or broken submission is a task failure, not this;
- waiting for the model service took more than ``gateway_wait_share_max`` of the agent's time.

A run the developer agent cut short with ``--seconds`` is judged the same way.
"""
import json
import sys
from pathlib import Path

import common as c

s = c.site()
run = Path(c.need("HB_RUN"))


def read(p):
    try:
        return json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return None


def invalid(reason):
    print(reason)
    sys.exit(10)


meta, usage = read(run / "out" / "meta.json") or {}, read(run / "usage.json") or {}
if meta.get("refused"):
    sys.exit(0)  # the request itself was wrong; nothing to rerun
if not meta.get("agent_started"):
    invalid("the agent never started")
if not usage.get("requests"):
    invalid("the agent made no model request")
m = read(run / "score" / "verify" / "verifier" / "evaluation" / "metrics.json")
if not isinstance(m, dict):
    invalid("the verifier left no readable metrics.json")
status = m.get("status")
if m.get("evaluation_error") is True:
    invalid("verifier: evaluation_error")
if status in ("tool_error", "interrupted"):
    invalid(f"verifier: status {status}")
seconds = ((meta.get("agent") or {}).get("seconds")) or 0
if seconds and (usage.get("wait_seconds") or 0) / seconds > float(s["gateway_wait_share_max"]):
    invalid(f"waiting for the model service took {usage['wait_seconds']:.0f} s of {seconds:.0f} s")
sys.exit(0)
