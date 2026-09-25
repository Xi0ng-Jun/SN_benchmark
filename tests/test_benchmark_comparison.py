"""Comparison must reject incompatible artifacts before producing a result."""
import copy
import importlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from rag_eval import benchmark_official as official
from rag_eval.benchmark_submission import build_submission
from rag_eval.notebook_bundle import prepare
from rag_eval.starter_protocol import fingerprint


def module():
    return importlib.import_module('rag_eval.benchmark_comparison')


def save(path, value):
    path.write_text(json.dumps(value, allow_nan=True))


def rehash(score):
    score.pop('scores_id', None)
    score['scores_id'] = fingerprint(score)


def examples(tmp_path, *, count=2):
    raw = [dict(meeting_transcripts=[dict(speaker='A', content='Cats run.')],
                general_query_list=[dict(query='Summarize.', answer='Cats run.'),
                                    dict(query='What happened?', answer='Cats ran.')],
                specific_query_list=[])]
    save(tmp_path / 'raw.jsonl', raw[0])
    save(tmp_path / 'source.json', dict(dataset='QMSum', split='test', revision='fixture',
         source_url='https://example.org/fixture', license='test'))
    bundle = prepare('qmsum', tmp_path / 'raw.jsonl', tmp_path / 'source.json', tmp_path / 'bundle',
                     adaptation_revision='notebook-data-v3')
    cases = bundle['cases']
    entries, submissions, scores = [], [], []
    # Separate batch values deliberately differ from the mean of per-case values.
    for i in range(count):
        method = dict(name=f'method-{i}', kind='reference', citation_style='none',
                      model_identity={'model': f'fixture-{i}'}, input_policy='full-transcript',
                      configuration={'max_output_tokens': 256 + i})
        rows = [dict(case_id=c['case_id'], status='success', prediction='Cats run.') for c in cases]
        submission = build_submission(bundle, method=method, predictions=rows)
        prepared = official.prepare_inputs(bundle, submission)
        dependencies = {'bridge': {'benchmark_official.py': 'a' * 64}, 'python': '3.13.15', 'packages': {}}
        identity = official.scorer_identity(prepared['profile'], dependencies)
        score = dict(format='benchmark-scored-submission-v1', bundle_id=submission['bundle_id'],
                     submission_id=fingerprint(submission), suite='qmsum', scope=submission['scope'],
                     case_ids=submission['case_ids'], method=method, method_id=submission['method_id'],
                     coverage=submission['coverage'], profile=prepared['profile'], dependencies=dependencies,
                     prepared_id=fingerprint(prepared), scorer_identity=identity, scorer_id=fingerprint(identity),
                     metrics={'rougeL': .4 + .1 * i, 'mauve': .2 + .1 * i},
                     per_case=[dict(case_id=c['case_id'], status='success', group_id=c['group_id'],
                                    task=c['task'], metrics={'rougeL': [.1 + i * .2, .9][j]})
                               for j, c in enumerate(cases)],
                     pending_metrics=['citation_support'], official_paper_reproduction=False)
        rehash(score)
        submission_path, score_path = tmp_path / f'submission-{i}.json', tmp_path / f'scores-{i}.json'
        save(submission_path, submission)
        save(score_path, score)
        entries.append({'submission': submission_path, 'scores': score_path})
        submissions.append(submission)
        scores.append(score)
    return bundle, entries, submissions, scores


def test_batch_metrics_are_preserved_while_paired_differences_keep_groups(tmp_path):
    compare = module()
    bundle, entries, _, _ = examples(tmp_path)
    report = compare.compare_submissions(tmp_path / 'bundle', entries)
    assert report['methods'][0]['metrics'] == {'rougeL': .4, 'mauve': .2}
    assert report['common_metrics'] == ['mauve', 'rougeL']
    assert report['paired_metrics'] == ['rougeL']
    pair = report['comparisons'][0]
    assert pair['batch_differences']['rougeL'] == pytest.approx(.1)
    assert pair['paired_mean_differences']['rougeL'] == pytest.approx(.1)
    assert pair['paired'][0]['differences']['rougeL'] == pytest.approx(.2)
    assert pair['paired'][1]['differences']['rougeL'] == 0
    assert pair['paired'][0]['group_id'] == bundle['cases'][0]['group_id']
    assert report['uncertainty']['computed'] is False
    assert set(report['method_differences']) >= {'model_identity', 'configuration'}


def test_only_common_metrics_are_compared_and_pending_differences_are_explicit(tmp_path):
    compare = module()
    _, entries, _, scores = examples(tmp_path)
    scores[1]['metrics'].pop('mauve')
    scores[1]['pending_metrics'].append('mauve')
    rehash(scores[1]); save(entries[1]['scores'], scores[1])
    report = compare.compare_submissions(tmp_path / 'bundle', entries)
    assert report['common_metrics'] == ['rougeL']
    assert report['methods'][1]['unavailable_metrics'] == ['mauve']
    assert 'mauve' in report['methods'][1]['pending_metrics']
    assert report['methods'][0]['unshared_metrics'] == ['mauve']


@pytest.mark.parametrize('field', ['scores_id', 'submission_id', 'prepared_id', 'method_id', 'scorer_id', 'bundle_id'])
def test_identity_tampering_is_rejected_even_after_outer_hash_is_recomputed(tmp_path, field):
    compare = module()
    _, entries, _, scores = examples(tmp_path)
    scores[1][field] = '0' * 64
    if field != 'scores_id':
        rehash(scores[1])
    save(entries[1]['scores'], scores[1])
    with pytest.raises(ValueError):
        compare.compare_submissions(tmp_path / 'bundle', entries)


@pytest.mark.parametrize('mutation', ['group', 'status', 'task', 'missing_row', 'duplicate_row',
                                    'coverage', 'scope', 'case_ids', 'per_case_metric_gap', 'profile'])
def test_scope_and_case_coverage_cannot_be_relabelled(tmp_path, mutation):
    compare = module()
    _, entries, _, scores = examples(tmp_path)
    s = scores[1]
    if mutation in {'group', 'status', 'task'}:
        s['per_case'][0][{'group': 'group_id'}.get(mutation, mutation)] = 'different'
    elif mutation == 'missing_row':
        s['per_case'].pop()
    elif mutation == 'duplicate_row':
        s['per_case'][1] = s['per_case'][0]
    elif mutation == 'coverage':
        s['coverage']['success'] = 1
    elif mutation == 'scope':
        s['scope'] = 'subset'
    elif mutation == 'case_ids':
        s['case_ids'] = s['case_ids'][:1]
    elif mutation == 'per_case_metric_gap':
        s['per_case'][0]['metrics'] = {}
    else:
        s['profile'] = 'different-profile'
    rehash(s); save(entries[1]['scores'], s)
    with pytest.raises(ValueError):
        compare.compare_submissions(tmp_path / 'bundle', entries)


@pytest.mark.parametrize('value', [float('nan'), float('inf'), True, -1, 1.1])
def test_nonfinite_or_invalid_metric_is_rejected(tmp_path, value):
    compare = module()
    _, entries, _, scores = examples(tmp_path)
    scores[1]['per_case'][0]['metrics']['rougeL'] = value
    if value is True or value in (-1, 1.1):
        rehash(scores[1])
    save(entries[1]['scores'], scores[1])
    with pytest.raises(ValueError):
        compare.compare_submissions(tmp_path / 'bundle', entries)


def test_different_saved_scorer_is_rejected_without_using_current_bridge(tmp_path):
    compare = module()
    _, entries, _, scores = examples(tmp_path)
    scores[1]['dependencies']['bridge']['benchmark_official.py'] = 'b' * 64
    scores[1]['scorer_identity'] = official.scorer_identity(scores[1]['profile'], scores[1]['dependencies'])
    scores[1]['scorer_id'] = fingerprint(scores[1]['scorer_identity'])
    rehash(scores[1]); save(entries[1]['scores'], scores[1])
    with pytest.raises(ValueError, match='scorer'):
        compare.compare_submissions(tmp_path / 'bundle', entries)


@pytest.mark.parametrize('status', ['missing', 'error'])
def test_incomplete_generation_cannot_enter_main_comparison(tmp_path, status):
    compare = module()
    bundle, entries, submissions, scores = examples(tmp_path)
    submissions[1]['predictions'][0].update(status=status, prediction='')
    sub = build_submission(bundle, method=submissions[1]['method'], predictions=submissions[1]['predictions'])
    s = scores[1]
    s.update(submission_id=fingerprint(sub), coverage=sub['coverage'],
             prepared_id=fingerprint(official.prepare_inputs(bundle, sub)))
    s['per_case'][0]['status'] = status
    rehash(s); save(entries[1]['scores'], s); save(entries[1]['submission'], sub)
    with pytest.raises(ValueError, match='incomplete|missing|error'):
        compare.compare_submissions(tmp_path / 'bundle', entries)


def test_duplicate_submission_or_method_attempt_requires_explicit_selection(tmp_path):
    compare = module()
    _, entries, _, _ = examples(tmp_path)
    with pytest.raises(ValueError, match='Duplicate|duplicate'):
        compare.compare_submissions(tmp_path / 'bundle', [entries[0], entries[0]])


def test_bundle_is_rebuilt_from_frozen_source_before_comparison(tmp_path):
    compare = module()
    _, entries, _, _ = examples(tmp_path)
    (tmp_path / 'bundle' / 'raw-data').write_text('{}')
    with pytest.raises(ValueError, match='hash'):
        compare.compare_submissions(tmp_path / 'bundle', entries)


def test_rehashed_score_source_difference_is_not_accepted_as_same_scorer(tmp_path):
    compare = module()
    _, entries, _, scores = examples(tmp_path)
    for i, score in enumerate(scores):
        score['dependencies']['sources'] = {'source.py': {'url': 'https://example.org/fixed.py',
                                                         'sha256': str(i) * 64}}
        score['scorer_identity'] = official.scorer_identity(score['profile'], score['dependencies'])
        score['scorer_id'] = fingerprint(score['scorer_identity'])
        rehash(score); save(entries[i]['scores'], score)
    with pytest.raises(ValueError, match='scorer'):
        compare.compare_submissions(tmp_path / 'bundle', entries)


def test_duplicate_method_with_another_answer_is_still_a_duplicate_attempt(tmp_path):
    compare = module()
    bundle, entries, submissions, scores = examples(tmp_path)
    submission = build_submission(bundle, method=submissions[0]['method'], predictions=[
        {**row, 'prediction': 'Different answer.'} for row in submissions[1]['predictions']])
    scores[1].update(method=submission['method'], method_id=submission['method_id'],
                     submission_id=fingerprint(submission), prepared_id=fingerprint(official.prepare_inputs(bundle, submission)))
    rehash(scores[1]); save(entries[1]['submission'], submission); save(entries[1]['scores'], scores[1])
    with pytest.raises(ValueError, match='Duplicate|duplicate'):
        compare.compare_submissions(tmp_path / 'bundle', entries)


def test_same_explicit_subset_is_valid_but_subset_and_full_cannot_mix(tmp_path):
    compare = module()
    bundle, entries, submissions, scores = examples(tmp_path)
    for i in range(2):
        subset = build_submission(bundle, method=submissions[i]['method'], predictions=submissions[i]['predictions'][:1],
                                  case_ids=submissions[i]['case_ids'][:1])
        scores[i].update(scope=subset['scope'], case_ids=subset['case_ids'], coverage=subset['coverage'],
                         submission_id=fingerprint(subset), prepared_id=fingerprint(official.prepare_inputs(bundle, subset)),
                         per_case=scores[i]['per_case'][:1])
        rehash(scores[i]); save(entries[i]['submission'], subset); save(entries[i]['scores'], scores[i])
        if i == 0:
            with pytest.raises(ValueError, match='scope|case_ids'):
                compare.compare_submissions(tmp_path / 'bundle', entries)
    report = compare.compare_submissions(tmp_path / 'bundle', entries)
    assert report['scope'] == 'subset'
    assert report['case_count'] == 1


def test_answer_metrics_can_be_pending_but_not_scored_simultaneously(tmp_path):
    compare = module()
    _, entries, _, scores = examples(tmp_path)
    scores[1]['pending_metrics'].append('rougeL: missing output')
    rehash(scores[1]); save(entries[1]['scores'], scores[1])
    with pytest.raises(ValueError, match='pending'):
        compare.compare_submissions(tmp_path / 'bundle', entries)


def test_empty_metric_intersection_has_no_main_comparison(tmp_path):
    compare = module()
    _, entries, _, scores = examples(tmp_path)
    scores[1]['metrics'] = {'another_metric': .5}
    for row in scores[1]['per_case']:
        row['metrics'] = {}
    rehash(scores[1]); save(entries[1]['scores'], scores[1])
    with pytest.raises(ValueError, match='common'):
        compare.compare_submissions(tmp_path / 'bundle', entries)


@pytest.mark.parametrize('denominators', [{'rougeL': 2}, {'rougeL': True, 'mauve': 2},
                                        {'rougeL': 0, 'mauve': 2}, {'rougeL': 3, 'mauve': 2}])
def test_metric_denominators_must_cover_metrics_and_fit_scope(tmp_path, denominators):
    compare = module()
    _, entries, _, scores = examples(tmp_path)
    scores[1]['metric_denominators'] = denominators
    rehash(scores[1]); save(entries[1]['scores'], scores[1])
    with pytest.raises(ValueError, match='denominator'):
        compare.compare_submissions(tmp_path / 'bundle', entries)


def test_same_metric_with_different_eligible_denominators_cannot_compare(tmp_path):
    compare = module()
    _, entries, _, scores = examples(tmp_path)
    for i, score in enumerate(scores):
        score['metric_denominators'] = {'rougeL': 2, 'mauve': 1 + i}
        score['metric_case_ids'] = {'rougeL': score['case_ids'], 'mauve': score['case_ids'][:1 + i]}
        rehash(score); save(entries[i]['scores'], score)
    with pytest.raises(ValueError, match='denominator'):
        compare.compare_submissions(tmp_path / 'bundle', entries)


def test_batch_denominators_are_disclosed_and_nonstandard_upstream_map_is_not_clamped(tmp_path):
    compare = module()
    _, entries, _, scores = examples(tmp_path)
    for i, score in enumerate(scores):
        score['metrics']['upstream_map_at_10'] = 1.7 + .1 * i
        score['metric_denominators'] = {'rougeL': 2, 'mauve': 2, 'upstream_map_at_10': 1}
        score['metric_case_ids'] = {'rougeL': score['case_ids'], 'mauve': score['case_ids'],
                                    'upstream_map_at_10': score['case_ids'][:1]}
        rehash(score); save(entries[i]['scores'], score)
    report = compare.compare_submissions(tmp_path / 'bundle', entries)
    assert report['metric_denominators']['upstream_map_at_10'] == 1
    assert report['methods'][0]['metrics']['upstream_map_at_10'] == 1.7
    assert report['comparisons'][0]['batch_differences']['upstream_map_at_10'] == pytest.approx(.1)


@pytest.mark.parametrize('mutation', ['not_object', 'missing_metric', 'not_list', 'unknown_id',
                                    'duplicate_id', 'wrong_order', 'count_mismatch', 'empty', 'nontext'])
def test_metric_case_ids_must_be_complete_ordered_unique_subsets(tmp_path, mutation):
    compare = module()
    _, entries, _, scores = examples(tmp_path)
    s = scores[1]
    s['metric_denominators'] = {'rougeL': 2, 'mauve': 2}
    s['metric_case_ids'] = {'rougeL': list(s['case_ids']), 'mauve': list(s['case_ids'])}
    if mutation == 'not_object':
        s['metric_case_ids'] = []
    elif mutation == 'missing_metric':
        s['metric_case_ids'].pop('mauve')
    elif mutation == 'not_list':
        s['metric_case_ids']['mauve'] = 'case'
    elif mutation == 'unknown_id':
        s['metric_case_ids']['mauve'][0] = 'unknown'
    elif mutation == 'duplicate_id':
        s['metric_case_ids']['mauve'] = s['case_ids'][:1] * 2
    elif mutation == 'wrong_order':
        s['metric_case_ids']['mauve'].reverse()
    elif mutation == 'count_mismatch':
        s['metric_case_ids']['mauve'].pop()
    elif mutation == 'empty':
        s['metric_case_ids']['mauve'] = []
    else:
        s['metric_case_ids']['mauve'][0] = 1
    rehash(s); save(entries[1]['scores'], s)
    with pytest.raises(ValueError, match='metric_case_ids|metric case|eligible-case'):
        compare.compare_submissions(tmp_path / 'bundle', entries)


def test_equal_denominator_different_metric_cases_are_rejected(tmp_path):
    compare = module()
    _, entries, _, scores = examples(tmp_path)
    for i, s in enumerate(scores):
        s['metric_denominators'] = {'rougeL': 2, 'mauve': 1}
        s['metric_case_ids'] = {'rougeL': s['case_ids'], 'mauve': s['case_ids'][i:i+1]}
        rehash(s); save(entries[i]['scores'], s)
    with pytest.raises(ValueError, match='eligible-case|metric case'):
        compare.compare_submissions(tmp_path / 'bundle', entries)


@pytest.mark.parametrize('metric', ['mauve', 'upstream_map_at_10', 'citation_rec', 'citation_prec'])
def test_conditional_metric_cannot_guess_case_identity_from_count(tmp_path, metric):
    compare = module()
    _, entries, _, scores = examples(tmp_path)
    for i, s in enumerate(scores):
        s['metrics'][metric] = .4
        s['metric_denominators'] = {key: 2 for key in s['metrics']}
        if metric == 'mauve':
            s['metric_denominators'][metric] = 1
        rehash(s); save(entries[i]['scores'], s)
    with pytest.raises(ValueError, match='metric_case_ids|metric case|eligible-case'):
        compare.compare_submissions(tmp_path / 'bundle', entries)


def test_matching_conditional_subsets_and_legacy_full_scope_are_disclosed(tmp_path):
    compare = module()
    _, entries, _, scores = examples(tmp_path)
    for i, s in enumerate(scores):
        s['metric_denominators'] = {'rougeL': 2, 'mauve': 1}
        s['metric_case_ids'] = {'rougeL': s['case_ids'], 'mauve': s['case_ids'][1:]}
        rehash(s); save(entries[i]['scores'], s)
    report = compare.write_comparison(tmp_path / 'bundle', entries, tmp_path / 'conditional-report')
    assert report['metric_case_ids']['mauve'] == scores[0]['case_ids'][1:]
    assert report['conditional_metric_scopes']['mauve'] == {'case_count': 1, 'case_ids': scores[0]['case_ids'][1:]}
    assert report['methods'][0]['metric_case_ids_source'] == 'explicit'
    assert 'Conditional metric scope' in (tmp_path / 'conditional-report/report.md').read_text()
    for i, s in enumerate(scores):
        s.pop('metric_case_ids')
        s['metric_denominators']['mauve'] = 2
        rehash(s); save(entries[i]['scores'], s)
    legacy = compare.compare_submissions(tmp_path / 'bundle', entries)
    assert legacy['metric_case_ids']['mauve'] == scores[0]['case_ids']
    assert legacy['methods'][0]['metric_case_ids_source'] == 'inferred_from_full_case_scope'
    assert legacy['conditional_metric_scopes'] == {}


def attach_observations(bundle, entry, submission, score, observations):
    for row, observation in zip(submission['predictions'], observations):
        row.update(observation)
    rebuilt = build_submission(bundle, method=submission['method'], predictions=submission['predictions'])
    score.update(submission_id=fingerprint(rebuilt), prepared_id=fingerprint(official.prepare_inputs(bundle, rebuilt)))
    rehash(score); save(entry['submission'], rebuilt); save(entry['scores'], score)


def test_absent_cost_observations_remain_unavailable_not_zero(tmp_path):
    compare = module()
    _, entries, _, _ = examples(tmp_path)
    report = compare.compare_submissions(tmp_path / 'bundle', entries)
    observed = report['methods'][0]['observed_cost']
    assert observed['case_count'] == 2
    assert observed['latency']['status'] == 'unavailable'
    assert observed['latency']['boundaries'] == []
    assert observed['usage']['status'] == 'unavailable'
    assert observed['usage']['measured_cases'] == 0
    assert observed['monetary_cost']['status'] == 'unavailable'


def test_partial_latency_and_usage_keep_coverage_and_boundary_separate(tmp_path):
    compare = module()
    bundle, entries, submissions, scores = examples(tmp_path)
    usage = {'coverage': 'provider_logged_events', 'cost': None, 'call_count': 2, 'calls_with_usage': 1,
             'prompt_tokens': 10, 'completion_tokens': 2, 'total_tokens': 12,
             'calls': [{'kind': 'chat', 'model': 'fixture', 'status': 'ok', 'latency_ms': 200,
                        'usage': {'prompt_tokens': 10, 'completion_tokens': 2, 'total_tokens': 12}}]}
    attach_observations(bundle, entries[0], submissions[0], scores[0], [
        {'record': {'latency_seconds': 2., 'usage': usage}},
        {'latency_seconds': .7, 'record': {'latency_seconds': None, 'usage': None}}])
    report = compare.write_comparison(tmp_path / 'bundle', entries, tmp_path / 'cost-report')
    observed = report['methods'][0]['observed_cost']
    boundaries = {x['field']: x for x in observed['latency']['boundaries']}
    assert boundaries['record.latency_seconds']['measured_cases'] == 1
    assert boundaries['record.latency_seconds']['total_seconds'] == 2
    assert boundaries['prediction.latency_seconds']['measured_cases'] == 1
    assert boundaries['prediction.latency_seconds']['mean_seconds'] == .7
    assert observed['usage']['measured_cases'] == 1
    assert observed['usage']['observations'][0]['usage'] == usage
    group = observed['usage']['aggregation_groups'][0]
    assert group['totals']['total_tokens'] == 12
    assert group['totals']['call_count'] == 2
    assert group['measured_cases'] == 1
    assert observed['monetary_cost']['status'] == 'unavailable'
    assert 'Observed cost' in (tmp_path / 'cost-report/report.md').read_text()


@pytest.mark.parametrize('observation', [
    {'record': {'latency_seconds': -1}},
    {'latency_seconds': True},
    {'record': {'usage': {'coverage': 'provider_logged_events', 'total_tokens': -3}}},
    {'record': {'usage': {'calls': [{'latency_ms': -1}]}}},
])
def test_invalid_observed_cost_cannot_be_reported_as_valid(tmp_path, observation):
    compare = module()
    bundle, entries, submissions, scores = examples(tmp_path)
    attach_observations(bundle, entries[0], submissions[0], scores[0], [observation, {}])
    with pytest.raises(ValueError, match='latency|usage'):
        compare.compare_submissions(tmp_path / 'bundle', entries)


def test_nonfinite_observed_cost_is_rejected(tmp_path):
    compare = module()
    _, entries, submissions, _ = examples(tmp_path)
    submissions[0]['predictions'][0]['record'] = {'latency_seconds': float('nan')}
    save(entries[0]['submission'], submissions[0])
    with pytest.raises(ValueError):
        compare.compare_submissions(tmp_path / 'bundle', entries)


def test_different_usage_schemas_are_not_added_together(tmp_path):
    compare = module()
    bundle, entries, submissions, scores = examples(tmp_path)
    attach_observations(bundle, entries[0], submissions[0], scores[0], [
        {'record': {'usage': {'coverage': 'provider_logged_events', 'total_tokens': 8, 'call_count': 1}}},
        {'record': {'usage': {'coverage': 'provider_response', 'input_tokens': 9, 'output_tokens': 3}}}])
    report = compare.compare_submissions(tmp_path / 'bundle', entries)
    usage = report['methods'][0]['observed_cost']['usage']
    assert usage['measured_cases'] == 2
    assert len(usage['aggregation_groups']) == 1
    assert usage['aggregation_groups'][0]['totals'] == {'call_count': 1, 'total_tokens': 8}
    assert usage['observations'][1]['usage']['input_tokens'] == 9
    assert usage['unaggregated_cases'] == 1


@pytest.mark.parametrize('mutation', ['sources_not_dict', 'packages_not_text'])
def test_malformed_dependency_identity_fails_cleanly(tmp_path, mutation):
    compare = module()
    _, entries, _, scores = examples(tmp_path)
    dependency = scores[1]['dependencies']
    if mutation == 'sources_not_dict':
        dependency['sources'] = []
    else:
        dependency['packages'] = {'numpy': None}
    scores[1]['scorer_identity'] = official.scorer_identity(scores[1]['profile'], dependency)
    scores[1]['scorer_id'] = fingerprint(scores[1]['scorer_identity'])
    rehash(scores[1]); save(entries[1]['scores'], scores[1])
    with pytest.raises(ValueError, match='source|package'):
        compare.compare_submissions(tmp_path / 'bundle', entries)


def test_three_method_cli_writes_json_markdown_and_requires_fresh_output(tmp_path):
    module()
    _, entries, _, _ = examples(tmp_path, count=3)
    script = Path(__file__).resolve().parents[1] / 'scripts/compare_benchmark_submissions.py'
    command = [sys.executable, str(script), '--bundle', str(tmp_path / 'bundle'),
               '--output', str(tmp_path / 'report')]
    for entry in entries:
        command += ['--entry', str(entry['submission']), str(entry['scores'])]
    run = subprocess.run(command, text=True, capture_output=True)
    assert run.returncode == 0, run.stderr
    report = json.loads((tmp_path / 'report/report.json').read_text())
    assert len(report['comparisons']) == 3
    markdown = (tmp_path / 'report/report.md').read_text()
    assert 'method-0' in markdown and 'max_output_tokens' in markdown
    before = (tmp_path / 'report/report.json').read_bytes()
    run = subprocess.run(command, text=True, capture_output=True)
    assert run.returncode != 0
    assert (tmp_path / 'report/report.json').read_bytes() == before
