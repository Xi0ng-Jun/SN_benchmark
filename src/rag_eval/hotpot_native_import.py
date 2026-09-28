"""Adapt HotpotQA's native answer/sp dictionaries through exact question IDs.

Model/track provenance is supplied explicitly; native format alone proves neither.
Unknown predicted supporting tuples remain false positives, and absent support
is not replaced by an empty prediction. No inference or gold projection occurs.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import shutil

from .artifacts import digest, save_json
from .external_submission_import import import_external_predictions
from .notebook_bundle import load_bundle


def prepare_rows(bundle, native):
    if not isinstance(native, dict) or set(native) - {'answer', 'sp'}:
        raise ValueError('Native Hotpot predictions require answer and optional sp dictionaries')
    answers, support = native.get('answer'), native.get('sp', {})
    if not isinstance(answers, dict) or not answers or not isinstance(support, dict):
        raise ValueError('Native Hotpot answer must be nonempty and sp must be a dictionary')
    cases = {case['sample_id']: case for case in bundle['cases']}
    if len(cases) != len(bundle['cases']) or (set(answers) | set(support)) - set(cases):
        raise ValueError('Native Hotpot prediction has ambiguous or unknown question IDs')
    rows, mappings = [], []
    for case in bundle['cases']:
        qid = case['sample_id']
        if qid not in answers and qid not in support:
            continue  # The unified submission keeps this frozen case as missing.
        answer = answers.get(qid, '')
        if not isinstance(answer, str):
            raise ValueError('Native Hotpot answer must be text')
        status = ('missing' if qid not in answers else 'success' if answer.strip() else 'no_answer')
        record = {'native_question_id': qid}
        if qid in support:
            facts = support[qid]
            if (not isinstance(facts, list) or any(
                not isinstance(fact, list) or len(fact) != 2
                or not isinstance(fact[0], str) or type(fact[1]) is not int for fact in facts)):
                raise ValueError('Native Hotpot sp must contain [title, sentence_id] pairs')
            record['predicted_supporting_facts'] = deepcopy(facts)
        rows.append(dict(external_id=qid, prediction=answer, status=status, record=record))
        mappings.append(dict(external_id=qid, case_id=case['case_id'], source_row=len(rows) - 1,
                             decision='exact-official-question-id'))
    return rows, mappings


def import_hotpot_predictions(bundle_dir, source_file, method, output_dir):
    bundle_dir, source_file, output_dir = (Path(p).resolve() for p in (bundle_dir, source_file, output_dir))
    for path in (bundle_dir, source_file.parent):
        if output_dir.is_relative_to(path) or path.is_relative_to(output_dir):
            raise ValueError('Hotpot import output must be outside inputs')
    bundle = load_bundle(bundle_dir)
    if (bundle['manifest']['suite'] != 'hotpotqa'
            or bundle['manifest'].get('adaptation_revision') != 'notebook-data-v3'):
        raise ValueError('Native Hotpot import requires a HotpotQA v3 bundle')
    if method.get('configuration', {}).get('benchmark_setting') != 'distractor':
        raise ValueError('Explicit method configuration.benchmark_setting=distractor required')
    source_bytes = source_file.read_bytes()
    native = json.loads(source_bytes)
    rows, mappings = prepare_rows(bundle, native)
    method = deepcopy(method)
    method['configuration']['native_prediction_source'] = dict(
        format='hotpotqa-answer-sp', sha256=digest(source_file))
    output_dir.mkdir(parents=True, exist_ok=False)
    raw = output_dir / 'raw'
    raw.mkdir()
    shutil.copyfile(source_file, raw / 'predictions.json')
    normalized = output_dir / 'normalized'
    save_json(normalized / 'predictions.json', rows)
    save_json(normalized / 'method.json', method)
    save_json(normalized / 'case-map.json', {'format': 'external-case-map-v1', 'mappings': mappings})
    return import_external_predictions(bundle_dir, normalized, method,
                                       case_map=normalized / 'case-map.json', output_dir=output_dir / 'submission')
