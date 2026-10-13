"""Temporary SQLite/HTTP creation checks with no daemon or provider calls."""
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn
from starlette.responses import JSONResponse
from rag_eval.workbench.api import create_app


class CreationFixture:
    def call(self, command):
        assert command['op'] == 'createAgent'
        assert set(command) <= {'op', 'workspaceId', 'expectedCwd', 'agentId',
                                'idempotencyKey', 'config', 'title'}
        title = command.get('title', 'Remote default title')
        if title == 'not-attempted':
            return {'result': {'status': 'not_attempted', 'code': 'AGENT_ALREADY_EXISTS',
                               'agentId': command['agentId']}, 'release': {'status': 'ok'}}
        if title == 'unknown':
            return {'result': {'status': 'unknown', 'code': 'CREATE_OUTCOME_UNKNOWN',
                               'cause': 'CONNECTION_UNAVAILABLE', 'agentId': command['agentId']},
                    'release': {'status': 'ok'}}
        ready = title not in ('not-ready', 'incomplete')
        provider, model = command['config']['provider'].split('/', 1)
        agent = {'id': command['agentId'], 'cwd': command['expectedCwd'],
                 'workspaceId': command['workspaceId'], 'title': title,
                 'status': 'idle' if ready else 'running', 'provider': provider,
                 'model': model, 'archivedAt': None, 'activeTurn': None,
                 'capabilities': {key: None for key in (
                     'supportsStreaming', 'supportsSessionPersistence', 'supportsSessionListing',
                     'supportsDynamicModes', 'supportsMcpServers', 'supportsReasoningStream',
                     'supportsToolInvocations', 'supportsRewindConversation', 'supportsRewindFiles',
                     'supportsRewindBoth')}}
        if title == 'incomplete':
            for key in ('status', 'archivedAt', 'activeTurn'):
                del agent[key]
        return {'result': {'status': 'created', 'agentId': command['agentId'], 'agent': agent,
                           'ready': ready, 'readiness': 'incomplete_snapshot' if title == 'incomplete'
                           else 'idle' if ready else 'not_idle'},
                'release': {'status': 'unavailable', 'code': 'CLOSE_FAILED'}
                if title == 'not-ready' else {'status': 'unavailable', 'code': 'BRIDGE_RELEASE_UNKNOWN'}
                if title == 'release-unknown' else {'status': 'ok'}}


with TemporaryDirectory(prefix='sn-workbench-creation-browser-') as state:
    base_config = {'workspace': {'title': 'Independent creation checks',
                                'location': str(Path.cwd().resolve())}, 'sources': []}
    config = {**base_config, 'agent_creation': {
                                'workspaceId': 'fixture-workspace', 'profiles': {
                                    'allowed': {'provider': 'fixture/model-example'},
                                    'second': {'provider': 'fixture/second-model'}}}}
    active = [create_app(state, config,
                     actor='alice', access_token='a-creation-browser-test',
                     agent_creation_bridge=CreationFixture(),
                     static_directory=Path('apps/sn-workbench/dist').resolve())]

    async def app(scope, receive, send):
        # Owned fixture control only: same temporary DB, current production GET
        # projection, but a fresh app with no creation config/allowed profiles.
        if scope['type'] == 'http' and scope['path'] == '/__fixture/remove-creation-config' and scope['method'] == 'POST':
            active[0] = create_app(state, base_config, actor='alice',
                                   access_token='a-creation-browser-test',
                                   static_directory=Path('apps/sn-workbench/dist').resolve())
            await JSONResponse({'removed': True})(scope, receive, send)
        else:
            await active[0](scope, receive, send)

    uvicorn.run(app, host='127.0.0.1', port=18095, log_level='warning', access_log=False)
