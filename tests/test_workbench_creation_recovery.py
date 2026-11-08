"""A known pre-dispatch refusal can have one audited successor, never a replay."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from rag_eval.workbench.repository import Conflict
from test_workbench_agent_creations import Bridge, created, prepared, setup


def refuse(store, task, code='SCOPE_MISMATCH'):
    intent = prepared(store, task)
    return store.attempt(task['id'], intent['id'], actor='alice', bridge=Bridge(lambda c: {
        'result': {'status': 'not_attempted', 'code': code, 'agentId': c['agentId']},
        'release': {'status': 'ok'}}))


def test_recovery_keeps_old_receipt_and_uses_corrected_binding_once(tmp_path):
    repo, task, bindings, store = setup(tmp_path)
    old = refuse(store, task)
    from rag_eval.workbench.agent_creations import AgentCreationStore
    bindings[task['workspace_id']]['workspaceId'] = 'corrected-workspace'
    fixed = AgentCreationStore(repo, bindings)
    with ThreadPoolExecutor(max_workers=3) as workers:
        retries = list(workers.map(lambda _: fixed.retry_not_attempted(task['id'], old['id'], actor='alice'), range(3)))
    new = retries[0]
    assert all(r == new for r in retries)
    assert new['status'] == 'prepared' and new['agent_id'] != old['agent_id']
    assert new['command']['workspaceId'] == 'corrected-workspace'
    assert fixed.get(task['id'], old['id'], actor='alice') == old
    bridge = Bridge(created)
    done = fixed.attempt(task['id'], new['id'], actor='alice', bridge=bridge)
    assert fixed.find_request(task['id'], old['request_key'], actor='alice') == done
    assert fixed.retry_not_attempted(task['id'], old['id'], actor='alice') == done
    fixed.attempt(task['id'], done['id'], actor='alice', bridge=bridge)
    assert len(bridge.commands) == 1
    assert len(fixed.list(task['id'], actor='alice')) == 2
    assert sum(e['kind'] == 'agent.creation.retry_prepared' for e in repo.task(task['id'])['history']) == 1


@pytest.mark.parametrize('status', ['prepared', 'unknown', 'created', 'exists'])
def test_recovery_refuses_possible_remote_effects(tmp_path, status):
    repo, task, _, store = setup(tmp_path)
    intent = prepared(store, task)
    if status != 'prepared':
        def response(c):
            if status == 'created':
                return created(c)
            result = (dict(status='unknown', code='CREATE_OUTCOME_UNKNOWN', cause='BRIDGE_PROCESS_FAILED')
                      if status == 'unknown' else dict(status='not_attempted', code='AGENT_ALREADY_EXISTS'))
            return dict(result=dict(result, agentId=c['agentId']), release=dict(status='ok'))
        store.attempt(task['id'], intent['id'], actor='alice', bridge=Bridge(response))
    with pytest.raises(Conflict):
        store.retry_not_attempted(task['id'], intent['id'], actor='alice')
    assert len(store.list(task['id'], actor='alice')) == 1


def test_recovery_checks_owner_and_current_authorization(tmp_path):
    repo, task, bindings, store = setup(tmp_path)
    old = refuse(store, task)
    with pytest.raises(ValueError):
        store.retry_not_attempted(task['id'], old['id'], actor='bob')
    from rag_eval.workbench.agent_creations import AgentCreationStore
    bindings[task['workspace_id']]['profiles'].clear()
    fixed = AgentCreationStore(repo, bindings)
    with pytest.raises(ValueError):
        fixed.retry_not_attempted(task['id'], old['id'], actor='alice')
    assert fixed.get(task['id'], old['id'], actor='alice') == old
    assert len(fixed.list(task['id'], actor='alice')) == 1
