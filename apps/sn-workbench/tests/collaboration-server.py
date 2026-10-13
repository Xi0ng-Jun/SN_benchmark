"""Dedicated browser state; the remote bridge is a synthetic read-only fixture.

Run from the repository root with PYTHONPATH=src. No daemon connection occurs.
SQLite, HTTP scope checks, source rereads, candidate storage and human revisions
are real. TemporaryDirectory removes the state after normal server shutdown.
"""
import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn
from rag_eval.workbench.api import create_app


class FixtureBridge:
    def __init__(self, cwd):
        self.cwd = str(cwd)
        self.page = json.loads(Path('apps/sn-agent-bridge/examples/read-timeline.response.json').read_text())
        self.page['result']['entries'][1]['item']['text']['value'] = '  模型原文\n独立保存  '

    def call(self, command):
        if command['op'] == 'readAgent':
            return {'result': {'status': 'ok', 'agent': {'id': command['agentId'], 'cwd': self.cwd,
                    'title': 'Synthetic browser agent', 'status': 'idle', 'provider': 'fixture', 'model': 'fixture'}},
                    'release': {'status': 'ok'}}
        assert command['op'] == 'readTimeline'
        assert command['expectedCwd'] == self.cwd
        assert command['projection'] == 'projected' and command['limit'] == 50
        result = copy.deepcopy(self.page)
        result['result']['agentId'] = command['agentId']
        return result


with TemporaryDirectory(prefix='sn-workbench-collaboration-browser-') as state:
    cwd = Path.cwd().resolve()
    app = create_app(state, {'workspace': {'title': 'Independent collaboration checks', 'location': str(cwd)},
                    'sources': []}, actor='alice', agent_bridge=FixtureBridge(cwd),
                    access_token='a-collaboration-browser-test', static_directory=Path('apps/sn-workbench/dist').resolve())
    uvicorn.run(app, host='127.0.0.1', port=18093, log_level='warning', access_log=False)
