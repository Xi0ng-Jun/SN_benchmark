"""Analyst/implementer roles, role briefs, hypothesis cards and the leak scan."""
import json

import pytest

from test_task_worktree import setup  # noqa: F401  (fixture)

AGENT = '38847573-1b4c-45f4-9568-469f37948f5d'
CARD = {
    'title': 'Rerank before synthesis', 'component': 'retrieval', 'mechanism': 'too many unranked chunks dilute evidence',
    'evidence': [{'run_id': 'r1', 'case_id': 'qasper:abc', 'note': 'context precision 0/16'}],
    'predictions': [{'case_id': 'qasper:abc', 'expect': 'improve'},
                    {'case_id': 'qasper:def', 'expect': 'not_worse', 'guard': True}],
    'falsification': 'target recall unchanged or guard worse',
    'implementer_brief': 'Enable the reranker after chunk retrieval and pass six chunks to synthesis.',
    'change_scope': ['backend/app/main.py'],
}


def created(store, task, role, hypothesis_id=None, key='create'):
    intent = store.prepare(task, 'sol', actor='a', request_key=key, role=role, hypothesis_id=hypothesis_id)
    agent = {'id': intent['command']['agentId'], 'cwd': intent['command']['expectedCwd'], 'title': None,
             'status': 'idle', 'provider': 'codex', 'model': 'model', 'workspaceId': intent['command']['workspaceId'],
             'capabilities': {}}
    return store._finish(task, intent['id'], 'a', {'result': {'status': 'created', 'agentId': agent['id'], 'agent': agent,
                                                              'ready': True, 'readiness': 'idle'}, 'release': {'status': 'ok'}})


@pytest.fixture
def roles(setup):  # noqa: F811
    from rag_eval.workbench.agent_creations import AgentCreationStore
    from rag_eval.workbench.hypotheses import HypothesisStore
    project, tree, repo, task, store, service = setup
    service.bind(task, tree, paseo_workspace_id='wks-task')
    workspace = repo.task(task)['workspace_id']
    creations = AgentCreationStore(repo, {workspace: {'workspaceId': 'wks-eval', 'profiles': {
        'sol': {'provider': 'codex/model', 'modeId': 'full-access'}}}})
    return repo, task, tree, workspace, creations, HypothesisStore(repo)


def test_roles_choose_cwd_and_implementer_needs_an_approved_card(roles):
    repo, task, tree, workspace, creations, cards = roles
    analyst = created(creations, task, 'analyst')
    location = next(w['location'] for w in repo.workspaces() if w['id'] == workspace)
    assert analyst['command']['expectedCwd'] == location and analyst['command']['workspaceId'] == 'wks-eval'
    card = cards.draft(task, CARD, author='agent:analyst')
    with pytest.raises(ValueError, match='not approved'):
        creations.prepare(task, 'sol', actor='a', request_key='early', role='implementer', hypothesis_id=card['id'])
    with pytest.raises(ValueError, match='exactly one'):
        creations.prepare(task, 'sol', actor='a', request_key='bare', role='implementer')
    cards.decide(task, card['id'], 'approve', actor='human')
    implementer = created(creations, task, 'implementer', card['id'], key='impl')
    assert implementer['command']['expectedCwd'] == str(tree)
    assert cards.get(task, card['id'])['status'] == 'implementing'
    sessions = repo.task(task)['sessions']
    assert {s['role'] for s in sessions} == {'analyst', 'implementer'}
    assert all('cwd' not in s for s in sessions)  # cwd stays on the server


def test_implementer_never_receives_benchmark_material_and_messages_use_session_cwd(roles):
    from rag_eval.workbench.agent_messages import AgentMessageStore, MessageInputRejected
    repo, task, tree, workspace, creations, cards = roles
    card = cards.draft(task, CARD, author='human')
    cards.decide(task, card['id'], 'approve', actor='human')
    implementer = created(creations, task, 'implementer', card['id'], key='impl')
    analyst = created(creations, task, 'analyst')
    messages = AgentMessageStore(repo, workspace_ids=[workspace])
    sha = '0' * 64
    for reference in ({'kind': 'task_runs'}, {'kind': 'run_case', 'run_id': 'r', 'case_id': 'c'}):
        with pytest.raises(MessageInputRejected):
            messages.prepare(task, implementer['session_id'], 'go', [{'reference': reference, 'sha256': sha}], 'interrupt',
                             actor='a', request_key='leak-' + reference['kind'])
    with pytest.raises(MessageInputRejected, match='own session'):
        messages.prepare(task, analyst['session_id'], 'go',
                         [{'reference': {'kind': 'brief', 'session_id': implementer['session_id']}, 'sha256': sha}],
                         'interrupt', actor='a', request_key='other-brief')
    sent = messages.prepare(task, analyst['session_id'], 'hello', [], 'interrupt', actor='a', request_key='ok')
    assert sent['command']['expectedCwd'] == analyst['command']['expectedCwd'] != str(tree)


def test_briefs_by_role(roles):
    from rag_eval.workbench.context import resolve_context
    repo, task, tree, workspace, creations, cards = roles
    card = cards.draft(task, CARD, author='human')
    cards.decide(task, card['id'], 'approve', actor='human')
    implementer = created(creations, task, 'implementer', card['id'], key='impl')
    analyst = created(creations, task, 'analyst')
    text = resolve_context(repo, task, {'kind': 'brief', 'session_id': analyst['session_id']})['text']
    assert '分析 Agent' in text and 'scripts/sn-eval' in text and 'docs/failure-ledger.md' in text
    assert '不要运行 run_notebook_benchmarks.py' in text
    brief = resolve_context(repo, task, {'kind': 'brief', 'session_id': implementer['session_id']})['text']
    assert '实现 Agent' in brief and CARD['implementer_brief'] in brief and str(tree) in brief
    for hidden in ('qasper:abc', 'qasper:def', 'context precision', 'falsification'):
        assert hidden not in brief


def test_card_lifecycle_and_prediction_check(roles):
    from rag_eval.workbench.hypotheses import check_predictions, validate_card
    from rag_eval.workbench.repository import Conflict
    repo, task, tree, workspace, creations, cards = roles
    with pytest.raises(ValueError):
        validate_card({**CARD, 'predictions': [{'case_id': 'x', 'expect': 'better'}]})
    with pytest.raises(ValueError):
        validate_card({k: v for k, v in CARD.items() if k != 'falsification'})
    card = cards.draft(task, CARD, author='agent:analyst')
    with pytest.raises(Conflict):
        cards.conclude(task, card['id'], effect='supported', cause='supported', note='', actor='human')
    cards.decide(task, card['id'], 'approve', actor='human')
    flips = {'admission': 'admitted', 'metric': 'answer_f1', 'counts': {'better': 1, 'worse': 1, 'same': 0, 'unpaired': 0},
             'rows': [{'case_id': 'qasper:abc', 'left': 0.2, 'right': 0.6, 'delta': 0.4},
                      {'case_id': 'qasper:def', 'left': 0.9, 'right': 0.5, 'delta': -0.4}]}
    result = check_predictions(cards.get(task, card['id'])['card']['predictions'], flips)
    assert [r['held'] for r in result['results']] == [True, False] and result['held'] == 1
    with pytest.raises(ValueError, match='not admitted'):
        cards.record_check(task, card['id'], baseline_run='a', candidate_run='b', flips={**flips, 'admission': 'rejected'},
                           leak=None, actor='human')
    checked = cards.record_check(task, card['id'], baseline_run='a', candidate_run='b', flips=flips, leak=None, actor='human')
    assert checked['status'] == 'verified' and checked['verification']['decided'] == 2
    done = cards.conclude(task, card['id'], effect='regression_or_tradeoff', cause='undecided', note='guard got worse', actor='human')
    assert done['status'] == 'concluded'
    kinds = [e['kind'] for e in repo.task(task)['history'] if e['kind'].startswith('hypothesis.')]
    assert kinds == ['hypothesis.drafted', 'hypothesis.decided', 'hypothesis.checked', 'hypothesis.concluded']


def test_leak_scan_reports_case_and_kind_without_text():
    from rag_eval.workbench.hypotheses import scan_text
    question = 'which classifiers did they experiment with in this paper'
    material = ({question: 'qasper:abc'}, {}, {'qasper:abc'})
    from rag_eval.workbench.hypotheses import _shingles
    gold = 'we used logistic regression with tf idf features over the whole corpus as a baseline'
    material[1].update({s: 'qasper:def' for s in _shingles(gold)})
    hits = scan_text('# Which classifiers did they experiment with in this paper?\nlogistic regression with tf idf features over the whole corpus', material)
    assert {(h['kind'], h['case_id']) for h in hits} == {('question', 'qasper:abc'), ('gold_or_reference_8gram', 'qasper:def')}
    assert scan_text('rerank six chunks before synthesis', material) == []


def test_agent_tool_roles_and_draft_leak_guard(tmp_path, monkeypatch, capsys):
    from rag_eval.workbench import agent_tools
    from rag_eval.workbench.repository import WorkbenchRepository
    from rag_eval.workbench.run_views import RunEntry, RunViews
    from test_run_views import CASE, RUN, SCORES
    repo = WorkbenchRepository(tmp_path / 'state')
    workspace = repo.register_workspace('E', tmp_path)
    task = repo.create_task(workspace['id'], 'T', 'goal', request_key='t')['id']
    views = RunViews([RunEntry('e5', RUN, SCORES, task_id=task)])
    monkeypatch.setattr(agent_tools, 'build', lambda state, config: (repo, views))
    with pytest.raises(SystemExit):
        agent_tools.main(['--task', task, '--role', 'implementer', 'case', 'e5', CASE])
    agent_tools.main(['--task', task, 'cases', 'e5'])
    assert json.loads(capsys.readouterr().out)['cases'][0]['case_id'] == CASE
    question = views.case('e5', CASE)['question']
    leaking = tmp_path / 'leak.json'
    leaking.write_text(json.dumps({**CARD, 'implementer_brief': 'Make sure we answer: ' + question}))
    agent_tools.main(['--task', task, 'hypothesis-draft', '--file', str(leaking)])
    out = json.loads(capsys.readouterr().out)
    assert out['saved'] is False and out['findings'][0]['kind'] == 'question'
    clean = tmp_path / 'card.json'
    clean.write_text(json.dumps(CARD))
    agent_tools.main(['--task', task, 'hypothesis-draft', '--file', str(clean)])
    assert json.loads(capsys.readouterr().out)['card']['status'] == 'draft'
