"""SDK 4.2.2 templates and scorers, with no automatic dataset/model creation."""
from __future__ import annotations

from importlib.metadata import distribution
from pathlib import Path

from .artifacts import digest
from .public_expansion_protocol import EXPANSION_SUITES
from .starter_protocol import SDK_VERSION, fingerprint, make_case


def check_sdk(manifest):
    dist = distribution("deepeval")
    if dist.version != SDK_VERSION or manifest["deepeval_version"] != SDK_VERSION:
        raise ValueError("DeepEval version changed; freeze a new protocol")
    root = Path(dist.locate_file("deepeval"))
    if manifest.get("suite") in EXPANSION_SUITES:
        from .public_expansion_native import validate_sdk_snapshot
        validate_sdk_snapshot(root, manifest)
        return root
    expected = manifest.get("sdk_source_hashes")
    if not expected:
        raise ValueError("Frozen SDK hashes required")
    for relative, expected_hash in expected.items():
        path = (root / relative).resolve()
        if not path.is_relative_to(root.resolve()) or digest(path) != expected_hash:
            raise ValueError("SDK source changed: " + relative)
    return root


def build_request(case, manifest):
    """Return serializable request plus Pydantic schema, using native templates."""
    if case.get("suite") in EXPANSION_SUITES:
        from .public_expansion_native import build_request as build_expansion_request
        return build_expansion_request(case, manifest)
    check_sdk(manifest)
    from deepeval.benchmarks import schema as schemas

    suite, row = case["suite"], case["raw_row"]
    if suite != manifest["suite"] or fingerprint(row) != case["raw_row_sha256"]:
        raise ValueError("Case identity differs from the frozen source")
    canonical = make_case(suite, row, case["source_row_index"], case["task"])
    if any(case.get(k) != canonical[k] for k in ("case_id", "sample_id", "references", "answer_type", "scorer")):
        raise ValueError("Case labels or scorer differ from the original row")
    if case["n_shots"] != (None if suite == "ifeval" else 0):
        raise ValueError("This starter implementation only supports the frozen zero-shot protocol")
    references = case["references"]
    if suite == "squad":
        from deepeval.benchmarks.squad.template import SQuADTemplate as template
        native_input = template.format_question(row, include_answer=False)
        expected = template.format_output(row)
        prompt = template.generate_output(input=native_input, n_shots=0)
        schema = schemas.StringSchema
    elif suite == "drop":
        from deepeval.benchmarks.drop.template import DROPTemplate as template
        from deepeval.benchmarks.drop.drop import DELIMITER
        native_input = template.format_question(row, include_answer=False)
        prompt = template.generate_output(input=native_input, train_set=[], n_shots=0)
        # Preserve the loader/predict round trip, even for delimiter-bearing text.
        expected = template.parse_str_to_list(template.parse_list_to_str(references, DELIMITER), DELIMITER)
        schema = {"number": schemas.DROPNumberSchema, "date": schemas.DROPDateSchema,
                  "span": schemas.DROPStringSchema}[case["answer_type"]]
    elif suite == "boolq":
        from deepeval.benchmarks.bool_q.template import BoolQTemplate as template
        native_input, expected = template.format_question(row), template.format_answer(row)
        prompt = template.generate_output(input=native_input, n_shots=0)
        schema = schemas.AffirmationSchema
    elif suite == "logiqa":
        from deepeval.benchmarks.logi_qa.template import LogiQATemplate as template
        native_input, expected = template.format_question(row), template.format_output(row)
        prompt = template.generate_output(input=native_input, n_shots=0)
        schema = schemas.MultipleChoiceSchema
    elif suite == "ifeval":
        native_input = prompt = row["prompt"]
        expected, schema = "", schemas.StringSchema
    else:
        raise ValueError("Unknown suite")
    request = {"track": "N", "suite": suite, "case_id": case["case_id"],
               "sample_id": case["sample_id"], "task": case["task"],
               "native_input": native_input, "prompt": prompt, "expected_output": expected,
               "schema": schema.model_json_schema(), "schema_name": schema.__name__,
               "n_shots": case["n_shots"], "scorer": case["scorer"],
               "sdk_identity": fingerprint(manifest["sdk_source_hashes"])}
    return request, schema


def audit_instruction(instruction_id, kwargs, positive, negative, manifest):
    """Explicit OFFLINE execution for a later verification phase; never auto-run.

    This checks one parameterized rule with human-supplied positive/negative
    fixtures. It is implementation evidence, not benchmark or quality calibration.
    """
    root = check_sdk(manifest)
    if not all(isinstance(t, str) and t for t in (positive, negative)) or positive == negative:
        raise ValueError("Distinct nonempty positive/negative fixtures required")
    from deepeval.benchmarks.ifeval.ifeval import IFEvalInstructionVerifier as verifier
    yes, yes_reason = verifier.verify_instruction_compliance(positive, instruction_id, kwargs)
    no, no_reason = verifier.verify_instruction_compliance(negative, instruction_id, kwargs)
    return {"instruction_id": instruction_id, "kwargs": kwargs,
            "positive": positive, "negative": negative,
            "positive_result": yes, "negative_result": no,
            "reasons": [yes_reason, no_reason],
            "verifier_sha256": digest(root / "benchmarks/ifeval/ifeval.py"),
            "status": "passed" if yes is True and no is False else "failed"}


def score_prediction(case, request, prediction, manifest, *, judge=None, instruction_audits=()):
    """Score a saved prediction. SQuAD calls the explicitly supplied judge.

    This is an execution API for later use, not part of the preparation command.
    Errors propagate to the result writer; they are never converted into zero.
    """
    if not isinstance(prediction, str):
        raise ValueError("Prediction must be the schema-parsed answer as text")
    if case.get("suite") in EXPANSION_SUITES:
        from .public_expansion_native import score_prediction as score_expansion_prediction
        return score_expansion_prediction(case, request, prediction, manifest)
    root = check_sdk(manifest)
    rebuilt, _ = build_request(case, manifest)
    if request != rebuilt:
        raise ValueError("Prediction request does not match the frozen protocol")
    suite = case["suite"]
    if suite == "ifeval":
        from deepeval.benchmarks.ifeval.ifeval import IFEvalInstructionVerifier as verifier
        verifier_hash = digest(root / "benchmarks/ifeval/ifeval.py")
        details = []
        for position, (instruction, kwargs) in enumerate(zip(case["raw_row"]["instruction_id_list"], case["raw_row"]["kwargs"])):
            evidence = next((a for a in instruction_audits
                             if a.get("instruction_id") == instruction and a.get("kwargs") == kwargs
                             and a.get("verifier_sha256") == verifier_hash and a.get("status") == "passed"
                             and a.get("positive_result") is True and a.get("negative_result") is False), None)
            if evidence is None:
                details.append({"position": position, "instruction_id": instruction,
                                "status": "not_applicable", "reason": "missing matching positive/negative audit"})
            else:
                # Recheck supplied audit evidence, rather than trusting an editable status flag.
                checked = audit_instruction(instruction, kwargs, evidence["positive"], evidence["negative"], manifest)
                if checked["status"] != "passed":
                    raise ValueError("Instruction audit no longer reproduces")
                passed, reason = verifier.verify_instruction_compliance(prediction, instruction, kwargs)
                details.append({"position": position, "instruction_id": instruction,
                                "status": "scored", "score": int(passed), "reason": reason})
        if not details or any(d["status"] != "scored" for d in details):
            return {"status": "not_applicable", "score": None, "reason": "not all instruction instances audited", "details": details}
        return {"status": "scored", "score": float(all(d["score"] for d in details)), "details": details}

    from deepeval.scorer import Scorer
    scorer = Scorer()
    if suite == "squad":
        if judge is None or getattr(judge, "role", None) != "judge":
            raise ValueError("SQuAD requires an explicit judge-role adapter; no default provider")
        score = scorer.squad_score(input=request["native_input"], prediction=prediction,
                                   expected_output=request["expected_output"], evaluation_model=judge,
                                   using_native_evaluation_model=False)
    elif suite == "drop":
        score = scorer.quasi_contains_score(request["expected_output"], prediction)
    else:
        score = scorer.exact_match_score(request["expected_output"], prediction)
    if score not in (0, 1):
        raise ValueError("Native binary scorer returned a value other than 0 or 1")
    return {"status": "scored", "score": float(score), "limitations": case["limitations"]}
