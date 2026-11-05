"""method-v4 config, verdict parsing, endpoint report (pure parts)."""
import json

import pytest

from rag_eval.optimizer.v4 import endpoint
from rag_eval.optimizer.v4.config import ConfigError, load
from rag_eval.optimizer.v4.sn_adapter_v4 import parse_verdict


def test_config_is_strict_and_reports_variants(tmp_path):
    path = tmp_path / "c.json"
    path.write_text(json.dumps(dict(time_budget_hours=8, c=0.5, adapter=dict(reviewer_profile="claude-kimi-k3"),
                                    safeguard_tolerances=dict(cost_per_task=1.0))))
    loaded = load(path)
    assert loaded.config.c == 0.5 and loaded.adapter_settings["reviewer_profile"] == "claude-kimi-k3"
    assert loaded.config.safeguard_tolerances == dict(infrastructure_failure_rate=0.05, cost_per_task=1.0)
    assert set(loaded.config.variants()) == {"c", "safeguard_tolerances"}
    for bad in (dict(time_budget_hours=8, typo=1), dict(c=1), dict(time_budget_hours=8, c=0.4, c_mech=0.6),
                dict(time_budget_hours=8, k_val=3), dict(time_budget_hours=8, adapter=dict(nope=1))):
        path.write_text(json.dumps(bad))
        with pytest.raises(ConfigError):
            load(path)


def test_parse_verdict_keeps_only_known_pairs_and_coerces(tmp_path):
    path = tmp_path / "v.json"
    path.write_text(json.dumps(dict(pairs=[
        dict(pair="p1", effect=True, changed_side="B", gaming="yes", note="x" * 900),
        dict(pair="p9", effect=True), "junk", dict(pair="p2", changed_side="C")])))
    rows = parse_verdict(path, ["p1", "p2"])
    assert rows[0] == dict(pair="p1", effect=True, changed_side="B", side_effect=False, gaming=False, note="x" * 500)
    assert rows[1]["changed_side"] is None and len(rows) == 2
    with pytest.raises(RuntimeError):
        parse_verdict(tmp_path / "missing.json", ["p1"])


def test_endpoint_report_interval_shrinkage_and_conclusion():
    lineage = [dict(version="s", kind="seed", round=0), dict(version="a", round=1, delta_val=0.03, tier="confirmed"),
               dict(version="b", round=2, delta_val=0.02, tier="unconfirmed")]
    nodes = [dict(index=0, version="s", candidate_id="cs"), dict(index=1, version="b", candidate_id="cb")]
    result = dict(status="finished", metric="str_em", n=98, k=1, curve=[
        dict(candidate_id="cs", score=0.40, delta_vs_seed=0.0, se_delta=0.0),
        dict(candidate_id="cb", score=0.43, delta_vs_seed=0.03, se_delta=0.02, boot_lower=-0.004, boot_upper=0.061,
             boot_units=30, boot_unit="family")])
    rep = endpoint.report(result, nodes, lineage, k_val=2)
    final = rep["points"][-1]
    assert final["delta_val_cumulative"] == pytest.approx(0.05) and final["shrinkage"] == pytest.approx(0.02)
    assert final["ci95"] == [-0.004, 0.061] and final["ci95_method"] == "bootstrap" and not rep["confirmed"]
    assert final["ci95_normal"][0] < 0 < final["ci95_normal"][1]
    assert rep["conclusion"] == "the test set did not confirm an improvement" and rep["deploy"] == "cb"
    assert rep["warnings"]       # k_sealed differs from k_val
    with pytest.raises(ValueError):
        endpoint.report(result, nodes[::-1], lineage, k_val=1)


def test_endpoint_node_set_uses_the_pruned_final_version():
    from rag_eval.optimizer.adapter import Version
    lineage = [dict(version="s", kind="seed", round=0), dict(version="a", round=1, delta_val=0.03),
               dict(version="b", round=2, delta_val=0.001)]
    versions = {h: Version(h, metadata=dict(candidate_id="c" + h)) for h in ("s", "a", "b", "p")}
    assert [n["version"] for n in endpoint.node_set(lineage, versions, "seed_final")] == ["s", "b"]
    pruned = endpoint.node_set(lineage, versions, "seed_final", final="p")
    assert [n["version"] for n in pruned] == ["s", "p"] and pruned[-1]["kind"] == "pruned"
    assert [n["version"] for n in endpoint.node_set(lineage, versions, "lineage", final="p")] == ["s", "a", "b", "p"]
    ab = dict(status="done", accepted=True, final="p", check=dict(delta=-0.001))
    assert endpoint._cumulative_val(lineage, ab)["p"] == pytest.approx(0.03)


def test_endpoint_figure_renders(tmp_path):
    pytest.importorskip("matplotlib")
    rep = dict(metric="str_em", points=[
        dict(index=0, score=0.4, ci95=None, elapsed_s=0.0),
        dict(index=1, score=0.45, ci95=[0.01, 0.09], elapsed_s=7200.0)])
    out = tmp_path / "c.svg"
    endpoint.figure(rep, out, lang="en")
    assert out.read_text().startswith("<?xml")
