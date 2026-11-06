"""Two calibration lanes with durable checkpoints owned by the coordinator.

P's cold root precedes its warm roots. D has immutable, disjoint batches;
expansion never replaces a measured scope. The adapter's execution admission
limits physical executions (including P's warm roots), not these two threads.
"""
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import dataclasses
import time

from ..adapter import BudgetExhausted, EvaluationCancelled, EvalResult
from . import decide


def measure(adapter, seed, path, saved, *, initial):
    from .calibration_session import _save
    from .loop import RunStopRequested

    schedule = saved['schedule']
    k = saved['identity']['calibration_k']
    concurrency = schedule['concurrency']
    pending = {}
    started = time.monotonic()

    def execute(kind, ids, target):
        t0 = time.monotonic()
        result = (adapter.evaluate_subset(seed, None, 'probe', ids, target)[0] if kind == 'probe'
                  else adapter.evaluate_search(seed, ids, 1))
        return result, time.monotonic() - t0, getattr(adapter, 'timing_profile', lambda r: [])(result)

    def submit_probe(pool):
        completed = schedule.get('probe_completed_k', 0)
        target = completed + 1 if concurrency == 1 or completed == 0 else k
        pending[pool.submit(execute, 'probe', saved['probe'], target)] = ('probe', target)

    def record(kind, target, outcome):
        result, wall, profile = outcome
        if kind == 'probe':
            schedule['probe_completed_k'] = target
            saved['walls']['probe'] = saved['walls'].get('probe', 0.) + wall
            # Each cumulative result already includes all completed P roots.
            saved['profiles']['probe'] = profile
            if target == k:
                saved['results']['probe'] = dataclasses.asdict(result)
        else:
            schedule['diagnosis_batches'][target].update(
                result=dataclasses.asdict(result), wall_s=wall, profiles=profile)
        _save(path, saved)

    pool = ThreadPoolExecutor(max_workers=min(2, concurrency), thread_name_prefix='v41-cal')
    try:
        if initial and 'probe' not in saved['results']:
            submit_probe(pool)
        for i, batch in enumerate(schedule['diagnosis_batches']):
            if 'result' not in batch:
                pending[pool.submit(execute, 'diagnosis', batch['tasks'], 1)] = ('diagnosis', i)
        while pending:
            adapter.check_runtime()
            done, _ = wait(pending, timeout=.25, return_when=FIRST_COMPLETED)
            failure = None
            for future in done:
                kind, target = pending.pop(future)
                try:
                    outcome = future.result()
                except BaseException as exc:
                    failure = failure or exc
                    continue
                record(kind, target, outcome)
            if failure:
                raise failure
            if (initial and 'probe' not in saved['results']
                    and not any(kind == 'probe' for kind, _ in pending.values())):
                submit_probe(pool)
    except BaseException as exc:
        # Wake polling siblings BEFORE joining the pool. Pause/interrupt retains
        # remote receipts; explicit stop/deadline requests physical cancellation.
        if isinstance(exc, (BudgetExhausted, EvaluationCancelled, RunStopRequested)):
            adapter.cancel_pending()
        else:
            adapter.stop_polling()
        for future in pending:
            future.cancel()
        raise
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
        # A sibling can finish successfully between the first error and join.
        # Keep that completion too; only the coordinator ever writes artifacts.
        for future, (kind, target) in pending.items():
            if not future.cancelled() and future.exception() is None:
                record(kind, target, future.result())
        schedule['active_wall_s'] = schedule.get('active_wall_s', 0.) + time.monotonic() - started
        _save(path, saved)
    return saved


def finish_diagnosis(adapter, seed, path, saved):
    from .calibration_session import _save

    batches = saved['schedule']['diagnosis_batches']
    covered = {task for batch in batches for task in batch['tasks']}
    extra = sorted(set(saved['diagnosis']) - covered)
    if extra:
        batches.append(dict(tasks=extra))
        _save(path, saved)  # intent before dispatch: replay uses exactly this scope
    saved = measure(adapter, seed, path, saved, initial=False)
    results = [EvalResult(**b['result']) for b in batches]
    saved['results']['diagnosis'] = dataclasses.asdict(decide.combine(*results))
    saved['walls']['diagnosis'] = sum(b['wall_s'] for b in batches)
    saved['profiles']['diagnosis'] = [p for b in batches for p in b['profiles']]
    return _save(path, saved)
