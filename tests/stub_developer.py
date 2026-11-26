"""Stand-in for a developer agent: no reasoning, a fixed sequence of operations.

Runs inside the sandbox (copied into the workspace by the test). It changes one
thing in the harness, runs, scores, enters an environment, asks a question, waits
for the answer, and delivers; it also tries things that must fail. Every step is
written to $HB_WORKSPACE/dev-log.json; the exit status is 0 only if every step went
as expected. Standard library only, old-Python-friendly syntax.
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

WS = os.environ["HB_WORKSPACE"]
LOG = []


def step(name, ok, **detail):
    detail.update(step=name, ok=bool(ok))
    LOG.append(detail)


def hb(*args):
    p = subprocess.run(["hb"] + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       universal_newlines=True, timeout=300)
    try:
        out = json.loads(p.stdout)
    except ValueError:
        out = {"raw": p.stdout}
    out["_stderr"] = p.stderr[-2000:]
    return p.returncode, out


def score_of(reply):
    try:
        return json.loads(reply["result"]["score_output"]["score.json"])["score"]
    except (KeyError, TypeError, ValueError):
        return None


def model_call(model):
    body = json.dumps({"model": model, "max_tokens": 8,
                       "messages": [{"role": "user", "content": "hello"}]}).encode()
    req = urllib.request.Request(os.environ["HB_MODEL_URL"].rstrip("/") + "/v1/messages",
                                 data=body, headers={"content-type": "application/json",
                                                     "x-api-key": os.environ["HB_MODEL_KEY"]})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except OSError as e:
        return "error: %s" % e


def main():
    with open(os.environ["HB_BRIEF"]) as f:
        brief = f.read()
    step("read brief", "MANIFEST.md" in brief and os.environ["HB_WORKSPACE"] in brief)

    status = model_call(os.environ["HB_MODEL"])
    step("call own model", status == 200, status=status)
    status = model_call("stub-solver")
    step("other model refused", status == 403, status=status)

    # change one thing: the solver answers correctly and calls the model once
    harness = os.path.join(WS, "harness")
    with open(os.path.join(harness, "settings.json"), "w") as f:
        json.dump({"answer": "correct", "model_calls": 1}, f)
    with open(os.path.join(harness, "MANIFEST.md"), "a") as f:
        f.write("- settings.json now says `correct`: run r0001 of v0 scored 0.\n")
    step("edit harness", True)

    code, out = hb("run", "p1")
    step("run p1", code == 0 and score_of(out) == 1.0, code=code, reply=out)
    run_id = out.get("result", {}).get("record", {}).get("run")

    code, out = hb("run", "p2", "--no-wait")
    rid = out.get("id")
    code, out = hb("wait", rid or "none", "--timeout", "120")
    step("run p2 in background", code == 0 and score_of(out) == 1.0, code=code, reply=out)

    product = os.path.join(WS, "product")
    os.makedirs(product, exist_ok=True)
    with open(os.path.join(product, "answer.json"), "w") as f:
        json.dump({"shape": "square", "sides": 4, "closed": "no"}, f)
    code, out = hb("score", "p2", product)
    s = score_of(out)
    step("score own product", code == 0 and s is not None and abs(s - 2 / 3) < 1e-9,
         code=code, reply=out)

    copy = os.path.join(WS, "rescore")
    shutil.copytree(os.path.join(os.environ["HB_RECORDS"], "runs", str(run_id), "out"), copy)
    code, out = hb("score", "p1", copy)
    step("score copy of a run", code == 0 and score_of(out) == 1.0, code=code, reply=out)

    code, out = hb("enter", "p1", "--", "cat", "task.json")
    stdout = out.get("result", {}).get("stdout", "")
    step("enter p1", code == 0 and '"color": "red"' in stdout, code=code, reply=out)

    code, out = hb("ask", "--question", "Which answer format is preferred?",
                   "--why", "The materials do not say.", "--assumption", "JSON as in the task.")
    qid = out.get("result", {}).get("id")
    step("ask", code == 0 and qid, code=code, reply=out)

    answer_text = None
    deadline = time.time() + 60
    while time.time() < deadline and answer_text is None:
        code, out = hb("status")
        for q in out.get("result", {}).get("questions", []):
            if q.get("id") == qid and q.get("answers"):
                with open(os.path.join(q["answers"][0], "answer.md")) as f:
                    answer_text = f.read()
        time.sleep(0.5)
    step("answer arrived", answer_text is not None and "keep JSON" in answer_text,
         answer=answer_text)

    code, out = hb("deliver")
    step("deliver", code == 0 and out.get("result", {}).get("version"), code=code, reply=out)

    # things that must not work
    code, out = hb("run", "e1")
    step("exam task refused", code == 1 and "not a practice task" in out.get("error", ""),
         reply=out)
    step("no exam tasks visible", sorted(os.listdir(os.environ["HB_TASKS"])) == ["p1", "p2", "p3"],
         seen=sorted(os.listdir(os.environ["HB_TASKS"])))
    raw = os.path.join(os.environ["HB_RECORDS"], "runs", str(run_id), "score", "score.json")
    try:
        with open(raw, "w") as f:
            f.write("{}")
        step("raw score output read-only", False)
    except OSError as e:
        step("raw score output read-only", True, error=str(e))
    code, out = hb("score", "p1", "/etc")
    step("path outside workspace refused", code != 0, code=code)
    if os.environ.get("HB_SANDBOX") == "container":
        try:
            socket.create_connection(("1.1.1.1", 53), timeout=3).close()
            step("no network", False)
        except OSError as e:
            step("no network", True, error=str(e))

    with open(os.path.join(WS, "dev-log.json"), "w") as f:
        json.dump(LOG, f, indent=1)
    return 0 if all(s["ok"] for s in LOG) else 1


if __name__ == "__main__":
    sys.exit(main())
