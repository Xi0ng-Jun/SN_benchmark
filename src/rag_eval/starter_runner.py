"""Sequential starter orchestration. Imported by the explicit run CLI only."""
from __future__ import annotations

from contextlib import ExitStack
from collections import Counter
import json
from pathlib import Path
import shutil
import time
from uuid import uuid4

from .artifacts import digest, save_json, save_jsonl
from .starter_product import check_boolq, product_bundle
from .starter_protocol import fingerprint, load_bundle
from .starter_results import EventJournal, planned_result, result_record
from .public_expansion_protocol import EXPANSION_SUITES, TraceEnvelope, normalize_answer

PRODUCT_CORRECTNESS = "product.GEval.AnswerCorrectness"
BOOLQ_SCORER = "product.boolq.explicit_conclusion.v1"
FAITHFULNESS = "product.Faithfulness"
EVIDENCE = "product.final_context.document_coverage"
CITATIONS = "product.citation_object.existence_ratio"


def read_rows(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()]


def update_state(run, phase, **fields):
    save_json(run / "state.json", {"phase": phase, "updated_at": time.time(), **fields})


def _ifeval_ready(case, source, audits):
    from .starter_native import audit_instruction
    expected_hash = source["sdk_source_hashes"]["benchmarks/ifeval/ifeval.py"]
    for instruction, kwargs in zip(case["raw_row"]["instruction_id_list"], case["raw_row"]["kwargs"]):
        evidence = next((a for a in audits if a.get("instruction_id") == instruction
                         and a.get("kwargs") == kwargs and a.get("verifier_sha256") == expected_hash
                         and a.get("status") == "passed"), None)
        if evidence is None:
            return False
        check = audit_instruction(instruction, kwargs, evidence["positive"], evidence["negative"], source)
        if check["status"] != "passed":
            return False
    return True


def native_predictions(run, source, cases, tested, audits, outputs):
    from .starter_native import build_request
    from app.core.llm_logging import LLMInteractionLogger
    from .usage_capture import capture_usage
    for case in cases:
        update_state(run, "predicting", case_id=case["case_id"])
        record = {"case_id": case["case_id"], "sample_id": case["sample_id"], "suite": case["suite"],
                  "task": case.get("task", case["suite"]), "status": "error",
                  "output_available": False, "prediction": None, "request_id": uuid4().hex}
        record["trace"] = TraceEnvelope.missing().to_dict()
        started = time.monotonic()
        try:
            request, schema = build_request(case, source)
            record["request"] = request
            if case["suite"] == "ifeval" and not _ifeval_ready(case, source, audits):
                record.update(status="not_applicable", reason="instruction audit incomplete; model prediction not attempted")
            else:
                with capture_usage(LLMInteractionLogger) as usage:
                    try:
                        with tested.for_case(case["case_id"], record["request_id"]):
                            response = tested.generate(request["prompt"], schema=schema)
                        record.update(status="success", output_available=True, prediction=str(response.answer))
                        if case["suite"] in EXPANSION_SUITES:
                            record["normalized_answer"] = normalize_answer(case["suite"], record["prediction"], task=case.get("task"))
                    finally:
                        record["usage"] = usage
        except Exception as exc:
            record.update(error_type=type(exc).__name__, reason="prediction_or_protocol_error; see model events")
        record["seconds"] = time.monotonic() - started
        outputs(record)


def product_predictions(run, cases, bundle, mode, repo, outputs):
    from .benchmark_runtime import prepare_notebook, run_questions
    from .usage_capture import capture_usage
    from app.core.llm_logging import LLMInteractionLogger
    cell = run / "product-artifacts"
    cell.mkdir()
    update_state(run, "importing")
    usage = {}
    try:
        with capture_usage(LLMInteractionLogger) as usage:
            notebook, mapping = prepare_notebook(repo, cell, bundle["documents"], cases[0]["suite"])
    finally:
        save_json(run / "preparation-usage.json", usage)
    documents = {d["id"]: d for d in bundle["documents"]}
    for question in bundle["questions"]:
        update_state(run, "asking", case_id=question["case_id"])
        try:
            # Reuses persistence checks, synthesis capture and citation object checks.
            # The request contains only question + mode; no conversation or gold scope.
            run_questions(repo, cell, notebook, mapping, [question], mode, documents=documents)
            saved = [r for r in read_rows(cell / "outputs.jsonl") if r["id"] == question["id"]]
            if len(saved) != 1:
                raise ValueError("Expected exactly one persisted product output")
            record = saved[0]
            output = {"case_id": question["case_id"], "sample_id": question["sample_id"],
                     "suite": question.get("suite", cases[0]["suite"]), "task": question.get("task", cases[0].get("task", cases[0]["suite"])),
                     "status": "success" if record["status"] == "success" else "error",
                     "output_available": bool(record.get("answer", "").strip()),
                     "prediction": record.get("answer", ""), "product_record": record,
                     "reason": None if record["status"] == "success" else "native product error or clarification interception"}
        except Exception as exc:
            output = {"case_id": question["case_id"], "sample_id": question["sample_id"],
                     "suite": question.get("suite", cases[0]["suite"]), "task": question.get("task", cases[0].get("task", cases[0]["suite"])),
                     "status": "error", "output_available": False, "prediction": None,
                     "error_type": type(exc).__name__, "reason": "product invocation or capture error; see product-artifacts"}
        # Journal write errors must stop execution, not create a second result.
        outputs(output)


def _product_score(record, scorer, judge, result_id):
    if scorer == BOOLQ_SCORER:
        return check_boolq(record["answer"], record["expected_answer"], product_status=record["status"])
    if scorer == EVIDENCE:
        from_ids = record.get("source_ids", [])
        covered = record.get("retrieved_document_ids", [])
        if not record.get("context_supported") or not from_ids or len(from_ids) != len(covered):
            return {"status": "not_applicable", "score": None, "reason": "complete final-context document mapping unavailable"}
        gold = set(record["gold_document_ids"])
        if not gold:
            return {"status": "not_applicable", "score": None, "reason": "gold document IDs unavailable"}
        return {"status": "scored", "score": len(gold & set(covered)) / len(gold),
                "details": {"gold": sorted(gold), "observed": covered, "ranking_available": False}}
    if scorer == CITATIONS:
        checks = record["deterministic"]
        if not checks["citation_count"]:
            return {"status": "not_applicable", "score": None, "reason": "no citation objects; absence is not a passing score"}
        return {"status": "scored", "score": checks["citation_valid_count"] / checks["citation_count"],
                "details": {**checks, "claim_support": "not_evaluated", "body_anchor_support": "not_evaluated"}}
    from .quality_metrics import build_quality_metrics, expected_answer, metric_availability
    from .cases import to_test_case
    name = "Faithfulness" if scorer == FAITHFULNESS else "Answer Correctness"
    condition = metric_availability(record)[name]
    if not condition["available"]:
        return {"status": "not_applicable", "score": None, "reason": condition["reason"]}
    metrics = build_quality_metrics(record, judge, diagnostic=False)
    metric = next(m for m in metrics if (getattr(m, "name", None) or getattr(m, "__name__", None)) == name)
    test_case = to_test_case({**record, "expected_answer": expected_answer(record)})
    with judge.for_case(record["case_id"], result_id):
        metric.measure(test_case)
    if metric.error or metric.score is None:
        raise ValueError("Metric returned an error or no score")
    return {"status": "scored", "score": float(metric.score), "reason": metric.reason,
            "details": {"metric": name, "human_calibration": "pending"}}


def score_outputs(run, source, cases, planned, judge, audits, scores):
    from .starter_native import score_prediction
    from .usage_capture import capture_usage
    from app.core.llm_logging import LLMInteractionLogger
    outputs = {r["case_id"]: r for r in read_rows(run / "outputs.jsonl")}
    by_case = {c["case_id"]: c for c in cases}
    for item in planned:
        update_state(run, "scoring", case_id=item["case_id"], scorer=item["scorer"])
        output = outputs.get(item["case_id"])
        details = {"output_file": "outputs.jsonl", "case_id": item["case_id"]}
        available = bool(output and output["output_available"])
        if output is None or output["status"] != "success":
            status = "not_applicable" if output and output["status"] == "not_applicable" else "unscored"
            scores(result_record(item, status=status, output_available=available,
                                 reason=output.get("reason", "prediction unavailable") if output else "prediction missing", details=details))
            continue
        started = time.monotonic()
        with capture_usage(LLMInteractionLogger) as usage:
            try:
                if item["track"] == "N":
                    if judge is not None:
                        with judge.for_case(item["case_id"], item["result_id"]):
                            result = score_prediction(by_case[item["case_id"]], output["request"], output["prediction"], source,
                                                      judge=judge, instruction_audits=audits)
                    else:
                        result = score_prediction(by_case[item["case_id"]], output["request"], output["prediction"], source,
                                                  instruction_audits=audits)
                else:
                    result = _product_score(output["product_record"], item["scorer"], judge, item["result_id"])
                row = result_record(item, status=result["status"], score=result["score"],
                                    reason=result.get("reason"), output_available=available,
                                    normalized_answer=result.get("normalized_answer"),
                                    trace=result.get("trace"), details={**details, "scorer_result": result})
            except Exception as exc:
                row = result_record(item, status="error", output_available=available,
                                    reason="scorer invocation or validation error",
                                    details={**details, "error_type": type(exc).__name__})
        row["details"].update(seconds=time.monotonic() - started, usage=usage)
        scores(row)


def execute(*, root, project, bundle_dir, run, track, mode, models_path, reviews_path=None, audits_path=None):
    """One explicit execution, no implicit resume. No callers run this at import."""
    from .starter_runtime import configure_environment, make_adapter, resolve_models, snapshot_sources
    source, cases = load_bundle(bundle_dir)
    if not cases:
        raise ValueError("Frozen selection is empty; no execution possible")
    product = None
    if track == "R":
        if source["suite"] not in {"squad", "drop", "boolq"}:
            if mode not in {"chunk", "reasoning"}:
                raise ValueError("R requires mode chunk or reasoning")
            # Unsupported expansion suites are represented explicitly as N/A.
            product = {"manifest": {"product_applicability": "not_applicable",
                                     "reason": f"Product adapter is not implemented for {source['suite']}"},
                       "questions": []}
            cases = []
        else:
            if mode not in {"chunk", "reasoning"} or reviews_path is None:
                raise ValueError("R requires mode and human suitability reviews")
            raw = read_rows(bundle_dir / "raw.jsonl")
            field = "context" if source["suite"] == "squad" else "passage"
            reviews = json.loads(reviews_path.read_text(encoding="utf-8"))
            product = product_bundle(cases, reviews, [r[field] for r in raw])
        product["manifest"]["source_manifest_sha256"] = digest(bundle_dir / "manifest.json")
        product["manifest"]["distractor_provenance"] = "same frozen raw.jsonl in original order"
        eligible = {q["case_id"] for q in product["questions"]}
        cases = [c for c in cases if c["case_id"] in eligible]
        if not cases:
            raise ValueError("No product questions approved with usable official annotations")
    elif track != "N" or mode is not None or reviews_path is not None:
        raise ValueError("N has no product mode or suitability reviews")
    roles = (["tested"] if track == "N" else []) + (["judge"] if track == "R" or source["suite"] == "squad" else [])
    resolved, public_models = resolve_models(models_path, roles)
    if audits_path is not None and (track != "N" or source["suite"] != "ifeval"):
        raise ValueError("Instruction audits apply only to IFEval N")
    audits = read_rows(audits_path) if audits_path else []
    for forbidden in (root / "src", root / "scripts", project, bundle_dir):
        if run.is_relative_to(forbidden) or forbidden.is_relative_to(run):
            raise ValueError("Run directory must be separate from source/product/frozen input")
    run.mkdir(parents=True, exist_ok=False)
    update_state(run, "initializing")
    try:
        shutil.copytree(bundle_dir, run / "input")
        # Recheck the copied bytes, so later phases use only the pinned input.
        copied_source, _ = load_bundle(run / "input")
        if copied_source != source:
            raise ValueError("Frozen source changed during copying")
        save_jsonl(run / "instruction-audits.jsonl", audits)
        if product:
            save_json(run / "product-bundle.json", product)
        source_identity = snapshot_sources(root, project, run)
        settings, runtime_identity = configure_environment(project, run, product_track=track == "R")
        from .starter_native import check_sdk
        check_sdk(source)
        identity = {"source": source, "models": public_models, "code": source_identity,
                    "runtime_settings": runtime_identity["comparable_settings_sha256"],
                    "product_services": runtime_identity["service_config_sha256"],
                    "product_bundle": product["manifest"] if product else None,
                    "audits_sha256": fingerprint(audits), "track": track}
        pairing_id = fingerprint(identity) if track == "R" else None
        protocol_id = fingerprint({**identity, "mode": mode})
        scorers = ([BOOLQ_SCORER if source["suite"] == "boolq" else PRODUCT_CORRECTNESS,
                    FAITHFULNESS, EVIDENCE, CITATIONS] if track == "R" else [source["scorer"]])
        planned = [planned_result(c, run_id=run.name, protocol_id=protocol_id, track=track, mode=mode, scorer=scorer)
                   for c in cases for scorer in scorers]
        save_jsonl(run / "planned.jsonl", planned)
        save_json(run / "manifest.json", {"format": "public-starter-run-v1", "run_id": run.name,
                  "suite": source["suite"], "track": track, "mode": mode, "protocol_id": protocol_id,
                  "pairing_id": pairing_id, "models": public_models, "source_manifest": source,
                  "planned_sha256": digest(run / "planned.jsonl"), "planned_predictions": len(cases),
                  "planned_scores": len(planned), "human_calibration": "pending", "release_gate": False,
                  "adaptation_counts": dict(Counter(d["status"] for d in product["decisions"])) if product else None,
                  "identity": identity, "outer_timeout": None})
        with ExitStack() as stack:
            events = stack.enter_context(EventJournal(run / "model-events.jsonl"))
            outputs = stack.enter_context(EventJournal(run / "outputs.jsonl"))
            scores = stack.enter_context(EventJournal(run / "scores.jsonl"))
            adapters = {}
            for role, spec in resolved.items():
                adapters[role] = make_adapter(spec, role, settings, events)
                stack.callback(adapters[role].client.close)
            if track == "N":
                native_predictions(run, source, cases, adapters["tested"], audits, outputs)
            else:
                from app.services.sqlite_repository import SQLiteRepository
                repo = SQLiteRepository(settings)
                stack.callback(repo.close)
                if repo.db_path.resolve() != run / "runtime/database.db":
                    raise ValueError("Product database isolation failed")
                product_predictions(run, cases, product, mode, repo, outputs)
            score_outputs(run, source, cases, planned, adapters.get("judge"), audits, scores)
        rows = read_rows(run / "scores.jsonl")
        output_rows = read_rows(run / "outputs.jsonl")
        has_errors = any(r["status"] == "error" for r in rows + output_rows)
        update_state(run, "finished_with_errors" if has_errors else "finished", recorded_scores=len(rows),
                     recorded_predictions=len(output_rows))
    except BaseException as exc:
        previous = json.loads((run / "state.json").read_text(encoding="utf-8"))
        update_state(run, "interrupted" if isinstance(exc, (KeyboardInterrupt, SystemExit)) else "failed",
                     error_type=type(exc).__name__, failed_phase=previous.get("phase"),
                     case_id=previous.get("case_id"), scorer=previous.get("scorer"))
        raise
