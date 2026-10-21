"""Agents of an iteration: node-bound or whole-iteration analysts, removal, merge derivation requests."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from rag_eval.workbench.agent_creations import AgentCreationStore
from rag_eval.workbench.sessions import TaskSessionReader
from test_workbench_iterations import BUDGET, approved, env, evaluated_root, finish  # noqa: F401
from test_workbench_roles_hypotheses import CARD


def creations(env):
    return AgentCreationStore(env['repo'], {env['workspace']['id']: {
        'workspaceId': 'wks-eval', 'profiles': {'sol': {'provider': 'codex/model', 'modeId': 'full-access'}}}})


def finished(store, task_id, intent):
    command = intent['command']
    agent = {'id': command['agentId'], 'cwd': command['expectedCwd'], 'title': None, 'status': 'idle',
             'provider': 'codex', 'model': 'model', 'workspaceId': command['workspaceId'], 'capabilities': {}}
    return store._finish(task_id, intent['id'], 'a', {'result': {'status': 'created', 'agentId': agent['id'], 'agent': agent,
                                                                 'ready': True, 'readiness': 'idle'},
                                                      'release': {'status': 'ok'}})


def evaluated_child(env, tree, root):
    service = env['service']
    child = service.derive(root['id'], proposal_id=approved(env, tree, root['id']), actor='alice', request_key='a')
    Path(child['worktree']['path'], 'backend/app/__init__.py').write_text('VALUE = 3\n')
    service.freeze(child['id'], actor='alice')
    finish(env, service.run(child['id'], budget=BUDGET, actor='alice', request_key='a1'))
    return service.candidate(child['id'])


def views(env):
    def run(execution_id):
        return [SimpleNamespace(id=f'{execution_id}-{i}', suite='qasper', mode='chunk', case_ids=['qasper:q'])
                for i in range(1, 8)]
    flips = lambda left, right: dict(metric='answer_f1', left_run=left, right_run=right, admission='admitted', note=None,
                                     rows=[dict(case_id='qasper:q', left=0.25, right=0.5, delta=0.25, flip='better')])
    return SimpleNamespace(_worktree_service=env['service'].trees, execution_runs=run, flips=flips, shared_bundle=lambda run_id: None,
                           run=lambda run_id: dict(official=dict(primary_metric='answer_f1', metrics=dict(answer_f1=0.5))),
                           admission=lambda run_id: dict(status='admitted', reason=None))


def test_iteration_analysts_are_bound_and_need_two_evaluated_candidates(env):
    tree, root = evaluated_root(env)
    store = creations(env)
    # In an iteration every analyst names a candidate or the whole iteration.
    with pytest.raises(ValueError, match='bound to a candidate or to the whole iteration'):
        store.prepare(tree['task_id'], 'sol', actor='a', request_key='u', role='analyst')
    with pytest.raises(ValueError, match='without a candidate'):
        store.prepare(tree['task_id'], 'sol', actor='a', request_key='x', role='analyst', candidate_id=root['id'],
                      scope='iteration')
    store.evaluated_count = lambda task_id: 1
    with pytest.raises(ValueError, match='two evaluated'):
        store.prepare(tree['task_id'], 'sol', actor='a', request_key='i', role='analyst', scope='iteration')
    store.evaluated_count = lambda task_id: 2
    intent = store.prepare(tree['task_id'], 'sol', actor='a', request_key='i2', role='analyst', scope='iteration')
    assert intent['scope'] == 'iteration' and intent['command']['expectedCwd'] == env['workspace']['location']
    session_id = finished(store, tree['task_id'], intent)['session_id']
    session = next(s for s in env['repo'].task(tree['task_id'])['sessions'] if s['id'] == session_id)
    assert session['scope'] == 'iteration' and 'candidate_id' not in session and 'removed_at' not in session


def test_iteration_analyst_brief_and_tree_context_cover_every_candidate(env):
    from rag_eval.workbench.briefs import resolve_brief
    from rag_eval.workbench.context import resolve_context
    tree, root = evaluated_root(env)
    child = evaluated_child(env, tree, root)
    with env['repo'].connect(write=True) as conn:
        conn.execute("INSERT INTO task_sessions(id,task_id,agent_id,role,cwd,scope) VALUES('s1',?,'agent-1','analyst','/eval','iteration')",
                     (tree['task_id'],))
    title, text, refs = resolve_brief(env['repo'], tree['task_id'], 's1', tool='sn-eval', run_views=views(env))
    assert title.endswith('整个迭代') and '本会话分析**整个迭代**' in text and '--scope iteration' in text
    assert '横向比较' in text and '合并派生' in text and '--parent <候选 id>' in text
    assert '**C0**' in text and '**A**' in text and child['id'] in text and '[已评测]' in text
    # A whole-benchmark execution points at its case table instead of listing every partition.
    assert '7 个分区' in text and 'execution-cases' in text and '-7`' not in text
    snapshot = resolve_context(env['repo'], tree['task_id'], {'kind': 'iteration_tree'}, run_views=views(env))
    assert snapshot['title'] == '迭代候选树（最新）' and '**A**' in snapshot['text']


def test_removing_a_session_archives_the_agent_and_keeps_its_record(env):
    tree, _ = evaluated_root(env)
    with env['repo'].connect(write=True) as conn:
        conn.execute("INSERT INTO task_sessions(id,task_id,agent_id,linked_by,linked_at,role,cwd) "
                     "VALUES('s1',?,'agent-1','a','2026-10-21','analyst','/eval')", (tree['task_id'],))

    class Bridge:
        def __init__(self, result):
            self.result, self.commands = result, []

        def call(self, command):
            self.commands.append(command)
            return {'result': self.result, 'release': {'status': 'ok'}}
    bridge = Bridge({'status': 'ok', 'archivedAt': 'now', 'already': False})
    removed = TaskSessionReader(env['repo'], bridge).remove(tree['task_id'], 's1', actor='alice')
    assert bridge.commands == [{'op': 'archiveAgent', 'agentId': 'agent-1'}]
    assert removed['archive'] == {'status': 'archived'} and removed['session']['removed_by'] == 'alice'
    session = env['repo'].task(tree['task_id'])['sessions'][0]
    assert session['removed_at'] == removed['session']['removed_at']
    assert any(h['kind'] == 'session.removed' for h in env['repo'].task(tree['task_id'])['history'])
    # Removing again keeps the first removal; a failed archive is reported, not hidden.
    again = TaskSessionReader(env['repo'], Bridge({'status': 'unavailable', 'code': 'READ_FAILED'})).remove(
        tree['task_id'], 's1', actor='bob')
    assert again['session']['removed_by'] == 'alice' and again['archive'] == {'status': 'unavailable', 'code': 'READ_FAILED'}
    missing = TaskSessionReader(env['repo'], Bridge({'status': 'missing', 'code': 'AGENT_NOT_FOUND'})).remove(
        tree['task_id'], 's1', actor='alice')
    assert missing['archive'] == {'status': 'not_found'}


def test_merge_derivation_requests_name_folded_candidates(env, monkeypatch, tmp_path, capsys):
    from rag_eval.workbench import agent_tools
    from rag_eval.workbench.action_requests import execute, normalize
    tree, root = evaluated_root(env)
    child = evaluated_child(env, tree, root)
    with pytest.raises(ValueError, match='integrated_from'):
        normalize('node-derive', {'integrated_from': [{'candidate_id': child['id'], 'method': 'squash'}]})
    fake = SimpleNamespace(_worktree_service=env['service'].trees,
                           execution_runs=lambda execution_id: [SimpleNamespace(id='run')],
                           admission=lambda run_id: dict(status='admitted', reason=None),
                           flips=views(env).flips)
    monkeypatch.setattr(agent_tools, 'build', lambda state, config: (env['repo'], fake))
    base = ['--task', tree['task_id'], '--scope', 'iteration']
    # An iteration analyst requests derivations only.
    with pytest.raises(SystemExit):
        agent_tools.main(base + ['node', 'run', child['id'], '--reason', 'r'])
    with pytest.raises(SystemExit):
        agent_tools.main(base + ['--node', root['id'], 'node', 'derive', root['id']])
    capsys.readouterr()
    agent_tools.main(base + ['node', 'derive', child['id'], '--integrate', root['id'], '--method', 'rewrite',
                             '--reason', 'fold the root note back in'])
    record = json.loads(capsys.readouterr().out)['request']
    assert record['params'] == {'integrated_from': [{'candidate_id': root['id'], 'method': 'rewrite', 'note': ''}]}
    merged = execute(env['service'], record, workspace_id=env['workspace']['id'], actor='alice')
    assert env['service'].candidate(merged['candidate_id'])['integrated_from'][0]['candidate_id'] == root['id']
