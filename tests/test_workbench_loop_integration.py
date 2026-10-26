"""Round engine through real services, Git, fake bridges and synthetic execution views."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from rag_eval.workbench.action_requests import auto_execute
from rag_eval.workbench.clusters import ClusterStore
from rag_eval.workbench.comparisons import Admission
from rag_eval.workbench.creation_api import CreationAPI
from rag_eval.workbench.iterations_api import IterationAPI
from rag_eval.workbench.loop import RoundService
from rag_eval.workbench.loop_services import RoundOperations
from rag_eval.workbench.message_api import MessageAPI
from rag_eval.workbench.sessions import TaskSessionReader
from test_workbench_agent_creations import Bridge, created
from test_workbench_agent_messages import acknowledged
from test_workbench_clusters import observation
from test_workbench_comparisons import Views
from test_workbench_iterations import env, iteration
from test_workbench_loop_services import ReadBridge
from test_workbench_roles_hypotheses import CARD


@pytest.mark.parametrize('round_count', [1, 2])
def test_real_round_services_resume_without_duplicate_agents_or_executions(env, round_count):
    service, repo = env['service'], env['repo']
    views = Views()
    views._worktree_service = service.trees
    views._load = lambda run_id: dict(_bundle=dict(cases=[]))
    views.task_runs_context = lambda tid: ('runs', json.dumps(dict(runs=[dict(kind='execution', id=e)
                                                for e in views.partitions])), {})
    views.run = lambda rid: dict(official=dict(primary_metric='answer_f1', metrics=dict(answer_f1=.2)))
    views.observation = lambda rid, cid: observation(cid, views.scores[rid.rsplit('-', 1)[0]][cid])
    views.cases = lambda rid: dict(cases=[dict(case_id=cid, status='success') for cid in views.scores[rid.rsplit('-', 1)[0]]])
    service.admission = Admission(views)
    # This synthetic bundle has only two papers: base is search-only, so the
    # truthful gate result is unmeasurable and two rounds stop on no acceptance.
    tree = iteration(env, case_ids=['qasper:q', 'qasper:q2'], fresh_batch_count=1,
                     fresh_papers_per_batch=1, budget=dict(limit_cny=1000, max_runs_per_candidate=30))
    config = dict(task_worktrees=dict(create_candidates=True), job_preparation=service.preparation_config,
                  job_execution=service.execution_config,
                  agent_creation=dict(workspaceId='fixture-evaluator', profiles={
                      'pi-flash': dict(provider='codex/model-example', modeId='full-access')}))
    api = IterationAPI(repo, env['workspace'], service.trees, plans=service.plans,
        job_api=SimpleNamespace(store=service.jobs, execution=service.executions), actor='alice',
        config=config, run_views=views, creator=env['creator'])
    creator, messenger = Bridge(created), Bridge(acknowledged)
    creation = CreationAPI(repo, env['workspace'], config, actor='alice', bridge=creator)
    creation.store.evaluated_count = lambda tid: sum(c['state'] == 'evaluated' for c in api.service.iteration(tree['id'])['candidates'])
    creation.store.candidate_state = lambda cid: api.service.candidate(cid)['state']
    messages = MessageAPI(repo, env['workspace'], config, actor='alice', bridge=messenger)
    reader = TaskSessionReader(repo, ReadBridge(creator))
    counts = {}
    read = reader.bridge.call
    def read_progress(command):
        counts[command['agentId']] = counts.get(command['agentId'], 0) + 1
        reader.bridge.status = 'running' if counts[command['agentId']] == 1 else 'idle'
        return read(command)
    reader.bridge.call = read_progress
    ops = RoundOperations(api, creation, messages, reader)
    clock = [100.]
    engine = RoundService(repo, ops, clock=lambda: clock[0])
    state = engine.start(tree['id'], actor='alice', request_key='integration', rounds=round_count)
    drafted, implemented, base_requested = set(), set(), set()
    store = ClusterStore(repo, api.service, views)

    def settle():
        for candidate in api.service.iteration(tree['id'])['candidates']:
            for attempt in candidate['attempts']:
                if attempt['execution_state'] != 'running':
                    continue
                job = api.service.jobs.get(tree['task_id'], attempt['job_id'])
                ids = job['plan_snapshot']['content']['selection']['case_ids']
                eid = attempt['execution_id']
                views.scores[eid] = {cid: .2 for cid in ids}
                views.partitions[eid] = [tuple(ids)]
                env['executions']._finish(eid, state='finished', exit_code=0, error_type=None,
                    run_state=dict(phase='finished', cost_observation=dict(currency='CNY', complete=True, total_cny=.01)))

    def request(cid, verb, params):
        recorded = api.requests.request(tree['task_id'], verb, cid, params,
                                       reason='Synthetic LCD step', requested_by='fake-implementer')
        result = auto_execute(api.service, recorded, workspace_id=env['workspace']['id'])
        assert result['status'] == 'executed', result

    for tick in range(100):
        settle()
        if state['rounds']:
            current = state['rounds'][-1]
            number = current['number']
            if state['phase'] == 'analysis' and current.get('analyst') and number not in drafted:
                baseline = current['baseline_candidate_id']
                cluster_id = 'round_' + str(number)
                store.propose(baseline, dict(cluster_id=cluster_id, description='Synthetic retrieval mechanism',
                    signature=dict(kind='range', field='metrics.answer_f1', max=.4)), actor='fake-analyst')
                api.service.draft_proposal(baseline, dict(CARD, evidence=[], cluster_id=cluster_id,
                    predictions=[dict(case_id='qasper:q', expect='improve')],
                    contract=dict(cluster_id=cluster_id, predicted_flips=['qasper:q'], guards=[])), author='fake-analyst')
                drafted.add(number)
            for candidate in current['candidates']:
                cid = candidate.get('candidate_id')
                if not cid or not candidate.get('agent'):
                    continue
                if cid not in implemented:
                    path = Path(api.service.candidate(cid)['worktree']['path']) / 'backend/app/__init__.py'
                    path.write_text(f'VALUE = {number + 5}\n')
                    request(cid, 'node-freeze', {})
                    request(cid, 'node-cluster-run', dict(cluster_id=candidate['cluster_id'], timeout_seconds=60))
                    implemented.add(cid)
                    settle()
                if cid not in base_requested:
                    api.comparisons.for_cluster(cid, candidate['cluster_id'], actor='fake-implementer')
                    request(cid, 'node-run', dict(attempts=2, timeout_seconds=60))
                    base_requested.add(cid)
                    settle()
        # Reconstruct service and adapter after every checkpoint; durable stores
        # must reuse the real Agent/message/job receipts, not fake-port counters.
        ops = RoundOperations(api, creation, messages, reader)
        engine = RoundService(repo, ops, clock=lambda: clock[0])
        state = engine.tick(state['id'])
        clock[0] += 1
        if state['status'] in ('completed', 'failed', 'stopped'):
            break
    assert state['status'] == 'completed', state
    assert len(state['rounds']) == round_count
    assert state['stop_reason'] == ('no_accept' if round_count == 2 else 'rounds_exhausted')
    assert all(r['candidates'][0]['verdict']['verdict'] == 'unmeasurable' for r in state['rounds'])
    assert len(creator.commands) == len(messenger.commands) == round_count * 2
    if round_count == 2:
        analysts = [r for r in creation.store.list(tree['task_id'], actor='alice') if r['role'] == 'analyst']
        assert {r.get('scope') for r in analysts} == {None, 'iteration'}
    before = len(env['executions'].records(env['workspace']['id']))
    assert engine.start(tree['id'], actor='alice', request_key='integration', rounds=round_count)['id'] == state['id']
    assert engine.tick(state['id']) == state
    assert len(env['executions'].records(env['workspace']['id'])) == before
