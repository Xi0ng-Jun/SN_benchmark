"""Offline workbench boundaries: official facts and durable human records."""
import copy
import importlib
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from test_benchmark_comparison import examples, rehash, save
from rag_eval.benchmark_submission import build_submission
from rag_eval.benchmark_official import prepare_inputs
from rag_eval.identity import fingerprint


def ingest_module():
    return importlib.import_module('rag_eval.workbench.ingest')


def repository_module():
    return importlib.import_module('rag_eval.workbench.repository')


def test_task_sessions_persist_independently_and_retry_without_duplicate_history(tmp_path):
    repo_class = repository_module().WorkbenchRepository
    repo = repo_class(tmp_path / 'state')
    ws = repo.register_workspace('SN', '/sn')
    first = repo.create_task(ws['id'], 'Investigate', '', request_key='first')
    second = repo.create_task(ws['id'], 'Continue', '', request_key='second')
    args = dict(task_id=first['id'], agent_id='paseo-agent-1', actor='alice', request_key='link')
    linked = repo.attach_session(**args)
    assert linked['agent_id'] == 'paseo-agent-1'
    assert linked['linked_by'] == 'alice'
    assert repo.attach_session(**args) == linked
    assert repo.attach_session(**{**args, 'request_key': 'fresh'}) == linked
    with pytest.raises(repository_module().Conflict):
        repo.attach_session(**{**args, 'agent_id': 'paseo-agent-2'})
    other = repo.attach_session(**{**args, 'task_id': second['id']})
    assert other['id'] != linked['id']
    replacement = repo.attach_session(**{**args, 'agent_id': 'paseo-agent-2', 'request_key': 'replacement'})
    restored = repo_class(tmp_path / 'state')
    assert restored.task(first['id'])['sessions'] == [linked, replacement]
    assert restored.task(second['id'])['sessions'] == [other]
    events = [e for e in restored.task(first['id'])['history'] if e['kind'] == 'session.attached']
    assert [e['content'] for e in events] == [linked, replacement]
    assert all('status' not in e['content'] for e in events)  # Runtime lifecycle stays in Paseo.


def test_invalid_session_link_leaves_no_records_and_v1_state_is_preserved(tmp_path):
    repo_class = repository_module().WorkbenchRepository
    repo = repo_class(tmp_path / 'state')
    ws = repo.register_workspace('SN', '/sn')
    task = repo.create_task(ws['id'], 'Existing task', '', request_key='first')
    note = repo.save_note(task['id'], 'Existing observation', actor='alice', request_key='note')
    # Simulate the previous v1 schema without the additive session table.
    with repo.connect(write=True) as conn:
        conn.execute('DROP TABLE IF EXISTS task_sessions')
        conn.execute('PRAGMA user_version=1')
    restored = repo_class(tmp_path / 'state')
    assert restored.task(task['id'])['sessions'] == []
    assert restored.task(task['id'])['history'][-1]['content'] == note
    for changes in ({'task_id': 'unknown'}, {'task_id': 7}, {'agent_id': ' '}, {'agent_id': 7}, {'actor': ''}, {'request_key': ''}):
        with pytest.raises(ValueError):
            restored.attach_session(**{**dict(task_id=task['id'], agent_id='agent', actor='alice', request_key='link'), **changes})
    assert restored.task(task['id'])['sessions'] == []
    assert len(restored.task(task['id'])['history']) == 2


def import_fixture(tmp_path):
    _, entries, _, _ = examples(tmp_path, count=1)
    return ingest_module().import_result(tmp_path / 'bundle', entries[0]['submission'],
                                        entries[0]['scores'], tmp_path / 'state')


def test_freezes_official_batch_estimate_and_rejects_tampering(tmp_path):
    result = import_fixture(tmp_path)
    reader = ingest_module()
    view = reader.read_result(tmp_path / 'state', result['id'])
    assert view['metrics']['rougeL'] == {'value': .4, 'denominator': 2, 'kind': 'per-case'}
    assert view['metrics']['mauve'] == {'value': .2, 'denominator': 2, 'kind': 'batch-only'}
    assert view['pending_metrics'] == ['citation_support']
    assert len(view['cases']) == 2
    # Mutable source changes cannot silently replace a review target.
    (tmp_path / 'scores-0.json').write_text('{}')
    assert reader.read_result(tmp_path / 'state', result['id'])['metrics'] == view['metrics']
    frozen = tmp_path / 'state' / 'imports' / result['id'] / 'snapshot.json'
    frozen.write_text('{}')
    with pytest.raises(ValueError, match='hash'):
        reader.read_result(tmp_path / 'state', result['id'])


def test_duplicate_import_has_one_identity_and_original_files_unchanged(tmp_path):
    _, entries, _, _ = examples(tmp_path, count=1)
    before = entries[0]['scores'].read_bytes()
    a = ingest_module().import_result(tmp_path / 'bundle', **entries[0], state_root=tmp_path / 'state')
    b = ingest_module().import_result(tmp_path / 'bundle', **entries[0], state_root=tmp_path / 'state')
    assert a['id'] == b['id']
    assert entries[0]['scores'].read_bytes() == before
    assert len(list((tmp_path / 'state' / 'imports').iterdir())) == 1


def test_incomplete_generation_is_readable_but_cannot_enter_comparison(tmp_path):
    bundle, entries, submissions, scores = examples(tmp_path, count=1)
    rows = copy.deepcopy(submissions[0]['predictions'])
    rows[0].update(status='no_answer', prediction='')
    rows[1].update(status='missing', prediction='')
    sub = build_submission(bundle, method=submissions[0]['method'], predictions=rows)
    score = scores[0]
    score.update(submission_id=fingerprint(sub), coverage=sub['coverage'],
                 prepared_id=fingerprint(prepare_inputs(bundle, sub)))
    for row, prediction in zip(score['per_case'], sub['predictions']):
        row.update(status=prediction['status'])
    score['per_case'][0]['metrics']['rougeL'] = 0
    rehash(score)
    save(entries[0]['submission'], sub); save(entries[0]['scores'], score)
    imported = ingest_module().import_result(tmp_path / 'bundle', **entries[0], state_root=tmp_path / 'state')
    view = ingest_module().read_result(tmp_path / 'state', imported['id'])
    assert view['coverage']['missing'] == 1
    assert view['cases'][0]['metrics']['rougeL'] == 0
    assert view['cases'][1]['status'] == 'missing'
    comparison = importlib.import_module('rag_eval.benchmark_comparison')
    with pytest.raises(ValueError, match='incomplete'):
        comparison.validate_scored_submission(bundle, sub, score, require_complete_generation=True)


def test_review_revision_conflict_idempotency_and_restart(tmp_path):
    result = import_fixture(tmp_path)
    repo_class = repository_module().WorkbenchRepository
    repo = repo_class(tmp_path / 'state')
    ws = repo.register_workspace('SN development', '/sn/worktree')
    task = repo.create_task(ws['id'], 'Investigate evidence', 'Check observed support', request_key='task-1')
    repo.attach_result(task['id'], result['id'])
    case_id = ingest_module().read_result(tmp_path / 'state', result['id'])['cases'][0]['case_id']
    args = dict(task_id=task['id'], result_id=result['id'], case_id=case_id,
                reviewer='alice', judgment='confirmed', reason='Observed answer mismatch',
                evidence=['answer'], base_revision=None, request_key='review-1')
    first = repo.save_review(**args)
    assert repo.save_review(**args) == first
    with pytest.raises(repository_module().Conflict):
        repo.save_review(**{**args, 'reason': 'Other', 'request_key': 'review-stale'})
    with pytest.raises(repository_module().Conflict):
        repo.save_review(**{**args, 'reason': 'Other'})
    second = repo.save_review(**{**args, 'base_revision': first['id'], 'judgment': 'undecided',
                                'request_key': 'review-2'})
    bob = repo.save_review(**{**args, 'reviewer': 'bob', 'request_key': 'review-1'})
    restored = repo_class(tmp_path / 'state')
    reviews = restored.reviews(task['id'], result['id'], case_id)
    assert {r['id'] for r in reviews} == {first['id'], second['id'], bob['id']}
    assert second['parent_id'] == first['id']
    assert len([e for e in restored.task(task['id'])['history'] if e['kind'] == 'review.saved']) == 3
    assert restored.task(task['id'])['results'][0]['id'] == result['id']


def test_unknown_case_and_evidence_do_not_create_human_records(tmp_path):
    result = import_fixture(tmp_path)
    repo = repository_module().WorkbenchRepository(tmp_path / 'state')
    ws = repo.register_workspace('SN', '/sn')
    task = repo.create_task(ws['id'], 'Review', '', request_key='t')
    repo.attach_result(task['id'], result['id'])
    args = dict(task_id=task['id'], result_id=result['id'], reviewer='alice', judgment='confirmed',
                reason='Evidence', base_revision=None, request_key='r')
    with pytest.raises(ValueError):
        repo.save_review(**args, case_id='unknown', evidence=['answer'])
    cid = ingest_module().read_result(tmp_path / 'state', result['id'])['cases'][0]['case_id']
    with pytest.raises(ValueError):
        repo.save_review(**args, case_id=cid, evidence=['invented-trace'])
    assert len(repo.task(task['id'])['history']) == 2


def test_concurrent_duplicate_import_and_review_only_publish_once(tmp_path):
    _, entries, _, _ = examples(tmp_path, count=1)
    def ingest():
        return ingest_module().import_result(tmp_path / 'bundle', **entries[0], state_root=tmp_path / 'state')
    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = list(pool.map(lambda _: ingest(), range(2)))
    assert a['id'] == b['id']
    repo = repository_module().WorkbenchRepository(tmp_path / 'state')
    ws = repo.register_workspace('SN', '/sn')
    task = repo.create_task(ws['id'], 'Review', '', request_key='t')
    repo.attach_result(task['id'], a['id'])
    cid = ingest_module().read_result(tmp_path / 'state', a['id'])['cases'][0]['case_id']
    args = dict(task_id=task['id'], result_id=a['id'], case_id=cid, reviewer='alice',
                judgment='undecided', reason='Unknown cause', evidence=[], base_revision=None, request_key='r')
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: repo.save_review(**args), range(2)))
    assert results[0]['id'] == results[1]['id']
    assert len(repo.reviews(task['id'], a['id'], cid)) == 1


def test_note_idempotency_keeps_evidence_and_rejects_cross_task_target(tmp_path):
    imported = import_fixture(tmp_path)
    repo = repository_module().WorkbenchRepository(tmp_path / 'state')
    ws = repo.register_workspace('SN', '/sn')
    task = repo.create_task(ws['id'], 'Review', '', request_key='t')
    other = repo.create_task(ws['id'], 'Other', '', request_key='other')
    repo.attach_result(task['id'], imported['id'])
    cid = ingest_module().read_result(tmp_path / 'state', imported['id'])['cases'][0]['case_id']
    context = dict(result_id=imported['id'], case_id=cid)
    with pytest.raises(ValueError, match='attached'):
        repo.save_note(other['id'], 'Observation', actor='alice', request_key='n', context=context)
    a = repo.save_note(task['id'], 'Observation', actor='alice', request_key='n', context=context)
    assert repo.save_note(task['id'], 'Observation', actor='alice', request_key='n', context=context) == a
    assert a['context'] == context


def test_case_metric_states_distinguish_batch_only_and_ineligible(tmp_path):
    _, entries, _, scores = examples(tmp_path, count=1)
    score = scores[0]
    ids = score['case_ids']
    score.update(metric_denominators={'rougeL': 1, 'mauve': 2},
                 metric_case_ids={'rougeL': [ids[0]], 'mauve': ids})
    score['per_case'][1]['metrics'] = {}
    rehash(score); save(entries[0]['scores'], score)
    imported = ingest_module().import_result(tmp_path / 'bundle', **entries[0], state_root=tmp_path / 'state')
    case = ingest_module().read_case(tmp_path / 'state', imported['id'], ids[1])
    assert case['metric_states'] == {'rougeL': 'not-applicable', 'mauve': 'batch-only'}


@pytest.mark.parametrize('field', ['raw_generation', 'answer', 'normalized_prediction', None])
def test_saved_answer_declares_actual_source_and_preserves_external_row(tmp_path, field):
    bundle, entries, submissions, scores = examples(tmp_path, count=1)
    sub = submissions[0]
    record = {'raw_prediction': {'model_answer': 'external saved text', 'id': 17}}
    if field:
        record[field] = 'saved answer text'
    sub['predictions'][0]['record'] = record
    score = scores[0]
    score.update(submission_id=fingerprint(sub), prepared_id=fingerprint(prepare_inputs(bundle, sub)))
    rehash(score)
    save(entries[0]['submission'], sub); save(entries[0]['scores'], score)
    imported = ingest_module().import_result(tmp_path / 'bundle', **entries[0], state_root=tmp_path / 'state')
    case = ingest_module().read_case(tmp_path / 'state', imported['id'], sub['case_ids'][0])
    assert case['answer_source'] == ('record.' + field if field else 'prediction')
    assert case['answer'] == (record[field] if field else sub['predictions'][0]['prediction'])
    assert next(e['value'] for e in case['evidence'] if e['id'] == 'raw_prediction') == record['raw_prediction']
    assert 'raw_prediction' in case['evidence_ids']
