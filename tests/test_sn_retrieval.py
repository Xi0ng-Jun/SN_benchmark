from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sqlite3
from types import ModuleType, SimpleNamespace

import pytest

from test_multihop_official import frozen, source_dir


def api():
    assert importlib.util.find_spec('rag_eval.sn_retrieval'), 'SN native ranking capture missing'
    from rag_eval import sn_retrieval
    return sn_retrieval


def fixture(tmp_path):
    bundle = frozen(tmp_path)
    document, = bundle['documents']
    connection = sqlite3.connect(':memory:')
    connection.execute('CREATE TABLE chunks(id TEXT, source_id TEXT, text TEXT)')
    chunks = [SimpleNamespace(chunk_id='first', source_id='source', text='kiwi mango', relevance=.1),
              SimpleNamespace(chunk_id='second', source_id='source', text='apple pear', relevance=.9)]
    for chunk in chunks:
        connection.execute('INSERT INTO chunks VALUES (?,?,?)', (chunk.chunk_id, chunk.source_id, chunk.text))
    mapping = {document['id']: dict(source_id='source', text_sha256=document['text_sha256'],
                                   chunk_ids=[c.chunk_id for c in chunks])}

    class Ask:
        def _activate_selected_source_graph(self, notebook_id, chunks, *, top_hits=(), max_results=20):
            return chunks, None

    service = Ask()
    repo = SimpleNamespace(_connect=lambda: connection, close_local=lambda: None,
                           _runtime=SimpleNamespace(ask_component=service))
    return bundle, repo, mapping, chunks


def test_capture_keeps_native_order_and_original_text_without_sorting_by_scores(tmp_path):
    m = api()
    bundle, repo, mapping, chunks = fixture(tmp_path)
    question = dict(case_id=bundle['cases'][0]['case_id'], question='apple', original_question='apple')
    service = repo._runtime.ask_component
    original = service._activate_selected_source_graph
    with m.capture_chunk_ranking(repo, 'notebook', question, bundle['documents'], mapping) as saved:
        result = service._activate_selected_source_graph('notebook', chunks)
        assert result[0] is chunks
    assert service._activate_selected_source_graph == original
    assert saved['status'] == 'complete'
    passages = m.validate_capture(saved, question, bundle['documents'], m.identity())
    assert [p['text'] for p in passages] == ['kiwi mango', 'apple pear']
    chunks[0].text = 'changed after observation'
    assert m.validate_capture(saved, question, bundle['documents'], m.identity()) == passages
    saved['snapshot']['chunks']['first']['text'] = 'injected fact'
    with pytest.raises(ValueError, match='hash|snapshot'):
        m.validate_capture(saved, question, bundle['documents'], m.identity())


def test_empty_selection_is_observed_but_absent_or_repeated_stages_are_not_rankings(tmp_path):
    m = api()
    bundle, repo, mapping, _ = fixture(tmp_path)
    question = dict(case_id=bundle['cases'][0]['case_id'], question='apple', original_question='apple')
    service = repo._runtime.ask_component
    with m.capture_chunk_ranking(repo, 'notebook', question, bundle['documents'], mapping) as empty:
        service._activate_selected_source_graph('notebook', [])
    assert m.validate_capture(empty, question, bundle['documents'], m.identity()) == []
    with m.capture_chunk_ranking(repo, 'notebook', question, bundle['documents'], mapping) as missing:
        pass
    assert missing['status'] == 'pending'
    assert m.validate_capture(missing, question, bundle['documents'], m.identity()) is None
    with m.capture_chunk_ranking(repo, 'notebook', question, bundle['documents'], mapping) as repeated:
        service._activate_selected_source_graph('notebook', [])
        service._activate_selected_source_graph('notebook', [])
    assert repeated['status'] == 'error'
    assert m.validate_capture(repeated, question, bundle['documents'], m.identity()) is None


def test_sn_run_exports_observed_ranking_and_scores_it_independently_of_generation(tmp_path, monkeypatch, source_dir):
    import sys
    from rag_eval import benchmark_runtime, runtime_environment, system_runtime
    from rag_eval.benchmark_submission import export_sn_runs
    from rag_eval.multihop_official import score_multihop_retrieval
    from rag_eval.notebook_runner import execute
    from rag_eval.run_report import load_run

    m = api()
    bundle, repo, mapping, chunks = fixture(tmp_path)
    logger = ModuleType('app.core.llm_logging')
    logger.LLMInteractionLogger = type('Logger', (), {'log': lambda *a, **k: None})
    monkeypatch.setitem(sys.modules, logger.__name__, logger)
    repository = ModuleType('app.services.sqlite_repository')
    def make_repo(settings):
        repo.db_path = settings.db_path
        repo.close = lambda: None
        return repo
    repository.SQLiteRepository = make_repo
    monkeypatch.setitem(sys.modules, repository.__name__, repository)
    monkeypatch.setattr(benchmark_runtime, 'prepare_notebook', lambda *a: ('notebook', mapping))
    def ask(repo, notebook, question, mode, mapping):
        assert 'gold' not in question and 'gold_document_ids' not in question
        hits = chunks if question['original_question'] == 'apple' else []
        repo._runtime.ask_component._activate_selected_source_graph(notebook, hits)
        failed = question['original_question'] == 'kiwi'
        return dict(question, status='error' if failed else 'success', answer='' if failed else 'yes',
                    mode=mode, response={}, reason='fixture generation failure' if failed else None)
    monkeypatch.setattr(system_runtime, 'run_system_question', ask)
    def gold_join(repo, record, mapping, **kwargs):
        assert record['retrieval']['status'] == 'complete'
    monkeypatch.setattr(system_runtime, 'complete_evidence_checks', gold_join)
    monkeypatch.setattr(runtime_environment, 'snapshot_sources', lambda *a: {'revision': 'fixture'})
    monkeypatch.setattr(runtime_environment, 'configure_environment', lambda project, run, **k: (
        SimpleNamespace(db_path=run/'runtime/database.db'),
        dict(comparable_settings_sha256='settings', service_config_sha256='service')))
    run = tmp_path/'run'
    execute(root=Path(__file__).resolve().parents[1], project=tmp_path/'product', bundle_dir=tmp_path/'data/bundle',
            run=run, mode='chunk', partition_id=bundle['partitions'][0]['partition_id'],
            request_revision='notebook-request-v3')
    loaded = load_run(run)
    assert loaded['manifest']['identity']['multihop_retrieval'] == m.identity()
    assert loaded['outputs'][1]['status'] == 'error'
    exported = export_sn_runs(tmp_path/'data/bundle', [run], tmp_path/'submission')
    submission = json.loads(exported.read_text())
    assert submission['method']['configuration']['retrieval_contract'] == m.POLICY
    scored = score_multihop_retrieval(bundle, submission, source_directory=source_dir)
    assert scored['status'] == 'complete'
    assert scored['coverage']['eligible'] == scored['coverage']['scored'] == 2
    assert scored['coverage']['excluded_null'] == 1
    assert scored['metrics'] == pytest.approx(dict(upstream_hits_at_10=.5, upstream_hits_at_4=.5,
                                                 upstream_map_at_10=.25, upstream_mrr_at_10=.25))
    # A declared missing observation must not turn into an observed empty list.
    missing = deepcopy(submission)
    record = missing['predictions'][1]['record']
    with m.capture_chunk_ranking(repo, 'notebook', record, bundle['documents'], mapping) as unobserved:
        pass
    record['retrieval'] = unobserved
    partial = score_multihop_retrieval(bundle, missing, source_directory=source_dir)
    assert partial['status'] == 'partial' and not partial['metrics']
    assert partial['coverage']['scored'] == 1
