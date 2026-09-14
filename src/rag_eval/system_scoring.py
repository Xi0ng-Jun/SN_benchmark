"""Explicit product scoring with pinned extraction; no model or judge calls.

This is an adapted system score using the SDK scorer, never the original Native
schema-generation path. SDK imports occur only when scoring is explicitly called.
"""
from __future__ import annotations

from copy import deepcopy
import re

from .system_product import SYSTEM_SUITES, SYSTEM_VERSION, answer_domain

EXTRACTION_VERSION = "sn-public-final-answer-v1"


def primary_scorer(suite):
    if suite not in SYSTEM_SUITES:
        raise ValueError("Unknown public system suite")
    if suite == "ifeval":
        return "product.ifeval.audited_all_instructions.full_body.v1"
    return "product.deepeval.exact_match_score.final_answer.v1"


def _extract(case, answer):
    if not isinstance(answer, str) or not answer.strip():
        return None, "empty or non-text answer"
    # Count every marker, including malformed/case-changed duplicates, so that
    # a second asserted conclusion cannot be silently ignored. A single exact
    # line is required; no last-number/letter recovery or Markdown repair.
    markers = re.findall(r"final\s+answer\s*:", answer, flags=re.IGNORECASE)
    if len(markers) != 1:
        return None, "requires exactly one unambiguous Final answer marker"
    matches = [re.fullmatch(r"Final answer: ([^\r\n]+)", line) for line in answer.splitlines()]
    values = [match.group(1) for match in matches if match is not None]
    if len(values) != 1 or not values[0].strip():
        return None, "missing exact standalone Final answer line"
    value = values[0]
    domain = answer_domain(case)
    kind = domain["kind"]
    if kind == "enum":
        valid = value in domain["values"]
    elif kind == "numeric_text":
        valid = re.fullmatch(r"[-+]?\$?[0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?", value) is not None
    elif kind == "integer_text":
        valid = re.fullmatch(r"-?[0-9]+", value) is not None
    elif kind == "closing_brackets":
        valid = re.fullmatch(r"[)\]}>](?:[ \t]+[)\]}>])*", value) is not None
    else:
        # StringSchema tasks preserve internal and boundary whitespace; only
        # the explicit line boundary and prefix are removed for extraction.
        valid = True
    if not valid:
        return None, "final answer is outside the pinned task output domain"
    return value, None


def parse_system_answer(case, answer):
    """Pure parsing observation saved before SDK scoring or any SDK imports.

    IFEval has no label extraction denominator: its untouched body is returned
    with not_applicable status. Other suites expose an explicit parsed/unparsed
    observation independently of whether later scoring can complete.
    """
    primary_scorer(case["suite"])
    if case["suite"] == "ifeval":
        return {"status": "not_applicable", "value": answer if isinstance(answer, str) else None,
                "reason": "IFEval uses the full response body; label extraction is not applicable",
                "extraction_version": EXTRACTION_VERSION}
    value, reason = _extract(case, answer)
    return {"status": "parsed" if value is not None else "unparsed", "value": value,
            "reason": reason, "extraction_version": EXTRACTION_VERSION}


def score_system_answer(case, answer, source, instruction_audits=()):
    """Return result_record-compatible fields from the saved system body.

    ``source`` is the frozen Native manifest. build_request verifies its SDK
    bytes and original answer mapping; its prompt is metadata, never SN input.
    Product clarification/refusal state is handled by the runtime, not guessed
    here. IFEval passes the untouched full body to the existing audited scorer.
    """
    suite = case["suite"]
    scorer_id = primary_scorer(suite)
    details = {"score_kind": "adapted_product", "official_native": False,
               "product_protocol": SYSTEM_VERSION, "extraction_version": EXTRACTION_VERSION,
               "scorer": scorer_id, "normalization_for_scoring": False,
               "extraction": "full_body_identity" if suite == "ifeval" else "unique_exact_final_answer_line"}
    parsed = parse_system_answer(case, answer)
    value, reason = parsed["value"], parsed["reason"]
    details["parsing"] = parsed
    if value is None:
        if suite == "ifeval":
            reason = "non-text IFEval response body"
        return {"status": "unparsed", "score": None, "reason": reason,
                "normalized_answer": None, "details": details}

    from .starter_native import build_request, score_prediction
    request, _ = build_request(case, source)
    details.update(native_request=deepcopy(request), official_expected_output=deepcopy(request["expected_output"]),
                   native_schema=deepcopy(request["schema"]), native_schema_name=request["schema_name"],
                   sdk_identity=request["sdk_identity"])
    if suite == "ifeval":
        result = score_prediction(case, request, value, source, instruction_audits=instruction_audits)
        details["instruction_results"] = deepcopy(result.get("details", []))
        return {"status": result["status"], "score": result.get("score"), "reason": result.get("reason"),
                "normalized_answer": value, "details": details}

    # Literal-valued tasks must satisfy the actual frozen SDK schema as well as
    # the product domain. Do not instantiate NumberSchema: it would erase comma
    # spelling and coerce the response before the official text scorer sees it.
    schema_answer = request["schema"].get("properties", {}).get("answer", {})
    if "enum" in schema_answer and value not in schema_answer["enum"]:
        return {"status": "unparsed", "score": None, "reason": "final answer is outside the official SDK literal schema",
                "normalized_answer": None, "details": details}
    details["answer_domain"] = answer_domain(case)
    details["native_schema_coercion_applied"] = False
    if suite == "gsm8k":
        details["numeric_protocol"] = "Extracted numeric text is unchanged, including commas; the Native integer schema is recorded, not used to coerce the system response"
    from deepeval.scorer import Scorer
    score = Scorer().exact_match_score(request["expected_output"], value)
    if score not in (0, 1):
        raise ValueError("SDK binary exact-match scorer returned a value other than 0 or 1")
    return {"status": "scored", "score": float(score), "reason": None,
            "normalized_answer": value, "details": details}
