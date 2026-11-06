"""Independent v4 partial-result tests through real RunViews and original artifacts."""
from copy import deepcopy
import json

import pytest

from rag_eval.benchmark_comparison import validate_scored_submission
from rag_eval.optimizer.adapter import EvaluationUnavailable
from rag_eval.optimizer.v4 import results
from test_benchmark_comparison import save
from .v41_artifacts import ArtifactFactory


@pytest.fixture
def artifacts(tmp_path):
    return ArtifactFactory(tmp_path)


def test_partial_official_artifact_preserves_valid_rows_without_formal_admission(artifacts):
    original = artifacts.make('original', statuses=('success', 'error'), values=(.25, None),
                              errors={1: 'APIConnectionError'})
    views = artifacts.views(original)
    # This is a valid whole score artifact, independently of complete generation.
    validate_scored_submission(artifacts.bundle, original.submission, original.scores)
    assert views.admission(original.entry.id)['status'] == 'not_admitted'
    assert views.flips(original.entry.id, original.entry.id)['admission'] == 'not_admitted'
    projected = results.read_execution(views, 'original', artifacts.ids)
    healthy, failed = [projected['rows'][task] for task in artifacts.ids]
    assert healthy['score'] == .25 and healthy['failure_kind'] is None
    assert failed['score'] is None and failed['failure_kind'] == 'infra_exhausted'
    measured = results.result_from_slots([(1, projected)], 'candidate', 'scope')
    assert measured.per_task_scores == {artifacts.ids[0]: .25}
    assert measured.error_classes[artifacts.ids[1]] == 'infra_exhausted'
    # v4 projection never upgrades the saved attempt's formal comparison status.
    assert views.admission(original.entry.id)['status'] == 'not_admitted'


@pytest.mark.parametrize('tamper', ['score_hash', 'source', 'answer_binding', 'output_hash', 'duplicate_output'])
def test_invalid_original_artifact_is_refused_as_a_whole(artifacts, tamper):
    source = deepcopy(artifacts.bundle['manifest'])
    if tamper == 'source':
        source['source']['revision'] = 'forged-source'
    original = artifacts.make('original', statuses=('success', 'error'), values=(.25, None),
                              errors={1: 'APIConnectionError'}, source_manifest=source)
    path = original.entry.run_dir
    if tamper == 'score_hash':
        original.scores['per_case'][0]['metrics']['rougeL'] = 1
        save(path / 'scores.json', original.scores)
    elif tamper == 'source':
        # Manifest and SN method agree with each other; the physical frozen
        # input is different. Only a verified source binding can reject this.
        from rag_eval.flip_report import _read_side
        side, bound = _read_side(path, original.entry.scores, None)
        assert bound is not None and side.get('reason') is None
    elif tamper == 'answer_binding':
        original.outputs[0]['prediction'] = 'A different answer.'
        (path / 'outputs.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in original.outputs))
    elif tamper == 'output_hash':
        # Answer is identical; changed execution evidence must still break its saved hash.
        original.outputs[0]['product_record']['usage']['total_tokens'] = 999
        (path / 'outputs.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in original.outputs))
    else:
        with (path / 'outputs.jsonl').open('a') as stream:
            stream.write(json.dumps(original.outputs[0]) + '\n')
    with pytest.raises(EvaluationUnavailable):
        results.read_execution(artifacts.views(original), 'original', artifacts.ids)


@pytest.mark.parametrize('value', [0., None])
def test_no_answer_uses_actual_official_zero_and_never_fills_missing_score(artifacts, value):
    original = artifacts.make('original', statuses=('success', 'no_answer'), values=(.25, value))
    projected = results.read_execution(artifacts.views(original), 'original', artifacts.ids)
    failed = projected['rows'][artifacts.ids[1]]
    assert failed['score'] == value
    if value is None:
        assert failed['failure_kind'] == 'candidate_unscored'
        with pytest.raises(EvaluationUnavailable):
            results.result_from_slots([(1, projected)], 'candidate', 'scope')
    else:
        assert failed['failure_kind'] is None
        measured = results.result_from_slots([(1, projected)], 'candidate', 'scope')
        assert measured.per_task_scores[artifacts.ids[1]] == 0
        assert measured.error_classes[artifacts.ids[1]] is None


@pytest.mark.parametrize('status,error,expected', [
    ('success', None, 'scoring_unavailable'),
    ('error', 'ValueError', 'unknown_failure'),
    ('error', 'case_timeout', 'unknown_failure'),
    ('error', 'TimeoutError', 'unknown_failure'),
])
def test_noninfra_missing_score_cannot_be_silently_dropped(artifacts, status, error, expected):
    original = artifacts.make('original', statuses=('success', status), values=(.25, None),
                              errors={1: error} if error else {})
    projected = results.read_execution(artifacts.views(original), 'original', artifacts.ids)
    assert projected['rows'][artifacts.ids[1]]['failure_kind'] == expected
    with pytest.raises(EvaluationUnavailable):
        results.result_from_slots([(1, projected)], 'candidate', 'scope')


def test_preparation_infra_uses_the_runners_actual_importing_phase(artifacts):
    # notebook_runner._preparation_diagnostics writes precisely this pair;
    # generic importing exceptions must not acquire the same exemption.
    original = artifacts.make('original', statuses=('success', 'error'), values=(.25, None),
                              errors={1: 'PreparationInfrastructureError'},
                              records={1: dict(failed_phase='importing')})
    projected = results.read_execution(artifacts.views(original), 'original', artifacts.ids)
    assert projected['rows'][artifacts.ids[1]]['failure_kind'] == 'infra_exhausted'
    measured = results.result_from_slots([(1, projected)], 'candidate', 'scope')
    assert measured.error_classes[artifacts.ids[1]] == 'infra_exhausted'


def test_infra_retry_replaces_only_unresolved_task_and_fresh_root_adds_second_score(artifacts):
    first = artifacts.make('first', statuses=('success', 'error'), values=(.25, None),
                           errors={1: 'APIConnectionError'})
    retry = artifacts.make('retry', values=(.99, .6))
    fresh = artifacts.make('fresh', values=(.35, .8))
    views = artifacts.views(first, retry, fresh)
    projections = [results.read_execution(views, name, artifacts.ids) for name in ('first', 'retry', 'fresh')]
    resolved = results.resolve_slot(projections[:2])
    assert [resolved['rows'][task]['score'] for task in artifacts.ids] == [.25, .6]
    assert resolved['rows'][artifacts.ids[0]]['trajectory']['execution_id'] == 'first'
    assert resolved['rows'][artifacts.ids[1]]['trajectory']['execution_id'] == 'retry'
    measured = results.result_from_slots([(1, resolved), (3, projections[2])], 'candidate', 'scope')
    assert measured.attempt_scores == [dict(zip(artifacts.ids, [.25, .6])), dict(zip(artifacts.ids, [.35, .8]))]
    assert measured.per_task_scores == dict(zip(artifacts.ids, [pytest.approx(.3), pytest.approx(.7)]))
    assert [[row['logical_attempt'] for row in measured.trajectories[task]] for task in artifacts.ids] == [[1, 3], [1, 3]]


def test_later_infra_retry_cannot_repair_missing_judge_by_selecting_a_new_answer(artifacts):
    original = artifacts.make('original', values=(.25, None))
    retry = artifacts.make('retry', values=(.9, .9))
    views = artifacts.views(original, retry)
    resolved = results.resolve_slot([results.read_execution(views, name, artifacts.ids)
                                     for name in ('original', 'retry')])
    assert resolved['rows'][artifacts.ids[1]]['score'] is None
    assert resolved['rows'][artifacts.ids[1]]['failure_kind'] == 'scoring_unavailable'
    with pytest.raises(EvaluationUnavailable):
        results.result_from_slots([(1, resolved)], 'candidate', 'scope')


def test_unresolved_infra_on_second_root_never_reuses_first_score_as_two(artifacts):
    first = artifacts.make('first', values=(.25, .75))
    second = artifacts.make('second', statuses=('success', 'error'), values=(.35, None),
                            errors={1: 'APIConnectionError'})
    views = artifacts.views(first, second)
    slots = [(root, results.read_execution(views, name, artifacts.ids))
             for root, name in ((1, 'first'), (3, 'second'))]
    measured = results.result_from_slots(slots, 'candidate', 'scope')
    assert measured.per_task_scores == {artifacts.ids[0]: pytest.approx(.3)}
    assert measured.attempt_scores[0][artifacts.ids[1]] == .75
    assert artifacts.ids[1] not in measured.attempt_scores[1]
    assert measured.error_classes[artifacts.ids[1]] == 'infra_exhausted'


def test_valid_but_incompatible_scorers_cannot_be_combined_as_retry_or_remeasure(artifacts):
    first = artifacts.make('first')
    different = artifacts.make('different', packages={'rouge-score': '0.1.2'})
    views = artifacts.views(first, different)
    projections = [results.read_execution(views, name, artifacts.ids) for name in ('first', 'different')]
    with pytest.raises(EvaluationUnavailable, match='scorer'):
        results.resolve_slot(projections)
    with pytest.raises(EvaluationUnavailable, match='scorer'):
        results.result_from_slots(list(enumerate(projections, 1)), 'candidate', 'scope')


def test_scope_or_partition_ambiguity_refuses_all_rows(artifacts):
    original = artifacts.make('original')
    views = artifacts.views(original)
    with pytest.raises(EvaluationUnavailable):
        results.read_execution(views, 'original', artifacts.ids[:1])
    with pytest.raises(EvaluationUnavailable):
        results.read_execution(views, 'missing-execution', artifacts.ids)
