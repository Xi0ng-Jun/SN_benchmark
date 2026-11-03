"""Tests for curve_view.project_curve and adoption/path helpers."""
from __future__ import annotations

import json
import math

import pytest

from rag_eval.optimizer.curve_view import adoption_rounds, main_path, project_curve, read_curve, run_rounds


def _node(vh, parent=None, state="certified", card=None):
    return dict(version_hash=vh, parent_hash=parent, state=state, card_ref=card)


def _round(round_id, candidates):
    return dict(round_id=round_id, candidates=candidates)


def _cand(verdict, candidate, parent=None, superseded=False):
    return dict(verdict=verdict, candidate=candidate, parent=parent, superseded=superseded)


def _point(index, vh, state, delta=None):
    score = 0.40 + (delta or 0)
    lo = score - 0.04 if delta is not None and index > 0 else None
    hi = score + 0.04 if delta is not None and index > 0 else None
    return dict(index=index, version_hash=vh, candidate_id=vh, state=state, style=None, status="measured",
                score=score, se=0.01, delta_vs_seed=delta, lower_adj=lo, upper_adj=hi, p_holm=None, holds_holm=None)


def _sealed(points):
    return dict(kind="v3-sealed-lineage", status="finished", metric="f1", n=20, k=2, alpha=0.05, z=1.65, z_adj=2.1, m=max(0, len(points)-1), correction="holm", points=points)


# -- adoption_rounds ----------------------------------------------------------

def test_adoption_rounds_picks_correct_verdicts():
    rounds = [
        _round(1, [_cand("screen_fail", "v1"), _cand("screen_pass", "v2")]),
        _round(2, [_cand("screen_pass", "v3")]),
    ]
    m = adoption_rounds(rounds)
    assert m == {"v2": 1, "v3": 2}


def test_adoption_rounds_superseded_is_demoted():
    # The non-superseded record wins even if it comes from a later round.
    rounds = [
        _round(1, [_cand("screen_pass", "v1", superseded=True)]),
        _round(2, [_cand("screen_pass", "v1", superseded=False)]),
    ]
    assert adoption_rounds(rounds)["v1"] == 2


def test_adoption_rounds_empty():
    assert adoption_rounds([]) == {}


# -- main_path ----------------------------------------------------------------

def test_main_path_simple_chain():
    lineage = [_node("s"), _node("a", parent="s"), _node("b", parent="a")]
    assert main_path(lineage) == ["s", "a", "b"]


def test_main_path_rolled_back_excluded():
    lineage = [_node("s"), _node("a", parent="s"), _node("rb", parent="a", state="rolled_back"), _node("c", parent="a")]
    path = main_path(lineage)
    assert "rb" not in path
    assert path == ["s", "a", "c"]


def test_main_path_all_rolled_back_gives_seed():
    lineage = [_node("s"), _node("a", parent="s", state="rolled_back")]
    assert main_path(lineage) == ["s"]


# -- project_curve ------------------------------------------------------------

def _simple_run():
    """seed (round 0) + one certified node (round 1) + one rolled-back (round 2)."""
    lineage = [_node("s"), _node("a", parent="s"), _node("rb", parent="a", state="rolled_back")]
    rounds = [
        _round(1, [_cand("screen_pass", "a")]),
        _round(2, [_cand("screen_pass", "rb")]),
    ]
    points = [_point(0, "s", "certified", 0.0), _point(1, "a", "certified", 0.08), _point(2, "rb", "rolled_back", 0.02)]
    return project_curve(_sealed(points), lineage, rounds)


def test_project_curve_rounds():
    c = _simple_run()
    assert c["points"][0]["round"] == 0
    assert c["points"][1]["round"] == 1
    assert c["points"][2]["round"] == 2


def test_project_curve_main_path():
    c = _simple_run()
    assert c["path"] == [0, 1]
    assert c["points"][2]["on_path"] is False
    assert c["points"][1]["on_path"] is True


def test_project_curve_parent_index():
    c = _simple_run()
    assert c["points"][1]["parent_index"] == 0   # a's parent is s (index 0)
    assert c["points"][2]["parent_index"] == 1   # rb's parent is a (index 1)


def test_project_curve_slots_multiple_in_one_round():
    lineage = [_node("s"), _node("a", parent="s"), _node("b", parent="a")]
    rounds = [_round(5, [_cand("screen_pass", "a"), _cand("screen_pass", "b")])]
    points = [_point(0, "s", "certified", 0.0), _point(1, "a", "certified", 0.07), _point(2, "b", "certified", 0.09)]
    c = project_curve(_sealed(points), lineage, rounds)
    assert c["points"][1]["round"] == 5 and c["points"][2]["round"] == 5
    assert c["points"][1]["slots"] == 2 and c["points"][2]["slots"] == 2
    assert c["points"][1]["slot"] == 0 and c["points"][2]["slot"] == 1
    assert c["max_round"] == 5


def test_project_curve_y_bounds_on_delta():
    c = _simple_run()
    seed_score = c["seed_score"]
    p = c["points"][1]
    assert math.isclose(p["y_lower"], seed_score + p["lower_adj"], abs_tol=1e-9)
    assert math.isclose(p["y_upper"], seed_score + p["upper_adj"], abs_tol=1e-9)


def test_project_curve_seed_has_no_whisker():
    # The seed point in the fixture has no lower_adj/upper_adj (not carried by the fixture),
    # so y_lower/y_upper are None — there is nothing to draw a whisker from.
    c = _simple_run()
    p = c["points"][0]
    assert p["y_lower"] is None and p["y_upper"] is None


def test_project_curve_round_inferred_when_no_screen_pass():
    lineage = [_node("s"), _node("a", parent="s")]
    rounds = []  # no screen_pass record at all
    points = [_point(0, "s", "certified", 0.0), _point(1, "a", "certified", 0.07)]
    c = project_curve(_sealed(points), lineage, rounds)
    assert c["points"][1]["round_inferred"] is True
    assert c["points"][1]["round"] == 0  # falls back to latest known (0 = seed)


def test_project_curve_returns_none_for_empty_sealed():
    lineage = [_node("s")]
    assert project_curve(None, lineage, []) is None
    assert project_curve(dict(status="finished", points=[]), lineage, []) is None


# -- run_rounds ---------------------------------------------------------------

def test_run_rounds_merges_by_round_id():
    state = dict(rounds=[dict(round_id=1, candidates=[_cand("screen_pass", "v1")]),
                         dict(round_id=2, candidates=[])])
    jsonl = [dict(round_id=1, candidates=[])]  # duplicate of round 1, state.json wins
    merged = run_rounds(state, jsonl)
    ids = [r["round_id"] for r in merged]
    assert ids == [1, 2]  # no duplicate


def test_run_rounds_open_round_included():
    state = dict(rounds=[], current_round=dict(round=dict(round_id=3, candidates=[])))
    assert [r["round_id"] for r in run_rounds(state)] == [3]


# -- read_curve integration ---------------------------------------------------

def test_read_curve_no_sealed_file_returns_none(tmp_path):
    (tmp_path / "state.json").write_text(json.dumps(dict(lineage=[], rounds=[], versions={})))
    assert read_curve(tmp_path) is None
