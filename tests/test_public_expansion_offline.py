import json
from pathlib import Path

from scripts.check_public_expansion_offline import audit_manifest, audit_path


def _manifest(**source):
    return {
        "protocol_version": "public-starter-v1",
        "status": "prepared_not_validated",
        "suite": "mmlu",
        "source": {
            "dataset": "cais/mmlu",
            "split": "test",
            "revision": "v1",
            "source_url": "https://example.invalid/source",
            "license": "MIT",
            "license_url": "https://example.invalid/license",
            "conversion": "local-jsonl",
            "origin_sha256": "a" * 64,
            "export_sha256": "b" * 64,
            **source,
        },
        "scorer": "deepeval.mmlu",
        "artifacts": {},
        "model_predictions": 0,
        "human_review": "pending",
        "release_gate": False,
    }


def test_missing_source_hash_is_reported():
    manifest = _manifest()
    del manifest["source"]["export_sha256"]

    errors = audit_manifest(manifest)

    assert any(error["code"] == "missing_source_hash" for error in errors)


def test_manifest_checks_suite_and_product_applicability():
    manifest = _manifest()
    manifest["suite"] = "hellaswag"
    manifest["source"]["dataset"] = "Rowan/hellaswag"
    manifest["scorer"] = "deepeval.hellaswag"
    manifest["product_review"] = {"status": "pending", "reason": None}

    errors = audit_manifest(manifest)

    assert any(error["code"] == "manifest_product_applicability_mismatch" for error in errors)


def test_audit_path_rejects_invalid_trace_value(tmp_path: Path):
    path = tmp_path / "run.json"
    path.write_text(json.dumps({"trace": {"trace_id": "t1", "completeness": "invented", "spans": []}}), encoding="utf-8")

    report = audit_path(path)

    assert any(error["code"] == "invalid_trace_completeness" for error in report["errors"])
