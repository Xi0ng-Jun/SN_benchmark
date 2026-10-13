import importlib

from fastapi.testclient import TestClient

from test_benchmark_comparison import examples


def test_session_association_actor_origin_retry_and_export(tmp_path):
    api = importlib.import_module('rag_eval.workbench.api')
    config = dict(workspace={'title': 'SN', 'location': '/sn'})
    app = api.create_app(tmp_path / 'state', config, actor='alice', access_token='test-access')
    with TestClient(app) as client:
        client.post('/api/session', json={'token': 'test-access'})
        ws = client.get('/api/bootstrap').json()['workspaces'][0]
        task = client.post('/api/tasks', json=dict(workspace_id=ws['id'], title='Investigate', request_key='task'), headers={'X-Workbench': '1'}).json()
        path = '/api/tasks/' + task['id'] + '/sessions'
        payload = dict(agent_id='paseo-agent-1', request_key='link')
        assert client.post(path, json=payload).status_code == 403
        headers = {'X-Workbench': '1'}
        assert client.post(path, json={**payload, 'actor': 'forged'}, headers=headers).status_code == 422
        linked = client.post(path, json=payload, headers=headers)
        assert linked.status_code == 200
        assert linked.json()['linked_by'] == 'alice'
        assert client.post(path, json=payload, headers=headers).json() == linked.json()
        assert client.post(path, json={**payload, 'agent_id': 'different'}, headers=headers).status_code == 409
        exported = client.get('/api/tasks/' + task['id'] + '/export').json()
        assert exported['sessions'] == [linked.json()]
        assert [e['kind'] for e in exported['history']] == ['task.created', 'session.attached']


def test_http_scope_actor_persistence_and_export(tmp_path):
    _, entries, _, _ = examples(tmp_path, count=1)
    api = importlib.import_module('rag_eval.workbench.api')
    config = dict(workspace={'title': 'SN development', 'location': '/sn'},
                  sources=[dict(id='fixture', title='Explicit fixture', description='test only',
                                bundle=str(tmp_path / 'bundle'), **{k:str(v) for k,v in entries[0].items()})])
    app = api.create_app(tmp_path / 'state', config, actor='local-reviewer', access_token='test-access')
    with TestClient(app) as client:
        assert client.get('/api/bootstrap').status_code == 401
        assert client.post('/api/session', json={'token':'wrong'}).status_code == 401
        assert client.post('/api/session', json={'token':'test-access'}).status_code == 200
        bootstrap = client.get('/api/bootstrap').json()
        ws = bootstrap['workspaces'][0]
        task = client.post('/api/tasks', json=dict(workspace_id=ws['id'],title='Investigate',goal='Check facts',request_key='t'), headers={'X-Workbench':'1'}).json()
        denied = client.post('/api/tasks/' + task['id'] + '/results', json={'source_id':'fixture'}, headers={'Origin':'https://untrusted.example','X-Workbench':'1'})
        assert denied.status_code == 403
        attached = client.post('/api/tasks/' + task['id'] + '/results', json={'source_id':'fixture'}, headers={'X-Workbench':'1'})
        assert attached.status_code == 200
        result = attached.json()
        detail = client.get('/api/results/' + result['id']).json()
        cid = detail['cases'][0]['case_id']
        packet = client.get('/api/results/' + result['id'] + '/case', params={'case_id':cid}).json()
        assert packet['prediction'] == 'Cats run.'
        review = dict(result_id=result['id'],case_id=cid,judgment='confirmed',reason='An observation',evidence=['answer'],base_revision=None,request_key='r',reviewer='forged')
        assert client.post('/api/tasks/' + task['id'] + '/reviews', json=review,headers={'X-Workbench':'1'}).status_code == 422
        review.pop('reviewer')
        receipt = client.post('/api/tasks/' + task['id'] + '/reviews',json=review,headers={'X-Workbench':'1'}).json()
        assert receipt['reviewer'] == 'local-reviewer'
        exported = client.get('/api/tasks/' + task['id'] + '/export').json()
        assert len(exported['history']) == 3
        assert exported['history'][-1]['content']['target']['scores'] == detail['scores_id']
    with TestClient(api.create_app(tmp_path / 'state',config,actor='local-reviewer',access_token='test-access')) as restored:
        restored.post('/api/session',json={'token':'test-access'})
        assert len(restored.get('/api/tasks/' + task['id']).json()['history']) == 3
