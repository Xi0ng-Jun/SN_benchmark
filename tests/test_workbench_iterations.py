"""Iteration/candidate identity contracts with real Git worktrees, no Paseo or model."""
import json
from pathlib import Path
import subprocess

import pytest

from rag_eval.artifact_store import ArtifactStore
from rag_eval.bundle_index import install_bundle
from rag_eval.workbench.evaluation_jobs import EvaluationJobStore
from rag_eval.workbench.hypotheses import HypothesisStore
from rag_eval.workbench.iterations import COORDINATOR, IterationService
from rag_eval.workbench.job_execution import JobExecutionStore
from rag_eval.workbench.jobs_api import build_execution_spec
from rag_eval.workbench.repository import Conflict
from rag_eval.workbench.task_worktree import TaskWorktreeService, initialize_task_worktrees
from test_hypothesis_worktrees import Creator, git
from test_workbench_evaluation_plans import setup as plan_setup
from test_workbench_notebook_job import settings
from test_workbench_roles_hypotheses import CARD

BUDGET = dict(timeout_seconds=600)


def commit(project, message):
    git(project, 'add', '.')
    git(project, '-c', 'user.name=fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', message)
    return git(project, 'rev-parse', 'HEAD')


@pytest.fixture
def env(tmp_path, monkeypatch):
    repo, workspace, _, plans, _, command = plan_setup(tmp_path)
    store = ArtifactStore(tmp_path / 'artifacts')
    refs = install_bundle(tmp_path / 'data/bundle', store.root)
    project = tmp_path / 'sn'
    (project / 'backend/app').mkdir(parents=True)
    (project / 'backend/app/__init__.py').write_text('VALUE = 1\n')
    subprocess.run(['git', 'init', '-q', str(project)], check=True)
    start = commit(project, 'start')
    (project / 'backend/app/__init__.py').write_text('VALUE = 2\n')
    head = commit(project, 'later head')
    initialize_task_worktrees(repo)
    trees = TaskWorktreeService(repo, store, project_root=project)
    executions = JobExecutionStore(repo)
    # Preserve spec and receipt publication; only the process launch is external.
    monkeypatch.setattr(executions, '_start', lambda record: None)
    creator = Creator(tmp_path / 'trees')
    service = IterationService(
        repo, trees, plans=plans, jobs=EvaluationJobStore(repo), executions=executions, creator=creator,
        preparation_config=dict(artifact_root=str(store.root),
                                inputs={'registered': dict(bundle_ref=refs['bundle'], index_ref=refs['index'])}),
        execution_config=settings(tmp_path, dict(artifact_store=store)), build_spec=build_execution_spec)
    create = dict(title='I1', start_commit=start, input_id='registered',
                  expected_bundle_id=command['expected_bundle_id'], case_ids=['qasper:q'], mode='chunk',
                  actor='alice', request_key='i1')
    return dict(repo=repo, workspace=workspace, project=project, start=start, head=head, service=service,
                creator=creator, executions=executions, create=create, store=store)


def iteration(env, **changes):
    return env['service'].create_iteration(env['workspace']['id'], **dict(env['create'], **changes))


def finish(env, candidate):
    """Mark the latest attempt's execution finished, as the supervisor would."""
    attempt = candidate['attempts'][-1]
    env['executions']._finish(attempt['execution_id'], state='finished', exit_code=0, error_type=None,
                              run_state=dict(phase='finished'))


def payload(env, candidate, attempt=-1):
    record = env['executions'].get(candidate['attempts'][attempt]['task_id'],
                                   candidate['attempts'][attempt]['execution_id'])
    return json.loads(record['argv'][-1])


def evaluated_root(env):
    tree = iteration(env)
    root = env['service'].run(tree['root_candidate_id'], budget=BUDGET, actor='alice', request_key='base')
    finish(env, root)
    return tree, env['service'].candidate(root['id'])


def approved(env, tree, parent):
    card = env['service'].draft_proposal(parent, CARD, author='agent:analyst')
    HypothesisStore(env['repo']).decide(tree['task_id'], card['id'], 'approve', actor='human')
    return card['id']


def test_root_starts_at_a_non_head_commit_and_freezes_without_committing(env):
    tree = iteration(env)
    root = tree['candidates'][0]
    assert tree['start_commit'] == env['start'] != env['head']
    assert root['id'] == tree['root_candidate_id'] and root['parent_candidate_id'] is None
    assert root['state'] == 'frozen' and root['frozen_commit'] == env['start']
    assert env['creator'].dispatched == [(str(env['project']), 'sn-cand-' + root['id'], env['start'])]
    path = Path(root['worktree']['path'])
    assert git(path, 'rev-parse', 'HEAD') == env['start']
    assert (path / 'backend/app/__init__.py').read_text() == 'VALUE = 1\n'
    assert git(env['project'], 'rev-parse', 'HEAD') == env['head']
    assert tree['protocol']['mode'] == 'chunk' and tree['selection_hash']
    # The same request recovers the original iteration without a second creation.
    assert iteration(env)['id'] == tree['id'] and len(env['creator'].dispatched) == 1


def test_seeded_iteration_starts_at_its_source_candidates_frozen_commit(env):
    tree = iteration(env)
    seeded = dict(iteration_id=tree['id'], candidate_id=tree['root_candidate_id'])
    with pytest.raises(ValueError, match='frozen commit'):
        iteration(env, request_key='i2', start_commit=env['head'], seeded_from=seeded)
    second = iteration(env, request_key='i3', seeded_from=seeded, mode='reasoning')
    assert second['seeded_from'] == seeded and second['task_id'] != tree['task_id']


def test_each_run_is_a_numbered_attempt_bound_to_its_preparation_and_runner_identity(env):
    tree = iteration(env)
    service, root_id = env['service'], tree['root_candidate_id']
    first = service.run(root_id, budget=BUDGET, actor='alice', request_key='base')
    assert [a['attempt'] for a in first['attempts']] == [1] and first['state'] == 'running'
    association = payload(env, first)['task_worktree']
    assert association == dict(task_id=tree['task_id'], worktree_path=first['worktree']['path'],
                               paseo_workspace_id='workspace-sn-cand-' + root_id, iteration_id=tree['id'],
                               candidate_id=root_id, attempt=1, frozen_commit=env['start'],
                               preparation_id=first['attempts'][0]['preparation_id'])
    prepared = env['service'].jobs.preparations(tree['task_id'], first['attempts'][0]['job_id'])[0]['preparation']
    assert prepared['source']['provenance']['head_revision'] == env['start']
    assert prepared['source']['artifact_ref'] == first['source_ref']
    assert service.run(root_id, budget=BUDGET, actor='alice', request_key='base')['attempts'] == first['attempts']
    env['executions']._finish(first['attempts'][0]['execution_id'], state='failed', exit_code=1,
                              error_type='RuntimeError', run_state=dict(phase='failed'))
    again = service.run(root_id, budget=BUDGET, actor='alice', request_key='base-retry')
    assert [(a['attempt'], a['execution_state']) for a in again['attempts']] == [(1, 'failed'), (2, 'running')]
    assert payload(env, again)['task_worktree']['attempt'] == 2


def test_proposals_hang_only_under_evaluated_candidates(env):
    tree = iteration(env)
    with pytest.raises(Conflict, match='evaluated'):
        env['service'].draft_proposal(tree['root_candidate_id'], CARD, author='agent:analyst')
    root = env['service'].run(tree['root_candidate_id'], budget=BUDGET, actor='alice', request_key='base')
    finish(env, root)
    card = env['service'].draft_proposal(root['id'], CARD, author='agent:analyst')
    assert card['parent_candidate_id'] == root['id'] and card['status'] == 'draft'
    with pytest.raises(Conflict, match='approved'):
        env['service'].derive(root['id'], proposal_id=card['id'], actor='alice', request_key='early')


def test_child_starts_at_parent_frozen_commit_and_the_coordinator_freezes_it(env):
    tree, root = evaluated_root(env)
    service = env['service']
    proposal = approved(env, tree, root['id'])
    child = service.derive(root['id'], proposal_id=proposal, actor='alice', request_key='a')
    assert child['parent_candidate_id'] == root['id'] and child['parent_commit'] == env['start']
    assert child['state'] == 'implementing' and child['proposal_id'] == proposal
    path = Path(child['worktree']['path'])
    assert git(path, 'rev-parse', 'HEAD') == env['start']
    # One proposal may yield several candidates.
    sibling = service.derive(root['id'], proposal_id=proposal, actor='alice', request_key='b')
    assert sibling['id'] != child['id'] and sibling['worktree']['path'] != child['worktree']['path']
    (path / 'backend/app/__init__.py').write_text('VALUE = 3\n')
    (path / 'backend/app/new.py').write_text('NEW = True\n')
    frozen = service.freeze(child['id'], actor='alice')
    commit_id = frozen['frozen_commit']
    assert commit_id not in (env['start'], env['head']) and frozen['state'] == 'frozen'
    assert git(path, 'rev-list', '--parents', '-n', '1', commit_id).split()[1:] == [env['start']]
    assert git(path, 'log', '-1', '--format=%an <%ae>') == f'{COORDINATOR[0]} <{COORDINATOR[1]}>'
    assert git(path, 'status', '--porcelain') == ''
    assert git(env['project'], 'rev-parse', 'HEAD') == env['head']
    with pytest.raises(Conflict, match='derive a child'):
        service.freeze(child['id'], actor='alice')
    # Editing after freezing never changes what the frozen candidate runs.
    (path / 'backend/app/__init__.py').write_text('VALUE = 4\n')
    ran = service.run(child['id'], budget=BUDGET, actor='alice', request_key='a1')
    prepared = service.jobs.preparations(tree['task_id'], ran['attempts'][0]['job_id'])[0]['preparation']
    assert prepared['source']['artifact_ref'] == frozen['source_ref']
    assert prepared['source']['provenance']['head_revision'] == commit_id
    grandchild = service.derive(child['id'], note='continue after freeze', actor='alice', request_key='a2')
    assert grandchild['origin'] == 'manual' and grandchild['parent_commit'] == commit_id
    assert git(Path(grandchild['worktree']['path']), 'rev-parse', 'HEAD') == commit_id
    view = service.iteration(tree['id'])
    assert [(c['id'], c['depth']) for c in view['candidates']] == [
        (root['id'], 0), (child['id'], 1), (grandchild['id'], 2), (sibling['id'], 1)]


def test_freeze_rejects_empty_private_and_agent_commits(env):
    tree, root = evaluated_root(env)
    service = env['service']
    child = service.derive(root['id'], note='manual', actor='alice', request_key='m')
    path = Path(child['worktree']['path'])
    with pytest.raises(Conflict, match='no changes'):
        service.freeze(child['id'], actor='alice')
    (path / '.env').write_text('SECRET=1\n')
    with pytest.raises(ValueError, match='Private'):
        service.freeze(child['id'], actor='alice')
    assert git(path, 'rev-parse', 'HEAD') == env['start'] and git(path, 'diff', '--cached', '--name-only') == ''
    (path / '.env').unlink()
    (path / 'backend/app/__init__.py').write_text('VALUE = 5\n')
    commit(path, 'agent commit')
    with pytest.raises(Conflict, match='only the coordinator'):
        service.freeze(child['id'], actor='alice')
    assert service.candidate(child['id'])['status'] == 'ready'


def test_freeze_resumes_its_own_commit_after_a_crash(env, monkeypatch):
    tree, root = evaluated_root(env)
    service = env['service']
    child = service.derive(root['id'], note='manual', actor='alice', request_key='m')
    path = Path(child['worktree']['path'])
    (path / 'backend/app/__init__.py').write_text('VALUE = 6\n')
    original = service._capture_clean
    monkeypatch.setattr(service, '_capture_clean', lambda *a: (_ for _ in ()).throw(OSError('crash')))
    with pytest.raises(OSError):
        service.freeze(child['id'], actor='alice')
    made = git(path, 'rev-parse', 'HEAD')
    assert made != env['start'] and service.candidate(child['id'])['status'] == 'ready'
    monkeypatch.setattr(service, '_capture_clean', original)
    assert service.freeze(child['id'], actor='alice')['frozen_commit'] == made


def test_uncertain_creation_is_reconciled_without_a_second_dispatch(env):
    env['creator'].failure = RuntimeError('lost response')
    tree = iteration(env)
    root = tree['candidates'][0]
    assert root['state'] == 'provisioning' and root['worktree']['status'] == 'unknown'
    env['creator'].failure = None
    recovered = iteration(env)
    assert recovered['candidates'][0]['state'] == 'frozen'
    assert len(env['creator'].dispatched) == 1


def test_integrated_sources_are_recorded_and_must_be_frozen_siblings(env):
    tree, root = evaluated_root(env)
    service = env['service']
    a = service.derive(root['id'], note='A', actor='alice', request_key='a')
    b = service.derive(root['id'], note='B', actor='alice', request_key='b')
    item = dict(candidate_id=b['id'], method='cherry-pick', note='bring B')
    with pytest.raises(ValueError, match='frozen'):
        service.derive(a['id'], note='M', actor='alice', request_key='m0', integrated_from=[item])
    for node, value in ((a, 7), (b, 8)):
        (Path(node['worktree']['path']) / 'backend/app/__init__.py').write_text(f'VALUE = {value}\n')
        service.freeze(node['id'], actor='alice')
    merged = service.derive(a['id'], note='M', actor='alice', request_key='m', integrated_from=[item])
    frozen_b = service.candidate(b['id'])['frozen_commit']
    assert merged['parent_candidate_id'] == a['id']
    assert merged['integrated_from'] == [dict(item, frozen_commit=frozen_b)]


def test_decisions_need_an_evaluated_candidate(env):
    tree = iteration(env)
    with pytest.raises(Conflict):
        env['service'].decide(tree['root_candidate_id'], 'adopted', actor='human')
    root = env['service'].run(tree['root_candidate_id'], budget=BUDGET, actor='alice', request_key='base')
    finish(env, root)
    decided = env['service'].decide(root['id'], 'adopted', actor='human', note='keep')
    assert decided['decision']['decision'] == 'adopted' and decided['state'] == 'evaluated'


def test_implementer_agent_is_created_in_the_candidate_worktree(env):
    from rag_eval.workbench.agent_creations import AgentCreationStore
    tree, root = evaluated_root(env)
    proposal = approved(env, tree, root['id'])
    child = env['service'].derive(root['id'], proposal_id=proposal, actor='alice', request_key='a')
    creations = AgentCreationStore(env['repo'], {env['workspace']['id']: {
        'workspaceId': 'wks-eval', 'profiles': {'sol': {'provider': 'codex/model', 'modeId': 'full-access'}}}})
    with pytest.raises(ValueError, match='candidate'):
        creations.prepare(tree['task_id'], 'sol', actor='a', request_key='x', role='implementer',
                          hypothesis_id=proposal, candidate_id=root['id'])
    intent = creations.prepare(tree['task_id'], 'sol', actor='a', request_key='impl', role='implementer',
                               hypothesis_id=proposal, candidate_id=child['id'])
    assert intent['command']['expectedCwd'] == child['worktree']['path']
    assert intent['command']['workspaceId'] == 'workspace-sn-cand-' + child['id']


def test_runner_rejects_malformed_candidate_associations_before_any_run():
    from rag_eval.notebook_runner import execute
    base = dict(task_id='t', worktree_path='/abs/tree', paseo_workspace_id='w', source_ref='s' * 64)
    candidate = dict(base, iteration_id='a' * 32, candidate_id='b' * 32, attempt=1,
                     frozen_commit='c' * 40, preparation_id='d' * 64)
    for bad in (dict(candidate, attempt=0), dict(candidate, frozen_commit='HEAD'),
                {k: v for k, v in candidate.items() if k != 'attempt'}, dict(candidate, extra=1)):
        with pytest.raises(ValueError, match='Task association'):
            execute(root='/nonexistent', project='/nonexistent', bundle_dir='/nonexistent', run='/nonexistent/run',
                    mode='chunk', partition_id='p', sn_source_ref={'id': 's' * 64}, task_worktree=bad)


def test_legacy_mapping_fills_only_evidenced_fields(tmp_path, monkeypatch):
    from test_workbench_job_worktree_provenance import captured_job
    client, repo, job, args, service, legacy, new, command = captured_job(tmp_path, monkeypatch)
    assert client.post(f"/api/tasks/{job['task_id']}/jobs/{job['job_id']}/run", json=command).status_code == 200
    iterations = IterationService(repo, service, executions=JobExecutionStore(repo))
    report = iterations.migrate_legacy(actor='operator')
    assert report['dry_run'] is True and iterations.iterations() == []
    task = next(item for item in report['tasks'] if item['task_id'] == job['task_id'])
    assert task['plan']['plan_id'] == job['plan_id'] and 'start_commit' in task['unknown']
    # The hypothesis tree came from project HEAD; that is evidence, not a parent edge.
    [found] = task['candidates']
    assert found['observed_creation_base'] == new['base_revision'] and found['executions'] == []
    [execution] = task['unassigned_executions']
    applied = iterations.migrate_legacy(actor='operator', dry_run=False)
    assert applied['dry_run'] is False
    [mapped] = iterations.iterations()
    assert mapped['origin'] == 'legacy' and mapped['start_commit'] is None and mapped['root_candidate_id'] is None
    assert iterations.migrate_legacy(actor='operator', dry_run=False)['tasks'] == []
    tree = iterations.iteration(mapped['id'])
    assert tree['unassigned_executions'] == [execution]
    [legacy_node] = tree['candidates']
    assert legacy_node['state'] == 'legacy' and legacy_node['parent_candidate_id'] is None
    assert set(legacy_node['unknown']) == {'parent', 'parent_commit', 'frozen_commit'}
    root = iterations.record_legacy_candidate(mapped['id'], label='C0', basis='capture of the legacy tree',
                                              actor='operator', execution_ids=[execution])
    assert root['links'][0]['basis'] == dict(kind='manual', basis='capture of the legacy tree')
    child = iterations.assign_legacy_parent(legacy_node['id'], root['id'], basis='receipt base equals C0 head',
                                            actor='operator')
    assert child['parent_candidate_id'] == root['id'] and 'parent' not in child['unknown']
    assert iterations.iteration(mapped['id'])['unassigned_executions'] == []
    with pytest.raises(Conflict):
        iterations.run(root['id'], budget=BUDGET, actor='operator', request_key='x')


def test_legacy_capture_of_a_directorys_clean_start_is_parent_evidence_not_the_candidate(tmp_path, monkeypatch):
    from rag_eval.workbench.hypothesis_worktrees import HypothesisWorktreeService
    from test_workbench_job_preparation_records import environment
    repo, jobs, job, args = environment(tmp_path)
    task = job['task_id']
    initialize_task_worktrees(repo)
    trees = TaskWorktreeService(repo, args['artifact_store'], project_root=tmp_path / 'sn')
    cards = HypothesisStore(repo)
    card = cards.draft(task, CARD, author='analyst')
    cards.decide(task, card['id'], 'approve', actor='human')
    hypothesis_trees = HypothesisWorktreeService(repo, trees, creator=Creator(tmp_path / 'trees'))
    tree = Path(hypothesis_trees.create(task, card['id'], actor='human')['binding']['worktree_path'])
    hypothesis_trees.activate(task, card['id'], actor='human')
    executions = JobExecutionStore(repo)
    monkeypatch.setattr(executions, '_start', lambda record: None)

    def execute(job_record, key):
        prep = jobs.capture_inputs_source(**dict(args, job_id=job_record['job_id'], sn_ref=trees.capture_source(task),
                                                 request_key=key))['preparation']
        spec = dict(argv=['true'], run_dir=str(tmp_path / 'runs' / key), budget=BUDGET,
                    preparation_id=prep['preparation_id'])
        return executions.run(task, job_record['job_id'], 1, execution_spec=spec, actor='alice',
                              request_key=key)['execution_id']

    # The same directory first serves its unchanged start (the baseline), then the change.
    baseline = execute(job, 'clean')
    (tree / 'backend/app/__init__.py').write_text('VALUE = "changed"\n')
    second = jobs.prepare(task, job['plan_id'], job['plan_revision'], job['plan_content_hash'],
                          actor='alice', request_key='second')
    changed = execute(second, 'changed')
    plan = IterationService(repo, trees, executions=executions).legacy_plan(task)
    [found] = plan['candidates']
    assert [run['execution_id'] for run in found['executions']] == [changed]
    assert found['executions'][0]['basis']['kind'] == 'saved_task_capture_after_start'
    assert plan['unassigned_executions'] == [baseline]
    assert plan['unassigned_evidence'][baseline][0]['kind'] == 'captured_from_clean_start_of'


def test_provision_resets_a_stranded_dispatch_marker_and_retries(env):
    """A crash after the dispatch marker but before creator.create() returns must not strand the candidate forever."""
    import sqlite3
    tree, root = evaluated_root(env)
    proposal = approved(env, tree, root['id'])

    class _CrashBeforeCreate:
        def create(self, project_root, branch, base_revision):
            raise RuntimeError('process killed before workspace creation')
        def list(self):
            return []

    env['service'].creator = _CrashBeforeCreate()
    # derive() calls provision(); the crash creator's create() raises,
    # provision() swallows it, and the 'null' dispatch marker stays in DB.
    child = env['service'].derive(root['id'], proposal_id=proposal, actor='alice', request_key='sc')
    child_id = child['id']
    with sqlite3.connect(env['repo'].database) as conn:
        row = conn.execute('SELECT workspace_json FROM candidate_worktrees WHERE candidate_id=?',
                           (child_id,)).fetchone()
    assert row[0] == 'null', f'dispatch marker should be null, got {row[0]!r}'

    # Second provision() with the crash creator: workspace_json is 'null' (not
    # None), so it skips the creation branch and goes to _reconcile(expected=None).
    # list() returns [] → 0 matches → marker reset to NULL.
    env['service'].provision(child_id)
    with sqlite3.connect(env['repo'].database) as conn:
        row = conn.execute('SELECT workspace_json FROM candidate_worktrees WHERE candidate_id=?',
                           (child_id,)).fetchone()
    assert row[0] is None, f'dispatch marker was not reset; workspace_json = {row[0]!r}'

    # Restore the real creator; the next provision() creates the workspace and reconciles.
    env['service'].creator = env['creator']
    recovered = env['service'].provision(child_id)
    assert recovered['worktree']['status'] == 'ready' and recovered['state'] == 'implementing'
