import json
from pathlib import Path

import pytest

from rag_eval.artifacts import digest, save_json, save_jsonl
from rag_eval.notebook_bundle import prepare, partition_bundle
from rag_eval.notebook_runner import plan_rows
from rag_eval.notebook_alce import OFFICIAL_REVISION
from rag_eval.notebook_alce_results import export_run, attach_scores
from rag_eval.starter_protocol import fingerprint
from rag_eval.starter_results import result_record
from rag_eval.starter_report import load_run


def saved_run(tmp_path):
    raw = [dict(question='Which cities?', docs=[dict(title='Cities', text='Rome and Paris.')],
                qa_pairs=[dict(question='City?', short_answers=['Rome'])])]
    save_json(tmp_path/'raw.json', raw)
    save_json(tmp_path/'source.json', dict(dataset='alce', split='dev', revision='release', source_url='https://example.org',
                                        license='fixture', task='asqa', retriever='gtr', variant='ordinary'))
    run = tmp_path/'run'
    bundle = prepare('alce', tmp_path/'raw.json', tmp_path/'source.json', run/'input')
    part = bundle['partitions'][0]['partition_id']
    product = partition_bundle(bundle, part)
    identity = dict(source=bundle['manifest'], models={}, code={}, runtime_settings='s', product_services='p',
                    product_bundle=product['manifest'], audits_sha256=fingerprint([]), track='R',
                    notebook_context=dict(partition_id=part, selected_cases=1, partition_count=1))
    protocol = fingerprint(dict(identity, mode='chunk'))
    plan = plan_rows(bundle['cases'], 'run', protocol, 'chunk')
    save_jsonl(run/'planned.jsonl', plan)
    save_json(run/'product-bundle.json', product)
    save_json(run/'manifest.json', dict(format='public-starter-run-v1', run_id='run', suite='alce', track='R', mode='chunk',
              protocol_id=protocol, pairing_id=fingerprint(identity), identity=identity, source_manifest=bundle['manifest'],
              product_protocol='sn-notebook-benchmarks-v1', planned_sha256=digest(run/'planned.jsonl'),
              planned_predictions=1, planned_scores=len(plan), release_gate=False))
    case = bundle['cases'][0]
    record = dict(status='success', answer='Rome [k1].', response={'anchors':[{'key':'k1','source_id':'s'}]},
                  source_to_document={'s': case['material_document_ids'][0]})
    save_jsonl(run/'outputs.jsonl', [dict(case_id=case['case_id'], sample_id=case['sample_id'], suite='alce', task='asqa',
                status='success', output_available=True, prediction=record['answer'], product_record=record,
                material_role='source_documents', product_protocol='sn-notebook-benchmarks-v1')])
    save_jsonl(run/'scores.jsonl', [result_record(p, status='unscored', reason='deferred', output_available=True) for p in plan])
    save_jsonl(run/'model-events.jsonl', [])
    save_json(run/'state.json', {'phase':'finished'})
    return run


def score_artifact(tmp_path, run):
    directory = tmp_path/'official'
    directory.mkdir()
    export_run(run, directory/'input.json')
    (directory/'official-source').mkdir()
    for name in ('eval.py','utils.py'):
        (directory/'official-source'/name).write_text('# synthetic scorer snapshot\n')
    invocation = dict(source={'revision':OFFICIAL_REVISION, 'files':{name:digest(directory/'official-source'/name) for name in ('eval.py','utils.py')}}, models={'autoais':{'files':{'model':'hash'}}},
                      metrics=['citations'], input_sha256=digest(directory/'input.json'), bridge_sha256='bridge',
                      score_scale='0..1', body_preprocessing='entire body', network_policy='offline', python_executable='python')
    save_json(directory/'invocation.json', invocation)
    save_json(directory/'execution.json', dict(status='completed', returncode=0))
    rows = [dict(case_id='alce:0', task='asqa', scorer='product.notebook.alce_citation_'+m+'_official_v1',
                 status='scored', score=s, reason=None, details={}) for m,s in [('rec',0.75),('prec',0.5)]]
    save_json(directory/'scores.json', dict(scores=rows, invocation=invocation))
    return directory


def test_attach_official_scores_is_immutable_and_reportable(tmp_path):
    run = saved_run(tmp_path)
    official = score_artifact(tmp_path, run)
    before = (run/'scores.jsonl').read_bytes()
    derived = tmp_path/'scored-run'
    attach_scores(run, official, derived)
    loaded = load_run(derived)
    values = [r['score'] for r in loaded['scores'] if r['status'] == 'scored']
    assert values == [0.75, 0.5]
    assert (run/'scores.jsonl').read_bytes() == before
    assert loaded['manifest']['identity']['official_scoring']['models']
    assert not loaded['warnings']


def test_reject_official_scores_from_different_answer(tmp_path):
    run = saved_run(tmp_path)
    official = score_artifact(tmp_path, run)
    value = json.loads((official/'input.json').read_text())
    value['data'][0]['output'] = 'A different answer.'
    save_json(official/'input.json', value)
    with pytest.raises(ValueError):
        attach_scores(run, official, tmp_path/'bad')


def test_reject_duplicate_unknown_and_out_of_range_scores(tmp_path):
    run = saved_run(tmp_path)
    official = score_artifact(tmp_path, run)
    value = json.loads((official/'scores.json').read_text())
    value['scores'].append(value['scores'][0])
    save_json(official/'scores.json', value)
    with pytest.raises(ValueError):
        attach_scores(run, official, tmp_path/'bad')


def test_reject_changed_official_source_snapshot(tmp_path):
    run = saved_run(tmp_path)
    official = score_artifact(tmp_path, run)
    (official/'official-source'/'eval.py').write_text('changed')
    with pytest.raises(ValueError, match='source'):
        attach_scores(run, official, tmp_path/'bad')
