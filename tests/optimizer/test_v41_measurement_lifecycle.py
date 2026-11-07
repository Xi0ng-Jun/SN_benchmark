"""Real durable workbench dispatch with a declared synthetic result projector."""
from copy import deepcopy

import pytest

from rag_eval.optimizer.adapter import Version
from rag_eval.optimizer.v4 import results
from rag_eval.optimizer.v4.cli import services_from_operations
from .test_v4_rehearsal import Crash, rig4, env, eight_papers  # noqa: F401


def test_infra_retry_does_not_replace_healthy_tasks_or_count_as_remeasurement(rig4, monkeypatch):
    seed = rig4.tree['root_candidate_id']
    ids = sorted(rig4.protocol['splits']['validation'])
    missing = ids[0]
    reader = results.read_execution

    def partial(views, execution_id, expected):
        projection = deepcopy(reader(views, execution_id, expected))
        attempts = rig4.api.service.candidate(seed)['attempts']
        attempt = next(a['attempt'] for a in attempts if a.get('execution_id') == execution_id)
        # Deliberately different retry scores expose accidental averaging or
        # replacing healthy first observations with retry outputs.
        for task, row in projection['rows'].items():
            if task == missing:
                row.update(score=None, failure_kind='infra_exhausted',
                           output=dict(status='error', product_record=dict(error_type='APIConnectionError',
                                                                          failed_phase='asking')))
            else:
                row['score'] = .99 if attempt % 2 == 0 else (.5 if attempt == 1 else .6)
        return projection
    monkeypatch.setattr(results, 'read_execution', partial)

    def adapter():
        return services_from_operations(rig4.ops, rig4.config, rig4.tree['id'], actor='alice',
                                        run_id='logical-slots', adapter_settings=dict(
                                        eval_poll_interval=.01, agent_poll_interval=.01, infra_retry=1)).adapter
    version = Version(seed, metadata=dict(candidate_id=seed))
    first_adapter = adapter()
    with rig4:
        first, _ = first_adapter.evaluate_subset(version, None, 'rest', ids, 1)
    assert first.error_classes[missing] == 'infra_exhausted'
    assert missing not in first.per_task_scores
    assert set(first.per_task_scores.values()) == {.5}
    before = rig4.api.service.candidate(seed)['attempts']
    assert len(before) == 2
    # A fresh adapter reconstructs roots from durable retry mappings. The retry
    # execution cannot satisfy k=2; a new root and its bounded retry must launch.
    second_adapter = adapter()
    rig4.crash_when = lambda rig: len(rig.api.service.candidate(seed)['attempts']) >= 3
    rig4.hold = lambda cid: len(rig4.api.service.candidate(seed)['attempts']) >= 3
    with rig4:
        with pytest.raises(Crash):
            second_adapter.remeasure_subset(version, 'rest', ids)
    assert len(rig4.api.service.candidate(seed)['attempts']) == 3
    rig4.crashed.clear()
    rig4.crash_when = None
    rig4.hold = lambda cid: False
    with rig4:
        second = adapter().remeasure_subset(version, 'rest', ids)
    assert len(rig4.api.service.candidate(seed)['attempts']) == 4
    healthy = next(task for task in ids if task != missing)
    assert [attempt[healthy] for attempt in second.attempt_scores] == [.5, .6]
    assert second.per_task_scores[healthy] == .55
    assert len({t['logical_attempt'] for t in second.trajectories[healthy]}) == 2
    # Replay the same frozen request, including after new coordinator construction.
    with rig4:
        replay = adapter().remeasure_subset(version, 'rest', ids)
    assert replay.attempt_scores == second.attempt_scores
    assert len(rig4.api.service.candidate(seed)['attempts']) == 4


@pytest.mark.parametrize("retry_succeeds", [True, False])
def test_terminal_failed_attempt_retries_without_replacing_healthy_scores(rig4, monkeypatch, retry_succeeds):
    seed = rig4.tree['root_candidate_id']
    ids = sorted(rig4.protocol['splits']['validation'])
    finish = rig4.env['executions']._finish
    rewritten = set()
    def finish_with_deadline(eid, **kwargs):
        if kwargs['state'] == 'finished' and not rewritten:
            rewritten.add(eid)
            kwargs.update(state='failed', exit_code=1, error_type='child_exit',
                          run_state=dict(phase='finished_with_errors'))
        return finish(eid, **kwargs)
    monkeypatch.setattr(rig4.env['executions'], '_finish', finish_with_deadline)
    reader = results.read_execution
    def partial(views, execution_id, expected):
        projection = deepcopy(reader(views, execution_id, expected))
        for task, row in projection['rows'].items():
            row['score'] = .25 if execution_id in rewritten else .99
            if (execution_id in rewritten or not retry_succeeds) and task == ids[0]:
                row.update(score=None, failure_kind='execution_incomplete')
        return projection
    monkeypatch.setattr(results, 'read_execution', partial)
    def adapter():
        return services_from_operations(rig4.ops, rig4.config, rig4.tree['id'], actor='alice',
            run_id='terminal-recovery', adapter_settings=dict(eval_poll_interval=.05, infra_retry=1)).adapter
    from rag_eval.optimizer.v4.measurements import V41Measurements
    from rag_eval.optimizer.adapter import EvaluationUnavailable
    infra = V41Measurements._infra
    def old_failure(self, attempt):
        if attempt.get('execution_state') == 'failed':
            raise EvaluationUnavailable('old release failed before retry')
        return infra(self, attempt)
    monkeypatch.setattr(V41Measurements, '_infra', old_failure)
    dispatch = rig4.api.service.run
    def finish_before_dispatch_returns(*args, **kwargs):
        result = dispatch(*args, **kwargs)
        rig4._settle()
        return result
    monkeypatch.setattr(rig4.api.service, 'run', finish_before_dispatch_returns)
    version = Version(seed, metadata=dict(candidate_id=seed))
    with pytest.raises(EvaluationUnavailable, match='old release'):
        adapter().evaluate_subset(version, None, 'rest', ids, 1)
    monkeypatch.setattr(V41Measurements, '_infra', infra)
    resumed = adapter()
    resumed._recovery_execution_timeout_seconds = 14400
    if not retry_succeeds:
        for _ in range(2):
            with pytest.raises(EvaluationUnavailable, match='execution_incomplete'):
                resumed.evaluate_subset(version, None, 'rest', ids, 1)
        assert len(rig4.api.service.candidate(seed)['attempts']) == 2
        return
    measured, _ = resumed.evaluate_subset(version, None, 'rest', ids, 1)
    replay, _ = resumed.evaluate_subset(version, None, 'rest', ids, 1)
    second, _ = resumed.evaluate_subset(version, None, 'rest', ids, 2)
    attempts = rig4.api.service.candidate(seed)['attempts']
    assert len(attempts) == 3
    assert [rig4.env['executions'].get(rig4.task_id, a['execution_id'])['budget']['timeout_seconds']
            for a in attempts] == [7200, 14400, 14400]
    assert measured.per_task_scores == {task: .99 if task == ids[0] else .25 for task in ids}
    assert replay.attempt_scores == measured.attempt_scores
    assert len(measured.attempt_scores) == 1 and len(second.attempt_scores) == 2
