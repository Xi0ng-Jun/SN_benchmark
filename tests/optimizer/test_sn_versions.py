"""SN versions are v2 iteration-tree candidates (binding plan §2.1, §2.5)."""
from __future__ import annotations

import subprocess

import pytest

from rag_eval.optimizer import ProtocolConfig, run_optimization
from rag_eval.optimizer.adapter import Card, Diff, Version
from rag_eval.optimizer.sn_adapter import SNAdapter, _v2_card_to_v3_card
from tests.optimizer.mock_adapter import MockAdapter

CONFIG = {"iteration_id": "I", "task_id": "T", "run_id": "R1", "actor": "v3-test"}


class _Conflict(Exception):
    pass


class _FakeService:
    """IterationService subset: candidate / provision / freeze."""

    def __init__(self, worktree_path="/wt/child", ready_after=2):
        self.candidates = {
            "root": dict(id="root", status="frozen", frozen_commit="c0", parent_candidate_id=None,
                         proposal_id=None, parent_commit=None,
                         worktree=dict(status="ready", path="/wt/root")),
        }
        self.worktree_path, self.ready_after = worktree_path, ready_after
        self.provisions, self.freezes = 0, []

    def candidate(self, cid):
        return dict(self.candidates[cid])

    def provision(self, cid):
        self.provisions += 1
        c = self.candidates[cid]
        if self.provisions >= self.ready_after:
            c["status"] = "ready"
            c["worktree"] = dict(status="ready", path=self.worktree_path, branch="sn-cand-" + cid,
                                 base_revision=c["parent_commit"])
        return dict(c)

    def freeze(self, cid, *, actor):
        c = self.candidates[cid]
        if c["status"] == "frozen":
            raise _Conflict("Candidate is already frozen; derive a child for further changes")
        self.freezes.append((cid, actor))
        c.update(status="frozen", frozen_commit="c-" + cid)
        return dict(c)


class _FakeRoundOps:
    def __init__(self, service, approval="approved"):
        self.service, self.approval, self.calls = service, approval, []
        self._derived = {}

    def approve(self, hypothesis_id, *, iteration_id, key, actor):
        self.calls.append(("approve", hypothesis_id, dict(iteration_id=iteration_id, key=key, actor=actor)))
        return dict(status=self.approval)

    def derive(self, baseline_id, *, hypothesis_id, key, actor):
        self.calls.append(("derive", baseline_id, dict(hypothesis_id=hypothesis_id, key=key, actor=actor)))
        if key not in self._derived:  # v2: same key returns the same child
            cid = f"cand{len(self._derived) + 1}"
            parent = self.service.candidates[baseline_id]
            self.service.candidates[cid] = dict(
                id=cid, status="provisioning", frozen_commit=None, parent_candidate_id=baseline_id,
                proposal_id=hypothesis_id, parent_commit=parent["frozen_commit"],
                worktree=dict(status="unknown", path=None))
            self._derived[key] = cid
        return self._derived[key]

    def agent(self, role, candidate_id, **kw):
        self.calls.append(("agent", role, candidate_id, kw))
        return dict(status="idle")


def _adapter(service=None, **kw):
    service = service or _FakeService()
    ops = _FakeRoundOps(service, **kw)
    return SNAdapter(dict(CONFIG), round_operations=ops, agent_poll_interval=0), ops, service


CARD = Card(failure_mode_desc="empty retrieval", change_hypothesis="retry", ref="H1")


def test_v2_card_carries_hypothesis_id_as_ref():
    card = _v2_card_to_v3_card({"id": "H9", "card": {"title": "t", "implementer_brief": "b"}})
    assert card.ref == "H9"


def test_version_for_reads_frozen_candidate():
    adapter, _, _ = _adapter()
    v = adapter.version_for("root")
    assert v.content_hash == "c0"
    assert v.metadata["candidate_id"] == "root" and v.metadata["parent_candidate_id"] is None


def test_version_for_refuses_unfrozen_candidate():
    adapter, ops, service = _adapter()
    ops.derive("root", hypothesis_id="H1", key="k", actor="a")
    with pytest.raises(ValueError, match="not frozen"):
        adapter.version_for("cand1")


def test_workspace_approves_then_derives_with_deterministic_keys():
    adapter, ops, service = _adapter()
    ws = adapter.workspace(adapter.version_for("root"), CARD)
    kinds = [c[0] for c in ops.calls]
    assert kinds == ["approve", "derive"]
    assert ops.calls[0][1] == "H1"
    assert ops.calls[0][2] == dict(iteration_id="I", key="v3/R1/approve/H1", actor="v3-test")
    assert ops.calls[1][1] == "root"
    assert ops.calls[1][2] == dict(hypothesis_id="H1", key="v3/R1/derive/root/H1", actor="v3-test")
    assert service.provisions >= 2  # waited until the worktree was ready
    assert ws["candidate_id"] == "cand1" and ws["parent_candidate_id"] == "root"
    assert ws["hypothesis_id"] == "H1" and ws["path"] == "/wt/child"
    # A rerun (crash/resume) replays the same keys and returns the same child.
    again = _adapter(service)[0]
    again._round_ops._derived = ops._derived
    assert again.workspace(adapter.version_for("root"), CARD)["candidate_id"] == "cand1"


def test_workspace_rejects_unapproved_card_and_missing_ref():
    adapter, ops, _ = _adapter(approval="rejected")
    with pytest.raises(RuntimeError, match="not approved"):
        adapter.workspace(adapter.version_for("root"), CARD)
    assert [c[0] for c in ops.calls] == ["approve"]  # no derive
    with pytest.raises(ValueError):
        adapter.workspace(adapter.version_for("root"), Card("x", "y"))


def test_workspace_needs_candidate_version_and_run_id():
    adapter, _, _ = _adapter()
    with pytest.raises(ValueError, match="candidate_id"):
        adapter.workspace(Version("c0", metadata={}), CARD)
    no_run = SNAdapter({k: v for k, v in CONFIG.items() if k != "run_id"},
                       round_operations=_FakeRoundOps(_FakeService()), agent_poll_interval=0)
    with pytest.raises(ValueError, match="run_id"):
        no_run.workspace(no_run.version_for("root"), CARD)


def test_workspace_times_out_when_worktree_never_ready():
    service = _FakeService(ready_after=10 ** 9)
    adapter = SNAdapter(dict(CONFIG), round_operations=_FakeRoundOps(service),
                        agent_poll_interval=0, worktree_timeout=0.01)
    with pytest.raises(TimeoutError):
        adapter.workspace(adapter.version_for("root"), CARD)


def test_implement_passes_candidate_and_prompt_forbids_freeze_and_runs():
    adapter, ops, _ = _adapter()
    ws = adapter.workspace(adapter.version_for("root"), CARD)
    diff = adapter.implement(CARD, ws)
    _, role, cid, kw = ops.calls[-1]
    assert (role, cid) == ("implementer", "cand1")
    assert kw["hypothesis_id"] == "H1" and kw["key"] == "v3/R1/implement/cand1"
    text = kw["text"]
    assert "node freeze" not in text and "node run" not in text
    assert "do not freeze" in text and "do not run or request any evaluation" in text
    assert diff.metadata["candidate_id"] == "cand1"
    assert diff.metadata["parent_candidate_id"] == "root"


def test_implement_diff_includes_untracked_files(tmp_path):
    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)
    git("init", "-q")
    (tmp_path / "a.py").write_text("x = 1\n")
    git("add", "a.py")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
    (tmp_path / "a.py").write_text("x = 2\n")
    (tmp_path / "new.py").write_text("y = 3\n")
    adapter, _, _ = _adapter(_FakeService(worktree_path=str(tmp_path)))
    ws = adapter.workspace(adapter.version_for("root"), CARD)
    content = adapter.implement(CARD, ws).content
    assert "+x = 2" in content and "new.py" in content and "+y = 3" in content


def test_materialize_freezes_and_returns_frozen_commit():
    adapter, ops, service = _adapter()
    root = adapter.version_for("root")
    diff = adapter.implement(CARD, adapter.workspace(root, CARD))
    v = adapter.materialize(diff, root)
    assert service.freezes == [("cand1", "v3-test")]
    assert v.content_hash == "c-cand1"
    assert v.metadata["candidate_id"] == "cand1"
    assert v.metadata["parent_candidate_id"] == "root" and v.metadata["hypothesis_id"] == "H1"
    # Rerun after a crash: already frozen is read back, not frozen twice.
    assert adapter.materialize(diff, root) == v
    assert len(service.freezes) == 1


def test_materialize_rejects_foreign_parent_and_missing_candidate():
    adapter, _, _ = _adapter()
    root = adapter.version_for("root")
    diff = adapter.implement(CARD, adapter.workspace(root, CARD))
    with pytest.raises(ValueError, match="not a child"):
        adapter.materialize(diff, Version("zz", metadata={"candidate_id": "other"}))
    with pytest.raises(ValueError, match="candidate_id"):
        adapter.materialize(Diff(content=""), root)


# -- loop keeps real Version objects ----------------------------------------

def test_loop_passes_versions_with_metadata():
    adapter = MockAdapter(script=[
        {"cards": ["fm-a", "fm-b"], "deltas": {f"t{i}": 0.12 for i in range(6)}, "smoke_pass": True},
        {"cards": ["fm-c"], "deltas": {f"t{i}": 0.12 for i in range(6)}, "smoke_pass": True},
    ], n_tasks=6)
    cfg = ProtocolConfig(mde=0.05, sigma_d=0.1, ladder_c=1.0, K_max=20, T=0.06, n_fresh=6,
                         b_cards=2, k_screen=1, k_smoke=1, n_smoke_sample=2,
                         predicate_coverage_floor=0.0)
    ids = [f"t{i}" for i in range(6)]
    run_optimization(adapter, adapter.seed, cfg, search_set_ids=ids, val_pool_ids=ids,
                     fresh_batch_ids=[ids, ids], smoke_sample_ids=ids[:2], max_rounds=3)
    methods = {m for m, _ in adapter.received}
    assert {"workspace", "materialize", "evaluate", "analyse"} <= methods
    for method, version in adapter.received:
        assert version.metadata.get("candidate_id") == version.content_hash, (method, version)
    # Non-seed parents were handed over as materialized (carry their parent link).
    assert any(v.metadata.get("parent_candidate_id") for m, v in adapter.received if m == "workspace")
