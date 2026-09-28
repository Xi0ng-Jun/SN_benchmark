import importlib.util
import json

import pytest

from rag_eval.benchmark_comparison import write_comparison
from test_benchmark_comparison import examples, rehash, save


def module():
    assert importlib.util.find_spec('rag_eval.benchmark_review'), 'Comparison case review is missing'
    from rag_eval import benchmark_review
    return benchmark_review


def test_review_retains_answers_and_samples_per_pair_without_inventing_judgments(tmp_path):
    m = module()
    bundle, entries, _, _ = examples(tmp_path, count=3)
    comparison = tmp_path / 'comparison'
    original = write_comparison(tmp_path / 'bundle', entries, comparison)
    out = tmp_path / 'review'
    review = m.write_review(tmp_path / 'bundle', comparison / 'report.json', out,
                            metrics=['rougeL'], per_stratum=1, seed=7)
    assert review['comparison_id'] == original['report_id']
    assert len(review['assignments']) == 6  # Two score strata for each of three method pairs.
    assert {r['stratum'] for r in review['assignments']} == {'right_higher', 'equal_nonzero'}
    assert len(review['cases']) == 2
    for item in review['cases']:
        case = json.loads((out / item['path']).read_text())
        assert case['question'] in {'Summarize.', 'What happened?'}
        assert case['references'] in [['Cats run.'], ['Cats ran.']]
        assert len(case['methods']) == 3
        assert case['methods'][0]['submitted_answer'] == 'Cats run.'
        assert case['methods'][0]['official_bridge_answer'] == 'Cats run.'
    annotations = [json.loads(line) for line in (out / 'annotations.jsonl').read_text().splitlines()]
    assert len(annotations) == 6
    assert all(a['status'] == 'unreviewed' and a['judgment'] is None for a in annotations)
    assert json.loads((comparison / 'report.json').read_text()) == original
    repeat = m.write_review(tmp_path / 'bundle', comparison / 'report.json', tmp_path / 'review-again',
                           metrics=['rougeL'], per_stratum=1, seed=7)
    assert repeat == review


def test_batch_only_metric_cannot_drive_case_selection(tmp_path):
    m = module()
    _, entries, _, _ = examples(tmp_path)
    write_comparison(tmp_path / 'bundle', entries, tmp_path / 'comparison')
    with pytest.raises(ValueError, match='per-case'):
        m.write_review(tmp_path / 'bundle', tmp_path / 'comparison/report.json', tmp_path / 'review',
                       metrics=['mauve'])
    assert not (tmp_path / 'review').exists()


def test_review_detects_changed_source_answer_before_writing(tmp_path):
    m = module()
    _, entries, submissions, _ = examples(tmp_path)
    write_comparison(tmp_path / 'bundle', entries, tmp_path / 'comparison')
    submissions[0]['predictions'][0]['prediction'] = 'Changed since scoring'
    entries[0]['submission'].write_text(json.dumps(submissions[0]))
    with pytest.raises(ValueError, match='changed|hash'):
        m.write_review(tmp_path / 'bundle', tmp_path / 'comparison/report.json', tmp_path / 'review',
                       metrics=['rougeL'])
    assert not (tmp_path / 'review').exists()


def test_review_keeps_model_abstention_before_submission_normalization():
    m = module()
    assert hasattr(m, '_answer_observation'), 'Raw answer observation unavailable'
    assert m._answer_observation({'prediction': '', 'record': {'answer': 'Cannot answer from these sources.'}}) == {
        'field': 'record.answer', 'text': 'Cannot answer from these sources.'}
    assert m._answer_observation({'prediction': 'unanswerable', 'record': {'raw_generation': 'unknown'}}) == {
        'field': 'record.raw_generation', 'text': 'unknown'}


def test_task_statistics_use_all_cases_not_selected_examples_or_batch_estimates(tmp_path):
    m = module()
    _, entries, _, scores = examples(tmp_path)
    scores[1]['per_case'][1]['metrics']['rougeL'] = .95
    rehash(scores[1])
    save(entries[1]['scores'], scores[1])
    original = write_comparison(tmp_path / 'bundle', entries, tmp_path / 'comparison')
    review = m.write_review(tmp_path / 'bundle', tmp_path / 'comparison/report.json', tmp_path / 'review',
                            metrics=['rougeL'], per_stratum=1)
    assert len(review['assignments']) == 1
    assert 'task_comparisons' in review, 'Full-scope task statistics are missing'
    row, = review['task_comparisons']
    assert row['case_count'] == row['eligible_cases'] == 2
    assert row['left_mean'] == pytest.approx(.5)
    assert row['right_mean'] == pytest.approx(.625)
    assert row['mean_difference'] == pytest.approx(.125)
    assert row['direction_counts'] == dict(left_higher=0, right_higher=2, equal_zero=0, equal_nonzero=0)
    assert original['methods'][0]['metrics']['rougeL'] == .4
    assert original['methods'][1]['metrics']['rougeL'] == .5
    assert json.loads((tmp_path / 'comparison/report.json').read_text()) == original
    markdown = (tmp_path / 'review/review.md').read_text()
    assert '| 2/2 | 0.500000 | 0.625000 | +0.125000 |' in markdown


def test_task_statistics_preserve_conditional_denominators_and_all_score_directions():
    m = module()
    assert hasattr(m, '_task_comparisons'), 'Full-scope task statistics are missing'
    rows = [dict(case_id=str(i), task='answerable', left={'hit': left}, right={'hit': right},
                 differences={'hit': right-left})
            for i, (left, right) in enumerate([(1, 0), (0, 1), (0, 0), (.5, .5)])]
    rows += [dict(case_id='4', task='answerable', left={}, right={}, differences={}),
             dict(case_id='5', task='null', left={}, right={}, differences={})]
    report = dict(comparisons=[dict(left_method_id='a', right_method_id='b', paired=rows)])
    answerable, null = m._task_comparisons(report, ['hit'])
    assert answerable['task'] == 'answerable'
    assert answerable['case_count'] == 5
    assert answerable['eligible_cases'] == 4
    assert answerable['left_mean'] == answerable['right_mean'] == .375
    assert answerable['mean_difference'] == 0
    assert answerable['direction_counts'] == dict(left_higher=1, right_higher=1, equal_zero=1, equal_nonzero=1)
    assert null['task'] == 'null' and null['case_count'] == 1 and null['eligible_cases'] == 0
    assert null['left_mean'] is null['right_mean'] is null['mean_difference'] is None
