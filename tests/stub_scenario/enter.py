"""Stub enter command. The task environment is HB_WORK with the task statement copied in.

Executes its arguments there and exits with their exit code.
"""
import os
import shutil
import subprocess
import sys

if len(sys.argv) < 2:
    print("usage: enter.py COMMAND [ARG...]", file=sys.stderr)
    sys.exit(2)
task, work = os.environ["HB_TASK"], os.environ["HB_WORK"]
shutil.copy(os.path.join("tasks", task, "task.json"), os.path.join(work, "task.json"))
try:
    sys.exit(subprocess.call(sys.argv[1:], cwd=work))
except OSError as e:
    print("enter: %s" % e, file=sys.stderr)
    sys.exit(127)
