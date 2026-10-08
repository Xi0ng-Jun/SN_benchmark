"""Shared ledger/trace guarantees retained when retiring older suites."""
import json

import pytest

from rag_eval.run_report import read_journal
from rag_eval.run_results import EventJournal, planned_result, result_record, summarize
from rag_eval.trace_contract import TraceEnvelope


def plan():
    return planned_result(dict(suite='qasper', task='qa', case_id='qasper:1', sample_id='1',
                               metric_role='primary', score_kind='continuous'),
                          run_id='run', protocol_id='identity', track='R', mode='chunk',
                          scorer='product.notebook.qasper_answer_token_f1_body_v1')


def test_missing_trace_is_explicit_and_serializable():
    trace = TraceEnvelope.missing()
    assert trace.to_dict() == {'trace_id': None, 'completeness': 'none', 'spans': []}


def test_trace_rejects_unknown_completeness():
    with pytest.raises(ValueError, match='completeness'):
        TraceEnvelope(trace_id='trace-1', completeness='unknown', spans=[])


@pytest.mark.parametrize('trace', [None, TraceEnvelope('trace-1', 'complete', [{'name': 'ask'}])])
def test_trace_alone_does_not_enable_agent_metrics(trace):
    planned = plan()
    row = summarize([planned], [result_record(planned, status='scored', score=1,
                                            output_available=True, trace=trace)])[0]
    assert row['trace_completeness']['none' if trace is None else 'complete'] == 1
    assert row['agent_metrics_suppressed'] is True


@pytest.mark.parametrize('change', [{'score': float('nan')}, {'result_id': 'unknown'},
                                    {'protocol_id': 'tampered'}, {'output_available': False}])
def test_saved_scores_cannot_escape_the_plan_or_claim_invalid_score(change):
    planned = plan()
    result = result_record(planned, status='scored', score=.5, output_available=True)
    with pytest.raises(ValueError):
        summarize([planned], [{**result, **change}])


def test_journal_is_exclusive_and_preserves_unicode_record_boundaries(tmp_path):
    path = tmp_path / 'scores.jsonl'
    with EventJournal(path) as sink:
        sink({'answer': 'one\u2028two'})
        assert json.loads(path.read_text())['answer'] == 'one\u2028two'
    with pytest.raises(FileExistsError):
        EventJournal(path)


def test_interrupted_last_journal_row_is_missing_not_corrupt_completed_data(tmp_path):
    path = tmp_path / 'scores.jsonl'
    path.write_text('{"case_id":"qasper:1"}\n{"case_id":')
    warnings = []
    assert read_journal(path, warnings) == [{'case_id': 'qasper:1'}]
    assert warnings and 'incomplete' in warnings[0]
    path.write_text('{"case_id":\n')
    with pytest.raises(ValueError, match='Corrupt'):
        read_journal(path, [])


@pytest.mark.parametrize('field', ['task', 'applicability'])
def test_incomplete_plan_identity_is_rejected(field):
    planned = plan()
    del planned[field]
    with pytest.raises(ValueError, match='Incomplete planned identity'):
        summarize([planned], [])
