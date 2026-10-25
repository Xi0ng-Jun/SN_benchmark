"""Explicit SN runs for frozen notebook datasets; no SN imports at module load."""
from __future__ import annotations

from contextlib import ExitStack
import json
from pathlib import Path
import re
import shutil
import time

from .artifacts import digest, save_json, save_jsonl
from .benchmark_runtime import DEFAULT_PREPARATION_POLICY, validate_preparation_policy
from .notebook_bundle import LEGACY_REQUEST_REVISION, load_bundle, partition_bundle
from .notebook_data import VERSION
from .identity import fingerprint
from .execution_diagnostics import config_sensitive_values, exception_details, sensitive_strings
from .run_results import EventJournal, planned_result, result_record
from .run_support import CITATIONS, citation_score, iter_rows, read_rows, update_state


def load_case_ids_file(path):
    """Read one frozen case ID per line for reproducible subset runs."""
    path = Path(path)
    try:
        lines = path.read_text(encoding='utf-8').splitlines()
    except OSError as exc:
        raise ValueError(f'Unable to read case ID file: {path}') from exc
    if not lines:
        raise ValueError('Case ID file must contain at least one case ID')
    if any(not line or any(character.isspace() for character in line) for line in lines):
        raise ValueError('Case ID file contains a blank or whitespace-containing case ID')
    if len(set(lines)) != len(lines):
        raise ValueError('Case ID file contains a duplicate case ID')
    return lines


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


PREPARED_SEED_FORMAT = 'sn-prepared-seed-v1'
_SEED_FILES = ('runtime/database.db', 'product-artifacts/state.json', 'product-artifacts/document-map.json')


def _seed_projection(identity, *, allow_source_change):
    from .benchmark_submission import sn_submission_identity
    config = sn_submission_identity(identity)
    if allow_source_change:
        # A candidate that changes only the answer path asks again on the
        # parent's prepared notebook. Every other condition must still match;
        # who runs it (task, candidate association) is not a condition.
        config['code'] = {'versions': (config.get('code') or {}).get('versions')}
        for key in ('runtime_model_services', 'task_worktree', 'candidate'):
            config.pop(key, None)
    return config


def _source_summary(identity):
    code = identity.get('code') or {}
    return {key: code.get(key) for key in ('head_revision', 'head_tree', 'source_manifest_sha256')}


def validate_prepared_seed(seed, *, run, mode, partition_id, identity, preparation_policy,
                           allow_source_change=False):
    """Accept only a prepared run of the same partition and answer conditions.

    The comparison is the export family used for merging runs into one
    submission, so a seeded retry can join its original batch and nothing else.
    allow_source_change additionally accepts a seed prepared by different SN
    source, for candidates whose change is confined to answering; both source
    identities are recorded.
    """
    seed = Path(seed).resolve()
    if seed == run or seed.is_relative_to(run) or run.is_relative_to(seed):
        raise ValueError('Prepared seed must be separate from the new run')
    if any(not (seed / name).is_file() for name in ('manifest.json', *_SEED_FILES)) \
            or not (seed / 'runtime/storage').is_dir():
        raise ValueError('Prepared seed lacks a saved SN runtime or preparation state')
    manifest = json.loads((seed / 'manifest.json').read_text(encoding='utf-8'))
    seed_identity = manifest.get('identity') or {}
    if manifest.get('mode') != mode:
        raise ValueError('Prepared seed uses a different mode')
    if seed_identity.get('notebook_context', {}).get('partition_id') != partition_id \
            or seed_identity.get('product_bundle') != identity['product_bundle']:
        raise ValueError('Prepared seed uses a different partition corpus')
    if (seed_identity.get('preparation_policy') or DEFAULT_PREPARATION_POLICY) != preparation_policy:
        raise ValueError('Prepared seed preparation policy does not match requested preparation policy')
    left = _seed_projection(seed_identity, allow_source_change=allow_source_change)
    right = _seed_projection(identity, allow_source_change=allow_source_change)
    if fingerprint(left) != fingerprint(right):
        # Name only the differing identity fields; values may be private.
        differing = sorted(key for key in set(left) | set(right) if fingerprint(left.get(key)) != fingerprint(right.get(key)))
        raise ValueError('Prepared seed differs in bundle, SN source, models, settings or request: '
                         + ', '.join(differing))
    return seed, manifest


def install_prepared_seed(seed, run):
    """Copy SN state before the repository opens; refuse references back to the seed."""
    import sqlite3
    runtime, cell = run / 'runtime', run / 'product-artifacts'
    if (runtime / 'database.db').exists() or (runtime / 'storage').exists() or cell.exists():
        raise ValueError('Seeded runtime must start empty')
    shutil.copytree(seed / 'runtime/storage', runtime / 'storage', symlinks=True)
    source = sqlite3.connect(seed.joinpath('runtime/database.db').as_uri() + '?mode=ro', uri=True)
    try:
        target = sqlite3.connect(runtime / 'database.db')
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()
    from .preparation_cache import rewrite_storage_paths
    rewritten = rewrite_storage_paths(runtime / 'database.db', str(seed / 'runtime/storage') + '/',
                                      str(runtime / 'storage') + '/', str(seed), what='seed run')
    cell.mkdir()
    for name in ('state.json', 'document-map.json'):
        shutil.copyfile(seed / 'product-artifacts' / name, cell / name)
    receipt = dict(format=PREPARED_SEED_FORMAT, seed_run=str(seed), seed_run_id=seed.name,
                   seed_manifest_sha256=digest(seed / 'manifest.json'),
                   seed_files_sha256={name: digest(seed / name) for name in _SEED_FILES},
                   rewritten_source_paths=rewritten,
                   storage_files=sum(1 for path in (runtime / 'storage').rglob('*') if path.is_file()),
                   note='SN state copied from a prepared run; documents were not imported again')
    save_json(run / 'prepared-seed.json', receipt)
    return receipt


def _publish_preparation(cache, key, material, receipt, run, lock):
    """Publishing is best effort: a cache failure never fails the run."""
    try:
        state = json.loads((run / 'product-artifacts/state.json').read_text(encoding='utf-8'))
        if not state.get('prepared'):
            raise ValueError('Notebook is not fully prepared')
        stored = cache.store(key, material, run)
    except Exception as exc:
        stored = dict(status='store_failed', cache_key=key, error_type=type(exc).__name__)
    finally:
        lock.release()
    save_json(run / 'preparation-cache.json', dict(receipt, store=stored))


def predictions(run, cases, product, mode, repo, outputs, *, agent_evaluation=None, session_factory=None,
                qasper_catalogues=None, hotpot_evidence=False, multihop_retrieval=False,
                diagnostic_sensitive_values=(), reasoning_protocol='reasoning-v1',
                preparation_policy=DEFAULT_PREPARATION_POLICY, prepared_seed=None, after_preparation=None,
                question_workers=1, case_deadline_seconds=None, partition_deadline=None):
    update_state(run, 'importing')
    from .benchmark_runtime import load_prepared_notebook, prepare_notebook
    from .system_runtime import complete_evidence_checks, is_cancellation, run_system_question
    from .usage_capture import capture_usage
    from app.core.llm_logging import LLMInteractionLogger
    cell = run / 'product-artifacts'
    if prepared_seed is not None:
        notebook, mapping = load_prepared_notebook(cell, product['documents'], preparation_policy=preparation_policy)
        save_json(run / 'preparation-usage.json', {})
    else:
        cell.mkdir()
        usage = {}
        try:
            with capture_usage(LLMInteractionLogger) as usage:
                notebook, mapping = prepare_notebook(repo, cell, product['documents'], product['manifest']['suite'],
                                                     preparation_policy=preparation_policy)
        finally:
            save_json(run / 'preparation-usage.json', usage)
        if after_preparation is not None:
            after_preparation()
    selected = {case['case_id']: case for case in cases}
    documents = {document['id']: document for document in product['documents']}
    questions = [question for question in product['questions'] if question['case_id'] in selected]

    def ask(question):
        with ExitStack() as observation_stack:
            if multihop_retrieval:
                from .sn_retrieval import capture_chunk_ranking
                ranking = observation_stack.enter_context(capture_chunk_ranking(
                    repo, notebook, question,
                    [documents[did] for did in question['material_document_ids']], mapping))
            try:
                record = run_system_question(repo, notebook, question, mode, mapping,
                    diagnostic_directory=run, diagnostic_sensitive_values=diagnostic_sensitive_values,
                    protocol=reasoning_protocol)
            except Exception as exc:
                if is_cancellation(exc):
                    raise
                details = exception_details(exc, phase='asking', directory=run,
                                            sensitive_values=diagnostic_sensitive_values)
                record = dict(question, status='error', answer='', response={},
                              reason='native invocation/capture failed', **details)
        if multihop_retrieval:
            record['retrieval'] = ranking
        return record

    def complete(question, record):
        """Evidence projections and scoring strata after the model has finished."""
        case = selected[question['case_id']]
        if qasper_catalogues is not None:
            from .qasper_evidence import capture_evidence
            did, = question['material_document_ids']
            captured = capture_evidence(repo, record, documents[did], qasper_catalogues[did], mapping)
            record['qasper_evidence'] = captured
            if captured['status'] == 'complete':
                record['predicted_evidence'] = captured['projection']['predicted_evidence']
        if hotpot_evidence:
            from .hotpot_evidence import capture_evidence
            public_documents = [documents[did] for did in question['material_document_ids']]
            captured = capture_evidence(repo, record, public_documents, mapping)
            record['hotpot_evidence'] = captured
            if captured['status'] == 'complete':
                record['predicted_supporting_facts'] = captured['projection']['predicted_supporting_facts']
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
        return record

    with EventJournal(cell / 'attempts.jsonl') as attempts:
        def persist(question, record):
            case = selected[question['case_id']]
            outputs(dict(case_id=question['case_id'], sample_id=question['sample_id'], suite=question['suite'],
                         task=case['task'], product_protocol=VERSION, material_role='source_documents',
                         status=record['status'], output_available=bool(record.get('answer', '').strip()),
                         prediction=record.get('answer', ''), product_record=record,
                         reason=record.get('reason'), behavior=record.get('behavior'), trace=record.get('trace')))
            attempts(dict(event='finished', case_id=question['case_id'], mode=mode, status=record['status']))
            return record

        # Observation captures that patch shared objects and native Agent
        # sessions are not safe to interleave, and an abandoned call cannot be
        # given a complete ranking/trace; those runs keep the sequential path.
        bounded = case_deadline_seconds is not None or partition_deadline is not None
        if (question_workers == 1 and not bounded) or agent_evaluation is not None or multihop_retrieval:
            for question in questions:
                case = selected[question['case_id']]
                update_state(run, 'asking', case_id=question['case_id'])
                attempts(dict(event='started', case_id=question['case_id'], mode=mode))
                invoke_and_persist = lambda question=question: persist(question, complete(question, ask(question)))
                if agent_evaluation is None:
                    invoke_and_persist()
                else:
                    agent_evaluation.run_case(
                        case_id=question['case_id'], mode=mode, question=question['question'],
                        invoke_and_persist=invoke_and_persist, session_factory=session_factory,
                        metadata={key: case[key] for key in ('suite', 'task', 'sample_id')})
        else:
            _ask_bounded(run, questions, mode, attempts, workers=question_workers,
                         case_deadline=case_deadline_seconds, deadline=partition_deadline,
                         answer=lambda question: complete(question, ask(question)),
                         unanswered=lambda question, record: complete(question, record),
                         persist=persist, is_cancellation=is_cancellation)


def _ask_bounded(run, questions, mode, attempts, *, workers, case_deadline, deadline, answer, unanswered,
                 persist, is_cancellation):
    """Ask up to ``workers`` questions at once, each with its own deadline.

    Each question is an independent request (no shared conversation). Outputs
    are journaled in completion order. A question still running at its
    deadline, or not started at the partition deadline, is saved as ``error``
    with its phase; the abandoned call keeps running in a daemon thread and its
    late result is discarded, never overwriting the saved terminal record.
    """
    import queue
    import threading
    lock = threading.Lock()
    completions = queue.Queue()
    pending, inflight = list(questions), {}

    def timeout(question, error_type, phase, message):
        record = dict(question, status='error', answer='', response={}, reason=message,
                      error_type=error_type, error_message=message, failed_phase=phase)
        persist(question, unanswered(question, record))

    def start(question):
        slot = dict(question=question, started=time.monotonic(), abandoned=False, persisted=False)

        def body():
            try:
                row, error = answer(question), None
            except BaseException as exc:
                row, error = None, exc
            with lock:
                if slot['abandoned']:
                    try:
                        attempts(dict(event='late_result_discarded', case_id=question['case_id'], mode=mode,
                                      status=(row or {}).get('status')))
                    except (ValueError, OSError):
                        pass
                    return
                if row is not None:
                    persist(question, row)
                    slot['persisted'] = True
            completions.put((slot, error))
        attempts(dict(event='started', case_id=question['case_id'], mode=mode))
        inflight[question['case_id']] = slot
        threading.Thread(target=body, daemon=True, name='ask-' + question['case_id']).start()

    def abandon(slot, error_type, phase, message):
        with lock:
            if slot['persisted']:
                return False
            slot['abandoned'] = True
        del inflight[slot['question']['case_id']]
        timeout(slot['question'], error_type, phase, message)
        return True

    while pending or inflight:
        now = time.monotonic()
        if deadline is not None and now >= deadline:
            for slot in list(inflight.values()):
                abandon(slot, 'partition_deadline', 'asking', 'partition deadline reached while asking')
            for question in pending:
                timeout(question, 'partition_deadline', 'not_started', 'partition deadline reached before asking')
            pending = []
            if not inflight:
                break
        while pending and len(inflight) < workers:
            start(pending.pop(0))
        update_state(run, 'asking', case_id=next(reversed(inflight), None), in_flight=sorted(inflight))
        limits = [slot['started'] + case_deadline for slot in inflight.values()] if case_deadline else []
        if deadline is not None:
            limits.append(deadline)
        wait = max(0.0, min(limits) - time.monotonic()) if limits else None
        try:
            slot, error = completions.get(timeout=wait)
        except queue.Empty:
            now = time.monotonic()
            for slot in list(inflight.values()):
                if case_deadline and now >= slot['started'] + case_deadline:
                    abandon(slot, 'case_timeout', 'asking', f'case deadline of {case_deadline}s exceeded')
            continue
        inflight.pop(slot['question']['case_id'], None)
        if error is not None:
            # Same as the sequential path: cancellation and capture failures stop the run.
            raise error


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
                    value = (citation_score(record) if plan['scorer'] == CITATIONS
                             else score_case(cases[plan['case_id']], record, plan['scorer']))
            except Exception as exc:
                value = dict(status='error', score=None, reason='scorer failed', details={'error_type': type(exc).__name__})
        sink(result_record(plan, status=value['status'], score=value.get('score'), reason=value.get('reason'),
                           details=value.get('details'), output_available=available,
                           trace=(output or {}).get('trace')))


TASK_ASSOCIATION_FIELDS = frozenset({'task_id', 'worktree_path', 'paseo_workspace_id', 'source_ref'})
CANDIDATE_ASSOCIATION_FIELDS = frozenset({'iteration_id', 'candidate_id', 'attempt', 'frozen_commit',
                                          'preparation_id'})


def _candidate_association(value):
    """Shape of a workbench candidate attempt; the commit is checked after loading."""
    hexed = lambda text, size: type(text) is str and re.fullmatch('[0-9a-f]{%d}' % size, text) is not None
    return (hexed(value.get('iteration_id'), 32) and hexed(value.get('candidate_id'), 32)
            and type(value.get('attempt')) is int and value['attempt'] >= 1
            and hexed(value.get('frozen_commit'), 40) and hexed(value.get('preparation_id'), 64))


FAILURE_FIELDS = ('error_type', 'error_message', 'failed_phase', 'diagnostic_path', 'diagnostic_write_error')


def _saved_failures(run):
    """One streaming pass over saved journals for the terminal error summary."""
    errors, failures = False, []
    for row in iter_rows(run / 'outputs.jsonl'):
        record = row.get('product_record', {})
        errors = (errors or row['status'] == 'error'
                  or any(record.get(key, {}).get('status') == 'error'
                         for key in ('qasper_evidence', 'hotpot_evidence', 'retrieval')))
        if record.get('error_type'):
            failures.append(dict(case_id=row['case_id'],
                                 **{key: record[key] for key in FAILURE_FIELDS if key in record}))
    errors = errors or any(row['status'] == 'error' for row in iter_rows(run / 'scores.jsonl'))
    return errors, failures


def execute(*, root, project, bundle_dir, run, mode, partition_id, request_revision=LEGACY_REQUEST_REVISION,
            agent_config=None, case_ids=None, model_config=None, artifact_root=None, artifact_index_id=None,
            sn_source_ref=None, dependency_requirements=None, expected_interpreter=None, task_worktree=None,
            reasoning_protocol='reasoning-v1', preparation_policy=DEFAULT_PREPARATION_POLICY,
            seed_prepared_run=None, seed_allow_source_change=False, preparation_cache=None,
            question_workers=1, case_deadline_seconds=None, partition_deadline_seconds=None):
    """Execute an explicit request revision; the API default preserves old callers.

    Online CLIs explicitly select v3. Frozen v1/v2 runs rebuild using their saved
    request revision, and their historical scoring plans remain unchanged.
    seed_prepared_run copies a prepared SN notebook of the same partition and
    answer conditions, so only the questions are asked again. preparation_cache
    names a content-addressed cache of prepared notebooks (see
    preparation_cache.py): a verified hit replaces importing, a miss imports
    and publishes the result; either way the run keeps a private copy.
    question_workers asks that many questions of the partition at once;
    case_deadline_seconds / partition_deadline_seconds save a question that
    overruns (or never starts) as an error and let the partition finish.
    """
    started_at = time.monotonic()
    if type(question_workers) is not int or not 1 <= question_workers <= 32:
        raise ValueError('question_workers must be an integer between 1 and 32')
    for value in (case_deadline_seconds, partition_deadline_seconds):
        if value is not None and (type(value) not in (int, float) or not value > 0):
            raise ValueError('Deadlines must be positive seconds')
    validate_preparation_policy(preparation_policy)
    if mode not in {'chunk', 'reasoning'}:
        raise ValueError('Explicit chunk/reasoning mode required')
    from .reasoning_protocol import protocol_identity
    protocol_info = protocol_identity(reasoning_protocol)
    if mode != 'reasoning' and reasoning_protocol != 'reasoning-v1':
        raise ValueError('Reasoning protocol variants require reasoning mode')
    if ((dependency_requirements is None) != (expected_interpreter is None)
            or (dependency_requirements is not None and sn_source_ref is None)):
        raise ValueError('Startup dependency checks require an explicit captured source')
    if task_worktree is not None:
        keys = set(task_worktree) if isinstance(task_worktree, dict) else None
        if (keys not in (TASK_ASSOCIATION_FIELDS, TASK_ASSOCIATION_FIELDS | CANDIDATE_ASSOCIATION_FIELDS)
                or (keys != TASK_ASSOCIATION_FIELDS and not _candidate_association(task_worktree))
                or not sn_source_ref or task_worktree.get('source_ref') != sn_source_ref.get('id')
                or not isinstance(task_worktree.get('task_id'), str) or not task_worktree['task_id']
                or not isinstance(task_worktree.get('worktree_path'), str)
                or not Path(task_worktree['worktree_path']).is_absolute()):
            raise ValueError('Task association must bind the actual captured source')
    root, project, bundle_dir, run = [Path(p).resolve() for p in (root, project, bundle_dir, run)]
    shared_refs = None
    for forbidden in (root / 'src', root / 'scripts', project, bundle_dir):
        if run.is_relative_to(forbidden) or forbidden.is_relative_to(run):
            raise ValueError('Run must be separate from code/product/input')
    if artifact_root is not None:
        store_path = Path(artifact_root).resolve()
        if any(store_path.is_relative_to(p) or p.is_relative_to(store_path)
               for p in (root / 'src', root / 'scripts', project)):
            raise ValueError('Shared store must be separate from code/product/input')
        if store_path.is_relative_to(bundle_dir) or bundle_dir.is_relative_to(store_path):
            # An immutable installed bundle is an owned object of this store.
            # Only that exact verified object may overlap; canonical inputs
            # and arbitrary directories still require separate storage.
            from .artifact_store import ArtifactStore
            from .bundle_index import find_installed_bundle
            shared_refs = find_installed_bundle(bundle_dir, artifact_root, expected_index_id=artifact_index_id)
            if bundle_dir != ArtifactStore(store_path).resolve(shared_refs['bundle']):
                raise ValueError('Shared input must be the verified installed bundle object')
        if run.is_relative_to(store_path) or store_path.is_relative_to(run):
            raise ValueError('Run must be separate from shared artifact store')
    if sn_source_ref is not None and artifact_root is None:
        raise ValueError('Captured SN source requires a shared artifact store')
    if preparation_cache is not None:
        preparation_cache = Path(preparation_cache).resolve()
        if seed_prepared_run is not None:
            raise ValueError('A run seeds from an explicit prepared run or from the cache, not both')
        protected = [root / 'src', root / 'scripts', project, bundle_dir, run]
        if artifact_root is not None:
            protected.append(Path(artifact_root).resolve())
        if any(preparation_cache.is_relative_to(p) or p.is_relative_to(preparation_cache) for p in protected):
            raise ValueError('Preparation cache must be separate from code, product, inputs and the run')
    if run.exists():
        raise ValueError('Use a new run directory; no implicit resume')
    if artifact_root is None:
        bundle = load_bundle(bundle_dir)
        product = partition_bundle(bundle, partition_id, request_revision=request_revision)
    else:
        from .bundle_index import find_installed_bundle, load_partition
        if shared_refs is None:
            shared_refs = find_installed_bundle(bundle_dir, artifact_root, expected_index_id=artifact_index_id)
        capsule = load_partition(artifact_root, shared_refs, partition_id, request_revision)
        bundle = dict(capsule, partitions=[capsule['partition']])
        if not capsule.get('qasper_evidence_catalogues'):
            bundle.pop('qasper_evidence_catalogues', None)
        product = capsule['product']
        if sn_source_ref is not None:
            shared_refs = {
                **shared_refs,
                'sources': {**shared_refs.get('sources', {}), 'sn': dict(sn_source_ref)},
            }
    cases = selected_cases(bundle, product, case_ids)
    if not cases:
        raise ValueError('Cannot execute an empty partition')
    from .runtime_environment import configure_environment, snapshot_sources, resolve_models, make_adapter
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
    if dependency_requirements is not None:
        dependency_requirements = Path(dependency_requirements).resolve()
    generation_private_values = (*sensitive_strings([question.get('question') for question in product['questions']]),
                                 *sensitive_strings([doc.get('text') for doc in product['documents']]),
                                 *config_sensitive_values(model_config))
    # Scoring labels stay at the runner boundary, including for diagnostics.
    # Only already-public inputs are supplied to the generation call below.
    private_values = (*generation_private_values,
                      *sensitive_strings([{key: case.get(key) for key in ('question', 'gold', 'references')}
                                          for case in cases]))
    run.mkdir(parents=True, exist_ok=False)
    observed_provider = cache_lock = None
    try:
        update_state(run, 'initializing')
        if shared_refs is None:
            shutil.copytree(bundle_dir, run / 'input')
            if load_bundle(run / 'input') != bundle:
                raise ValueError('Input changed while copying')
        else:
            from .artifact_store import write_run_refs
            write_run_refs(run, artifact_root, shared_refs)
        save_json(run / 'product-bundle.json', product)
        source_kwargs = {'artifact_root': artifact_root} if artifact_root is not None else {}
        if sn_source_ref is not None:
            source_kwargs['captured_source_ref'] = sn_source_ref
        code = snapshot_sources(root, project, run, **source_kwargs)
        if task_worktree is not None and 'candidate_id' in task_worktree and (
                code.get('head_revision') != task_worktree['frozen_commit']
                or code.get('modified_paths') or code.get('added_paths') or code.get('deleted_paths')):
            raise ValueError('Loaded source differs from the frozen candidate commit')
        environment_kwargs = {
            'document_limit': bundle['manifest']['max_documents'],
        }
        if model_config is not None:
            environment_kwargs['model_config'] = model_config
        if sn_source_ref is not None:
            environment_kwargs.update(source_root=run / 'captured-source', source_identity=code)
        if dependency_requirements is not None:
            environment_kwargs.update(dependency_requirements=dependency_requirements,
                                      expected_interpreter=expected_interpreter)
        settings, runtime = configure_environment(project, run, product_track=True, **environment_kwargs)
        if 'startup_dependencies' in runtime:
            from .runtime_environment import initialize_captured_model_services
            observed_provider, loaded_services = initialize_captured_model_services(
                settings, run, source_identity=code)
            runtime['loaded_model_services'] = loaded_services
            save_json(run / 'runtime-identity.json', runtime)
        from .feature_identity import FEATURE_SETTING_FIELDS, build_feature_identity
        runtime['effective_settings'] = {name: getattr(settings, name) for name in FEATURE_SETTING_FIELDS
                                         if hasattr(settings, name)}
        runtime['preparation_policy'] = preparation_policy
        if reasoning_protocol != 'reasoning-v1':
            runtime['reasoning_protocol'] = protocol_info
        save_json(run / 'runtime-identity.json', runtime)
        tracing = None
        if agent_config is not None:
            from .native_agent import NativeAgentEvaluation, require_native_tracing
            tracing = require_native_tracing()
        identity = dict(source=bundle['manifest'], models={}, code=code,
                        runtime_settings=runtime['comparable_settings_sha256'],
                        product_services=runtime['service_config_sha256'], product_bundle=product['manifest'],
                        audits_sha256=fingerprint([]), track='R', preparation_policy=preparation_policy,
                        notebook_context=dict(partition_id=partition_id, selected_cases=bundle['manifest']['selected_cases'],
                                              partition_count=bundle['manifest']['partition_count']))
        if reasoning_protocol != 'reasoning-v1':
            identity['reasoning_protocol'] = protocol_info
        if task_worktree is not None:
            identity['task_worktree'] = {key: task_worktree[key] for key in sorted(TASK_ASSOCIATION_FIELDS)}
            if 'candidate_id' in task_worktree:
                identity['candidate'] = {key: task_worktree[key]
                                         for key in sorted(CANDIDATE_ASSOCIATION_FIELDS | {'source_ref'})}
        identity['feature_set'] = build_feature_identity(runtime)
        identity['dotenv_sha256'] = runtime.get('dotenv_snapshot', {}).get('sha256')
        if 'startup_dependencies' in runtime:
            from .runtime_dependency_comparison import startup_comparison_projection
            environment = startup_comparison_projection(
                runtime['startup_dependencies'], run_coordinate=str(run))
            identity['runtime_dependencies'] = {
                'format': 'sn-startup-binding-v1',
                'observation_sha256': fingerprint(runtime['startup_dependencies']),
                'requirements_sha256': runtime['startup_dependencies']['requirements']['sha256'],
                'environment_sha256': fingerprint(environment),
                'run_coordinate': str(run),
            }
            identity['runtime_model_services'] = {
                'format': 'sn-loaded-model-services-binding-v1',
                'observation_sha256': fingerprint(runtime['loaded_model_services']['observation']),
                'source_modules_sha256': fingerprint(runtime['loaded_model_services']['source_modules']),
            }
        # Comparison cohorts drop per-partition product_bundle; keep the request
        # revision in notebook_context as well, so request versions cannot mix.
        if request_revision != LEGACY_REQUEST_REVISION:
            identity['notebook_context']['request_revision'] = request_revision
        qasper_catalogues = bundle.get('qasper_evidence_catalogues') if request_revision == 'notebook-request-v3' else None
        if qasper_catalogues is not None:
            from .qasper_evidence import identity as evidence_identity
            identity['qasper_evidence'] = evidence_identity()
        hotpot_evidence = bundle['manifest']['suite'] == 'hotpotqa' and request_revision == 'notebook-request-v3'
        if hotpot_evidence:
            from .hotpot_evidence import identity as hotpot_identity
            identity['hotpot_evidence'] = hotpot_identity()
        multihop_retrieval = (bundle['manifest']['suite'] == 'multihop_rag'
                             and request_revision == 'notebook-request-v3' and mode == 'chunk')
        if multihop_retrieval:
            from .sn_retrieval import identity as ranking_identity
            identity['multihop_retrieval'] = ranking_identity()
        if case_ids is not None:
            identity['notebook_context']['case_ids'] = [case['case_id'] for case in cases]
        seed = seed_record = None
        if seed_prepared_run is not None:
            seed, seed_manifest = validate_prepared_seed(
                seed_prepared_run, run=run, mode=mode, partition_id=partition_id, identity=identity,
                preparation_policy=preparation_policy, allow_source_change=seed_allow_source_change)
            seed_source = _source_summary(seed_manifest.get('identity') or {})
            seed_record = dict(seed_run_id=seed.name, seed_manifest_sha256=digest(seed / 'manifest.json'))
            if seed_source != _source_summary(identity):
                seed_record.update(source_change=True, seed_source=seed_source)
        cache = cache_key = cache_material = cache_receipt = None
        if preparation_cache is not None and seed is None:
            from .preparation_cache import CacheRejected, PreparationCache, preparation_key
            cache = PreparationCache(preparation_cache)
            cache_key, cache_material = preparation_key(
                partition_id=partition_id, product_bundle=product['manifest'],
                document_limit=bundle['manifest']['max_documents'], preparation_policy=preparation_policy,
                code=code, source_root=run / 'captured-source' if sn_source_ref is not None else None,
                settings=settings, run=run, service_config_sha256=runtime.get('service_config_sha256'),
                requirements_sha256=(identity.get('runtime_dependencies') or {}).get('requirements_sha256'),
                evaluator_preparation_sha256=digest(Path(__file__).with_name('benchmark_runtime.py')))
            # Held until this run publishes its own preparation (or ends), so a
            # concurrent run with the same key waits and then copies.
            cache_lock = cache.lock(cache_key)
            try:
                cache_receipt = cache.install(cache_key, cache_material, run)
            except CacheRejected as exc:
                cache_receipt = dict(status='rejected', cache_key=cache_key, reason=str(exc)[:300])
            if cache_receipt is None:
                cache_receipt = dict(status='miss', cache_key=cache_key)
            cache_receipt['lock_waited_seconds'] = cache_lock.waited_seconds
            if cache_receipt['status'] == 'hit':
                cache_lock.release()
            save_json(run / 'preparation-cache.json', cache_receipt)
        cache_hit = cache_receipt is not None and cache_receipt['status'] == 'hit'
        if agent_config is not None:
            identity['agent_evaluation'] = {'protocol': tracing.NATIVE_TRACE_VERSION, 'sdk_version': '4.2.2',
                                            'metrics': list(agent_metrics), 'sdk_timeout': sdk_timeout_identity(),
                                            'trajectory': agent_config['trajectory'], 'judge': models['judge']}
        protocol_id = fingerprint({**identity, 'mode': mode})
        planned = plan_rows(cases, run.name, protocol_id, mode)
        save_jsonl(run / 'planned.jsonl', planned)
        from .artifact_store import reference_identity
        save_json(run / 'manifest.json', dict(**reference_identity(run), format='public-starter-run-v1', run_id=run.name,
                  suite=bundle['manifest']['suite'], track='R', mode=mode, protocol_id=protocol_id,
                  pairing_id=fingerprint(identity), models={}, source_manifest=bundle['manifest'],
                  product_protocol=VERSION, planned_sha256=digest(run / 'planned.jsonl'),
                  planned_predictions=len(cases), planned_scores=len(planned), identity=identity,
                  human_calibration='pending', release_gate=False,
                  **({'prepared_seed': seed_record} if seed is not None else {}),
                  # Disclosure only: a cache hit is not a comparison condition.
                  **({'preparation_cache': {k: cache_receipt[k] for k in ('cache_key', 'status')}}
                     if cache_receipt is not None else {}),
                  **({'prepared_from': {k: cache_receipt[k] for k in ('cache_key', 'source_run', 'entry_sha256')}}
                     if cache_hit else {}),
                  # Scheduling only; disclosed, never a comparison condition.
                  **({'execution_controls': dict(question_workers=question_workers,
                                                 case_deadline_seconds=case_deadline_seconds,
                                                 partition_deadline_seconds=partition_deadline_seconds)}
                     if (question_workers, case_deadline_seconds, partition_deadline_seconds) != (1, None, None)
                     else {})))
        if seed is not None:
            install_prepared_seed(seed, run)
        with ExitStack() as stack:
            model_events = stack.enter_context(EventJournal(run / 'model-events.jsonl'))
            outputs = stack.enter_context(EventJournal(run / 'outputs.jsonl'))
            scores = stack.enter_context(EventJournal(run / 'scores.jsonl'))
            if observed_provider is not None:
                from .runtime_model_identity import capture_model_service_generations
                stack.enter_context(capture_model_service_generations(observed_provider, model_events))
                # Drain accepted futures while their observer and journal are
                # still alive, including a repository-construction failure.
                stack.callback(observed_provider.close)
            from app.services.sqlite_repository import SQLiteRepository
            repo = (SQLiteRepository(settings, model_provider=observed_provider)
                    if observed_provider is not None else SQLiteRepository(settings))
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
                        qasper_catalogues=qasper_catalogues, hotpot_evidence=hotpot_evidence,
                        multihop_retrieval=multihop_retrieval, diagnostic_sensitive_values=generation_private_values,
                        reasoning_protocol=reasoning_protocol, preparation_policy=preparation_policy,
                        prepared_seed=seed if seed is not None else (cache_receipt if cache_hit else None),
                        after_preparation=(lambda: _publish_preparation(cache, cache_key, cache_material,
                                                                        cache_receipt, run, cache_lock))
                        if cache is not None and not cache_hit else None,
                        question_workers=question_workers, case_deadline_seconds=case_deadline_seconds,
                        partition_deadline=started_at + partition_deadline_seconds
                        if partition_deadline_seconds is not None else None)
            score_outputs(run, cases, planned, scores)
        errors, failures = _saved_failures(run)
        errors = errors or bool(agent_evaluation and agent_evaluation.has_errors)
        update_state(run, 'finished_with_errors' if errors else 'finished',
                     **(dict(failures=failures, **failures[0]) if failures else {}))
    except BaseException as exc:
        from .system_runtime import is_cancellation
        try:
            previous = json.loads((run / 'state.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            previous = {'phase': 'initializing'}
        details = exception_details(exc, phase=previous.get('phase', 'initializing'), directory=run,
                                    sensitive_values=private_values)
        try:
            update_state(run, 'interrupted' if is_cancellation(exc) else 'failed',
                         **details, case_id=previous.get('case_id'))
        except OSError as state_error:
            details['state_write_error'] = type(state_error).__name__
        raise
    finally:
        if cache_lock is not None:
            cache_lock.release()
        if observed_provider is not None:
            observed_provider.close()


def execute_oracle(*, source_run, gold_evidence, run, synthesis_service, mode, case_ids=None, llm_client=None):
    """Explicit diagnostic entry using SN's native chunk synthesis boundary.

    The caller supplies a fresh, isolated initialized SN service and approved
    model budget. This does not initialize providers, ingest or retrieve. Native
    context budgeting still applies; intended gold units and observed synthesis
    context are saved separately. Reasoning synthesis requires its own controlled
    entry and is refused here rather than silently substituting chunk behavior.
    """
    if mode != 'chunk':
        raise ValueError('Oracle native entry currently supports explicit chunk synthesis only')
    if not callable(getattr(synthesis_service, '_answer_chunks', None)):
        raise ValueError('Initialized native SN chunk synthesis service required')
    from .oracle_track import execute_oracle_track
    from app.domain.retrieval import RetrievedChunk
    from dataclasses import asdict, is_dataclass

    def generate(request):
        chunks = [RetrievedChunk(chunk_id='oracle:' + fingerprint([u['document_id'], u['source_unit_id']]),
                    source_id=u['document_id'], source_title=u['document_id'], section_path=u['source_unit_id'],
                    text=u['text']) for u in request['context_units']]
        sink = {}
        answer, grounded, anchors = synthesis_service._answer_chunks(
            request['question']['question'], chunks, history='', memory_hits=[], notebook_id='',
            llm_client=llm_client, baseline_sink=sink)
        serialized_anchors = [asdict(a) if is_dataclass(a) else a.model_dump(mode='json')
                              if hasattr(a, 'model_dump') else dict(a) for a in anchors]
        # Native ordered_handles are tuples; persisted captures are public JSON.
        observed = json.loads(json.dumps(sink, ensure_ascii=False))
        return dict(status='success' if str(answer or '').strip() else 'no_answer',
                    prediction=str(answer or ''), grounded=grounded, anchors=serialized_anchors,
                    synthesis_entry='SN._answer_chunks', synthesis_mode='chunk', captures=[observed])

    return execute_oracle_track(source_run, gold_evidence, run, generate=generate, case_ids=case_ids)


def validate_saved_run(run, manifest, planned, outputs, *, reader=None):
    """Rebuild the entire public input/partition/metric contract before reporting."""
    from .run_reader import RunReadContext
    reader = reader or RunReadContext()
    frozen = reader.bundle_for_run(run)
    if frozen['manifest'] != manifest['identity']['source'] or frozen['manifest'] != manifest['source_manifest']:
        raise ValueError('Notebook source differs from run identity')
    context = manifest['identity']['notebook_context']
    expected_context = dict(partition_id=context['partition_id'], selected_cases=frozen['manifest']['selected_cases'],
                            partition_count=frozen['manifest']['partition_count'])
    request_revision = context.get('request_revision', LEGACY_REQUEST_REVISION)
    if request_revision != LEGACY_REQUEST_REVISION:
        expected_context['request_revision'] = request_revision
    product = reader.partition(frozen, context['partition_id'], request_revision)
    cases = reader.selected_cases(frozen, product, context.get('case_ids'))
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
    hotpot_policy = manifest['identity'].get('hotpot_evidence')
    retrieval_policy = manifest['identity'].get('multihop_retrieval')
    documents = {d['id']: d for d in product['documents']}
    by_case = {c['case_id']: c for c in cases}
    public_questions = {q['case_id']: q for q in product['questions']}
    for row in outputs:
        if row.get('product_protocol') != VERSION or row.get('material_role') != 'source_documents':
            raise ValueError('Notebook output adaptation identity changed')
        if retrieval_policy is not None:
            from .sn_retrieval import validate_capture
            if manifest['mode'] != 'chunk' or frozen['manifest']['suite'] != 'multihop_rag':
                raise ValueError('Native ranking observation requires a MultiHop chunk run')
            case = by_case[row['case_id']]
            validate_capture(row.get('product_record', {}).get('retrieval'), public_questions[row['case_id']],
                             [documents[did] for did in case['material_document_ids']], retrieval_policy)
        if hotpot_policy is not None:
            from .hotpot_evidence import validate_prediction
            validate_prediction(row.get('product_record', {}),
                                [documents[did] for did in by_case[row['case_id']]['material_document_ids']],
                                hotpot_policy, row['status'], row['prediction'])
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
