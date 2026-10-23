from copy import deepcopy
import json

import pytest

from rag_eval.workbench import clusters


def observation(cid, score, *, answer='wrong', recall=0, invalid=0):
    return dict(case_id=cid, question='A synthetic question about retrieval?', status='success', prediction=answer,
                response=dict(answer=answer, grounded=False, evidence_level='low'),
                deterministic=dict(evidence_hit=bool(recall), citation_invalid_count=invalid, citation_valid_count=0),
                stage_metrics=dict(gold_evidence_recall=dict(value=recall), context_precision=dict(value=0)),
                metrics=dict(answer_f1=score), usage=dict(total_tokens=10), retrieval_context=[dict(text='observed context')])


def test_cluster_statistics_are_hand_computable_and_search_only():
    iteration = dict(protocol=dict(splits=dict(search=['a', 'b'], validation=['v']), success_threshold=.5))
    cluster = dict(signature=dict(kind='range', field='stage_metrics.context_precision.value', max=0))
    executions = [dict(execution_id='e1', cases=[observation('a', 0, answer='unanswerable'), observation('b', 0), observation('v', 0)]),
                  dict(execution_id='e2', cases=[observation('a', 1, recall=1), observation('b', 0, recall=1, invalid=1)])]
    scan = clusters.scan(iteration, cluster, executions)
    assert scan['members'] == ['a', 'b']
    assert (scan['n_cases'], scan['n_attempts'], scan['n_failed']) == (2, 4, 3)
    assert scan['q'] == .25 and scan['m'] == pytest.approx(2/3) and scan['v'] == pytest.approx(1/3)
    assert scan['retrieval_ceiling'] == pytest.approx(.5) and scan['verification_ceiling'] == pytest.approx(.25)
    assert 'v' not in scan['observations']
    executions[0]['cases'][0]['metrics']['answer_f1'] = None
    unknown = clusters.scan(iteration, cluster, executions)
    assert unknown['q'] is None and unknown['n_unknown'] == 1


@pytest.mark.parametrize('field', ['question', 'case_id', 'gold.answer', 'response.gold', 'metrics.secret',
                                   'retrieval_context.0.text', 'prediction.__class__'])
def test_signatures_reject_non_whitelisted_paths(field):
    with pytest.raises(ValueError):
        clusters.validate_signature(dict(kind='regex', field=field, pattern='x'))


def test_combinators_regex_and_ranges_and_missing_fields():
    sig = dict(all_of=[dict(kind='range', field='metrics.answer_f1', min=0, max=.4),
                      dict(not_=dict(kind='regex', field='prediction', pattern='correct'))])
    # Canonical negation spelling is JSON "not".
    sig['all_of'][1] = {'not': sig['all_of'][1]['not_']}
    assert clusters.matches(sig, observation('a', 0))
    assert not clusters.matches(sig, observation('a', 1))
    assert clusters.matches(dict(kind='regex', field='retrieval_context.*.text', pattern='observed'), observation('a', 0))
    for bad in [dict(kind='range', field='prediction', min=float('nan')), {'any_of': []},
                dict(kind='regex', field='prediction', pattern='['),
                dict(kind='range', field='retrieval_context.*.text', min=0)]:
        with pytest.raises(ValueError):
            clusters.validate_signature(bad)


def test_observation_projects_saved_outputs_without_gold_annotations(tmp_path):
    from synthetic_workbench_run import saved_qasper
    from rag_eval.workbench.run_views import RunEntry, RunViews
    run, score, cid = saved_qasper(tmp_path)
    views = RunViews([RunEntry('synthetic', run, score)])
    obs = views.observation('synthetic', cid)
    assert obs['question'] and obs['prediction']
    assert len(obs['retrieval_context']) == 16
    assert obs['metrics']['answer_f1'] == .5
    assert obs['stage_metrics']['gold_evidence_recall']['value'] is not None
    assert not {'gold', 'references', 'papers', 'qasper_evidence', 'annotations'} & set(obs)

from test_workbench_iterations import env, iteration, BUDGET, finish  # noqa: E402,F401


@pytest.fixture
def frozen_cluster(env):
    from rag_eval.workbench.comparisons import Admission
    from test_workbench_comparisons import Views
    from rag_eval.workbench.hypotheses import HypothesisStore
    from test_workbench_roles_hypotheses import CARD
    views = Views()
    views._worktree_service = env['service'].trees
    service = env['service']
    service.admission = Admission(views)
    tree = iteration(env, case_ids=['qasper:q', 'qasper:q2'])
    questions = dict(a='Which synthetic retrieval stage omitted the required material?', b='How does the synthetic reranker order context blocks?')
    secret = 'confidential annotation tokens must never enter any implementer message'
    views._load = lambda run_id: dict(_bundle=dict(cases=[dict(case_id='qasper:q', question=questions['a'],
        gold=dict(annotations=[dict(answer=secret, evidence=[secret])])), dict(case_id='qasper:foreign')]))
    views.observation = lambda run_id, cid: dict(observation(cid, views.scores[run_id.rsplit('-', 1)[0]][cid]),
        question=questions['a'] if cid == 'qasper:q' else questions['b'], gold={'answer': secret},
        references=[secret], papers=[dict(gold_evidence=secret)], retrieval_context=[dict(text='observed qasper:foreign')])
    for i in range(2):
        result = service.run(tree['root_candidate_id'], budget=BUDGET, actor='test', request_key=f'base{i}')
        eid = result['attempts'][-1]['execution_id']
        views.scores[eid] = {'qasper:q': i, 'qasper:q2': 0}
        views.partitions[eid] = [('qasper:q', 'qasper:q2')]
        finish(env, result)
    store = clusters.ClusterStore(env['repo'], service, views)
    card = dict(cluster_id='c01', description='Missing context before synthesis',
                signature=dict(kind='range', field='stage_metrics.context_precision.value', max=0))
    store.propose(tree['root_candidate_id'], card, actor='analyst')
    proposal_card = dict(CARD, cluster_id='c01', evidence=[], predictions=[dict(case_id='qasper:q', expect='improve')],
                         contract=dict(cluster_id='c01', predicted_flips=['qasper:q'], guards=['qasper:q2'], tau=.5))
    proposal = service.draft_proposal(tree['root_candidate_id'], proposal_card, author='analyst')
    HypothesisStore(env['repo']).decide(tree['task_id'], proposal['id'], 'approve', actor='human')
    candidate = service.derive(tree['root_candidate_id'], actor='test', request_key='child', proposal_id=proposal['id'])
    return dict(env=env, service=service, views=views, tree=tree, store=store, card=card, proposal=proposal,
                proposal_card=proposal_card, candidate=candidate, secret=secret, questions=questions)


def test_cluster_freeze_contract_membership_and_implementer_projection(frozen_cluster):
    f = frozen_cluster
    task, store, candidate = f['tree']['task_id'], f['store'], f['candidate']['id']
    before = store.get(task, 'c01')
    assert store.show(task, 'c01')['members'] == ['qasper:q', 'qasper:q2']
    impl = store.show(task, 'c01', implementer_candidate=candidate)
    encoded = json.dumps(impl)
    assert f['questions']['a'] in encoded and 'official_primary' in encoded and 'context_blocks' in encoded
    assert f['secret'] not in encoded and 'qasper:' not in encoded
    assert [r['member'] for r in impl['members']] == ['m01', 'm02']
    assert len(impl['members'][0]['attempts']) == 2
    with pytest.raises(ValueError, match='own cluster'):
        store.show(task, 'c02', implementer_candidate=candidate)
    with pytest.raises(Exception, match='frozen'):
        store.propose(f['tree']['root_candidate_id'], f['card'], actor='analyst')
    bad = deepcopy(f['proposal_card'])
    bad['contract']['predicted_flips'] = ['not-a-member']
    with pytest.raises(ValueError, match='members'):
        f['service'].draft_proposal(f['tree']['root_candidate_id'], bad, author='analyst')
    f['views'].scores.clear()
    assert store.get(task, 'c01') == before
    assert store.show(task, 'c01', implementer_candidate=candidate) == impl


def test_cluster_description_leak_scan_is_unchanged(frozen_cluster):
    f = frozen_cluster
    for description in [f['questions']['a'], f['secret'], 'qasper:q']:
        with pytest.raises(ValueError, match='overlaps'):
            f['store'].propose(f['tree']['root_candidate_id'], dict(f['card'], cluster_id='c02', description=description), actor='analyst')


def test_prepared_implementer_cluster_context_has_no_annotations_or_ids(frozen_cluster):
    from rag_eval.workbench.context import resolve_context
    from rag_eval.workbench.agent_messages import AgentMessageStore, MessageInputRejected
    f = frozen_cluster
    repo, task = f['env']['repo'], f['tree']['task_id']
    session = repo.attach_session(task, '38847573-1b4c-45f4-9568-469f37948f5d', actor='test', request_key='session')
    with repo.connect(write=True) as conn:
        conn.execute("UPDATE task_sessions SET role='implementer',candidate_id=?,hypothesis_id=?,cwd=? WHERE id=?",
                     (f['candidate']['id'], f['proposal']['id'], f['candidate']['worktree']['path'], session['id']))
    ref = dict(kind='cluster_case', cluster_id='c01', member='m01')
    snapshot = resolve_context(repo, task, ref, run_views=f['views'])
    messages = AgentMessageStore(repo, workspace_ids=[f['env']['workspace']['id']], run_views=f['views'])
    prepared = messages.prepare(task, session['id'], 'Inspect this evidence',
        [dict(reference=ref, sha256=snapshot['sha256'])], 'steer', actor='test', request_key='message')
    encoded = json.dumps(prepared['snapshots'])
    assert f['questions']['a'] in encoded and f['secret'] not in encoded and 'qasper:' not in encoded
    assert prepared['status'] == 'prepared'
    for forbidden in [dict(kind='case', result_id='r', case_id='c'), dict(kind='run_case', run_id='r', case_id='c'),
                      dict(kind='task_runs'), dict(kind='iteration_tree'), dict(kind='result', result_id='r')]:
        with pytest.raises(MessageInputRejected):
            messages.prepare(task, session['id'], 'bad', [dict(reference=forbidden, sha256='0'*64)],
                             'steer', actor='test', request_key=forbidden['kind'])
    with pytest.raises(MessageInputRejected, match='own cluster'):
        messages.prepare(task, session['id'], 'bad', [dict(reference=dict(ref, cluster_id='c02'), sha256='0'*64)],
                         'steer', actor='test', request_key='other')


def test_cluster_cli_projects_implementer_and_rejects_propose(frozen_cluster, monkeypatch, capsys):
    from rag_eval.workbench import agent_tools
    f = frozen_cluster
    monkeypatch.setattr(agent_tools, 'build', lambda *args: (f['env']['repo'], f['views']))
    args = ['--task', f['tree']['task_id'], '--role', 'implementer', '--candidate', f['candidate']['id']]
    agent_tools.main([*args, 'cluster', 'show', 'c01'])
    impl = json.loads(capsys.readouterr().out)
    assert impl['members'][0]['member'] == 'm01' and 'qasper:' not in json.dumps(impl)
    agent_tools.main([*args, 'cluster', 'list'])
    assert len(json.loads(capsys.readouterr().out)) == 1
    with pytest.raises(SystemExit):
        agent_tools.main([*args, 'cluster', 'propose', '--file', 'not-read'])
    with pytest.raises(ValueError, match='own cluster'):
        agent_tools.main([*args, 'cluster', 'show', 'other'])


def test_analyst_cluster_propose_file_and_sealed_context_refusal(frozen_cluster, monkeypatch, capsys, tmp_path):
    from rag_eval.workbench import agent_tools
    from rag_eval.workbench.context import resolve_context
    f = frozen_cluster
    repo, task = f['env']['repo'], f['tree']['task_id']
    monkeypatch.setattr(agent_tools, 'build', lambda *args: (repo, f['views']))
    path = tmp_path / 'cluster.json'
    path.write_text(json.dumps(dict(f['card'], cluster_id='c02', candidate_id=f['tree']['root_candidate_id'])))
    agent_tools.main(['--task', task, 'cluster', 'propose', '--file', str(path)])
    assert json.loads(capsys.readouterr().out)['cluster_id'] == 'c02'
    with repo.connect(write=True) as conn:
        protocol = f['tree']['protocol']
        protocol['splits']['sealed'] = ['qasper:q']
        conn.execute('UPDATE iterations SET protocol_json=? WHERE id=?', (json.dumps(protocol), f['tree']['id']))
    with pytest.raises(ValueError, match='sealed'):
        resolve_context(repo, task, dict(kind='cluster_case', cluster_id='c01', member='m01'))


def test_missing_entire_execution_still_matches_missing_search_cases():
    result = clusters.scan(dict(protocol=dict(splits=dict(search=['a'], validation=['v']))),
        dict(signature=dict(kind='regex', field='status', pattern='^missing$')),
        [dict(execution_id='failed-before-publication', cases=[])])
    assert result['members'] == ['a'] and result['n_failed'] == 1 and result['q'] == 0
    assert result['observations']['a'][0]['metrics'] == {}
