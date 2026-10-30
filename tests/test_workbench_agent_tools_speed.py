"""sn-eval read commands: import-light startup and one execution discovery per command."""
import json
from pathlib import Path
import subprocess
import sys
import time

import pytest

from test_workbench_iterations import BUDGET, env, finish, iteration  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]


def grown(env, children=6, attempts=2, partitions=4):
    """An iteration with evaluated children; every execution plans a few partition runs."""
    service = env['service']
    tree = iteration(env)
    root = tree['root_candidate_id']
    ids = [root]
    for i in range(children):
        child = service.derive(root, note=f'child {i}', actor='alice', request_key=f'c{i}')
        Path(child['worktree']['path'], 'backend/app/__init__.py').write_text(f'VALUE = {10 + i}\n')
        service.freeze(child['id'], actor='alice')
        ids.append(child['id'])
    for cid in ids:
        for a in range(attempts):
            finish(env, service.run(cid, budget=BUDGET, actor='alice', request_key=f'{cid}-{a}'))
    for record in env['executions'].records(env['workspace']['id']):
        run_dir = Path(record['run_dir'])
        plan = []
        for p in range(partitions):
            (run_dir / 'partitions' / f'p{p}').mkdir(parents=True, exist_ok=True)
            plan.append(dict(run_dir=str(run_dir / 'partitions' / f'p{p}'), case_ids=['qasper:q'], suite='qasper', mode='chunk'))
        (run_dir / 'batch-plan.json').write_text(json.dumps(plan))
    return tree


@pytest.fixture
def cli(env, monkeypatch, capsys):
    from rag_eval.workbench import agent_tools
    from rag_eval.workbench.job_execution import JobExecutionStore
    from rag_eval.workbench.run_views import RunViews
    calls = []
    original = RunViews._discover_executions_locked

    def counted(self):
        calls.append(1)
        return original(self)
    monkeypatch.setattr(RunViews, '_discover_executions_locked', counted)

    def build(state, config):
        views = RunViews([], worktree_service=env['service'].trees, execution_enabled=True,
                         execution_records=lambda: JobExecutionStore(env['repo']).records(env['workspace']['id']))
        views._command_config = {}
        return env['repo'], views
    monkeypatch.setattr(agent_tools, 'build', build)

    def run(*argv):
        calls.clear()
        capsys.readouterr()
        started = time.perf_counter()
        agent_tools.main(list(argv))
        elapsed = time.perf_counter() - started
        return json.loads(capsys.readouterr().out), len(calls), elapsed
    return run


def test_read_commands_discover_executions_once(env, cli):
    tree = grown(env)
    base = ['--task', tree['task_id']]
    run_id = env['executions'].records(env['workspace']['id'])[-1]['execution_id'] + '-2'
    for command in (['node', 'list'], ['iteration', 'show'], ['task'], ['iteration', 'ledger'],
                    ['case', run_id, 'qasper:q']):
        out, scans, elapsed = cli(*base, *command)
        print(command, scans, f'{elapsed:.3f}s', file=sys.__stderr__)
        assert scans <= 1, (command, scans)
    out, _, _ = cli(*base, 'node', 'list')
    assert len(out['nodes']) == 7


PROBE = '''
import contextlib, io, json, sys
from rag_eval.workbench import agent_tools
state, config, task = sys.argv[1:4]
for command in (['task'], ['node', 'list'], ['iteration', 'show'], ['cluster', 'list'], ['runs'], ['docs']):
    with contextlib.redirect_stdout(io.StringIO()):
        try:
            agent_tools.main(['--state', state, '--config', config, '--task', task, *command])
        except SystemExit:
            pass  # this Task backs no iteration: the command's services are built all the same
print(json.dumps(sorted(m for m in sys.modules if m.split('.')[0] in ('fastapi', 'starlette', 'uvicorn')
                        or m == 'rag_eval.workbench.jobs_api')))
'''


def test_read_commands_never_load_the_http_stack(tmp_path):
    from rag_eval.workbench.repository import WorkbenchRepository
    (tmp_path / 'workspace').mkdir()
    config = tmp_path / 'config.json'
    config.write_text(json.dumps(dict(workspace=dict(title='SN', location='workspace'), inputs=[],
                                      task_worktrees=dict(create_candidates=True, project_root='sn', artifact_root='artifacts'))))
    repository = WorkbenchRepository(tmp_path / 'state')
    workspace = repository.register_workspace('SN', tmp_path / 'workspace')
    task = repository.create_task(workspace['id'], 'Read', '', request_key='t')
    result = subprocess.run([sys.executable, '-c', PROBE, str(tmp_path / 'state'), str(config), task['id']],
                            capture_output=True, text=True, cwd=tmp_path, timeout=120,
                            env={**__import__('os').environ, 'PYTHONPATH': str(ROOT / 'src')})
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.strip().splitlines()[-1]) == []


def test_each_read_scope_discovers_again(tmp_path):
    """One discovery per command, never a remembered 'no runs' across commands (or wait polls)."""
    from rag_eval.workbench.run_views import RunViews
    root = tmp_path / 'execution'
    root.mkdir()
    record = dict(execution_id='e1', task_id='t1', run_dir=str(root), state='finished', terminal=True)
    views = RunViews([], execution_records=lambda: [record])
    with views.request_scope():
        assert views.execution_runs('e1') == []
        (root / 'partitions' / 'p').mkdir(parents=True)
        (root / 'batch-plan.json').write_text(json.dumps([dict(run_dir=str(root / 'partitions' / 'p'), case_ids=['c'])]))
        assert views.execution_runs('e1') == []  # the same command keeps its snapshot
    with views.request_scope():
        assert [run.id for run in views.execution_runs('e1')] == ['e1-1']
