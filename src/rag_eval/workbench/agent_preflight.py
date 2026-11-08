"""Read-only deployment checks over the same transport and cwd as creation.

This is a snapshot, not a model health claim or a replacement for createAgent's
final scope check. Python and the owned Node subprocess run on the same host.
"""
from pathlib import Path
import re
import time


class AgentPreflightError(ValueError):
    def __init__(self, code):
        self.code = code if isinstance(code, str) and re.fullmatch(r'[A-Z][A-Z0-9_]{0,63}', code) else 'BRIDGE_INVALID_RESPONSE'
        hints = {
            'SCOPE_MISMATCH': '任务目录与远端工作区目录不一致，请修正工作区绑定',
            'BRIDGE_BUILD_MISSING': '缺少 Agent bridge 构建产物，请在 apps/sn-agent-bridge 构建后重试',
            'WORKSPACE_NOT_FOUND': '未找到配置中的远端工作区',
            'WORKSPACE_UNAVAILABLE': '工作区目录或状态不可用',
        }
        super().__init__(f"Agent 启动预检失败：{self.code}；" + hints.get(self.code, '请检查 bridge 部署和连接'))


def check_scope(transport, command):
    if transport._default_command and not Path(transport._command[-1]).is_file():
        raise AgentPreflightError('BRIDGE_BUILD_MISSING')
    try:
        expected = Path(command['expectedCwd']).resolve(strict=True)
        if not expected.is_dir():
            raise OSError()
    except (OSError, RuntimeError):
        raise AgentPreflightError('WORKSPACE_UNAVAILABLE') from None
    cursor, seen, started = None, set(), time.monotonic()
    for _ in range(20):
        remaining = 30 - (time.monotonic() - started)
        if remaining <= 0:
            raise AgentPreflightError('BRIDGE_TIMEOUT')
        response = transport.call(dict(op='listWorkspaces', limit=100, **({'cursor': cursor} if cursor else {})),
                                  timeout=remaining)
        result, release = response.get('result', {}), response.get('release', {})
        if result.get('status') != 'ok' or release.get('status') != 'ok':
            raise AgentPreflightError(result.get('code') or release.get('code'))
        entries, page = result.get('entries'), result.get('pageInfo')
        if (not isinstance(entries, list) or any(not isinstance(e, dict) for e in entries)
                or not isinstance(page, dict) or type(page.get('hasMore')) is not bool):
            raise AgentPreflightError('BRIDGE_INVALID_RESPONSE')
        matches = [e for e in entries if e.get('id') == command['workspaceId']]
        if len(matches) > 1:
            raise AgentPreflightError('BRIDGE_INVALID_RESPONSE')
        if matches:
            entry = matches[0]
            if entry.get('status') not in ('needs_input', 'failed', 'running', 'attention', 'done'):
                raise AgentPreflightError('WORKSPACE_UNAVAILABLE')
            try:
                directory = entry.get('directory')
                if not isinstance(directory, str) or not Path(directory).is_absolute():
                    raise OSError()
                actual = Path(directory).resolve(strict=True)
                if not actual.is_dir():
                    raise OSError()
            except (OSError, RuntimeError, ValueError):
                raise AgentPreflightError('WORKSPACE_UNAVAILABLE') from None
            if actual != expected:
                raise AgentPreflightError('SCOPE_MISMATCH')
            return
        if not page['hasMore']:
            raise AgentPreflightError('WORKSPACE_NOT_FOUND')
        cursor = page.get('nextCursor')
        if not isinstance(cursor, str) or not cursor or cursor in seen:
            raise AgentPreflightError('BRIDGE_INVALID_RESPONSE')
        seen.add(cursor)
    raise AgentPreflightError('BRIDGE_RESPONSE_LIMIT')
