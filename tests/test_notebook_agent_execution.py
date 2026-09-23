"""Notebook integration keeps corpus, predictions and judge failures independent."""
import json
import sys
from contextlib import nullcontext
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from rag_eval.notebook_runner import execute
from rag_eval.starter_report import load_run
from test_notebook_execution import bundle_fixture


@pytest.mark.parametrize('cancelled', [False, True])
def test_native_scoring_runs_after_durable_answer_without_changing_corpus(tmp_path, monkeypatch, cancelled):
    from rag_eval import starter_runtime, benchmark_runtime, system_runtime, usage_capture

    partition = bundle_fixture(tmp_path)
    run = tmp_path / 'native'
    settings = SimpleNamespace(db_path=run / 'runtime/database.db')
    monkeypatch.setattr(starter_runtime, 'snapshot_sources', lambda *a: {'revision': 'test'})
    monkeypatch.setattr(starter_runtime, 'configure_environment', lambda *a, **k: (
        settings, {'comparable_settings_sha256': 'settings', 'service_config_sha256': 'services'}))
    monkeypatch.setattr(starter_runtime, 'resolve_models', lambda *a: (
        {'judge': {'model_id': 'fake'}}, {'judge': {'model_id': 'fake', 'config_sha256': 'identity'}}))
    monkeypatch.setattr(starter_runtime, 'make_adapter', lambda *a: object())
    monkeypatch.setattr(usage_capture, 'capture_usage', lambda *a: nullcontext({}))
    repository = ModuleType('app.services.sqlite_repository')
    repository.SQLiteRepository = lambda s: SimpleNamespace(db_path=s.db_path, close=lambda: None)
    monkeypatch.setitem(sys.modules, repository.__name__, repository)
    logger = ModuleType('app.core.llm_logging')
    logger.LLMInteractionLogger = object
    monkeypatch.setitem(sys.modules, logger.__name__, logger)
    tracing = ModuleType('app.core.evaluation_tracing')
    tracing.NATIVE_TRACE_VERSION = 'sn-deepeval-native-v1'
    tracing.evaluation_session = lambda **k: nullcontext(SimpleNamespace(errors=[]))
    monkeypatch.setitem(sys.modules, tracing.__name__, tracing)
    native = ModuleType('rag_eval.native_agent')
    called = []

    class Evaluation:
        def __init__(self, *a, **kwargs):
            self.has_errors = False

        def run_case(self, *, case_id, question, mode, invoke_and_persist, **kwargs):
            called.append(case_id)
            record = invoke_and_persist()
            saved = json.loads((run / 'outputs.jsonl').read_text())
            assert saved['case_id'] == case_id
            assert record['answer'] == saved['prediction']
            assert record['usage'] == {'scope': 'generation'}
            self.has_errors = True  # Simulates an SDK-recorded judge error.
            return record

        def finish(self):
            pytest.fail('run_case owns summary; outer cleanup must not replace cancellation')

    native.NativeAgentEvaluation = Evaluation
    native.require_native_tracing = lambda: tracing
    monkeypatch.setitem(sys.modules, native.__name__, native)

    def prepare(repo, cell, documents, suite):
        assert len(documents) == 1
        assert 'Red birds fly.' in documents[0]['text']
        return 'notebook', {documents[0]['id']: {'source_id': 'source', 'chunk_ids': ['chunk']}}

    monkeypatch.setattr(benchmark_runtime, 'prepare_notebook', prepare)

    class AskCancelled(Exception):
        pass
    class StreamingCancelled(AskCancelled):
        pass
    cancellation = StreamingCancelled('stop')

    def ask(repo, notebook, question, mode, mapping):
        if cancelled:
            raise cancellation
        return dict(question, status='success', answer='Red birds', response={'anchors': [], 'citations': []},
                    context_supported=True, retrieval_context=['Red birds fly.'], usage={'scope': 'generation'},
                    retrieved_document_ids=list(mapping), deterministic={'citation_count': 0, 'citation_valid_count': 0})

    monkeypatch.setattr(system_runtime, 'run_system_question', ask)
    arguments = dict(root=Path(__file__).resolve().parents[1], project=tmp_path / 'product',
                     bundle_dir=tmp_path / 'bundle', run=run, mode='chunk', partition_id=partition,
                     case_ids=['qasper:1'], agent_config={'judge_config': tmp_path / 'judge.json', 'trajectory': True,
                                                        'metrics': ['step_efficiency'], 'task_timeout': 600})
    if cancelled:
        with pytest.raises(StreamingCancelled) as caught:
            execute(**arguments)
        assert caught.value is cancellation
        assert json.loads((run / 'state.json').read_text())['phase'] == 'interrupted'
        assert (run / 'outputs.jsonl').read_text() == ''
        assert len(called) == 1
        return
    execute(**arguments)
    loaded = load_run(run)
    assert called == ['qasper:1']
    assert loaded['state']['phase'] == 'finished_with_errors'
    assert loaded['outputs'][0]['status'] == 'success'
    primary = [row for row in loaded['scores'] if row['metric_role'] == 'primary']
    assert primary and all(row['status'] == 'scored' and row['score'] == 1 for row in primary)
    assert loaded['manifest']['planned_predictions'] == 1
    assert loaded['manifest']['identity']['notebook_context']['case_ids'] == ['qasper:1']
    assert loaded['manifest']['identity']['agent_evaluation']['judge']['model_id'] == 'fake'
    assert loaded['manifest']['identity']['agent_evaluation']['metrics'] == ['step_efficiency']
    assert len(json.loads((run / 'product-bundle.json').read_text())['questions']) == 2


@pytest.mark.parametrize('ids', [[], ['qasper:missing'], ['qasper:0', 'qasper:0']])
def test_invalid_case_selection_fails_before_runtime_or_run_directory(tmp_path, ids):
    partition = bundle_fixture(tmp_path)
    with pytest.raises(ValueError, match='case'):
        execute(root=Path(__file__).resolve().parents[1], project=tmp_path / 'product',
                bundle_dir=tmp_path / 'bundle', run=tmp_path / 'run', mode='chunk',
                partition_id=partition, case_ids=ids)
    assert not (tmp_path / 'run').exists()
