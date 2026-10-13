"""Saved journals reach complete offline consumers without changing execution."""
import builtins
import json

import pytest

from rag_eval.artifacts import digest, save_jsonl
from rag_eval.experiment_aggregation import aggregate_runs
from rag_eval.run_reader import load_run, read_journal
from rag_eval.run_report import write_report
from rag_eval.scheduled_generation_audit import ScheduledGenerationAuditError
from test_experiment_dashboard import make_run


def scheduled_pair(call_id=1, *, phase='future', status='completed'):
    # Independent saved-row fixture, not a copy of the audit implementation.
    identity = dict(call_id=call_id,
        service=dict(service_id='chat', kind='chat', protocol='openai', model='fixture',
            endpoint_sha256='1' * 64, credential_variable_name=None,
            credential_variable_configured=False, max_concurrency=1,
            top_p=None, generation='2' * 64),
        context=dict(workload_id='ask_answer', support_id='opaque-saved-context', priority='interactive'),
        snapshot_sha256='3' * 64, recorded_at='2026-10-13T00:00:00+00:00')
    attempt = dict(identity, kind='model_service_submission_attempt', phase='submission')
    terminal = dict(identity, kind='model_service_future_terminal', phase=phase, status=status,
                    error_type='ValueError' if status == 'error' else None)
    return attempt, terminal


def test_pending_generation_reaches_reader_report_and_run_level_aggregation(tmp_path, monkeypatch):
    run = make_run(tmp_path)
    before = aggregate_runs([run])
    saved_hashes = {name: digest(run / name) for name in ('manifest.json', 'outputs.jsonl', 'scores.jsonl')}
    attempt, _ = scheduled_pair()
    # One legacy pending adapter call belongs to a case; the new event does not.
    legacy = {'event': 'started', 'call_id': 'legacy', 'case_id': 'qasper:q1'}
    save_jsonl(run / 'model-events.jsonl', [legacy, attempt])
    original_import = builtins.__import__

    def offline_import(name, *args, **kwargs):
        if name == 'app' or name.startswith('app.'):
            pytest.fail('Offline consumers must not import SN')
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', offline_import)
    loaded = load_run(run)
    audit = loaded['scheduled_generation_audit']
    assert loaded['unfinished_model_calls'] == 1
    assert audit['attempt_count'] == 1 and audit['terminal_count'] == 0
    assert audit['unfinished'] == [{'call_id': 1, 'state': 'pending'}]
    assert audit['completeness']['pair_state'] == 'incomplete'
    assert audit['completeness']['history_exhaustive'] is False
    assert any('Scheduled generation attempts without a terminal' in w for w in loaded['warnings'])
    data = aggregate_runs([run])
    assert data['runs'][0]['scheduled_generation_audit'] == audit
    assert data['runs'][0]['config_family'] == before['runs'][0]['config_family']
    case_events = [row for value in data['observations'].values() for row in value['events']]
    assert case_events == [legacy]
    report = write_report([run], tmp_path / 'report')
    assert report['runs'][0]['scheduled_generation_audit'] == audit
    text = (tmp_path / 'report/report.md').read_text()
    assert 'scheduled-generation 日志审计' in text and '未配对 1' in text
    assert '显式模型适配器中未结束的调用记录：1' in text
    assert '不证明整次执行历史完整' in text
    assert all(digest(run / name) == h for name, h in saved_hashes.items())


def test_complete_interleaving_and_sync_refusal_remain_run_facts(tmp_path):
    run = make_run(tmp_path)
    a, terminal_a = scheduled_pair()
    b, refused = scheduled_pair(2, phase='submission', status='error')
    save_jsonl(run / 'model-events.jsonl', [a, b, refused, terminal_a])
    loaded = load_run(run)
    assert loaded['unfinished_model_calls'] == 0
    audit = loaded['scheduled_generation_audit']
    assert audit['paired_count'] == 2
    assert [p['terminal_kind'] for p in audit['pairs']] == ['returned_future', 'sync_refusal']
    assert audit['completeness']['pair_state'] == 'complete'
    assert audit['completeness']['history_exhaustive'] is False
    data = aggregate_runs([run])
    assert all(value['events'] == [] for value in data['observations'].values())
    report = write_report([run], tmp_path / 'report')
    assert report['runs'][0]['scheduled_generation_audit']['paired_count'] == 2


@pytest.mark.parametrize('journal,observation', [
    (None, 'no_observation'), ('', 'observed_empty'),
    ('{"event":"started","call_id":"legacy"}\n', 'legacy_only'),
])
def test_missing_empty_and_legacy_only_are_distinct_offline(tmp_path, journal, observation):
    run = make_run(tmp_path)
    path = run / 'model-events.jsonl'
    if journal is None:
        path.unlink()  # Only this independent generated fixture.
    else:
        path.write_text(journal)
    loaded = load_run(run)
    audit = loaded['scheduled_generation_audit']
    assert audit['observation_state'] == observation
    assert audit['attempt_count'] == audit['terminal_count'] == 0
    assert loaded['unfinished_model_calls'] == (1 if observation == 'legacy_only' else 0)


def test_truncated_tail_is_reader_fact_even_after_complete_parsed_pair(tmp_path):
    run = make_run(tmp_path)
    save_jsonl(run / 'model-events.jsonl', scheduled_pair())
    path = run / 'model-events.jsonl'
    with path.open('a') as handle:
        handle.write('{"kind":')
    loaded = load_run(run)
    audit = loaded['scheduled_generation_audit']
    assert audit['paired_count'] == 1 and audit['unfinished'] == []
    assert audit['completeness']['truncated_tail'] is True
    assert audit['completeness']['pair_state'] == 'incomplete'
    assert any('incomplete final line' in w for w in loaded['warnings'])
    # Existing journal API still returns the valid prefix and rejects bad middle rows.
    assert len(read_journal(path, [])) == 2
    path.write_text('{"kind":\n' + json.dumps(scheduled_pair()[0]) + '\n')
    with pytest.raises(ValueError, match='Corrupt journal'):
        load_run(run)


@pytest.mark.parametrize('problem', ['orphan', 'identity'])
def test_complete_reader_rejects_generation_structure_without_rewriting_answers(tmp_path, problem):
    run = make_run(tmp_path)
    a, terminal = scheduled_pair()
    if problem == 'identity':
        terminal['service'] = {**terminal['service'], 'generation': '4' * 64}
    save_jsonl(run / 'model-events.jsonl', [terminal] if problem == 'orphan' else [a, terminal])
    answers = (run / 'outputs.jsonl').read_bytes()
    with pytest.raises(ScheduledGenerationAuditError, match='without attempt|identity mismatch'):
        load_run(run)
    assert (run / 'outputs.jsonl').read_bytes() == answers


def test_events_disabled_does_not_read_or_audit_journal(tmp_path, monkeypatch):
    run = make_run(tmp_path)
    path = run / 'model-events.jsonl'
    path.write_text('corrupt\n')
    read_text = type(path).read_text

    def guarded_read(self, *args, **kwargs):
        if self == path:
            pytest.fail('include_model_events=False must not open the model journal')
        return read_text(self, *args, **kwargs)

    monkeypatch.setattr(type(path), 'read_text', guarded_read)
    loaded = load_run(run, include_model_events=False)
    assert loaded['unfinished_model_calls'] is None
    assert loaded.get('scheduled_generation_audit') is None
