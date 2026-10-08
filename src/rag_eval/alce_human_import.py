"""Recover real ALCE model answers from the author's fixed human-evaluation sample.

Human/automatic labels remain in the immutable source file, never become scores.
The sample does not contain the original shown-doc list. This adapter imports
answer text only and explicitly prevents full citation scoring of invented docs.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil

from .artifacts import digest, save_json
from .external_submission_import import import_external_predictions
from .notebook_alce import OFFICIAL_REVISION, OFFICIAL_URL
from .notebook_bundle import load_bundle
from .identity import fingerprint


SOURCE_PATH = 'human_eval/human_eval_citations_completed.json'
SOURCE_SHA256 = 'cfed9293752413d7c7631f36524dd4ee9ef58b209cdf9c63f6fc1e280b43cca6'


def _variant(task, key):
    retriever = 'gtr' if task == 'asqa' else 'bm25'
    prefix = f'{task}-gpt-35-turbo-{retriever}'
    interactive = 'interact_search_summary' if task == 'asqa' else 'interact_doc_id_extraction'
    choices = {
        f'{prefix}-shot2-ndoc5-42-azure.json': ('vanilla', 'gpt-35-turbo', 5),
        f'{prefix}_{interactive}-shot2-ndoc10-42-azure.json': ('interactive', 'gpt-35-turbo', 10),
        f'{prefix}-shot2-ndoc5-42-azure-sample4.json.rerank': ('rerank', 'gpt-35-turbo', 5),
        f'{task}-vicuna-13b-rigorous-shot2-ndoc3-42.json': ('vicuna', 'vicuna-13b', 3),
    }
    if key not in choices:
        raise ValueError('Unknown fixed ALCE human-evaluation method: ' + key)
    return choices[key]


def prepare_method(bundle, key, source_rows, *, source_sha256):
    tasks = {case['task'] for case in bundle['cases']}
    if len(tasks) != 1 or not tasks <= {'asqa', 'eli5'}:
        raise ValueError('ALCE human-evaluation import requires one ASQA or ELI5 bundle')
    task, = tasks
    variant, model_label, ndoc = _variant(task, key)
    cases = {}
    for case in bundle['cases']:
        if case['question'] in cases:
            raise ValueError('Ambiguous frozen ALCE question text')
        cases[case['question']] = case
    rows, mappings, seen = [], [], set()
    for question_id, native in source_rows.items():
        if question_id == 'overall_results':
            continue
        if (not isinstance(native, dict) or native.get('id') != question_id
                or not isinstance(native.get('output'), str) or native.get('question') not in cases):
            raise ValueError('Unknown question or invalid native ALCE answer: ' + question_id)
        case = cases[native['question']]
        if case['case_id'] in seen:
            raise ValueError('Duplicate ALCE case mapping: ' + case['case_id'])
        seen.add(case['case_id'])
        output = native['output']
        rows.append(dict(external_id=question_id, query=native['question'], prediction=output,
                         status='success' if output.strip() else 'no_answer',
                         record={'native_source': dict(sha256=source_sha256, task=task, method_key=key,
                                                        question_id=question_id)}))
        mappings.append(dict(external_id=question_id, case_id=case['case_id'], source_row=len(rows) - 1,
                             decision='exact-unique-question; native question ID retained'))
    if not rows:
        raise ValueError('Empty ALCE human-evaluation method')
    method = dict(
        name=f'alce-human-{task}-{variant}', kind='reference', citation_style='numeric',
        model_identity=dict(author_model_label=model_label, exact_checkpoint='not recorded in answer file',
                            source_method_key=key, source_revision=OFFICIAL_REVISION),
        input_policy='author-human-evaluation-sample; exact shown documents unavailable',
        configuration=dict(
            comparison_category='recomputed-subset', citation_mapping_status='unavailable',
            source_repository=OFFICIAL_URL, source_revision=OFFICIAL_REVISION, source_sha256=source_sha256,
            author_method_key=key, variant=variant, ndoc_from_method_key=ndoc, shots_from_method_key=2,
            retriever_from_task='gtr' if task == 'asqa' else 'bm25',
            generation_conditions='method key observed; prompt realization, model checkpoint, temperature and retries unverified',
            comparison_claim='descriptive answer comparison on this published sample; not a controlled rerun',
            source_labels='human and automatic labels excluded from scorer inputs'))
    audit = dict(mapped_count=len(rows), scope_selection='author human-evaluation sample; not selected by current scores',
                 empty_answers=sum(not row['prediction'].strip() for row in rows),
                 excluded_aggregate_keys=['overall_results'] if 'overall_results' in source_rows else [],
                 citation_mapping='pending: original shown-doc list absent; human file retains at most 3 citations per sentence',
                 candidate_input_identity='not fully proven from published answers',
                 automatic_text_scoring='ASQA str_em/str_hit available; ELI5 requires model metrics')
    return method, rows, mappings, audit


def import_alce_human(bundle_dir: Path, source_file: Path, output_dir: Path):
    bundle_dir, source_file, output_dir = (Path(p).resolve() for p in (bundle_dir, source_file, output_dir))
    if (output_dir.is_relative_to(bundle_dir) or bundle_dir.is_relative_to(output_dir)
            or source_file.is_relative_to(output_dir) or output_dir.is_relative_to(source_file.parent)):
        raise ValueError('Import output must be outside bundle and source directory')
    if digest(source_file) != SOURCE_SHA256:
        raise ValueError('Fixed ALCE human-evaluation source SHA256 mismatch')
    bundle = load_bundle(bundle_dir)
    if (bundle['manifest']['suite'] != 'alce'
            or bundle['manifest'].get('adaptation_revision') != 'notebook-data-v3'):
        raise ValueError('ALCE human import requires an ALCE notebook-data-v3 bundle')
    tasks = {case['task'] for case in bundle['cases']}
    if len(tasks) != 1 or not tasks <= {'asqa', 'eli5'}:
        raise ValueError('One ASQA or ELI5 bundle required')
    task, = tasks
    source = json.loads(source_file.read_text())[task]
    prepared = [prepare_method(bundle, key, rows, source_sha256=SOURCE_SHA256) for key, rows in source.items()]
    output_dir.mkdir(parents=True, exist_ok=False)
    raw = output_dir / 'raw'
    raw.mkdir()
    shutil.copyfile(source_file, raw / source_file.name)
    save_json(output_dir / 'source.json', dict(
        repository=OFFICIAL_URL, revision=OFFICIAL_REVISION, path=SOURCE_PATH, sha256=SOURCE_SHA256,
        bundle_id=fingerprint(bundle['manifest']), adapter_sha256=digest(Path(__file__))))
    outputs = []
    for method, rows, mappings, audit in prepared:
        root = output_dir / method['configuration']['variant']
        normalized = root / 'normalized'
        save_json(normalized / 'predictions.json', rows)
        save_json(normalized / 'case-map.json', {'format': 'external-case-map-v1', 'mappings': mappings})
        save_json(normalized / 'method.json', method)
        save_json(normalized / 'source.json', audit)
        ids = {m['case_id'] for m in mappings}
        case_ids = [case['case_id'] for case in bundle['cases'] if case['case_id'] in ids]
        (normalized / 'case-ids.txt').write_text('\n'.join(case_ids) + '\n')
        outputs.append(import_external_predictions(bundle_dir, normalized, method,
                                                   case_map=normalized / 'case-map.json', case_ids=case_ids,
                                                   output_dir=root / 'submission'))
    return outputs
