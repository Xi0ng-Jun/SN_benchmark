"""Iteration verbs in sn-eval and action requests that a person confirms before execution."""
import json

import pytest

from rag_eval.workbench import agent_tools
from rag_eval.workbench.action_requests import ActionRequestStore, execute
from rag_eval.workbench.comparisons import Admission
from rag_eval.workbench.repository import Conflict
from test_workbench_comparisons import Views, child, ran
from test_workbench_iterations import env, iteration  # noqa: F401  (fixture)


@pytest.fixture
def t(env, monkeypatch):  # noqa: F811
    views = Views()
    views._worktree_service = env['service'].trees
    env['service'].admission = Admission(views)
    tree = iteration(env)
    monkeypatch.setattr(agent_tools, 'build', lambda state, config: (env['repo'], views))
    return dict(env=env, views=views, tree=tree, root=tree['root_candidate_id'],
                requests=ActionRequestStore(env['repo']))


def sn_eval(t, capsys, *argv):
    agent_tools.main(['--task', t['tree']['task_id'], *argv])
    return json.loads(capsys.readouterr().out)


def test_a_request_records_exact_parameters_and_runs_nothing(t, capsys):
    out = sn_eval(t, capsys, 'node', 'run', t['root'], '--timeout', '900', '--reason', 'baseline needed')
    request = out['request']
    assert request['status'] == 'pending' and request['verb'] == 'node-run' and request['target_id'] == t['root']
    assert request['params'] == dict(timeout_seconds=900) and request['requested_by'] == 'agent:analyst'
    assert 'nothing has run' in out['next']
    assert t['env']['service'].candidate(t['root'])['attempts'] == []
    again = sn_eval(t, capsys, 'node', 'run', t['root'], '--timeout', '900', '--reason', 'asked twice')
    assert again['request']['id'] == request['id']
    assert [r['id'] for r in sn_eval(t, capsys, 'requests', '--status', 'pending')] == [request['id']]
    with pytest.raises(ValueError, match='reason'):
        sn_eval(t, capsys, 'node', 'run', t['root'])


def test_confirming_executes_the_recorded_request_once(t):
    service, store = t['env']['service'], t['requests']
    record = store.request(t['tree']['task_id'], 'node-run', t['root'], dict(timeout_seconds=600),
                           reason='baseline', requested_by='agent:analyst')
    store.claim(record['id'], actor='alice')
    result = execute(service, record, workspace_id=t['env']['workspace']['id'], actor='alice')
    done = store.finish(record['id'], result=result)
    assert done['status'] == 'executed' and done['decided_by'] == 'alice' and done['result']['attempt'] == 1
    assert service.candidate(t['root'])['attempts'][0]['execution_state'] == 'running'
    # The request id is the idempotency key: executing it again does not start a second attempt.
    assert execute(service, record, workspace_id=t['env']['workspace']['id'], actor='alice')['attempt'] == 1
    with pytest.raises(Conflict, match='executed'):
        store.reject(record['id'], actor='alice', note='too late')


def test_rejection_needs_a_note_and_closes_the_request(t):
    store = t['requests']
    record = store.request(t['tree']['task_id'], 'node-freeze', t['root'], {}, reason='done',
                           requested_by='agent:implementer')
    with pytest.raises(ValueError, match='note'):
        store.reject(record['id'], actor='alice', note=' ')
    rejected = store.reject(record['id'], actor='alice', note='tests are missing')
    assert rejected['status'] == 'rejected' and rejected['decision_note'] == 'tests are missing'
    with pytest.raises(Conflict, match='rejected'):
        store.claim(record['id'], actor='alice')


def test_parameters_are_closed(t):
    store, task = t['requests'], t['tree']['task_id']
    with pytest.raises(ValueError, match='Unexpected'):
        store.request(task, 'node-freeze', t['root'], dict(force=True), reason='r', requested_by='a')
    with pytest.raises(ValueError, match='timeout_seconds'):
        store.request(task, 'node-run', t['root'], dict(timeout_seconds=5), reason='r', requested_by='a')
    with pytest.raises(ValueError, match='iteration-create needs'):
        store.request(task, 'iteration-create', None, dict(title='x'), reason='r', requested_by='a')
    with pytest.raises(ValueError, match='needs target'):
        store.request(task, 'node-run', None, {}, reason='r', requested_by='a')


def test_an_implementer_may_only_ask_to_freeze_or_run_its_own_candidate(t, capsys):
    ran(t, t['root'], {'a': .5, 'b': .5}, 'base')
    a = child(t, t['root'], 'a', {'a': .6, 'b': .6})
    implementer = ['--role', 'implementer', '--candidate', a]
    assert sn_eval(t, capsys, *implementer, 'node', 'run', a, '--reason', 'done')['request']['target_id'] == a
    with pytest.raises(SystemExit):
        sn_eval(t, capsys, *implementer, 'node', 'run', t['root'], '--reason', 'not mine')
    with pytest.raises(SystemExit):
        sn_eval(t, capsys, *implementer, 'node', 'adopt', a, '--reason', 'not an implementer verb')
    with pytest.raises(SystemExit):
        sn_eval(t, capsys, *implementer, 'node', 'list')


def test_read_verbs_show_the_tree_comparisons_and_rank_by_root(t, capsys):
    ran(t, t['root'], {'a': .5, 'b': .5}, 'base')
    a = child(t, t['root'], 'a', {'a': .6, 'b': .6})
    b = child(t, a, 'b', {'a': .9, 'b': .9})
    c = child(t, t['root'], 'c', {'a': .4, 'b': .5})
    tree = sn_eval(t, capsys, 'node', 'list')
    assert [n['id'] for n in tree['nodes']] == [t['root'], a, b, c] and tree['order'] == 'tree'
    assert tree['nodes'][1]['root_delta']['status'] == 'same_as_parent'
    ranked = sn_eval(t, capsys, 'node', 'list', '--rank', 'root')
    assert [n['id'] for n in ranked['nodes']] == [b, a, t['root'], c]
    assert ranked['nodes'][0]['rank_delta'] == pytest.approx(.4)
    parent = sn_eval(t, capsys, 'compare', b, a)
    assert parent['kind'] == 'parent' and 'id' in parent
    root = sn_eval(t, capsys, 'compare', a, t['root'])
    assert root['kind'] == 'parent' and root['reference']['candidate_id'] == t['root']
    pinned = sn_eval(t, capsys, 'compare', c, b)
    assert pinned['kind'] == 'pinned' and pinned['stored'] is False
    shown = sn_eval(t, capsys, 'node', 'show', b)
    assert shown['candidate']['reference']['attempt'] == 1 and shown['comparisons']['root']['kind'] == 'root'


def test_proposals_are_drafted_with_proposal_draft(t, capsys, tmp_path):
    from test_workbench_roles_hypotheses import CARD
    ran(t, t['root'], {'a': .5, 'b': .5}, 'base')
    card = tmp_path / 'card.json'
    card.write_text(json.dumps(CARD))
    t['views'].task_runs_context = lambda task_id: ('', json.dumps({'runs': []}))
    t['views']._binding_read = lambda task_id: None
    out = sn_eval(t, capsys, 'proposal', 'draft', '--parent', t['root'], '--file', str(card))
    assert out['saved'] and out['card']['parent_candidate_id'] == t['root']
    with pytest.raises(SystemExit):
        sn_eval(t, capsys, 'hypothesis-draft', '--parent', t['root'], '--file', str(card))
