"""Independent HTTP/SQLite message fixture; no daemon or provider calls.

All external reads and sends are synthetic. Count sends separately from HTTP
requests so lost browser responses and explicit receipt GETs can be checked.
"""
import copy
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn
from fastapi import Request
from rag_eval.workbench.api import create_app


class Reads:
    def __init__(self, cwd):
        self.cwd = str(cwd)
        self.page = json.loads(Path('apps/sn-agent-bridge/examples/read-timeline.response.json').read_text())

    def call(self, command):
        if command['op'] == 'readAgent':
            return {'result': {'status': 'ok', 'agent': {'id': command['agentId'], 'cwd': self.cwd,
                    'title': 'Synthetic message Agent', 'status': 'idle', 'provider': 'fixture', 'model': 'fixture'}},
                    'release': {'status': 'ok'}}
        assert command['op'] == 'readTimeline' and command['expectedCwd'] == self.cwd
        result = copy.deepcopy(self.page)
        result['result']['agentId'] = command['agentId']
        return result


class Sends:
    def __init__(self):
        self.commands = []; self.enabled = True

    @property
    def call(self):
        return self._call if self.enabled else None

    def _call(self, command):
        assert set(command) == {'op', 'agentId', 'expectedCwd', 'text', 'messageId', 'activeTurnBehavior'}
        assert command['op'] == 'sendAgent'
        self.commands.append(copy.deepcopy(command))
        body = json.loads(command['text'])
        result = {'status': 'acknowledged', 'agentId': command['agentId'], 'messageId': command['messageId']}
        if body['message'] == 'unknown-fixture':
            result.update(status='unknown', code='SEND_OUTCOME_UNKNOWN', cause='CONNECTION_UNAVAILABLE')
        release = {'status': 'unavailable', 'code': 'CLOSE_FAILED'} if body['message'] == 'release-failed' else {'status': 'ok'}
        return {'result': result, 'release': release}


with TemporaryDirectory(prefix='sn-workbench-messages-browser-') as temporary:
    root = Path(temporary)
    workspace = root / 'workspace'; workspace.mkdir()
    (workspace / 'context.txt').write_text('  Explicit selected context\n', encoding='utf-8')
    sends = Sends()
    app = create_app(root / 'state', {'workspace': {'title': 'Independent message checks', 'location': str(workspace)},
                    'sources': []}, actor='alice', agent_bridge=Reads(workspace), agent_message_bridge=sends,
                    access_token='a-messages-browser-test', static_directory=Path(os.environ['WORKBENCH_STATIC_DIRECTORY']).resolve())

    @app.get('/api/message-fixture/stats')
    def stats():
        return {'send_count': len(sends.commands), 'sent': [
            {'body': json.loads(command['text']), 'agent_id': command['agentId'],
             'message_id': command['messageId'], 'behavior': command['activeTurnBehavior']}
            for command in sends.commands]}

    # Place fixture-only endpoint before the catch-all static mount.
    app.router.routes.insert(-1, app.router.routes.pop())

    @app.post('/api/message-fixture/mutate-context')
    def mutate_context():
        (workspace / 'context.txt').write_text('Changed selected context\n', encoding='utf-8')
        return {'changed': True}

    app.router.routes.insert(-1, app.router.routes.pop())

    @app.post('/api/message-fixture/delete-context')
    def delete_context():
        (workspace / 'context.txt').unlink()
        return {'removed': True}

    app.router.routes.insert(-1, app.router.routes.pop())

    @app.post('/api/message-fixture/configure-send')
    async def configure_send(request: Request):
        sends.enabled = (await request.json())['enabled']
        return {'enabled': sends.enabled}

    app.router.routes.insert(-1, app.router.routes.pop())

    uvicorn.run(app, host='127.0.0.1', port=18096, log_level='warning', access_log=False)
