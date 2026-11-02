"""W7: the method-v3 CLI (rag_eval.optimizer.cli), with build_services replaced by fakes."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from rag_eval.optimizer import cli
from rag_eval.optimizer.adapter import Card, EvaluationUnavailable, Predicate
from tests.optimizer.mock_adapter import MockAdapter

POOL = [f"t{i}" for i in range(6)]
FRESH = [f"t{i}" for i in range(6, 12)]
BASE_PROTOCOL = dict(mde=0.05, sigma_d=0.1, ladder_c=1.0, K_max=20, T=10.0, n_fresh=6,
                     b_cards=2, k_screen=1, predicate_coverage_floor=0.0,
                     max_rounds=1, smoke_sample_size=2,
                     agent_poll_interval=0.0, eval_poll_interval=0.0)


class FakeSN(MockAdapter):
    """MockAdapter plus the SN-only surface the CLI reads; records every call by name."""

    def __init__(self, *a, k_base=1, completed=(), **kw):
        super().__init__(*a, n_tasks=12, **kw)
        self.k_base, self.completed = k_base, list(completed)
        self.calls: list[str] = []
        self.last_analysis: dict = {}
        self.last_analyst_text = None

    def partitions(self):
        self.calls.append("partitions")
        return dict(search=list(POOL), validation=list(POOL),
                    fresh_batches=[dict(batch_id="f1", case_ids=list(FRESH))],
                    completed_fresh_batch_ids=list(self.completed), k_base=self.k_base,
                    safeguards=list(self.safeguard_specs))

    safeguard_specs = [dict(key="infrastructure_failure_rate", direction="lower_is_better", margin=0.1)]

    def version_for(self, candidate_id):
        self.calls.append("version_for")
        assert candidate_id == "root"
        return self.seed

    def evaluate(self, version, task_ids, k, *, purpose="screen"):
        self.calls.append(f"evaluate:{purpose}")
        return super().evaluate(version, task_ids, k, purpose=purpose)

    def evaluate_pair(self, version, reference, task_ids, k, *, purpose):
        self.calls.append(f"evaluate_pair:{purpose}")
        return super().evaluate_pair(version, reference, task_ids, k, purpose=purpose)

    def workspace(self, version, card=None):
        self.calls.append("workspace")
        return super().workspace(version, card)

    def implement(self, card, workspace):
        self.calls.append("implement")
        return super().implement(card, workspace)

    def materialize(self, diff, parent):
        self.calls.append("materialize")
        return super().materialize(diff, parent)

    def rebase(self, candidate, new_parent):
        self.calls.append("rebase")
        return super().rebase(candidate, new_parent)


class AnalystOnlySN(FakeSN):
    """analyse() renders a message and returns two cards, one with a v2 signature predicate."""

    def analyse(self, search_set_trajectories, n_cards, *, case_roles=None, parent=None,
                round_id=None, history=None):
        self.calls.append("analyse")
        self.analyse_args = dict(selected=dict(search_set_trajectories), n_cards=n_cards, roles=case_roles,
                                 parent=parent, round_id=round_id, history=history)
        self.last_analyst_text = "Diagnose ...\n[c1 failure] evidence"
        self.last_analysis = dict(selected=len(search_set_trajectories), with_view=2, shown=2, omitted=0,
                                  evidence_chars=33)
        sig = {"all_of": [{"field": "trace.n_empty_retrievals", "op": "gt", "value": 0},
                          {"field": "behavior.refused", "op": "eq", "value": True}]}
        return [Card("Empty retrievals", "retry", Predicate(sig, "decrease"), ref="h1"),
                Card("Verbose answers", "trim", None, ref="h2")][:n_cards]


@pytest.fixture
def services(monkeypatch):
    """Replace the service construction boundary; returns the list of build calls."""
    built: list[dict] = []
    holder: dict = {}

    def fake_build(config_path, state_root, iteration_id, *, actor, run_id, adapter_settings):
        built.append(dict(iteration_id=iteration_id, actor=actor, run_id=run_id, settings=adapter_settings))
        return cli.Services(adapter=holder["adapter"], iteration_id=iteration_id, task_id="task1",
                            root_candidate_id="root", writable=True)

    monkeypatch.setattr(cli, "build_services", fake_build)

    def use(adapter):
        holder["adapter"] = adapter
        return adapter
    use.built = built
    return use


def write_protocol(tmp_path: Path, name="protocol.json", **overrides) -> Path:
    data = dict(BASE_PROTOCOL)
    data.update(overrides)
    for key in [k for k, v in overrides.items() if v is None and k not in ("analyst_cards",)]:
        data.pop(key)
    path = tmp_path / name
    path.write_text(json.dumps(data))
    return path


def common(tmp_path, run_dir="run"):
    return ["--iteration", "it1", "--actor", "operator", "--run-id", "r1",
            "--state-dir", str(tmp_path / run_dir), "--config", "cfg.json", "--state", str(tmp_path / "wb")]


def run_cli(tmp_path, protocol, *extra, run_dir="run"):
    return cli.main(["run", *common(tmp_path, run_dir), "--protocol", str(protocol), *extra])


def state(tmp_path, run_dir="run"):
    return json.loads((tmp_path / run_dir / "state.json").read_text())


# ---------------------------------------------------------------------------
# argument validation and protocol strictness
# ---------------------------------------------------------------------------

def test_unknown_subcommand_and_missing_args_exit_2(tmp_path, services, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["frobnicate"])
    assert exc.value.code == 2
    services(FakeSN([]))
    assert cli.main(["run", *common(tmp_path)]) == 2            # no --protocol
    assert "--protocol" in capsys.readouterr().err
    assert cli.main(["run", "--protocol", str(write_protocol(tmp_path))]) == 2
    assert cli.main(["status"]) == 2                            # no --state-dir
    assert services.built == []


@pytest.mark.parametrize("overrides,message", [
    (dict(surprise=1), "unknown protocol key"),
    (dict(n_smoke_sample=3), "smoke_sample_size"),
    (dict(max_rounds=None), "missing protocol key"),
    (dict(mde=None), "missing protocol key"),
    (dict(b_cards=True), "integer"),
    (dict(k_screen=1.5), "integer"),
    (dict(T="high"), "number"),
    (dict(k_smoke=2), "k_smoke"),
    (dict(smoke_sample_size=0), "smoke_sample_size"),
    (dict(analyst_profile=""), "non-empty string"),
    (dict(analyst_view_budget=20000), "analyst_view_budget"),
])
def test_protocol_file_is_strict(tmp_path, services, capsys, overrides, message):
    adapter = services(FakeSN([]))
    assert run_cli(tmp_path, write_protocol(tmp_path, **overrides)) == 2
    assert message in capsys.readouterr().err
    assert services.built == [] and adapter.calls == []
    assert not (tmp_path / "run").exists()


def test_protocol_not_json_object(tmp_path, services):
    services(FakeSN([]))
    bad = tmp_path / "p.json"
    bad.write_text("[1, 2]")
    assert run_cli(tmp_path, bad) == 2
    bad.write_text("{not json")
    assert run_cli(tmp_path, bad) == 2


def test_load_protocol_maps_fields_and_defaults(tmp_path):
    p = cli.load_protocol(write_protocol(tmp_path, analyst_profile="opus", smoke_sample_size=4))
    assert p.config.n_smoke_sample == 4 and p.config.k_screen == 1 and p.max_rounds == 1
    assert p.adapter_settings["analyst_profile"] == "opus"
    assert p.adapter_settings["eval_timeout"] == cli.ADAPTER_DEFAULTS["eval_timeout"]
    assert p.resolved["smoke_sample_size"] == 4 and p.resolved["mde"] == 0.05
    raw = (tmp_path / "protocol.json").read_bytes()
    assert p.sha256 == hashlib.sha256(raw).hexdigest()


def test_changed_protocol_file_refused_on_resume(tmp_path, services, capsys):
    services(FakeSN([]))
    protocol = write_protocol(tmp_path)
    assert run_cli(tmp_path, protocol) == 0
    ident = json.loads((tmp_path / "run" / "cli-run.json").read_text())
    assert ident["protocol_sha256"] == hashlib.sha256(protocol.read_bytes()).hexdigest()
    assert (tmp_path / "run" / "protocol.json").read_bytes() == protocol.read_bytes()
    capsys.readouterr()
    protocol.write_text(protocol.read_text() + "\n")            # any byte change
    adapter = services(FakeSN([]))
    assert run_cli(tmp_path, protocol) == 2
    assert "protocol file changed" in capsys.readouterr().err
    assert adapter.calls == []
    # Same bytes again: resume is accepted.
    protocol.write_text(json.dumps(BASE_PROTOCOL))
    assert run_cli(tmp_path, protocol) == 0


def test_resume_refuses_another_identity(tmp_path, services, capsys):
    services(FakeSN([]))
    protocol = write_protocol(tmp_path)
    assert run_cli(tmp_path, protocol) == 0
    args = common(tmp_path)
    args[args.index("--actor") + 1] = "someone-else"
    assert cli.main(["run", *args, "--protocol", str(protocol)]) == 2
    assert "--actor" in capsys.readouterr().err


def test_k_mismatch_refused_before_any_work(tmp_path, services, capsys):
    adapter = services(FakeSN([], k_base=2))
    assert run_cli(tmp_path, write_protocol(tmp_path, k_screen=1)) == 2
    assert "k_base=2" in capsys.readouterr().err
    assert adapter.calls == ["partitions"]
    assert not (tmp_path / "run").exists()


def test_safeguards_come_only_from_the_iteration_protocol(tmp_path, services, capsys):
    adapter = services(FakeSN([]))
    bounds = write_protocol(tmp_path, name="bounds.json", safeguard_bounds={"cost": 0.5})
    assert run_cli(tmp_path, bounds) == 2
    assert "safeguard_bounds is not accepted" in capsys.readouterr().err
    assert services.built == [] and adapter.calls == []
    adapter.safeguard_specs = []
    assert run_cli(tmp_path, write_protocol(tmp_path)) == 2
    assert "no pre-registered safeguards" in capsys.readouterr().err
    assert adapter.calls == ["partitions"]
    del adapter.safeguard_specs
    assert run_cli(tmp_path, write_protocol(tmp_path)) == 0
    assert "safeguards (iteration protocol): infrastructure_failure_rate lower_is_better margin 0.1" in \
        capsys.readouterr().out


def test_new_run_refuses_iteration_with_used_fresh_batches(tmp_path, services, capsys):
    adapter = services(FakeSN([], completed=["f1"]))
    assert run_cli(tmp_path, write_protocol(tmp_path)) == 2
    assert "already used" in capsys.readouterr().err
    assert not any(c.startswith("evaluate") for c in adapter.calls)


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------

def test_smoke_sample_is_deterministic():
    ids = [f"case-{i}" for i in range(20)]
    expected = sorted(ids, key=lambda c: hashlib.sha256(c.encode()).hexdigest())[:5]
    assert cli.smoke_sample(ids, 5) == expected
    assert cli.smoke_sample(list(reversed(ids)), 5) == expected
    with pytest.raises(cli.UsageError):
        cli.smoke_sample(ids, 21)


def test_run_passes_original_partitions_and_stores_smoke(tmp_path, services, capsys):
    adapter = services(FakeSN([]))
    assert run_cli(tmp_path, write_protocol(tmp_path)) == 0
    out = capsys.readouterr().out
    assert "halt reason: max_rounds" in out and "ledger:" in out and "certified nodes: 1" in out
    smoke = json.loads((tmp_path / "run" / "smoke-sample.json").read_text())
    assert smoke["case_ids"] == cli.smoke_sample(POOL, 2) and "sha256" in smoke["rule"]
    st = state(tmp_path)
    assert st["partitions"] == dict(search=POOL, initial_pool=POOL, fresh_batches=[FRESH],
                                    smoke=smoke["case_ids"])
    assert st["seed"] == adapter.seed.content_hash
    assert services.built[0]["run_id"] == "r1"
    assert services.built[0]["settings"]["analyst_profile"] == "pi-flash"
    assert (tmp_path / "run" / "ledger.json").exists()


def test_process_failure_exit_3_and_retry_bumps_key_generation(tmp_path, services, capsys):
    script = [{"cards": [dict(name="a", deltas={t: 0.2 for t in POOL})]}]
    adapter = services(FakeSN(script, fail_on={"screen": EvaluationUnavailable("runner down")}))
    protocol = write_protocol(tmp_path)
    assert run_cli(tmp_path, protocol) == 3
    assert "process_failure" in capsys.readouterr().out
    assert state(tmp_path)["key_generation"] == 0

    adapter = services(FakeSN(script))
    assert run_cli(tmp_path, protocol, "--retry-failed") == 0
    st = state(tmp_path)
    assert st["key_generation"] == 1 and adapter.key_generation == 1
    assert [e["generation"] for e in st["key_generation_log"]] == [0, 1]
    capsys.readouterr()
    # Not after a non-failure halt.
    services(FakeSN(script))
    assert run_cli(tmp_path, protocol, "--retry-failed") == 2
    assert "only allowed after a process_failure" in capsys.readouterr().err
    assert state(tmp_path)["key_generation"] == 1


def test_retry_failed_needs_an_existing_run(tmp_path, services, capsys):
    adapter = services(FakeSN([]))
    assert run_cli(tmp_path, write_protocol(tmp_path), "--retry-failed") == 2
    assert services.built == [] and adapter.calls == []


def test_budget_exhausted_exit_3(tmp_path, services):
    from rag_eval.optimizer.adapter import BudgetExhausted
    services(FakeSN([], fail_on={"baseline": BudgetExhausted("no budget")}))
    assert run_cli(tmp_path, write_protocol(tmp_path)) == 3
    assert state(tmp_path)["halt_reason"] == "budget_exhausted"


def test_unexpected_error_exit_1(tmp_path, services, capsys):
    adapter = services(FakeSN([]))
    adapter.version_for = lambda cid: (_ for _ in ()).throw(RuntimeError("boom"))
    assert run_cli(tmp_path, write_protocol(tmp_path)) == 1
    assert "boom" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# analyse-only
# ---------------------------------------------------------------------------

def test_analyse_only_dispatches_analyst_once_and_touches_nothing_else(tmp_path, services, capsys):
    adapter = services(AnalystOnlySN([], base_scores={"t0": 0.0, "t1": 0.2}))
    assert cli.main(["analyse-only", *common(tmp_path)]) == 0
    assert adapter.calls == ["partitions", "version_for", "evaluate:diagnose", "analyse"]
    a = adapter.analyse_args
    assert a["round_id"] == 0 and a["history"] == [] and a["parent"] == adapter.seed
    assert a["roles"]["t0"] == "failure" and a["roles"]["t1"] == "failure"
    assert a["n_cards"] == cli.default_analysis_config().b_cards
    evidence = tmp_path / "run" / "analyse-only-evidence.txt"
    assert evidence.read_text() == adapter.last_analyst_text
    assert not (tmp_path / "run" / "state.json").exists()
    out = capsys.readouterr().out
    assert "selected=" in out and "evidence_chars=33" in out and "cards: 2" in out
    assert "Empty retrievals" in out and "behavior.refused, trace.n_empty_retrievals" in out
    assert "Verbose answers" in out and "predicate: none" in out


def test_analyse_only_n_cards_and_protocol(tmp_path, services, capsys):
    adapter = services(AnalystOnlySN([], base_scores={"t0": 0.0}))
    protocol = write_protocol(tmp_path, analyst_max_failures=1, analyst_n_controls=0, analyst_profile="opus")
    assert cli.main(["analyse-only", *common(tmp_path), "--n-cards", "1", "--protocol", str(protocol)]) == 0
    assert adapter.analyse_args["n_cards"] == 1
    assert adapter.analyse_args["roles"] == {"t0": "failure"}
    assert services.built[0]["settings"]["analyst_profile"] == "opus"
    assert "cards: 1" in capsys.readouterr().out
    assert cli.main(["analyse-only", *common(tmp_path), "--n-cards", "0"]) == 2


def test_analyse_only_writes_evidence_even_if_dispatch_fails(tmp_path, services):
    class Failing(AnalystOnlySN):
        def analyse(self, *a, **kw):
            super().analyse(*a, **kw)
            raise TimeoutError("analyst agent did not finish")
    services(Failing([], base_scores={"t0": 0.0}))
    assert cli.main(["analyse-only", *common(tmp_path)]) == 1
    assert (tmp_path / "run" / "analyse-only-evidence.txt").exists()


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

def test_status_prints_run_state(tmp_path, services, capsys):
    services(FakeSN([]))
    assert run_cli(tmp_path, write_protocol(tmp_path)) == 0
    capsys.readouterr()
    assert cli.main(["status", "--state-dir", str(tmp_path / "run")]) == 0
    out = capsys.readouterr().out
    for line in ("run: r1 iteration: it1", "rounds: 1", "pointer: seed_v0", "certified nodes: 1",
                 "halt reason: max_rounds", "key_generation: 0", "validation pool: 6", "m: ",
                 "fresh batches used: 0/1", "ledger: "):
        assert line in out, line
    assert services.built.__len__() == 1        # status builds no services


def test_status_without_state_exit_2(tmp_path, capsys):
    assert cli.main(["status", "--state-dir", str(tmp_path / "nothing")]) == 2
    assert "not found" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# sealed
# ---------------------------------------------------------------------------

@pytest.fixture
def sealed_calls(monkeypatch):
    calls = []

    def fake(adapter, lineage, versions, *, confirmed, budget, k=None, actor=None):
        calls.append(dict(confirmed=confirmed, budget=budget, k=k, actor=actor,
                          nodes=[n.version_hash for n in lineage.all_nodes()]))
        return dict(kind="v3-sealed-lineage", status="finished", k=k or 3, n=10, correction="holm",
                    points=[], metadata={})
    monkeypatch.setattr("rag_eval.optimizer.sn_sealed.evaluate_lineage_sealed", fake)
    return calls


def sealed_args(tmp_path, *extra):
    budget = tmp_path / "budget.json"
    budget.write_text(json.dumps(dict(timeout_seconds=3600)))
    return ["sealed", *common(tmp_path), "--budget", str(budget), *extra]


def test_sealed_refuses_without_confirm(tmp_path, services, sealed_calls, capsys):
    services(FakeSN([]))
    assert run_cli(tmp_path, write_protocol(tmp_path)) == 0
    built = len(services.built)
    capsys.readouterr()
    assert cli.main(sealed_args(tmp_path)) == 2
    out = capsys.readouterr().out
    assert "1 node(s)" in out and "node_set_hash" in out and "nothing claimed" in out
    assert sealed_calls == [] and len(services.built) == built
    assert not (tmp_path / "run" / "sealed-curve.json").exists()


def test_sealed_refuses_before_halt(tmp_path, services, sealed_calls, capsys):
    services(FakeSN([]))
    assert run_cli(tmp_path, write_protocol(tmp_path)) == 0
    st = state(tmp_path)
    st["halt_reason"] = None
    (tmp_path / "run" / "state.json").write_text(json.dumps(st))
    assert cli.main(sealed_args(tmp_path, "--confirm")) == 2
    assert "has not halted" in capsys.readouterr().err
    assert sealed_calls == []


def test_sealed_refuses_process_failure_halt_unless_allowed(tmp_path, services, sealed_calls, capsys):
    script = [{"cards": [dict(name="a", deltas={t: 0.2 for t in POOL})]}]
    services(FakeSN(script, fail_on={"screen": EvaluationUnavailable("runner down")}))
    assert run_cli(tmp_path, write_protocol(tmp_path)) == 3
    services(FakeSN([]))
    assert cli.main(sealed_args(tmp_path, "--confirm")) == 2
    assert "retry-failed" in capsys.readouterr().err
    assert cli.main(sealed_args(tmp_path, "--confirm", "--allow-process-failure-halt")) == 0
    assert len(sealed_calls) == 1


def test_sealed_with_confirm_writes_curve(tmp_path, services, sealed_calls, capsys):
    adapter = services(FakeSN([]))
    assert run_cli(tmp_path, write_protocol(tmp_path)) == 0
    assert cli.main(sealed_args(tmp_path, "--confirm", "--k", "3")) == 0
    assert sealed_calls == [dict(confirmed=True, budget=dict(timeout_seconds=3600), k=3, actor="operator",
                                 nodes=[adapter.seed.content_hash])]
    # The sealed step polls with the run's pre-registered adapter settings, not the defaults.
    assert services.built[-1]["settings"]["eval_poll_interval"] == 0.0
    curve = json.loads((tmp_path / "run" / "sealed-curve.json").read_text())
    assert curve["status"] == "finished"
    args = sealed_args(tmp_path, "--confirm")
    args[args.index("--iteration") + 1] = "other"
    assert cli.main(args) == 2


def test_sealed_unavailable_exit_3(tmp_path, services, monkeypatch):
    services(FakeSN([]))
    assert run_cli(tmp_path, write_protocol(tmp_path)) == 0

    def unavailable(*a, **kw):
        raise EvaluationUnavailable("not settled")
    monkeypatch.setattr("rag_eval.optimizer.sn_sealed.evaluate_lineage_sealed", unavailable)
    assert cli.main(sealed_args(tmp_path, "--confirm")) == 3


# ---------------------------------------------------------------------------
# build_services against the real server wiring
# ---------------------------------------------------------------------------

def test_build_services_uses_the_server_app_wiring(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    from rag_eval.workbench import env_guard
    from rag_eval.workbench.hypotheses import HypothesisStore
    from rag_eval.workbench.iterations import IterationService
    from rag_eval.workbench.loop_services import RoundOperations
    installed = []
    monkeypatch.setattr(env_guard, "install", lambda path: installed.append(Path(path)))
    config = tmp_path / "config.json"
    config.write_text(json.dumps(dict(workspace=dict(title="test", location="sn"),
                                      task_worktrees=dict(create_candidates=True))))
    state_root = tmp_path / "state"
    with pytest.raises(cli.UsageError, match="unknown iteration"):
        cli.build_services(config, state_root, "missing", actor="operator", run_id="r1",
                           adapter_settings=dict(cli.ADAPTER_DEFAULTS))
    monkeypatch.setattr(IterationService, "iteration_record",
                        lambda self, iid: dict(id=iid, task_id="task1", root_candidate_id="root-c", protocol={}))
    built = cli.build_services(config, state_root, "it1", actor="operator", run_id="r1",
                               adapter_settings=dict(cli.ADAPTER_DEFAULTS, analyst_profile="opus"))
    adapter = built.adapter
    assert isinstance(adapter._round_ops, RoundOperations)
    assert isinstance(adapter._hypotheses, HypothesisStore)
    assert adapter._run_views is adapter._round_ops.api.views
    assert adapter._config["iteration_id"] == "it1" and adapter._config["task_id"] == "task1"
    assert adapter._config["run_id"] == "r1" and adapter._config["actor"] == "operator"
    assert adapter._config["workspace"]["location"] == str((tmp_path / "sn").resolve())
    assert adapter._analyst_profile == "opus" and adapter._agent_timeout == 5400.0
    assert built.root_candidate_id == "root-c" and built.writable is True
    assert installed and installed[-1] == state_root / "env-guard.jsonl"


# ---------------------------------------------------------------------------
# The run owns its directory and its iteration
# ---------------------------------------------------------------------------

def _run_args(tmp_path, protocol, run_id="r1"):
    return ["run", "--config", "c", "--state", "s", "--iteration", "it1", "--actor", "operator", "--run-id", run_id,
            "--state-dir", str(tmp_path / "run"), "--protocol", str(protocol)]


def test_run_binds_the_iteration_and_holds_its_directory(tmp_path, monkeypatch):
    from rag_eval.optimizer import run_view
    from rag_eval.workbench import auto_runs
    from rag_eval.workbench.repository import WorkbenchRepository
    repository = WorkbenchRepository(tmp_path / "state")
    seen = {}

    def fake_build(config_path, state_root, iteration_id, *, actor, run_id, adapter_settings):
        return cli.Services(adapter=FakeSN(script=[]), iteration_id=iteration_id, task_id="task1",
                            root_candidate_id="root", writable=True, repository=repository)
    real = cli.run_optimization

    def spy(*a, **kw):
        seen["alive"] = run_view.run_alive(kw["state_dir"])
        return real(*a, **kw)
    monkeypatch.setattr(cli, "build_services", fake_build)
    monkeypatch.setattr(cli, "run_optimization", spy)
    protocol = write_protocol(tmp_path)
    assert cli.main(_run_args(tmp_path, protocol)) == 0
    assert seen["alive"] is True
    assert run_view.run_alive(tmp_path / "run") is False
    bound = auto_runs.binding(repository, "it1")
    assert bound["run_id"] == "r1" and bound["state_dir"] == str((tmp_path / "run").resolve())
    with run_view.hold_run_lock(tmp_path / "run"):
        assert cli.main(_run_args(tmp_path, protocol)) == 2


def test_a_second_run_cannot_take_a_bound_iteration(tmp_path, monkeypatch):
    from rag_eval.workbench import auto_runs
    from rag_eval.workbench.repository import WorkbenchRepository
    repository = WorkbenchRepository(tmp_path / "state")
    auto_runs.register(repository, "it1", run_id="other", state_dir="/elsewhere", actor="a")
    monkeypatch.setattr(cli, "build_services", lambda *a, **kw: cli.Services(
        adapter=FakeSN(script=[]), iteration_id="it1", task_id="task1", root_candidate_id="root", writable=True,
        repository=repository))
    assert cli.main(_run_args(tmp_path, write_protocol(tmp_path))) == 2
