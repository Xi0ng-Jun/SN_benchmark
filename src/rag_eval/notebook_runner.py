"""Explicit SN runs for frozen notebook datasets; no SN imports at module load."""
from __future__ import annotations

from contextlib import ExitStack
import json
from pathlib import Path
import shutil

from .artifacts import digest, save_json, save_jsonl
from .notebook_bundle import LEGACY_REQUEST_REVISION, load_bundle, partition_bundle
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


def predictions(run, cases, product, mode, repo, outputs, *, agent_evaluation=None, session_factory=None,
                qasper_catalogues=None):
    from .benchmark_runtime import prepare_notebook
    from .system_runtime import complete_evidence_checks, is_cancellation, run_system_question
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
    selected = {case['case_id']: case for case in cases}
    documents = {document['id']: document for document in product['documents']}
    with EventJournal(cell / 'attempts.jsonl') as attempts:
        for question in product['questions']:
            if question['case_id'] not in selected:
                continue
            case = selected[question['case_id']]
            update_state(run, 'asking', case_id=question['case_id'])
            attempts(dict(event='started', case_id=question['case_id'], mode=mode))

            def invoke_and_persist():
                try:
                    record = run_system_question(repo, notebook, question, mode, mapping)
                except Exception as exc:
                    if is_cancellation(exc):
                        raise
                    record = dict(question, status='error', answer='', response={},
                                  reason='native invocation/capture failed', error_type=type(exc).__name__)
                if qasper_catalogues is not None:
                    from .qasper_evidence import capture_evidence
                    did, = question['material_document_ids']
                    captured = capture_evidence(repo, record, documents[did], qasper_catalogues[did], mapping)
                    record['qasper_evidence'] = captured
                    if captured['status'] == 'complete':
                        record['predicted_evidence'] = captured['projection']['predicted_evidence']
                # The model has finished. Recover scoring strata from the frozen
                # case; v3's public task intentionally hides answer-type labels.
                record['task'] = case['task']
                if 'adaptation_revision' in case:
                    record['adaptation_revision'] = case['adaptation_revision']
                if 'gold_document_ids' not in question:
                    complete_evidence_checks(repo, record, mapping, gold_document_ids=case['gold_document_ids'])
                record['source_to_document'] = {v['source_id']: key for key, v in mapping.items()}
                try:
                    record['anchor_documents'], record['anchor_mapping_errors'] = _anchor_documents(repo, record, mapping)
                except Exception as exc:
                    record.update(anchor_documents={}, anchor_mapping_errors=[{'reason': 'mapping_failed', 'error_type': type(exc).__name__}])
                outputs(dict(case_id=question['case_id'], sample_id=question['sample_id'], suite=question['suite'],
                             task=case['task'], product_protocol=VERSION, material_role='source_documents',
                             status=record['status'], output_available=bool(record.get('answer', '').strip()),
                             prediction=record.get('answer', ''), product_record=record,
                             reason=record.get('reason'), behavior=record.get('behavior'), trace=record.get('trace')))
                attempts(dict(event='finished', case_id=question['case_id'], mode=mode, status=record['status']))
                return record

            if agent_evaluation is None:
                invoke_and_persist()
            else:
                agent_evaluation.run_case(
                    case_id=question['case_id'], mode=mode, question=question['question'],
                    invoke_and_persist=invoke_and_persist, session_factory=session_factory,
                    metadata={key: case[key] for key in ('suite', 'task', 'sample_id')})


def selected_cases(bundle, product, case_ids=None):
    """Select requests in frozen order; always retain the partition's whole corpus."""
    members = {q['case_id'] for q in product['questions']}
    if case_ids is not None:
        if (not isinstance(case_ids, (list, tuple)) or not case_ids
                or any(not isinstance(cid, str) for cid in case_ids)
                or len(set(case_ids)) != len(case_ids) or not set(case_ids) <= members):
            raise ValueError('Invalid or duplicate case IDs for this notebook partition')
        members = set(case_ids)
    return [case for case in bundle['cases'] if case['case_id'] in members]


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
                if plan['scorer'] == CITATIONS and record.get('evidence_check_status') == 'error':
                    value = dict(status='error', score=None, reason='citation object checks failed after generation',
                                 details={'error_type': record.get('evidence_check_error')})
                else:
                    value = (_product_score(record, CITATIONS, None, plan['result_id']) if plan['scorer'] == CITATIONS
                             else score_case(cases[plan['case_id']], record, plan['scorer']))
            except Exception as exc:
                value = dict(status='error', score=None, reason='scorer failed', details={'error_type': type(exc).__name__})
        sink(result_record(plan, status=value['status'], score=value.get('score'), reason=value.get('reason'),
                           details=value.get('details'), output_available=available,
                           trace=(output or {}).get('trace')))


def execute(*, root, project, bundle_dir, run, mode, partition_id, request_revision=LEGACY_REQUEST_REVISION,
            agent_config=None, case_ids=None, model_config=None):
    """Execute an explicit request revision; the API default preserves old callers.

    Online CLIs explicitly select v3. Frozen v1/v2 runs rebuild using their saved
    request revision, and their historical scoring plans remain unchanged.
    """
    if mode not in {'chunk', 'reasoning'}:
        raise ValueError('Explicit chunk/reasoning mode required')
    root, project, bundle_dir, run = [Path(p).resolve() for p in (root, project, bundle_dir, run)]
    for forbidden in (root / 'src', root / 'scripts', project, bundle_dir):
        if run.is_relative_to(forbidden) or forbidden.is_relative_to(run):
            raise ValueError('Run must be separate from code/product/input')
    if run.exists():
        raise ValueError('Use a new run directory; no implicit resume')
    bundle = load_bundle(bundle_dir)
    product = partition_bundle(bundle, partition_id, request_revision=request_revision)
    cases = selected_cases(bundle, product, case_ids)
    if not cases:
        raise ValueError('Cannot execute an empty partition')
    from .starter_runtime import configure_environment, snapshot_sources, resolve_models, make_adapter
    resolved, models = {}, {}
    if agent_config is not None:
        if (not {'judge_config', 'trajectory'} <= set(agent_config)
                or set(agent_config) - {'judge_config', 'trajectory', 'metrics', 'task_timeout'}
                or type(agent_config['trajectory']) is not bool):
            raise ValueError('Agent evaluation requires explicit judge configuration and trajectory choice')
        from .native_metrics import select_metrics
        from .native_sdk import configure_local_sdk, sdk_timeout_identity
        agent_metrics = select_metrics(agent_config.get('metrics'), trajectory=agent_config['trajectory'])
        configure_local_sdk(task_timeout=agent_config.get('task_timeout'))
        resolved, models = resolve_models(Path(agent_config['judge_config']).resolve(), ['judge'])
    if model_config is not None:
        model_config = Path(model_config).resolve()
    run.mkdir(parents=True, exist_ok=False)
    update_state(run, 'initializing')
    try:
        shutil.copytree(bundle_dir, run / 'input')
        if load_bundle(run / 'input') != bundle:
            raise ValueError('Input changed while copying')
        save_json(run / 'product-bundle.json', product)
        code = snapshot_sources(root, project, run)
        settings, runtime = configure_environment(project, run, product_track=True,
                                                   document_limit=bundle['manifest']['max_documents'],
                                                   **({'model_config': model_config} if model_config is not None else {}))
        tracing = None
        if agent_config is not None:
            from .native_agent import NativeAgentEvaluation, require_native_tracing
            tracing = require_native_tracing()
        identity = dict(source=bundle['manifest'], models={}, code=code,
                        runtime_settings=runtime['comparable_settings_sha256'],
                        product_services=runtime['service_config_sha256'], product_bundle=product['manifest'],
                        audits_sha256=fingerprint([]), track='R',
                        notebook_context=dict(partition_id=partition_id, selected_cases=bundle['manifest']['selected_cases'],
                                              partition_count=bundle['manifest']['partition_count']))
        # Comparison cohorts drop per-partition product_bundle; keep the request
        # revision in notebook_context as well, so request versions cannot mix.
        if request_revision != LEGACY_REQUEST_REVISION:
            identity['notebook_context']['request_revision'] = request_revision
        qasper_catalogues = bundle.get('qasper_evidence_catalogues') if request_revision == 'notebook-request-v3' else None
        if qasper_catalogues is not None:
            from .qasper_evidence import identity as evidence_identity
            identity['qasper_evidence'] = evidence_identity()
        if case_ids is not None:
            identity['notebook_context']['case_ids'] = [case['case_id'] for case in cases]
        if agent_config is not None:
            identity['agent_evaluation'] = {'protocol': tracing.NATIVE_TRACE_VERSION, 'sdk_version': '4.2.2',
                                            'metrics': list(agent_metrics), 'sdk_timeout': sdk_timeout_identity(),
                                            'trajectory': agent_config['trajectory'], 'judge': models['judge']}
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
            agent_evaluation = None
            if agent_config is not None:
                judge_events = stack.enter_context(EventJournal(run / 'judge-events.jsonl'))
                judge = make_adapter(resolved['judge'], 'judge', settings, judge_events)
                agent_evaluation = NativeAgentEvaluation(run / 'agent', judge=judge, judge_identity=models['judge'],
                                                         enable_whole_trace_metrics=agent_config['trajectory'],
                                                         metrics=agent_config.get('metrics'))
                # run_case owns durable summary finalization, including errors
                # and cancellation. A second ExitStack write could mask those.
            predictions(run, cases, product, mode, repo, outputs, agent_evaluation=agent_evaluation,
                        session_factory=tracing.evaluation_session if tracing is not None else None,
                        qasper_catalogues=qasper_catalogues)
            score_outputs(run, cases, planned, scores)
        errors = any(row['status'] == 'error' for name in ('outputs.jsonl', 'scores.jsonl') for row in read_rows(run / name))
        errors = errors or bool(agent_evaluation and agent_evaluation.has_errors)
        errors = errors or any(row.get('product_record', {}).get('qasper_evidence', {}).get('status') == 'error'
                               for row in read_rows(run / 'outputs.jsonl'))
        update_state(run, 'finished_with_errors' if errors else 'finished')
    except BaseException as exc:
        from .system_runtime import is_cancellation
        previous = json.loads((run / 'state.json').read_text(encoding='utf-8'))
        update_state(run, 'interrupted' if is_cancellation(exc) else 'failed',
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
    request_revision = context.get('request_revision', LEGACY_REQUEST_REVISION)
    if request_revision != LEGACY_REQUEST_REVISION:
        expected_context['request_revision'] = request_revision
    product = partition_bundle(frozen, context['partition_id'], request_revision=request_revision)
    cases = selected_cases(frozen, product, context.get('case_ids'))
    if 'case_ids' in context:
        expected_context['case_ids'] = [case['case_id'] for case in cases]
    if context != expected_context or manifest.get('track') != 'R' or manifest.get('release_gate') is not False:
        raise ValueError('Notebook run scope changed')
    if (json.loads((run / 'product-bundle.json').read_text(encoding='utf-8')) != product
            or manifest['identity']['product_bundle'] != product['manifest']):
        raise ValueError('Notebook product materials changed')
    if planned != plan_rows(cases, manifest['run_id'], manifest['protocol_id'], manifest['mode']):
        raise ValueError('Notebook score plan changed')
    if manifest['identity'].get('official_scoring'):
        from .notebook_alce_results import validate_attachment
        validate_attachment(run, manifest, planned)
    elif 'scoring_attachment' in manifest:
        raise ValueError('Scoring attachment has no official scoring identity')
    evidence_policy = manifest['identity'].get('qasper_evidence')
    documents = {d['id']: d for d in product['documents']}
    by_case = {c['case_id']: c for c in cases}
    for row in outputs:
        if row.get('product_protocol') != VERSION or row.get('material_role') != 'source_documents':
            raise ValueError('Notebook output adaptation identity changed')
        if evidence_policy is not None:
            from .qasper_evidence import verify_evidence
            record = row.get('product_record', {})
            saved = record.get('qasper_evidence', {})
            if {k: saved.get(k) for k in evidence_policy} != evidence_policy:
                raise ValueError('QASPER evidence snapshot differs from run identity')
            if row.get('status') != record.get('status') or row.get('prediction') != record.get('answer'):
                raise ValueError('QASPER evidence answer differs from saved output')
            if saved.get('status') == 'complete':
                did, = by_case[row['case_id']]['material_document_ids']
                prediction = verify_evidence(record, documents[did], frozen['qasper_evidence_catalogues'][did])
                if record.get('predicted_evidence') != prediction:
                    raise ValueError('QASPER evidence differs from saved projection')
            elif saved.get('status') != 'error' or 'predicted_evidence' in record:
                raise ValueError('QASPER evidence capture is incomplete or carries partial evidence')
