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
