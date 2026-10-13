"""Draft revisions, receipts and global CAS share one SQLite transaction."""
import copy
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from rag_eval.identity import fingerprint
from rag_eval.workbench.evaluation_plans import PlanDraftStore
from rag_eval.workbench.plan_inputs import NotebookPlanInputs
from rag_eval.workbench.repository import Conflict, WorkbenchRepository
from test_workbench_plan_inputs import input_fixture, public_identity


def setup(tmp_path):
    inputs, bundle, bundle_id = input_fixture(tmp_path / 'data')
    repo = WorkbenchRepository(tmp_path / 'state')
    workspace = repo.register_workspace('SN', tmp_path / 'workspace')
    task = repo.create_task(workspace['id'], 'Plan', '', request_key='task')
    command = dict(task_id=task['id'], input_id='registered', expected_bundle_id=bundle_id,
                   case_ids=['qasper:q'], title='  Review QASPER  ', actor='alice',
                   request_key=' save ', mode='chunk')
    return repo, workspace, task, PlanDraftStore(repo, inputs), bundle, command


def rows(repo):
    with repo.connect() as conn:
        return tuple([tuple(r) for r in conn.execute('SELECT * FROM ' + table + ' ORDER BY rowid')]
                     for table in ('history', 'requests'))


def test_save_freezes_complete_draft_content_and_server_owned_identity(tmp_path):
    repo, workspace, task, store, bundle, command = setup(tmp_path)
    saved = store.save(**command)
    assert saved['state'] == 'draft' and saved['execution'] is False
    assert saved['parent_revision'] is None
    assert saved['revision'] and saved['plan_id'] and saved['revision'] != saved['plan_id']
    assert saved['task_id'] == task['id'] and saved['workspace_id'] == workspace['id']
    assert saved['title'] == '  Review QASPER  '
    expected_content = dict(
        format='sn-workbench-evaluation-plan-draft-v1', title='  Review QASPER  ',
        input=public_identity(bundle, command['expected_bundle_id']),
        selection=dict(kind='explicit', case_ids=['qasper:q'],
                       partition_ids=[bundle['partitions'][0]['partition_id']],
                       selection_hash=fingerprint(dict(kind='explicit', case_ids=['qasper:q'],
                           partition_ids=[bundle['partitions'][0]['partition_id']]))),
        execution_intent=dict(entrypoint='scripts/run_notebook_benchmarks.py', mode='chunk',
                              request_revision='notebook-request-v3', model_services_ref=None,
                              official_scorer_ref=None),
        source_intent=dict(kind='task-workspace-at-freeze', workspace_id=workspace['id'],
                           binding='not_captured'))
    assert saved['content'] == expected_content
    assert saved['content_hash'] == fingerprint(expected_content)
    assert store.list(task['id']) == [saved]
    assert store.get(task['id'], saved['plan_id']) == saved
    assert store.get(task['id'], saved['plan_id'], saved['revision']) == saved
    assert store.receipt(task['id'], actor='alice', request_key=' save ') == saved
    event = repo.task(task['id'])['history'][-1]
    assert (event['kind'], event['object_id'], event['content']) == ('plan.draft.saved', saved['plan_id'], saved)
    assert task['id'] and repo.task(task['id'])['results'] == []  # No result registration prerequisite.


def test_old_receipt_and_revisions_survive_source_change_loss_and_reopen(tmp_path):
    repo, _, task, store, _, command = setup(tmp_path)
    saved = store.save(**command)
    source = tmp_path / 'data/bundle'
    (source / 'raw-data').write_text('{}')
    assert store.save(**command) == saved
    with pytest.raises(ValueError):
        store.save(**dict(command, request_key='new'))
    source.rename(tmp_path / 'deleted-synthetic-bundle')
    reopened_repo = WorkbenchRepository(tmp_path / 'state')
    reopened = PlanDraftStore(reopened_repo, NotebookPlanInputs({}))
    assert reopened.save(**command) == saved
    assert reopened.receipt(task['id'], actor='alice', request_key=' save ') == saved
    assert reopened.get(task['id'], saved['plan_id'], saved['revision']) == saved
    assert reopened.list(task['id']) == [saved]
    for change in (dict(title='Review QASPER'), dict(case_ids=['qasper:q2']),
                   dict(expected_bundle_id='0' * 64), dict(mode='reasoning'),
                   dict(plan_id=saved['plan_id'], base_revision=saved['revision'])):
        with pytest.raises(Conflict):
            reopened.save(**dict(command, **change))
    assert rows(repo) == rows(reopened_repo)
    assert len(repo.task(task['id'])['history']) == 2


def test_original_selection_order_and_title_are_part_of_retry_identity(tmp_path):
    repo, _, task, store, _, command = setup(tmp_path)
    command['case_ids'] = ['qasper:q3', 'qasper:q']
    saved = store.save(**command)
    assert saved['content']['selection']['case_ids'] == ['qasper:q', 'qasper:q3']
    before = rows(repo)
    with pytest.raises(Conflict):
        store.save(**dict(command, case_ids=['qasper:q', 'qasper:q3']))
    with pytest.raises(Conflict):
        store.save(**dict(command, title=command['title'].strip()))
    assert rows(repo) == before
    second = store.save(**dict(command, request_key='save'))
    assert second['plan_id'] != saved['plan_id']
    assert second['content_hash'] == saved['content_hash']
    assert store.receipt(task['id'], actor='alice', request_key='save') == second
    assert store.receipt(task['id'], actor='alice', request_key=' save ') == saved
    assert store.receipt(task['id'], actor='bob', request_key=' save ') is None


def test_append_and_global_cross_actor_cas_keep_fixed_old_revision(tmp_path):
    repo, _, task, store, _, command = setup(tmp_path)
    first = store.save(**command)
    append = dict(command, plan_id=first['plan_id'], base_revision=first['revision'],
                  request_key='second', actor='bob', title='Append', mode='reasoning')
    second = store.save(**append)
    assert second['parent_revision'] == first['revision']
    assert second['revision'] != first['revision']
    before = rows(repo)
    with pytest.raises(Conflict):
        store.save(**dict(append, request_key='stale', actor='alice'))
    assert rows(repo) == before
    assert store.get(task['id'], first['plan_id'], first['revision']) == first
    assert store.get(task['id'], first['plan_id']) == second
    assert store.list(task['id']) == [second]
    assert store.save(**command) == first  # Head has changed, exact prior still wins.
    restored = PlanDraftStore(WorkbenchRepository(tmp_path / 'state'), NotebookPlanInputs({}))
    assert restored.get(task['id'], first['plan_id'], first['revision']) == first
    assert restored.list(task['id']) == [second]

    def append_one(actor):
        try:
            return store.save(**dict(append, request_key='race', actor=actor,
                                     base_revision=second['revision']))
        except Conflict:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(append_one, ['carol', 'dave']))
    assert sum(value is not None for value in outcomes) == 1
    assert len([e for e in repo.task(task['id'])['history'] if e['kind'] == 'plan.draft.saved']) == 3
    with repo.connect() as conn:
        assert conn.execute("SELECT count(*) FROM requests WHERE scope LIKE 'plan.draft.save:%'").fetchone()[0] == 3


def test_concurrent_same_request_publishes_one_revision(tmp_path):
    repo, _, task, store, _, command = setup(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(lambda _: store.save(**command), range(2)))
    assert first == second
    assert len(repo.task(task['id'])['history']) == 2


@pytest.mark.parametrize('change', [dict(case_ids=[]), dict(case_ids=['unknown']),
    dict(case_ids=['qasper:q', 'qasper:q']), dict(case_ids=['qasper:q ']), dict(case_ids=[2]),
    dict(expected_bundle_id=None), dict(expected_bundle_id='bad'),
    dict(expected_bundle_id='0' * 64), dict(mode='agent'), dict(title=' '),
    dict(request_key=' '), dict(task_id='unknown'), dict(base_revision='unknown'),
    dict(plan_id='unknown', base_revision='unknown')])
def test_invalid_new_saves_leave_no_history_or_receipt(tmp_path, change):
    repo, _, _, store, _, command = setup(tmp_path)
    before = rows(repo)
    with pytest.raises(ValueError):
        store.save(**dict(command, **change))
    assert rows(repo) == before


@pytest.mark.parametrize('extra', ['workspace_id', 'content', 'revision', 'parent_revision',
                                    'content_hash', 'execution', 'partition_ids', 'input'])
def test_command_cannot_override_server_fields(tmp_path, extra):
    repo, _, _, store, _, command = setup(tmp_path)
    before = rows(repo)
    with pytest.raises(TypeError):
        store.save(**dict(command, **{extra: 'forged'}))
    assert rows(repo) == before


def test_plan_and_revision_cannot_cross_tasks_or_workspace_ownership(tmp_path):
    repo, workspace, task, store, _, command = setup(tmp_path)
    saved = store.save(**command)
    other_workspace = repo.register_workspace('Other', tmp_path / 'other')
    other_task = repo.create_task(other_workspace['id'], 'Other', '', request_key='other')
    before = rows(repo)
    with pytest.raises(ValueError):
        store.save(**dict(command, task_id=other_task['id'], plan_id=saved['plan_id'],
                          base_revision=saved['revision']))
    with pytest.raises(ValueError):
        store.get(other_task['id'], saved['plan_id'])
    with pytest.raises(ValueError):
        store.get(task['id'], saved['plan_id'], 'unknown')
    assert store.list(other_task['id']) == []
    assert rows(repo) == before


def test_event_and_receipt_roll_back_together_on_write_failure(tmp_path, monkeypatch):
    repo, _, _, store, _, command = setup(tmp_path)
    before = rows(repo)
    original = repo._receipt
    def fail(*args):
        raise RuntimeError('simulated SQLite write interruption')
    monkeypatch.setattr(repo, '_receipt', fail)
    with pytest.raises(RuntimeError):
        store.save(**command)
    assert rows(repo) == before
    monkeypatch.setattr(repo, '_receipt', original)
    assert store.save(**command)['state'] == 'draft'


@pytest.mark.parametrize('where', ['history', 'requests'])
def test_unknown_saved_schema_is_rejected_without_interpreting_or_rewriting(tmp_path, where):
    repo, _, task, store, _, command = setup(tmp_path)
    saved = store.save(**command)
    altered = copy.deepcopy(saved)
    altered['content']['format'] = 'sn-workbench-evaluation-plan-draft-future'
    altered['content_hash'] = fingerprint(altered['content'])
    with repo.connect(write=True) as conn:
        if where == 'history':
            conn.execute("UPDATE history SET content=? WHERE kind='plan.draft.saved'", (json.dumps(altered),))
        else:
            conn.execute("UPDATE requests SET receipt=? WHERE scope LIKE 'plan.draft.save:%'", (json.dumps(altered),))
    before = rows(repo)
    if where == 'history':
        for read in (lambda: store.list(task['id']), lambda: store.get(task['id'], saved['plan_id']),
                     lambda: store.save(**dict(command, plan_id=saved['plan_id'],
                         base_revision=saved['revision'], request_key='append'))):
            with pytest.raises(ValueError, match='schema'):
                read()
    else:
        with pytest.raises(ValueError, match='schema'):
            store.receipt(task['id'], actor='alice', request_key=' save ')
        with pytest.raises(ValueError, match='schema'):
            store.save(**command)
    assert rows(repo) == before


def test_missing_source_new_key_is_sanitized_and_writes_nothing(tmp_path):
    repo, _, _, store, _, command = setup(tmp_path)
    store.inputs = NotebookPlanInputs({'registered': tmp_path / 'PRIVATE-gone'})
    before = rows(repo)
    with pytest.raises(FileNotFoundError) as exc:
        store.save(**command)
    assert str(tmp_path) not in str(exc.value) and exc.value.filename is None
    assert rows(repo) == before


@pytest.mark.parametrize('change', [dict(mode=['chunk']), dict(mode={'mode': 'chunk'}),
                                    dict(input_id=[]), dict(title=1)])
def test_malformed_json_choices_are_value_errors_with_zero_residue(tmp_path, change):
    repo, _, _, store, _, command = setup(tmp_path)
    before = rows(repo)
    with pytest.raises(ValueError):
        store.save(**dict(command, **change))
    assert rows(repo) == before


def test_prior_wins_before_new_validation_or_any_external_source_read(tmp_path, monkeypatch):
    repo, _, task, store, _, command = setup(tmp_path)
    saved = store.save(**command)
    before = rows(repo)
    def unreadable(*args, **kwargs):
        raise AssertionError('prior lookup must precede source access')
    monkeypatch.setattr(store.inputs, 'canonical', unreadable)
    assert store.save(**command) == saved
    for change in (dict(case_ids=[]), dict(expected_bundle_id=None), dict(mode='agent'),
                   dict(title=' '), dict(plan_id=saved['plan_id'], base_revision=None)):
        with pytest.raises(Conflict):
            store.save(**dict(command, **change))
    assert store.receipt(task['id'], actor='alice', request_key='missing') is None
    assert rows(repo) == before


def test_returned_objects_are_detached_and_hash_is_independent_of_actor_revision_and_time(tmp_path):
    _, _, task, store, _, command = setup(tmp_path)
    first = store.save(**command)
    old = copy.deepcopy(first)
    second = store.save(**dict(command, actor='bob'))
    assert first['content_hash'] == second['content_hash']
    assert first['revision'] != second['revision'] and first['plan_id'] != second['plan_id']
    first['content']['selection']['case_ids'].append('forged')
    assert store.get(task['id'], old['plan_id'], old['revision']) == old
    replay = store.receipt(task['id'], actor='alice', request_key=' save ')
    replay['content']['title'] = 'forged'
    assert store.save(**command) == old


def test_valid_source_replacement_conflicts_for_new_save_but_recovers_original_receipt(tmp_path):
    repo, _, task, store, _, command = setup(tmp_path)
    first = store.save(**command)
    from rag_eval.workbench.plan_inputs import NotebookPlanInputs
    from test_notebook_data_v3 import freeze, qasper_raw
    raw = qasper_raw()
    raw['p']['qas'][0]['answers'][0]['answer']['extractive_spans'] = ['REPLACED-GOLD']
    changed = freeze(tmp_path / 'replacement', 'qasper', raw)
    store.inputs = NotebookPlanInputs({'registered': tmp_path / 'replacement/bundle'})
    before = rows(repo)
    assert store.save(**command) == first
    with pytest.raises(Conflict):
        store.save(**dict(command, request_key='new-source'))
    assert rows(repo) == before
    next_revision = store.save(**dict(command, request_key='accept-new-source',
        plan_id=first['plan_id'], base_revision=first['revision'],
        expected_bundle_id=fingerprint(changed['manifest'])))
    assert next_revision['content']['input']['bundle_id'] != first['content']['input']['bundle_id']
    assert 'REPLACED-GOLD' not in json.dumps(next_revision)
    assert store.get(task['id'], first['plan_id'], first['revision']) == first


@pytest.mark.parametrize('revision', [
    '/private/owner-only/REVISION_PATH_SECRET/release.txt',
    'https://fixture-user:REVISION_CREDENTIAL_SECRET@example.invalid/release?token=QUERY_SECRET',
], ids=['private-absolute-path', 'credential-url'])
def test_revision_secrets_never_enter_history_receipt_or_reopened_plan(tmp_path, revision):
    from test_workbench_plan_inputs import revision_fixture
    repo, _, task, store, _, command = setup(tmp_path)
    inputs, bundle, bundle_id = revision_fixture(tmp_path / 'private-data', revision)
    store.inputs = inputs
    command['expected_bundle_id'] = bundle_id
    source_root = tmp_path / 'private-data'
    before = {p.relative_to(source_root): p.read_bytes() for p in source_root.rglob('*') if p.is_file()}
    first = store.save(**command)
    second = store.save(**dict(command, plan_id=first['plan_id'], base_revision=first['revision'],
                               request_key='append-private-revision', mode='reasoning'))
    restored = PlanDraftStore(WorkbenchRepository(tmp_path / 'state'), NotebookPlanInputs({}))
    responses = [first, second, restored.list(task['id']),
                 restored.get(task['id'], first['plan_id'], first['revision']),
                 restored.get(task['id'], first['plan_id']),
                 restored.receipt(task['id'], actor='alice', request_key=' save '),
                 restored.save(**command)]
    encoded = json.dumps(responses) + json.dumps(rows(repo))
    assert revision not in encoded
    for marker in ('REVISION_PATH_SECRET', 'REVISION_CREDENTIAL_SECRET', 'QUERY_SECRET'):
        assert marker not in encoded
    for response in (first, second):
        assert response['content']['input']['source_revision'] == 'sha256:' + fingerprint(revision)
        assert response['content']['input']['bundle_id'] == fingerprint(bundle['manifest'])
    assert {p.relative_to(source_root): p.read_bytes() for p in source_root.rglob('*') if p.is_file()} == before


@pytest.mark.parametrize('where', ['history', 'requests'])
@pytest.mark.parametrize('raw_revision', [
    'fixture', '/private/REVISION_PATH_SECRET',
    'https://user:REVISION_CREDENTIAL_SECRET@example.invalid/revision',
    'sha256:' + 'A' * 64, 'sha256:' + 'a' * 63, 'sha256:' + 'a' * 64 + '\n',
])
def test_saved_revision_must_use_exact_public_identity_without_rewriting(tmp_path, where, raw_revision):
    repo, _, task, store, _, command = setup(tmp_path)
    original = store.save(**command)
    altered = copy.deepcopy(original)
    altered['content']['input']['source_revision'] = raw_revision
    altered['content_hash'] = fingerprint(altered['content'])
    with repo.connect(write=True) as conn:
        if where == 'history':
            conn.execute("UPDATE history SET content=? WHERE kind='plan.draft.saved'", (json.dumps(altered),))
        else:
            conn.execute("UPDATE requests SET receipt=? WHERE scope LIKE 'plan.draft.save:%'", (json.dumps(altered),))
    before = rows(repo)
    if where == 'history':
        reads = [lambda: store.list(task['id']),
                 lambda: store.get(task['id'], original['plan_id'], original['revision']),
                 lambda: store.save(**dict(command, plan_id=original['plan_id'],
                     base_revision=original['revision'], request_key='append-raw-revision'))]
    else:
        reads = [lambda: store.receipt(task['id'], actor='alice', request_key=' save '),
                 lambda: store.save(**command)]
    for read in reads:
        with pytest.raises(ValueError, match='schema') as exc:
            read()
        assert raw_revision not in str(exc.value)
    assert rows(repo) == before
