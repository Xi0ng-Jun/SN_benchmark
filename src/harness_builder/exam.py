"""The final exam (rule 6), held exactly as the scenario's ``exam`` settings say.

Compared objects: the delivered harness (named ``delivered``) and every harness
folder under ``exam.compare``. Each object is run on every exam task ``repeats``
times, by one runner: the same run command, score command, limits, solver
settings and invalid-run condition for all of them. Each run is one of

- ``scored``:   the score command ended with exit code 0 and a number stands at
                the configured place; that number counts;
- ``no_score``: no official score and not invalid; ``failure_score`` counts;
- ``invalid``:  the researcher's invalid-run condition holds; the run does not
                count and is done again, at most ``invalid_reruns`` times.

An object's aggregate is ``exam.aggregate`` over the numbers that count; places
left without a valid run are listed next to it. This is the only place where the
outside program reads a score; the report copies what is recorded here.

At most ``exam.uses`` exams are ever started in one state directory. An exam that
was interrupted is continued (finished runs are kept, not repeated) and is not a
new use. The exam starts only if the delivered version passed its delivery check
and every compared folder still has the hash it was frozen with. Exam records live
under ``exam/`` of the state directory, which no sandbox mounts, and no development
session is accepted once an exam has started.
"""
from __future__ import annotations

import math
import statistics
import threading
from concurrent.futures import ThreadPoolExecutor

from . import delivery
from .harness_repo import HarnessError, tree_hash
from .runs import Runner
from .scenario import DELIVERED, ScenarioError
from .state import State, exclusive

AREA = "exam"
POOL = "exam"
AGGREGATE = {"mean": statistics.fmean, "median": statistics.median, "min": min, "max": max,
             "sum": math.fsum}


class ExamRefused(Exception):
    pass


def sittings(ledger) -> list[dict]:
    """Every exam on record: {"start": entry, "runs": [entries], "end": entry or None}."""
    found: dict[str, dict] = {}
    for e in ledger.entries():
        kind, data = e["kind"], e["data"]
        if kind == "exam_started":
            found[data["exam"]] = {"start": e, "runs": [], "end": None}
        elif kind == "exam_run":
            found[data["exam"]]["runs"].append(e)
        elif kind in ("exam_finished", "exam_closed"):
            found[data["exam"]]["end"] = e
    return list(found.values())


def slots(start: dict) -> list[tuple[int, str, str]]:
    """Every (repeat, task, object name) of an exam, in the order they are run."""
    return [(repeat, task, o["name"]) for repeat in range(1, start["settings"]["repeats"] + 1)
            for task in start["tasks"] for o in start["objects"]]


def attempts_by_slot(runs: list[dict]) -> dict[tuple[int, str, str], list[dict]]:
    """The ``exam_run`` records of an exam, grouped by (repeat, task, object name)."""
    out: dict[tuple[int, str, str], list[dict]] = {}
    for d in runs:
        out.setdefault((d["repeat"], d["task"], d["object"]), []).append(d)
    return out


def results(start: dict, runs: list[dict]) -> dict:
    """Per object: the aggregate of what counts, and the places that do not count."""
    settings, done, out = start["settings"], attempts_by_slot(runs), {}
    for o in start["objects"]:
        counted, no_score, missing = [], 0, []
        for repeat, task, name in slots(start):
            if name != o["name"]:
                continue
            last = (done.get((repeat, task, name)) or [{"outcome": None}])[-1]
            if last["outcome"] == "scored":
                counted.append(last["score"])
            elif last["outcome"] == "no_score":
                counted.append(settings["failure_score"])
                no_score += 1
            else:
                missing.append({"task": task, "repeat": repeat})
        out[o["name"]] = {
            "aggregate": AGGREGATE[settings["aggregate"]](counted) if counted else None,
            "counted": len(counted), "no_score": no_score, "without_valid_run": missing}
    return out


def _refuse(state: State, reason: str):
    state.ledger.append("exam_refused", reason=reason)
    raise ExamRefused(reason)


def _open_sitting(state: State) -> dict | None:
    unfinished = [s for s in sittings(state.ledger) if s["end"] is None]
    return unfinished[-1] if unfinished else None


def _begin(state: State) -> dict:
    """Check that a new exam may start, fix the compared objects, record the start."""
    scenario, ledger = state.scenario, state.ledger
    held, uses = len(sittings(ledger)), scenario.exam_settings["uses"]
    if held >= uses:
        _refuse(state, f"the exam tasks may be used for {uses} exam(s); {held} already on record")
    passed = delivery.accepted(state)
    if passed is None:
        _refuse(state, "the version delivered last has not passed a delivery check")
    objects = [{"name": DELIVERED, "version": passed["version"], "hash": passed["hash"]}]
    try:
        if tree_hash(state.repo.path(passed["version"])) != passed["hash"]:
            _refuse(state, f"the delivered harness {passed['version']} was changed after it was "
                           "frozen: its hash differs")
        for name in scenario.exam_settings["compare"]:
            meta = state.repo.save(scenario.compare_folder(name), note=f"exam comparison {name}")
            objects.append({"name": name, "version": meta["version"], "hash": meta["hash"]})
    except (HarnessError, ScenarioError, OSError) as e:
        _refuse(state, str(e))
    start = {"exam": f"x{held + 1:04d}", "objects": objects, "tasks": list(scenario.exam),
             "settings": scenario.config["exam"], "limits": scenario.limits,
             "solver_model": scenario.models["solver"],
             "solver_settings": dict(scenario.solver_settings),
             "scenario_hash": ledger.last("scenario")["data"]["hash"]}
    return ledger.append("exam_started", **start)["data"]


def _unchanged(state: State, start: dict) -> list[str]:
    """Names of compared objects whose stored folder no longer has its frozen hash."""
    changed = []
    for o in start["objects"]:
        try:
            if tree_hash(state.repo.path(o["version"])) != o["hash"]:
                changed.append(o["name"])
        except (HarnessError, OSError):
            changed.append(o["name"])
    return changed


def _outcome(state: State, runner: Runner, record: dict) -> tuple[str, float | None]:
    if record["invalid"]["verdict"] == "invalid":
        return "invalid", None
    scored = record["score_result"]
    if scored["exit_code"] == 0 and not scored["timed_out"]:
        value = state.scenario.score_value(runner.run_dir(record["run"]) / "score")
        if value is not None:
            return "scored", value
    return "no_score", None


def run(state: State, gateway) -> dict:
    """Hold the exam, or continue the one that was interrupted; return its closing record."""
    with exclusive(state.root, "exam"):
        ledger = state.ledger
        sitting = _open_sitting(state)
        if sitting is None:
            start, runs = _begin(state), []
        else:
            start, runs = sitting["start"]["data"], [e["data"] for e in sitting["runs"]]
            if ledger.last("scenario")["data"]["hash"] != start["scenario_hash"]:
                _refuse(state, f"the scenario directory changed since exam {start['exam']} "
                               "began, so its remaining runs would not be held under the same "
                               "conditions; restore it, or close this exam as it stands")
            ledger.append("exam_continued", exam=start["exam"])
        changed = _unchanged(state, start)
        if changed:
            _refuse(state, f"harness folders changed since exam {start['exam']} began: {changed}")

        xid, reruns = start["exam"], start["settings"]["invalid_reruns"]
        versions = {o["name"]: o["version"] for o in start["objects"]}
        runner = Runner(state, f"{AREA}/{xid}", start["tasks"], gateway=gateway, pool=POOL)
        done = attempts_by_slot(runs)
        halt = threading.Event()  # set on an error or an interrupt: no further run starts

        def sit(slot) -> None:
            repeat, task, name = slot
            attempts = done.get(slot, [])
            try:
                while not halt.is_set() and (not attempts or (
                        attempts[-1]["outcome"] == "invalid" and len(attempts) <= reruns)):
                    record = runner.run(task, versions[name])
                    outcome, score = _outcome(state, runner, record)
                    attempts.append(ledger.append(
                        "exam_run", exam=xid, object=name, version=versions[name], task=task,
                        repeat=repeat, attempt=len(attempts) + 1, area=runner.area,
                        run=record["run"], outcome=outcome, score=score)["data"])
            except BaseException:
                halt.set()
                raise

        pool = ThreadPoolExecutor(max_workers=state.scenario.limits["parallel_runs"])
        try:
            for future in [pool.submit(sit, slot) for slot in slots(start)]:
                future.result()
        finally:  # runs under way end and are recorded; the exam can be continued later
            halt.set()
            pool.shutdown(wait=True, cancel_futures=True)

        all_runs = [e["data"] for e in ledger.entries("exam_run") if e["data"]["exam"] == xid]
        return ledger.append("exam_finished", exam=xid, results=results(start, all_runs),
                             changed_since_start=_unchanged(state, start))["data"]


def close(state: State) -> dict:
    """End an interrupted exam as it stands; places not yet run stay without a valid run."""
    with exclusive(state.root, "exam"):
        sitting = _open_sitting(state)
        if sitting is None:
            raise ExamRefused("there is no unfinished exam to close")
        start = sitting["start"]["data"]
        runs = [e["data"] for e in sitting["runs"]]
        return state.ledger.append("exam_closed", exam=start["exam"],
                                   results=results(start, runs),
                                   changed_since_start=_unchanged(state, start))["data"]
