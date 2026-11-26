"""hb: the developer agent's command inside the sandbox. Standalone, standard library only.

Every command writes a request into the request directory and (unless --no-wait)
waits for the outside program's reply, which it prints as JSON. Exit status 0
means the request succeeded, 1 that it was refused or failed, 2 a usage error.

  hb run TASK [--version V | --harness DIR] [--no-wait]
  hb score TASK PRODUCT_DIR [--no-wait]
  hb enter [--work DIR] TASK -- COMMAND [ARG...]
  hb ask --question Q --why W --assumption A
  hb deliver [--version V | --harness DIR]
  hb status
  hb wait REQUEST_ID [--timeout SECONDS]

DIR and PRODUCT_DIR must lie inside the workspace ($HB_WORKSPACE); without
--version or --harness, run and deliver use $HB_WORKSPACE/harness as it is now.
"""
import argparse
import json
import os
import secrets
import sys
import time

POLL_SECONDS = 0.2


def _env(name):
    v = os.environ.get(name)
    if not v:
        sys.exit("hb: %s is not set" % name)
    return v


def _workspace_rel(path):
    ws = os.path.realpath(_env("HB_WORKSPACE"))
    p = os.path.realpath(path)
    if p != ws and not p.startswith(ws + os.sep):
        sys.exit("hb: %s is not inside the workspace %s" % (path, ws))
    return os.path.relpath(p, ws)


def submit(op, args):
    ops = _env("HB_OPS")
    rid = "%s-%s" % (time.strftime("%Y%m%dT%H%M%S"), secrets.token_hex(4))
    tmp = os.path.join(ops, "requests", ".tmp-" + rid)
    with open(tmp, "w") as f:
        json.dump({"op": op, "args": args}, f)
    os.rename(tmp, os.path.join(ops, "requests", rid + ".json"))
    return rid


def wait(rid, timeout=None):
    path = os.path.join(_env("HB_OPS"), "responses", rid + ".json")
    deadline = None if timeout is None else time.monotonic() + timeout
    while not os.path.exists(path):
        if deadline is not None and time.monotonic() > deadline:
            return None
        time.sleep(POLL_SECONDS)
    with open(path) as f:
        return json.load(f)


def main(argv=None):
    p = argparse.ArgumentParser(prog="hb", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run a harness version on a practice task, then score it")
    r.add_argument("task")
    g = r.add_mutually_exclusive_group()
    g.add_argument("--version")
    g.add_argument("--harness")
    r.add_argument("--no-wait", action="store_true")
    s = sub.add_parser("score", help="apply the official scoring to a product directory")
    s.add_argument("task")
    s.add_argument("product")
    s.add_argument("--no-wait", action="store_true")
    e = sub.add_parser("enter", help="run a command in a practice task's environment")
    e.add_argument("task")
    e.add_argument("--work", help="exchange directory in the workspace "
                                  "(default: $HB_WORKSPACE/enter/TASK)")
    e.add_argument("command", nargs=argparse.REMAINDER)
    a = sub.add_parser("ask", help="ask the researcher a question (does not block)")
    a.add_argument("--question", required=True)
    a.add_argument("--why", required=True, help="why the materials and runs cannot answer it")
    a.add_argument("--assumption", required=True, help="what you assume until it is answered")
    d = sub.add_parser("deliver", help="name a harness version as the delivery")
    g = d.add_mutually_exclusive_group()
    g.add_argument("--version")
    g.add_argument("--harness")
    sub.add_parser("status", help="budget, time, versions, questions, deliveries")
    w = sub.add_parser("wait", help="wait for the reply to an earlier request")
    w.add_argument("request_id")
    w.add_argument("--timeout", type=float)
    ns = p.parse_args(argv)

    if ns.cmd == "wait":
        reply = wait(ns.request_id, ns.timeout)
        if reply is None:
            print(json.dumps({"id": ns.request_id, "ok": False, "error": "no reply yet"}))
            return 1
        print(json.dumps(reply, indent=1))
        return 0 if reply.get("ok") else 1

    args, no_wait = {}, getattr(ns, "no_wait", False)
    if ns.cmd in ("run", "deliver"):
        if ns.version:
            args["version"] = ns.version
        else:
            args["harness"] = _workspace_rel(
                ns.harness or os.path.join(_env("HB_WORKSPACE"), "harness"))
    if ns.cmd in ("run", "score", "enter"):
        args["task"] = ns.task
    if ns.cmd == "score":
        args["product"] = _workspace_rel(ns.product)
    if ns.cmd == "enter":
        command = ns.command[1:] if ns.command[:1] == ["--"] else ns.command
        if not command:
            p.error("enter: give the command after --")
        args["command"] = command
        if ns.work:
            args["work"] = _workspace_rel(ns.work)
    if ns.cmd == "ask":
        args.update(question=ns.question, why=ns.why, assumption=ns.assumption)

    rid = submit(ns.cmd, args)
    if no_wait:
        print(json.dumps({"id": rid, "submitted": True}))
        return 0
    reply = wait(rid)
    print(json.dumps(reply, indent=1))
    return 0 if reply.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
