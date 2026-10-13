"""Chat fixture: synthetic Agent creation, sends and replies; no daemon or provider calls.

Each send appends the user body to that Agent's timeline; the synthetic reply
(one tool activity and one assistant message) appears on the second timeline
read after the send, while the Agent reports `running` in between.
"""
import copy
import json
import os
import threading
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn
from rag_eval.workbench.api import create_app
from rag_eval.workbench.repository import WorkbenchRepository

E5 = Path('apps/sn-workbench/test-results/source-execution-20261012/e5-s3-real-run-20261013')


def fragment(value):
    return {'value': value, 'truncated': False, 'originalLength': len(value), 'lengthUnit': 'utf16'}


class Agents:
    def __init__(self):
        self.lock = threading.Lock()
        self.agents = {}   # agent id -> {'cwd', 'provider', 'model', 'title', 'items', 'pending'}
        self.sends = []

    # Creation bridge
    def create(self, command):
        provider, model = command['config']['provider'].split('/', 1)
        with self.lock:
            self.agents[command['agentId']] = {'cwd': command['expectedCwd'], 'provider': provider, 'model': model,
                                               'title': command.get('title'), 'items': [], 'pending': None}
        agent = {'id': command['agentId'], 'cwd': command['expectedCwd'], 'workspaceId': command['workspaceId'],
                 'title': command.get('title'), 'status': 'idle', 'provider': provider, 'model': model,
                 'archivedAt': None, 'activeTurn': None,
                 'capabilities': {key: None for key in (
                     'supportsStreaming', 'supportsSessionPersistence', 'supportsSessionListing',
                     'supportsDynamicModes', 'supportsMcpServers', 'supportsReasoningStream',
                     'supportsToolInvocations', 'supportsRewindConversation', 'supportsRewindFiles',
                     'supportsRewindBoth')}}
        return {'result': {'status': 'created', 'agentId': command['agentId'], 'agent': agent,
                           'ready': True, 'readiness': 'idle'}, 'release': {'status': 'ok'}}

    # Read bridge
    def read(self, command):
        with self.lock:
            agent = self.agents.get(command['agentId'])
            if agent is None:
                return {'result': {'status': 'missing', 'code': 'AGENT_NOT_FOUND'}, 'release': {'status': 'ok'}}
            if command['op'] == 'readAgent':
                return {'result': {'status': 'ok', 'agent': {'id': command['agentId'], 'cwd': agent['cwd'], 'title': agent['title'],
                        'status': 'running' if agent['pending'] else 'idle', 'provider': agent['provider'], 'model': agent['model']}},
                        'release': {'status': 'ok'}}
            assert command['op'] == 'readTimeline' and command['expectedCwd'] == agent['cwd']
            if agent['pending']:
                agent['pending']['reads'] += 1
                if agent['pending']['reads'] >= 2:
                    body = agent['pending']['body']
                    tools = 120 if 'many-activities' in body['message'] else 1
                    for _ in range(tools):
                        agent['items'].append({'type': 'tool_call', 'text': None, 'title': fragment('读取 docs/failure-ledger.md'), 'status': 'completed'})
                    attached = '、'.join(item['title'] for item in body['context']) or '无'
                    agent['items'].append({'type': 'assistant_message', 'text': fragment(f'收到：{body["message"]}（附带材料：{attached}）'), 'title': None, 'status': None})
                    agent['pending'] = None
            entries = []
            limit = command.get('limit', 50)
            first = max(1, len(agent['items']) - limit + 1)
            for seq, item in enumerate(agent['items'][first - 1:], start=first):
                entries.append({'provider': 'fixture', 'timestamp': '2026-10-14T00:00:00Z', 'seqStart': seq, 'seqEnd': seq,
                                'sourceSeqRanges': [{'startSeq': seq, 'endSeq': seq}], 'collapsed': [], 'turnId': 'turn',
                                'item': copy.deepcopy(item)})
            last = len(agent['items'])
            return {'result': {'status': 'ok', 'agentId': command['agentId'], 'direction': 'tail', 'projection': 'projected',
                    'epoch': 'epoch-fixture', 'reset': False, 'staleCursor': False, 'gap': False,
                    'window': {'minSeq': 1, 'maxSeq': last, 'nextSeq': last + 1},
                    'startCursor': {'epoch': 'epoch-fixture', 'seq': first} if last else None,
                    'endCursor': {'epoch': 'epoch-fixture', 'seq': last} if last else None,
                    'hasOlder': first > 1, 'hasNewer': False, 'mergeWindow': None, 'entries': entries}, 'release': {'status': 'ok'}}

    # Send bridge
    def send(self, command):
        body = json.loads(command['text'])
        with self.lock:
            self.sends.append({'agent_id': command['agentId'], 'cwd': command['expectedCwd'], 'body': body,
                               'behavior': command['activeTurnBehavior']})
            agent = self.agents[command['agentId']]
            if agent['cwd'] != command['expectedCwd']:
                return {'result': {'status': 'not_attempted', 'code': 'SCOPE_MISMATCH', 'agentId': command['agentId'],
                                   'messageId': command['messageId']}, 'release': {'status': 'ok'}}
            agent['items'].append({'type': 'user_message', 'text': fragment(command['text']), 'title': None, 'status': None})
            agent['pending'] = {'body': body, 'reads': 0}
        return {'result': {'status': 'acknowledged', 'agentId': command['agentId'], 'messageId': command['messageId']},
                'release': {'status': 'ok'}}


class Bridge:
    def __init__(self, fn):
        self.call = fn


class Worktrees:
    """Actual isolated Git trees, synthetic Paseo registrations; no daemon."""
    def __init__(self, root):
        self.root, self.rows = root, []

    def create(self, project_root, branch, base_revision):
        path = self.root / branch
        subprocess.run(['git', '-C', str(project_root), 'worktree', 'add', '-qb', branch, str(path), base_revision], check=True)
        row = {'workspace_id': 'fixture-' + branch, 'path': str(path)}
        self.rows.append(row)
        return row

    def list(self):
        return self.rows


with TemporaryDirectory(prefix='sn-workbench-chat-browser-') as temporary:
    root = Path(temporary)
    workspace = root / 'workspace'; workspace.mkdir()
    (workspace / 'notes.md').write_text('# 观察\nchunk 模式 16 个检索块仅 1 个相关。\n', encoding='utf-8')
    agents = Agents()
    project = root / 'sn-project'
    (project / 'backend/app').mkdir(parents=True)
    (project / 'backend/app/main.py').write_text('VALUE = 1\n')
    for args in [('init', '-q'), ('add', '.'), ('-c', 'user.name=fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'initial')]:
        subprocess.run(['git', '-C', str(project), *args], check=True)
    # A task with one registered real run (E5 fixture), for the run-summary attachment.
    repository = WorkbenchRepository(root / 'state')
    workspace_row = repository.register_workspace('Chat checks', workspace)
    with_runs = repository.create_task(workspace_row['id'], '有运行的任务', '读 E5 单题结果', request_key='fixture-task-with-runs')
    from rag_eval.workbench.hypotheses import HypothesisStore
    HypothesisStore(repository).draft(with_runs['id'], {
        'title': '重排后再送入生成', 'component': '检索 / 重排', 'mechanism': '16 个未重排块稀释了相关证据',
        'evidence': [{'run_id': 'e5', 'case_id': 'qasper:397a1e851aab41c455c2b284f5e4947500d797f0', 'note': '上下文精度 1/16'}],
        'predictions': [{'case_id': 'qasper:397a1e851aab41c455c2b284f5e4947500d797f0', 'expect': 'not_worse', 'guard': True}],
        'falsification': '目标题召回不变或守护题变差',
        'implementer_brief': '在 chunk 检索后启用重排，只把前 6 块送入生成；用合成文档写单元测试。'}, author='agent:analyst')
    config = {'workspace': {'title': 'Chat checks', 'location': str(workspace)}, 'sources': [],
              'task_worktrees': {'project_root': str(project), 'artifact_root': str(root / 'artifacts'), 'create_from_hypothesis': True},
              'run_views': {'runs': [{'id': 'e5', 'title': 'E5 单题', 'run_dir': str((E5 / 'attempt-1/cli-run').resolve()),
                                      'scores': str((E5 / 'official/scores/scores.json').resolve()), 'task_id': with_runs['id']}]},
              'agent_creation': {'workspaceId': 'fixture-workspace', 'profiles': {
                  'codex-high': {'provider': 'codex/gpt-fixture', 'modeId': 'full-access', 'thinkingOptionId': 'high'},
                  'claude-opus': {'provider': 'claude/claude-fixture', 'thinkingOptionId': 'high'}}}}
    app = create_app(root / 'state', config, actor='alice', agent_bridge=Bridge(agents.read),
                     hypothesis_worktree_creator=Worktrees(root),
                     agent_message_bridge=Bridge(agents.send), agent_creation_bridge=Bridge(agents.create),
                     access_token='a-chat-browser-test', static_directory=Path(os.environ['WORKBENCH_STATIC_DIRECTORY']).resolve())

    @app.get('/api/chat-fixture/stats')
    def stats():
        with agents.lock:
            return {'sends': copy.deepcopy(agents.sends), 'agents': sorted(agents.agents)}

    app.router.routes.insert(-1, app.router.routes.pop())
    uvicorn.run(app, host='127.0.0.1', port=int(os.environ.get('CHAT_FIXTURE_PORT', '18097')), log_level='warning', access_log=False)
