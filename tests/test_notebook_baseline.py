"""Baseline contracts exercised with frozen local fixtures and a fake model transport."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from rag_eval.notebook_baseline import build_qmsum_prompt, rank_qmsum_turns, run_qmsum_case


def qmsum_case():
    return {
        'case_id': 'qmsum:0:specific:0', 'sample_id': '0:specific:0', 'suite': 'qmsum',
        'task': 'specific', 'question': 'Why is the launch delayed?',
        'adaptation_revision': 'notebook-data-v2',
        'gold': {'answer': 'GOLD_SENTINEL', 'relevant_text_span': [[1, 1]], 'turns': turns()},
        'references': ['GOLD_SENTINEL'], 'material_document_ids': ['meeting'],
    }


def turns():
    return [
        {'id': 0, 'speaker': 'A', 'content': 'We discuss the budget.'},
        {'id': 1, 'speaker': 'B', 'content': 'The launch is delayed until June.'},
        {'id': 2, 'speaker': 'A', 'content': 'The budget is approved for the launch.'},
    ]


def test_bm25_ranking_budget_and_transcript_order():
    selected = rank_qmsum_turns('When is the launch delayed?', turns(), top_k=3, max_context_chars=70)
    assert [row['turn_id'] for row in selected] == [1]
    assert len('\n\n'.join(row['text'] for row in selected)) <= 70
    selected = rank_qmsum_turns('launch delayed', turns(), top_k=2, max_context_chars=500)
    assert [row['turn_id'] for row in selected] == [1, 2]
    assert selected[0]['score'] > selected[1]['score'] > 0


def test_bm25_scores_are_hand_calculated():
    import math
    data = [{'id': 0, 'speaker': 'A', 'content': 'apple'},
            {'id': 1, 'speaker': 'B', 'content': 'pear'}]
    selected = rank_qmsum_turns('apple', data, top_k=1)
    assert selected[0]['score'] == pytest.approx(math.log(2))


def test_budget_does_not_truncate_turn_and_ids_are_unique():
    assert rank_qmsum_turns('launch', turns(), max_context_chars=1) == []
    with pytest.raises(ValueError, match='unique'):
        rank_qmsum_turns('launch', turns() + [turns()[0]])


def test_question_and_source_only_determine_prompt():
    case = qmsum_case()
    selected = rank_qmsum_turns(case['question'], turns())
    before = build_qmsum_prompt(case, selected)
    changed = copy.deepcopy(case)
    changed['gold'] = {'answer': 'OTHER_GOLD', 'relevant_text_span': [[2, 2]]}
    changed['references'] = ['OTHER_GOLD']
    assert build_qmsum_prompt(changed, selected) == before
    assert 'GOLD_SENTINEL' not in before and 'relevant_text_span' not in before
    assert case['question'] in before and '[turn 1] B:' in before


def test_case_saves_dashboard_fields_and_reuses_scorer(monkeypatch):
    from rag_eval import notebook_scoring
    scorer_calls = []
    def scorer(case, record, name):
        scorer_calls.append((case['case_id'], record['answer'], name))
        return {'status': 'scored', 'score': .5, 'details': {'fixture': True}}
    monkeypatch.setattr(notebook_scoring, 'score_case', scorer)
    output, scores = run_qmsum_case(qmsum_case(), lambda prompt: 'June.', turns=turns(), top_k=2)
    assert output['prediction'] == output['product_record']['answer'] == 'June.'
    assert output['product_record']['question'] == qmsum_case()['question']
    assert output['retrieval_turn_ids'] == [1, 2]
    assert {s['scorer'] for s in scores} == {c[2] for c in scorer_calls}
    assert all(s['score'] == .5 for s in scores)
    assert output['product_record']['retrieved_document_ids'] == ['meeting', 'meeting']
    json.dumps(output, allow_nan=False)


def test_generation_and_scoring_errors_preserve_output(monkeypatch):
    from rag_eval import notebook_scoring
    def error(*args):
        raise RuntimeError('PRIVATE_ENDPOINT_AND_SECRET')
    output, scores = run_qmsum_case(qmsum_case(), error, turns=turns())
    assert output['status'] == 'error'
    assert all(s['status'] == 'unscored' and s['score'] is None for s in scores)
    assert 'PRIVATE_ENDPOINT' not in json.dumps((output, scores))
    monkeypatch.setattr(notebook_scoring, 'score_case', error)
    saved = []
    output, scores = run_qmsum_case(qmsum_case(), lambda _: 'June.', turns=turns(), output_sink=saved.append)
    assert saved[0]['prediction'] == 'June.'
    assert all(s['status'] == 'error' and s['score'] is None for s in scores)


def prepare_fixture(tmp_path):
    from rag_eval.notebook_bundle import prepare
    from rag_eval.artifacts import save_json
    meeting = {'meeting_transcripts': [{k: t[k] for k in ('speaker', 'content')} for t in turns()],
               'general_query_list': [{'query': 'Summarize the launch.', 'answer': 'GOLD_SENTINEL'}],
               'specific_query_list': [{'query': 'Why is the launch delayed?', 'answer': 'GOLD_SENTINEL',
                                        'relevant_text_span': [['1', '1']]}]}
    raw = tmp_path/'raw.jsonl'
    raw.write_text(json.dumps(meeting)+'\n'+json.dumps(meeting)+'\n')
    source = tmp_path/'source.json'
    save_json(source, dict(dataset='qmsum', split='test', revision='fixture',
                           source_url='https://example.org/fixture', license='fixture'))
    bundle = prepare('qmsum', raw, source, tmp_path/'bundle')
    return bundle


def run_fixture(tmp_path, monkeypatch, *, interrupt=False, partition=0, name='bm25', top_k=2):
    from rag_eval import starter_runtime
    from scripts import run_notebook_baseline as cli
    from rag_eval.notebook_bundle import load_bundle
    from rag_eval.starter_model import ExplicitBenchmarkModel
    from rag_eval.starter_protocol import fingerprint
    if not (tmp_path/'bundle').exists():
        prepare_fixture(tmp_path)
    bundle = load_bundle(tmp_path/'bundle')
    run = tmp_path/name
    monkeypatch.setenv('BASELINE_TEST_URL', 'https://private.invalid/v1')
    monkeypatch.setenv('BASELINE_TEST_KEY', 'SECRET_SENTINEL')
    config = tmp_path/'models.json'
    config.write_text(json.dumps({'tested': dict(model_id='test-model', base_url_env='BASELINE_TEST_URL',
        api_key_env='BASELINE_TEST_KEY', parameters=dict(temperature=0, top_p=1, max_tokens=100, max_retries=0, timeout=60))}))
    monkeypatch.setattr(starter_runtime, 'configure_environment', lambda *a, **k:
        (SimpleNamespace(), dict(comparable_settings_sha256='settings', service_config_sha256=None)))
    # Snapshot product code is a boundary; the actual source identity is tested elsewhere.
    monkeypatch.setattr(starter_runtime, 'snapshot_sources', lambda *a:
        dict(product_revision='fixture', benchmark_source_hashes={'src/rag_eval/notebook_scoring.py': 'fixture'}, versions={}))
    def make_adapter(spec, role, settings, sink):
        class Transport:
            calls = 0
            def chat_json(self, messages, hint, **kwargs):
                self.calls += 1
                assert (run/'manifest.json').exists() and (run/'planned.jsonl').exists()
                if self.calls == 2:
                    assert len((run/'outputs.jsonl').read_text().splitlines()) == 1
                    if interrupt:
                        raise KeyboardInterrupt()
                assert 'GOLD_SENTINEL' not in json.dumps(messages)
                return {'answer': 'The launch is delayed until June.'}
        return ExplicitBenchmarkModel(Transport(), model_id=spec['model_id'], role=role,
            parameters=spec['parameters'], config_sha256=spec['config_sha256'], sink=sink)
    monkeypatch.setattr(starter_runtime, 'make_adapter', make_adapter)
    argv = ['--bundle', str(tmp_path/'bundle'), '--partition-id', bundle['partitions'][partition]['partition_id'],
            '--project-root', str(tmp_path/'product'), '--model-config', str(config), '--run-dir', str(run),
            '--top-k', str(top_k)]
    if interrupt:
        with pytest.raises(KeyboardInterrupt):
            cli.main(argv)
    else:
        cli.main(argv)
    return run


def test_cli_freezes_input_and_builds_dashboard_and_report(tmp_path, monkeypatch):
    from rag_eval.starter_report import load_run, write_report
    from rag_eval.experiment_aggregation import write_dashboard
    run = run_fixture(tmp_path, monkeypatch)
    loaded = load_run(run)
    assert loaded['manifest']['planned_predictions'] == 2
    assert loaded['outputs'][0]['prediction']
    assert not loaded['warnings']
    report = write_dashboard([run], tmp_path/'dashboard')
    data = (report/'dashboard-data.json').read_text()
    assert 'SECRET_SENTINEL' not in data and 'https://private.invalid' not in data
    assert all(e['mode'] == 'bm25' for e in json.loads(data)['entries'])
    write_report([run], tmp_path/'report')
    assert 'BM25' in (tmp_path/'report/report.md').read_text()


def test_interrupted_cli_keeps_full_plan_and_completed_cases(tmp_path, monkeypatch):
    from rag_eval.starter_report import load_run
    run = run_fixture(tmp_path, monkeypatch, interrupt=True)
    loaded = load_run(run)
    assert loaded['state']['phase'] == 'interrupted'
    assert loaded['manifest']['planned_predictions'] == 2
    assert len(loaded['outputs']) == 1
    assert loaded['unfinished_model_calls'] == 1
    assert loaded['warnings']


@pytest.mark.parametrize('artifact', ['input/documents.jsonl', 'input/cases.jsonl', 'product-bundle.json', 'outputs.jsonl'])
def test_artifact_tampering_rejected(tmp_path, monkeypatch, artifact):
    from rag_eval.starter_report import load_run
    run = run_fixture(tmp_path, monkeypatch)
    path = run/artifact
    text = path.read_text()
    if artifact == 'outputs.jsonl':
        path.write_text(text.replace('Retrieved meeting turns:', 'CHANGED_CONTEXT:'))
    else:
        path.write_text(text.replace('launch', 'TAMPERED'))
    with pytest.raises(ValueError):
        load_run(run)


def test_different_baseline_budgets_not_pooled_but_meetings_are(tmp_path, monkeypatch):
    from rag_eval.experiment_aggregation import aggregate_runs
    a = run_fixture(tmp_path, monkeypatch, name='first')
    b = run_fixture(tmp_path, monkeypatch, name='second', partition=1)
    c = run_fixture(tmp_path, monkeypatch, name='third', partition=1, top_k=1)
    families = [r['config_family'] for r in aggregate_runs([a,b,c])['runs']]
    assert families[0] == families[1]
    assert families[0] != families[2]


def test_cli_rejects_output_in_product_before_runtime(tmp_path):
    from scripts.run_notebook_baseline import main
    with pytest.raises((SystemExit, ValueError)):
        main(['--bundle', str(tmp_path/'bundle'), '--partition-id', 'p',
              '--project-root', str(tmp_path/'product'), '--model-config', str(tmp_path/'models'),
              '--run-dir', str(tmp_path/'product/run')])
    assert not (tmp_path/'product').exists()


def make_sn_fixture(run, destination):
    """Build a valid saved SN protocol artifact; no live product is invoked."""
    import shutil
    from rag_eval.artifacts import digest, save_json, save_jsonl
    from rag_eval.notebook_bundle import load_bundle
    from rag_eval.notebook_data import VERSION
    from rag_eval.notebook_runner import plan_rows
    from rag_eval.starter_protocol import fingerprint
    from rag_eval.starter_results import result_record
    shutil.copytree(run, destination)
    manifest = json.loads((run/'manifest.json').read_text())
    identity = manifest['identity']
    identity.pop('baseline')
    identity['models'] = {}
    identity['product_services'] = 'sn-services'
    identity['product_bundle'] = json.loads((run/'product-bundle.json').read_text())['manifest']
    manifest.update(run_id=destination.name, mode='chunk', product_protocol=VERSION,
                    protocol_id=fingerprint({**identity, 'mode': 'chunk'}), pairing_id=fingerprint(identity))
    bundle = load_bundle(run/'input')
    ids = {q['case_id'] for q in json.loads((run/'product-bundle.json').read_text())['questions']}
    planned = plan_rows([c for c in bundle['cases'] if c['case_id'] in ids], destination.name, manifest['protocol_id'], 'chunk')
    save_jsonl(destination/'planned.jsonl', planned)
    manifest.update(planned_sha256=digest(destination/'planned.jsonl'), planned_scores=len(planned))
    save_json(destination/'manifest.json', manifest)
    outputs = [json.loads(line) for line in (run/'outputs.jsonl').read_text().splitlines()]
    for o in outputs:
        o['product_protocol'] = VERSION
    save_jsonl(destination/'outputs.jsonl', outputs)
    save_jsonl(destination/'scores.jsonl', [result_record(p, status='scored', score=.75, output_available=True) for p in planned])
    return destination


def scored_pair(tmp_path, monkeypatch):
    from rag_eval import notebook_scoring
    monkeypatch.setattr(notebook_scoring, 'score_case', lambda *a: dict(status='scored', score=.25))
    baseline = run_fixture(tmp_path, monkeypatch)
    # Give the fixture an explicit comparable scorer/dependency identity.
    from rag_eval.artifacts import digest, save_json, save_jsonl
    from rag_eval.starter_protocol import fingerprint
    from rag_eval.notebook_baseline import plan_rows
    from rag_eval.notebook_bundle import load_bundle
    manifest = json.loads((baseline/'manifest.json').read_text())
    manifest['identity']['code']['benchmark_source_hashes'].update({
        'src/rag_eval/notebook_data.py': 'data', 'src/rag_eval/protocol.py': 'context'})
    manifest['identity']['code']['versions'] = {'rouge-score': '0.1.2', 'nltk': 'fixture'}
    manifest.update(protocol_id=fingerprint({**manifest['identity'], 'mode': 'bm25'}),
                    pairing_id=fingerprint(manifest['identity']))
    bundle = load_bundle(baseline/'input')
    ids = {o['case_id'] for o in map(json.loads, (baseline/'outputs.jsonl').read_text().splitlines())}
    planned = plan_rows([c for c in bundle['cases'] if c['case_id'] in ids], baseline.name, manifest['protocol_id'])
    from rag_eval.starter_results import result_record
    save_jsonl(baseline/'planned.jsonl', planned)
    save_jsonl(baseline/'scores.jsonl', [result_record(p, status='scored', score=.25, output_available=True) for p in planned])
    manifest['planned_sha256'] = digest(baseline/'planned.jsonl')
    save_json(baseline/'manifest.json', manifest)
    return baseline, make_sn_fixture(baseline, tmp_path/'sn')


def test_comparison_uses_shared_questions_not_unpaired_means(tmp_path, monkeypatch):
    from rag_eval.notebook_baseline_comparison import write_comparison
    a, b = scored_pair(tmp_path, monkeypatch)
    # One SN ROUGE score missing: do not zero-fill or include it in paired mean.
    lines = (b/'scores.jsonl').read_text().splitlines()
    (b/'scores.jsonl').write_text('\n'.join(lines[1:])+'\n')
    output = write_comparison([a], [b], tmp_path/'comparison')
    report = json.loads((output/'comparison.json').read_text())
    assert report['model_alignment'] == 'not_verified'
    assert all(g['mean_delta_sn_minus_baseline'] == .5 for g in report['groups'] if g['paired_scored'])
    group = next(g for g in report['groups'] if g['sn_scored'] == 0)
    assert group['paired_scored'] == 0 and group['mean_delta_sn_minus_baseline'] is None
    assert group['common_planned'] == 1 and group['baseline_scored'] == 1
    assert 'sn-services' in (output/'comparison.json').read_text()
    assert (output/'comparison.md').exists()


@pytest.mark.parametrize('change', ['scorer', 'dependency', 'duplicate', 'source'])
def test_comparison_rejects_incomparable_or_duplicate_cells(tmp_path, monkeypatch, change):
    from rag_eval.notebook_baseline_comparison import compare_runs
    from rag_eval.artifacts import save_json
    from rag_eval.starter_protocol import fingerprint
    a,b = scored_pair(tmp_path, monkeypatch)
    manifest = json.loads((b/'manifest.json').read_text())
    if change == 'scorer':
        manifest['identity']['code']['benchmark_source_hashes']['src/rag_eval/notebook_scoring.py'] = 'changed'
    elif change == 'dependency':
        manifest['identity']['code']['versions']['nltk'] = 'different'
    elif change == 'source':
        manifest['identity']['source']['revision'] = 'other'
    if change != 'duplicate':
        # Keep identity fingerprints valid; the loader may reject source/plan
        # drift before the comparison rejects the scorer mismatch.
        manifest.update(protocol_id=fingerprint({**manifest['identity'], 'mode': 'chunk'}),
                        pairing_id=fingerprint(manifest['identity']))
        old_plans = [json.loads(x) for x in (b/'planned.jsonl').read_text().splitlines()]
        from rag_eval.notebook_runner import plan_rows
        from rag_eval.notebook_bundle import load_bundle
        from rag_eval.artifacts import save_jsonl, digest
        cases = [c for c in load_bundle(b/'input')['cases'] if c['case_id'] in {p['case_id'] for p in old_plans}]
        plans = plan_rows(cases, b.name, manifest['protocol_id'], 'chunk')
        save_jsonl(b/'planned.jsonl', plans)
        save_jsonl(b/'scores.jsonl', [])
        manifest['planned_sha256'] = digest(b/'planned.jsonl')
        save_json(b/'manifest.json', manifest)
    with pytest.raises(ValueError):
        compare_runs([a,a] if change == 'duplicate' else [a], [b])


def test_comparison_cannot_write_into_input_run(tmp_path, monkeypatch):
    from rag_eval.notebook_baseline_comparison import write_comparison
    a,b = scored_pair(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match='outside'):
        write_comparison([a], [b], a/'report')
    assert not (a/'report').exists()


def test_empty_model_answer_is_observed_but_unscored():
    output, scores = run_qmsum_case(qmsum_case(), lambda prompt: ' ', turns=turns())
    assert output['status'] == 'no_answer' and not output['output_available']
    assert all(s['status'] == 'unscored' and s['score'] is None for s in scores)


def test_no_matching_words_has_deterministic_ties_and_empty_context_prompt():
    selected = rank_qmsum_turns('xyz', list(reversed(turns())), top_k=2)
    assert [r['turn_id'] for r in selected] == [0,1]
    assert all(r['score'] == 0 for r in selected)
    from rag_eval.notebook_baseline import case_request
    prompt, retrieval = case_request(qmsum_case(), turns(), max_context_chars=1)
    assert retrieval['selected'] == [] and retrieval['excluded_for_budget']
    assert 'If evidence is insufficient, state that' in prompt
    assert 'GOLD_SENTINEL' not in prompt


@pytest.mark.parametrize('case_update', [{'suite':'qasper'}, {'product_protocol':'sn-notebook-v1'}])
def test_bm25_mode_cannot_be_declared_as_an_sn_run(case_update):
    from rag_eval.starter_results import planned_result
    from rag_eval.notebook_baseline import BASELINE_VERSION
    case = dict(qmsum_case(), product_protocol=BASELINE_VERSION)
    case.update(case_update)
    with pytest.raises(ValueError, match='BM25'):
        planned_result(case, run_id='x', protocol_id='y', track='R', mode='bm25', scorer='scorer')


def test_interrupted_later_scorer_preserves_previous_scores(tmp_path, monkeypatch):
    from rag_eval import notebook_scoring
    from rag_eval.starter_report import load_run
    calls = []
    def scorer(case, record, name):
        calls.append(name)
        if len(calls) == 2:
            raise KeyboardInterrupt()
        return dict(status='scored', score=.5)
    monkeypatch.setattr(notebook_scoring, 'score_case', scorer)
    with pytest.raises(KeyboardInterrupt):
        run_fixture(tmp_path, monkeypatch)
    run = load_run(tmp_path/'bm25')
    assert run['state']['phase'] == 'interrupted'
    assert len(run['outputs']) == 1
    assert len(run['scores']) == 1 and run['scores'][0]['score'] == .5
    assert len(run['planned']) == 7
