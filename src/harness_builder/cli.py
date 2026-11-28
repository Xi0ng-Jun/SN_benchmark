"""The researcher's command line for the outside program, on the host.

    python -m harness_builder --scenario DIR --state DIR COMMAND ...
    hb-host --scenario DIR --state DIR COMMAND ...      (when the package is installed)

The name ``hb`` is the developer agent's command inside the sandbox (client.py);
the host side is called ``hb-host`` so the two cannot be confused.

  develop [--sandbox container|local] [--materials DIR]...
                      start a development session, or continue with another one
  questions           list the developer agent's questions and their answers
  answer ID [TEXT] [--file FILE] [--by NAME]
                      answer a question; the answer becomes a new material
  add-material PATH [--note TEXT]
                      add a file or folder to the materials
  limits [KEY=JSON]...
                      show the limits, or change them in scenario.json (recorded;
                      a running session keeps the limits it started with)
  stop                stop the running development session; runs under way finish first
  status              what is on record so far
  deliver [--task TASK]
                      freeze the delivered version and try it on one practice task
  exam [--close]      hold the final exam or continue an interrupted one;
                      --close ends an interrupted one as it stands
  report              write and print the report
  verify              check the ledger, the materials and the harness versions

Exit status: 0 done, 1 refused or failed, 2 usage error. While a session runs,
``questions``, ``answer``, ``add-material``, ``limits``, ``status`` and ``stop`` are
used from another terminal. Materials and answers must not come from anyone who
has seen exam tasks, their answers or exam results (rule 5); once an exam has
started they are no longer accepted.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
from pathlib import Path

from . import delivery, exam, report
from . import scenario as sc
from .develop import DevSession, SessionRefused, make_sandbox
from .gateway import Gateway, GatewayError
from .harness_repo import HarnessError, tree_hash
from .ledger import LedgerError
from .materials import Materials, MaterialsError, Questions
from .runs import RunRefused
from .split_lock import SplitChanged
from .state import Busy, State, holder, open_state

PROG = "hb-host"
REQUEST_LOG = "model-requests.jsonl"  # the model exit's request log, in the state directory


class Refused(Exception):
    pass


ERRORS = (Refused, sc.ScenarioError, SplitChanged, LedgerError, GatewayError, SessionRefused,
          Busy, delivery.DeliveryError, exam.ExamRefused, MaterialsError, HarnessError,
          RunRefused)


def _open(ns) -> State:
    scenario = sc.load(ns.scenario)
    try:
        return open_state(ns.state, scenario)
    except ValueError as e:
        raise Refused(str(e)) from None


def _gateway(state: State) -> Gateway:
    return Gateway.from_scenario(state.scenario, log_path=state.root / REQUEST_LOG)


def _materials(state: State) -> tuple[Materials, Questions]:
    materials = Materials(state.root / "materials", state.ledger)
    return materials, Questions(state.root / "questions", state.ledger, materials)


def _before_exam(state: State, what: str) -> None:
    if state.ledger.last("exam_started") is not None:
        raise Refused(f"an exam has been started in this state directory; {what}")


def _print(obj) -> None:
    print(json.dumps(obj, indent=1))


def _on_term(signum, frame):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)  # one stop request is enough
    raise KeyboardInterrupt


def _develop(ns) -> int:
    state = _open(ns)
    _before_exam(state, "its development cannot continue")
    with _gateway(state) as gateway:
        session = DevSession(state, gateway, make_sandbox(state.scenario, ns.sandbox))
        known = {m["hash"] for m in session.materials.entries()}
        for folder in ns.materials:
            if not Path(folder).is_dir():
                raise Refused(f"{folder}: not a directory")
            if tree_hash(folder) not in known:  # the same folder given again adds nothing
                known.add(session.materials.add(
                    folder, note="given when a development session was started")["hash"])
        try:
            previous = signal.signal(signal.SIGTERM, _on_term)
        except ValueError:  # not in the main thread
            previous = None
        try:
            record = session.run()
        except KeyboardInterrupt:
            print(f"{PROG}: the development session was stopped", file=sys.stderr)
            return 1
        finally:
            if previous is not None:
                signal.signal(signal.SIGTERM, previous)
    _print(record)
    return 0


def _questions(ns) -> int:
    _print(_materials(_open(ns))[1].list())
    return 0


def _answer(ns) -> int:
    state = _open(ns)
    _before_exam(state, "answers are no longer accepted")
    if (ns.text is None) == (ns.file is None):
        raise Refused("give the answer as TEXT or with --file, not both")
    text = ns.text if ns.file is None else Path(ns.file).read_text()
    _print(_materials(state)[1].answer(ns.question_id, text, by=ns.by))
    return 0


def _add_material(ns) -> int:
    state = _open(ns)
    _before_exam(state, "materials are no longer accepted")
    _print(_materials(state)[0].add(ns.path, note=ns.note))
    return 0


def _limits(ns) -> int:
    state = _open(ns)  # an edit made by hand before this is recorded here
    path = state.scenario.root / sc.CONFIG_NAME
    config = json.loads(path.read_text())
    before = config["limits"]
    if not ns.changes:
        _print(before)
        return 0
    after = dict(before)
    for item in ns.changes:
        key, eq, text = item.partition("=")
        if not eq or key not in sc.LIMIT_KEYS:
            raise Refused(f"{item!r}: write KEY=VALUE with KEY one of {list(sc.LIMIT_KEYS)}")
        try:
            after[key] = json.loads(text)
        except ValueError:
            raise Refused(f"{item!r}: the value must be JSON: a number, null, or an object "
                          "of caps") from None
    sc.check_limits(after)
    if after == before:
        _print({"changed": {}, "limits": before})
        return 0
    config["limits"] = after
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(config, indent=2) + "\n")
    os.replace(tmp, path)
    state = _open(ns)  # writes the changed configuration onto the ledger
    _print({"changed": {k: {"before": before[k], "after": after[k]} for k in after
                        if after[k] != before[k]},
            "limits": after, "record": state.ledger.last("scenario")["seq"]})
    return 0


def _stop(ns) -> int:
    state = _open(ns)
    held = holder(state.root)
    if not held or held.get("what") != "development session" or not held.get("pid"):
        raise Refused("no development session is running")
    state.ledger.append("stop_requested", pid=held["pid"])
    os.kill(held["pid"], signal.SIGTERM)
    print(f"asked the development session (process {held['pid']}) to stop; "
          "runs under way finish first")
    return 0


def _status(ns) -> int:
    state = _open(ns)
    r = report.build(state)
    delivered = r["delivery"]["delivered"]
    _print({"in_use": holder(state.root), "ledger": r["ledger"],
            "materials_problems": r["materials_problems"],
            "development": r["development"], "costs": r["costs"],
            "questions": _materials(state)[1].list(),
            "delivery": {**r["delivery"], "delivered": delivered and {
                k: v for k, v in delivered.items() if k != "manifest"}},
            "exams": [{k: x[k] for k in ("exam", "status", "objects", "results")}
                      for x in r["exams"]],
            "exams_refused": r["exams_refused"]})
    return 0


def _deliver(ns) -> int:
    state = _open(ns)
    with _gateway(state) as gateway:
        record = delivery.check(state, gateway, task=ns.task)
    _print(record)
    return 0 if record["outcome"] == "passed" else 1


def _exam(ns) -> int:
    state = _open(ns)
    if ns.close:
        _print(exam.close(state))
        return 0
    with _gateway(state) as gateway:
        try:
            record = exam.run(state, gateway)
        except KeyboardInterrupt:
            print(f"{PROG}: the exam was interrupted; run 'exam' again to continue it",
                  file=sys.stderr)
            return 1
    _print(record)
    return 0


def _report(ns) -> int:
    json_path, text_path = report.write(_open(ns))
    print(text_path.read_text())
    print(f"{PROG}: written to {text_path} and {json_path}", file=sys.stderr)
    return 0


def _verify(ns) -> int:
    state = _open(ns)  # a ledger that does not verify stops here
    problems = {"ledger": state.ledger.verify(),
                "materials": _materials(state)[0].verify(),
                "harness_versions": [f"{v['version']}: does not match its recorded hash"
                                     for v in state.repo.versions()
                                     if not state.repo.verify(v["version"])]}
    _print(problems)
    return 1 if any(problems.values()) else 0


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=PROG, description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--scenario", required=True, help="the scenario directory")
    p.add_argument("--state", required=True,
                   help="the state directory of this development (outside the scenario "
                        "directory); created on first use")
    sub = p.add_subparsers(dest="cmd", required=True)

    def command(name, func, help):
        c = sub.add_parser(name, help=help)
        c.set_defaults(func=func)
        return c

    c = command("develop", _develop, "start or continue development")
    c.add_argument("--sandbox", choices=("container", "local"), default="container",
                   help="'local' gives no isolation and is for tests only")
    c.add_argument("--materials", action="append", default=[], metavar="DIR",
                   help="a folder to add to the materials first (skipped if already added)")
    command("questions", _questions, "list questions and answers")
    c = command("answer", _answer, "answer a question")
    c.add_argument("question_id")
    c.add_argument("text", nargs="?")
    c.add_argument("--file", help="read the answer from this file")
    c.add_argument("--by", default="researcher", help="who answers")
    c = command("add-material", _add_material, "add a file or folder to the materials")
    c.add_argument("path")
    c.add_argument("--note", default="")
    c = command("limits", _limits, "show or change the limits")
    c.add_argument("changes", nargs="*", metavar="KEY=JSON")
    command("stop", _stop, "stop the running development session")
    command("status", _status, "what is on record so far")
    c = command("deliver", _deliver, "freeze the delivered version and try it once")
    c.add_argument("--task", help="the practice task of the trial (default: the first one)")
    c = command("exam", _exam, "hold the final exam")
    c.add_argument("--close", action="store_true",
                   help="end an interrupted exam as it stands instead of continuing it")
    command("report", _report, "write and print the report")
    command("verify", _verify, "check ledger, materials and harness versions")
    return p


def main(argv=None) -> int:
    ns = _parser().parse_args(argv)
    try:
        return ns.func(ns)
    except ERRORS as e:
        print(f"{PROG}: {e}", file=sys.stderr)
        return 1
