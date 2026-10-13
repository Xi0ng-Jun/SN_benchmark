"""Input registration must never consume gold or narrow canonical materials."""
import copy
import json

import pytest

from rag_eval.artifacts import digest
from rag_eval.identity import fingerprint
from rag_eval.notebook_bundle import load_bundle, partition_bundle, prepare
from rag_eval.workbench.plan_inputs import NotebookPlanInputs
from rag_eval.workbench.repository import Conflict
from test_benchmark_reference import frozen
from test_hotpotqa import hotpot_row, hotpot_source
from test_notebook_data_v3 import freeze, qasper_raw


def canonical_fixture(tmp_path, suite='qasper'):
    """Build all source bytes in tmp_path using the mature suite fixtures."""
    if suite == 'qasper':
        raw = qasper_raw()
        extra = copy.deepcopy(raw['p']['qas'][0])
        extra['question_id'] = 'q2'
        raw['p']['qas'].append(extra)
        raw['second'] = copy.deepcopy(raw['p'])
        raw['second']['qas'] = [copy.deepcopy(extra)]
        raw['second']['qas'][0]['question_id'] = 'q3'
        return freeze(tmp_path, suite, raw)
    if suite == 'hotpotqa':
        tmp_path.mkdir(parents=True, exist_ok=True)
        (tmp_path / 'raw').write_text(json.dumps([hotpot_row()]))
        (tmp_path / 'source').write_text(json.dumps(hotpot_source()))
        return prepare(suite, tmp_path / 'raw', tmp_path / 'source', tmp_path / 'bundle',
                       adaptation_revision='notebook-data-v3')
    return frozen(tmp_path, suite, questions=2)


def input_fixture(tmp_path, suite='qasper'):
    bundle = canonical_fixture(tmp_path, suite)
    inputs = NotebookPlanInputs({'registered': tmp_path / 'bundle'})
    return inputs, bundle, fingerprint(bundle['manifest'])


def public_identity(bundle, bundle_id):
    return dict(input_id='registered', adapter='notebook-bundle', bundle_id=bundle_id,
                suite=bundle['manifest']['suite'],
                source_revision='sha256:' + fingerprint(bundle['manifest']['source']['revision']),
                adaptation_revision='notebook-data-v3')


@pytest.mark.parametrize('suite', ['qasper', 'qmsum', 'multihop_rag', 'alce', 'hotpotqa'])
def test_five_canonical_adapters_project_only_public_ids_and_versions(tmp_path, suite):
    inputs, bundle, bundle_id = input_fixture(tmp_path, suite)
    description = inputs.describe('registered')
    assert description == dict(public_identity(bundle, bundle_id),
                               case_ids=[c['case_id'] for c in bundle['cases']],
                               partitions=[dict(partition_id=p['partition_id'], case_ids=p['case_ids'])
                                           for p in bundle['partitions']])
    frozen_plan = inputs.canonical('registered', case_ids=[bundle['cases'][0]['case_id']],
                                  expected_bundle_id=bundle_id)
    assert frozen_plan['input'] == public_identity(bundle, bundle_id)
    assert set(frozen_plan) == {'input', 'selection'}
    encoded = json.dumps(frozen_plan)
    for forbidden in ('GOLD_SECRET', 'SECRET-GOLD', 'question', 'references', 'gold',
                      'documents', 'source_url', str(tmp_path)):
        assert forbidden not in encoded


def test_selection_derives_partitions_and_keeps_canonical_order_and_complete_materials(tmp_path):
    inputs, bundle, bundle_id = input_fixture(tmp_path)
    before = copy.deepcopy(bundle)
    selection = inputs.selection('registered', case_ids=['qasper:q3', 'qasper:q'],
                                 expected_bundle_id=bundle_id)
    assert selection == dict(kind='explicit', case_ids=['qasper:q', 'qasper:q3'],
                             partition_ids=[p['partition_id'] for p in bundle['partitions']],
                             selection_hash=fingerprint(dict(kind='explicit',
                                 case_ids=['qasper:q', 'qasper:q3'],
                                 partition_ids=[p['partition_id'] for p in bundle['partitions']])))
    assert inputs.selection('registered', case_ids=['qasper:q', 'qasper:q3'],
                            expected_bundle_id=bundle_id) == selection
    assert load_bundle(tmp_path / 'bundle') == before
    product = partition_bundle(load_bundle(tmp_path / 'bundle'), selection['partition_ids'][0],
                               request_revision='notebook-request-v3')
    assert len(product['questions']) == 2  # Selecting one case did not rewrite its partition.
    assert product['documents'] == [before['documents'][0]]
    assert 'Fish swim.' in product['documents'][0]['text']
    assert 'Figure 1: Bird counts.' in product['documents'][0]['text']


@pytest.mark.parametrize('bad', [[], [''], [' '], ['qasper:q', 'qasper:q'],
                                    ['qasper:q '], ['qasper:\tq'], ['unknown'], None, 'qasper:q', [4]])
def test_invalid_explicit_case_selection_is_rejected(tmp_path, bad):
    inputs, _, bundle_id = input_fixture(tmp_path)
    with pytest.raises(ValueError):
        inputs.selection('registered', case_ids=bad, expected_bundle_id=bundle_id)


def test_partition_only_or_caller_partition_selection_is_not_a_command(tmp_path):
    inputs, bundle, bundle_id = input_fixture(tmp_path)
    with pytest.raises(TypeError):
        inputs.selection('registered', partition_ids=[bundle['partitions'][0]['partition_id']],
                         expected_bundle_id=bundle_id)
    with pytest.raises(TypeError):
        inputs.canonical('registered', case_ids=['qasper:q'], partition_ids=['unrelated'],
                         expected_bundle_id=bundle_id)


@pytest.mark.parametrize('expected', [None, '', 'wrong', 'A' * 64, 7])
def test_new_selection_requires_valid_expected_bundle_identity(tmp_path, expected):
    inputs, _, _ = input_fixture(tmp_path)
    with pytest.raises(ValueError):
        inputs.canonical('registered', case_ids=['qasper:q'], expected_bundle_id=expected)


def test_changed_bundle_identity_is_conflict_and_full_canonical_is_verified(tmp_path):
    inputs, bundle, bundle_id = input_fixture(tmp_path)
    with pytest.raises(Conflict):
        inputs.canonical('registered', case_ids=['qasper:q'], expected_bundle_id='0' * 64)
    # Rehashing modified derived data must not make a noncanonical subset valid.
    path = tmp_path / 'bundle' / 'cases.jsonl'
    path.write_text(json.dumps(bundle['cases'][0]) + '\n')
    bundle['manifest']['files']['cases.jsonl'] = digest(path)
    (tmp_path / 'bundle' / 'manifest.json').write_text(json.dumps(bundle['manifest']))
    with pytest.raises(ValueError, match='canonical'):
        inputs.canonical('registered', case_ids=['qasper:q'], expected_bundle_id=bundle_id)


def test_old_adaptation_and_result_snapshot_are_rejected(tmp_path):
    freeze(tmp_path / 'old', 'qasper', qasper_raw(), revision='notebook-data-v2')
    with pytest.raises(ValueError, match='notebook-data-v3'):
        NotebookPlanInputs({'old': tmp_path / 'old/bundle'}).describe('old')
    snapshot = tmp_path / 'snapshot'
    snapshot.mkdir()
    (snapshot / 'snapshot.json').write_text('{}')
    (snapshot / 'COMMIT.json').write_text('{}')
    with pytest.raises(ValueError):
        NotebookPlanInputs({'snapshot': snapshot}).describe('snapshot')


def test_missing_source_and_malformed_private_source_errors_are_sanitized(tmp_path):
    inputs = NotebookPlanInputs({'gone': tmp_path / 'PRIVATE-path'})
    with pytest.raises(FileNotFoundError) as exc:
        inputs.describe('gone')
    assert str(tmp_path) not in str(exc.value) and exc.value.filename is None
    private = tmp_path / 'PRIVATE-path'
    private.mkdir()
    (private / 'manifest.json').write_text(json.dumps(dict(format='private-format',
                                                        path=str(private))))
    with pytest.raises(ValueError) as exc:
        inputs.describe('gone')
    assert str(private) not in str(exc.value)
    with pytest.raises(ValueError):
        inputs.describe('unknown')


def test_freeze_reads_one_complete_bundle_and_uses_that_operation_only(tmp_path, monkeypatch):
    inputs, bundle, bundle_id = input_fixture(tmp_path)
    from rag_eval.workbench import plan_inputs
    real_load = plan_inputs.load_bundle
    calls = []
    def once(path):
        calls.append(path)
        if len(calls) > 1:
            raise AssertionError('one freeze must not reread a changing source')
        return real_load(path)
    monkeypatch.setattr(plan_inputs, 'load_bundle', once)
    assert inputs.canonical('registered', case_ids=['qasper:q'],
                            expected_bundle_id=bundle_id)['input']['bundle_id'] == bundle_id
    assert len(calls) == 1


def test_malformed_source_os_error_cannot_disclose_private_paths(tmp_path, monkeypatch):
    inputs, _, _ = input_fixture(tmp_path)
    from rag_eval.workbench import plan_inputs
    def private_error(path):
        raise PermissionError(13, 'PRIVATE-OS-diagnostic', str(tmp_path / 'PRIVATE-source'))
    monkeypatch.setattr(plan_inputs, 'load_bundle', private_error)
    with pytest.raises(ValueError) as exc:
        inputs.describe('registered')
    assert 'PRIVATE' not in str(exc.value) and str(tmp_path) not in str(exc.value)


@pytest.mark.parametrize('input_id', ['', ' ', ' registered', 'registered\n', 1])
def test_input_id_is_exact_and_never_trimmed(tmp_path, input_id):
    inputs, _, _ = input_fixture(tmp_path)
    with pytest.raises(ValueError):
        inputs.describe(input_id)


# These are synthetic secrets, deliberately valid source revision text. The
# public representation must not depend on trying to recognize paths or URLs.
PRIVATE_REVISIONS = [
    '/private/owner-only/REVISION_PATH_SECRET/release.txt',
    'https://fixture-user:REVISION_CREDENTIAL_SECRET@example.invalid/release?token=QUERY_SECRET',
]


def revision_fixture(tmp_path, revision):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / 'raw').write_text(json.dumps(qasper_raw()))
    (tmp_path / 'source').write_text(json.dumps(dict(
        dataset='qasper', split='test', revision=revision,
        source_url='https://example.invalid/public', license='fixture')))
    bundle = prepare('qasper', tmp_path / 'raw', tmp_path / 'source', tmp_path / 'bundle',
                     adaptation_revision='notebook-data-v3')
    return NotebookPlanInputs({'registered': tmp_path / 'bundle'}), bundle, fingerprint(bundle['manifest'])


@pytest.mark.parametrize('revision', PRIVATE_REVISIONS, ids=['private-absolute-path', 'credential-url'])
def test_private_canonical_revision_is_an_opaque_public_content_identity(tmp_path, revision):
    inputs, bundle, bundle_id = revision_fixture(tmp_path, revision)
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    # This is a real complete canonical bundle; rejecting it is not a fix.
    assert load_bundle(tmp_path / 'bundle')['manifest']['source']['revision'] == revision
    description = inputs.describe('registered')
    canonical = inputs.canonical('registered', case_ids=['qasper:q'], expected_bundle_id=bundle_id)
    for value in (description, canonical['input']):
        assert value['source_revision'] == 'sha256:' + fingerprint(revision)
        assert value['bundle_id'] == bundle_id
        encoded = json.dumps(value)
        assert revision not in encoded
        for marker in ('REVISION_PATH_SECRET', 'REVISION_CREDENTIAL_SECRET', 'QUERY_SECRET'):
            assert marker not in encoded
    assert {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()} == before
