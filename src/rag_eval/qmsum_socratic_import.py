"""Import the fixed Salesforce Socratic SegEnc QMSum test answer release.

The release has one prediction per line, without question IDs. Mapping follows
the documented preprocessing order (meeting, general queries, specific queries)
and is explicitly disclosed as code-order provenance, not a recovered input log.
Only the pinned 281-line release and pinned QMSum test file are admitted here.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil

from .artifacts import digest, save_json
from .external_submission_import import import_external_predictions
from .notebook_bundle import load_bundle
from .identity import fingerprint


MODEL = 'Salesforce/socratic-pretraining-qmsum'
REVISION = 'd127cbc54b974a58e8bd75935f2863d136e0bc3d'
SOURCE_SHA256 = '8cdbbb9e2b1a6bbd8f99aa7d59b7314d3a3659914c4902b627756bbe7d69247e'
DATA_SHA256 = '6bcd428211260ad2efae3af76cbaf6a7f5ae4bb5e1e59c45a4b8e89539cb9208'
CODE_REVISION = 'd0de964b4c26746c11f634f0c1162aa80c27baa5'
PREPROCESSING_REVISION = '8666f5023cb4932037d2b907fba9373d325d1bef'
SOURCE_URL = f'https://huggingface.co/{MODEL}/resolve/{REVISION}/test.predictions'


def prepare_rows(bundle, raw_meetings, predictions):
    cases = {case['sample_id']: case for case in bundle['cases']}
    ordered = []
    for meeting_index, meeting in enumerate(raw_meetings):
        for kind in ('general', 'specific'):
            for query_index, query in enumerate(meeting[kind + '_query_list']):
                sample_id = f'{meeting_index}:{kind}:{query_index}'
                case = cases.get(sample_id)
                if (case is None or case['question'] != query['query']
                        or case['gold']['answer'] != query['answer']):
                    raise ValueError('QMSum source question/reference differs from frozen case: ' + sample_id)
                ordered.append(case)
    if (len(cases) != len(bundle['cases']) or len(ordered) != len(cases)
            or len(predictions) != len(ordered)):
        raise ValueError('QMSum prediction count must equal the complete canonical query count')
    rows, mappings = [], []
    for index, (case, prediction) in enumerate(zip(ordered, predictions)):
        if not isinstance(prediction, str):
            raise ValueError('QMSum prediction must be text')
        external_id = f'socratic-test:{index}'
        rows.append(dict(external_id=external_id, prediction=prediction,
                         status='success' if prediction.strip() else 'no_answer',
                         record={'native_source': dict(source_sha256=SOURCE_SHA256, line_index=index,
                                                        model_revision=REVISION)}))
        mappings.append(dict(external_id=external_id, case_id=case['case_id'], source_row=index,
                             decision='published-code-order: meetings; general then specific; query order preserved'))
    return rows, mappings


def import_socratic_predictions(bundle_dir: Path, source_file: Path, output_dir: Path):
    bundle_dir, source_file, output_dir = (Path(p).resolve() for p in (bundle_dir, source_file, output_dir))
    for source in (bundle_dir, source_file.parent):
        if output_dir.is_relative_to(source) or source.is_relative_to(output_dir):
            raise ValueError('Socratic output must be outside input directories')
    if digest(source_file) != SOURCE_SHA256:
        raise ValueError('Fixed Socratic prediction SHA256 mismatch')
    bundle = load_bundle(bundle_dir)
    if (bundle['manifest']['suite'] != 'qmsum'
            or bundle['manifest'].get('adaptation_revision') != 'notebook-data-v3'
            or digest(bundle_dir / 'raw-data') != DATA_SHA256):
        raise ValueError('Socratic import requires the fixed QMSum test notebook-data-v3 bundle')
    raw = [json.loads(line) for line in (bundle_dir / 'raw-data').read_text().splitlines() if line.strip()]
    predictions = source_file.read_text(encoding='utf-8').splitlines()
    rows, mappings = prepare_rows(bundle, raw, predictions)
    method = dict(
        name='qmsum-socratic-segenc-author-test', kind='reference', citation_style='none',
        model_identity=dict(repository=MODEL, revision=REVISION,
                            architecture='BartForMultiConditionalGeneration',
                            exact_generation_checkpoint='release-associated; no run-level weight hash'),
        input_policy='author full-transcript SegEnc preprocessing; 32 overlapping chunks, no relevant-span selection',
        configuration=dict(
            comparison_category='recomputed-subset', source_url=SOURCE_URL,
            source_sha256=SOURCE_SHA256, model_revision=REVISION, code_revision=CODE_REVISION,
            preprocessing_repository='salesforce/query-focused-sum',
            preprocessing_revision=PREPROCESSING_REVISION,
            mapping_basis='published preprocessing/inference order; author run input manifest unavailable',
            generation_conditions='example script: chunks=32, stride=true, source_len=512, generation_max_len=512, seed=1; actual run overrides unavailable',
            input_preprocessing='speaker prefix; remove brace annotations and empty utterances; normalize AMI/LCD/PMS/TV; join full transcript',
            comparison_claim='descriptive answer comparison; different training and generation conditions; not paper reproduction',
            original_scoring='Stanza plus SummEval; replaced by the same frozen Perl/HMNet-seg profile used for SN'))
    output_dir.mkdir(parents=True, exist_ok=False)
    raw_dir = output_dir / 'raw'
    raw_dir.mkdir()
    shutil.copyfile(source_file, raw_dir / 'test.predictions')
    normalized = output_dir / 'normalized'
    save_json(normalized / 'predictions.json', rows)
    save_json(normalized / 'case-map.json', {'format': 'external-case-map-v1', 'mappings': mappings})
    save_json(normalized / 'method.json', method)
    save_json(normalized / 'source.json', dict(
        source_url=SOURCE_URL, source_sha256=SOURCE_SHA256, raw_data_sha256=DATA_SHA256,
        bundle_id=fingerprint(bundle['manifest']), mapped_count=len(rows),
        mapping_basis=method['configuration']['mapping_basis'],
        empty_answers=sum(not p.strip() for p in predictions),
        adapter_sha256=digest(Path(__file__))))
    (normalized / 'case-ids.txt').write_text('\n'.join(c['case_id'] for c in bundle['cases']) + '\n')
    return import_external_predictions(bundle_dir, normalized, method,
                                       case_map=normalized / 'case-map.json',
                                       output_dir=output_dir / 'submission')
