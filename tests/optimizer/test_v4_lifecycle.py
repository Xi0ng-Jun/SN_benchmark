"""Deadline and cancellation behavior at the adapter's real polling boundaries."""
import threading
import time
from types import SimpleNamespace

import pytest

from rag_eval.optimizer.adapter import EvaluationUnavailable
from rag_eval.optimizer.sn_adapter import SNAdapter


def adapter(**kwargs):
    return SNAdapter(dict(run_id='run', actor='alice'), **kwargs)


def test_runtime_deadline_interrupts_long_measurement_poll_without_new_dispatch():
    sn = adapter(eval_poll_interval=30)
    end = time.monotonic() + .06
    assert hasattr(sn, 'configure_runtime'), 'adapter needs the optional runtime contract'
    sn.configure_runtime(remaining=lambda: end - time.monotonic(), max_concurrent_executions=1)
    calls = []
    start = time.monotonic()
    with pytest.raises(EvaluationUnavailable, match='deadline'):
        sn._await({'pending': ('same-request', lambda: calls.append('dispatch') or dict(status='running'))})
    assert time.monotonic() - start < .6
    assert calls == ['dispatch']


def test_cancel_interrupts_candidate_lock_wait_and_prevents_dispatch():
    sn = adapter()
    assert hasattr(sn, 'configure_runtime'), 'adapter needs cooperative cancellation'
    sn.configure_runtime(remaining=lambda: 60, max_concurrent_executions=1)
    lock = sn._plan_locks['parent'] = threading.Lock()
    lock.acquire()
    outcome = []
    def worker():
        try:
            sn._first_call(('parent',), lambda: outcome.append('dispatched'))
        except EvaluationUnavailable:
            outcome.append('cancelled')
    thread = threading.Thread(target=worker)
    thread.start()
    sn.cancel_pending()
    thread.join(.5)
    lock.release()
    assert not thread.is_alive()
    assert outcome == ['cancelled']


@pytest.mark.parametrize('boundary', ['worktree', 'rebase', 'agent'])
def test_expired_runtime_prevents_poll_boundary_side_effects(boundary):
    touched = []
    service = SimpleNamespace(candidate=lambda _: touched.append('candidate'),
                              provision=lambda _: touched.append('provision'))
    ops = SimpleNamespace(service=service, rebase=lambda *a, **k: touched.append('rebase'),
                          agent=lambda *a, **k: touched.append('agent'))
    sn = adapter(round_operations=ops)
    assert hasattr(sn, 'configure_runtime'), 'adapter needs deadline checking'
    sn.configure_runtime(remaining=lambda: 0, max_concurrent_executions=1)
    with pytest.raises(EvaluationUnavailable):
        if boundary == 'worktree':
            sn._await_worktree('candidate')
        elif boundary == 'rebase':
            sn._apply_source('candidate', 'same-key', 'test')
        else:
            sn._poll_agent('analyst', 'candidate', dict(key='same-key'))
    assert touched == []


def test_cancel_archives_owned_agent_that_returns_after_cancellation():
    entered, release = threading.Event(), threading.Event()
    archives = []
    def agent(*args, **kwargs):
        entered.set()
        assert release.wait(2)
        return dict(status='running', session_id='session')
    ops = SimpleNamespace(agent=agent,
        sessions=SimpleNamespace(scope=lambda *a: (dict(agent_id='owned-agent'), '/worktree'),
                                 bridge=SimpleNamespace(call=lambda request: archives.append(request) or
                                                        dict(result=dict(status='ok')))))
    sn = SNAdapter(dict(run_id='run', actor='alice', task_id='task'), round_operations=ops)
    sn.configure_runtime(remaining=lambda: 60, max_concurrent_executions=1)
    outcome = []
    def worker():
        try:
            sn._poll_agent('analyst', 'candidate', dict(key='same-agent-key'))
        except EvaluationUnavailable:
            outcome.append('cancelled')
    thread = threading.Thread(target=worker)
    thread.start()
    assert entered.wait(2)
    sn.cancel_pending()
    release.set()
    thread.join(1)
    assert not thread.is_alive()
    assert outcome == ['cancelled']
    assert archives == [dict(op='archiveAgent', agentId='owned-agent')]
    assert any(r.get('remote_stop_confirmed') is False for r in sn.cancellation_report)


def test_stop_polling_preserves_remote_agent_receipt_for_restart():
    archives = []
    sn = SNAdapter(dict(run_id='run', actor='alice', task_id='task'),
        round_operations=SimpleNamespace(sessions=SimpleNamespace(
            scope=lambda *a: (dict(agent_id='owned-agent'), '/worktree'),
            bridge=SimpleNamespace(call=lambda request: archives.append(request)))))
    sn.configure_runtime(remaining=lambda: 60, max_concurrent_executions=1)
    assert hasattr(sn, 'stop_polling'), 'interruption must preserve remote receipts'
    sn.stop_polling()
    with pytest.raises(EvaluationUnavailable):
        sn._track_agent('same-agent-key', dict(status='running', session_id='session'))
    assert archives == []
