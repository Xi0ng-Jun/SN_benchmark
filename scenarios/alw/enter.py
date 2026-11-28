#!/usr/bin/env python3
"""ALW enter command: run one command in a task's environment, without an agent.

The environment is the task's own image (``tasks/<task>/environment``, built on the agent
image, so the EDA tools and files are those the solving agent sees), with no network. The
exchange folder HB_WORK is mounted at ``/alb-workspace/hb`` and is the current directory;
the task's files are one level up (``../task``, ``../GUIDE.md``, ``../DEVICE_API.md``,
``../eda_env.sh``).
"""
import os
import re
import signal
import subprocess
import sys
from pathlib import Path

import common as c

s = c.site()
task, work = c.need("HB_TASK"), Path(c.need("HB_WORK")).resolve()
command = sys.argv[1:]
if not command:
    c.die("enter: no command given")
toml = c.task_toml(s, task)
environment = toml.get("environment") or {}
tag = "hb-alw-env:" + re.sub(r"[^a-z0-9_.-]", "-", task.lower())[:100]
build = subprocess.run(["docker", "build", "-q", "-t", tag, "--build-arg",
                        f"ALW_BASE_IMAGE={s['agent_image']}",
                        str(c.task_dir(s, task) / "environment")],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                       text=True)
if build.returncode != 0:
    sys.stderr.write(build.stderr[-4000:])
    c.die("enter: building the task environment failed", 125)
name = f"hb-enter-{os.getpid()}"
argv = ["docker", "run", "--rm", "--init", "--name", name, "--network", "none",
        "--label", f"hb.enter={name}", "-v", f"{work}:/alb-workspace/hb", "-w", "/alb-workspace/hb"]
if environment.get("cpus"):
    argv += ["--cpus", str(environment["cpus"])]
if environment.get("memory_mb"):
    argv += ["--memory", f"{int(environment['memory_mb'])}m"]
for k, v in (environment.get("env") or {}).items():
    argv += ["-e", f"{k}={v}"]
argv += [tag, *command]
proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, env=c.host_env())


def stop(_signum, _frame):  # the container is not in our process group: remove it ourselves
    subprocess.Popen(["docker", "rm", "-f", name], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
rc = proc.wait()
c.chown_back(s, work)
sys.exit(rc)
