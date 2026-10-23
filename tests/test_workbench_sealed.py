import json
import pytest
from test_workbench_iterations import env, iteration, BUDGET, finish  # noqa: F401
from rag_eval.workbench.repository import Conflict


def registered_tree(env, **kwargs):
    env['service'].preparation_config['sealed_selections']['synthetic-holdout']['case_ids'] = ['qasper:q3']
    return iteration(env, **kwargs)


def test_sealed_requires_confirmation_and_registration_and_claims_once(env):
    from rag_eval.workbench.sealed import SealedService
    tree = registered_tree(env)
    sealed = SealedService(env['repo'], env['service'], None)
    assert sealed.score(tree['id']) is None
    with pytest.raises(ValueError, match='human confirmation'):
        sealed.run(tree['root_candidate_id'], budget=BUDGET, actor='human', confirmed=False)
    result = sealed.run(tree['root_candidate_id'], budget=BUDGET, actor='human', confirmed=True)
    assert result['status'] == 'dispatched'
    candidate = env['service'].candidate(tree['root_candidate_id'])
    a = candidate['attempts'][0]
    job = env['service'].jobs.get(tree['task_id'], a['job_id'])
    assert job['plan_snapshot']['content']['selection']['case_ids'] == ['qasper:q3']
    assert a['batch_id'] == 'sealed:once'
    with pytest.raises(Conflict, match='already claimed'):
        sealed.run(tree['root_candidate_id'], budget=BUDGET, actor='human', confirmed=True)
    second = registered_tree(env, request_key='other-study-alias')
    with pytest.raises(Conflict, match='already claimed'):
        sealed.run(second['root_candidate_id'], budget=BUDGET, actor='human', confirmed=True)


def test_ordinary_run_and_plan_refuse_sealed_and_failed_claim_is_not_released(env, monkeypatch):
    from rag_eval.workbench.sealed import SealedService
    tree = registered_tree(env)
    sealed = SealedService(env['repo'], env['service'], None)
    with pytest.raises(ValueError, match='sealed'):
        env['service'].plans.save(task_id=tree['task_id'], input_id='registered',
            expected_bundle_id=env['create']['expected_bundle_id'], case_ids=['qasper:q3'],
            title='forbidden', mode='chunk', actor='test', request_key='forbidden')
    with pytest.raises(ValueError, match='sealed'):
        env['service'].run(tree['root_candidate_id'], budget=BUDGET, actor='test', request_key='escape', batch_id='sealed:once')
    monkeypatch.setattr(env['service'], 'run', lambda *a, **k: (_ for _ in ()).throw(ValueError('synthetic failure')))
    with pytest.raises(ValueError, match='synthetic'):
        sealed.run(tree['root_candidate_id'], budget=BUDGET, actor='human', confirmed=True)
    assert sealed.score(tree['id'])['status'] == 'failed'
    with pytest.raises(Conflict, match='already claimed'):
        sealed.run(tree['root_candidate_id'], budget=BUDGET, actor='human', confirmed=True)


def test_sealed_result_is_aggregate_only_and_kept_out_of_candidate_reference(env, monkeypatch):
    from rag_eval.workbench.sealed import SealedService
    tree = registered_tree(env)
    sealed = SealedService(env['repo'], env['service'], None)
    sealed.run(tree['root_candidate_id'], budget=BUDGET, actor='human', confirmed=True)
    finish(env, env['service'].candidate(tree['root_candidate_id']))
    monkeypatch.setattr(sealed, '_measure', lambda claim, execution: dict(metric='answer_f1', n=1, mean=.75))
    score = sealed.score(tree['id'])
    assert score['mean'] == .75 and score['status'] == 'finished'
    assert 'qasper:' not in json.dumps(score)
    assert env['service'].candidate(tree['root_candidate_id'])['reference'] is None


def test_registration_is_rechecked_and_ordinary_job_cannot_repeat_sealed(env):
    from rag_eval.workbench.sealed import SealedService
    tree = registered_tree(env)
    service = env['service']
    sealed = SealedService(env['repo'], service, None)
    service.preparation_config['sealed_selections']['synthetic-holdout']['case_ids'] = ['qasper:q2']
    with pytest.raises(ValueError, match='trusted sealed registration'):
        sealed.run(tree['root_candidate_id'], budget=BUDGET, actor='human', confirmed=True)
    assert sealed.score(tree['id']) is None
    service.preparation_config['sealed_selections']['synthetic-holdout']['case_ids'] = ['qasper:q3']
    sealed.run(tree['root_candidate_id'], budget=BUDGET, actor='human', confirmed=True)
    attempt = service.candidate(tree['root_candidate_id'])['attempts'][0]
    job = service.jobs.get(tree['task_id'], attempt['job_id'])
    with pytest.raises(ValueError, match='sealed'):
        service.jobs.prepare(tree['task_id'], job['plan_id'], job['plan_revision'], job['plan_content_hash'],
                             actor='human', request_key='bypass')
    restarted = SealedService(env['repo'], service, None)
    with pytest.raises(Conflict, match='already claimed'):
        restarted.run(tree['root_candidate_id'], budget=BUDGET, actor='human', confirmed=True)


def test_sealed_action_is_never_automatically_confirmed(env):
    from rag_eval.workbench.action_requests import ActionRequestStore, auto_execute, execute
    tree = registered_tree(env, budget=dict(limit_cny=100, max_runs_per_candidate=10))
    service = env['service']
    store = ActionRequestStore(env['repo'])
    request = store.request(tree['task_id'], 'iteration-sealed-run', tree['root_candidate_id'], {},
                            reason='Final holdout', requested_by='agent:analyst')
    assert auto_execute(service, request, workspace_id=env['workspace']['id'])['status'] == 'pending'
    with pytest.raises(ValueError, match='human confirmation'):
        execute(service, request, workspace_id=env['workspace']['id'], actor='human')
    assert not service.candidate(tree['root_candidate_id'])['attempts']
    assert execute(service, request, workspace_id=env['workspace']['id'], actor='human',
                   human_confirmed=True)['status'] == 'dispatched'


def test_formal_sealed_aggregate_uses_only_the_claimed_selection(tmp_path):
    from rag_eval.workbench.sealed import SealedService
    from rag_eval.workbench.run_views import RunViews, RunEntry
    from synthetic_workbench_run import saved_qasper
    run, scores, cid = saved_qasper(tmp_path)
    service = object.__new__(SealedService)
    service.views = RunViews([RunEntry('execution-1', run, scores=scores, case_ids=(cid,))])
    execution = dict(execution_id='execution', state='finished', run_dir=str(run))
    claim = dict(case_ids_json=json.dumps([cid]))
    assert service._measure(claim, execution) == dict(metric='answer_f1', n=1, mean=.5)
    claim['case_ids_json'] = json.dumps([cid, 'extra'])
    with pytest.raises(ValueError, match='selection differs'):
        service._measure(claim, execution)


def test_http_ledger_scope_and_human_confirmation_adapter(env):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from rag_eval.workbench.iterations_api import IterationAPI
    from rag_eval.workbench.action_requests import ActionRequestStore
    from rag_eval.workbench.repository import UnknownIdentity
    tree = registered_tree(env)
    api = object.__new__(IterationAPI)
    api.workspace, api.actor, api.writable = env['workspace'], 'human', True
    api.service, api.repository, api.views = env['service'], env['repo'], None
    api.requests = ActionRequestStore(env['repo'])
    app = FastAPI()
    app.include_router(api.router())
    with TestClient(app) as client:
        response = client.get('/api/iterations/' + tree['id'] + '/ledger')
    assert response.status_code == 200
    assert set(response.json()) == {'sampling_ledger', 'overfit_ledger', 'structure_ledger', 'sealed_score'}
    assert response.json()['sealed_score'] is None
    with pytest.raises(UnknownIdentity):
        api._scope(iteration_id='unowned')
    request = api.requests.request(tree['task_id'], 'iteration-sealed-run', tree['root_candidate_id'], {},
                                    reason='Final report', requested_by='agent:analyst')
    result = api.confirm(request['id'])
    assert result['status'] == 'executed'
    assert api.confirm(request['id']) == result  # a receipt recovery never dispatches again
    assert len(api.service.candidate(tree['root_candidate_id'])['attempts']) == 1
