"""Agent-CLI execution requests: the server dispatches and settles them; missing credentials are infrastructure."""
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace


from rag_eval.workbench import agent_tools, env_guard
from rag_eval.workbench.action_requests import ActionRequestStore, auto_execute
from rag_eval.workbench.comparisons import ComparisonService
from rag_eval.workbench.job_execution import JobExecutionStore
from rag_eval.workbench.repository import WorkbenchRepository
from test_workbench_clusters import frozen_cluster, env  # noqa: F401
from test_workbench_cluster_runs import set_budget
from test_workbench_iterations import iteration
from test_workbench_job_execution import fixture, wait_terminal

KEY = 'SYSTEM_SN_DISPATCH_TEST_KEY'


def declare_credential(service, monkeypatch):
    """The model config names a credential; this process (an agent CLI) does not have it."""
    path = Path(service.execution_config['model_config'])
    path.write_text(path.read_text().replace('[services.chat]\n', f'[services.chat]\napi_key_env = "{KEY}"\n'))
    monkeypatch.delenv(KEY, raising=False)
    monkeypatch.setattr(env_guard, '_boot', None)


def frozen_child(f):
    set_budget(f)
    service, candidate = f['service'], f['candidate']['id']
    (Path(f['candidate']['worktree']['path']) / 'backend/app/__init__.py').write_text('VALUE = 3\n')
    service.freeze(candidate, actor='test')
    return service, candidate


def scoped(service, cid):
    return [a for a in service.candidate(cid)['attempts'] if a['batch_id'] == 'cluster:c01']


def request(f, candidate, reason='check mechanism'):
    return ActionRequestStore(f['env']['repo']).request(f['tree']['task_id'], 'node-cluster-run', candidate,
                                                        {'cluster_id': 'c01'}, reason=reason,
                                                        requested_by='agent:implementer')


def progress(f, candidate):
    out = ComparisonService(f['env']['repo'], f['service'], f['views']).for_cluster(
        candidate, 'c01', actor='agent:implementer', implementer=True)
    assert 'qasper:' not in json.dumps(out)
    return out


def test_cli_hands_its_run_to_the_server_which_dispatches_with_server_credentials(frozen_cluster, monkeypatch, capsys):
    from rag_eval.workbench.action_requests import HANDOFF_NOTE
    from rag_eval.workbench.server_dispatch import ServerDispatch, serving
    f = frozen_cluster
    service, candidate = frozen_child(f)
    declare_credential(service, monkeypatch)
    seen = []
    monkeypatch.setattr(f['env']['executions'], '_start', lambda record: seen.append(env_guard.child_env()[0].get(KEY)))
    dispatcher = ServerDispatch(service, f['env']['executions'], f['env']['workspace']['id'])
    assert not serving(f['env']['repo'].root)
    assert dispatcher.hold() and serving(f['env']['repo'].root)
    # The agent's CLI, without credentials, while the server owns the state root.
    monkeypatch.setattr(agent_tools, 'build', lambda state, config: (f['env']['repo'], f['views']))
    monkeypatch.setattr(agent_tools, 'iteration_service', lambda *a, **k: service)
    agent_tools.main(['--task', f['tree']['task_id'], '--role', 'implementer', '--candidate', candidate,
                      'node', 'cluster-run', candidate, 'c01', '--reason', 'check mechanism'])
    out = json.loads(capsys.readouterr().out)
    assert out['request']['status'] == 'confirmed' and out['request']['decision_note'] == HANDOFF_NOTE
    assert 'server dispatches' in out['next']
    assert not scoped(service, candidate) and not seen
    assert progress(f, candidate)['state'] == 'pending'
    # The server process has the credentials; it launches what the CLI confirmed.
    monkeypatch.setenv(KEY, 'server-value')
    dispatcher.dispatch()
    dispatcher.join(60)
    assert ActionRequestStore(f['env']['repo']).get(out['request']['id'])['status'] == 'executed'
    assert seen == ['server-value'] * 4
    assert len(scoped(service, candidate)) == 2 and len(scoped(service, f['tree']['root_candidate_id'])) == 2
    dispatcher.close()
    assert not serving(f['env']['repo'].root)


def test_dispatch_without_credentials_is_refused_as_infrastructure_before_any_reservation(frozen_cluster, monkeypatch):
    from rag_eval.workbench.budgets import BudgetLedger
    f = frozen_cluster
    service, candidate = frozen_child(f)
    declare_credential(service, monkeypatch)
    before = BudgetLedger(f['env']['repo']).summary(f['tree']['id'])
    refused = auto_execute(service, request(f, candidate), workspace_id=f['env']['workspace']['id'])
    assert refused['status'] == 'pending' and KEY in refused['result']['infrastructure_error']
    assert not scoped(service, candidate) and not scoped(service, f['tree']['root_candidate_id'])
    assert BudgetLedger(f['env']['repo']).summary(f['tree']['id']) == before
    shown = progress(f, candidate)
    assert shown['state'] == 'infrastructure_error' and KEY not in json.dumps(shown)
    monkeypatch.setenv(KEY, 'present')
    again = auto_execute(service, request(f, candidate), workspace_id=f['env']['workspace']['id'])
    assert again['id'] == refused['id'] and again['status'] == 'executed'
    assert len(scoped(service, candidate)) == 2


def test_round_measurement_waits_on_missing_credentials_without_attempt_or_infra_retry(env, monkeypatch):
    from test_workbench_loop_measurements import finish, ports
    tree = iteration(env)
    root = tree['root_candidate_id']
    declare_credential(env['service'], monkeypatch)
    measurements = ports(env)
    result = measurements.measure(root, key='credentials', actor='alice', infra_retry=1)
    assert result['status'] == 'running' and result['errors'][0]['type'] == 'CredentialsUnavailable'
    candidate = env['service'].candidate(root)
    assert candidate['attempts'] == [] and candidate['state'] == 'frozen'
    monkeypatch.setenv(KEY, 'present')
    assert measurements.measure(root, key='credentials', actor='alice', infra_retry=1)['status'] == 'running'
    assert len(env['service'].candidate(root)['attempts']) == 2
    # These synthetic executions publish no runs, so each counts as an infrastructure failure:
    # the refused dispatch left the single infra retry unused.
    finish(env, env['service'].candidate(root))
    assert measurements.measure(root, key='credentials', actor='alice', infra_retry=1)['reason'] == 'Infrastructure retry planned'
    assert measurements.measure(root, key='credentials', actor='alice', infra_retry=1)['status'] == 'running'
    assert len(env['service'].candidate(root)['attempts']) == 4
    finish(env, env['service'].candidate(root))
    assert measurements.measure(root, key='credentials', actor='alice', infra_retry=1)['status'] == 'complete'


def test_server_settles_an_execution_whose_dispatching_cli_exited(tmp_path):
    from rag_eval.workbench.server_dispatch import ServerDispatch
    repo, task, job = fixture(tmp_path)
    run = tmp_path / 'run'
    code = (f'import time; from pathlib import Path; time.sleep(1); p=Path({str(run)!r}); p.mkdir(); '
            '(p / "state.json").write_text("{\\"phase\\": \\"finished\\", \\"terminal\\": true}")')
    spec = dict(argv=[sys.executable, '-c', code], run_dir=str(run), budget=dict(timeout_seconds=30),
                preparation_id='p' * 64)
    cli = ('import json, os, sys\n'
           'from rag_eval.workbench.repository import WorkbenchRepository\n'
           'from rag_eval.workbench.job_execution import JobExecutionStore\n'
           'store = JobExecutionStore(WorkbenchRepository(sys.argv[1]))\n'
           'record = store.run(sys.argv[2], sys.argv[3], 1, execution_spec=json.loads(sys.argv[4]), '
           'actor="agent", request_key="cli")\n'
           'print(record["execution_id"], flush=True)\n'
           'os._exit(0)\n')
    source = str(Path(__file__).resolve().parents[1] / 'src')
    done = subprocess.run([sys.executable, '-c', cli, str(repo.root), task['id'], job['job_id'], json.dumps(spec)],
                          capture_output=True, text=True, timeout=60, env=dict(os.environ, PYTHONPATH=source))
    execution_id = done.stdout.strip()
    server = JobExecutionStore(WorkbenchRepository(repo.root))
    assert server.get(task['id'], execution_id)['state'] == 'running'
    dispatcher = ServerDispatch(SimpleNamespace(repository=server.repository), server, 'workspace',
                                dispatching=False, poll_seconds=.05, reconcile_seconds=.05)
    dispatcher.start()
    try:
        finished = wait_terminal(server, task['id'], execution_id)
    finally:
        dispatcher.close()
    assert finished['state'] == 'finished'


def test_redispatch_after_settlement_creates_k_new_attempts_and_result_explains_state(frozen_cluster):
    f = frozen_cluster
    service, candidate = frozen_child(f)
    assert auto_execute(service, request(f, candidate), workspace_id=f['env']['workspace']['id'])['status'] == 'executed'
    attempts = scoped(service, candidate) + scoped(service, f['tree']['root_candidate_id'])
    assert len(attempts) == 4
    shown = progress(f, candidate)
    assert shown['status'] != 'admitted' and shown['state'] == 'pending'
    # The dispatching CLI exits: its supervision is gone, nothing published a launch identity.
    for lock in f['env']['executions']._supervisors.values():
        lock.close()
    f['env']['executions']._supervisors.clear()
    server = JobExecutionStore(WorkbenchRepository(f['env']['repo'].root))
    server.reconcile()
    for a in attempts:
        assert wait_terminal(server, a['task_id'], a['execution_id'])['error_type'] == 'execution_lost'
    assert progress(f, candidate)['state'] == 'infrastructure_error'
    assert auto_execute(service, request(f, candidate, 'again'), workspace_id=f['env']['workspace']['id'])['status'] == 'executed'
    assert len(scoped(service, candidate)) == 4 and len(scoped(service, f['tree']['root_candidate_id'])) == 4
    for a in scoped(service, candidate)[2:] + scoped(service, f['tree']['root_candidate_id'])[2:]:
        f['env']['executions']._finish(a['execution_id'], state='failed', exit_code=1, error_type='child_exit',
                                       run_state=dict(phase='failed'))
    shown = progress(f, candidate)
    assert shown['status'] != 'admitted' and shown['state'] == 'failed' and shown['reason'] == 'execution failed'
