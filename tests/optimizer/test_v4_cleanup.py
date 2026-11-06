"""Cancellation must precede executor joins, while resumable work stays durable."""
import threading

import pytest

from rag_eval.optimizer.adapter import EvaluationUnavailable
from rag_eval.optimizer.v4 import calibrate
from rag_eval.optimizer.v4.config import V4Config
from .v4_mock import ScriptedRun, V4Mock


def config(**kw):
    kw.setdefault("time_budget_hours", 1)
    kw.setdefault("b", 2)
    return V4Config(**kw)


def execute(mock, cfg=None, path=None):
    run = ScriptedRun(mock, mock.seed, cfg or config(), search=mock.search, validation=mock.validation,
                      state_dir=path)
    return run, run.run()


@pytest.mark.parametrize("failure_type", [EvaluationUnavailable, KeyboardInterrupt])
def test_fast_calibration_failure_wakes_slow_search_before_join(failure_type):
    class Mock(V4Mock):
        def evaluate_search(self, version, task_ids, k):
            self.started.set()
            self.woke_before_join = self.stopped.wait(.5)
            return super().evaluate_search(version, task_ids, k)

        def evaluate_subset(self, version, reference, subset, task_ids, k):
            if subset == "mb":
                assert self.started.wait(.5)
                raise failure_type("fast calibration failure")
            return super().evaluate_subset(version, reference, subset, task_ids, k)

        def stop_polling(self):
            self.stopped.set()

        def cancel_pending(self):
            self.physically_cancelled = True
            self.stopped.set()

    mock = Mock([])
    mock.started, mock.stopped = threading.Event(), threading.Event()
    mock.physically_cancelled = False
    with pytest.raises(failure_type):
        calibrate.measure(mock, mock.seed, config(), mock.search, mock.validation[:20], mock.validation[20:])
    assert mock.woke_before_join
    assert not mock.physically_cancelled


def test_ablation_interrupt_wakes_local_pool_before_join():
    class Mock(V4Mock):
        def evaluate_subset(self, version, reference, subset, task_ids, k):
            if "*" in version.content_hash and subset == "mb":
                if version.content_hash.startswith("seed*r2-B"):
                    assert self.started.wait(.5)
                    raise KeyboardInterrupt("ablation interrupted")
                self.started.set()
                self.woke_before_join = self.stopped.wait(.5)
            return super().evaluate_subset(version, reference, subset, task_ids, k)

        def stop_polling(self):
            self.stopped.set()

    mock = Mock([[dict(name="A", delta=.1)], [dict(name="B", delta=.1)], [dict(name="C", delta=.1)], []])
    mock.started, mock.stopped = threading.Event(), threading.Event()
    with pytest.raises(KeyboardInterrupt):
        execute(mock, config(ablation=True, ablation_parallel=3))
    assert mock.woke_before_join
