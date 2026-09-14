"""Save an unimplemented Product cell without loading models, SDK or SN."""
from __future__ import annotations

from copy import deepcopy
import shutil

from .artifacts import digest, save_json, save_jsonl
from .starter_protocol import fingerprint, load_bundle
from .starter_results import EventJournal, planned_result, result_record

SCORER = "product.applicability.v1"


def record_not_applicable(*, root, bundle_dir, source, cases, run, mode, reason):
    """Retain each requested case in the plan; null scores never become zero."""
    run.mkdir(parents=True, exist_ok=False)
    save_json(run / "state.json", {"phase": "initializing"})
    try:
        shutil.copytree(bundle_dir, run / "input")
        copied_source, copied_cases = load_bundle(run / "input")
        if copied_source != source or copied_cases != cases:
            raise ValueError("Frozen bundle changed during copying")
        code = {str(path.relative_to(root)): digest(path)
                for folder in ("src/rag_eval", "scripts")
                for path in sorted((root / folder).rglob("*.py"))}
        identity = {"source": source, "track": "R", "models": {},
                    "benchmark_source_hashes": code,
                    "applicability": {"status": "not_applicable", "reason": reason}}
        protocol_id = fingerprint({**identity, "mode": mode})
        planned = []
        for case in cases:
            case = deepcopy(case)
            case["product_review"] = {"status": "not_applicable", "reason": reason}
            planned.append(planned_result(case, run_id=run.name, protocol_id=protocol_id,
                                          track="R", mode=mode, scorer=SCORER))
        save_jsonl(run / "planned.jsonl", planned)
        save_json(run / "manifest.json", {
            "format": "public-starter-run-v1", "run_id": run.name,
            "suite": source["suite"], "track": "R", "mode": mode,
            "protocol_id": protocol_id, "pairing_id": fingerprint(identity),
            "models": {}, "source_manifest": source, "identity": identity,
            "planned_sha256": digest(run / "planned.jsonl"),
            "planned_predictions": len(cases), "planned_scores": len(planned),
            "adaptation_counts": {"not_applicable": len(cases)},
            "human_calibration": "pending", "release_gate": False,
            "execution_status": "not_applicable", "outer_timeout": None,
        })
        with EventJournal(run / "outputs.jsonl") as outputs, \
                EventJournal(run / "scores.jsonl") as scores, \
                EventJournal(run / "model-events.jsonl"):
            for case, item in zip(cases, planned):
                row = result_record(item, status="not_applicable", reason=reason)
                outputs({"case_id": case["case_id"], "sample_id": case["sample_id"],
                         "suite": case["suite"], "task": case["task"],
                         "status": "not_applicable", "output_available": False,
                         "prediction": None, "normalized_answer": None,
                         "trace": row["trace"], "reason": reason})
                scores(row)
        save_json(run / "state.json", {"phase": "not_applicable", "reason": reason,
                                      "recorded_scores": len(planned),
                                      "recorded_predictions": len(cases)})
    except BaseException as exc:
        save_json(run / "state.json", {"phase": "failed", "error_type": type(exc).__name__})
        raise
