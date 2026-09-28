from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from rag_eval.notebook_data import adapt
from test_hotpotqa import hotpot_row, hotpot_source


def api():
    assert importlib.util.find_spec('rag_eval.hotpot_evidence'), 'Hotpot citation projection missing'
    from rag_eval import hotpot_evidence
    return hotpot_evidence


def fixture(tmp_path):
    raw = hotpot_row()
    raw['context'][0][1] = ['Same sentence.', '', 'Same sentence.']
    data = adapt('hotpotqa', [raw], adaptation_revision='notebook-data-v3')
    connection = sqlite3.connect(':memory:')
    connection.executescript('''
        CREATE TABLE sources(id TEXT, file_path TEXT);
        CREATE TABLE chunks(id TEXT, source_id TEXT, text TEXT, section_path TEXT, element_ids TEXT);
        CREATE TABLE source_elements(id TEXT, source_id TEXT, text TEXT, metadata TEXT);
    ''')
    mapping, anchors, context = {}, [], []
    for i, document in enumerate(data['documents']):
        sid, cid, key = f's{i}', f'c{i}', f'k{i+1}'
        path = tmp_path / f'{i}.md'
        path.write_text(document['text'])
        connection.execute('INSERT INTO sources VALUES (?,?)', (sid, str(path)))
        position, texts, eids = len(document['title']), [], []
        for unit in document['source_units']:
            position += 2
            start = position
            position += len(unit['text'])
            if not unit['text'].strip():
                continue
            eid = f'e{i}-{unit["sent_id"]}'
            eids.append(eid)
            texts.append(unit['text'])
            connection.execute('INSERT INTO source_elements VALUES (?,?,?,?)',
                               (eid, sid, unit['text'], json.dumps(dict(char_start=start,char_end=position))))
        text = '\n'.join(texts)
        connection.execute('INSERT INTO chunks VALUES (?,?,?,?,?)', (cid,sid,text,'',json.dumps(eids)))
        mapping[document['id']] = dict(source_id=sid,text_sha256=document['text_sha256'],chunk_ids=[cid])
        anchor = dict(key=key,object_id=cid,object_type='chunk',source_id=sid,element_id=eids[0])
        anchors.append(anchor)
        context.append(key+': '+text)
    answer = 'Alpha City [k1][k2].'
    record = dict(status='success',answer=answer,response=dict(answer=answer,anchors=anchors[:2]),
                  captures=[dict(succeeded=True,sectioned=False,answer=answer,
                                 context_block='\n'.join(context),id_map={a['key']:deepcopy(a) for a in anchors})])
    repo = SimpleNamespace(_connect=lambda: connection,close_local=lambda:None)
    return data, repo, mapping, record


def test_final_citations_project_all_visible_sentence_positions_across_documents(tmp_path):
    m = api()
    data, repo, mapping, record = fixture(tmp_path)
    saved = m.capture_evidence(repo, record, data['documents'], mapping)
    assert saved['status'] == 'complete'
    assert saved['projection']['predicted_supporting_facts'] == [['Alpha',0],['Alpha',2],['Beta',0]]
    record['hotpot_evidence'] = saved
    assert m.verify_evidence(record,data['documents']) == [['Alpha',0],['Alpha',2],['Beta',0]]
    record['hotpot_evidence']['projection']['predicted_supporting_facts'].pop()
    with pytest.raises(ValueError,match='projection'):
        m.verify_evidence(record,data['documents'])


def test_clipped_citation_does_not_expand_to_unseen_sentences_or_use_gold(tmp_path):
    m = api()
    data, repo, mapping, record = fixture(tmp_path)
    record['answer'] = record['captures'][0]['answer'] = 'Answer [k1][k99]'
    record['response']['anchors'] = record['response']['anchors'][:1]
    record['captures'][0]['context_block'] = 'k1: Same sent…'
    saved = m.capture_evidence(repo,record,data['documents'],mapping)
    assert saved['status'] == 'complete'
    facts = saved['projection']['predicted_supporting_facts']
    assert facts[0] == ['Alpha',0]
    assert len(facts) == 2 and facts[1][0].startswith('\0') and facts[1][1] == -1
    data['cases'][0]['gold'] = {'arbitrary': 'No projection input'}
    assert m.capture_evidence(repo,record,data['documents'],mapping)['projection'] == saved['projection']


def test_mapping_error_retains_answer_and_does_not_forge_empty_support(tmp_path):
    m = api()
    data, repo, mapping, record = fixture(tmp_path)
    record['captures'] = []
    saved = m.capture_evidence(repo,record,data['documents'],mapping)
    assert saved['status'] == 'error'
    assert 'projection' not in saved
    assert record['answer'] == 'Alpha City [k1][k2].'


def test_sn_support_requires_observed_policy_instead_of_arbitrary_tuples():
    from rag_eval.benchmark_submission import build_submission
    from rag_eval.benchmark_official import prepare_inputs
    data = adapt('hotpotqa', [hotpot_row()], adaptation_revision='notebook-data-v3')
    data['manifest'] = dict(suite='hotpotqa',adaptation_revision='notebook-data-v3')
    method = dict(name='sn',kind='sn',citation_style='sn',model_identity={'test':True},
                  input_policy='public',configuration={})
    row = dict(case_id=data['cases'][0]['case_id'],status='success',prediction='Alpha City',
               record={'predicted_supporting_facts':[['Alpha',0]]})
    with pytest.raises(ValueError,match='policy'):
        prepare_inputs(data,build_submission(data,method=method,predictions=[row]))


def test_failed_hotpot_row_cannot_inject_supporting_facts():
    from rag_eval.benchmark_submission import build_submission
    from rag_eval.benchmark_official import prepare_inputs
    data = adapt('hotpotqa', [hotpot_row()], adaptation_revision='notebook-data-v3')
    data['manifest'] = dict(suite='hotpotqa', adaptation_revision='notebook-data-v3')
    method = dict(name='reference', kind='reference', citation_style='none', model_identity={'test':True},
                  input_policy='public', configuration={})
    row = dict(case_id=data['cases'][0]['case_id'], status='error', prediction='',
               record={'predicted_supporting_facts': [['Alpha', 0]]})
    with pytest.raises(ValueError, match='Failed Hotpot'):
        prepare_inputs(data, build_submission(data, method=method, predictions=[row]))


@pytest.mark.parametrize('mapping_failure', [False, True])
def test_full_sn_run_exports_replayable_support_and_keeps_failed_mapping_pending(tmp_path, monkeypatch, mapping_failure):
    from types import ModuleType
    import sys
    from rag_eval import benchmark_runtime, starter_runtime, system_runtime
    from rag_eval.notebook_runner import execute
    from rag_eval.notebook_bundle import prepare
    from rag_eval.starter_report import load_run
    from rag_eval.benchmark_submission import export_sn_runs
    from rag_eval.benchmark_official import prepare_inputs
    data, repo, mapping, record = fixture(tmp_path)
    raw = hotpot_row()
    raw['context'] = [[d['title'], [u['text'] for u in d['source_units']]] for d in data['documents']]
    raw_path, source_path = tmp_path/'raw.json', tmp_path/'source.json'
    raw_path.write_text(json.dumps([raw]))
    source_path.write_text(json.dumps(hotpot_source()))
    bundle = prepare('hotpotqa',raw_path,source_path,tmp_path/'bundle',adaptation_revision='notebook-data-v3')
    if mapping_failure:
        record['captures'] = []
    logger = ModuleType('app.core.llm_logging')
    logger.LLMInteractionLogger = type('Logger',(),{'log':lambda *a,**k:None})
    monkeypatch.setitem(sys.modules,logger.__name__,logger)
    repository = ModuleType('app.services.sqlite_repository')
    repository.SQLiteRepository = lambda settings: SimpleNamespace(
        db_path=settings.db_path,_connect=repo._connect,close_local=lambda:None,close=lambda:None)
    monkeypatch.setitem(sys.modules,repository.__name__,repository)
    monkeypatch.setattr(benchmark_runtime,'prepare_notebook',lambda *a:('notebook',mapping))
    monkeypatch.setattr(system_runtime,'run_system_question',lambda *a:deepcopy(record))
    def check_capture_before_gold(repo, saved, mapping, **kwargs):
        assert 'hotpot_evidence' in saved
        assert 'gold' not in saved
    monkeypatch.setattr(system_runtime,'complete_evidence_checks',check_capture_before_gold)
    monkeypatch.setattr(starter_runtime,'snapshot_sources',lambda *a:{'revision':'offline-fixture'})
    monkeypatch.setattr(starter_runtime,'configure_environment',lambda project,run,**k:(
        SimpleNamespace(db_path=run/'runtime/database.db'),dict(comparable_settings_sha256='settings',service_config_sha256='service')))
    run = tmp_path/'run'
    execute(root=Path(__file__).resolve().parents[1],project=tmp_path/'product',bundle_dir=tmp_path/'bundle',
            run=run,mode='chunk',partition_id=bundle['partitions'][0]['partition_id'],request_revision='notebook-request-v3')
    loaded = load_run(run)
    assert loaded['manifest']['identity']['hotpot_evidence'] == api().identity()
    assert loaded['state']['phase'] == ('finished_with_errors' if mapping_failure else 'finished')
    exported = export_sn_runs(tmp_path/'bundle',[run],tmp_path/'submission')
    prepared = prepare_inputs(bundle,json.loads(exported.read_text()))
    assert prepared['supporting_fact_supported'] is (not mapping_failure)
    assert prepared['data'][0]['answer'] == 'Alpha City .'
    from rag_eval.benchmark_official import score_prepared
    scored = score_prepared(bundle, json.loads(exported.read_text()), prepared,
                            source_directory=tmp_path, output_dir=tmp_path/'official-score')
    assert scored['metrics']['answer_f1'] == 1
    if not mapping_failure:
        assert prepared['data'][0]['supporting_facts'] == [['Alpha',0],['Alpha',2],['Beta',0]]
        assert scored['metrics']['supporting_fact_f1'] == .8
        assert scored['metrics']['joint_f1'] == .8
        assert set(scored['metric_denominators'].values()) == {1}
    else:
        assert 'supporting_fact_f1' not in scored['metrics']
        assert 'supporting_fact_f1' in scored['pending_metrics']
