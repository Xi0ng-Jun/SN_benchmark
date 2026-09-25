"""Observable projection rules, independent of gold and retrieval candidates."""
from copy import deepcopy
import importlib
import json
import sqlite3
from types import SimpleNamespace

import pytest

from rag_eval.notebook_data import adapt
from test_notebook_data_v3 import DATA_V3, qasper_raw


def api():
    return importlib.import_module('rag_eval.qasper_evidence')


def fixture(tmp_path, *, paragraphs=None, visible=None, answer='Birds [k1]', elements=None):
    raw = qasper_raw()
    raw['p']['full_text'][0]['paragraphs'] = paragraphs or ['Birds fly.', 'Fish swim.']
    doc = adapt('qasper', raw, adaptation_revision=DATA_V3)['documents'][0]
    texts = elements or ['Results', *raw['p']['full_text'][0]['paragraphs']]
    connection = sqlite3.connect(':memory:')
    connection.executescript('''
      CREATE TABLE sources(id TEXT, file_path TEXT);
      CREATE TABLE chunks(id TEXT, source_id TEXT, text TEXT, section_path TEXT, element_ids TEXT);
      CREATE TABLE source_elements(id TEXT, source_id TEXT, text TEXT, metadata TEXT);
    ''')
    source = tmp_path / 'paper.md'
    source.write_text(doc['text'])
    connection.execute('INSERT INTO sources VALUES (?,?)', ('s', str(source)))
    pos = 0
    for i, text in enumerate(texts):
        start = doc['text'].index(text, pos)
        pos = start + len(text)
        connection.execute('INSERT INTO source_elements VALUES (?,?,?,?)',
                           (f'e{i}', 's', text, json.dumps(dict(char_start=start, char_end=pos))))
    chunk = '\n'.join(texts)
    connection.execute('INSERT INTO chunks VALUES (?,?,?,?,?)',
                       ('c', 's', chunk, '', json.dumps([f'e{i}' for i in range(len(texts))])))
    anchor = dict(key='k1', object_id='c', object_type='chunk', source_id='s', element_id='e0')
    context = 'k1: ' + (visible if visible is not None else chunk)
    rec = dict(status='success', answer=answer, response=dict(answer=answer, anchors=[anchor]),
               captures=[dict(succeeded=True, sectioned=False, answer=answer,
                              context_block=context, id_map={'k1': deepcopy(anchor)})])
    mapping = {doc['id']: dict(source_id='s', text_sha256=doc['text_sha256'], chunk_ids=['c'])}
    repo = SimpleNamespace(_connect=lambda: connection, close_local=lambda: None)
    return raw, doc, repo, mapping, rec


def capture(f):
    raw, doc, repo, mapping, record = f
    catalogue = api().public_catalogues(raw, [doc])[doc['id']]
    return api().capture_evidence(repo, record, doc, catalogue, mapping)


def test_actual_final_chunk_expands_all_paragraphs_not_first_heading(tmp_path):
    f = fixture(tmp_path)
    result = capture(f)
    assert result['status'] == 'complete'
    assert result['projection']['predicted_evidence'] == ['Birds fly.', 'Fish swim.']
    assert result['projection']['unit_ids'] == ['0:0', '0:1']
    assert result['projection']['invalid_keys'] == []


def test_public_catalogue_preserves_duplicate_text_positions_without_gold(tmp_path):
    f = fixture(tmp_path, paragraphs=['Same.', 'Same.'])
    before = capture(f)
    f[0]['p']['qas'] = [{'anything': 'SECRET GARBAGE'}]
    after = capture(f)
    assert before == after
    assert before['projection']['unit_ids'] == ['0:0', '0:1']
    assert before['projection']['predicted_evidence'] == ['Same.', 'Same.']


def test_repeated_markers_and_overlapping_chunks_are_unique_by_unit_id(tmp_path):
    f = fixture(tmp_path, answer='Birds [k1, k2]. Again 【k1，k2】.')
    _, _, repo, _, rec = f
    repo._connect().execute('INSERT INTO chunks VALUES (?,?,?,?,?)', ('c2', 's', 'Fish swim.', '', '["e2"]'))
    other = dict(key='k2', object_id='c2', object_type='chunk', source_id='s', element_id='e2')
    rec['response']['anchors'].append(other)
    rec['captures'][0]['id_map']['k2'] = deepcopy(other)
    rec['captures'][0]['context_block'] += '\nk2: Fish swim.'
    assert capture(f)['projection']['predicted_evidence'] == ['Birds fly.', 'Fish swim.']


@pytest.mark.parametrize('visible,expected', [
    ('Results\nBirds fl…', ['Birds fly.']),
    ('Results\nBirds fly.\n', ['Birds fly.']),
    ('Results\nBirds fly.\nFi…', ['Birds fly.', 'Fish swim.']),
    ('Results\nBirds fl', ['Birds fly.']),
])
def test_truncated_chunk_never_selects_unseen_tail(tmp_path, visible, expected):
    result = capture(fixture(tmp_path, visible=visible))
    assert result['status'] == 'complete'
    assert result['projection']['predicted_evidence'] == expected


def test_invalid_marker_keeps_false_positive_slot_and_does_not_use_fallback_citations(tmp_path):
    f = fixture(tmp_path, answer='Birds [k1]. Other [k999]. Again [k999].')
    f[4]['response']['citations'] = [dict(element_id='e2', source_id='s')]
    result = capture(f)
    assert result['status'] == 'complete'
    prediction = result['projection']['predicted_evidence']
    assert prediction[:2] == ['Birds fly.', 'Fish swim.']
    assert len(prediction) == 3 and prediction[2].startswith('\x00invalid-sn-evidence:')
    assert result['projection']['invalid_keys'] == ['k999']


def test_no_citation_is_explicit_empty_prediction_even_with_candidates(tmp_path):
    f = fixture(tmp_path, answer='Unanswerable')
    f[4]['response']['anchors'] = []
    f[4]['captures'] = []
    f[4]['response']['citations'] = [dict(element_id='e1', source_id='s')]
    result = capture(f)
    assert result['status'] == 'complete'
    assert result['projection']['predicted_evidence'] == []


@pytest.mark.parametrize('failure', ['missing_capture', 'missing_chunk', 'changed_source',
                                     'ambiguous_marker', 'conflicting_anchor', 'rewritten_context'])
def test_mapping_failure_preserves_answer_and_is_not_an_empty_prediction(tmp_path, failure):
    f = fixture(tmp_path)
    before = f[4]['answer']
    if failure == 'missing_capture':
        f[4]['captures'] = []
    elif failure == 'missing_chunk':
        f[2]._connect().execute('DELETE FROM chunks')
    elif failure == 'changed_source':
        (tmp_path / 'paper.md').write_text('Changed after generation')
    elif failure == 'ambiguous_marker':
        f[4]['captures'][0]['context_block'] += '\nk1: injected source content'
    elif failure == 'conflicting_anchor':
        f[4]['response']['anchors'][0]['source_id'] = 'other-source'
    else:
        f[4]['captures'][0]['context_block'] = 'k1: A model-written summary instead.'
    result = capture(f)
    assert result['status'] == 'error'
    assert 'projection' not in result
    assert f[4]['answer'] == before


def test_direct_element_projects_original_bytes_not_normalized_parser_text(tmp_path):
    f = fixture(tmp_path, paragraphs=['Birds\n  fly.', 'Fish swim.'], elements=['Results', 'Birds\n  fly.', 'Fish swim.'])
    f[2]._connect().execute('UPDATE source_elements SET text=? WHERE id=?', ('Birds fly.', 'e1'))
    anchor = dict(key='k1', object_id='e1', object_type='element', source_id='s', element_id='e1',
                  source_title='Paper title', name='paragraph', location_label='paragraph', tier='personal')
    f[4]['response']['anchors'] = [anchor]
    f[4]['captures'][0]['id_map'] = {'k1': deepcopy(anchor)}
    f[4]['captures'][0]['context_block'] = 'k1: [source-element][personal] Paper title · paragraph — Birds fl…'
    assert capture(f)['projection']['predicted_evidence'] == ['Birds\n  fly.']


def test_replay_rejects_edited_prediction_and_changed_public_source(tmp_path):
    f = fixture(tmp_path)
    raw, doc, _, _, rec = f
    result = capture(f)
    catalogue = api().public_catalogues(raw, [doc])[doc['id']]
    rec['qasper_evidence'] = deepcopy(result)
    assert api().verify_evidence(rec, doc, catalogue) == ['Birds fly.', 'Fish swim.']
    rec['qasper_evidence']['projection']['predicted_evidence'] = ['Birds fly.']
    with pytest.raises(ValueError):
        api().verify_evidence(rec, doc, catalogue)


def test_catalogue_handles_same_text_in_title_section_and_paragraph(tmp_path):
    raw = qasper_raw()
    raw['p'].update(title='Same', abstract='Same')
    raw['p']['full_text'] = [dict(section_name='Same', paragraphs=['Same', 'Same'])]
    doc = adapt('qasper', raw, adaptation_revision=DATA_V3)['documents'][0]
    cat = api().public_catalogues(raw, [doc])[doc['id']]
    assert [(u['id'],u['start']) for u in cat['units'][:3]] == [('abstract',16),('0:0',28),('0:1',34)]


def test_frozen_bundle_derives_catalogue_without_changing_manifest_files(tmp_path):
    from rag_eval.notebook_bundle import load_bundle
    from test_notebook_data_v3 import freeze
    bundle = freeze(tmp_path, 'qasper', qasper_raw())
    restored = load_bundle(tmp_path / 'bundle')
    assert restored == bundle
    assert restored['qasper_evidence_catalogues'] == api().public_catalogues(qasper_raw(), bundle['documents'])
    assert 'qasper_evidence_catalogues' not in bundle['manifest']['files']


def test_official_preparation_replays_snapshot_and_rejects_evidence_tampering(tmp_path):
    from rag_eval.benchmark_official import prepare_inputs
    from rag_eval.benchmark_submission import build_submission
    from test_benchmark_submission import method
    from test_notebook_data_v3 import freeze
    f = fixture(tmp_path)
    bundle = freeze(tmp_path / 'bundle-input', 'qasper', f[0])
    f[4]['qasper_evidence'] = capture(f)
    f[4]['predicted_evidence'] = ['Birds fly.', 'Fish swim.']
    m = method()
    m.update(kind='sn', citation_style='sn')
    m['configuration']['qasper_evidence'] = api().identity()
    row = dict(case_id=bundle['cases'][0]['case_id'], status='success', prediction=f[4]['answer'], record=f[4])
    def prepare():
        return prepare_inputs(bundle, build_submission(bundle, method=m, predictions=[row]))
    p = prepare()
    assert p['evidence_supported'] is True
    row['record']['predicted_evidence'] = ['Birds fly.']
    with pytest.raises(ValueError, match='evidence|Evidence'):
        prepare()


def test_one_mapping_error_prevents_partial_evidence_f1_not_answer_scoring(tmp_path):
    from rag_eval.benchmark_official import prepare_inputs
    from rag_eval.benchmark_submission import build_submission
    from test_benchmark_submission import method
    from test_notebook_data_v3 import freeze
    f = fixture(tmp_path)
    f[0]['p']['qas'].append(deepcopy(f[0]['p']['qas'][0]))
    f[0]['p']['qas'][-1]['question_id'] = 'second'
    bundle = freeze(tmp_path / 'input', 'qasper', f[0])
    m = method();m.update(kind='sn',citation_style='sn')
    m['configuration']['qasper_evidence'] = api().identity()
    f[4]['qasper_evidence'] = capture(f)
    f[4]['predicted_evidence'] = ['Birds fly.', 'Fish swim.']
    other = deepcopy(f[4]);other.pop('predicted_evidence')
    other['qasper_evidence'] = {**api().identity(), 'status':'error', 'reason':'missing_context'}
    rows = [dict(case_id=c['case_id'], status='success', prediction=r['answer'], record=r)
            for c,r in zip(bundle['cases'], [f[4],other])]
    p = prepare_inputs(bundle, build_submission(bundle, method=m, predictions=rows))
    assert not p['evidence_supported'] and len(p['predictions']) == 2
    assert p['evidence_mapping_errors'] == ['qasper:second']


def test_unclaimed_snapshot_cannot_bypass_declared_method_policy(tmp_path):
    from rag_eval.benchmark_official import prepare_inputs
    from rag_eval.benchmark_submission import build_submission
    from test_benchmark_submission import method
    from test_notebook_data_v3 import freeze
    f = fixture(tmp_path)
    bundle = freeze(tmp_path / 'input', 'qasper', f[0])
    f[4]['qasper_evidence'] = capture(f)
    f[4]['predicted_evidence'] = ['Birds fly.', 'Fish swim.']
    m = method();m.update(kind='sn',citation_style='sn')
    row = dict(case_id=bundle['cases'][0]['case_id'],status='success',prediction=f[4]['answer'],record=f[4])
    with pytest.raises(ValueError,match='policy|identity'):
        prepare_inputs(bundle,build_submission(bundle,method=m,predictions=[row]))


def test_runtime_freezes_evidence_before_gold_join(tmp_path, monkeypatch):
    from rag_eval import notebook_runner, benchmark_runtime, system_runtime
    from rag_eval.notebook_bundle import partition_bundle
    from test_notebook_data_v3 import freeze, REQUEST_V3
    from types import ModuleType
    import sys
    f = fixture(tmp_path)
    bundle = freeze(tmp_path / 'input', 'qasper', f[0])
    product = partition_bundle(bundle, bundle['partitions'][0]['partition_id'], request_revision=REQUEST_V3)
    module = ModuleType('app.core.llm_logging')
    module.LLMInteractionLogger = type('Logger', (), {'log': lambda *a, **k: None})
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(benchmark_runtime, 'prepare_notebook', lambda *a: ('n', f[3]))
    monkeypatch.setattr(system_runtime, 'run_system_question', lambda *a: deepcopy(f[4]))
    def check_after_capture(repo, record, mapping, **kwargs):
        assert record['qasper_evidence']['status'] == 'complete'
        assert record['predicted_evidence'] == ['Birds fly.', 'Fish swim.']
    monkeypatch.setattr(system_runtime, 'complete_evidence_checks', check_after_capture)
    run = tmp_path / 'run';run.mkdir()
    rows = []
    notebook_runner.predictions(run, bundle['cases'], product, 'chunk', f[2], rows.append,
                                qasper_catalogues=bundle['qasper_evidence_catalogues'])
    assert rows[0]['status'] == 'success'
    assert rows[0]['product_record']['qasper_evidence']['status'] == 'complete'
    assert 'qasper_evidence_catalogues' not in product


@pytest.mark.parametrize('recover', [False, True])
def test_export_new_snapshot_or_explicit_readonly_old_recovery(tmp_path, monkeypatch, recover):
    from rag_eval.benchmark_submission import export_sn_runs
    from rag_eval import starter_report
    from rag_eval.benchmark_official import prepare_inputs
    from test_notebook_data_v3 import freeze
    from pathlib import Path
    import hashlib
    f = fixture(tmp_path)
    bundle = freeze(tmp_path / 'input', 'qasper', f[0])
    run = tmp_path / 'run';run.mkdir()
    config = dict(source=bundle['manifest'], product_services='services', runtime_settings='settings',
                  notebook_context=dict(request_revision='notebook-request-v3', partition_id='p'))
    if recover:
        runtime = run / 'runtime';runtime.mkdir()
        with sqlite3.connect(runtime / 'database.db') as connection:
            f[2]._connect().commit();f[2]._connect().backup(connection)
        cell = run / 'product-artifacts';cell.mkdir()
        (cell / 'document-map.json').write_text(json.dumps(f[3]))
    else:
        f[4]['qasper_evidence'] = capture(f)
        f[4]['predicted_evidence'] = ['Birds fly.', 'Fish swim.']
        config['qasper_evidence'] = api().identity()
    loaded = dict(manifest=dict(mode='chunk', run_id='r', identity=config), outputs=[
        dict(case_id=bundle['cases'][0]['case_id'], status='success', prediction=f[4]['answer'], product_record=f[4])])
    original = deepcopy(loaded)
    hashes = lambda: {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in run.rglob('*') if p.is_file()}
    before = hashes()
    monkeypatch.setattr(starter_report, 'load_run', lambda path: loaded)
    result = export_sn_runs(tmp_path / 'input/bundle', [run], tmp_path / 'export', qasper_evidence=recover)
    submission = json.loads(Path(result).read_text())
    assert submission['method']['configuration']['qasper_evidence'] == api().identity()
    assert prepare_inputs(bundle, submission)['evidence_supported'] is True
    assert loaded == original and hashes() == before
    assert submission['predictions'][0]['record']['predicted_evidence'] == ['Birds fly.', 'Fish swim.']


def test_partial_markdown_element_allows_only_blank_source_suffix(tmp_path):
    f = fixture(tmp_path, paragraphs=['1) **Birds** fly.', 'Fish swim.'])
    doc, connection = f[1], f[2]._connect()
    start = doc['text'].index('1) **Birds** fly.')
    connection.execute('UPDATE source_elements SET text=?, metadata=? WHERE id=?',
                       ('Birds fly.', json.dumps(dict(char_start=start, char_end=start+len('1) **Birds** fly.')+2)), 'e1'))
    connection.execute('UPDATE chunks SET text=? WHERE id=?', ('Results\nBirds fly.\nFish swim.','c'))
    f[4]['captures'][0]['context_block'] = 'k1: Results\nBir…'
    result = capture(f)
    assert result['status'] == 'complete'
    assert result['projection']['predicted_evidence'] == ['1) **Birds** fly.']


def test_repository_cleanup_failure_never_discards_generated_answer(tmp_path):
    f = fixture(tmp_path)
    def cannot_close():
        raise RuntimeError('private failure details')
    f[2].close_local = cannot_close
    result = capture(f)
    assert f[4]['answer'] == 'Birds [k1]'
    assert result['status'] == 'error'
    assert 'private failure details' not in json.dumps(result)


def test_partial_code_element_keeps_strip_alignment(tmp_path):
    f = fixture(tmp_path, paragraphs=['    abc\n    def', 'Fish swim.'])
    # SN preserves indentation in code element, but chunk text strips its ends.
    f[2]._connect().execute('UPDATE chunks SET text=? WHERE id=?', ('Results\nabc\n    def\nFish swim.','c'))
    f[4]['captures'][0]['context_block']='k1: Results\nabc\n    def'
    assert capture(f)['projection']['predicted_evidence'] == ['    abc\n    def']


def test_sectioned_citations_bind_own_selected_sections(tmp_path):
    f=fixture(tmp_path)
    first=f[4]['captures'][0]
    first['sectioned']=True
    second=deepcopy(first)
    second['id_map']={'k2':{**first['id_map']['k1'],'key':'k2'}}
    second['context_block']=first['context_block'].replace('k1:', 'k2:')
    second['answer']='Fish [k2]'
    f[4]['captures'].append(second)
    f[4]['answer']='## Birds\n\nBirds [k1]\n\n## Fish\n\nFish [k2]'
    f[4]['response']['anchors'].append(second['id_map']['k2'])
    assert capture(f)['projection']['unit_ids'] == ['0:0','0:1']
    second['answer']='Fish without a reference'
    assert capture(f)['reason']=='reference_not_selected_in_section'


def test_stripped_code_block_does_not_hide_last_original_paragraph(tmp_path):
    f=fixture(tmp_path,paragraphs=['```\n  aa','z\n```'])
    doc,connection=f[1],f[2]._connect()
    start=doc['text'].index('```');end=doc['text'].index('z\n```')+len('z\n```')
    connection.execute('UPDATE source_elements SET text=?,metadata=? WHERE id=?',
                       ('  aa\n\nz',json.dumps(dict(char_start=start,char_end=end)),'e1'))
    connection.execute('UPDATE chunks SET text=?,element_ids=? WHERE id=?',('aa\n\nz','["e1"]','c'))
    f[4]['captures'][0]['context_block']='k1: aa\n\nz'
    assert capture(f)['projection']['unit_ids']==['0:0','0:1']


def test_sn_explicit_evidence_cannot_bypass_policy_by_omitting_snapshot(tmp_path):
    from rag_eval.benchmark_official import prepare_inputs
    from rag_eval.benchmark_submission import build_submission
    from test_benchmark_submission import method
    from test_notebook_data_v3 import freeze
    f=fixture(tmp_path);bundle=freeze(tmp_path/'input','qasper',f[0])
    m=method();m.update(kind='sn',citation_style='sn')
    f[4]['predicted_evidence']=['Birds fly.']
    row=dict(case_id=bundle['cases'][0]['case_id'],status='success',prediction=f[4]['answer'],record=f[4])
    with pytest.raises(ValueError,match='policy|snapshot'):
        prepare_inputs(bundle,build_submission(bundle,method=m,predictions=[row]))


@pytest.mark.parametrize('separator', ['\n\n', '\n\n[Confirmed Memory]\n', '\n\n[Direct source elements]\n'])
def test_truncated_citation_before_next_context_partition(tmp_path, separator):
    f=fixture(tmp_path,visible='Results\nBirds fl…')
    call=f[4]['captures'][0]
    call['id_map']['k1001']=dict(object_type='object',object_id='unselected')
    call['context_block']+=separator+'k1001: Unrelated'
    result=capture(f)
    assert result['status']=='complete'
    assert result['projection']['predicted_evidence']==['Birds fly.']


def test_folded_image_description_without_source_span_fails_closed(tmp_path):
    f=fixture(tmp_path,paragraphs=['![Pic](image.png)', '> **图片描述**\n> Relevant detail.'])
    connection=f[2]._connect();start=f[1]['text'].index('![Pic]')
    connection.execute('UPDATE source_elements SET text=?, metadata=? WHERE id=?',
                       ('Pic Relevant detail.',json.dumps(dict(char_start=start,char_end=start+len('![Pic](image.png)'),
                        description='Relevant detail.')),'e1'))
    connection.execute('UPDATE chunks SET text=?,element_ids=? WHERE id=?',('Pic Relevant detail.','["e1"]','c'))
    f[4]['captures'][0]['context_block']='k1: Pic Relevant detail.'
    result=capture(f)
    assert result['status']=='error'
    assert 'source' in result['reason']


@pytest.mark.parametrize('field', ['id_map', 'context_block'])
def test_unknown_reference_requires_complete_observations(tmp_path, field):
    f=fixture(tmp_path,answer='Unsupported [k999]')
    f[4]['response']['anchors']=[]
    del f[4]['captures'][0][field]
    assert capture(f)['status']=='error'


def test_declared_new_run_snapshot_checked_by_saved_run_reader(tmp_path, monkeypatch):
    from rag_eval import notebook_runner
    from rag_eval.notebook_bundle import partition_bundle
    from test_notebook_data_v3 import freeze, REQUEST_V3
    from rag_eval.notebook_data import VERSION
    f=fixture(tmp_path);bundle=freeze(tmp_path/'input','qasper',f[0])
    product=partition_bundle(bundle,bundle['partitions'][0]['partition_id'],request_revision=REQUEST_V3)
    run=tmp_path/'run';run.mkdir();(run/'product-bundle.json').write_text(json.dumps(product))
    monkeypatch.setattr(notebook_runner,'load_bundle',lambda path:bundle)
    context=dict(partition_id=bundle['partitions'][0]['partition_id'],selected_cases=bundle['manifest']['selected_cases'],
                 partition_count=bundle['manifest']['partition_count'],request_revision=REQUEST_V3)
    manifest=dict(identity=dict(source=bundle['manifest'],product_bundle=product['manifest'],notebook_context=context,
                                qasper_evidence=api().identity()),source_manifest=bundle['manifest'],track='R',
                  release_gate=False,run_id='r',protocol_id='p',mode='chunk')
    planned=notebook_runner.plan_rows(bundle['cases'],'r','p','chunk')
    f[4]['qasper_evidence']=capture(f);f[4]['predicted_evidence']=['Birds fly.','Fish swim.']
    outputs=[dict(case_id=bundle['cases'][0]['case_id'],product_protocol=VERSION,material_role='source_documents',
                  status='success',prediction=f[4]['answer'],product_record=f[4])]
    notebook_runner.validate_saved_run(run,manifest,planned,outputs)
    f[4]['predicted_evidence']=['Birds fly.']
    with pytest.raises(ValueError,match='evidence|Evidence'):
        notebook_runner.validate_saved_run(run,manifest,planned,outputs)


@pytest.mark.parametrize('mapping_failure', [False,True])
def test_execute_records_policy_and_preserves_answer_on_mapping_failure(tmp_path, monkeypatch, mapping_failure):
    from rag_eval import benchmark_runtime, starter_runtime, system_runtime
    from rag_eval.notebook_runner import execute
    from rag_eval.starter_report import load_run
    from rag_eval.benchmark_submission import export_sn_runs
    from rag_eval.benchmark_official import prepare_inputs
    from test_notebook_data_v3 import freeze, REQUEST_V3
    from pathlib import Path
    from types import ModuleType
    import sys
    f=fixture(tmp_path);bundle=freeze(tmp_path/'input','qasper',f[0]);run=tmp_path/'run'
    if mapping_failure:
        f[4]['captures']=[]
    logger=ModuleType('app.core.llm_logging');logger.LLMInteractionLogger=type('Logger',(),{'log':lambda *a,**k:None})
    monkeypatch.setitem(sys.modules,logger.__name__,logger)
    repository=ModuleType('app.services.sqlite_repository')
    def make_repo(settings):
        return SimpleNamespace(db_path=settings.db_path,_connect=f[2]._connect,close_local=lambda:None,close=lambda:None)
    repository.SQLiteRepository=make_repo
    monkeypatch.setitem(sys.modules,repository.__name__,repository)
    monkeypatch.setattr(benchmark_runtime,'prepare_notebook',lambda *a:('notebook',f[3]))
    monkeypatch.setattr(system_runtime,'run_system_question',lambda *a:deepcopy(f[4]))
    monkeypatch.setattr(system_runtime,'complete_evidence_checks',lambda *a,**k:None)
    monkeypatch.setattr(starter_runtime,'snapshot_sources',lambda *a:{'revision':'offline-fixture'})
    monkeypatch.setattr(starter_runtime,'configure_environment',lambda project,run,**k:(
        SimpleNamespace(db_path=run/'runtime/database.db'),dict(comparable_settings_sha256='settings',service_config_sha256='service')))
    execute(root=Path(__file__).resolve().parents[1],project=tmp_path/'product',bundle_dir=tmp_path/'input/bundle',run=run,
            mode='chunk',partition_id=bundle['partitions'][0]['partition_id'],request_revision=REQUEST_V3)
    loaded=load_run(run)
    assert loaded['manifest']['identity']['qasper_evidence']==api().identity()
    assert loaded['state']['phase']==('finished_with_errors' if mapping_failure else 'finished')
    assert loaded['outputs'][0]['status']=='success'
    assert loaded['outputs'][0]['prediction']=='Birds [k1]'
    exported=export_sn_runs(tmp_path/'input/bundle',[run],tmp_path/'submission')
    prepared=prepare_inputs(bundle,json.loads(exported.read_text()))
    assert prepared['evidence_supported'] is (not mapping_failure)
    assert len(prepared['predictions'])==1
