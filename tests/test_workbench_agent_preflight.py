"""Read-only checks must use the real creation command, including canonical cwd."""
from pathlib import Path

import pytest

from rag_eval.workbench.creation_bridge import AgentCreationBridgeAdapter
from test_workbench_agent_creations import prepared, setup


def page(entries, cursor=None):
    return dict(result=dict(status='ok', entries=entries,
                pageInfo=dict(hasMore=cursor is not None, nextCursor=cursor, prevCursor=None)),
                release=dict(status='ok'))


def test_workspace_check_follows_pages_and_uses_frozen_cwd(tmp_path, monkeypatch):
    _, task, _, store = setup(tmp_path)
    command = prepared(store, task)['command']
    other = tmp_path / 'other'
    other.mkdir()
    bridge = AgentCreationBridgeAdapter({'url': 'ws://unused'}, command=['unused'])
    calls = []
    def read(request, **kwargs):
        calls.append(request)
        if 'cursor' not in request:
            return page([dict(id='foreign', directory=str(other), status='done')], 'page-two')
        return page([dict(id=command['workspaceId'], directory=str(other), status='attention')])
    monkeypatch.setattr(bridge._transport, 'call', read)
    with pytest.raises(ValueError, match='SCOPE_MISMATCH'):
        bridge.preflight(command)
    assert len(calls) == 2 and all(c['op'] == 'listWorkspaces' for c in calls)
    # A real alias resolving to the same directory is allowed, as in the Node guard.
    alias = tmp_path / 'alias'
    alias.symlink_to(tmp_path, target_is_directory=True)
    monkeypatch.setattr(bridge._transport, 'call', lambda _, **kw: page([
        dict(id=command['workspaceId'], directory=str(alias), status='attention')]))
    bridge.preflight(command)


@pytest.mark.parametrize('reply,code', [
    (page([]), 'WORKSPACE_NOT_FOUND'),
    (dict(result=dict(status='unavailable', code='BRIDGE_TIMEOUT'), release=dict(status='ok')), 'BRIDGE_TIMEOUT'),
    (dict(result=dict(status='ok', entries=[]), release=dict(status='ok')), 'BRIDGE_INVALID_RESPONSE'),
    (dict(result=dict(status='ok', entries=[], pageInfo=dict(hasMore=False)),
          release=dict(status='unavailable', code='CLOSE_FAILED')), 'CLOSE_FAILED'),
])
def test_preflight_refuses_missing_or_unobservable_scope(tmp_path, monkeypatch, reply, code):
    _, task, _, store = setup(tmp_path)
    command = prepared(store, task)['command']
    bridge = AgentCreationBridgeAdapter({'url': 'ws://unused'}, command=['unused'])
    monkeypatch.setattr(bridge._transport, 'call', lambda _, **kw: reply)
    with pytest.raises(ValueError, match=code):
        bridge.preflight(command)


def test_missing_build_is_explicit_and_precedes_bridge_spawn(tmp_path, monkeypatch):
    _, task, _, store = setup(tmp_path)
    command = prepared(store, task)['command']
    bridge = AgentCreationBridgeAdapter({'url': 'ws://unused'})
    bridge._transport._command = ('node', str(tmp_path / 'missing-dist' / 'cli.js'))
    monkeypatch.setattr(bridge._transport, 'call', lambda _, **kw: pytest.fail('spawned missing bridge'))
    with pytest.raises(ValueError, match='BRIDGE_BUILD_MISSING'):
        bridge.preflight(command)


def test_pagination_cycle_is_bounded(tmp_path, monkeypatch):
    _, task, _, store = setup(tmp_path)
    command = prepared(store, task)['command']
    bridge = AgentCreationBridgeAdapter({'url': 'ws://unused'}, command=['unused'])
    monkeypatch.setattr(bridge._transport, 'call', lambda _, **kw: page([], 'same'))
    with pytest.raises(ValueError, match='BRIDGE_INVALID_RESPONSE'):
        bridge.preflight(command)


def test_preflight_bounds_each_read_by_remaining_check_budget(tmp_path, monkeypatch):
    _, task, _, store = setup(tmp_path)
    command = prepared(store, task)['command']
    bridge = AgentCreationBridgeAdapter({'url': 'ws://unused'}, command=['unused'])
    budgets = []
    def read(_, **kwargs):
        budgets.append(kwargs.get('timeout'))
        return page([dict(id=command['workspaceId'], directory=command['expectedCwd'], status='done')])
    monkeypatch.setattr(bridge._transport, 'call', read)
    bridge.preflight(command)
    assert len(budgets) == 1 and budgets[0] is not None and 0 < budgets[0] <= 30
