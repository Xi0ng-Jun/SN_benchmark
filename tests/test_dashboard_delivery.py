"""The portable report loads full observations only after a user selects one."""
import json
import importlib.util
from pathlib import Path

from rag_eval.artifacts import save_jsonl
from rag_eval.experiment_aggregation import write_dashboard
from test_experiment_dashboard import make_run


def test_report_keeps_full_evidence_out_of_initial_page(tmp_path):
    run = make_run(tmp_path)
    original = json.loads((run / 'outputs.jsonl').read_text())
    evidence = 'LONG_EVIDENCE_SENTINEL ' * 4000 + '</script><img src=x onerror=alert(1)>'
    original['product_record']['retrieval_context'] = [evidence]
    save_jsonl(run / 'outputs.jsonl', [original])
    report = write_dashboard([run], tmp_path / 'report')
    index = json.loads((report / 'dashboard-data.json').read_text())
    assert index['format'] == 'sn-experiment-dashboard-v3'
    assert evidence not in (report / 'dashboard.html').read_text()
    assert 'LONG_EVIDENCE_SENTINEL' not in (report / 'dashboard-data.json').read_text()
    details = [report / item['detail_file'] for item in index['observations'].values()]
    assert all(path.is_file() for path in details)
    assert any('LONG_EVIDENCE_SENTINEL' in path.read_text() for path in details)
    assert all('window.SNExplorer.registerDetail' in path.read_text() for path in details)
    assert index['graph']['nodes'] and index['graph']['edges']


def test_offline_demo_has_valid_three_paths_and_verified_rescore(tmp_path):
    source = Path(__file__).resolve().parents[1] / 'scripts/build_dashboard_demo.py'
    spec = importlib.util.spec_from_file_location('dashboard_demo', source)
    demo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(demo)
    html = demo.build_demo(tmp_path / 'demo')
    index = json.loads(html.with_name('dashboard-data.json').read_text())
    assert index['summary']['generation_run_count'] == 3
    assert index['summary']['rescoring_run_count'] == 1
    assert index['summary']['planned_outputs'] == 9
    assert index['summary']['unique_questions'] == 3
    assert {r['mode'] for r in index['runs']} == {'chunk', 'reasoning', 'bm25'}
    assert all(r['manifest']['demo'] for r in index['runs'])
    assert any(e['kind'] == 'rescore' for e in index['graph']['edges'])
    assert any(e['scope'] == 'retrieval' for e in index['entries'])
    assert any(e['scope'] == 'trajectory' and e['score'] == 0 for e in index['entries'])
    assert any(e['status'] == 'not_applicable' and e['score'] is None for e in index['entries'])
