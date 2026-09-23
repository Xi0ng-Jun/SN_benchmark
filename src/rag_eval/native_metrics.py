"""Public DeepEval metrics with per-metric durable checkpoints.

The delegate owns the algorithm and prompts; this BaseMetric owns only invocation
identity and persistence. No SDK trace construction or private fields are used.
"""
from contextlib import nullcontext
from time import perf_counter
from uuid import uuid4
import threading

from deepeval.metrics import (BaseMetric, AnswerRelevancyMetric, ContextualRelevancyMetric,
                             FaithfulnessMetric, TaskCompletionMetric, StepEfficiencyMetric,
                             PlanQualityMetric, PlanAdherenceMetric)


METRICS = {
    'contextual_relevancy': ContextualRelevancyMetric,
    'faithfulness': FaithfulnessMetric,
    'answer_relevancy': AnswerRelevancyMetric,
    'task_completion': TaskCompletionMetric,
    'step_efficiency': StepEfficiencyMetric,
    'plan_quality': PlanQualityMetric,
    'plan_adherence': PlanAdherenceMetric,
}
COMPONENT_METRICS = tuple(list(METRICS)[:3])
METRIC_NAMES = dict(zip(METRICS, ('Contextual Relevancy', 'Faithfulness', 'Answer Relevancy',
                                'Task Completion', 'Step Efficiency', 'Plan Quality', 'Plan Adherence')))


def select_metrics(metrics, *, trajectory=False):
    if metrics is None:
        return tuple(METRICS if trajectory else COMPONENT_METRICS)
    if (not metrics or len(set(metrics)) != len(metrics) or set(metrics) - METRICS.keys()
            or (not trajectory and set(metrics) - set(COMPONENT_METRICS))):
        raise ValueError('Select unique known metrics; trajectory metrics require --trajectory')
    return tuple(metrics)


class CheckpointMetric(BaseMetric):
    """Synchronous adapter; supported only in our serial SDK evaluation runner."""
    def __init__(self, delegate, *, link, on_start, on_result):
        self.delegate = delegate
        self.link = {**link, 'score_id': uuid4().hex}
        self.on_start, self.on_result = on_start, on_result
        self.started = None
        self.persisted = False
        self._lock = threading.RLock()
        for field in ('threshold', 'evaluation_model', 'strict_mode', 'include_reason',
                      'requires_trace', 'model', 'using_native_model'):
            setattr(self, field, getattr(delegate, field))
        self.async_mode = False

    @property
    def __name__(self):
        return self.delegate.__name__

    def measure(self, test_case, *args, **kwargs):
        self.started = perf_counter()
        self.on_start({**self.link, 'metric': self.__name__})
        binding = getattr(self.model, 'for_metric', None)
        scope = binding(metric=self.__name__, is_active=lambda: not self.persisted and self.error is None,
                        **self.link) if binding else nullcontext()
        failure = None
        try:
            with scope:
                return self.delegate.measure(test_case, *args, **kwargs)
        except BaseException as exc:
            failure = exc
            # Do not leak exception messages (endpoints/keys) to SDK reports.
            if isinstance(exc, Exception):
                raise RuntimeError('Metric failed: ' + type(exc).__name__) from exc
            raise
        finally:
            with self._lock:
                if not self.persisted:
                    sdk_error = self.error
                    for field in ('score', 'reason', 'success', 'error', 'evaluation_cost',
                                  'input_tokens', 'output_tokens', 'verbose_logs'):
                        setattr(self, field, getattr(self.delegate, field, None))
                    if sdk_error or failure is not None:
                        self.score, self.success = None, False
                        self.error = sdk_error or type(failure).__name__
                    self.checkpoint(failure=failure)

    async def a_measure(self, test_case, *args, **kwargs):
        raise RuntimeError('CheckpointMetric requires synchronous evaluation')

    def checkpoint(self, *, failure=None):
        """Also called after SDK returns, for metrics skipped by cancellation/deadline."""
        from .starter_model import call_failure_details
        with self._lock:
            if self.persisted:
                return
            error = failure is not None or self.error is not None or self.score is None
            reason = self.error if error else self.reason
            if error and not reason:
                reason = 'metric not started' if self.started is None else 'metric interrupted without a result'
            self.on_result(self, {**self.link, 'status': 'error' if error else 'scored',
                           'score': None if error else self.score, 'reason': reason,
                           'execution_status': 'not_started' if self.started is None else
                               ('failed' if error else 'completed'),
                           'elapsed_seconds': None if self.started is None else perf_counter() - self.started,
                           **(call_failure_details(failure) if failure is not None else {})})
            self.persisted = True
