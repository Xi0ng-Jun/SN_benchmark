"""Request revisions must not rewrite the already frozen experiment inputs."""
import copy
import json

import pytest

from rag_eval.artifacts import save_json
from rag_eval.notebook_bundle import partition_bundle, prepare
from rag_eval.notebook_runner import plan_rows, validate_saved_run
from rag_eval.starter_protocol import fingerprint


def bundle_at(tmp_path, suite='qmsum'):
    if suite == 'qmsum':
        raw = dict(meeting_transcripts=[dict(speaker='A', content='Discuss delivery.')],
                   general_query_list=[dict(query='Summarize the whole meeting.', answer='SECRET-GOLD')],
                   specific_query_list=[])
    else:
        raw = {'p': dict(title='Paper', abstract='', full_text=[dict(section_name='Result', paragraphs=['Birds fly.'])],
                        qas=[dict(question_id='q', question='What flies?', answers=[dict(answer=dict(
                            unanswerable=False, extractive_spans=['SECRET-GOLD'], free_form_answer='',
                            yes_no=None, evidence=['Birds fly.']))])])}
    save_json(tmp_path / 'raw.json', raw)
    if suite == 'qmsum':
        (tmp_path / 'raw.json').write_text(json.dumps(raw) + '\n')
    save_json(tmp_path / 'source.json', dict(dataset=suite, split='test', revision='fixture',
                                           source_url='https://example.org', license='fixture'))
    return prepare(suite, tmp_path / 'raw.json', tmp_path / 'source.json', tmp_path / 'input')


@pytest.mark.parametrize('suite', ['qmsum', 'qasper'])
def test_v2_changes_only_instruction_and_preserves_legacy_and_gold_boundary(tmp_path, suite):
    bundle = bundle_at(tmp_path, suite)
    before = copy.deepcopy(bundle)
    part = bundle['partitions'][0]['partition_id']
    legacy = partition_bundle(bundle, part)
    explicit_legacy = partition_bundle(bundle, part, request_revision='notebook-request-v1')
    current = partition_bundle(bundle, part, request_revision='notebook-request-v2')
    assert legacy == explicit_legacy
    assert 'request_revision' not in legacy['manifest']
    assert current['manifest']['request_revision'] == 'notebook-request-v2'
    assert current['documents'] == legacy['documents']
    old, new = legacy['questions'][0], current['questions'][0]
    assert new['original_question'] == old['original_question']
    assert new['question'].startswith(new['original_question'] + '\n\n')
    assert 'SECRET-GOLD' not in new['question']
    assert 'SECRET-GOLD' not in json.dumps(current['documents'])
    assert new['question'] != old['question']
    assert 'this query' not in new['question']
    assert 'if it is' not in new['question']
    assert fingerprint(current['manifest']) != fingerprint(legacy['manifest'])
    assert bundle == before
    with pytest.raises(ValueError, match='request revision'):
        partition_bundle(bundle, part, request_revision='unknown')


@pytest.mark.parametrize('revision', ['notebook-request-v1', 'notebook-request-v2'])
def test_saved_run_reconstructs_own_request_revision(tmp_path, revision):
    bundle = bundle_at(tmp_path)
    part = bundle['partitions'][0]['partition_id']
    product = partition_bundle(bundle, part, request_revision=revision)
    context = dict(partition_id=part, selected_cases=1, partition_count=1)
    if revision != 'notebook-request-v1':
        context['request_revision'] = revision
    identity = dict(source=bundle['manifest'], notebook_context=context, product_bundle=product['manifest'])
    manifest = dict(identity=identity, source_manifest=bundle['manifest'], track='R', release_gate=False,
                    run_id='fixture', protocol_id='fixture', mode='reasoning')
    planned = plan_rows(bundle['cases'], 'fixture', 'fixture', 'reasoning')
    save_json(tmp_path / 'product-bundle.json', product)
    validate_saved_run(tmp_path, manifest, planned, [])
    product['questions'][0]['question'] += ' altered'
    save_json(tmp_path / 'product-bundle.json', product)
    with pytest.raises(ValueError, match='materials changed'):
        validate_saved_run(tmp_path, manifest, planned, [])
