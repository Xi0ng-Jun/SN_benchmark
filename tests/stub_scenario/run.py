"""Stub run command. The task environment is the output directory itself.

Copies the task statement into HB_OUT, then executes the harness's start command
there with only the model variables and the researcher's solver settings
(HB_SETTING_*) in its environment. The agent's output goes to agent.log; the exit
code is the agent's.

This stub has no agent product to apply the solver settings to. It writes the
settings it was given to HB_OUT/solver_settings.json after the agent has ended, in
place of anything the agent left under that name.
"""
import json
import os
import shutil
import subprocess
import sys

task, harness, out = os.environ["HB_TASK"], os.environ["HB_HARNESS"], os.environ["HB_OUT"]
shutil.copy(os.path.join("tasks", task, "task.json"), os.path.join(out, "task.json"))
env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
for k in ("HB_MODEL_URL", "HB_MODEL_KEY", "HB_MODEL"):
    env[k] = os.environ[k]
PREFIX = "HB_SETTING_"
settings = {k[len(PREFIX):]: v for k, v in os.environ.items() if k.startswith(PREFIX)}
env.update((PREFIX + k, v) for k, v in settings.items())
with open(os.path.join(out, "agent.log"), "wb") as log:
    code = subprocess.call([os.path.join(harness, "start")], cwd=out, env=env,
                           stdout=log, stderr=subprocess.STDOUT)
path = os.path.join(out, "solver_settings.json")
if os.path.isdir(path) and not os.path.islink(path):
    shutil.rmtree(path)
elif os.path.lexists(path):
    os.remove(path)
with open(path, "w") as f:
    json.dump(settings, f, sort_keys=True)
print("run: agent exited with %d" % code)
sys.exit(code)
