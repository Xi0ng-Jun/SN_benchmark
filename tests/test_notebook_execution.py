import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from rag_eval.notebook_bundle import prepare
from rag_eval.notebook_runner import execute
from rag_eval.starter_report import load_run
from rag_eval.experiment_aggregation import aggregate_runs
from rag_eval.starter_results import planned_result, result_record, summarize


def test_continuous_primary_is_not_binary_accuracy():
    case = dict(suite='qasper', case_id='q', sample_id='q', metric_role='primary', score_kind='continuous')
    plan = planned_result(case, run_id='x', protocol_id='p', track='R', mode='chunk', scorer='f1')
    row = result_record(plan, status='scored', score=0.4, output_available=True)
    group = summarize([plan], [row])[0]
    assert group['mean_over_scored'] == 0.4
    assert group['metric_role'] == 'primary'
    assert group['correct_over_planned'] is None
    assert group['known_correct'] is None


def bundle_fixture(tmp_path):
    raw = {'paper': dict(title='Birds', abstract='Birds in water.', full_text=[dict(section_name='Results', paragraphs=['Red birds fly.'])],
                        qas=[dict(question_id=str(i), question='What flies?', answers=[dict(answer=dict(
                            unanswerable=False, extractive_spans=['Red birds'], free_form_answer='', yes_no=None, evidence=['Red birds fly.']))]) for i in range(2)])}
    (tmp_path/'raw.json').write_text(json.dumps(raw))
    (tmp_path/'source.json').write_text(json.dumps(dict(dataset='qasper', split='test', revision='v0.3',
                                                     source_url='https://example.org', license='CC-BY-4.0')))
    bundle = prepare('qasper', tmp_path/'raw.json', tmp_path/'source.json', tmp_path/'bundle')
    return bundle['partitions'][0]['partition_id']


def test_execution_preserves_clarification_and_dashboard_pairing(tmp_path, monkeypatch):
    from rag_eval import starter_runtime, benchmark_runtime, system_runtime
    part = bundle_fixture(tmp_path)
    settings = SimpleNamespace()
    monkeypatch.setattr(starter_runtime, 'snapshot_sources', lambda *a: {'revision':'test'})
    def configure(project, run, **kwargs):
        settings.db_path = run/'runtime/database.db'
        assert kwargs['document_limit'] == 40
        return settings, dict(comparable_settings_sha256='settings', service_config_sha256='services')
    monkeypatch.setattr(starter_runtime, 'configure_environment', configure)
    module = ModuleType('app.services.sqlite_repository')
    module.SQLiteRepository = lambda s: SimpleNamespace(db_path=s.db_path, close=lambda: None)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    llm = ModuleType('app.core.llm_logging')
    llm.LLMInteractionLogger = type('Logger', (), {})
    monkeypatch.setitem(sys.modules, llm.__name__, llm)
    from contextlib import nullcontext
    from rag_eval import usage_capture
    monkeypatch.setattr(usage_capture, 'capture_usage', lambda *a: nullcontext({}))
    def prepare_notebook(repo, cell, docs, suite):
        assert len(docs) == 1
        assert 'What flies?' not in docs[0]['text']
        return 'notebook', {docs[0]['id']: {'source_id':'source', 'chunk_ids':['chunk']}}
    monkeypatch.setattr(benchmark_runtime, 'prepare_notebook', prepare_notebook)
    def ask(repo, notebook, question, mode, mapping):
        assert notebook == 'notebook'
        assert question['question'].startswith('What flies?')
        success = question['sample_id'] == '0'
        return dict(question, status='success' if success else 'clarification', answer='Red birds [k1]' if success else '',
                    response={'anchors':[], 'citations':[]}, context_supported=True,
                    retrieval_context=['Red birds fly.'], source_ids=['source'], retrieved_document_ids=list(mapping),
                    deterministic=dict(citation_count=0, citation_valid_count=0),
                    behavior={'kind':'answer_returned' if success else 'clarification'})
    monkeypatch.setattr(system_runtime, 'run_system_question', ask)
    paths = []
    for mode in ('chunk', 'reasoning'):
        run = tmp_path/mode
        execute(root=Path(__file__).resolve().parents[1], project=tmp_path/'product', bundle_dir=tmp_path/'bundle',
                run=run, mode=mode, partition_id=part)
        loaded = load_run(run)
        assert loaded['manifest']['planned_predictions'] == 2
        assert loaded['outputs'][1]['status'] == 'clarification'
        assert all(r['score'] is None for r in loaded['scores'] if r['case_id'] == 'qasper:1')
        assert not loaded['warnings']
        paths.append(run)
    dashboard = aggregate_runs(paths)
    assert len(dashboard['runs']) == 2
    assert {e['partition'] for e in dashboard['entries']} == {part}
    assert {e['suite'] for e in dashboard['entries']} == {'qasper'}
    assert len({r['manifest']['pairing_id'] for r in dashboard['runs']}) == 1
    assert not any('哈希' in w for r in dashboard['runs'] for w in r['warnings'])
    # Changing the saved corpus must be detected before visualization can use it.
    saved = json.loads((paths[0]/'product-bundle.json').read_text())
    saved['documents'][0]['text'] = 'Tampered'
    (paths[0]/'product-bundle.json').write_text(json.dumps(saved))
    with pytest.raises(ValueError):
        load_run(paths[0])
