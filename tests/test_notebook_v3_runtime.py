"""SN generation sees public inputs; labels are joined only after generation."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
from types import ModuleType, SimpleNamespace

import pytest

from rag_eval.notebook_bundle import partition_bundle
from rag_eval.notebook_data import adapt
from rag_eval.notebook_runner import execute, predictions
from rag_eval.notebook_scoring import metric_specs, score_case
from rag_eval.starter_report import load_run
from test_notebook_data_v3 import DATA_V3, REQUEST_V3, freeze, news_raw, qasper_raw


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = {'references', 'expected_answer', 'gold_document_ids', 'gold', 'raw_annotations'}


@pytest.mark.parametrize('suite,task', [('qasper', 'qa'), ('multihop_rag', 'inference_query'),
                                      ('alce', 'asqa'), ('alce', 'qampari'), ('alce', 'eli5'), ('qmsum', 'general')])
def test_v3_plans_existing_sn_adaptation_scores_for_every_suite(suite, task):
    specs = metric_specs(dict(suite=suite, task=task, adaptation_revision=DATA_V3))
    assert any(spec['metric_role'] == 'primary' for spec in specs)
    assert all(spec['scorer'].startswith('product.notebook.') for spec in specs)


def test_v3_context_diagnostics_keep_whitespace_and_empty_turn_semantics():
    raw = qasper_raw()
    raw['p']['qas'][0]['answers'][0]['answer']['evidence'] = ['Birds\n  fly.']
    case = adapt('qasper', raw, adaptation_revision=DATA_V3)['cases'][0]
    observation = dict(status='success', answer='Birds', context_supported=True,
                       retrieval_context=['Birds fly.'], retrieved_document_ids=case['material_document_ids'])
    scorer = 'product.notebook.qasper_context_paragraph_f1_whitespace_v2'
    assert score_case(case, observation, scorer)['score'] == 1
    raw = [dict(meeting_transcripts=[dict(speaker='A', content=''), dict(speaker='B', content='Done.')],
                general_query_list=[], specific_query_list=[dict(query='Decision?', answer='Done.', relevant_text_span=[['0', '1']])])]
    case = adapt('qmsum', raw, adaptation_revision=DATA_V3)['cases'][0]
    observation.update(answer='Done.', retrieval_context=['Done.'], retrieved_document_ids=case['material_document_ids'])
    result = score_case(case, observation, 'product.notebook.qmsum_context_nonempty_turn_recall_v2')
    assert result['score'] == 1
    assert result['details']['excluded_empty_turn_ids'] == [0]


@pytest.fixture
def native_boundary(tmp_path, monkeypatch):
    """Replace external SN/model infrastructure, retaining real capture and SQL checks."""
    from rag_eval import benchmark_runtime, starter_runtime

    class Logger:
        def log(self, record):
            pass

    logging = ModuleType('app.core.llm_logging')
    logging.LLMInteractionLogger = Logger
    monkeypatch.setitem(sys.modules, logging.__name__, logging)
    request_types = ModuleType('app.models.ask')
    request_types.AskRequest = lambda **values: SimpleNamespace(**values)
    request_types.AskIntentConfirmation = lambda **values: SimpleNamespace(**values)
    monkeypatch.setitem(sys.modules, request_types.__name__, request_types)
    observed_requests = []

    class Service:
        def _answer_chunks(self, question, *, baseline_sink=None):
            baseline_sink.update(context_block='k1: Alpha fact.',
                                 id_map={'k1': {'object_id': 'chunk-0', 'source_id': 'source-0'}})
            return 'Alpha [k1]', []

    class Repository:
        def __init__(self, settings):
            self.db_path = settings.db_path
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self.connection = sqlite3.connect(self.db_path)
            self.connection.executescript('''
                CREATE TABLE answers(id TEXT, notebook_id TEXT, question TEXT, payload TEXT);
                CREATE TABLE chunks(id TEXT, source_id TEXT);
                CREATE TABLE source_elements(id TEXT, source_id TEXT);
            ''')
            self._runtime = SimpleNamespace(ask_component=Service())

        def _connect(self):
            return self.connection

        def close_local(self):
            pass

        def close(self):
            self.connection.close()

        def preview_reasoning_intent(self, notebook, question, history):
            assert history == ''
            return SimpleNamespace(needs_clarification=False, resolved_question=question,
                                   model_dump=lambda **_: dict(needs_clarification=False, resolved_question=question))

        def ask(self, notebook, payload):
            assert not FORBIDDEN.intersection(vars(payload))
            assert 'SECRET-GOLD' not in payload.question
            if payload.mode == 'reasoning':
                assert payload.intent.answers == []
            observed_requests.append(deepcopy(vars(payload)))
            answer, _ = self._runtime.ask_component._answer_chunks(payload.question)
            response = dict(answer=answer, mode=payload.mode, answer_id='answer-1',
                            citations=[dict(source_id='source-0', element_id='chunk-0')],
                            anchors=[dict(key='k1', source_id='source-0', object_id='chunk-0')])
            self.connection.execute('INSERT INTO answers VALUES (?, ?, ?, ?)',
                                    ('answer-1', notebook, payload.question.strip(), json.dumps(response)))
            self.connection.commit()
            return SimpleNamespace(**response, model_dump=lambda **_: deepcopy(response))

    def prepare_notebook(repo, cell, documents, suite):
        assert len(documents) == 2  # The distractor stays in every run.
        assert 'SECRET-GOLD' not in json.dumps(documents)
        mapping = {}
        for index, document in enumerate(documents):
            source, chunk = f'source-{index}', f'chunk-{index}'
            repo.connection.execute('INSERT INTO chunks VALUES (?, ?)', (chunk, source))
            mapping[document['id']] = dict(source_id=source, chunk_ids=[chunk])
        repo.connection.commit()
        return 'notebook', mapping

    monkeypatch.setattr(benchmark_runtime, 'prepare_notebook', prepare_notebook)
    module = ModuleType('app.services.sqlite_repository')
    module.SQLiteRepository = Repository
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(starter_runtime, 'snapshot_sources', lambda *a: {'revision': 'fixture'})
    monkeypatch.setattr(starter_runtime, 'configure_environment', lambda project, run, **kwargs: (
        SimpleNamespace(db_path=run / 'runtime/database.db'),
        dict(comparable_settings_sha256='settings', service_config_sha256='services')))
    return SimpleNamespace(Repository=Repository, prepare=prepare_notebook, observed_requests=observed_requests)


def news_bundle(tmp_path):
    raw, corpus = news_raw()
    return freeze(tmp_path, 'multihop_rag', raw, corpus=corpus)


def test_gold_free_runtime_defers_label_checks_until_scoring_boundary(tmp_path, native_boundary):
    from rag_eval.system_runtime import run_system_question
    bundle = news_bundle(tmp_path / 'input')
    product = partition_bundle(bundle, bundle['partitions'][0]['partition_id'], request_revision=REQUEST_V3)
    question = product['questions'][0]
    before = deepcopy(question)
    repo = native_boundary.Repository(SimpleNamespace(db_path=tmp_path / 'direct/database.db'))
    try:
        notebook, mapping = native_boundary.prepare(repo, tmp_path, product['documents'], 'multihop_rag')
        record = run_system_question(repo, notebook, question, 'chunk', mapping)
    finally:
        repo.close()
    assert record['status'] == 'success'
    assert record['persistence_verified'] is True
    assert record['context_supported'] is True
    assert record['evidence_check_status'] == 'deferred'
    assert 'evidence_check_error' not in record
    assert not FORBIDDEN.intersection(record)
    assert question == before


@pytest.mark.parametrize('mode', ['chunk', 'reasoning'])
def test_v3_full_run_joins_scoring_metadata_after_generation_and_exports_submission(tmp_path, native_boundary, monkeypatch, mode):
    from rag_eval import system_runtime
    from rag_eval.benchmark_submission import export_sn_runs, validate_submission
    bundle = news_bundle(tmp_path / 'input')
    original = system_runtime.run_system_question
    seen_questions = []

    def checked_generation(repo, notebook, question, mode, mapping):
        assert not FORBIDDEN.intersection(question)
        assert question['task'] == 'qa'
        seen_questions.append(deepcopy(question))
        return original(repo, notebook, question, mode, mapping)

    monkeypatch.setattr(system_runtime, 'run_system_question', checked_generation)
    run = tmp_path / 'run'
    execute(root=ROOT, project=tmp_path / 'product', bundle_dir=tmp_path / 'input/bundle', run=run,
            mode=mode, partition_id=bundle['partitions'][0]['partition_id'], request_revision=REQUEST_V3)
    loaded = load_run(run)
    assert loaded['state']['phase'] == 'finished'
    assert not loaded['warnings']
    output = loaded['outputs'][0]
    record = output['product_record']
    assert output['task'] == record['task'] == 'inference_query'
    assert record['adaptation_revision'] == DATA_V3
    assert record['evidence_check_status'] == 'completed'
    assert 'evidence_check_error' not in record
    assert not FORBIDDEN.intersection(record)
    assert record['deterministic']['evidence_hit'] == 1
    assert record['deterministic']['evidence_coverage'] == 1
    assert record['deterministic']['citation_valid_count'] == 1
    assert record['anchor_documents'] == {'k1': bundle['cases'][0]['gold_document_ids'][0]}
    assert len(native_boundary.observed_requests) == len(seen_questions) == 1
    scores = {row['scorer']: row for row in loaded['scores']}
    assert scores['product.notebook.multihop_official_weak_match_body_v1']['score'] == 0
    assert scores['product.notebook.multihop_context_fact_recall_v1']['score'] == 1
    assert scores['product.citation_object.existence_ratio']['score'] == 1
    submission_path = export_sn_runs(tmp_path / 'input/bundle', [run], tmp_path / 'submission')
    submission = validate_submission(bundle, json.loads(submission_path.read_text()))
    assert submission['coverage']['success'] == submission['coverage']['planned'] == 1
    assert submission['predictions'][0]['prediction'] == 'Alpha [k1]'
    assert submission['method']['configuration']['identity']['notebook_context']['request_revision'] == REQUEST_V3


def test_old_request_on_v3_data_cannot_be_exported_as_official_submission(tmp_path, native_boundary):
    from rag_eval.benchmark_submission import export_sn_runs
    bundle = news_bundle(tmp_path / 'input')
    run = tmp_path / 'legacy-run'
    execute(root=ROOT, project=tmp_path / 'product', bundle_dir=tmp_path / 'input/bundle', run=run,
            mode='chunk', partition_id=bundle['partitions'][0]['partition_id'],
            request_revision='notebook-request-v1')
    with pytest.raises(ValueError, match='request-v3'):
        export_sn_runs(tmp_path / 'input/bundle', [run], tmp_path / 'submission')
    assert not (tmp_path / 'submission').exists()


def test_agent_metadata_uses_scoring_task_without_changing_v3_question(tmp_path, native_boundary):
    bundle = news_bundle(tmp_path / 'input')
    product = partition_bundle(bundle, bundle['partitions'][0]['partition_id'], request_revision=REQUEST_V3)
    outputs, metadata = [], []
    class Evaluation:
        def run_case(self, *, invoke_and_persist, **kwargs):
            metadata.append(kwargs['metadata'])
            return invoke_and_persist()
    run = tmp_path / 'run'
    run.mkdir()
    repo = native_boundary.Repository(SimpleNamespace(db_path=run / 'runtime/database.db'))
    try:
        predictions(run, bundle['cases'], product, 'chunk', repo, outputs.append, agent_evaluation=Evaluation())
    finally:
        repo.close()
    assert metadata == [dict(suite='multihop_rag', task='inference_query', sample_id='0')]
    assert product['questions'][0]['task'] == 'qa'
    assert not FORBIDDEN.intersection(product['questions'][0])
    assert outputs[0]['task'] == 'inference_query'


def test_post_generation_evidence_failure_preserves_answer_and_is_scoring_error(tmp_path, native_boundary, monkeypatch):
    from rag_eval import benchmark_runtime
    bundle = news_bundle(tmp_path / 'input')
    def unavailable_database(repo, record, mapping):
        assert record['gold_document_ids'] == bundle['cases'][0]['gold_document_ids']
        assert record['answer'] == 'Alpha [k1]'
        raise sqlite3.OperationalError('read failed')
    monkeypatch.setattr(benchmark_runtime, 'evidence_checks', unavailable_database)
    run = tmp_path / 'run'
    execute(root=ROOT, project=tmp_path / 'product', bundle_dir=tmp_path / 'input/bundle', run=run,
            mode='chunk', partition_id=bundle['partitions'][0]['partition_id'], request_revision=REQUEST_V3)
    loaded = load_run(run)
    output = loaded['outputs'][0]
    assert output['status'] == 'success'
    assert output['prediction'] == 'Alpha [k1]'
    assert output['product_record']['evidence_check_status'] == 'error'
    assert output['product_record']['evidence_check_error'] == 'OperationalError'
    assert 'deterministic' not in output['product_record']
    citation = next(row for row in loaded['scores'] if row['scorer'] == 'product.citation_object.existence_ratio')
    assert citation['status'] == 'error'
    assert citation['score'] is None
    assert loaded['state']['phase'] == 'finished_with_errors'


@pytest.mark.parametrize('script_name', ['run_notebook_benchmarks.py', 'run_notebook_agent.py'])
@pytest.mark.parametrize('revision', [None, 'notebook-request-v1'])
def test_online_clis_default_to_v3_and_allow_explicit_legacy(tmp_path, monkeypatch, script_name, revision):
    from rag_eval import notebook_runner
    spec = importlib.util.spec_from_file_location('tested_notebook_cli', ROOT / 'scripts' / script_name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    arguments = []
    monkeypatch.setattr(notebook_runner, 'execute', lambda **kwargs: arguments.append(kwargs))
    argv = [script_name, '--bundle', str(tmp_path / 'bundle'), '--partition-id', 'part', '--mode', 'chunk',
            '--run-dir', str(tmp_path / 'run'), '--project-root', str(tmp_path / 'product')]
    if script_name == 'run_notebook_agent.py':
        argv.extend(['--judge-config', str(tmp_path / 'judge.json')])
    if revision:
        argv.extend(['--request-revision', revision])
    monkeypatch.setattr(sys, 'argv', argv)
    assert module.main() == 0
    assert arguments[0]['request_revision'] == (revision or REQUEST_V3)
