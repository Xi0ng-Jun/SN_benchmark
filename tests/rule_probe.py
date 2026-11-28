"""Run inside a container sandbox by test_rules.py: tries what rules 1 and 2 forbid and
writes what happened to $HB_WORKSPACE/probe.json. Standard library only."""
import json
import os
import socket
import subprocess

WS = os.environ["HB_WORKSPACE"]
with open(os.path.join(WS, "probe-input.json")) as f:
    given = json.load(f)
found = {}

# rule 1: exam tasks are nowhere to be seen
found["tasks"] = sorted(os.listdir(os.environ["HB_TASKS"]))
found["host_paths_present"] = [p for p in given["host_paths"] if os.path.exists(p)]
leaks = []
for var in ("HB_TASKS", "HB_RECORDS", "HB_VERSIONS", "HB_MATERIALS", "HB_WORKSPACE", "HB_OPS"):
    for dirpath, _, names in os.walk(os.environ[var]):
        for name in names:
            path = os.path.join(dirpath, name)
            if name == "probe-input.json":
                continue
            try:
                with open(path, "rb") as f:
                    data = f.read(1 << 20)
            except OSError:
                continue
            if any(m.encode() in data for m in given["exam_markers"]):
                leaks.append(path)
found["exam_content_found"] = leaks

# rule 1: no network
try:
    socket.create_connection(("1.1.1.1", 53), timeout=3).close()
    found["connect"] = "connected"
except OSError as e:
    found["connect"] = "failed: %s" % e
try:
    socket.getaddrinfo("example.com", 80)
    found["resolve"] = "resolved"
except OSError as e:
    found["resolve"] = "failed: %s" % e

# rule 2: the raw score output of a run cannot be changed from here
p = subprocess.run(["hb", "run", "p1", "--version", "v0"], stdout=subprocess.PIPE,
                   universal_newlines=True)
score_dir = os.path.join(json.loads(p.stdout)["result"]["run_dir"], "score")
target = os.path.join(score_dir, "score.json")
with open(target) as f:
    before = f.read()


def overwrite():
    with open(target, "w") as f:
        f.write("{}")


def add():
    open(os.path.join(score_dir, "extra.json"), "w").close()


attempts = {}
for name, action in (("overwrite", overwrite), ("chmod", lambda: os.chmod(target, 0o666)),
                     ("chmod_dir", lambda: os.chmod(score_dir, 0o777)),
                     ("overwrite_after_chmod", overwrite), ("remove", lambda: os.remove(target)),
                     ("add", add),
                     ("rename", lambda: os.rename(target, target + ".old"))):
    try:
        action()
        attempts[name] = "done"
    except OSError as e:
        attempts[name] = "refused: %s" % e
found["score_changes"] = attempts
try:
    with open(target) as f:
        found["score_unchanged"] = f.read() == before
except OSError:
    found["score_unchanged"] = False
found["score_dir"] = sorted(os.listdir(score_dir))

with open(os.path.join(WS, "probe.json"), "w") as f:
    json.dump(found, f, indent=1)
