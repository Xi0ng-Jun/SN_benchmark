"""HTTP collaboration preserves server scope, exact sources and human decisions."""
import copy

from rag_eval.workbench.candidates import CandidateStore
from rag_eval.workbench.repository import WorkbenchRepository
from test_workbench_session_api import create_link, fixture
from test_workbench_sessions import Reader
from test_workbench_timeline import page


HEADERS = {'X-Workbench': '1'}


def linked_page(client, reader):
    task, session = create_link(client)
    reader.response['result']['agentId'] = 'agent-1'
    path = f"/api/tasks/{task['id']}/sessions/{session['id']}"
    return task, session, path


def test_http_timeline_fixes_scope_and_preserves_actual_page_without_writes(tmp_path):
    reader = Reader(page())
    with fixture(tmp_path, reader) as client:
        task, session, path = linked_page(client, reader)
        cursor = {'epoch': 'epoch-example', 'seq': 14}
        response = client.post(path + '/timeline', json={'direction': 'before', 'cursor': cursor}, headers=HEADERS)
        assert response.status_code == 200
        result = response.json()['result']
        assert result['entries'][1]['text'] == 'Example read.'
        assert result['startCursor'] == {'epoch': 'epoch-example', 'seq': 10}
        assert result['endCursor'] == {'epoch': 'epoch-example', 'seq': 13}
        assert result['hasOlder'] is True and result['hasNewer'] is True
        assert reader.calls == [{'op': 'readTimeline', 'agentId': 'agent-1', 'expectedCwd': str(tmp_path / 'code'),
                                 'projection': 'projected', 'limit': 100, 'direction': 'before', 'cursor': cursor}]
        history = client.get('/api/tasks/' + task['id']).json()['history']
        assert [event['kind'] for event in history] == ['task.created', 'session.attached']


def test_http_timeline_rejects_client_scope_and_invalid_cursor_before_remote_read(tmp_path):
    reader = Reader(page())
    with fixture(tmp_path, reader) as client:
        _, _, path = linked_page(client, reader)
        for command in ({'agentId': 'foreign'}, {'expectedCwd': '/foreign'}, {'limit': 100},
                        {'projection': 'raw'}, {'config': {}}, {'direction': 'sideways'},
                        {'cursor': {'epoch': 'epoch-example', 'seq': True}},
                        {'cursor': {'epoch': 'epoch-example', 'seq': -1}},
                        {'cursor': {'epoch': 'epoch-example', 'seq': 9007199254740992}},
                        {'cursor': {'epoch': 'epoch-example', 'seq': 14, 'offset': 1}}):
            response = client.post(path + '/timeline', json=command, headers=HEADERS)
            assert response.status_code == 422
        assert client.post(path.replace('/sessions/', '/sessions/foreign-') + '/timeline', json={}, headers=HEADERS).status_code == 422
        assert reader.calls == []


def test_http_unconfigured_timeline_preserves_association_and_validates_scope(tmp_path):
    with fixture(tmp_path) as client:
        task, session = create_link(client)
        path = f"/api/tasks/{task['id']}/sessions/{session['id']}/timeline"
        response = client.post(path, json={}, headers=HEADERS)
        assert response.status_code == 200
        assert response.json()['result'] == {'status': 'unavailable', 'code': 'BRIDGE_NOT_CONFIGURED'}
        assert response.json()['release'] == {'status': 'not_needed'}
        assert client.post(path.replace(session['id'], 'foreign'), json={}, headers=HEADERS).status_code == 422
        assert client.get('/api/tasks/' + task['id']).json()['sessions'] == [session]


def test_http_sourced_save_replays_receipt_and_requires_explicit_human_decision(tmp_path):
    reader = Reader(page())
    with fixture(tmp_path, reader) as client:
        task, session, path = linked_page(client, reader)
        response = client.post(path + '/timeline', json={}, headers=HEADERS)
        assert response.status_code == 200
        locator = response.json()['result']['entries'][1]['source_locator']
        command = {'locator': locator, 'kind': 'finding', 'text': 'Check the mapping', 'request_key': 'candidate'}
        saved_response = client.post(path + '/candidates', json=command, headers=HEADERS)
        assert saved_response.status_code == 200
        saved = saved_response.json()
        assert saved['source']['text'] == 'Example read.'
        assert saved['source']['sha256'] == locator['sha256']
        assert saved['selected_by'] == 'alice' and saved['origin'] == 'agent'
        assert reader.calls[-1]['cursor'] == {'epoch': 'epoch-example', 'seq': 14}
        reads = len(reader.calls)
        reader.response = {'result': {'status': 'missing', 'code': 'AGENT_NOT_FOUND'}, 'release': {'status': 'ok'}}
        assert client.post(path + '/candidates', json=command, headers=HEADERS).json() == saved
        assert len(reader.calls) == reads
        assert client.post(path + '/candidates', json={**command, 'text': 'Changed'}, headers=HEADERS).status_code == 409
        list_path = '/api/tasks/' + task['id'] + '/candidates'
        listed = client.get(list_path).json()
        assert len(listed) == 1 and listed[0]['decision'] is None
        decision = {'judgment': 'confirmed', 'reason': 'Checked manually', 'base_revision': None, 'request_key': 'decision'}
        acknowledged = client.post(list_path + '/' + saved['id'] + '/decisions', json=decision, headers=HEADERS)
        assert acknowledged.status_code == 200 and acknowledged.json()['actor'] == 'alice'
        assert client.post(list_path + '/' + saved['id'] + '/decisions', json=decision, headers=HEADERS).json() == acknowledged.json()
        assert client.post(list_path + '/' + saved['id'] + '/decisions', json={**decision, 'request_key': 'stale'}, headers=HEADERS).status_code == 409
        listed = client.get(list_path).json()
        assert listed[0]['source'] == saved['source'] and len(listed[0]['decisions']) == 1


def test_http_candidate_cannot_forge_source_actor_or_save_changed_sdk_text(tmp_path):
    reader = Reader(page())
    with fixture(tmp_path, reader) as client:
        task, _, path = linked_page(client, reader)
        response = client.post(path + '/timeline', json={}, headers=HEADERS)
        assert response.status_code == 200
        locator = response.json()['result']['entries'][1]['source_locator']
        command = {'locator': locator, 'kind': 'direction', 'text': 'Check', 'request_key': 'candidate'}
        reads = len(reader.calls)
        for injected in ({'actor': 'forged'}, {'agentId': 'foreign'}, {'source': {'text': 'forged'}},
                         {'locator': {**locator, 'text': 'forged'}}):
            assert client.post(path + '/candidates', json={**command, **injected}, headers=HEADERS).status_code == 422
        assert len(reader.calls) == reads
        changed = copy.deepcopy(reader.response)
        changed['result']['entries'][1]['item']['text']['value'] = 'Changed remotely'
        reader.response = changed
        assert client.post(path + '/candidates', json=command, headers=HEADERS).status_code == 409
        assert client.get('/api/tasks/' + task['id'] + '/candidates').json() == []
        assert len(client.get('/api/tasks/' + task['id']).json()['history']) == 2


def test_http_human_decision_is_separate_actor_owned_and_task_scoped(tmp_path):
    reader = Reader(page())
    with fixture(tmp_path, reader) as client:
        task, session = create_link(client)
        store = CandidateStore(WorkbenchRepository(tmp_path / 'state'))
        saved = store.save(task['id'], session['id'],
                           {'agent_id': 'agent-1', 'epoch': 'epoch-example', 'seq_start': 11, 'seq_end': 13,
                            'text': 'Original model excerpt'},
                           kind='finding', text='A candidate', actor='alice', request_key='seed')
        path = '/api/tasks/' + task['id'] + '/candidates'
        listed = client.get(path)
        assert listed.status_code == 200
        assert listed.json()[0]['decision'] is None
        decision = {'judgment': 'undecided', 'reason': 'Needs more evidence', 'base_revision': None, 'request_key': 'human'}
        target = path + '/' + saved['id'] + '/decisions'
        assert client.post(target, json={**decision, 'actor': 'forged'}, headers=HEADERS).status_code == 422
        other = client.post('/api/tasks', json={'workspace_id': task['workspace_id'], 'title': 'Other task',
                                               'request_key': 'other-task'}, headers=HEADERS).json()
        assert client.post(target.replace(task['id'], other['id']), json=decision, headers=HEADERS).status_code == 422
        assert client.get('/api/tasks/' + other['id'] + '/candidates').json() == []
        assert [event['kind'] for event in client.get('/api/tasks/' + other['id']).json()['history']] == ['task.created']
        response = client.post(target, json=decision, headers=HEADERS)
        assert response.status_code == 200 and response.json()['actor'] == 'alice'
        assert client.post(target, json=decision, headers=HEADERS).json() == response.json()
        assert client.post(target, json={**decision, 'request_key': 'stale'}, headers=HEADERS).status_code == 409
        listed = client.get(path).json()
        assert listed[0]['source'] == saved['source'] and len(listed[0]['decisions']) == 1
        assert reader.calls == []
