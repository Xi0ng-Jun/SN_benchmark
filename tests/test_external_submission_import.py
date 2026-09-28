import json
from pathlib import Path
import subprocess
import sys

import pytest

from rag_eval.artifacts import save_json
from rag_eval.notebook_bundle import prepare
from rag_eval.external_submission_import import import_external_predictions


def make_bundle(tmp_path):
    source = tmp_path / 'input'
    source.mkdir()
    (source / 'raw.jsonl').write_text(json.dumps({
        'meeting_transcripts': [{'speaker': 'A', 'content': 'Discuss delivery.'}],
        'general_query_list': [
            {'query': 'Summarize the meeting.', 'answer': 'SECRET-GOLD'},
            {'query': 'What happened?', 'answer': 'SECRET-GOLD'},
        ],
        'specific_query_list': [],
    }) + '\n')
    save_json(source / 'source.json', {
        'dataset': 'qmsum', 'split': 'test', 'revision': 'fixture',
        'source_url': 'https://example.org/qmsum', 'license': 'fixture',
    })
    return prepare('qmsum', source / 'raw.jsonl', source / 'source.json', tmp_path / 'bundle')


def method():
    return {
        'name': 'author-output', 'kind': 'reference', 'citation_style': 'none',
        'model_identity': {'model': 'fixture-author'},
        'input_policy': 'frozen-source-documents',
        'configuration': {'track': 'ordinary', 'comparison_category': 'recomputed-subset'},
    }


def source_files(tmp_path, *, rows=None, mappings=None):
    source = tmp_path / 'external'
    source.mkdir()
    save_json(source / 'predictions.json', rows if rows is not None else [{
        'external_id': 'author-q1', 'status': 'success', 'answer': 'Discussed delivery.',
        'evidence': ['turn-1'], 'ranking': [{'id': 'turn-1', 'rank': 1}],
    }])
    case_map = tmp_path / 'case-map.json'
    save_json(case_map, {'format': 'external-case-map-v1', 'mappings': mappings if mappings is not None else [{
        'external_id': 'author-q1', 'case_id': 'qmsum:0:general:0', 'source_row': 0,
        'decision': 'unique',
    }]})
    return source, case_map


def test_import_preserves_raw_prediction_provenance_and_validates_submission(tmp_path):
    bundle = make_bundle(tmp_path)
    source, case_map = source_files(tmp_path)

    output = import_external_predictions(bundle_dir=tmp_path / 'bundle', source_dir=source,
                                         method=method(), case_map=case_map,
                                         output_dir=tmp_path / 'imported')

    submission = json.loads((output / 'submission.json').read_text())
    row = submission['predictions'][0]
    assert output == (tmp_path / 'imported').resolve()
    assert row['case_id'] == 'qmsum:0:general:0'
    assert row['prediction'] == 'Discussed delivery.'
    assert row['record']['external_id'] == 'author-q1'
    assert row['record']['source_row'] == 0
    assert row['record']['raw_prediction']['answer'] == 'Discussed delivery.'
    assert row['record']['evidence'] == ['turn-1']
    assert row['record']['ranking'][0]['rank'] == 1
    assert len(row['record']['source_sha256']) == 64
    assert submission['method']['configuration']['external_source']['case_map_sha256']
    audit = json.loads((output / 'import-audit.json').read_text())
    assert audit['mapped_rows'] == 1
    assert audit['uncovered_case_ids'] == ['qmsum:0:general:1']


def test_import_can_declare_a_complete_explicit_subset(tmp_path):
    bundle = make_bundle(tmp_path)
    source, case_map = source_files(tmp_path)

    output = import_external_predictions(bundle_dir=tmp_path / 'bundle', source_dir=source,
                                         method=method(), case_map=case_map,
                                         case_ids=['qmsum:0:general:0'],
                                         output_dir=tmp_path / 'imported')

    submission = json.loads((output / 'submission.json').read_text())
    assert submission['scope'] == 'subset'
    assert submission['case_ids'] == ['qmsum:0:general:0']
    assert submission['coverage']['generation_complete'] is True
    assert json.loads((output / 'import-audit.json').read_text())['scope_case_ids'] == ['qmsum:0:general:0']


def test_import_uses_model_answer_when_source_also_carries_gold_observation(tmp_path):
    bundle = make_bundle(tmp_path)
    source, case_map = source_files(tmp_path, rows=[{
        'external_id': 'author-q1', 'model_answer': 'model output',
        'gold_answer': 'SECRET-GOLD', 'question_type': 'general_query',
    }])
    output = import_external_predictions(bundle_dir=tmp_path / 'bundle', source_dir=source,
                                         method=method(), case_map=case_map,
                                         output_dir=tmp_path / 'imported')
    row = json.loads((output / 'submission.json').read_text())['predictions'][0]
    assert row['prediction'] == 'model output'
    assert row['record']['raw_prediction']['gold_answer'] == 'SECRET-GOLD'


def test_cli_subset_file_keeps_missing_cases_in_declared_scope(tmp_path):
    make_bundle(tmp_path)
    source, case_map = source_files(tmp_path)
    save_json(tmp_path / 'method.json', method())
    case_file = tmp_path / 'case-ids.txt'
    case_file.write_text('qmsum:0:general:0\n')
    command = [sys.executable, str(Path(__file__).parents[1] / 'scripts/benchmark_protocol.py'),
               'import-external', '--bundle', str(tmp_path / 'bundle'), '--source', str(source),
               '--method', str(tmp_path / 'method.json'), '--case-map', str(case_map),
               '--case-id-file', str(case_file)]
    subprocess.run(command + ['--output', str(tmp_path / 'subset')], check=True, capture_output=True, text=True)
    subset = json.loads((tmp_path / 'subset/submission.json').read_text())
    assert subset['case_ids'] == ['qmsum:0:general:0']
    assert subset['coverage']['generation_complete'] is True
    # A declared but absent prediction cannot disappear from the denominator.
    case_file.write_text('qmsum:0:general:0\nqmsum:0:general:1\n')
    subprocess.run(command + ['--output', str(tmp_path / 'full')], check=True, capture_output=True, text=True)
    full = json.loads((tmp_path / 'full/submission.json').read_text())
    assert full['coverage']['planned'] == 2
    assert full['coverage']['missing'] == 1
    assert full['coverage']['generation_complete'] is False


@pytest.mark.parametrize('rows,mappings,match', [
    ([{'external_id': 'author-q1', 'answer': 'one'},
      {'external_id': 'author-q1', 'answer': 'two'}],
     [{'external_id': 'author-q1', 'case_id': 'qmsum:0:general:0'}], 'Duplicate external'),
    ([{'external_id': 'author-q1', 'answer': 'one'}],
     [{'external_id': 'author-q1', 'case_id': 'qmsum:unknown'}], 'unknown case'),
    ([{'external_id': 'author-q1', 'answer': 'one'}],
     [{'external_id': 'author-q1', 'case_id': 'qmsum:0:general:0'},
      {'external_id': 'author-q1', 'case_id': 'qmsum:0:general:0'}], 'Duplicate mapping'),
])
def test_import_rejects_duplicate_unknown_or_ambiguous_mapping(tmp_path, rows, mappings, match):
    bundle = make_bundle(tmp_path)
    source, case_map = source_files(tmp_path, rows=rows, mappings=mappings)
    with pytest.raises(ValueError, match=match):
        import_external_predictions(bundle_dir=tmp_path / 'bundle', source_dir=source,
                                    method=method(), case_map=case_map,
                                    output_dir=tmp_path / 'imported')


def test_import_rejects_source_row_without_mapping(tmp_path):
    bundle = make_bundle(tmp_path)
    source, case_map = source_files(tmp_path, rows=[
        {'external_id': 'author-q1', 'answer': 'one'},
        {'external_id': 'author-q2', 'answer': 'two'},
    ], mappings=[{'external_id': 'author-q1', 'case_id': 'qmsum:0:general:0'}])
    with pytest.raises(ValueError, match='missing mapping'):
        import_external_predictions(bundle_dir=tmp_path / 'bundle', source_dir=source,
                                    method=method(), case_map=case_map,
                                    output_dir=tmp_path / 'imported')


def test_import_rejects_output_inside_source_or_bundle(tmp_path):
    bundle = make_bundle(tmp_path)
    source, case_map = source_files(tmp_path)
    for output in [source / 'nested', tmp_path / 'bundle' / 'nested']:
        with pytest.raises(ValueError, match='outside'):
            import_external_predictions(bundle_dir=tmp_path / 'bundle', source_dir=source,
                                        method=method(), case_map=case_map, output_dir=output)


def test_import_rejects_oracle_method_from_ordinary_track(tmp_path):
    bundle = make_bundle(tmp_path)
    source, case_map = source_files(tmp_path)
    oracle = method()
    oracle['input_policy'] = 'gold-input-oracle-relevant-spans'
    with pytest.raises(ValueError, match='oracle|gold-input'):
        import_external_predictions(bundle_dir=tmp_path / 'bundle', source_dir=source,
                                    method=oracle, case_map=case_map,
                                    output_dir=tmp_path / 'imported')


def test_import_requires_explicit_comparison_category(tmp_path):
    bundle = make_bundle(tmp_path)
    source, case_map = source_files(tmp_path)
    unclassified = method()
    unclassified['configuration'].pop('comparison_category')
    with pytest.raises(ValueError, match='comparison_category'):
        import_external_predictions(bundle_dir=tmp_path / 'bundle', source_dir=source,
                                    method=unclassified, case_map=case_map,
                                    output_dir=tmp_path / 'imported')
