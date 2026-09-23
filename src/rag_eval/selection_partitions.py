"""Deterministic local corpus partitions, independent of the question selection.

This module neither imports a runtime/SDK nor acquires source data. A plan is a
rebuildable artifact; it never supplies a human suitability approval.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import re

from .starter_product import document, product_bundle
from .starter_protocol import VERSION, fingerprint, make_case, require_text
from .system_product import SYSTEM_SUITES, SYSTEM_VERSION, _canonical, _task_material, build_system_bundle

PARTITION_VERSION = "public-corpus-partitions-v1"
_LEGACY = {"squad", "drop", "boolq"}
_SCOPE = "entire fixed partition corpus, never per-question gold"


def _review_inputs(cases, reviews, suite):
    if not isinstance(reviews, dict):
        raise ValueError("Reviews must be an object keyed by sample ID")
    samples = {case["sample_id"]: case for case in cases}
    if set(reviews) - samples.keys():
        raise ValueError("Review contains sample IDs outside this frozen selection")
    if reviews and suite not in _LEGACY:
        raise ValueError("System suites do not consume human suitability reviews")
    normalized = deepcopy(reviews)
    for sample_id, review in normalized.items():
        if not isinstance(review, dict):
            raise ValueError("Each human review must be an object")
        status = review.get("status")
        if status not in {"approved", "pending", "excluded", "rejected"}:
            raise ValueError("Unknown human review status")
        if status != "pending":
            require_text(review.get("reviewer"), "reviewer")
            require_text(review.get("reason"), "review reason")
        if (status != "pending" or "raw_row_sha256" in review) and (
                review.get("raw_row_sha256") != samples[sample_id]["raw_row_sha256"]):
            raise ValueError("Human review refers to different source content")
        # The legacy adapter names a negative human verdict 'excluded'. Keep the
        # supplied verdict verbatim in the plan and in its applicability log.
        if status == "rejected":
            review["status"] = "excluded"
    return normalized


def _bundle(cases, source, reviews, distractors, cap):
    if cases[0]["suite"] in SYSTEM_SUITES:
        return build_system_bundle(cases, source, max_documents=cap)
    subset = {case["sample_id"]: reviews[case["sample_id"]]
              for case in cases if case["sample_id"] in reviews}
    result = product_bundle(cases, subset, distractors, max_documents=cap)
    # Legacy batch preparation deduplicates samples. Preserve every canonical
    # membership if a caller supplies a sample with multiple task memberships.
    if len({case["sample_id"] for case in cases}) != len(cases):
        singles = [product_bundle([case], {case["sample_id"]: subset[case["sample_id"]]}, [],
                                  max_documents=cap) for case in cases]
        result["questions"] = [q for single in singles for q in single["questions"]]
        result["decisions"] = [d for single in singles for d in single["decisions"]]
    return result


def _group(case):
    if case["suite"] in {"mmlu", "bbh"}:
        return case["task"]
    if case["suite"] == "squad":
        return require_text(case["raw_row"].get("title"), "SQuAD title")
    return case["suite"]


def build_partition_plan(cases, source, reviews=None, max_documents=40):
    """Retain every case decision and partition executable same-material units.

    ``source`` is the canonical-compatible Native manifest returned by the
    verified selection loader. Exact same material stays together even when it
    occurs under different task/title groups; its first source occurrence owns
    the unit's group. Capacity counts documents, never questions/memberships.
    """
    cases = list(cases)
    if not cases or not all(isinstance(case, dict) for case in cases):
        raise ValueError("Nonempty canonical selection cases required")
    if type(max_documents) is not int or not 1 <= max_documents <= 40:
        raise ValueError("Partition document cap must be in 1..40")
    suites = {case.get("suite") for case in cases}
    if len(suites) != 1 or not suites <= (_LEGACY | SYSTEM_SUITES.keys()):
        raise ValueError("Exactly one supported suite required")
    suite = next(iter(suites))
    if (not isinstance(source, dict) or source.get("suite") != suite
            or source.get("selection_protocol") != "public-selection-v1"
            or not re.fullmatch(r"[0-9a-f]{64}", source.get("selection_bundle_sha256", ""))):
        raise ValueError("Verified public selection source identity required")
    ids, samples = set(), {}
    for case in cases:
        if type(case.get("source_row_index")) is not int or case["source_row_index"] < 0:
            raise ValueError("Source row index must be a nonnegative integer")
        if case["case_id"] in ids:
            raise ValueError("Duplicate canonical case ID")
        ids.add(case["case_id"])
        previous = samples.setdefault(case["sample_id"], case["raw_row_sha256"])
        if previous != case["raw_row_sha256"]:
            raise ValueError("Sample identity points to conflicting source rows")
        if suite in _LEGACY:
            canonical = make_case(suite, case["raw_row"], case["source_row_index"], case["task"])
            if source.get("protocol_version") != VERSION or case != canonical:
                raise ValueError("Case differs from the frozen canonical source row")
        if "case_fingerprints" in source and source["case_fingerprints"].get(case["case_id"]) != fingerprint(case):
            raise ValueError("Case differs from frozen source fingerprints")
    if "case_fingerprints" in source and set(source["case_fingerprints"]) != ids:
        raise ValueError("Partition plan requires the complete frozen selection membership")
    declared_count = source.get("selection_summary", {}).get("selected_memberships")
    if declared_count is not None and declared_count != len(cases):
        raise ValueError("Partition plan requires the complete frozen selection membership")
    saved_reviews = deepcopy({} if reviews is None else reviews)
    normalized_reviews = _review_inputs(cases, saved_reviews, suite)
    source_fingerprint = fingerprint(source)
    # Physical source order takes precedence over selector task enumeration.
    ordered = sorted(cases, key=lambda case: case["source_row_index"])
    decisions, units = {}, {}
    for case in ordered:
        subset = {case["sample_id"]: normalized_reviews[case["sample_id"]]} if case["sample_id"] in normalized_reviews else {}
        if suite in SYSTEM_SUITES:
            # Reuse exactly the adapter's canonical reconstruction and whitelist
            # without hashing its full source manifest once for every question.
            material, _, _ = _task_material(_canonical(case, source))
            material_ids = [document(material, suite)["id"]]
            original = {"status": "applicable", "basis": "machine_field_extraction",
                        "reason": "Whitelisted original task fields under the independent system adapter; no human suitability judgment inferred"}
            executable = True
        else:
            single = _bundle([case], source, subset, [], max_documents)
            original = single["decisions"][0]
            executable = bool(single["questions"])
            material_ids = [doc["id"] for doc in single["documents"]]
        supplied_review = saved_reviews.get(case["sample_id"])
        status = "applicable" if executable else original["status"]
        if supplied_review and supplied_review["status"] in {"rejected", "excluded"}:
            status = "rejected"
        reason = original.get("reason") or ("Human suitability review approved" if executable else "Human suitability review required")
        decision = {"case_id": case["case_id"], "sample_id": case["sample_id"],
                    "task": case["task"], "status": status, "reason": reason,
                    "partition_id": None, "material_document_ids": [],
                    "product_protocol": "legacy" if suite in _LEGACY else SYSTEM_VERSION}
        if suite in _LEGACY:
            decision["human_review"] = deepcopy(supplied_review or {"status": "pending", "reason": "human suitability review required"})
            if suite == "drop" and not case["raw_row"].get("_annotations"):
                # Review and annotation availability are independent. A pending
                # or rejected review remains visible even while data blocks R.
                decision["annotation_status"] = "not_applicable"
                decision["annotation_reason"] = "complete official DROP annotations not attached"
        else:
            decision["basis"] = original["basis"]
        decisions[case["case_id"]] = decision
        if not executable:
            continue
        if len(material_ids) != 1:
            raise ValueError("Partition protocol requires one material unit per canonical question")
        decision["material_document_ids"] = material_ids
        unit = units.setdefault(material_ids[0], {"group": _group(case), "cases": []})
        unit["cases"].append(case)
    groups = {}
    for unit in units.values():
        groups.setdefault(unit["group"], []).append(unit)
    # Only selected, structurally valid canonical passages are available here.
    # Do not imply that these are all passages in the upstream split.
    distractors = list(dict.fromkeys(case["passage"] for case in ordered)) if suite in _LEGACY else []
    partitions = []
    for group, grouped_units in groups.items():
        for start in range(0, len(grouped_units), max_documents):
            partition_cases = [case for unit in grouped_units[start:start + max_documents] for case in unit["cases"]]
            partition_id = f"{suite}-{len(partitions) + 1:04d}"
            for case in partition_cases:
                decisions[case["case_id"]]["partition_id"] = partition_id
            bundle = _bundle(partition_cases, source, normalized_reviews, distractors, max_documents)
            bundle["decisions"] = [deepcopy(decisions[case["case_id"]]) for case in partition_cases]
            manifest = bundle["manifest"]
            manifest.update(
                corpus_protocol=PARTITION_VERSION, partition_id=partition_id,
                product_protocol="legacy" if suite in _LEGACY else SYSTEM_VERSION,
                source_protocol_version=source["protocol_version"],
                source_manifest_fingerprint=source_fingerprint,
                source_manifest_sha256=source["selection_bundle_sha256"],
                source_scope=_SCOPE, planned_case_count=len(partition_cases),
                distinct_sample_count=len({case["sample_id"] for case in partition_cases}),
                question_count=len(bundle["questions"]),
                questions_sha256=fingerprint(bundle["questions"]),
                documents_sha256=fingerprint(bundle["documents"]),
                decisions_sha256=fingerprint(bundle["decisions"]),
            )
            if suite in _LEGACY:
                manifest["distractor_provenance"] = {
                    "scope": "valid canonical selected cases only; not the complete raw export or upstream split",
                    "order": "source_row_index ascending; first exact passage occurrence",
                    "source_manifest_fingerprint": source_fingerprint,
                    "passages_sha256": fingerprint(distractors),
                }
            partitions.append({"partition_id": partition_id, "group": group,
                               "case_ids": [case["case_id"] for case in partition_cases],
                               "product_bundle": bundle})
    ordered_decisions = [decisions[case["case_id"]] for case in cases]
    plan = {"partitions": partitions, "decisions": ordered_decisions,
            "reviews": saved_reviews, "max_documents": max_documents}
    plan["manifest"] = {
        "protocol_version": PARTITION_VERSION, "suite": suite,
        "source_protocol_version": source["protocol_version"],
        "selection_bundle_sha256": source["selection_bundle_sha256"],
        "source_manifest_fingerprint": source_fingerprint, "cases_sha256": fingerprint(cases),
        "partitions_sha256": fingerprint(partitions), "decisions_sha256": fingerprint(ordered_decisions),
        "reviews_sha256": fingerprint(saved_reviews), "planned_case_count": len(cases),
        "distinct_sample_count": len(samples), "partition_count": len(partitions),
        "applicable_case_count": sum(d["status"] == "applicable" for d in ordered_decisions),
        "decision_counts": dict(Counter(d["status"] for d in ordered_decisions)),
        "partition_rule": "task/title groups in first source occurrence order; same material kept together under its first group; continuous material batches",
        "source_scope": _SCOPE, "status": "prepared_not_validated",
    }
    plan["manifest"]["plan_sha256"] = fingerprint(plan)
    return plan


def load_partition_plan(path, cases, source):
    """Validate hashes and exact reconstruction before any runtime is created."""
    plan = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(plan, dict) or not isinstance(plan.get("manifest"), dict):
        raise ValueError("Malformed partition plan")
    manifest = plan["manifest"]
    if manifest.get("protocol_version") != PARTITION_VERSION:
        raise ValueError("Unsupported partition protocol")
    unhashed = deepcopy(plan)
    expected = unhashed["manifest"].pop("plan_sha256", None)
    if expected != fingerprint(unhashed):
        raise ValueError("Partition plan content hash changed")
    if (manifest.get("source_manifest_fingerprint") != fingerprint(source)
            or manifest.get("cases_sha256") != fingerprint(cases)):
        raise ValueError("Partition plan refers to different source cases")
    if "reviews" not in plan or "max_documents" not in plan:
        raise ValueError("Partition reconstruction inputs missing")
    rebuilt = build_partition_plan(cases, source, plan["reviews"], plan["max_documents"])
    if plan != rebuilt:
        raise ValueError("Partition plan no longer reproduces exact canonical contents")
    return plan
