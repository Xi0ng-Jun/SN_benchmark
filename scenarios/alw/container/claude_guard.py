"""Apply the fixed solver settings of /opt/hb/fixed.json to one launch of Claude Code.

``reasoning_effort``: any ``--effort`` among the arguments is dropped and the fixed one is
given, on the command line and as CLAUDE_CODE_EFFORT_LEVEL. ``max_output_tokens``:
CLAUDE_CODE_MAX_OUTPUT_TOKENS. A setting the researcher did not fix is left as it came.
"""
import json
import os
import sys
import time

FIXED = "/opt/hb/fixed.json"  # read-only mount; not taken from the environment, which start controls


def apply(argv, env, settings):
    argv, env = list(argv), dict(env)
    effort = settings.get("reasoning_effort")
    if effort is not None:
        kept, skip = [], False
        for a in argv:
            if skip:
                skip = False
            elif a == "--effort":
                skip = True
            elif not a.startswith("--effort="):
                kept.append(a)
        argv = kept + ["--effort", effort]
        env["CLAUDE_CODE_EFFORT_LEVEL"] = effort
    cap = settings.get("max_output_tokens")
    if cap is not None:
        env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = cap
    return argv, env


def main():
    try:
        with open(FIXED) as f:
            fixed = json.load(f)
    except (OSError, ValueError):
        fixed = {}
    real = fixed.get("real_claude", "/opt/claude-code/bin/claude")
    argv, env = apply(sys.argv[1:], os.environ, fixed.get("settings") or {})
    try:  # one line per launch, next to the workspace copy
        d = os.environ.get("HB_LOG_DIR", "/logs/agent/hb")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "claude_launches.jsonl"), "a") as f:
            f.write(json.dumps({"t": round(time.time(), 3), "n_args": len(argv),
                                "settings": fixed.get("settings") or {}}) + "\n")
    except OSError:
        pass
    os.execve(real, [real, *argv], env)


if __name__ == "__main__":
    main()
