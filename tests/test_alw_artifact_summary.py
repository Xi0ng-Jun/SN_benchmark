"""Milestone 2: per-trial artifact summary (review Item 21) and secret scrubbing."""
from __future__ import annotations

import json
import re

import pytest

from ssap.kernel import artifact_summary as asum

BASE = dict(task_id="t-dev-01", seed=1, version_id="H_base", model_id="m", key_slot=0,
            agent_image="img@sha256:abc", watermarks={"t_soft_sec": 9900},
            pre_repair_gds_sha256="a" * 64, post_repair_gds_sha256=None,
            submission_gds_sha256="a" * 64, submission_src_tree_sha256="s" * 64,
            draft_latest_gds_sha256="d" * 64, m_pre=[1, 1, 1, 1, 1, 0], u_pre=[0, 0, 0, 0, 0, -2.0],
            raw_u_pre=[0, 0, 1.5, 0.2, 0, -2.0], gate_triggered=True, trigger_reasons=["m_best_not_all_certified"],
            repair_ran=False, ratchet_promoted=False, promote_reason=None, phase_a_reused=True,
            phase_b_invoked=False, model_request_diverged=False, infra_error=False,
            outer_metrics={"Y_star": 0.3, "C_star": 0}, cost_ledger={"wall_sec": 10.0})


def test_submission_artifact_diverged_without_model_request_divergence(tmp_path):
    baseline = asum.build_summary(**BASE)
    hooked = asum.build_summary(**{**BASE, "submission_gds_sha256": "b" * 64}, baseline=baseline)
    assert hooked["model_request_diverged"] is False and hooked["submission_artifact_diverged"] is True
    src_only = asum.build_summary(**{**BASE, "submission_src_tree_sha256": "t" * 64}, baseline=baseline)
    assert src_only["submission_artifact_diverged"] is True
    same = asum.build_summary(**BASE, baseline=baseline)
    assert same["submission_artifact_diverged"] is False
    assert asum.build_summary(**BASE)["submission_artifact_diverged"] is None   # no baseline


def test_credentials_and_literal_secrets_are_scrubbed(tmp_path):
    secrets = asum.literal_secrets_from_env(env={"SSAP_UPSTREAM_KEY": "sk-abc123secret",
                                                 "SSAP_UPSTREAM_KEYS": "k1-aaaa1111, k2-bbbb2222"})
    assert secrets == ["sk-abc123secret", "k1-aaaa1111", "k2-bbbb2222"]
    leaky = {**BASE, "infra_reason": "proxy http://user:p/a@ss@host:8080 failed; key sk-abc123secret",
             "agent_image": "socks5://u:pw@proxy.local:1080 k2-bbbb2222",
             "cost_ledger": {"wall_sec": 1.0, "note": "https://x:y@gw.example/v1"}}
    s = asum.build_summary(**leaky, secrets=secrets)
    j, m = asum.write_summary(tmp_path, s, secrets)
    for text in (j.read_text(), m.read_text()):
        for bad in ("user", "p/a", "ss@host", "sk-abc123secret", "k2-bbbb2222", "u:pw", "x:y@"):
            assert bad not in text, bad
        assert "http://***@host:8080" in text and "socks5://***@proxy.local:1080" in text
    assert asum.scrub_text("ftp://a@b.c and git+ssh://me:x@h/r") == "ftp://***@b.c and git+ssh://***@h/r"


def test_json_and_markdown_schema_complete(tmp_path):
    s = asum.build_summary(**BASE)
    assert list(s)[:1] == ["schema_version"] and set(asum.FIELDS) <= set(s)
    required = {"task_id", "seed", "version_id", "model_id", "key_slot", "agent_image", "watermarks",
                "pre_repair_gds_sha256", "post_repair_gds_sha256", "submission_gds_sha256",
                "submission_src_tree_sha256", "draft_latest_gds_sha256", "m_pre", "u_pre", "raw_u_pre",
                "m_post", "u_post", "raw_u_post", "gate_triggered", "trigger_reasons", "repair_ran",
                "ratchet_promoted", "promote_reason", "phase_a_reused", "phase_b_invoked",
                "model_request_diverged", "submission_artifact_diverged", "infra_error", "infra_reason",
                "outer_metrics", "cost_ledger"}
    assert required == set(asum.FIELDS)
    assert set(asum.OUTER_METRIC_KEYS) <= set(s["outer_metrics"]) and s["outer_metrics"]["d_star"] is None
    assert set(asum.COST_KEYS) <= set(s["cost_ledger"]) and s["cost_ledger"]["usd"] is None
    j, m = asum.write_summary(tmp_path, s)
    doc = json.loads(j.read_text())
    assert doc == json.loads(json.dumps(s))
    md = m.read_text()
    for k in asum.FIELDS:
        if k not in ("outer_metrics", "cost_ledger", "watermarks"):
            assert re.search(rf"^\| `{k}` \|", md, re.M), k
    for section in ("## watermarks", "## outer_metrics", "## cost_ledger"):
        assert section in md
    with pytest.raises(ValueError, match="unknown summary fields"):
        asum.build_summary(**BASE, bogus=1)
