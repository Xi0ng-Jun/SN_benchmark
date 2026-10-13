"""Independent-state HTTP integration; no daemon, scorer or model execution."""
import json
import shutil

from fastapi.testclient import TestClient
import pytest

from test_benchmark_comparison import examples
from rag_eval.identity import fingerprint
from rag_eval.workbench.api import create_app
from rag_eval.workbench.repository import WorkbenchRepository


HEADERS = {'Authorization': 'Bearer test-access', 'X-Workbench': '1'}


def setup(tmp_path, *, inputs=True):
    bundle, entries, _, _ = examples(tmp_path)
    config = {'workspace': {'title': 'SN', 'location': str(tmp_path / 'workspace')},
              'sources': [{'id': str(i), 'title': f'Fixture {i}', 'description': 'Synthetic scores',
                           'bundle': str(tmp_path / 'bundle'),
                           **{k: str(v) for k, v in entry.items()}} for i, entry in enumerate(entries)]}
    if inputs:
        config['inputs'] = [{'id': 'canonical', 'title': 'Canonical', 'description': 'Independent input',
                             'bundle': str(tmp_path / 'bundle'), 'bundle_id': fingerprint(bundle['manifest'])}]
    client = TestClient(create_app(tmp_path / 'state', config, actor='alice', access_token='test-access'))
    ws = client.get('/api/bootstrap', headers=HEADERS).json()['workspaces'][0]
    task = client.post('/api/tasks', json={'workspace_id': ws['id'], 'title': 'Plan or compare',
                                        'request_key': 'task'}, headers=HEADERS).json()
    return client, config, task, bundle


def command(bundle):
    return {'title': 'Explicit draft', 'input_id': 'canonical',
            'expected_bundle_id': fingerprint(bundle['manifest']),
            'case_ids': [bundle['cases'][1]['case_id'], bundle['cases'][0]['case_id']],
            'mode': 'chunk', 'plan_id': None, 'base_revision': None, 'request_key': 'plan'}


def test_plan_without_results_save_reopen_revision_receipt_and_source_loss(tmp_path):
    client, config, task, bundle = setup(tmp_path)
    path = f"/api/tasks/{task['id']}/plans"
    payload = command(bundle)
    assert client.get('/api/inputs/canonical', headers=HEADERS).json()['bundle_id'] == payload['expected_bundle_id']
    assert client.get(f"/api/tasks/{task['id']}", headers=HEADERS).json()['results'] == []
    saved_response = client.post(path, json=payload, headers=HEADERS)
    assert saved_response.status_code == 200
    assert saved_response.json()['request_key'] == payload['request_key']
    saved = saved_response.json()['record']
    assert saved['actor'] == 'alice'
    assert saved['state'] == 'draft' and saved['execution'] is False
    assert saved['content']['source_intent']['binding'] == 'not_captured'
    assert saved['content']['execution_intent']['model_services_ref'] is None
    assert saved['content']['execution_intent']['official_scorer_ref'] is None
    assert saved['content']['selection']['case_ids'] == [case['case_id'] for case in bundle['cases']]
    assert saved['content']['selection']['kind'] == 'explicit'
    revised_payload = {**payload, 'plan_id': saved['plan_id'], 'base_revision': saved['revision'],
                       'request_key': 'revise', 'title': 'Revised draft'}
    revised = client.post(path, json=revised_payload, headers=HEADERS).json()['record']
    assert revised['parent_revision'] == saved['revision']
    assert client.post(path, json={**revised_payload, 'request_key': 'stale'}, headers=HEADERS).status_code == 409
    shutil.rmtree(tmp_path / 'bundle')
    with TestClient(create_app(tmp_path / 'state', config, actor='alice', access_token='test-access')) as reopened:
        assert reopened.get(path, headers=HEADERS).json() == [revised]
        assert reopened.get(path + '/' + saved['plan_id'], params={'revision': saved['revision']}, headers=HEADERS).json() == saved
        assert reopened.get(path + '/receipt', params={'request_key': 'plan'}, headers=HEADERS).json() == {'request_key': 'plan', 'record': saved}
        assert reopened.post(path, json=payload, headers=HEADERS).json() == {'request_key': 'plan', 'record': saved}
        assert reopened.post(path, json={**payload, 'request_key': 'missing'}, headers=HEADERS).status_code == 404
    history = WorkbenchRepository(tmp_path / 'state').task(task['id'])['history']
    assert [e['kind'] for e in history].count('plan.draft.saved') == 2
    without_inputs = {key: value for key, value in config.items() if key != 'inputs'}
    with TestClient(create_app(tmp_path / 'state', without_inputs, actor='alice', access_token='test-access')) as retired:
        assert retired.get(path, headers=HEADERS).json() == [revised]
        assert retired.get('/api/bootstrap', headers=HEADERS).json()['inputs'] == []


@pytest.mark.parametrize('field,value', [('actor', 'forged'), ('workspace_id', 'forged'),
                                         ('partition_ids', []), ('content', {'execution': True}),
                                         ('canonical_bundle_directory', '/private/path')])
def test_plan_commands_reject_server_fields_paths_and_implicit_partitions(tmp_path, field, value):
    client, _, task, bundle = setup(tmp_path)
    path = f"/api/tasks/{task['id']}/plans"
    assert client.post(path, json={**command(bundle), field: value}, headers=HEADERS).status_code == 422
    assert client.get(path, headers=HEADERS).json() == []


def test_plan_routes_auth_origin_and_catalog_do_not_expose_materials(tmp_path):
    client, _, task, bundle = setup(tmp_path)
    path = f"/api/tasks/{task['id']}/plans"
    assert client.get(path).status_code == 401
    assert client.post(path, json=command(bundle), headers={**HEADERS, 'Origin': 'https://other.invalid'}).status_code == 403
    boot = client.get('/api/bootstrap', headers=HEADERS).json()
    assert boot['capabilities']['execution'] is False
    assert boot['capabilities']['plan_drafts'] is True
    assert boot['inputs'] == [{'id': 'canonical', 'title': 'Canonical', 'description': 'Independent input'}]
    description = client.get('/api/inputs/canonical', headers=HEADERS).json()
    assert str(tmp_path) not in json.dumps(description)
    assert all(secret not in json.dumps(description) for secret in ('Cats run', 'gold', 'answer', 'references'))


def test_domain_rejection_identifies_original_command_and_leaves_no_plan_write(tmp_path):
    client, _, task, bundle = setup(tmp_path)
    path = f"/api/tasks/{task['id']}/plans"
    payload = {**command(bundle), 'case_ids': ['unknown-case']}
    response = client.post(path, json=payload, headers=HEADERS)
    assert response.status_code == 422
    assert response.json() == {'error': 'Selection is outside the canonical bundle',
                               'code': 'PLAN_SAVE_REJECTED', 'request_key': 'plan'}
    assert client.get(path, headers=HEADERS).json() == []
    assert client.get(path + '/receipt', params={'request_key': 'plan'}, headers=HEADERS).json() == {'request_key': 'plan', 'record': None}
    assert WorkbenchRepository(tmp_path / 'state').task(task['id'])['history'][-1]['kind'] == 'task.created'


def test_comparison_reads_frozen_objects_from_trusted_registration_and_never_writes(tmp_path):
    client, _, task, _ = setup(tmp_path)
    results = [client.post(f"/api/tasks/{task['id']}/results", json={'source_id': str(i)}, headers=HEADERS).json()
               for i in range(2)]
    params = {'left_result_id': results[0]['id'], 'right_result_id': results[1]['id']}
    path = f"/api/tasks/{task['id']}/comparison"
    before = (tmp_path / 'state' / 'workbench.sqlite').read_bytes()
    response = client.get(path, params=params, headers=HEADERS)
    assert response.status_code == 200
    result = response.json()
    assert result['status'] == 'comparable'
    assert result['report']['methods'][0]['metrics']['rougeL'] == .4
    assert str(tmp_path) not in json.dumps(result)
    assert (tmp_path / 'state' / 'workbench.sqlite').read_bytes() == before
    assert client.get(path, params={**params, 'right_result_id': results[0]['id']}, headers=HEADERS).status_code == 422
    assert client.post(path, json=params, headers=HEADERS).status_code == 405
    for field in ('bundle', 'canonical_bundle_directory'):
        assert client.get(path, params={**params, field: '/private/path'}, headers=HEADERS).status_code == 422


def test_missing_registration_does_not_trust_commit_paths_and_preserves_sides(tmp_path):
    client, _, task, _ = setup(tmp_path, inputs=False)
    results = [client.post(f"/api/tasks/{task['id']}/results", json={'source_id': str(i)}, headers=HEADERS).json()
               for i in range(2)]
    response = client.get(f"/api/tasks/{task['id']}/comparison", headers=HEADERS,
                          params={'left_result_id': results[0]['id'], 'right_result_id': results[1]['id']})
    assert response.status_code == 200
    result = response.json()
    assert result['status'] == 'unavailable'
    assert result['left']['status'] == result['right']['status'] == 'readable'
    assert result['report'] is None


def test_registered_identity_mismatch_never_selects_unpinned_canonical(tmp_path):
    client, config, task, _ = setup(tmp_path)
    results = [client.post(f"/api/tasks/{task['id']}/results", json={'source_id': str(i)}, headers=HEADERS).json()
               for i in range(2)]
    config['inputs'][0]['bundle_id'] = '0' * 64
    with TestClient(create_app(tmp_path / 'state', config, actor='alice', access_token='test-access')) as other:
        result = other.get(f"/api/tasks/{task['id']}/comparison", headers=HEADERS,
                           params={'left_result_id': results[0]['id'], 'right_result_id': results[1]['id']}).json()
        assert result['status'] == 'unavailable'
        assert result['report'] is None
        assert result['left']['status'] == result['right']['status'] == 'readable'
