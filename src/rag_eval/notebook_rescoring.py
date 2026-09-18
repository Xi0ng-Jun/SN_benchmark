"""Create an auditable scoring-only pass over a saved notebook run.

This module never constructs the SN runtime or a generation client.  It reads
the frozen input and saved answer ledger, then writes a new derived run whose
identity records the source run and scorer selection.
"""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import shutil
import uuid

from .artifacts import digest, save_json, save_jsonl
from .notebook_bundle import load_bundle
from .starter_protocol import fingerprint
from .starter_report import load_run
from .starter_results import EventJournal, result_record
from .starter_runner import read_rows

BASELINE_PROTOCOL = "sn-notebook-baseline-v1"
NOTEBOOK_PROTOCOL = "sn-notebook-benchmarks-v1"
RESCORING_VERSION = "notebook-rescoring-v1"


def _files(run):
    names = ["manifest.json", "planned.jsonl", "outputs.jsonl", "scores.jsonl",
             "product-bundle.json", "model-events.jsonl", "source-identity.json",
             "runtime-identity.json", "preparation-usage.json"]
    return {name: digest(run / name) for name in names if (run / name).is_file()}


def _score_value(case, record, scorer, protocol):
    from . import notebook_scoring
    if record is None or record.get("status") != "success" or not record.get("output_available"):
        return dict(status="unscored", score=None,
                    reason="no successful saved answer", details={})
    try:
        if protocol == NOTEBOOK_PROTOCOL:
            if scorer == "product.notebook.citation_object_existence_v1":
                from .starter_runner import CITATIONS, _product_score
                return _product_score(record["product_record"], CITATIONS, None, "rescoring")
            return notebook_scoring.score_case(case, record["product_record"], scorer)
        if protocol == BASELINE_PROTOCOL:
            return notebook_scoring.score_case(case, record["product_record"], scorer)
    except Exception as exc:
        return dict(status="error", score=None, reason="scorer failed",
                    details={"error_type": type(exc).__name__})
    raise ValueError("Unsupported notebook protocol for rescoring")


def _select(planned, existing, *, metrics, case_ids, all_scores):
    allowed = {(p["case_id"], p["scorer"]): p for p in planned}
    if metrics is not None:
        unknown = set(metrics) - {p["scorer"] for p in planned}
        if unknown:
            raise ValueError("Requested scorer is not in the saved plan: " + sorted(unknown)[0])
    if case_ids is not None:
        unknown = set(case_ids) - {p["case_id"] for p in planned}
        if unknown:
            raise ValueError("Requested case is not in the saved plan: " + sorted(unknown)[0])
    chosen = []
    for key, plan in allowed.items():
        if metrics is not None and plan["scorer"] not in metrics:
            continue
        if case_ids is not None and plan["case_id"] not in case_ids:
            continue
        old = existing.get(key)
        if not all_scores and old is not None and old.get("status") not in {"error", "unscored"}:
            continue
        chosen.append(plan)
    return chosen


def rescore_run(source, output, *, metrics=None, case_ids=None, all_scores=False):
    """Score saved answers into a new derived run and return its path.

    With no filters, only missing/error/unscored cells are selected.  Filters
    narrow the cells; ``all_scores`` explicitly recomputes every selected cell.
    Existing successful cells remain in the derived ledger unless selected.
    """
    source, output = Path(source).resolve(), Path(output).resolve()
    if output.exists() or output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError("Rescoring output must be a new directory separate from source")
    if metrics is not None:
        metrics = list(dict.fromkeys(metrics))
    if case_ids is not None:
        case_ids = list(dict.fromkeys(case_ids))
    loaded = load_run(source)
    manifest = loaded.get("manifest")
    if not manifest or manifest.get("product_protocol") not in {NOTEBOOK_PROTOCOL, BASELINE_PROTOCOL}:
        raise ValueError("Only saved notebook benchmark runs can be rescored")
    protocol = manifest["product_protocol"]
    # Verify every source artifact before creating the derived directory.
    origin_files = _files(source)
    bundle = load_bundle(source / "input")
    cases = {case["case_id"]: case for case in bundle["cases"]}
    plans = loaded["planned"]
    outputs = {row["case_id"]: row for row in loaded["outputs"]}
    existing = {(row["case_id"], row["scorer"]): row for row in loaded["scores"]}
    selected = _select(plans, existing, metrics=metrics, case_ids=case_ids, all_scores=all_scores)
    selected_keys = {(row["case_id"], row["scorer"]) for row in selected}
    if metrics is not None or case_ids is not None or all_scores:
        selection = "explicit"
    else:
        selection = "missing_or_failed"
    if not selected and not existing:
        raise ValueError("No score cells are available for rescoring")
    scoring_batch = dict(version=RESCORING_VERSION, origin_run_id=manifest["run_id"],
                         origin_manifest_sha256=digest(source / "manifest.json"),
                         origin_artifacts=origin_files, selection=selection,
                         scorers=sorted({p["scorer"] for p in selected}),
                         case_ids=sorted({p["case_id"] for p in selected}),
                         all_scores=bool(all_scores))
    identity = dict(manifest["identity"], scoring_batch=scoring_batch)
    protocol_id = fingerprint({**identity, "mode": manifest["mode"]})
    pairing_id = fingerprint(identity)
    output.mkdir(parents=True, exist_ok=False)
    save_json(output / "state.json", {"phase": "initializing"})
    try:
        shutil.copytree(source / "input", output / "input")
        for name in ("product-bundle.json", "outputs.jsonl", "model-events.jsonl",
                     "source-identity.json", "runtime-identity.json", "preparation-usage.json"):
            if (source / name).is_file():
                shutil.copyfile(source / name, output / name)
        save_json(output / "base-manifest.json", manifest)
        save_jsonl(output / "base-planned.jsonl", plans)
        save_jsonl(output / "base-scores.jsonl", loaded["scores"])
        new_plans = []
        for plan in plans:
            new_plans.append({**plan, "run_id": output.name, "protocol_id": protocol_id})
        for plan in new_plans:
            identity_fields = {key: plan[key] for key in plan if key != "result_id"}
            plan["result_id"] = fingerprint(identity_fields)
        save_jsonl(output / "planned.jsonl", new_plans)
        save_json(output / "manifest.json", {**manifest, "run_id": output.name,
                  "identity": identity, "protocol_id": protocol_id,
                  "pairing_id": pairing_id, "planned_sha256": digest(output / "planned.jsonl"),
                  "scoring_batch": scoring_batch})
        new_by_key = {(p["case_id"], p["scorer"]): p for p in new_plans}
        errors = False
        with EventJournal(output / "scores.jsonl") as score_sink:
            for key, plan in new_by_key.items():
                save_json(output / "state.json", {"phase": "scoring", "case_id": plan["case_id"],
                                                   "scorer": plan["scorer"]})
                old = existing.get(key)
                if key in selected_keys:
                    value = _score_value(cases[plan["case_id"]], outputs.get(plan["case_id"]),
                                          plan["scorer"], protocol)
                elif old is not None:
                    value = old
                else:
                    continue
                errors |= value.get("status") == "error"
                score_sink(result_record(plan, status=value["status"], score=value.get("score"),
                                         output_available=bool(outputs.get(plan["case_id"], {}).get("output_available")),
                                         reason=value.get("reason"), details=value.get("details"),
                                         normalized_answer=old.get("normalized_answer") if old else value.get("normalized_answer"),
                                         trace=old.get("trace") if old else None))
        save_json(output / "state.json", {"phase": "finished_with_errors" if errors else "finished"})
    except BaseException as exc:
        previous = json.loads((output / "state.json").read_text(encoding="utf-8"))
        save_json(output / "state.json", {"phase": "interrupted" if isinstance(exc, (KeyboardInterrupt, SystemExit)) else "failed",
                  "error_type": type(exc).__name__, "failed_phase": previous.get("phase"),
                  "case_id": previous.get("case_id"), "scorer": previous.get("scorer")})
        raise
    load_run(output)
    return output
