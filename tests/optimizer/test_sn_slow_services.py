"""Slow services may outlast local polling limits, never the run's budget."""
from types import SimpleNamespace

import pytest

from rag_eval.optimizer.adapter import EvaluationCancelled, EvaluationUnavailable
from rag_eval.optimizer.sn_adapter import SNAdapter


@pytest.fixture
def clock(monkeypatch):
    import rag_eval.optimizer.sn_adapter as module

    value = SimpleNamespace(now=0.0)

    def advance(seconds):
        value.now += seconds

    value.advance = advance
    monkeypatch.setattr(module.time, "monotonic", lambda: value.now)
    monkeypatch.setattr(module.time, "sleep", advance)
    return value


def adapter_for(clock, monkeypatch, *, runtime=True, wait_for_running=True, budget=10):
    options = {} if wait_for_running is None else {"wait_for_running": wait_for_running}
    adapter = SNAdapter(
        {"run_id": "slow-test"}, agent_timeout=2, eval_timeout=2,
        execution_timeout_seconds=0, agent_poll_interval=1, eval_poll_interval=1,
        **options,
    )
    if runtime:
        adapter.configure_runtime(remaining=lambda: budget - clock.now,
                                  max_concurrent_executions=1)
        monkeypatch.setattr(adapter._runtime_cancel, "wait", clock.advance)
    return adapter


def run_service(adapter, kind, response):
    if kind == "evaluation":
        return adapter._await({"search": ("same-request", response)})
    adapter._round_ops = SimpleNamespace(agent=lambda *args, **kwargs: response())
    return adapter._poll_agent("analyst", None, {"key": "same-request"})


@pytest.mark.parametrize("kind,terminal", [("evaluation", "complete"), ("agent", "idle")])
def test_positive_running_can_finish_after_local_deadline(clock, monkeypatch, kind, terminal):
    adapter = adapter_for(clock, monkeypatch)
    run_service(adapter, kind, lambda: {"status": terminal if clock.now >= 4 else "running"})
    assert clock.now == 4


@pytest.mark.parametrize("kind,error", [("evaluation", EvaluationUnavailable), ("agent", TimeoutError)])
@pytest.mark.parametrize("options", [dict(wait_for_running=None), dict(runtime=False)])
def test_default_or_unbounded_runtime_keeps_local_deadline(clock, monkeypatch, kind, error, options):
    adapter = adapter_for(clock, monkeypatch, **options)
    with pytest.raises(error):
        run_service(adapter, kind, lambda: {"status": "running"})
    assert clock.now == 2


@pytest.mark.parametrize("kind", ["evaluation", "agent"])
def test_running_stops_at_global_budget(clock, monkeypatch, kind):
    adapter = adapter_for(clock, monkeypatch, budget=4.5)
    with pytest.raises(EvaluationCancelled, match="deadline"):
        run_service(adapter, kind, lambda: {"status": "running"})
    assert clock.now == 4.5


@pytest.mark.parametrize("kind", ["evaluation", "agent"])
def test_running_honors_cancellation_after_local_deadline(clock, monkeypatch, kind):
    adapter = adapter_for(clock, monkeypatch)

    def wait(seconds):
        clock.advance(seconds)
        if clock.now >= 3:
            adapter.stop_polling()

    monkeypatch.setattr(adapter._runtime_cancel, "wait", wait)
    with pytest.raises(EvaluationCancelled, match="cancelled"):
        run_service(adapter, kind, lambda: {"status": "running"})
    assert clock.now == 3


@pytest.mark.parametrize("running_until,expected_end", [(0, 2), (4, 6)])
@pytest.mark.parametrize("unconfirmed", [
    {"status": "unknown"},
    {"status": "running", "reason": "agent_observation_unavailable"},
])
def test_unconfirmed_agent_remains_bounded_even_after_long_running(
    clock, monkeypatch, running_until, expected_end, unconfirmed,
):
    adapter = adapter_for(clock, monkeypatch)
    with pytest.raises(TimeoutError):
        run_service(adapter, "agent", lambda: (
            {"status": "running"} if clock.now < running_until else unconfirmed
        ))
    assert clock.now == expected_end


@pytest.mark.parametrize("unconfirmed", [
    {"status": "unknown"},
    {"status": "running", "reason": "agent_observation_unavailable"},
])
def test_transient_observation_loss_recovers_and_positive_running_resets_window(
    clock, monkeypatch, unconfirmed,
):
    adapter = adapter_for(clock, monkeypatch)

    def response():
        if clock.now >= 7:
            return {"status": "idle"}
        return unconfirmed if clock.now in (4, 6) else {"status": "running"}

    assert run_service(adapter, "agent", response) == {"status": "idle"}
    assert clock.now == 7


def test_alternating_observation_loss_cannot_extend_global_budget(clock, monkeypatch):
    adapter = adapter_for(clock, monkeypatch, budget=9.5)
    with pytest.raises(EvaluationCancelled, match="deadline"):
        run_service(adapter, "agent", lambda: {
            "status": "unknown" if clock.now % 2 == 0 else "running"
        })
    assert clock.now == 9.5


@pytest.mark.parametrize("options", [dict(wait_for_running=None), dict(runtime=False)])
def test_default_or_unconfigured_runtime_does_not_restart_unknown_deadline(clock, monkeypatch, options):
    adapter = adapter_for(clock, monkeypatch, **options)
    with pytest.raises(TimeoutError):
        run_service(adapter, "agent", lambda: {
            "status": "running" if clock.now < 1 else "unknown"
        })
    assert clock.now == 2


@pytest.mark.parametrize("kind,terminal", [("evaluation", "complete"), ("agent", "idle")])
def test_completion_returned_after_global_budget_is_not_accepted(clock, monkeypatch, kind, terminal):
    adapter = adapter_for(clock, monkeypatch, budget=3)

    def delayed_completion():
        clock.advance(4)
        return {"status": terminal}

    with pytest.raises(EvaluationCancelled, match="deadline"):
        run_service(adapter, kind, delayed_completion)


@pytest.mark.parametrize("kind,error", [("evaluation", EvaluationUnavailable), ("agent", RuntimeError)])
def test_failure_after_long_running_is_not_retried(clock, monkeypatch, kind, error):
    adapter = adapter_for(clock, monkeypatch)
    with pytest.raises(error, match="failed"):
        run_service(adapter, kind, lambda: {"status": "failed" if clock.now >= 4 else "running"})
    assert clock.now == 4
