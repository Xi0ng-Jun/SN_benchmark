"""The report: raw facts only, each taken from the ledger (method-design section 10).

``build`` reads nothing but ledger entries, plus the delivered folder's manifest,
whose hash is on the ledger. Every figure carries the sequence numbers of the
entries it comes from (``record`` / ``records``), so it can be looked up in
``ledger/ledger.jsonl``. The only derived numbers are counts, sums of usage, and
the exam aggregates, which the exam itself recorded. Nothing is judged here.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import delivery, exam
from .gateway import TOKEN_COUNTERS
from .harness_repo import HarnessError, tree_hash
from .ledger import now
from .materials import Materials
from .state import State

DEV_AREA = "dev"
COUNTERS = ("requests", *TOKEN_COUNTERS, "total_tokens")


def _count(entries: list[dict]) -> dict:
    return {"count": len(entries), "records": [e["seq"] for e in entries]}


def _usage(entries: list[dict]) -> dict:
    """Model use summed over the entries that carry a usage record."""
    total, seqs = dict.fromkeys(COUNTERS, 0), []
    for e in entries:
        u = e["data"].get("usage")
        if u:
            for k in COUNTERS:
                total[k] += u.get(k, 0)
            seqs.append(e["seq"])
    return {**total, "records": seqs}


def _scenario(entries: list[dict], split: dict) -> dict:
    last = entries[-1]
    config = last["data"]["config"]
    return {"record": last["seq"], "path": last["data"]["path"], "hash": last["data"]["hash"],
            "split_record": split["seq"], "split_hash": split["data"]["hash"],
            "practice_tasks": split["data"]["split"]["practice"],
            "exam_tasks": split["data"]["split"]["exam"],
            "solver_model": config["models"]["solver"],
            "developer_model": config["models"]["developer"],
            "limits": config["limits"], "solver_settings": config.get("solver_settings", {}),
            "exam": config.get("exam")}


def _scenario_changes(entries: list[dict]) -> list[dict]:
    """What differed each time the scenario directory was found changed."""
    out = []
    for before, after in zip(entries, entries[1:]):
        a, b = before["data"]["config"], after["data"]["config"]
        la, lb = a.get("limits") or {}, b.get("limits") or {}
        out.append({"record": after["seq"], "time": after["time"],
                    # an empty list: only files other than scenario.json changed
                    "config_keys": sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k)),
                    "limits": {k: {"before": la.get(k), "after": lb.get(k)}
                               for k in sorted(set(la) | set(lb)) if la.get(k) != lb.get(k)}})
    return out


def _delivery(state: State, named: list[dict], checks: list[dict]) -> dict:
    out = {"designations": [{"record": e["seq"], "time": e["time"], **e["data"]} for e in named],
           "checks": [{"record": e["seq"], "time": e["time"], **e["data"]} for e in checks],
           "delivered": None}
    passed = delivery.accepted(state)
    if passed is None:
        return out
    manifest = folder_matches = None
    try:
        folder = state.repo.path(passed["version"])
        folder_matches = tree_hash(folder) == passed["hash"]
        manifest = (folder / delivery.MANIFEST_NAME).read_bytes()
    except (HarnessError, OSError):
        pass
    out["delivered"] = {
        "record": passed["seq"], "version": passed["version"], "hash": passed["hash"],
        "folder_matches_hash": folder_matches,
        "manifest": None if manifest is None else manifest.decode(errors="replace"),
        "manifest_sha256": passed["manifest_sha256"],
        "manifest_matches_hash": manifest is not None and hashlib.sha256(
            manifest).hexdigest() == passed["manifest_sha256"]}
    return out


def _exam(sitting: dict, run_entries: dict) -> dict:
    start, end = sitting["start"]["data"], sitting["end"]
    failure_score = start["settings"]["failure_score"]
    attempts = {}
    for e in sitting["runs"]:
        d = e["data"]
        attempts.setdefault((d["repeat"], d["task"], d["object"]), []).append(e)
    scores: dict = {}
    costs = {o["name"]: {"seconds": 0.0, **dict.fromkeys(COUNTERS, 0), "runs": 0}
             for o in start["objects"]}
    for slot in exam.slots(start):
        repeat, task, name = slot
        rows = []
        for e in attempts.get(slot, []):
            d = e["data"]
            finished = run_entries.get((d["area"], d["run"]))
            usage = (finished["data"].get("usage") or {}) if finished else {}
            seconds = finished["data"]["run_result"]["seconds"] if finished else None
            rows.append({"attempt": d["attempt"], "outcome": d["outcome"], "score": d["score"],
                         "run": f"{d['area']}/runs/{d['run']}", "record": e["seq"],
                         "run_record": finished["seq"] if finished else None,
                         "run_seconds": seconds,
                         "usage": {k: usage[k] for k in COUNTERS if k in usage}})
            cost = costs[name]
            cost["runs"] += 1
            cost["seconds"] = round(cost["seconds"] + (seconds or 0), 3)
            for k in COUNTERS:
                cost[k] += usage.get(k, 0)
        last = rows[-1]["outcome"] if rows else None
        counts = (rows[-1]["score"] if last == "scored"
                  else failure_score if last == "no_score" else None)
        scores.setdefault(name, {}).setdefault(task, []).append(
            {"repeat": repeat, "counts": counts, "attempts": rows})
    return {"exam": start["exam"], "record": sitting["start"]["seq"],
            "time": sitting["start"]["time"],
            "status": "unfinished" if end is None else end["kind"].removeprefix("exam_"),
            "objects": start["objects"], "tasks": start["tasks"], "settings": start["settings"],
            "limits": start["limits"], "solver_model": start["solver_model"],
            "solver_settings": start["solver_settings"], "scores": scores,
            "results": end["data"]["results"] if end else None,
            "results_record": end["seq"] if end else None,
            "changed_since_start": end["data"]["changed_since_start"] if end else None,
            "costs": costs}


def build(state: State) -> dict:
    ledger = state.ledger
    entries = ledger.entries()
    kinds: dict[str, list[dict]] = {}
    for e in entries:
        kinds.setdefault(e["kind"], []).append(e)

    def of(kind: str, area: str | None = None) -> list[dict]:
        return [e for e in kinds.get(kind, []) if area is None or e["data"].get("area") == area]

    runs = of("run_finished")
    dev_runs = of("run_finished", DEV_AREA)
    developer, dev_run_use = _usage(of("dev_session_finished")), _usage(
        dev_runs + of("run_aborted", DEV_AREA))
    sessions = of("dev_session_finished")
    return {
        "generated": now(),
        "ledger": {"entries": len(entries), "last_hash": entries[-1]["hash"] if entries else "",
                   "problems": ledger.verify()},
        "materials_problems": Materials(state.root / "materials", ledger).verify(),
        "scenario": _scenario(of("scenario"), of("split_locked")[0]),
        "exams": [_exam(s, {(e["data"]["area"], e["data"]["run"]): e for e in runs})
                  for s in exam.sittings(ledger)],
        "exams_refused": [{"record": e["seq"], "time": e["time"], "reason": e["data"]["reason"]}
                          for e in of("exam_refused")],
        "costs": {
            "development": {
                "developer_agent": developer, "runs": dev_run_use,
                "total": {k: developer[k] + dev_run_use[k] for k in COUNTERS}},
            "delivery_trials": _usage(of("run_finished", delivery.TRIAL_AREA))},
        "development": {
            "sessions": {**_count(sessions), "seconds": round(
                sum(e["data"]["seconds"] for e in sessions), 3)},
            "runs": _count(dev_runs),
            "runs_refused": _count(of("run_refused", DEV_AREA)),
            "runs_aborted": _count(of("run_aborted", DEV_AREA)),
            "scorings": _count(of("score_finished", DEV_AREA)),
            "environment_commands": _count(of("enter_finished", DEV_AREA)),
            "questions": _count(of("question")),
            "answers": _count(of("answer")),
            "materials_added": _count(of("material_added")),
            "scenario_changes": _scenario_changes(of("scenario")),
            "stops_requested": _count(of("stop_requested")),
            "invalid_runs": [{"record": e["seq"], "run": f"{e['data']['area']}/runs/"
                              f"{e['data']['run']}", "task": e["data"]["task"],
                              "reason": e["data"]["invalid"].get("reason", "")}
                             for e in runs if e["data"]["invalid"]["verdict"] == "invalid"],
            "invalid_checks_failed": _count(
                [e for e in runs if e["data"]["invalid"]["verdict"] == "check_error"])},
        "delivery": _delivery(state, of("delivery_designated"), of("delivery_checked")),
    }


def _refs(seqs) -> str:
    seqs = [] if seqs is None else [seqs] if isinstance(seqs, int) else list(seqs)
    if not seqs:
        return ""
    shown = ", ".join(f"#{n}" for n in seqs[:8]) + (f", ... ({len(seqs)} records)"
                                                    if len(seqs) > 8 else "")
    return f" [{shown}]"


def _number(v) -> str:
    return "-" if v is None else json.dumps(v)


def _usage_text(u: dict) -> str:
    return ", ".join(f"{k} {u.get(k, 0)}" for k in COUNTERS)


def render(r: dict) -> str:
    """The report as plain text. ``[#n]`` names the ledger entry a figure comes from."""
    sc, dev, costs, deliv = r["scenario"], r["development"], r["costs"], r["delivery"]
    out = ["# Report", "", f"Generated {r['generated']}. Figures are followed by the ledger "
           "entries they come from, as [#sequence number].", ""]
    out += [f"Scenario: {sc['path']} (hash {sc['hash']}){_refs(sc['record'])}",
            f"Split hash: {sc['split_hash']}{_refs(sc['split_record'])}; practice tasks "
            f"{len(sc['practice_tasks'])}, exam tasks {len(sc['exam_tasks'])}",
            f"Solver model: {sc['solver_model']}; developer model: {sc['developer_model']}",
            f"Solver settings: {json.dumps(sc['solver_settings'], sort_keys=True)}",
            f"Limits: {json.dumps(sc['limits'], sort_keys=True)}",
            f"Exam settings: {json.dumps(sc['exam'], sort_keys=True)}",
            f"Ledger: {r['ledger']['entries']} entries; problems found: "
            f"{r['ledger']['problems'] or 'none'}",
            f"Materials: problems found: {r['materials_problems'] or 'none'}", ""]

    out += ["## Scores", ""]
    if not r["exams"]:
        out += ["No exam has been held.", ""]
    for x in r["exams"]:
        out += [f"### Exam {x['exam']} ({x['status']}){_refs(x['record'])}", "",
                f"Settings: {json.dumps(x['settings'], sort_keys=True)}",
                f"Solver settings: {json.dumps(x['solver_settings'], sort_keys=True)}", ""]
        for o in x["objects"]:
            out += [f"- {o['name']}: harness {o['version']}, hash {o['hash']}"]
        out += ["", "Official score of every run (object, task, repeat, attempt):", ""]
        for name, tasks in x["scores"].items():
            for task, repeats in tasks.items():
                for rep in repeats:
                    for a in rep["attempts"]:
                        out.append(f"- {name} / {task} / repeat {rep['repeat']} / attempt "
                                   f"{a['attempt']}: {a['outcome']}, score {_number(a['score'])}"
                                   f", {a['run']}{_refs(a['record'])}")
                    if not rep["attempts"]:
                        out.append(f"- {name} / {task} / repeat {rep['repeat']}: not run")
        out.append("")
        if x["results"] is None:
            out += ["Not finished; no aggregate.", ""]
        else:
            out += [f"Aggregate ({x['settings']['aggregate']} of the numbers that count; a run "
                    f"without an official score counts {_number(x['settings']['failure_score'])}"
                    f"; invalid runs do not count){_refs(x['results_record'])}:", ""]
            for name, res in x["results"].items():
                line = (f"- {name}: {_number(res['aggregate'])} over {res['counted']} runs, "
                        f"of which {res['no_score']} without an official score")
                if res["without_valid_run"]:
                    line += f"; without a valid run: {json.dumps(res['without_valid_run'])}"
                out.append(line)
            out.append("")
            if x["changed_since_start"]:
                out += [f"Harness folders changed during the exam: {x['changed_since_start']}",
                        ""]
    for e in r["exams_refused"]:
        out.append(f"- Exam refused {e['time']}: {e['reason']}{_refs(e['record'])}")
    if r["exams_refused"]:
        out.append("")

    agent, asked = costs["development"]["developer_agent"], costs["development"]["runs"]
    out += ["## Costs", "",
            f"- Development, developer agent: {_usage_text(agent)}{_refs(agent['records'])}",
            f"- Development, runs it asked for: {_usage_text(asked)}{_refs(asked['records'])}",
            f"- Development, total: {_usage_text(costs['development']['total'])}",
            f"- Development time: {dev['sessions']['seconds']} s in {dev['sessions']['count']} "
            f"session(s){_refs(dev['sessions']['records'])}",
            f"- Delivery trial runs: {_usage_text(costs['delivery_trials'])}"
            f"{_refs(costs['delivery_trials']['records'])}"]
    for x in r["exams"]:
        for name, tasks in x["scores"].items():
            for task, repeats in tasks.items():
                for rep in repeats:
                    for a in rep["attempts"]:
                        out.append(f"- Exam {x['exam']}, {name} / {task} / repeat "
                                   f"{rep['repeat']} / attempt {a['attempt']}: "
                                   f"{_number(a['run_seconds'])} s, {_usage_text(a['usage'])}"
                                   f"{_refs(a['run_record'])}")
            c = x["costs"][name]
            out.append(f"- Exam {x['exam']}, {name}, all {c['runs']} runs: {c['seconds']} s, "
                       f"{_usage_text(c)}")
    out.append("")

    out += ["## Development process", ""]
    for label, key in (("Runs", "runs"), ("Runs refused", "runs_refused"),
                       ("Runs aborted", "runs_aborted"), ("Scorings", "scorings"),
                       ("Environment commands", "environment_commands"),
                       ("Questions", "questions"), ("Answers", "answers"),
                       ("Additions to the materials", "materials_added"),
                       ("Stops requested", "stops_requested"),
                       ("Runs whose invalid-run check itself failed", "invalid_checks_failed")):
        out.append(f"- {label}: {dev[key]['count']}{_refs(dev[key]['records'])}")
    out.append(f"- Changes of the scenario directory: {len(dev['scenario_changes'])}")
    for c in dev["scenario_changes"]:
        what = (f"configuration keys {c['config_keys']}" if c["config_keys"]
                else "files other than the configuration")
        limits = "".join(f"; {k}: {_number(v['before'])} -> {_number(v['after'])}"
                         for k, v in c["limits"].items())
        out.append(f"  - {c['time']}: {what}{limits}{_refs(c['record'])}")
    out.append(f"- Invalid runs: {len(dev['invalid_runs'])}")
    for i in dev["invalid_runs"]:
        out.append(f"  - {i['run']} (task {i['task']}): {i['reason']}{_refs(i['record'])}")
    out.append("")

    out += ["## Delivered harness", ""]
    for d in deliv["designations"]:
        out.append(f"- Named as delivery {d['time']}: {d['version']}, hash {d['hash']}"
                   f"{_refs(d['record'])}")
    for c in deliv["checks"]:
        out.append(f"- Delivery check {c['time']}: {c['version']} {c['outcome']}"
                   + (f" ({'; '.join(c['problems'])})" if c["problems"] else "")
                   + f"{_refs(c['record'])}")
    d = deliv["delivered"]
    if d is None:
        out += ["", "No delivered version has passed the delivery check."
                if deliv["designations"] else "Nothing was delivered.", ""]
    else:
        out += ["", f"Delivered: {d['version']}, hash {d['hash']}{_refs(d['record'])}",
                f"Stored folder matches this hash: {_number(d['folder_matches_hash'])}; "
                f"manifest matches its recorded hash: {_number(d['manifest_matches_hash'])}",
                "", "Manifest as delivered:", "", "~~~~", (d["manifest"] or "").rstrip("\n"),
                "~~~~", ""]
    return "\n".join(out)


def write(state: State) -> tuple[Path, Path]:
    """Write ``report/report.json`` and ``report/report.md`` in the state directory."""
    r = build(state)
    d = state.root / "report"
    d.mkdir(exist_ok=True)
    (d / "report.json").write_text(json.dumps(r, indent=1))
    (d / "report.md").write_text(render(r))
    return d / "report.json", d / "report.md"
