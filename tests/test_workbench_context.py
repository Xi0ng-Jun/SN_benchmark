"""Explicit context selection uses task scope and bounded, frozen content."""
import importlib
import json

import pytest
from fastapi.testclient import TestClient

from rag_eval.workbench.repository import WorkbenchRepository
from test_workbench import import_fixture


def setup_task(tmp_path):
    checkout = tmp_path / 'checkout'
    checkout.mkdir()
    repo = WorkbenchRepository(tmp_path / 'state')
    workspace = repo.register_workspace('Development', checkout)
    task = repo.create_task(workspace['id'], 'Investigate', 'Explain the observed behavior', request_key='task')
    return repo, task, checkout


def resolve(repo, task, reference):
    return importlib.import_module('rag_eval.workbench.context').resolve_context(repo, task['id'], reference)


def test_goal_and_file_context_are_explicit_content_snapshots(tmp_path):
    repo, task, checkout = setup_task(tmp_path)
    (checkout / 'docs').mkdir()
    document = checkout / 'docs' / 'design.md'
    document.write_text('Design evidence', encoding='utf-8')
    goal = resolve(repo, task, {'kind': 'goal'})
    assert goal['text'] == task['goal']
    snapshot = resolve(repo, task, {'kind': 'file', 'path': 'docs/design.md'})
    assert snapshot['text'] == 'Design evidence'
    assert snapshot['reference'] == {'kind': 'file', 'path': 'docs/design.md'}
    assert str(checkout) not in str(snapshot)
    document.write_text('Changed evidence', encoding='utf-8')
    changed = resolve(repo, task, snapshot['reference'])
    assert changed['sha256'] != snapshot['sha256']
    assert snapshot['text'] == 'Design evidence'


def test_context_rejects_escape_private_binary_oversize_and_unknown_fields(tmp_path):
    repo, task, checkout = setup_task(tmp_path)
    (tmp_path / 'outside.txt').write_text('Private outside')
    (checkout / 'escape').symlink_to(tmp_path / 'outside.txt')
    (checkout / '.env').write_text('Private configuration')
    (checkout / 'var').mkdir()
    (checkout / 'var' / 'secret').write_text('Private state')
    (checkout / 'binary').write_bytes(b'\x00\xff')
    (checkout / 'large').write_text('x' * 65537)
    for path in ('../outside.txt', str(tmp_path / 'outside.txt'), 'escape', '.env', 'var/secret', 'binary', 'large'):
        with pytest.raises(ValueError):
            resolve(repo, task, {'kind': 'file', 'path': path})
    with pytest.raises(ValueError):
        resolve(repo, task, {'kind': 'goal', 'path': '.env'})
    assert len(repo.task(task['id'])['history']) == 1  # Reading is not a domain write.


def test_file_context_excludes_runtime_and_non_text_controls_and_keeps_byte_limit(tmp_path):
    repo, task, checkout = setup_task(tmp_path)
    (checkout / 'runtime').mkdir()
    (checkout / 'runtime' / 'trace.txt').write_text('Private runtime trace')
    with pytest.raises(ValueError, match='outside selectable'):
        resolve(repo, task, {'kind': 'file', 'path': 'runtime/trace.txt'})
    (checkout / 'controls').write_bytes(b'\x01' * 100)
    with pytest.raises(ValueError, match='not text'):
        resolve(repo, task, {'kind': 'file', 'path': 'controls'})
    (checkout / 'boundary').write_text('é' * 32768, encoding='utf-8')
    assert len(resolve(repo, task, {'kind': 'file', 'path': 'boundary'})['text']) == 32768
    (checkout / 'boundary').write_text('é' * 32768 + 'x', encoding='utf-8')
    with pytest.raises(ValueError, match='exceeds'):
        resolve(repo, task, {'kind': 'file', 'path': 'boundary'})


def test_result_and_case_context_require_attachment_and_exclude_gold(tmp_path):
    imported = import_fixture(tmp_path)
    repo, task, _ = setup_task(tmp_path)
    ref = {'kind': 'result', 'result_id': imported['id']}
    with pytest.raises(ValueError, match='attached'):
        resolve(repo, task, ref)
    repo.attach_result(task['id'], imported['id'])
    result = resolve(repo, task, ref)
    assert imported['id'] in result['text']
    result_text = json.loads(result['text'])
    assert set(result_text) == {'id', 'suite', 'scope', 'case_count', 'bundle_case_count', 'metrics', 'pending_metrics', 'coverage', 'method'}
    assert set(result_text['method']) == {'name', 'kind'}
    reader = importlib.import_module('rag_eval.workbench.ingest')
    case_id = reader.read_result(repo.root, imported['id'])['cases'][0]['case_id']
    case = resolve(repo, task, {'kind': 'case', 'result_id': imported['id'], 'case_id': case_id})
    assert 'Cats run.' in case['text']
    assert 'gold' not in case['text']
    assert 'references' not in case['text']
    assert set(json.loads(case['text'])) == {'case_id', 'question', 'status', 'answer', 'answer_source'}
    assert case['source_refs']['scores']


def test_http_context_preview_uses_workspace_scope_without_writes(tmp_path):
    checkout = tmp_path / 'checkout'
    checkout.mkdir()
    (checkout / 'README.md').write_text('Workspace context')
    module = importlib.import_module('rag_eval.workbench.api')
    app = module.create_app(tmp_path / 'state', {'workspace': {'title': 'SN', 'location': str(checkout)}}, access_token='test-access')
    with TestClient(app) as client:
        client.post('/api/session', json={'token': 'test-access'})
        ws = client.get('/api/bootstrap').json()['workspaces'][0]
        task = client.post('/api/tasks', json=dict(workspace_id=ws['id'], title='Investigate', request_key='t'), headers={'X-Workbench': '1'}).json()
        path = '/api/tasks/' + task['id'] + '/context'
        assert client.post(path, json={'reference': {'kind': 'file', 'path': 'README.md'}}).status_code == 403
        response = client.post(path, json={'reference': {'kind': 'file', 'path': 'README.md'}}, headers={'X-Workbench': '1'})
        assert response.status_code == 200
        assert response.json()['text'] == 'Workspace context'
        assert client.post(path, json={'reference': {'kind': 'file', 'path': '../outside'}}, headers={'X-Workbench': '1'}).status_code == 422
        assert len(client.get('/api/tasks/' + task['id']).json()['history']) == 1


def test_run_case_context_is_analysis_material_with_gold_and_retrieval(tmp_path):
    from rag_eval.workbench.run_views import RunEntry, RunViews
    from test_run_views import CASE, RUN, SCORES
    repo, task, _ = setup_task(tmp_path)
    views = RunViews([RunEntry('e5', RUN, SCORES)])
    reference = {'kind': 'run_case', 'run_id': 'e5', 'case_id': CASE}
    with pytest.raises(ValueError, match='not configured'):
        resolve(repo, task, reference)
    module = importlib.import_module('rag_eval.workbench.context')
    snapshot = module.resolve_context(repo, task['id'], reference, run_views=views)
    content = json.loads(snapshot['text'])
    assert content['case_id'] == CASE and content['question']
    assert content['role'].startswith('analysis-only') and content['gold'] is not None
    assert content['retrieved'] and {'rank', 'cited', 'contains_gold'} <= set(content['retrieved'][0])
    assert content['artifacts']['outputs'].endswith('outputs.jsonl')
    assert snapshot['source_refs'] == {'run_id': 'e5', 'case_id': CASE}
    with pytest.raises(ValueError, match='not registered'):
        module.resolve_context(repo, task['id'], {**reference, 'run_id': 'unknown'}, run_views=views)


def test_task_runs_context_summarizes_only_this_tasks_runs(tmp_path):
    from rag_eval.workbench.run_views import RunEntry, RunViews
    from test_run_views import RUN, SCORES
    repo, task, _ = setup_task(tmp_path)
    views = RunViews([RunEntry('mine', RUN, SCORES, task_id=task['id']), RunEntry('other', RUN, SCORES, task_id='other')])
    module = importlib.import_module('rag_eval.workbench.context')
    snapshot = module.resolve_context(repo, task['id'], {'kind': 'task_runs'}, run_views=views)
    content = json.loads(snapshot['text'])
    assert [run['id'] for run in content['runs']] == ['mine']
    run = content['runs'][0]
    assert run['phase'] and run['run_dir'] and 'attention' in run and 'metrics' in run['official']
    assert snapshot['title'] == '本任务的评测运行 · 1 个'
