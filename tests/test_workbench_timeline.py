"""Scoped pages expose exact source identities, never array-offset identities."""
import copy
import json
from pathlib import Path

from rag_eval.workbench.repository import WorkbenchRepository
from test_workbench_sessions import Reader


def setup_timeline(tmp_path, envelope):
    from rag_eval.workbench.timeline import TaskTimelineReader
    repo = WorkbenchRepository(tmp_path / 'state')
    ws = repo.register_workspace('SN', tmp_path / 'code')
    task = repo.create_task(ws['id'], 'Read history', '', request_key='task')
    session = repo.attach_session(task['id'], 'agent-example', actor='alice', request_key='session')
    bridge = Reader(envelope)
    return repo, task, session, bridge, TaskTimelineReader(repo, bridge)


def page():
    return json.loads(Path('apps/sn-agent-bridge/examples/read-timeline.response.json').read_text())


def test_timeline_scope_and_sdk_page_metadata_remain_exact(tmp_path):
    source = page()
    repo, task, session, bridge, service = setup_timeline(tmp_path, source)
    response = service.read_page(task['id'], session['id'], direction='before', cursor={'epoch': 'epoch-example', 'seq': 14})
    assert bridge.calls == [{'op': 'readTimeline', 'agentId': 'agent-example', 'expectedCwd': str(tmp_path / 'code'),
                             'projection': 'projected', 'limit': 100, 'direction': 'before',
                             'cursor': {'epoch': 'epoch-example', 'seq': 14}}]
    for field in ('epoch', 'startCursor', 'endCursor', 'hasOlder', 'hasNewer', 'reset', 'gap', 'staleCursor', 'window'):
        assert response['result'][field] == source['result'][field]
    entry = response['result']['entries'][1]
    assert entry['kind'] == 'assistant'
    assert entry['text'] == 'Example read.'
    assert entry['candidateBlockedReason'] is None
    assert entry['source_locator']['source_seq_ranges'] == [{'start_seq': 11, 'end_seq': 11}, {'start_seq': 13, 'end_seq': 13}]
    assert entry['source_locator']['turn_id'] == 'turn-example'
    assert [e['kind'] for e in repo.task(task['id'])['history']] == ['task.created', 'session.attached']


def test_source_id_changes_with_exact_text_or_epoch_but_not_page_offset(tmp_path):
    source = page()
    _, task, session, bridge, service = setup_timeline(tmp_path, source)
    original = service.read_page(task['id'], session['id'])['result']['entries'][1]['id']
    bridge.response['result']['entries'].pop(0)
    assert service.read_page(task['id'], session['id'])['result']['entries'][0]['id'] == original
    bridge.response['result']['entries'][0]['item']['text']['value'] = ' Changed\n'
    changed = service.read_page(task['id'], session['id'])['result']['entries'][0]['id']
    assert changed != original
    bridge.response['result']['epoch'] = 'replacement'
    assert service.read_page(task['id'], session['id'])['result']['entries'][0]['id'] != changed


def test_incomplete_or_failed_page_never_authorizes_candidate(tmp_path):
    source = page()
    _, task, session, bridge, service = setup_timeline(tmp_path, source)
    for key in ('gap', 'staleCursor'):
        bridge.response = page()
        bridge.response['result'][key] = True
        assert service.read_page(task['id'], session['id'])['result']['entries'][1]['candidateBlockedReason']
    bridge.response = page()
    bridge.response['result']['entries'][1]['item']['text']['truncated'] = True
    assert service.read_page(task['id'], session['id'])['result']['entries'][1]['candidateBlockedReason']
    bridge.response = page()
    bridge.response['release'] = {'status': 'unavailable', 'code': 'CLOSE_FAILED'}
    assert service.read_page(task['id'], session['id'])['result']['entries'][1]['candidateBlockedReason']
    bridge.response = {'result': {'status': 'unavailable', 'code': 'SCOPE_MISMATCH'}, 'release': {'status': 'ok'}}
    response = service.read_page(task['id'], session['id'])
    assert response['result'] == bridge.response['result']
    assert 'entries' not in response['result']


def test_malformed_page_metadata_is_not_a_candidate_source(tmp_path):
    from rag_eval.workbench.repository import Conflict
    from rag_eval.workbench.sourced_candidates import SourcedCandidates
    import pytest

    _, task, session, bridge, service = setup_timeline(tmp_path, page())
    locator = service.read_page(task['id'], session['id'])['result']['entries'][1]['source_locator']
    producer = SourcedCandidates(service.scope_reader.repository, service)
    invalid = [*({key: None} for key in ('gap', 'staleCursor', 'reset', 'hasOlder', 'hasNewer')),
               {'gap': 0}, {'staleCursor': 'false'}, {'direction': 'sideways'}, {'projection': 'unknown'},
               {'epoch': ''}, {'mergeWindow': []}, {'startCursor': []},
               {'endCursor': {'epoch': 'epoch-example', 'seq': True}},
               {'startCursor': {'epoch': 'epoch-example', 'seq': 9007199254740992}},
               {'window': {'minSeq': 1, 'maxSeq': 20, 'nextSeq': False}},
               {'window': {'minSeq': 1, 'maxSeq': 20}}]
    for changes in invalid:
        bridge.response = page()
        bridge.response['result'].update(changes)
        assert service.read_page(task['id'], session['id'])['result'] == {'status': 'unavailable', 'code': 'BRIDGE_INVALID_RESPONSE'}
        with pytest.raises(Conflict):
            producer.save(task['id'], session['id'], locator, kind='finding', text='Check', actor='alice', request_key='invalid')
    assert len(service.scope_reader.repository.task(task['id'])['history']) == 2


def test_unknown_activity_identity_includes_original_source_type(tmp_path):
    source = page()
    item = source['result']['entries'][1]['item']
    item.update(type='activity', text=None, title={'value': 'Activity', 'truncated': False},
                sourceType={'value': 'future_a', 'truncated': False})
    _, task, session, bridge, service = setup_timeline(tmp_path, source)
    first = service.read_page(task['id'], session['id'])['result']['entries'][1]
    bridge.response = copy.deepcopy(source)
    bridge.response['result']['entries'][1]['item']['sourceType']['value'] = 'future_b'
    second = service.read_page(task['id'], session['id'])['result']['entries'][1]
    assert first['activityType'] == 'future_a' and second['activityType'] == 'future_b'
    assert first['id'] != second['id']


def test_invalid_turn_or_unsafe_sequence_does_not_advertise_candidate(tmp_path):
    _, task, session, bridge, service = setup_timeline(tmp_path, page())
    for changes in ({'turnId': 7}, {'turnId': ''}, {'turnId': ' '}, {'seqStart': True},
                    {'seqEnd': 9007199254740992},
                    {'seqStart': 9007199254740992, 'seqEnd': 9007199254740992,
                     'sourceSeqRanges': [{'startSeq': 9007199254740992, 'endSeq': 9007199254740992}]}):
        bridge.response = page()
        bridge.response['result']['entries'][1].update(changes)
        assert service.read_page(task['id'], session['id'])['result'] == {'status': 'unavailable', 'code': 'BRIDGE_INVALID_RESPONSE'}


def test_valid_empty_page_and_unsupported_locator_remain_honest(tmp_path):
    source = page()
    source['result'].update(entries=[], window={'minSeq': 0, 'maxSeq': 0, 'nextSeq': 0},
                            startCursor=None, endCursor=None, hasOlder=False, hasNewer=False)
    _, task, session, bridge, service = setup_timeline(tmp_path, source)
    response = service.read_page(task['id'], session['id'])
    assert response['result']['status'] == 'ok' and response['result']['entries'] == []
    for field in ('epoch', 'turnId'):
        bridge.response = page()
        if field == 'epoch':
            bridge.response['result'][field] = 'e' * 201
        else:
            bridge.response['result']['entries'][1][field] = 't' * 201
        entry = service.read_page(task['id'], session['id'])['result']['entries'][1]
        assert entry['candidateBlockedReason'] is not None
        assert entry['source_locator']['epoch' if field == 'epoch' else 'turn_id'] == ('e' if field == 'epoch' else 't') * 201
