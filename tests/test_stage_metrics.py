"""Offline stage metrics use observed context, not answers or document presence."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from rag_eval.notebook_data import adapt
from test_notebook_data_v3 import DATA_V3, freeze, qasper_raw, news_raw
from test_qasper_evidence import fixture, capture

REAL_RUN = (Path(__file__).resolve().parents[1] / 'apps/sn-workbench/test-results/'
            'source-execution-20261012/e5-s3-real-run-20261013/attempt-1/cli-run')


def qasper_case_output(tmp_path):
    f = fixture(tmp_path)
    bundle = adapt('qasper', f[0], adaptation_revision=DATA_V3)
    rec = f[4]
    rec['qasper_evidence'] = capture(f)
    # Identical retrieval_context/capture must not double the block denominator.
    rec.update(context_supported=True, retrieval_context=['k1: Results\nBirds fly.\nFish swim.', 'k2: Unrelated.'])
    row = dict(case_id='qasper:q', status='success', product_record=rec, prediction=rec['answer'])
    from rag_eval.qasper_evidence import public_catalogues
    cats = public_catalogues(f[0], bundle['documents'])
    return bundle, row, cats


def test_qasper_recall_precision_and_anchor_hit_use_distinct_denominators(tmp_path):
    from rag_eval.stage_metrics import case_metrics
    bundle, row, cats = qasper_case_output(tmp_path)
    case = bundle['cases'][0]
    case['gold']['annotations'].append(deepcopy(case['gold']['annotations'][0]))
    metrics = case_metrics(case, row, documents=bundle['documents'], catalogues=cats)
    assert metrics['gold_evidence_recall']['value'] == 1
    assert metrics['gold_evidence_recall']['denominator'] == 1
    assert metrics['context_precision']['value'] == .5
    assert metrics['context_precision']['numerator'] == 1
    assert metrics['context_precision']['denominator'] == 2
    assert metrics['citation_precision']['value'] == 1


def test_edited_projection_cannot_create_an_anchor_hit(tmp_path):
    from rag_eval.stage_metrics import case_metrics
    bundle, row, cats = qasper_case_output(tmp_path)
    row['product_record']['qasper_evidence']['projection']['citations'][0]['unit_ids'] = ['not-gold']
    metric = case_metrics(bundle['cases'][0], row, documents=bundle['documents'], catalogues=cats)['citation_precision']
    assert metric['value'] is None and metric['status'] == 'unavailable'


def test_sectioned_context_uses_all_saved_captures_and_never_anchor_snippets(tmp_path):
    from rag_eval.stage_metrics import case_metrics
    bundle, row, cats = qasper_case_output(tmp_path)
    record = row['product_record']
    record.update(context_supported=False, context_unavailable_reason='sectioned_synthesis', retrieval_context=[])
    record['captures'] = [dict(succeeded=True, sectioned=True, answer='Birds',
                               context_block='k1: Birds fly.', id_map={'k1': {}}),
                          dict(succeeded=True, sectioned=True, answer='Fish',
                               context_block='k1: Fish swim.', id_map={'k1': {}})]
    result = case_metrics(bundle['cases'][0], row, documents=bundle['documents'], catalogues=cats)
    assert result['context_precision']['denominator'] == 2
    assert result['context_precision']['value'] == .5
    record['captures'] = []
    assert case_metrics(bundle['cases'][0], row)['gold_evidence_recall']['value'] is None


def test_missing_error_empty_gold_and_empty_context_do_not_become_zero_scores(tmp_path):
    from rag_eval.stage_metrics import case_metrics
    bundle, row, cats = qasper_case_output(tmp_path)
    case = bundle['cases'][0]
    assert case_metrics(case, None)['status'] == 'missing'
    assert case_metrics(case, None)['context_precision']['value'] is None
    row['status'] = 'error'
    row['product_record'] = {}
    assert case_metrics(case, row)['gold_evidence_recall']['value'] is None
    row['product_record'] = dict(context_supported=True, retrieval_context=[], response=dict(anchors=[]))
    assert case_metrics(case, row)['gold_evidence_recall']['value'] == 0
    assert case_metrics(case, row)['context_precision']['value'] is None
    case['gold']['annotations'][0]['evidence'] = []
    assert case_metrics(case, row)['gold_evidence_recall']['status'] == 'not_applicable'


@pytest.mark.parametrize('status', ['missing', 'pending'])
def test_unobserved_output_status_cannot_reuse_a_stale_product_record(tmp_path, status):
    from rag_eval.stage_metrics import case_metrics
    bundle, row, cats = qasper_case_output(tmp_path)
    row['status'] = status
    row['product_record']['usage'] = dict(calls=[dict(latency_ms=100)], total_tokens=99)
    result = case_metrics(bundle['cases'][0], row, documents=bundle['documents'], catalogues=cats)
    assert result['status'] == status
    assert all(result[key]['value'] is None for key in
               ('gold_evidence_recall', 'context_precision', 'citation_precision'))
    assert result['model_usage']['total_tokens'] is None
    assert result['model_usage']['provider_logged_call_count'] is None


def test_failed_answer_keeps_actually_observed_retrieval_context(tmp_path):
    from rag_eval.stage_metrics import case_metrics
    bundle, row, cats = qasper_case_output(tmp_path)
    row['status'] = 'error'
    result = case_metrics(bundle['cases'][0], row, documents=bundle['documents'], catalogues=cats)
    assert result['status'] == 'error'
    assert result['gold_evidence_recall']['value'] == 1
    assert result['context_precision']['value'] == .5


def test_hotpot_and_multihop_use_evidence_content_and_document_ownership():
    from rag_eval.stage_metrics import case_metrics
    docs = [dict(id='d', title='Paper', source_units=[dict(id='sentence:0', sent_id=0, text='Birds fly.')])]
    case = dict(case_id='hotpotqa:q', suite='hotpotqa', gold=dict(supporting_facts=[
        dict(document_id='d', source_unit_id='sentence:0', title='Paper', sent_id=0)]))
    row = dict(status='success', product_record=dict(context_supported=True,
        retrieval_context=['k1: Birds fly.', 'k2: Nothing.'], source_ids=['s', 'other'],
        source_to_document={'s': 'd', 'other': 'unrelated'}, response=dict(anchors=[])))
    assert case_metrics(case, row, documents=docs)['context_precision']['value'] == .5
    raw, corpus = news_raw()
    b = adapt('multihop_rag', raw, corpus=corpus, adaptation_revision=DATA_V3)
    row['product_record']['retrieval_context'] = ['k1: Alpha fact.', 'k2: Alpha fact.']
    row['product_record']['source_to_document']['s'] = b['documents'][0]['id']
    assert case_metrics(b['cases'][0], row, documents=b['documents'])['context_precision']['value'] == .5
    assert case_metrics(dict(case_id='alce:0', suite='alce', gold={}), row)['context_precision']['status'] == 'not_applicable'


def test_usage_is_partial_observation_not_added_to_scheduled_events(tmp_path):
    from rag_eval.stage_metrics import case_metrics
    bundle, row, _ = qasper_case_output(tmp_path)
    row['product_record'].update(usage=dict(calls=[dict(latency_ms=100), dict(latency_ms=250)],
                                          call_count=2, coverage='provider_logged_events'), latency_seconds=.6)
    costs = case_metrics(bundle['cases'][0], row)['model_usage']
    assert costs['provider_logged_call_count'] == 2
    assert costs['provider_latency_seconds_sum'] == .35
    assert costs['ask_latency_seconds'] == .6
    assert costs['scheduled_call_count'] is None


def test_today_real_run_matches_manual_16_blocks_one_relevant_and_evidence_hit():
    from rag_eval.stage_metrics import compute_stage_metrics
    assert REAL_RUN.is_dir(), 'Required real fixture is unavailable'
    result = compute_stage_metrics(REAL_RUN)
    case, = result['cases']
    assert case['status'] == 'success'
    assert case['gold_evidence_recall']['value'] == 1
    assert case['context_precision']['denominator'] == 16
    assert case['context_precision']['numerator'] == 1
    assert case['context_precision']['value'] == 1 / 16
    assert case['citation_precision']['value'] == 1
    assert case['model_usage']['provider_logged_call_count'] == 2
    assert case['model_usage']['scheduled_call_count'] == 4
    assert case['model_usage']['scheduled_latency_count'] == 4
    assert result['model_events']['submission_count'] == 20
    assert result['model_events']['unattributed_call_count'] == 16
    assert result['summary']['refusal_rate']['value'] == 0


def saved_run(tmp_path, name, status=None, phase='finished', raw=None):
    b = freeze(tmp_path / name / 'source', 'qasper', raw or qasper_raw())
    run = tmp_path / name / 'run'
    run.mkdir()
    import shutil
    shutil.copytree(tmp_path / name / 'source/bundle', run / 'input')
    (run / 'manifest.json').write_text(json.dumps(dict(mode='chunk', suite='qasper', identity=dict(
        notebook_context=dict(partition_id=b['partitions'][0]['partition_id'])))))
    (run / 'planned.jsonl').write_text(json.dumps(dict(case_id='qasper:q')) + '\n')
    (run / 'state.json').write_text(json.dumps(dict(phase=phase)))
    if status:
        (run / 'outputs.jsonl').write_text(json.dumps(dict(case_id='qasper:q', status=status, product_record={})) + '\n')
    return run


def test_multi_run_summary_preserves_missing_pending_errors_and_null_denominators(tmp_path):
    from rag_eval.stage_metrics import write_stage_metrics
    runs = [saved_run(tmp_path, 'one', 'no_answer'), saved_run(tmp_path, 'two', 'error'),
            saved_run(tmp_path, 'three'), saved_run(tmp_path, 'four', phase='asking')]
    report = write_stage_metrics(runs, summary_output=tmp_path / 'summary.json')
    group, = report['groups']
    assert group['status_counts'] == {'no_answer': 1, 'error': 1, 'missing': 1, 'pending': 1}
    assert group['refusal_rate']['value'] == .5
    assert group['refusal_rate']['denominator'] == 2
    assert group['refusal_rate']['planned'] == 4
    assert group['context_precision']['value'] is None
    assert all((r / 'stage-metrics.json').is_file() for r in runs)
    assert (tmp_path / 'summary.json').is_file()


def test_duplicate_outputs_and_foreign_scope_are_rejected(tmp_path):
    from rag_eval.stage_metrics import compute_stage_metrics
    run = saved_run(tmp_path, 'one', 'success')
    text = (run / 'outputs.jsonl').read_text()
    (run / 'outputs.jsonl').write_text(text + text)
    with pytest.raises(ValueError, match='duplicate|Duplicate'):
        compute_stage_metrics(run)
    (run / 'outputs.jsonl').write_text(text.replace('qasper:q', 'qasper:unknown'))
    with pytest.raises(ValueError, match='scope'):
        compute_stage_metrics(run)


@pytest.mark.parametrize('phase', ['finished_with_errors', 'interrupted'])
def test_terminal_error_and_interruption_keep_missing_outputs_missing(tmp_path, phase):
    from rag_eval.stage_metrics import compute_stage_metrics
    run = saved_run(tmp_path, 'one', phase=phase)
    assert compute_stage_metrics(run)['cases'][0]['status'] == 'missing'


def test_actual_hotpot_snapshot_and_multihop_anchors_support_citation_precision(tmp_path):
    from rag_eval.stage_metrics import case_metrics
    from test_hotpot_evidence import fixture as hotpot_fixture, api
    data, repo, mapping, record = hotpot_fixture(tmp_path)
    record['hotpot_evidence'] = api().capture_evidence(repo, record, data['documents'], mapping)
    record['source_to_document'] = {v['source_id']: did for did, v in mapping.items()}
    row = dict(status='success', product_record=record)
    assert case_metrics(data['cases'][0], row, documents=data['documents'])['citation_precision']['value'] == 1
    raw, corpus = news_raw()
    b = adapt('multihop_rag', raw, corpus=corpus, adaptation_revision=DATA_V3)
    anchor = dict(key='k1', source_id='s', object_id='c', object_type='chunk')
    record = dict(response=dict(anchors=[anchor]), source_to_document={'s': b['documents'][0]['id']},
                  captures=[dict(succeeded=True, answer='Answer [k1]', context_block='k1: Alpha fact.',
                                 id_map={'k1': anchor})])
    assert case_metrics(b['cases'][0], dict(status='success', product_record=record),
                        documents=b['documents'])['citation_precision']['value'] == 1


def test_scheduled_pending_call_does_not_fabricate_latency_and_empty_journal_is_unknown(tmp_path):
    from rag_eval.stage_metrics import compute_stage_metrics
    run = saved_run(tmp_path, 'one', 'success')
    (run / 'model-events.jsonl').write_text('')
    assert compute_stage_metrics(run)['cases'][0]['model_usage']['scheduled_call_count'] is None
    # Current scheduled events have no case ID; use the saved Ask window.
    row = json.loads((REAL_RUN / 'model-events.jsonl').read_text().splitlines()[0])
    (run / 'product-artifacts').mkdir()
    (run / 'product-artifacts/attempts.jsonl').write_text('\n'.join(json.dumps(dict(
        event=event, case_id='qasper:q', recorded_at=stamp)) for event, stamp in
        [('started', '2026-10-13T00:00:00+00:00'), ('finished', '2026-10-14T00:00:00+00:00')]) + '\n')
    (run / 'model-events.jsonl').write_text(json.dumps(row) + '\n')
    result = compute_stage_metrics(run)['cases'][0]['model_usage']
    assert result['scheduled_call_count'] == 1
    assert result['scheduled_pending_count'] == 1
    assert result['scheduled_latency_seconds_sum'] is None


def test_ambiguous_or_orphan_scheduled_events_are_rejected(tmp_path):
    from rag_eval.stage_metrics import compute_stage_metrics
    run = saved_run(tmp_path, 'one', 'success')
    lines = (REAL_RUN / 'model-events.jsonl').read_text().splitlines()
    (run / 'model-events.jsonl').write_text(lines[1] + '\n')
    with pytest.raises(ValueError):
        compute_stage_metrics(run)
    (run / 'model-events.jsonl').write_text(lines[0] + '\n' + lines[0] + '\n')
    with pytest.raises(ValueError):
        compute_stage_metrics(run)


def test_cost_summary_reports_coverage_and_does_not_sum_two_observation_sources():
    from rag_eval.stage_metrics import summarize_cases
    from rag_eval.stage_metrics import case_metrics
    case = dict(suite='alce', case_id='a', gold={})
    row = dict(status='success', product_record=dict(usage=dict(calls=[dict(latency_ms=200)]), latency_seconds=.3))
    a = case_metrics(case, row)
    b = case_metrics(dict(case, case_id='b'), None)
    summary = summarize_cases([a, b])['model_usage']
    assert summary['provider_logged_call_count']['sum_over_observed'] == 1
    assert summary['provider_logged_call_count']['observed_cases'] == 1
    assert summary['provider_logged_call_count']['planned'] == 2
    assert summary['scheduled_call_count']['sum_over_observed'] is None


def test_qasper_rewritten_context_or_partial_paragraph_is_not_full_recall(tmp_path):
    from rag_eval.stage_metrics import case_metrics
    bundle, row, cats = qasper_case_output(tmp_path)
    row['product_record']['retrieval_context'] = ['k1: Birds fl…', 'k2: Birds are capable of flight.']
    metrics = case_metrics(bundle['cases'][0], row, documents=bundle['documents'], catalogues=cats)
    assert metrics['gold_evidence_recall']['value'] == 0
    assert metrics['context_precision']['value'] == 0


def test_hotpot_missing_public_units_are_unknown_not_false_negative():
    from rag_eval.stage_metrics import case_metrics
    case = dict(suite='hotpotqa', case_id='q', gold=dict(supporting_facts=[
        dict(document_id='d', source_unit_id='sentence:0', title='P', sent_id=0)]))
    row = dict(status='success', product_record=dict(context_supported=True, retrieval_context=['k1: Bird'],
                source_ids=['s'], source_to_document={'s': 'd'}))
    assert case_metrics(case, row)['gold_evidence_recall']['value'] is None


def test_invalid_model_timestamp_does_not_become_zero_case_calls(tmp_path):
    from rag_eval.stage_metrics import compute_stage_metrics
    run = saved_run(tmp_path, 'one', 'success')
    row = json.loads((REAL_RUN / 'model-events.jsonl').read_text().splitlines()[0])
    row['recorded_at'] = 'not-a-timestamp'
    (run / 'model-events.jsonl').write_text(json.dumps(row) + '\n')
    (run / 'product-artifacts').mkdir()
    (run / 'product-artifacts/attempts.jsonl').write_text('\n'.join(json.dumps(dict(
        event=e, case_id='qasper:q', recorded_at=t)) for e, t in
        [('started', '2026-10-13T00:00:00+00:00'), ('finished', '2026-10-14T00:00:00+00:00')]) + '\n')
    assert compute_stage_metrics(run)['cases'][0]['model_usage']['scheduled_call_count'] is None


def test_partial_saved_plan_cannot_silently_shrink_manifest_scope(tmp_path):
    from rag_eval.stage_metrics import compute_stage_metrics
    raw = qasper_raw()
    raw['p']['qas'].append(deepcopy(raw['p']['qas'][0]))
    raw['p']['qas'][-1]['question_id'] = 'second'
    run = saved_run(tmp_path, 'two', 'success', raw=raw)
    with pytest.raises(ValueError, match='scope'):
        compute_stage_metrics(run)
