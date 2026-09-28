"""Audited import of third-party, case-level benchmark predictions.

The importer is deliberately format-light: an external source is a JSON/JSONL
list of rows and a separate explicit mapping assigns each external identifier
to one frozen bundle case.  It records the original row and provenance in the
submission observation, while ``benchmark_submission`` remains the final
identity and coverage validator.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import re

from .artifacts import digest, save_json
from .benchmark_submission import STATUSES, write_submission
from .notebook_bundle import load_bundle


FORMAT = 'external-import-audit-v1'
_PREDICTION_FILES = ('predictions.json', 'raw_predictions.json',
                     'predictions.jsonl', 'raw_predictions.jsonl')
_TEXT_FIELDS = ('prediction', 'model_answer', 'answer', 'summary', 'output', 'text')
_OBSERVATION_FIELDS = {
    'evidence', 'predicted_evidence', 'citations', 'citation',
    'citation_index_to_document_id', 'ranking', 'ranked_items',
    'retrieved', 'retrieval', 'retrieved_documents', 'retrieved_chunks',
    'supporting_facts', 'supporting_fact', 'documents', 'docs',
}
_POLICY_FIELDS = {'input_policy', 'condition', 'track', 'mode', 'retrieval_policy'}
_ORACLE_RE = re.compile(r'(?:oracle|gold[ _-]?input|gold[ _-]?evidence|gold[ _-]?span)', re.I)


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f'Invalid JSON source: {path}') from exc


def _read_predictions(source_file: Path):
    if source_file.suffix == '.jsonl':
        rows = []
        for line_number, line in enumerate(source_file.read_text(encoding='utf-8').splitlines(), 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f'Invalid prediction JSON at source line {line_number}') from exc
            rows.append(row)
        return rows
    payload = _read_json(source_file)
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ('predictions', 'outputs', 'results'):
            if key in payload:
                if not isinstance(payload[key], list):
                    raise ValueError(f'Source field {key} must be a list')
                return payload[key]
    raise ValueError('External prediction source must be a list or contain predictions/outputs/results')


def _prediction_file(source_dir: Path) -> Path:
    candidates = [source_dir / name for name in _PREDICTION_FILES if (source_dir / name).is_file()]
    raw_dir = source_dir / 'raw'
    candidates += [raw_dir / name for name in _PREDICTION_FILES if (raw_dir / name).is_file()]
    if len(candidates) != 1:
        raise ValueError('Source directory must contain exactly one predictions JSON or JSONL file')
    return candidates[0]


def _read_case_map(path: Path):
    payload = _read_json(path)
    if isinstance(payload, list):
        entries = payload
    elif isinstance(payload, dict):
        entries = payload.get('mappings', payload.get('entries'))
    else:
        entries = None
    if not isinstance(entries, list) or not entries:
        raise ValueError('Case map must contain a nonempty mappings list')
    return entries


def _external_id(row, index):
    if not isinstance(row, dict):
        raise ValueError(f'Prediction source row {index} must be an object')
    values = [(key, row[key]) for key in ('external_id', 'id', 'sample_id', 'question_id') if key in row]
    if not values or not isinstance(values[0][1], str) or not values[0][1].strip():
        raise ValueError(f'Prediction source row {index} requires a nonempty external_id')
    if any(value != values[0][1] for _, value in values[1:]):
        raise ValueError(f'Conflicting external identifiers at source row {index}')
    return values[0][1]


def _text_and_status(row, index):
    values = [(key, row[key]) for key in _TEXT_FIELDS if key in row]
    if any(not isinstance(value, str) for _, value in values):
        raise ValueError(f'Prediction text at source row {index} must be a string')
    if values and any(value != values[0][1] for _, value in values[1:]):
        raise ValueError(f'Conflicting prediction text fields at source row {index}')
    text = values[0][1] if values else ''
    status = row.get('status')
    if status is None:
        status = 'success' if text.strip() else 'no_answer'
    if status not in STATUSES:
        raise ValueError(f'Invalid prediction status at source row {index}')
    return text, status


def _has_oracle_marker(value):
    if isinstance(value, str):
        return bool(_ORACLE_RE.search(value))
    if isinstance(value, dict):
        return any(key in _POLICY_FIELDS and isinstance(item, str) and _ORACLE_RE.search(item)
                   for key, item in value.items())
    if isinstance(value, list):
        return any(_has_oracle_marker(item) for item in value)
    return False


def _source_policy_marker(source_dir: Path, rows):
    metadata = source_dir / 'source.json'
    if metadata.is_file() and _has_oracle_marker(_read_json(metadata)):
        return True
    return any(_has_oracle_marker(row) for row in rows)


def _method_has_oracle_marker(method):
    return _has_oracle_marker(method.get('input_policy')) or _has_oracle_marker(method.get('configuration'))


def _record_for(row, *, external_id, source_row, source_file, source_sha256, mapping):
    record = deepcopy(row.get('record', {}))
    if not isinstance(record, dict):
        raise ValueError(f'Prediction source row {source_row} record must be an object')
    for field in _OBSERVATION_FIELDS:
        if field in row:
            record[field] = deepcopy(row[field])
    record.update({
        'external_id': external_id,
        'source_row': source_row,
        'source_file': str(source_file),
        'source_sha256': source_sha256,
        'raw_prediction': deepcopy(row),
        'normalized_prediction': next((row[field] for field in _TEXT_FIELDS if field in row), ''),
        'mapping': deepcopy(mapping),
    })
    return record


def _outside(output: Path, input_path: Path, label: str):
    if output.is_relative_to(input_path) or input_path.is_relative_to(output):
        raise ValueError(f'Import output must be outside {label}')


def import_external_predictions(bundle_dir: Path, source_dir: Path, method: dict, *,
                                case_map: Path, output_dir: Path,
                                case_ids: list[str] | tuple[str, ...] | None = None) -> Path:
    """Convert an audited external source into ``benchmark-submission-v1``.

    The source directory must contain exactly one ``predictions.json[ l]`` (or
    ``raw_predictions`` equivalent).  The case map is intentionally explicit;
    no row order or sample-id heuristic is used.
    """
    bundle_dir, source_dir = Path(bundle_dir).resolve(), Path(source_dir).resolve()
    case_map, output_dir = Path(case_map).resolve(), Path(output_dir).resolve()
    if not source_dir.is_dir():
        raise ValueError('External source directory is required')
    if not case_map.is_file():
        raise ValueError('External case map file is required')
    if not isinstance(method, dict):
        raise ValueError('External method metadata must be an object')
    method_configuration = method.get('configuration')
    category = (method_configuration.get('comparison_category')
                if isinstance(method_configuration, dict) else None)
    if category not in {'recomputed-subset', 'controlled-rerun'}:
        raise ValueError('External method configuration.comparison_category must explicitly declare '
                         'recomputed-subset or controlled-rerun')
    _outside(output_dir, source_dir, 'external source')
    _outside(output_dir, bundle_dir, 'bundle')
    if _method_has_oracle_marker(method):
        raise ValueError('Oracle/gold-input method cannot enter ordinary external import')

    bundle = load_bundle(bundle_dir)
    all_case_ids = [case['case_id'] for case in bundle['cases']]
    all_cases = set(all_case_ids)
    if case_ids is None:
        selected_case_ids = all_case_ids
    elif (not isinstance(case_ids, (list, tuple)) or not case_ids
          or any(not isinstance(case_id, str) for case_id in case_ids)
          or len(set(case_ids)) != len(case_ids) or not set(case_ids) <= all_cases):
        raise ValueError('Invalid external submission case scope')
    else:
        selected_case_ids = [case_id for case_id in all_case_ids if case_id in set(case_ids)]
    source_file = _prediction_file(source_dir)
    rows = _read_predictions(source_file)
    if not rows:
        raise ValueError('External prediction source is empty')
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError('Every external prediction must be an object')
    if _source_policy_marker(source_dir, rows):
        raise ValueError('Oracle/gold-input source cannot enter ordinary external import')
    source_sha256 = digest(source_file)
    map_entries = _read_case_map(case_map)

    by_external = {}
    source_external = {}
    for index, row in enumerate(rows):
        external_id = _external_id(row, index)
        if external_id in source_external:
            raise ValueError(f'Duplicate external prediction ID: {external_id}')
        source_external[external_id] = (index, row)

    by_case = {}
    for map_index, mapping in enumerate(map_entries):
        if not isinstance(mapping, dict):
            raise ValueError(f'Case map row {map_index} must be an object')
        external_id, case_id = mapping.get('external_id'), mapping.get('case_id')
        if not isinstance(external_id, str) or not external_id.strip():
            raise ValueError(f'Case map row {map_index} requires external_id')
        if external_id in by_external:
            raise ValueError(f'Duplicate mapping for external ID: {external_id}')
        if external_id not in source_external:
            raise ValueError(f'Case map references unknown external ID: {external_id}')
        if not isinstance(case_id, str) or case_id not in all_cases:
            raise ValueError(f'Case map references unknown case: {case_id}')
        if case_id not in set(selected_case_ids):
            raise ValueError(f'Case map references out-of-scope case: {case_id}')
        if case_id in by_case:
            raise ValueError(f'Duplicate mapping for case ID: {case_id}')
        source_index = mapping.get('source_row')
        if source_index is not None and (type(source_index) is not int or source_index != source_external[external_id][0]):
            raise ValueError(f'Case map source_row disagrees for external ID: {external_id}')
        decision = mapping.get('decision', 'unique')
        if not isinstance(decision, str) or not decision.strip():
            raise ValueError(f'Case map decision is invalid for external ID: {external_id}')
        normalized = dict(mapping, external_id=external_id, case_id=case_id,
                          source_row=source_external[external_id][0], decision=decision)
        by_external[external_id] = normalized
        by_case[case_id] = normalized

    missing_mapping = sorted(set(source_external) - set(by_external))
    if missing_mapping:
        raise ValueError('Source rows have missing mapping: ' + ', '.join(missing_mapping))
    map_file_sha256 = digest(case_map)
    predictions = []
    for external_id, (source_index, row) in source_external.items():
        text, status = _text_and_status(row, source_index)
        mapping = by_external[external_id]
        predictions.append({
            'case_id': mapping['case_id'],
            'status': status,
            'prediction': text,
            'record': _record_for(row, external_id=external_id, source_row=source_index,
                                  source_file=source_file, source_sha256=source_sha256,
                                  mapping=mapping),
        })

    imported_method = deepcopy(method)
    config = deepcopy(imported_method.get('configuration', {}))
    config['external_source'] = {
        'format': 'external-predictions-v1',
        'prediction_file': str(source_file),
        'prediction_sha256': source_sha256,
        'case_map_file': str(case_map),
        'case_map_sha256': map_file_sha256,
    }
    imported_method['configuration'] = config
    submission_path = write_submission(bundle_dir, output_dir, method=imported_method,
                                       predictions=predictions, case_ids=selected_case_ids)
    submission = json.loads(submission_path.read_text(encoding='utf-8'))
    audit = {
        'format': FORMAT,
        'bundle_id': submission['bundle_id'],
        'method_id': submission['method_id'],
        'source_file': {'path': str(source_file), 'sha256': source_sha256},
        'case_map': {'path': str(case_map), 'sha256': map_file_sha256},
        'source_rows': len(rows),
        'mapped_rows': len(predictions),
        'scope_case_ids': selected_case_ids,
        'status_counts': dict(Counter(row['status'] for row in predictions)),
        'uncovered_case_ids': [case_id for case_id in selected_case_ids if case_id not in by_case],
        'mapping_decisions': [by_external[key] for key in source_external],
    }
    save_json(output_dir / 'import-audit.json', audit)
    return submission_path.parent
