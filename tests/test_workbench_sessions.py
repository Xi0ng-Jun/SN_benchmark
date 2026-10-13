"""Task-local associations never authorize reading another workspace."""
import copy
import importlib

import pytest

from rag_eval.workbench.repository import WorkbenchRepository


class Reader:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def call(self, command):
        self.calls.append(command)
        return copy.deepcopy(self.response)


def setup_reader(tmp_path, response):
    repo = WorkbenchRepository(tmp_path / 'state')
    workspace = repo.register_workspace('SN', str(tmp_path / 'code'))
    task = repo.create_task(workspace['id'], 'Investigate', '', request_key='task')
    session = repo.attach_session(task['id'], 'agent-1', actor='alice', request_key='link')
    reader = Reader(response)
    service = importlib.import_module('rag_eval.workbench.sessions').TaskSessionReader(repo, reader)
    return repo, task, session, reader, service


def snapshot(tmp_path):
    return {'result': {'status': 'ok', 'agent': {
        'id': 'agent-1', 'cwd': str(tmp_path / 'code'), 'title': 'Read code',
        'status': 'idle', 'provider': 'codex', 'model': 'test-model',
        'workspaceId': 'private-workspace', 'metadata': {'secret': 'do not expose'},
        'capabilities': {'supportsStreaming': True}}}, 'release': {'status': 'ok'}}


def test_agent_read_projects_only_browser_fields_without_writing_history(tmp_path):
    repo, task, session, reader, service = setup_reader(tmp_path, snapshot(tmp_path))
    before = repo.task(task['id'])
    response = service.read_agent(task['id'], session['id'])
    assert reader.calls == [{'op': 'readAgent', 'agentId': 'agent-1'}]
    assert response['result'] == {'status': 'ok', 'agent': {
        'id': 'agent-1', 'title': 'Read code', 'status': 'idle',
        'provider': 'codex', 'model': 'test-model'}}
    assert response['release'] == {'status': 'ok'}
    assert isinstance(response['observed_at'], str)
    assert repo.task(task['id']) == before


def test_foreign_local_association_fails_before_remote_read(tmp_path):
    repo, task, session, reader, service = setup_reader(tmp_path, snapshot(tmp_path))
    second = repo.create_task(task['workspace_id'], 'Other', '', request_key='other')
    for task_id, session_id in ((second['id'], session['id']), (task['id'], 'unknown'), ('unknown', session['id'])):
        with pytest.raises(ValueError):
            service.read_agent(task_id, session_id)
    assert reader.calls == []


@pytest.mark.parametrize('change', [{'cwd': '/other'}, {'cwd': None}, {'id': 'different'}])
def test_remote_workspace_and_identity_mismatch_never_exposes_agent(tmp_path, change):
    source = snapshot(tmp_path)
    source['result']['agent'].update(change)
    _, task, session, _, service = setup_reader(tmp_path, source)
    response = service.read_agent(task['id'], session['id'])
    assert response['result'] == {'status': 'unavailable', 'code': 'AGENT_SCOPE_MISMATCH'}
    assert response['release'] == {'status': 'ok'}


@pytest.mark.parametrize('result', [{'status': 'missing', 'code': 'AGENT_NOT_FOUND'},
                                  {'status': 'unavailable', 'code': 'CONNECT_FAILED'}])
def test_missing_and_unavailable_keep_association_and_release(tmp_path, result):
    response = {'result': result, 'release': {'status': 'unavailable', 'code': 'CLOSE_FAILED'}}
    repo, task, session, _, service = setup_reader(tmp_path, response)
    read = service.read_agent(task['id'], session['id'])
    assert read['result'] == result
    assert read['release'] == response['release']
    assert repo.task(task['id'])['sessions'] == [session]


def test_successful_read_does_not_hide_release_failure(tmp_path):
    response = snapshot(tmp_path)
    response['release'] = {'status': 'unavailable', 'code': 'CLOSE_FAILED'}
    _, task, session, _, service = setup_reader(tmp_path, response)
    read = service.read_agent(task['id'], session['id'])
    assert read['result']['status'] == 'ok'
    assert read['release'] == response['release']


@pytest.mark.parametrize('change', [{'cwd': '/bad\x00path'}, {'remove': 'status'}, {'remove': 'provider'}, {'remove': 'title'}, {'remove': 'model'}])
def test_malformed_snapshot_fails_with_fixed_result(tmp_path, change):
    response = snapshot(tmp_path)
    if 'remove' in change:
        response['result']['agent'].pop(change['remove'])
    else:
        response['result']['agent'].update(change)
    _, task, session, _, service = setup_reader(tmp_path, response)
    assert service.read_agent(task['id'], session['id'])['result'] == {
        'status': 'unavailable', 'code': 'BRIDGE_INVALID_RESPONSE'}
