#!/usr/bin/env python3
"""ALW run command: one Harbor trial of Claude Code on a task, started through the harness.

How the harness folder gets control: the agent image's ``claude`` dispatcher hands every
call to ``/opt/harness/bin/mcr_entrypoint.py`` when that file exists. This command mounts
``container/`` there, so Harbor's call of ``claude ... --print`` reaches our entry
(``container/mcr_entrypoint.py``), which runs the harness's ``start`` with the same
arguments and the task instruction on stdin, stops it when the agent's time is up, and
copies the workspace to the trial's agent log folder.

Time: the agent gets the task's own limit (``task.toml`` ``agent.timeout_sec``), or less
when HB_SECONDS is shorter: ``HB_SECONDS - reserve_seconds``. The reserve covers building
the task environment, starting it and collecting the output.

Solver settings (HB_SETTING_*): ``reasoning_effort`` and ``max_output_tokens``. They are
written to a read-only file in the container and applied by ``container/claude``, the
program ``start`` is told to use for launching Claude Code ($HB_CLAUDE). A harness that
launched the product some other way would escape them; nothing here can prevent that.

Output (HB_OUT): ``submission/`` (what is scored), ``harbor/`` (the whole Harbor job:
agent log, trajectory, the workspace copy under ``.../agent/hb/workspace``), ``hb/``
(what was fixed for this run), ``meta.json``.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import common as c

MODEL_SLOTS = ("ANTHROPIC_MODEL", "ANTHROPIC_SMALL_FAST_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL",
               "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL",
               "CLAUDE_CODE_SUBAGENT_MODEL", "CLAUDE_CODE_AUTO_MODE_MODEL",
               "CLAUDE_CODE_BG_CLASSIFIER_MODEL")
FIXED_ENV = {"DISABLE_NON_ESSENTIAL_MODEL_CALLS": "1", "CLAUDE_CODE_TWO_STAGE_CLASSIFIER": "false",
             "API_TIMEOUT_MS": "3000000"}
KNOWN_SETTINGS = ("reasoning_effort", "max_output_tokens")
KEY_ENV = "ALW_RUN_KEY"  # the run's model key travels in Harbor's environment, not in a file
MIN_AGENT_SECONDS = 60


def q(s) -> str:
    return json.dumps(str(s))


def main() -> int:
    s = c.site()
    task, out = c.need("HB_TASK"), Path(c.need("HB_OUT"))
    harness = Path(c.need("HB_HARNESS")).resolve()
    model, url, key = c.need("HB_MODEL"), c.need("HB_MODEL_URL"), c.need("HB_MODEL_KEY")
    settings = {k[len("HB_SETTING_"):]: v for k, v in os.environ.items()
                if k.startswith("HB_SETTING_")}
    unknown = sorted(set(settings) - set(KNOWN_SETTINGS))
    if unknown:
        c.die(f"solver settings this scenario cannot apply: {unknown}; known: {list(KNOWN_SETTINGS)}")

    toml = c.task_toml(s, task)
    task_seconds = float(toml["agent"]["timeout_sec"])
    limit = os.environ.get("HB_SECONDS")
    agent_seconds = task_seconds
    if limit:
        agent_seconds = min(task_seconds, float(limit) - float(s["reserve_seconds"]))
    meta = {"task": task, "model": model, "settings": settings, "run_limit_seconds": limit,
            "task_agent_seconds": task_seconds, "agent_seconds": agent_seconds,
            "reserve_seconds": s["reserve_seconds"], "agent_image": s["agent_image"]}
    if agent_seconds < MIN_AGENT_SECONDS:
        (out / "meta.json").write_text(json.dumps(meta | {"refused": "time limit too short"}, indent=1))
        c.die(f"a run needs more than {s['reserve_seconds']} + {MIN_AGENT_SECONDS} seconds: "
              f"{s['reserve_seconds']} s are kept for setting up the task environment and "
              "collecting the output")

    name = f"hb-{os.getpid()}-{int(time.time())}"
    fixed = out / "hb"
    fixed.mkdir()
    (fixed / "fixed.json").write_text(json.dumps(
        {"agent_seconds": agent_seconds, "settings": settings,
         "real_claude": "/opt/claude-code/bin/claude"}, indent=1))
    overlay = fixed / "compose.yaml"
    overlay.write_text("\n".join([
        "services:", "  main:",
        "    extra_hosts:", f"      - {q('host.docker.internal:host-gateway')}",
        "    environment:", f"      NO_PROXY: {q(c.NO_PROXY)}", f"      no_proxy: {q(c.NO_PROXY)}",
        "    labels:", f"      hb.run: {q(name)}",
        "    volumes:",
        f"      - {q(str(c.HERE / 'container') + ':/opt/harness/bin:ro')}",
        f"      - {q(str(fixed) + ':/opt/hb:ro')}",
        f"      - {q(str(harness) + ':/opt/hb-harness:ro')}",
        "    build:", "      args:", f"        ALW_BASE_IMAGE: {q(s['agent_image'])}", ""]))

    env = {slot: model for slot in MODEL_SLOTS} | FIXED_ENV | {
        "ANTHROPIC_BASE_URL": url, "ANTHROPIC_API_KEY": "${" + KEY_ENV + "}",
        "HB_MODEL_URL": url, "HB_MODEL_KEY": "${" + KEY_ENV + "}", "HB_MODEL": model,
        "NO_PROXY": c.NO_PROXY, "no_proxy": c.NO_PROXY}
    kwargs = {"version": s["claude_code_version"]}
    harbor_env = {KEY_ENV: key, "HARBOR_TELEMETRY": "0"}
    if "reasoning_effort" in settings:
        kwargs["reasoning_effort"] = settings["reasoning_effort"]
    if "max_output_tokens" in settings:
        env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = settings["max_output_tokens"]
        harbor_env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = settings["max_output_tokens"]
    job = {"job_name": name, "jobs_dir": str(out / "harbor"), "n_attempts": 1,
           "n_concurrent_trials": 1, "retry": {"max_retries": 0},
           "environment": {"type": "docker", "delete": True,
                           "extra_docker_compose": [str(overlay)]},
           "agents": [{"name": "claude-code", "model_name": model, "kwargs": kwargs, "env": env,
                       "override_timeout_sec": agent_seconds + float(s["stop_grace_seconds"])}],
           "tasks": [{"path": str(c.task_dir(s, task))}]}
    for k, v in (s.get("job_extra") or {}).items():
        job[k] = v
    cfg = fixed / "harbor_job.json"
    cfg.write_text(json.dumps(job, indent=1))

    stopped = {"signal": None}
    proc = subprocess.Popen([s["harbor"], "run", "--config", str(cfg)], env=c.host_env(harbor_env),
                            stdin=subprocess.DEVNULL)

    def on_signal(signum, _frame):
        stopped["signal"] = signum
        try:
            proc.send_signal(signal.SIGTERM)
        except OSError:
            pass

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    started = time.monotonic()
    while True:
        try:
            rc = proc.wait(timeout=5)
            break
        except subprocess.TimeoutExpired:
            if stopped["signal"] is not None:
                # Stopped from outside (the run command's own time limit): everything in this
                # process group is killed within seconds. Leave a detached cleaner behind that
                # removes the trial's container and hands the files back, and say what happened.
                (out / "meta.json").write_text(json.dumps(
                    meta | {"stopped_by_signal": stopped["signal"], "agent_started": None}, indent=1))
                subprocess.Popen(
                    [sys.executable, "-B", str(c.HERE / "common.py"), "cleanup", f"hb.run={name}", str(out)],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    start_new_session=True)
                return 1
    c.remove_containers(f"hb.run={name}")
    c.chown_back(s, out)

    agent_dirs = sorted((out / "harbor").glob("**/agent/hb"))
    agent = None
    if agent_dirs:
        try:
            agent = json.loads((agent_dirs[0] / "run.json").read_text())
        except (OSError, ValueError):
            agent = None
        sub = agent_dirs[0] / "workspace" / "submission"
        if sub.is_dir():
            shutil.copytree(sub, out / "submission", symlinks=True)
    meta |= {"harbor_exit_code": rc, "harbor_seconds": round(time.monotonic() - started, 1),
             "stopped_by_signal": stopped["signal"], "agent": agent,
             "agent_started": agent is not None,
             "submission_present": (out / "submission" / "layout.gds").is_file()}
    (out / "meta.json").write_text(json.dumps(meta, indent=1))
    print(json.dumps({k: meta[k] for k in ("harbor_exit_code", "agent_started",
                                           "submission_present", "agent_seconds")}))
    return 0 if rc == 0 and agent is not None else 1


if __name__ == "__main__":
    sys.exit(main())
