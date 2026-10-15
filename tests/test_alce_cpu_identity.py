import copy

from rag_eval import benchmark_official as official
from rag_eval.identity import fingerprint
from test_benchmark_official import fixture
from rag_eval.benchmark_submission import build_submission


def test_cpu_patch_changes_scorer_identity_and_survives_score_export(tmp_path, monkeypatch):
    b, method, rows = fixture('alce', 'asqa')
    sub = build_submission(b, method=method, predictions=rows)
    prepared = official.prepare_inputs(b, sub)
    monkeypatch.setattr(official, '_alce_text', lambda *a: ({'str_em': 1.}, [{'str_em': 1.}], []))
    dep = {'device': 'cpu', 'scorer_label': '官方脚本 + CPU 设备适配补丁',
           'device_patch': {'id': 'alce-cpu-device-v1', 'sha256': 'a'*64}}
    monkeypatch.setattr('rag_eval.alce_official_cli.score_alce_batch', lambda *a, **k: {
        'raw_metrics': {'str_em': 100.00000000000004},
        'metric_normalizations': {'str_em': {'normalized_percent': 100.}},
        'metrics': {'str_em': 1.}, 'metric_case_ids': {'str_em': ['alce:q1']}, 'dependencies': dep})
    result = official.score_prepared(b, sub, prepared, source_directory=tmp_path,
                                    output_dir=tmp_path/'score', alce_runtime={'alce_device': 'cpu'})
    assert result['raw_metrics']['str_em'] == 100.00000000000004
    assert result['metric_normalizations']['str_em']['normalized_percent'] == 100.
    assert result['scorer_label'] == '官方脚本 + CPU 设备适配补丁'
    assert result['scorer_identity']['dependencies']['alce_batch']['device_patch']['sha256'] == 'a'*64
    modified = copy.deepcopy(result['scorer_identity'])
    modified['dependencies']['alce_batch']['device_patch']['sha256'] = 'b'*64
    assert fingerprint(modified) != result['scorer_id']
    import json
    assert json.loads((tmp_path/'score/scores.json').read_text())['scorer_label'] == result['scorer_label']


def test_comparison_markdown_discloses_cpu_adaptation_from_identity():
    from rag_eval.benchmark_comparison import _markdown
    report = dict(suite='alce', scope='subset', case_count=2, profile='alce-246c476-cli-v1',
                  common_metrics=['str_em'], methods=[], metric_denominators={'str_em': 2},
                  conditional_metric_scopes={}, comparisons=[], interpretation=[],
                  scorer_identity={'dependencies': {'alce_batch': {
                      'scorer_label': '官方脚本 + CPU 设备适配补丁',
                      'device_patch': {'sha256': 'a'*64}}}})
    markdown = _markdown(report)
    assert '官方脚本 + CPU 设备适配补丁' in markdown
    assert 'a'*64 in markdown
