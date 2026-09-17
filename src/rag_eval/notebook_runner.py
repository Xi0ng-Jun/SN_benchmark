"""Explicit SN runs for frozen notebook datasets; no SN imports at module load."""
from __future__ import annotations

from contextlib import ExitStack
import json
from pathlib import Path
import shutil

from .artifacts import digest, save_json, save_jsonl
from .notebook_bundle import load_bundle, partition_bundle
from .notebook_data import VERSION
from .starter_protocol import fingerprint
from .starter_results import EventJournal, planned_result, result_record
from .starter_runner import CITATIONS, _product_score, read_rows, update_state


def metric_specs(case):
    from .notebook_scoring import metric_specs as notebook_specs
    return [*notebook_specs(case), dict(scorer=CITATIONS, metric_role='diagnostic', score_kind='continuous')]


def plan_rows(cases, run_id, protocol_id, mode):
    return [planned_result({**case, 'metric_role': spec['metric_role'], 'score_kind': spec['score_kind']},
                           run_id=run_id, protocol_id=protocol_id, track='R', mode=mode, scorer=spec['scorer'])
            for case in cases for spec in metric_specs(case)]


def _anchor_documents(repo, record, mapping):
    """Resolve only observed anchors against imported object ownership, read-only."""
    sources = {v['source_id']: key for key, v in mapping.items()}
    chunks = {cid: doc for doc, v in mapping.items() for cid in v.get('chunk_ids', [])}
    resolved, errors, seen = {}, [], set()
    for anchor in record.get('response', {}).get('anchors', []):
        key = anchor.get('key')
        if not isinstance(key, str) or not key:
            errors.append({'key': key, 'reason': 'missing_anchor_key'})
            continue
        if key in seen:
            resolved.pop(key, None)
            errors.append({'key': key, 'reason': 'duplicate_anchor_key'})
            continue
        seen.add(key)
        candidates = set()
        for object_id in dict.fromkeys(filter(None, (anchor.get('object_id'), anchor.get('element_id')))):
            if object_id in chunks:
                candidates.add(chunks[object_id])
            else:
                for table in ('source_elements', 'chunks'):
                    rows = repo._connect().execute('SELECT source_id FROM ' + table + ' WHERE id=?', (object_id,)).fetchall()
                    candidates.update(sources[str(row[0])] for row in rows if str(row[0]) in sources)
        claimed_source = anchor.get('source_id')
        if len(candidates) != 1 or (claimed_source and sources.get(claimed_source) not in candidates):
            errors.append({'key': key, 'reason': 'anchor_object_not_uniquely_in_imported_sources'})
        else:
            resolved[key] = next(iter(candidates))
    if hasattr(repo, 'close_local'):
        repo.close_local()
    return resolved, errors


def predictions(run, cases, product, mode, repo, outputs):
    from .benchmark_runtime import prepare_notebook
    from .system_runtime import run_system_question
    from .usage_capture import capture_usage
    from app.core.llm_logging import LLMInteractionLogger
    cell = run / 'product-artifacts'
    cell.mkdir()
    usage = {}
    update_state(run, 'importing')
    try:
        with capture_usage(LLMInteractionLogger) as usage:
            notebook, mapping = prepare_notebook(repo, cell, product['documents'], product['manifest']['suite'])
    finally:
        save_json(run / 'preparation-usage.json', usage)
    with EventJournal(cell / 'attempts.jsonl') as attempts:
        for question in product['questions']:
            update_state(run, 'asking', case_id=question['case_id'])
            attempts(dict(event='started', case_id=question['case_id'], mode=mode))
            try:
                record = run_system_question(repo, notebook, question, mode, mapping)
            except Exception as exc:
                record = dict(question, status='error', answer='', response={},
                              reason='native invocation/capture failed', error_type=type(exc).__name__)
            record['source_to_document'] = {v['source_id']: key for key, v in mapping.items()}
            try:
                record['anchor_documents'], record['anchor_mapping_errors'] = _anchor_documents(repo, record, mapping)
            except Exception as exc:
                record.update(anchor_documents={}, anchor_mapping_errors=[{'reason': 'mapping_failed', 'error_type': type(exc).__name__}])
            outputs(dict(case_id=question['case_id'], sample_id=question['sample_id'], suite=question['suite'],
                         task=question['task'], product_protocol=VERSION, material_role='source_documents',
                         status=record['status'], output_available=bool(record.get('answer', '').strip()),
                         prediction=record.get('answer', ''), product_record=record,
                         reason=record.get('reason'), behavior=record.get('behavior'), trace=record.get('trace')))
            attempts(dict(event='finished', case_id=question['case_id'], mode=mode, status=record['status']))


def score_outputs(run, cases, planned, sink):
    from .notebook_scoring import score_case
    outputs = {row['case_id']: row for row in read_rows(run / 'outputs.jsonl')}
    cases = {case['case_id']: case for case in cases}
    for plan in planned:
        update_state(run, 'scoring', case_id=plan['case_id'], scorer=plan['scorer'])
        output = outputs.get(plan['case_id'])
        available = bool(output and output['output_available'])
        if not output or output['status'] != 'success':
            value = dict(status='unscored', score=None, reason='no successful SN answer: ' + (output['status'] if output else 'missing'))
        else:
            try:
                record = output['product_record']
                value = (_product_score(record, CITATIONS, None, plan['result_id']) if plan['scorer'] == CITATIONS
                         else score_case(cases[plan['case_id']], record, plan['scorer']))
            except Exception as exc:
                value = dict(status='error', score=None, reason='scorer failed', details={'error_type': type(exc).__name__})
        sink(result_record(plan, status=value['status'], score=value.get('score'), reason=value.get('reason'),
                           details=value.get('details'), output_available=available,
                           trace=(output or {}).get('trace')))


def execute(*, root, project, bundle_dir, run, mode, partition_id):
    if mode not in {'chunk', 'reasoning'}:
        raise ValueError('Explicit chunk/reasoning mode required')
    root, project, bundle_dir, run = [Path(p).resolve() for p in (root, project, bundle_dir, run)]
    for forbidden in (root / 'src', root / 'scripts', project, bundle_dir):
        if run.is_relative_to(forbidden) or forbidden.is_relative_to(run):
            raise ValueError('Run must be separate from code/product/input')
    if run.exists():
        raise ValueError('Use a new run directory; no implicit resume')
    bundle = load_bundle(bundle_dir)
    product = partition_bundle(bundle, partition_id)
    members = {q['case_id'] for q in product['questions']}
    cases = [c for c in bundle['cases'] if c['case_id'] in members]
    if not cases:
        raise ValueError('Cannot execute an empty partition')
    from .starter_runtime import configure_environment, snapshot_sources
    run.mkdir(parents=True, exist_ok=False)
    update_state(run, 'initializing')
    try:
        shutil.copytree(bundle_dir, run / 'input')
        if load_bundle(run / 'input') != bundle:
            raise ValueError('Input changed while copying')
        save_json(run / 'product-bundle.json', product)
        code = snapshot_sources(root, project, run)
        settings, runtime = configure_environment(project, run, product_track=True,
                                                   document_limit=bundle['manifest']['max_documents'])
        identity = dict(source=bundle['manifest'], models={}, code=code,
                        runtime_settings=runtime['comparable_settings_sha256'],
                        product_services=runtime['service_config_sha256'], product_bundle=product['manifest'],
                        audits_sha256=fingerprint([]), track='R',
                        notebook_context=dict(partition_id=partition_id, selected_cases=bundle['manifest']['selected_cases'],
                                              partition_count=bundle['manifest']['partition_count']))
        protocol_id = fingerprint({**identity, 'mode': mode})
        planned = plan_rows(cases, run.name, protocol_id, mode)
        save_jsonl(run / 'planned.jsonl', planned)
        save_json(run / 'manifest.json', dict(format='public-starter-run-v1', run_id=run.name,
                  suite=bundle['manifest']['suite'], track='R', mode=mode, protocol_id=protocol_id,
                  pairing_id=fingerprint(identity), models={}, source_manifest=bundle['manifest'],
                  product_protocol=VERSION, planned_sha256=digest(run / 'planned.jsonl'),
                  planned_predictions=len(cases), planned_scores=len(planned), identity=identity,
                  human_calibration='pending', release_gate=False))
        with ExitStack() as stack:
            stack.enter_context(EventJournal(run / 'model-events.jsonl'))
            outputs = stack.enter_context(EventJournal(run / 'outputs.jsonl'))
            scores = stack.enter_context(EventJournal(run / 'scores.jsonl'))
            from app.services.sqlite_repository import SQLiteRepository
            repo = SQLiteRepository(settings)
            stack.callback(repo.close)
            if repo.db_path.resolve() != run / 'runtime/database.db':
                raise ValueError('Database isolation failed')
            predictions(run, cases, product, mode, repo, outputs)
            score_outputs(run, cases, planned, scores)
        errors = any(row['status'] == 'error' for name in ('outputs.jsonl', 'scores.jsonl') for row in read_rows(run / name))
        update_state(run, 'finished_with_errors' if errors else 'finished')
    except BaseException as exc:
        previous = json.loads((run / 'state.json').read_text(encoding='utf-8'))
        update_state(run, 'interrupted' if isinstance(exc, (KeyboardInterrupt, SystemExit)) else 'failed',
                     error_type=type(exc).__name__, failed_phase=previous.get('phase'), case_id=previous.get('case_id'))
        raise


def validate_saved_run(run, manifest, planned, outputs):
    """Rebuild the entire public input/partition/metric contract before reporting."""
    frozen = load_bundle(run / 'input')
    if frozen['manifest'] != manifest['identity']['source'] or frozen['manifest'] != manifest['source_manifest']:
        raise ValueError('Notebook source differs from run identity')
    context = manifest['identity']['notebook_context']
    expected_context = dict(partition_id=context['partition_id'], selected_cases=frozen['manifest']['selected_cases'],
                            partition_count=frozen['manifest']['partition_count'])
    if context != expected_context or manifest.get('track') != 'R' or manifest.get('release_gate') is not False:
        raise ValueError('Notebook run scope changed')
    product = partition_bundle(frozen, context['partition_id'])
    if (json.loads((run / 'product-bundle.json').read_text(encoding='utf-8')) != product
            or manifest['identity']['product_bundle'] != product['manifest']):
        raise ValueError('Notebook product materials changed')
    ids = {q['case_id'] for q in product['questions']}
    cases = [c for c in frozen['cases'] if c['case_id'] in ids]
    if planned != plan_rows(cases, manifest['run_id'], manifest['protocol_id'], manifest['mode']):
        raise ValueError('Notebook score plan changed')
    if manifest['identity'].get('official_scoring'):
        from .notebook_alce_results import validate_attachment
        validate_attachment(run, manifest, planned)
    elif 'scoring_attachment' in manifest:
        raise ValueError('Scoring attachment has no official scoring identity')
    for row in outputs:
        if row.get('product_protocol') != VERSION or row.get('material_role') != 'source_documents':
            raise ValueError('Notebook output adaptation identity changed')
