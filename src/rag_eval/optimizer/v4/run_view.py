"""Read model of one method-v4 run, for the workbench's automatic-run page.

Projects ``state.json``, ``ledger.json``, ``cli-run.json`` and ``endpoint.json``
of a v4 run directory into what the page shows: status and next action,
calibration and feasibility, the lineage, each round's candidates through the
cascade and the round-end decision, ablation, the time account and the
endpoint. Pure apart from reading those files.

Counts, never task ids: minibatch membership and the reviewer's pair → task
key stay in ``state.json``. The reviewer's per-pair verdicts are shown by pair
label only.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

VIEW_SCHEMA_VERSION = 1
METHOD = "v4"
ENDPOINT_FILE = "endpoint.json"
FAILURE_HALTS = ("process_failure", "budget_exhausted")

CONFIG_SHOWN = ("time_budget_hours", "b", "cards_per_analysis", "smoke_size", "minibatch_fraction", "minibatch_min",
                "c", "c_mech", "minibatch_kill", "review", "review_pairs", "review_controls", "no_adoption_rounds",
                "merge", "ablation", "ablation_kill", "k_val", "calibration_k", "endpoint_nodes",
                "safeguard_tolerances", "max_rounds", "round_timebox_hours")
_RECORD_COPIED = ("card_index", "title", "card_ref", "stage", "label", "reason", "attempt", "repaired", "pruned",
                  "touches_preparation", "carried_to", "parent", "preflight",
                  "halted", "unmeasured", "delta_mb", "se_mb", "n_mb", "delta_val", "se_val", "se_own", "se_floor",
                  "se_method", "n_val", "dropped_val", "diff_lines", "timings", "started_at", "finished_at")


def _cid(versions: dict, h: Optional[str]) -> Optional[str]:
    return ((versions.get(h) or {}).get("candidate_id")) if h else None


def _safeguards(sg: Optional[dict]) -> Optional[dict]:
    if not sg:
        return None
    return dict(ok=sg.get("ok"), breached=sg.get("breached") or [], unobserved=sg.get("unobserved") or [],
                rows=[{k: r.get(k) for k in ("key", "direction", "subject", "reference", "tolerance", "harm", "breached")}
                      for r in sg.get("rows") or []])


def _mechanism(mech: Optional[dict]) -> Optional[dict]:
    if not mech:
        return None
    rev = mech.get("reviewer") or {}
    mechanical = mech.get("mechanical") or {}
    verdicts = [dict(pair=v.get("pair"), effect=v.get("effect"), changed_side=v.get("changed_side"),
                     side_effect=v.get("side_effect"), gaming=v.get("gaming"), note=v.get("note"))
                for v in rev.get("verdicts") or []]
    kinds = {p.get("pair"): p.get("kind") for p in rev.get("pairs") or []}
    for v in verdicts:
        v["kind"] = kinds.get(v["pair"])          # candidate / control: the page may say which were controls
    return dict(tier=mech.get("tier"),
                mechanical=dict(status=mechanical.get("status"), candidate_rate=mechanical.get("candidate_rate"),
                                parent_rate=mechanical.get("parent_rate"), aa_gap=mechanical.get("aa_gap"),
                                direction=mechanical.get("direction")) if mechanical else None,
                reviewer=dict(conclusion=rev.get("conclusion"), trusted=rev.get("trusted"), reason=rev.get("reason"),
                              effects=rev.get("effects"), wrong_side=rev.get("wrong_side"),
                              control_effects=rev.get("control_effects"), gaming=rev.get("gaming") or [],
                              side_effects=rev.get("side_effects"), verdicts=verdicts))


def _record(rec: dict, versions: dict) -> dict:
    out = {k: rec.get(k) for k in _RECORD_COPIED}
    out.update(candidate=rec.get("candidate"), candidate_id=(_cid(versions, rec.get("candidate")) or (rec.get("preflight") or {}).get("candidate_id")),
               smoke=(rec.get("smoke") or {}).get("verdict"), smoke_reason=(rec.get("smoke") or {}).get("reason"),
               safeguards=_safeguards(rec.get("safeguards") or rec.get("safeguards_mb")),
               mechanism=_mechanism(rec.get("mechanism")),
               qualification={k: (rec.get("qualification") or {}).get(k)
                              for k in ("qualified", "coefficient", "threshold", "reasons")}
               if rec.get("qualification") else None,
               cache=dict(minibatch=rec.get("cache_mb"), rest=rec.get("cache_rest")))
    return out


def _round(rd: dict, versions: dict, *, open_: bool) -> dict:
    records = sorted((rd.get("records") or {}).values(), key=lambda r: r["card_index"])
    cards = rd.get("cards")
    decision = rd.get("decision") or None
    if decision:
        merge = decision.get("merge") or None
        decision = dict(qualified=decision.get("qualified") or [], best=decision.get("best"),
                        adopted=decision.get("adopted"), adopted_id=_cid(versions, decision.get("adopted")),
                        merge=dict(of=merge.get("of"), adopt=merge.get("adopt"), skipped=merge.get("skipped"),
                                   delta_val=merge.get("delta_val"), se_val=merge.get("se_val"),
                                   candidate_id=_cid(versions, merge.get("candidate"))) if merge else None)
    split = rd.get("split") or {}
    queued = [dict(card_index=i, title=c.get("failure_mode_desc"), has_expectation=c.get("predicate") is not None)
              for i, c in enumerate(cards or []) if str(i) not in (rd.get("records") or {})]
    return dict(round=rd["round"], parent=rd.get("parent"), parent_id=_cid(versions, rd.get("parent")),
                started_at=rd.get("started_at"), finished_at=rd.get("finished_at"), wall_s=rd.get("wall_s"),
                analysis_s=rd.get("analysis_s"), decision_s=rd.get("decision_s"),
                cards=None if cards is None else len(cards),
                cards_with_expectation=sum(c.get("predicate") is not None for c in cards or []),
                minibatch_size=len(split.get("minibatch") or []), rest_size=len(split.get("rest") or []),
                diagnosis_s=rd.get("diagnosis_s"), diagnosis_prefetched=rd.get("diagnosis_prefetched"),
                timeboxed=bool(rd.get("timeboxed")), not_started=len(rd.get("not_started") or []),
                open=open_, records=[_record(r, versions) for r in records], queued=queued, decision=decision)


def _phase(state: dict, alive: bool) -> str:
    if state.get("halt_reason"):
        return "halted"
    if state.get("calibration") is None:
        return "calibrating"
    if state.get("stop_reason"):
        return "ablating"
    cur = state.get("current_round")
    if cur is None:
        return "between_rounds"
    if cur.get("cards") is None:
        return "analysing"
    if cur.get("decision") is None and any(r.get("stage") == "measured" for r in (cur.get("records") or {}).values()) \
            and not any(r.get("stage") in ("pending", "built", "repair", "rebase", "smoke_pass", "minibatch_pass", "validated")
                        and not r.get("unmeasured") and not r.get("carried_to") for r in (cur.get("records") or {}).values()):
        return "deciding"
    return "building"


def _next_action(status: str, halt: Optional[str], endpoint: Optional[dict], test_set: bool) -> Optional[str]:
    if status == "running" or status == "not_started":
        return None
    if status == "interrupted":
        return "resume"
    if halt == "process_failure":
        return "retry"
    if halt == "budget_exhausted":
        return "budget"
    if not test_set:
        return "no_test"
    return "review" if endpoint else "test"


def _lineage(state: dict, versions: dict) -> list[dict]:
    titles = {}
    for rd in (state.get("rounds") or []) + ([state["current_round"]] if state.get("current_round") else []):
        for r in (rd.get("records") or {}).values():
            titles[(rd["round"], r["card_index"])] = r.get("title")
    ab = state.get("ablation") or {}
    pruned = set(ab.get("pruned") or [])          # lineage indices (1 = first adopted node)
    out = []
    for i, n in enumerate(state.get("lineage") or []):
        out.append(dict(index=i, version=n["version"], candidate_id=_cid(versions, n["version"]), kind=n.get("kind"),
                        round=n.get("round"), parent=n.get("parent"), delta_val=n.get("delta_val"),
                        se_val=n.get("se_val"), tier=n.get("tier"), elapsed_s=n.get("elapsed_s"),
                        titles=[titles.get((n.get("round"), c)) for c in n.get("card_indices") or []],
                        pruned=bool(ab.get("accepted")) and i in pruned))
    return out


def _ablation(ab: Optional[dict], versions: dict) -> Optional[dict]:
    if not ab:
        return None
    units = [dict({k: u.get(k) for k in ("index", "round", "kind", "status", "delta", "se", "n", "prune")},
                  candidate_id=_cid(versions, u.get("version")))
             for _, u in sorted((ab.get("units") or {}).items(), key=lambda kv: int(kv[0]))]
    return dict(status=ab.get("status"), reason=ab.get("reason"), pruned=ab.get("pruned") or [],
                accepted=ab.get("accepted"), check=ab.get("check"), final_id=_cid(versions, ab.get("final")),
                budget=ab.get("budget"), wall_s=ab.get("wall_s"), units=units)


def _calibration(cal: Optional[dict]) -> Optional[dict]:
    if not cal:
        return None
    val = cal.get("validation") or {}
    return dict(k_val=cal.get("k_val"), k_val_rule=cal.get("k_val_rule"), rerun_var=cal.get("rerun_var"),
                task_var=val.get("task_var"), aa_sd=val.get("aa_sd"), aa_se=val.get("aa_se"),
                aa_delta=val.get("aa_delta"), constant_fraction=val.get("constant_fraction"), seed_mean=val.get("mean"),
                infrastructure_failure_rate=cal.get("infrastructure_failure_rate"), sizes=cal.get("sizes"),
                walls=cal.get("walls"), wall_s=cal.get("wall_s"), cache=cal.get("cache"))


def project(state: Optional[dict], *, identity: Optional[dict] = None, ledger: Optional[dict] = None,
            endpoint: Optional[dict] = None, alive: bool = False) -> dict:
    identity = identity or {}
    run = dict(method=METHOD, run_id=identity.get("run_id"), iteration_id=identity.get("iteration_id"),
               actor=identity.get("actor"), config_sha256=identity.get("config_sha256"),
               created_at=identity.get("created_at"), test_set=bool(identity.get("test_set")))
    base = dict(schema_version=VIEW_SCHEMA_VERSION, method=METHOD)
    if state is None:
        return dict(base, run=dict(run, status="running" if alive else "not_started", alive=alive), phase=None,
                    next_action=None, progress=None, config=None, variants={}, calibration=None, feasibility=None,
                    lineage=[], rounds=[], ablation=None, time_account=None, endpoint=None, deviations=[])
    versions = state.get("versions") or {}
    halt = state.get("halt_reason")
    status = "running" if alive else "halted" if halt else "interrupted"
    resolved = identity.get("config_resolved") or {}
    config = {k: resolved.get(k) for k in CONFIG_SHOWN if k in resolved}
    budget_s = (resolved.get("time_budget_hours") or 0) * 3600 or None
    lineage = _lineage(state, versions)
    rounds = [_round(rd, versions, open_=False) for rd in state.get("rounds") or []]
    if state.get("current_round"):
        rounds.append(_round(state["current_round"], versions, open_=True))
    records = [r for rd in rounds for r in rd["records"]]
    stage_counts: dict = {}
    for r in records:
        stage_counts[r["stage"]] = stage_counts.get(r["stage"], 0) + 1
    progress = dict(rounds_finished=len(state.get("rounds") or []),
                    round=(state.get("current_round") or {}).get("round"),
                    adopted=len(lineage) - 1, candidates=len(records), stage_counts=stage_counts,
                    no_adoption=state.get("no_adoption", 0), no_adoption_limit=resolved.get("no_adoption_rounds"),
                    run_failures=state.get("run_failures", 0),
                    run_failures_limit=resolved.get("max_consecutive_run_failures"),
                    elapsed_s=state.get("elapsed_s"), budget_s=budget_s, stop_reason=state.get("stop_reason"),
                    key_generation=state.get("key_generation", 0),
                    search_size=len((state.get("partitions") or {}).get("search") or []),
                    validation_size=len((state.get("partitions") or {}).get("validation") or []),
                    test_size=(state.get("partitions") or {}).get("test_size"),
                    families=(state.get("partitions") or {}).get("families"))
    return dict(base,
                run=dict(run, status=status, alive=alive, halt_reason=halt, started_at=state.get("started_at"),
                         updated_at=state.get("updated_at"), sessions=state.get("sessions") or []),
                phase=_phase(state, alive),
                next_action=_next_action(status, halt, endpoint, run["test_set"]),
                progress=progress, config=config, variants=(ledger or {}).get("variants") or {},
                calibration=_calibration(state.get("calibration")), feasibility=state.get("feasibility"),
                lineage=lineage, rounds=rounds, ablation=_ablation(state.get("ablation"), versions),
                time_account=(ledger or {}).get("time_account"), endpoint=endpoint,
                deviations=(ledger or {}).get("deviations") or [])


def read_run(state_dir: str | Path, *, alive: bool) -> dict:
    from ..run_view import IDENTITY_FILE, _optional_json
    from .loop import LEDGER_FILE, STATE_FILE
    state_dir = Path(state_dir)
    return project(_optional_json(state_dir / STATE_FILE), identity=_optional_json(state_dir / IDENTITY_FILE),
                   ledger=_optional_json(state_dir / LEDGER_FILE), endpoint=_optional_json(state_dir / ENDPOINT_FILE),
                   alive=alive)


def is_v4(identity: Optional[dict[str, Any]]) -> bool:
    return bool(identity) and identity.get("method") == METHOD
