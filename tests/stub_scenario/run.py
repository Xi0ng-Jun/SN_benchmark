"""Stub run command. The task environment is the output directory itself.

Copies the task statement into HB_OUT, then executes the harness's start command
there with only the model variables in its environment. The agent's output goes
to agent.log; the exit code is the agent's.
"""
import os
import shutil
import subprocess
import sys

task, harness, out = os.environ["HB_TASK"], os.environ["HB_HARNESS"], os.environ["HB_OUT"]
shutil.copy(os.path.join("tasks", task, "task.json"), os.path.join(out, "task.json"))
env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
for k in ("HB_MODEL_URL", "HB_MODEL_KEY", "HB_MODEL"):
    env[k] = os.environ[k]
with open(os.path.join(out, "agent.log"), "wb") as log:
    code = subprocess.call([os.path.join(harness, "start")], cwd=out, env=env,
                           stdout=log, stderr=subprocess.STDOUT)
print("run: agent exited with %d" % code)
sys.exit(code)
