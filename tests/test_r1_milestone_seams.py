"""Independent milestone counterexamples at formal submission/run boundaries."""
from copy import deepcopy
import json

import pytest

from rag_eval.benchmark_submission import build_submission
from rag_eval.benchmark_official import prepare_inputs, score_prepared
from rag_eval.benchmark_comparison import compare_submissions
from rag_eval.flip_report import compare_runs_flips
from rag_eval.identity import fingerprint
from rag_eval.stage_metrics import compute_stage_metrics
from test_benchmark_official import fixture
from test_benchmark_comparison import examples, rehash, save
from test_flip_report import runs
from test_stage_metrics import saved_run


def test_not_applicable_stage_stays_a_terminal_nonapplicable_status(tmp_path):
    result=compute_stage_metrics(saved_run(tmp_path,'terminal',phase='not_applicable'))
    assert result['cases'][0]['status']=='not_applicable'
    assert result['summary']['status_counts']=={'not_applicable':1}
    assert result['cases'][0]['gold_evidence_recall']['value'] is None


@pytest.mark.parametrize('location',['input_policy','configuration','prediction','record','product_record'])
def test_generic_submission_excludes_explicit_oracle_provenance(location):
    bundle,method,rows=fixture()
    if location=='input_policy':method['input_policy']='gold-input-oracle-public-units'
    elif location=='configuration':method['configuration']['diagnostic']=True
    elif location=='prediction':rows[0]['kind']='diagnostic/oracle'
    else:rows[0].setdefault(location,{})['diagnostic']=True
    with pytest.raises(ValueError,match='diagnostic|oracle'):
        build_submission(bundle,method=method,predictions=rows)


@pytest.mark.parametrize('consumer',['prepare','score','compare'])
def test_existing_oracle_submission_is_rejected_by_read_consumers(tmp_path,consumer):
    bundle,entries,submissions,scores=examples(tmp_path)
    prepared=prepare_inputs(bundle,submissions[0])
    sub=deepcopy(submissions[0]);sub['predictions'][0]['diagnostic']=True
    score=deepcopy(scores[0]);score['submission_id']=fingerprint(sub);rehash(score)
    save(entries[0]['submission'],sub);save(entries[0]['scores'],score)
    with pytest.raises(ValueError,match='diagnostic|oracle'):
        if consumer=='prepare':prepare_inputs(bundle,sub)
        elif consumer=='score':score_prepared(bundle,sub,prepared,source_directory=tmp_path,output_dir=tmp_path/'score')
        else:compare_submissions(tmp_path/'bundle',entries)
    assert not (tmp_path/'score').exists()


def sn_runs(tmp_path):
    paths,_=runs(tmp_path)
    sub=json.loads((paths[0]/'submission.json').read_text())
    method=deepcopy(sub['method']);method['kind']='sn'
    source=json.loads((paths[0]/'manifest.json').read_text())['identity']['source']
    identity=dict(source=source,runtime_settings='a'*64,
                  notebook_context=dict(request_revision='notebook-request-v3'))
    method['configuration'].update(mode='chunk',identity=identity)
    from rag_eval.notebook_bundle import load_bundle
    bundle=load_bundle(tmp_path/'bundle')
    sub=build_submission(bundle,method=method,predictions=sub['predictions'])
    score=json.loads((paths[0]/'scores.json').read_text())
    score.update(method=method,method_id=sub['method_id'],submission_id=fingerprint(sub),
                 prepared_id=fingerprint(prepare_inputs(bundle,sub)));rehash(score)
    for p in paths:
        save(p/'submission.json',sub);save(p/'scores.json',score)
        save(p/'manifest.json',dict(run_id=p.name,mode='chunk',identity=deepcopy(identity)))
        (p/'outputs.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in sub['predictions']))
    return paths


@pytest.mark.parametrize('difference',['mode','runtime_settings','feature_set'])
def test_flip_cannot_borrow_identical_answers_from_a_different_sn_identity(tmp_path,difference):
    paths=sn_runs(tmp_path)
    manifest=json.loads((paths[1]/'manifest.json').read_text())
    if difference=='mode':manifest['mode']='reasoning'
    elif difference=='runtime_settings':manifest['identity']['runtime_settings']='b'*64
    else:manifest['identity']['feature_set']={'fingerprint':'different'}
    save(paths[1]/'manifest.json',manifest)
    result=compare_runs_flips(*paths,bundle_directory=tmp_path/'bundle')
    assert result['admission']['status']=='not_admitted'
    assert result['after']['readable']
    assert 'identity' in result['after']['reason'] or 'mode' in result['after']['reason']


def test_matching_sn_identity_can_use_scope_normalization(tmp_path):
    paths=sn_runs(tmp_path)
    for p in paths:
        manifest=json.loads((p/'manifest.json').read_text())
        manifest['identity']['product_bundle']='local-partition-object'
        manifest['identity']['notebook_context'].update(partition_id='local-partition',case_ids=['local-scope'])
        save(p/'manifest.json',manifest)
    result=compare_runs_flips(*paths,bundle_directory=tmp_path/'bundle')
    assert result['admission']['status']=='admitted'
