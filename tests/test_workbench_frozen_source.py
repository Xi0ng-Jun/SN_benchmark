"""Frozen Git source reads: real objects, dirty checkout, bounded and task-scoped."""
import json
import subprocess
from types import SimpleNamespace

import pytest

from test_workbench_auto_runs import app_env
from rag_eval.workbench import auto_runs, agent_tools


@pytest.fixture
def product(tmp_path):
    root = tmp_path / 'product'
    root.mkdir()
    def git(*args):
        return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()
    git('init', '-q')
    git('config', 'user.email', 'offline@example.invalid')
    git('config', 'user.name', 'Offline')
    files = {'backend/app/answer.py': 'original = 1\nneedle.* = True\nlast = 3\n',
             'backend/tests/test_answer.py': 'assert True\n',
             'backend/app/data/private.py': 'hidden data\n',
             'backend/app/secrets.py': 'hidden secret\n',
             'backend/app/evaluator.py': 'hidden evaluation\n',
             'backend/app/binary.py': 'binary\x00payload',
             'frontend/src/view.ts': 'export const value = 1;\n',
             'benchmark-deepeval/src/evaluator.py': 'hidden evaluator\n',
             'backend/app/large.py': 'x' * (1024 * 1024 + 1),
             'backend/app/long.py': 'x' * 50000 + '\n' + 'line\n' * 300}
    for path, content in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    (root / 'backend/app/link.py').symlink_to('answer.py')
    git('add', '.')
    git('commit', '-qm', 'source fixture')
    commit = git('rev-parse', 'HEAD')
    (root / 'backend/app/answer.py').write_text('dirty = True\n')
    return root, commit


def reader(product, **kwargs):
    from rag_eval.workbench.frozen_source import read_frozen_source
    root, commit = product
    return read_frozen_source(root, commit, **kwargs)


def test_reads_frozen_lines_and_literal_query(product):
    result = reader(product, path='backend/app/answer.py', start=2, lines=1)
    assert result['commit'] == product[1]
    assert result['lines'] == [{'number': 2, 'text': 'needle.* = True'}]
    assert 'dirty' not in json.dumps(result)
    assert reader(product, path='backend/app/answer.py', query='.*')['lines'] == result['lines']
    assert reader(product, path='backend/app/answer.py', query='nomatch')['lines'] == []


@pytest.mark.parametrize('path', ['../backend/app/answer.py', '/backend/app/answer.py',
    'backend/app/../app/answer.py', 'backend//app/answer.py', 'backend/app/link.py',
    'backend/app/data/private.py', 'backend/app/secrets.py', 'backend/app/evaluator.py',
    'benchmark-deepeval/src/evaluator.py', 'backend/app/.env', 'backend/app/binary.py',
    'backend/app/large.py', 'backend/app/answer.py\x00ignored'])
def test_rejects_unsafe_paths_types_and_blob_sizes(product, path):
    with pytest.raises(ValueError):
        reader(product, path=path)


def test_bounds_output_and_requires_exact_commit(product):
    result = reader(product, path='backend/app/long.py', lines=200)
    assert result['truncated'] is True
    assert len(json.dumps(result, ensure_ascii=False).encode()) < 32768
    for kwargs in ({'start': 0}, {'lines': 201}, {'lines': -1}, {'query': ''}):
        with pytest.raises(ValueError):
            reader(product, path='backend/app/answer.py', **kwargs)
    from rag_eval.workbench.frozen_source import read_frozen_source
    with pytest.raises(ValueError, match='commit'):
        read_frozen_source(product[0], 'HEAD', path='backend/app/answer.py')


def test_v4_only_gate_and_cli_node_ownership(app_env, product, monkeypatch, capsys):
    _, repository, task_id, state = app_env
    auto_runs.register(repository, 'it1', run_id='r1', state_dir=str(state), actor='test', method='method-v4')
    assert auto_runs.agent_refusal(repository, task_id, 'analyst', 'source') is None
    assert auto_runs.agent_refusal(repository, task_id, 'implementer', 'source')
    assert auto_runs.agent_refusal(repository, task_id, 'reviewer', 'source')
    root, commit = product
    node = dict(id='root-it1', iteration_id='it1', frozen_commit=commit)
    service = SimpleNamespace(trees=SimpleNamespace(project_root=root), candidate=lambda _: node,
                              iteration_record=lambda _: dict(task_id=task_id))
    monkeypatch.setattr(agent_tools, 'build', lambda *_: (repository, object()))
    monkeypatch.setattr(agent_tools, 'iteration_service', lambda *_, **__: service)
    argv = ['--task', task_id, '--node', 'root-it1', 'source', '--path', 'backend/app/answer.py']
    agent_tools.main(argv)
    result = json.loads(capsys.readouterr().out)
    assert result['candidate_id'] == 'root-it1'
    assert result['lines'][0]['text'] == 'original = 1'
    with pytest.raises((ValueError, SystemExit), match='node'):
        agent_tools.main(['--task', task_id, 'source', '--path', 'backend/app/answer.py'])
    service.iteration_record = lambda _: dict(task_id='another-task')
    with pytest.raises(ValueError, match='Task'):
        agent_tools.main(argv)


def test_binding_method_is_immutable_and_legacy_is_v3(app_env):
    _, repository, task_id, state = app_env
    auto_runs.register(repository, 'it1', run_id='r1', state_dir=str(state), actor='test')
    assert auto_runs.agent_refusal(repository, task_id, 'analyst', 'source')
    from rag_eval.workbench.repository import Conflict
    with pytest.raises(Conflict):
        auto_runs.register(repository, 'it1', run_id='r1', state_dir=str(state), actor='test', method='method-v4')
    with pytest.raises(ValueError):
        auto_runs.register(repository, 'it1', run_id='r1', state_dir=str(state), actor='test', method='other')


def test_v4_briefs_require_source_grounding_and_allow_evidenced_skip():
    from rag_eval.workbench.briefs import v4_analyst_brief, v4_implementer_brief
    task = dict(id='task', title='Iteration')
    analyst = v4_analyst_brief(task, tool='sn-eval', candidate_id='node')
    for text in ('source --path', 'frozen', 'search_refs', 'cheapest', 'adopted', 'not a filesystem sandbox'):
        assert text in analyst
    implementer = v4_implementer_brief(task, tool='sn-eval', hypothesis_id='hypothesis', candidate_id='candidate', worktree='/sn')
    for text in ('preflight', 'already_satisfied', 'inconclusive', 'candidate_id', 'parent_candidate_id',
                 'hypothesis_id', 'decision', 'checks', 'independent proof'):
        assert text in implementer
    assert 'must run tests' not in implementer


def test_git_replacement_cannot_change_frozen_bytes(product):
    root, commit = product
    git = lambda *args: subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()
    git('add', 'backend/app/answer.py')
    git('commit', '-qm', 'different bytes')
    git('replace', commit, git('rev-parse', 'HEAD'))
    result = reader(product, path='backend/app/answer.py')
    assert result['lines'][0]['text'] == 'original = 1'


def test_implementer_report_example_is_json():
    from rag_eval.workbench.briefs import v4_implementer_brief
    text = v4_implementer_brief(dict(id='task', title='Iteration'), tool='sn-eval',
                               hypothesis_id='h', candidate_id='c', worktree='/sn')
    example = next(line for line in text.splitlines() if line.startswith('{'))
    assert set(json.loads(example)) == {'candidate_id', 'hypothesis_id', 'parent_candidate_id',
                                        'decision', 'reason', 'summary', 'checks'}


def test_legacy_database_binding_cannot_gain_source_permission(app_env):
    _, repository, task_id, state = app_env
    with repository.connect(write=True) as conn:
        conn.execute('CREATE TABLE auto_runs(iteration_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, '
                     'state_dir TEXT NOT NULL, actor TEXT NOT NULL, registered_at TEXT NOT NULL)')
        conn.execute('INSERT INTO auto_runs VALUES(?,?,?,?,?)', ('it1', 'r1', str(state), 'test', 'now'))
    assert auto_runs.agent_refusal(repository, task_id, 'analyst', 'source')
    binding = auto_runs.register(repository, 'it1', run_id='r1', state_dir=str(state), actor='test')
    assert binding['method'] == 'method-v3'
    assert auto_runs.agent_refusal(repository, task_id, 'analyst', 'source')
