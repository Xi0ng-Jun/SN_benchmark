"""Preparation facts persist independently of intent, with a final Job CAS."""
import copy
import json
import subprocess

import pytest

from rag_eval.artifact_store import ArtifactStore
from rag_eval.bundle_index import install_bundle
from rag_eval.sn_source_capture import capture_sn_source
from rag_eval.workbench.evaluation_jobs import EvaluationJobStore
from rag_eval.workbench.repository import Conflict
from test_workbench_evaluation_plans import setup


def environment(tmp_path):
    repo, _, task, plans, _, command = setup(tmp_path)
    plan = plans.save(**command)
    jobs = EvaluationJobStore(repo)
    job = jobs.prepare(task['id'], plan['plan_id'], plan['revision'], plan['content_hash'],
                       actor='alice', request_key='intent')
    store = ArtifactStore(tmp_path / 'artifacts')
    refs = install_bundle(tmp_path / 'data/bundle', store.root)
    project = tmp_path / 'sn'
    app = project / 'backend/app'
    app.mkdir(parents=True)
    (app / '__init__.py').write_text('VALUE = 1\n')
    subprocess.run(['git', 'init', '-q', str(project)], check=True)
    subprocess.run(['git', '-C', str(project), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(project), '-c', 'user.name=test', '-c',
                    'user.email=test@example.invalid', 'commit', '-qm', 'fixture'], check=True)
    source = capture_sn_source(project, store)
    args = dict(task_id=task['id'], job_id=job['job_id'], expected_state_version=1,
                artifact_store=store, bundle_ref=refs['bundle'], index_ref=refs['index'],
                sn_ref=source.ref, actor='alice', request_key='capture')
    return repo, jobs, job, args


def test_preparation_receipt_survives_cancel_and_artifact_unavailability(tmp_path):
    repo, jobs, original, args = environment(tmp_path)
    record = jobs.capture_inputs_source(**args)
    assert record['preparation']['executable'] is False
    assert jobs.get(original['task_id'], original['job_id']) == original
    jobs.cancel(original['task_id'], original['job_id'], 1, actor='alice', request_key='cancel')
    args['artifact_store'].root.rename(tmp_path / 'offline-artifacts')
    assert jobs.capture_inputs_source(**args) == record
    assert jobs.preparation_receipt(original['task_id'], actor='alice', request_key='capture') == record
    assert jobs.preparations(original['task_id'], original['job_id']) == [record]
    assert jobs.receipt(original['task_id'], actor='alice', request_key='intent') == original
    with pytest.raises(Conflict):
        jobs.capture_inputs_source(**dict(args, sn_ref=dict(args['sn_ref'], id='0' * 64)))


def test_cancellation_during_artifact_read_prevents_preparation_commit(tmp_path, monkeypatch):
    from rag_eval.workbench import job_preparation
    repo, jobs, original, args = environment(tmp_path)
    build = job_preparation.build_job_preparation

    def cancel_during_build(*a, **kw):
        result = build(*a, **kw)
        jobs.cancel(original['task_id'], original['job_id'], 1, actor='alice', request_key='cancel')
        return result

    monkeypatch.setattr(job_preparation, 'build_job_preparation', cancel_during_build)
    with pytest.raises(Conflict):
        jobs.capture_inputs_source(**args)
    assert jobs.preparation_receipt(original['task_id'], actor='alice', request_key='capture') is None
    assert jobs.preparations(original['task_id'], original['job_id']) == []


def test_corrupt_preparation_receipt_is_rejected_without_artifact_io(tmp_path):
    repo, jobs, original, args = environment(tmp_path)
    saved = jobs.capture_inputs_source(**args)
    broken = copy.deepcopy(saved)
    broken['preparation']['executable'] = True
    with repo.connect(write=True) as conn:
        conn.execute('UPDATE requests SET receipt=? WHERE request_key=?',
                     (json.dumps(broken), 'capture'))
    with pytest.raises(ValueError):
        jobs.preparation_receipt(original['task_id'], actor='alice', request_key='capture')


@pytest.mark.parametrize('read', ['replay', 'receipt'])
def test_valid_preparation_for_other_source_cannot_replace_command_receipt(tmp_path, read):
    from rag_eval.workbench.job_preparation import build_job_preparation
    repo, jobs, original, args = environment(tmp_path)
    saved = jobs.capture_inputs_source(**args)
    (tmp_path / 'sn/backend/app/__init__.py').write_text('VALUE = 2\n')
    other = capture_sn_source(tmp_path / 'sn', args['artifact_store'])
    saved['preparation'] = build_job_preparation(original, args['artifact_store'],
        bundle_ref=args['bundle_ref'], index_ref=args['index_ref'], sn_ref=other.ref)
    with repo.connect(write=True) as conn:
        conn.execute('UPDATE requests SET receipt=? WHERE request_key=?',
                     (json.dumps(saved), 'capture'))
    args['artifact_store'].root.rename(tmp_path / 'offline-artifacts')
    with pytest.raises(ValueError):
        if read == 'replay':
            jobs.capture_inputs_source(**args)
        else:
            jobs.preparation_receipt(original['task_id'], actor='alice', request_key='capture')
