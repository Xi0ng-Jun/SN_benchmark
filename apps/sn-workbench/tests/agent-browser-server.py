"""Temporary browser state with reads restricted to one explicitly supplied Agent.

No create/send operations, credentials, model calls or preview state. Use an
already accessible local daemon; failed authentication is not bypassed.
"""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn
from rag_eval.workbench.api import create_app
from rag_eval.workbench.bridge_adapter import AgentBridgeAdapter


class ExistingAgentReads:
    def __init__(self, agent_id):
        self.agent_id = agent_id
        self.calls = {'readAgent': 0, 'readTimeline': 0}
        self.bridge = AgentBridgeAdapter({'url': os.environ.get('WORKBENCH_DAEMON_URL', 'ws://127.0.0.1:6767/ws'),
                                          'connectTimeoutMs': 3000}, timeout=10)

    def call(self, command):
        if command.get('op') not in self.calls or command.get('agentId') != self.agent_id:
            raise ValueError('Only the specified existing Agent may be read')
        self.calls[command['op']] += 1
        return self.bridge.call(command)


bridge = ExistingAgentReads(os.environ['WORKBENCH_EXISTING_AGENT_ID'])
with TemporaryDirectory(prefix='sn-workbench-existing-agent-browser-') as state:
    app = create_app(state, {'workspace': {'title': 'Temporary existing-Agent checks', 'location': str(Path.cwd())},
                    'sources': []}, actor='integration-check', agent_bridge=bridge,
                    access_token='a-existing-agent-browser-test', static_directory=Path('apps/sn-workbench/dist').resolve())
    uvicorn.run(app, host='127.0.0.1', port=18094, log_level='warning', access_log=False)
    print(json.dumps({'read_calls': bridge.calls}), flush=True)
