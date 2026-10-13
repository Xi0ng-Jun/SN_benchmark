"""Agent candidates retain source identity and separate human decisions."""
import importlib
import hashlib

import pytest

from rag_eval.workbench.repository import Conflict, WorkbenchRepository


def setup_store(tmp_path):
    repo = WorkbenchRepository(tmp_path / 'state')
    ws = repo.register_workspace('Development', tmp_path)
    task = repo.create_task(ws['id'], 'Investigate', '', request_key='task')
    session = repo.attach_session(task['id'], 'agent-1', actor='alice', request_key='session')
    store = importlib.import_module('rag_eval.workbench.candidates').CandidateStore(repo)
    source = dict(agent_id='agent-1', epoch='epoch-1', seq_start=3, seq_end=5,
                  text='Observed inconsistency; check the retrieval mapping.')
    return repo, task, session, store, source


def test_candidate_provenance_retry_and_restart_are_distinct_from_human_review(tmp_path):
    repo, task, session, store, source = setup_store(tmp_path)
    args = dict(task_id=task['id'], session_id=session['id'], source=source,
                kind='finding', text='Check the retrieval mapping', actor='alice', request_key='candidate')
    first = store.save(**args)
    assert first['origin'] == 'agent'
    assert first['selected_by'] == 'alice'
    assert 'judgment' not in first and 'reviewer' not in first
    assert store.save(**args) == first
    with pytest.raises(Conflict):
        store.save(**{**args, 'text': 'Different'})
    source['text'] = 'Changed after selection'
    restored = importlib.import_module('rag_eval.workbench.candidates').CandidateStore(WorkbenchRepository(repo.root))
    assert restored.list(task['id'])[0]['source']['text'].startswith('Observed inconsistency')
    assert restored.list(task['id'])[0]['decision'] is None
    assert [e['kind'] for e in repo.task(task['id'])['history']] == ['task.created', 'session.attached', 'candidate.saved']


def test_candidate_decisions_append_revisions_without_rewriting_source(tmp_path):
    repo, task, session, store, source = setup_store(tmp_path)
    candidate = store.save(task['id'], session['id'], source, kind='next_step', text='Inspect source IDs', actor='alice', request_key='candidate')
    args = dict(task_id=task['id'], candidate_id=candidate['id'], judgment='confirmed', reason='I checked the saved source',
                actor='alice', base_revision=None, request_key='decision')
    first = store.decide(**args)
    assert store.decide(**args) == first
    with pytest.raises(Conflict):
        store.decide(**{**args, 'request_key': 'stale'})
    second = store.decide(**{**args, 'judgment': 'undecided', 'reason': 'More evidence is needed',
                             'base_revision': first['id'], 'request_key': 'revision'})
    view = store.list(task['id'])[0]
    assert view['source'] == candidate['source']
    assert view['decision'] == second
    assert second['parent_id'] == first['id']
    assert len([e for e in repo.task(task['id'])['history'] if e['kind'] == 'candidate.decided']) == 2


def test_candidate_source_keeps_exact_selected_message_bytes(tmp_path):
    _, task, session, store, source = setup_store(tmp_path)
    source['text'] = '  Observed message\n'
    candidate = store.save(task['id'], session['id'], source, kind='finding', text='Observation', actor='alice', request_key='source')
    assert candidate['source']['text'] == source['text']
    assert candidate['source']['sha256'] == hashlib.sha256(source['text'].encode('utf-8')).hexdigest()


def test_candidates_reject_wrong_task_session_and_invalid_sequence_without_writes(tmp_path):
    repo, task, session, store, source = setup_store(tmp_path)
    other = repo.create_task(task['workspace_id'], 'Other task', '', request_key='other')
    args = dict(task_id=task['id'], session_id=session['id'], source=source, kind='direction', text='Check mapping', actor='alice', request_key='candidate')
    for changes in ({'task_id': other['id']}, {'source': {**source, 'agent_id': 'different'}},
                    {'source': {**source, 'seq_start': 6}}, {'source': {**source, 'seq_end': True}}, {'kind': 'official_score'}):
        with pytest.raises(ValueError):
            store.save(**{**args, **changes})
    assert store.list(task['id']) == []


def test_candidate_keeps_disjoint_sdk_source_ranges_and_turn(tmp_path):
    repo, task, session, store, source = setup_store(tmp_path)
    source.update(source_seq_ranges=[{'start_seq': 3, 'end_seq': 3}, {'start_seq': 5, 'end_seq': 5}], turn_id='turn-1')
    candidate = store.save(task['id'], session['id'], source, kind='finding', text='Observation', actor='alice', request_key='ranges')
    assert candidate['source']['source_seq_ranges'] == source['source_seq_ranges']
    assert candidate['source']['turn_id'] == 'turn-1'
    source['source_seq_ranges'][0]['end_seq'] = 4
    assert store.list(task['id'])[0]['source']['source_seq_ranges'][0]['end_seq'] == 3


def test_candidate_invalid_source_ranges_leave_no_records(tmp_path):
    _, task, session, store, source = setup_store(tmp_path)
    for ranges in ([], [{'start_seq': 2, 'end_seq': 3}], [{'start_seq': 5, 'end_seq': 6}],
                   [{'start_seq': True, 'end_seq': 3}], [{'start_seq': 5, 'end_seq': 3}]):
        with pytest.raises(ValueError):
            store.save(task['id'], session['id'], {**source, 'source_seq_ranges': ranges}, kind='finding',
                       text='Observation', actor='alice', request_key='invalid')
    assert store.list(task['id']) == []
