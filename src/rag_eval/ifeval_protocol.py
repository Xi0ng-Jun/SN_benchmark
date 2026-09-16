"""Runtime scoring identities, independent of immutable v1 dataset metadata."""

DIRECT_POLICY = "deepeval-ifeval-direct-v1"
NATIVE_SCORER = "deepeval.ifeval.all_instructions.v1"
PRODUCT_SCORER = "product.ifeval.all_instructions.full_body.v2"
LEGACY_NATIVE_SCORER = "deepeval.ifeval.audited_all_instructions"
LEGACY_PRODUCT_SCORER = "product.ifeval.audited_all_instructions.full_body.v1"


def scorer_for(track, policy=DIRECT_POLICY):
    """New plans default to direct scoring; absent saved policy means legacy."""
    if track not in {"N", "R"}:
        raise ValueError("Unknown IFEval track")
    if policy == DIRECT_POLICY:
        return NATIVE_SCORER if track == "N" else PRODUCT_SCORER
    if policy is None:
        return LEGACY_NATIVE_SCORER if track == "N" else LEGACY_PRODUCT_SCORER
    raise ValueError("Unknown IFEval scoring policy: " + str(policy))
