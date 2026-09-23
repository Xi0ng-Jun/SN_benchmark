"""Pure selection/partition identity helpers; no product or SDK execution."""
from copy import deepcopy

from .starter_protocol import fingerprint
from .ifeval_protocol import DIRECT_POLICY, scorer_for


def execution_context(source, selected_memberships, plan=None, partition=None):
    from .selection_partitions import PARTITION_VERSION
    return {
        "selection_protocol": source["selection_protocol"],
        "selection_bundle_sha256": source["selection_bundle_sha256"],
        "selected_memberships": selected_memberships,
        "corpus_protocol": PARTITION_VERSION if plan is not None else None,
        "partition_plan_sha256": fingerprint(plan) if plan is not None else None,
        "partition_id": partition["partition_id"] if partition is not None else None,
        "case_ids": list(partition["case_ids"]) if partition is not None else None,
    }


def select_partition(cases, source, plan_path, partition_id):
    from .selection_partitions import load_partition_plan
    plan = load_partition_plan(plan_path, cases, source)
    matches = [p for p in plan["partitions"] if p["partition_id"] == partition_id]
    if len(matches) != 1:
        raise ValueError("Unknown or duplicate partition ID; choose an explicit planned partition")
    partition = matches[0]
    by_id = {c["case_id"]: c for c in cases}
    selected = [by_id[key] for key in partition["case_ids"]]
    if not selected:
        raise ValueError("Empty partition cannot start a product runtime")
    return (selected, deepcopy(partition["product_bundle"]),
            execution_context(source, len(cases), plan, partition), plan)


def expected_scorers(suite, track, *, ifeval_scoring=DIRECT_POLICY):
    """Reuse scorer identities from the existing runner; no metric construction."""
    from .starter_runner import BOOLQ_SCORER, PRODUCT_CORRECTNESS, FAITHFULNESS, EVIDENCE, CITATIONS
    from .starter_protocol import SUITES
    from .public_expansion_protocol import EXPANSION_SUITES
    from .system_product import SYSTEM_SUITES
    from .system_scoring import primary_scorer
    if suite == "ifeval":
        return [scorer_for(track, ifeval_scoring)] + ([CITATIONS] if track == "R" else [])
    if track == "N":
        return [{**SUITES, **EXPANSION_SUITES}[suite]["scorer"]]
    if suite in SYSTEM_SUITES:
        return [primary_scorer(suite), CITATIONS] + ([EVIDENCE] if suite == "logiqa" else [])
    return [BOOLQ_SCORER if suite == "boolq" else PRODUCT_CORRECTNESS, FAITHFULNESS, EVIDENCE, CITATIONS]


def validate_saved_selection(run, manifest, planned, outputs):
    """Bind a single saved run to its copied full selection and partition plan."""
    from collections import Counter
    import json
    from .selection_bundle import load_selection_bundle
    selection = load_selection_bundle(run / "input")
    source, cases = selection["native_source"], selection["cases"]
    if manifest["identity"]["source"] != source or manifest["source_manifest"] != source:
        raise ValueError("Saved selection source differs from run identity")
    if manifest["track"] == "R":
        context = manifest["identity"].get("selection_context") or {}
        cases, product, expected_context, _ = select_partition(
            cases, source, run / "partition-plan.json", context.get("partition_id"))
        if (product != json.loads((run / "product-bundle.json").read_text(encoding="utf-8"))
                or product["manifest"] != manifest["identity"]["product_bundle"]):
            raise ValueError("Saved partition product bundle differs from the frozen plan")
    else:
        expected_context = execution_context(source, len(cases))
    if (manifest.get("selection_context") != expected_context
            or manifest["identity"].get("selection_context") != expected_context):
        raise ValueError("Saved selection context differs from the full frozen plan")
    expected = Counter((c["case_id"], scorer) for c in cases
                       for scorer in expected_scorers(source["suite"], manifest["track"],
                                                      ifeval_scoring=manifest["identity"].get("ifeval_scoring")))
    if Counter((p["case_id"], p["scorer"]) for p in planned) != expected:
        raise ValueError("Saved result plan drops or changes frozen selection cases/scorers")
    by_id = {c["case_id"]: c for c in cases}
    for row in [*planned, *outputs]:
        case = by_id.get(row["case_id"])
        if case is None or any(row.get(k) != case[k] for k in ("sample_id", "suite", "task")):
            raise ValueError("Saved record identity differs from its selected source case")
