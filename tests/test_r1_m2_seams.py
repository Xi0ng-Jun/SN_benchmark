"""Shared entry points preserve admission, worktree scope and human decisions."""
import json
from copy import deepcopy
from pathlib import Path

import pytest

from rag_eval.identity import fingerprint
from test_task_worktree import setup


def test_creation_and_session_use_bound_task_scope(setup):
    from rag_eval.workbench.agent_creations import AgentCreationStore
    from rag_eval.workbench.sessions import TaskSessionReader
    project, tree, repo, task, store, service = setup
    service.bind(task, tree, paseo_workspace_id='wks-task')
    workspace = repo.task(task)['workspace_id']
    bindings = {workspace: {'workspaceId': 'wks-eval', 'profiles': {
        'sol': {'provider': 'codex/model', 'modeId': 'full-access'}}}}
    intent = AgentCreationStore(repo, bindings).prepare(task, 'sol', actor='a', request_key='create')
    assert intent['command']['workspaceId'] == 'wks-task'
    assert intent['command']['expectedCwd'] == str(tree)
    session = repo.attach_session(task, 'agent', actor='a', request_key='session')
    assert TaskSessionReader(repo, None).scope(task, session['id'])[1] == str(tree)
    from rag_eval.workbench.agent_messages import AgentMessageStore
    agent = '38847573-1b4c-45f4-9568-469f37948f5d'
    linked = repo.attach_session(task, agent, actor='a', request_key='message-session')
    message = AgentMessageStore(repo, workspace_ids=[workspace]).prepare(
        task, linked['id'], 'hello', [], 'steer', actor='a', request_key='message')
    # Messages must target the same cwd the Agent was created in.
    assert message['command']['expectedCwd'] == str(tree)


def test_registered_run_http_is_authenticated_and_never_accepts_paths(tmp_path):
    from fastapi.testclient import TestClient
    from rag_eval.workbench.api import create_app
    from test_run_views import RUN, SCORES
    config = {'workspace': {'title': 'test', 'location': str(tmp_path)},
              'run_views': {'runs': [{'id': 'e5', 'run_dir': str(RUN), 'scores': str(SCORES)}]}}
    client = TestClient(create_app(tmp_path / 'state', config, access_token='fixture'))
    assert client.get('/api/run-views/runs').status_code == 401
    headers = {'Authorization': 'Bearer fixture'}
    assert client.get('/api/run-views/runs', headers=headers).json()['runs'][0]['id'] == 'e5'
    assert client.get('/api/run-views/runs/unknown', headers=headers).status_code == 404
    assert client.get('/api/run-views/runs/e5/case?case_id=unknown', headers=headers).status_code == 404


def test_run_views_uses_admitted_flips_and_rejects_forged_score(tmp_path):
    from rag_eval.workbench.run_views import RunEntry, RunViews
    from test_run_views import RUN, SCORES
    views = RunViews([RunEntry('e5', RUN, SCORES)])
    flips = views.flips('e5', 'e5')
    assert flips['kind'] == 'paired' and flips['admission'] == 'admitted'
    assert flips['counts']['same'] == 1
    forged = json.loads(SCORES.read_text())
    forged['metrics']['answer_f1'] = 1.0
    (tmp_path / 'scores.json').write_text(json.dumps(forged))
    # A copied answer sheet cannot lend its official label to altered scores.
    (tmp_path / 'submission.json').write_bytes((SCORES.parent / 'submission.json').read_bytes())
    other = RunViews([RunEntry('forged', RUN, tmp_path / 'scores.json')])
    assert other.run('forged')['official']['status'] == 'mismatch'


def test_sn_comparison_discloses_configuration_without_blocking(tmp_path):
    from test_benchmark_comparison import examples, rehash, save
    from rag_eval.benchmark_submission import build_submission
    from rag_eval.benchmark_official import prepare_inputs
    from rag_eval.benchmark_comparison import compare_submissions, _markdown
    from rag_eval.feature_identity import build_feature_identity
    bundle, entries, submissions, scores = examples(tmp_path)
    for i, (submission, score) in enumerate(zip(submissions, scores)):
        method = deepcopy(submission['method'])
        method['kind'] = 'sn'
        method['configuration']['identity'] = {
            'source': bundle['manifest'], 'runtime_settings': 'a' * 64,
            'product_services': ('b' if i else 'c') * 64,
            'feature_set': build_feature_identity({'overrides': {'LLM_CACHE_ENABLED': 'false'}})}
        changed = build_submission(bundle, method=method, predictions=submission['predictions'])
        score.update(method=method, method_id=changed['method_id'], submission_id=fingerprint(changed),
                     prepared_id=fingerprint(prepare_inputs(bundle, changed)))
        rehash(score); save(entries[i]['submission'], changed); save(entries[i]['scores'], score)
    report = compare_submissions(tmp_path / 'bundle', entries)
    disclosure, = report['identity_disclosures']
    assert disclosure['comparable'] and 'configuration' in disclosure['disclosure_differences']
    assert disclosure['unverifiable']['remote_models']['comparison'] == 'not_compared'
    assert '披露差异' in _markdown(report)
    assert report['report_id'] == fingerprint({k: v for k, v in report.items() if k != 'report_id'})


def test_same_method_attempts_only_allowed_by_flip_entry(tmp_path):
    from test_flip_report import runs
    from test_benchmark_comparison import rehash, save
    from rag_eval.benchmark_submission import build_submission
    from rag_eval.benchmark_official import prepare_inputs
    from rag_eval.notebook_bundle import load_bundle
    from rag_eval.flip_report import compare_runs_flips
    from rag_eval.benchmark_comparison import compare_submissions
    paths, scores = runs(tmp_path)
    bundle = load_bundle(tmp_path / 'bundle')
    a = json.loads((paths[0] / 'submission.json').read_text())
    b = json.loads((paths[1] / 'submission.json').read_text())
    b['predictions'][0]['prediction'] = 'Different attempt'
    b = build_submission(bundle, method=a['method'], predictions=b['predictions'])
    scores[1].update(method=b['method'], method_id=b['method_id'], submission_id=fingerprint(b),
                     prepared_id=fingerprint(prepare_inputs(bundle, b)))
    rehash(scores[1]); save(paths[1] / 'submission.json', b); save(paths[1] / 'scores.json', scores[1])
    (paths[1] / 'outputs.jsonl').write_text(''.join(json.dumps(p) + '\n' for p in b['predictions']))
    with pytest.raises(ValueError, match='Duplicate'):
        compare_submissions(tmp_path / 'bundle', [dict(submission=p / 'submission.json', scores=p / 'scores.json') for p in paths])
    report = compare_runs_flips(*paths, bundle_directory=tmp_path / 'bundle')
    assert report['admission']['status'] == 'admitted'
    assert len(report['cases']) == 2


def test_same_head_and_capture_do_not_imply_task_run_association(tmp_path):
    from rag_eval.workbench.run_views import RunEntry, RunViews
    from test_run_views import RUN, SCORES
    manifest = json.loads((RUN / 'manifest.json').read_text())
    binding = {'task_id': 't', 'base_revision': manifest['identity']['code']['head_revision'],
               'start_snapshot': {'source_ref': {'id': '81a682e4e65f66b5f1a69c3b18bce6d637a6dbb6c04605676fe357b182599a5a'}}}
    views = RunViews([RunEntry('e5', RUN, SCORES)])
    assert views._runs_for_binding(binding) == []
    associated = RunViews([RunEntry('e5', RUN, SCORES, task_id='t')])
    assert [r['id'] for r in associated._runs_for_binding(binding)] == ['e5']


def test_worktree_cli_produces_capture_and_binding_without_launch(setup, tmp_path):
    from rag_eval.workbench.cli import main
    from unittest.mock import patch
    project, tree, repo, task, store, service = setup
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'workspace': {'location': str(tmp_path)},
        'task_worktrees': {'project_root': str(project), 'artifact_root': str(store.root),
            'model_config': str(tmp_path / 'private.toml'),
            'registrations': [{'id': 'exp', 'path': str(tree), 'paseo_workspace_id': 'wks-task'}]}}))
    with patch('sys.argv', ['workbench', '--state', str(repo.root), 'worktree-bind', '--config', str(config), '--task', task, '--worktree', 'exp']):
        main()
    assert service.binding(task)['worktree_path'] == str(tree)
    argv = service.run_argv(task, model_config=tmp_path / 'private.toml')
    association = json.loads(Path(argv[argv.index('--task-worktree-ref') + 1]).read_text())
    assert association['task_id'] == task
    assert association['worktree_path'] == str(tree)
    ref = json.loads(Path(argv[argv.index('--sn-source-ref') + 1]).read_text())
    assert association['source_ref'] == ref['id']


def test_formal_reports_and_explorer_refuse_oracle(tmp_path):
    from test_oracle_track import fixture
    from rag_eval.oracle_track import execute_oracle_track
    from rag_eval.run_report import write_report
    from rag_eval.experiment_aggregation import aggregate_runs
    product, evidence, _ = fixture()
    source = tmp_path / 'source'; source.mkdir()
    (source / 'product-bundle.json').write_text(json.dumps(product))
    diagnostic = tmp_path / 'diagnostic'
    execute_oracle_track(source, evidence, diagnostic, generate=lambda r: {'status': 'success', 'prediction': 'x'})
    for call in (lambda: write_report([diagnostic], tmp_path / 'report'), lambda: aggregate_runs([diagnostic])):
        with pytest.raises(ValueError, match='diagnostic'):
            call()


def test_oracle_runner_calls_only_native_chunk_synthesis(tmp_path, monkeypatch):
    from types import ModuleType, SimpleNamespace
    from test_oracle_track import fixture
    from rag_eval.notebook_runner import execute_oracle
    product, evidence, _ = fixture()
    source = tmp_path / 'source'; source.mkdir()
    (source / 'product-bundle.json').write_text(json.dumps(product))
    module = ModuleType('app.domain.retrieval')
    module.RetrievedChunk = lambda **kwargs: SimpleNamespace(**kwargs)
    monkeypatch.setitem(__import__('sys').modules, 'app.domain.retrieval', module)
    class Synthesis:
        def _answer_chunks(self, question, chunks, **kwargs):
            assert [c.text for c in chunks] == ['Birds fly.']
            assert kwargs['memory_hits'] == [] and kwargs['history'] == ''
            kwargs['baseline_sink'].update(context_block='k1: Birds fly.', id_map={'k1': {'object_id': chunks[0].chunk_id}}, ordered_handles=('k1',))
            return 'Birds [k1]', True, []
        def retrieve(self, *args):
            raise AssertionError('Oracle must bypass retrieval')
    result = execute_oracle(source_run=source, gold_evidence=evidence, run=tmp_path / 'oracle',
                            synthesis_service=Synthesis(), mode='chunk')
    assert result['phase'] == 'finished'
    row = json.loads((tmp_path / 'oracle/outputs.jsonl').read_text())
    assert row['prediction'] == 'Birds [k1]'
    assert row['captures'][0]['context_block'] == 'k1: Birds fly.'
    with pytest.raises(ValueError, match='chunk'):
        execute_oracle(source_run=source, gold_evidence=evidence, run=tmp_path / 'reasoning', synthesis_service=Synthesis(), mode='reasoning')
