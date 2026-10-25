"""SN document ingestion and observed evidence checks for isolated Notebook runs."""
import hashlib
import json
from contextlib import nullcontext
from pathlib import Path
import time

from .artifacts import save_json


DEFAULT_PREPARATION_POLICY = 'per-source-metadata-v1'
DEFERRED_METADATA_POLICY = 'deferred-notebook-metadata-v1'
PREPARATION_POLICIES = (DEFAULT_PREPARATION_POLICY, DEFERRED_METADATA_POLICY)


def validate_preparation_policy(policy):
    if policy not in PREPARATION_POLICIES:
        raise ValueError('Unknown preparation policy: ' + str(policy))


def read_json(path, default=None):
    return json.loads(Path(path).read_text()) if Path(path).exists() else default

def prepare_notebook(repo, cell, documents, dataset, *, preparation_policy=DEFAULT_PREPARATION_POLICY,
                     workers=1):
    """Import every document, then verify chunks, embeddings and the scale index.

    ``workers=1`` processes each upload synchronously on this thread. With more
    workers, uploads stay sequential on this thread and each source's SN
    pipeline (parse, summarize, metadata, embeddings) runs in a bounded thread
    pool, each task under a copy of this thread's context so SN's ContextVar
    scopes travel with it. Content does not depend on the worker count; the
    document map keeps upload order, per-document processing seconds and the
    order in which documents completed. A failed document fails preparation
    after in-flight work ends; finished documents stay recorded.
    """
    validate_preparation_policy(preparation_policy)
    if type(workers) is not int or not 1 <= workers <= 64:
        raise ValueError('Preparation workers must be an integer between 1 and 64')
    deferred = preparation_policy == DEFERRED_METADATA_POLICY
    metadata_scope = nullcontext
    if deferred:
        try:
            from app.services.notebook_metadata import suppress_notebook_metadata_refresh
        except ImportError as exc:
            raise ValueError('SN does not support deferred notebook metadata refresh') from exc
        if not callable(getattr(repo, '_augment_notebook_meta', None)):
            raise ValueError('SN does not expose the notebook metadata refresh hook')
        metadata_scope = suppress_notebook_metadata_refresh
    from app.models.notebooks import NotebookCreate
    from app.services.repository import UploadedSourceFile
    from app.services.batch_ingest import backfill_chunk_embeddings
    state = read_json(cell / 'state.json', {})
    if state.get('notebook_id') and state.get('preparation_policy', DEFAULT_PREPARATION_POLICY) != preparation_policy:
        raise ValueError('Existing notebook preparation policy does not match requested preparation policy')
    state['preparation_policy'] = preparation_policy
    if not state.get('notebook_id'):
        state['notebook_id'] = repo.create_notebook(NotebookCreate(name='Benchmark ' + dataset)).id
        save_json(cell / 'state.json', state)
    notebook = state['notebook_id']
    mapping = read_json(cell / 'document-map.json', {})
    capacity = repo.effective_document_limit(None)
    if capacity < len(documents):
        raise ValueError('Evaluation document capacity is smaller than frozen corpus')
    # scheduler=None keeps ingestion on this thread, inside the SN ContextVar
    # scope. Other ingestion hooks and all per-source model work remain active.
    with metadata_scope():
        if workers > 1:
            _ingest_concurrently(repo, cell, notebook, documents, mapping, dataset, capacity, workers,
                                 UploadedSourceFile)
        for index, document in enumerate(documents):
            public_id = document['id']
            if public_id in mapping:
                continue
            # Keep original paragraph bytes; titles are metadata, not answer hints.
            content = document['text'].encode('utf-8')
            filename = hashlib.sha256(public_id.encode()).hexdigest()[:24] + '.md'
            started = time.monotonic()
            uploaded = repo.upload_sources(notebook, [UploadedSourceFile(
                file_name=filename, content_type='text/markdown', content=content,
                title=document.get('title') or public_id)], scheduler=None, capacity_limit=capacity)
            mapping[public_id] = {'source_id': uploaded[0].id, 'text_sha256': hashlib.sha256(content).hexdigest(),
                                  'filename': filename, 'ingest_seconds': time.monotonic() - started,
                                  'completion_index': len(mapping) + 1}
            save_json(cell / 'document-map.json', mapping)
            print(dataset, 'import', index + 1, '/', len(documents), flush=True)
    if not state.get('prepared'):
        backfill_chunk_embeddings(repo, notebook, missing_only=True)
        rows = repo._connect().execute('SELECT id, source_id FROM chunks WHERE notebook_id=?', (notebook,)).fetchall()
        for item in mapping.values():
            item['chunk_ids'] = [str(r[0]) for r in rows if str(r[1]) == item['source_id']]
        if any(not m['chunk_ids'] for m in mapping.values()):
            raise ValueError('Document without chunks')
        embedded = repo._connect().execute('SELECT count(*) FROM chunk_embeddings WHERE chunk_id IN (SELECT id FROM chunks WHERE notebook_id=?)', (notebook,)).fetchone()[0]
        if embedded != len(rows):
            raise ValueError('Embedding coverage incomplete')
        if repo._notebook_has_kg(notebook):
            raise ValueError('Unexpected KG in text-only benchmark')
        repo.close_local()
        repo.build_scale_index(notebook)
        save_json(cell / 'document-map.json', mapping)
        if deferred:
            # SN may fall back internally. A returned hook is not proof of a
            # successful model response; retain only what this boundary observes.
            refresh = dict(invoked=True, call_returned=False, source_count=len(mapping))
            state['final_metadata_refresh'] = refresh
            save_json(cell / 'state.json', state)
            started = time.monotonic()
            try:
                repo._augment_notebook_meta(notebook)
                refresh['call_returned'] = True
            finally:
                refresh['elapsed_seconds'] = time.monotonic() - started
                save_json(cell / 'state.json', state)
        state.update(prepared=True, documents=len(mapping), chunks=len(rows), embedded_chunks=embedded,
                     kg_present=False, effective_document_limit=capacity)
        save_json(cell / 'state.json', state)
    return notebook, mapping

def _ingest_concurrently(repo, cell, notebook, documents, mapping, dataset, capacity, workers, UploadedSourceFile):
    """Sequential uploads, bounded concurrent per-source processing (see prepare_notebook)."""
    from concurrent.futures import ThreadPoolExecutor
    import contextvars
    import threading
    lock = threading.Lock()
    order = list(mapping)          # resumed entries first, then upload order
    entries, done, failures = {}, dict(mapping), []

    def publish():
        save_json(cell / 'document-map.json', {key: done[key] for key in order if key in done})

    def process(public_id, source_id):
        started = time.monotonic()
        error = None
        try:
            summary = repo.process_source(source_id)
            if 'failed' in (getattr(summary, 'status', ''), getattr(summary, 'parse_status', '')):
                error = 'source processing failed'
        except Exception as exc:
            error = type(exc).__name__
        with lock:
            entry = dict(entries[public_id], ingest_seconds=time.monotonic() - started)
            if error is not None:
                failures.append(dict(document_id=public_id, source_id=source_id, error=error))
                return
            done[public_id] = dict(entry, completion_index=len(done) + 1)
            publish()
            print(dataset, 'import', len(done), '/', len(documents), flush=True)

    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix='sn-prepare')
    futures, completed = [], False
    try:
        for document in documents:
            public_id = document['id']
            if public_id in mapping:
                continue
            with lock:
                if failures:
                    break  # stop uploading; in-flight documents still finish
            content = document['text'].encode('utf-8')
            filename = hashlib.sha256(public_id.encode()).hexdigest()[:24] + '.md'
            entry = {'text_sha256': hashlib.sha256(content).hexdigest(), 'filename': filename}

            def schedule(source_id, public_id=public_id, entry=entry):
                with lock:
                    entries[public_id] = dict(source_id=source_id, **entry)
                    order.append(public_id)
                # A fresh context copy per task: one Context cannot be entered by two threads.
                futures.append(pool.submit(contextvars.copy_context().run, process, public_id, source_id))

            started = time.monotonic()
            uploaded = repo.upload_sources(notebook, [UploadedSourceFile(
                file_name=filename, content_type='text/markdown', content=content,
                title=document.get('title') or public_id)], scheduler=schedule, capacity_limit=capacity)
            with lock:
                if public_id not in entries:
                    # SN reused an existing same-content source without scheduling it.
                    entries[public_id] = dict(source_id=uploaded[0].id, **entry)
                    order.append(public_id)
                    done[public_id] = dict(entries[public_id], ingest_seconds=time.monotonic() - started,
                                           completion_index=len(done) + 1)
                    publish()
        for future in futures:
            future.result()  # every scheduled document is processed before verification
        completed = True
    finally:
        # Queued work is dropped only when this thread is leaving with an exception.
        pool.shutdown(wait=True, cancel_futures=not completed)
    mapping.clear()
    mapping.update({key: done[key] for key in order if key in done})
    publish()
    if failures:
        state = read_json(cell / 'state.json', {})
        state['preparation_failures'] = failures
        save_json(cell / 'state.json', state)
        raise ValueError(f'{len(failures)} document(s) failed preparation: '
                         + ', '.join(item['document_id'] for item in failures[:10]))


def load_prepared_notebook(cell, documents, *, preparation_policy=DEFAULT_PREPARATION_POLICY):
    """Accept a copied, fully prepared notebook without importing documents again."""
    validate_preparation_policy(preparation_policy)
    state = read_json(cell / 'state.json', {})
    mapping = read_json(cell / 'document-map.json', {})
    if not state.get('prepared') or not state.get('notebook_id'):
        raise ValueError('Seed notebook was not fully prepared')
    if (state.get('preparation_policy') or DEFAULT_PREPARATION_POLICY) != preparation_policy:
        raise ValueError('Seed preparation policy does not match requested preparation policy')
    if set(mapping) != {document['id'] for document in documents}:
        raise ValueError('Seed corpus differs from the frozen partition')
    for document in documents:
        item = mapping[document['id']]
        if item.get('text_sha256') != hashlib.sha256(document['text'].encode('utf-8')).hexdigest():
            raise ValueError('Seed document text differs from the frozen partition')
        if not item.get('source_id') or not item.get('chunk_ids'):
            raise ValueError('Seed document without source or chunks')
    return state['notebook_id'], mapping


def evidence_checks(repo, record, mapping):
    source_to_public = {m['source_id']: key for key, m in mapping.items()}
    public_ids = [source_to_public[s] for s in record.get('source_ids', []) if s in source_to_public]
    gold = set(record['gold_document_ids'])
    citations = record.get('response', {}).get('citations', [])
    existing = 0
    for citation in citations:
        source = citation.get('source_id')
        if source not in source_to_public:
            continue
        element = citation.get('element_id')
        row = repo._connect().execute('SELECT 1 FROM source_elements WHERE id=? AND source_id=?', (element, source)).fetchone()
        chunk = repo._connect().execute('SELECT 1 FROM chunks WHERE id=? AND source_id=?', (element, source)).fetchone()
        existing += bool(row or chunk)
    repo.close_local()
    record['retrieved_document_ids'] = public_ids
    record['deterministic'] = {'evidence_hit': float(bool(gold & set(public_ids))) if record['context_supported'] else None,
                               'evidence_coverage': len(gold & set(public_ids)) / len(gold) if gold and record['context_supported'] else None,
                               'citation_count': len(citations), 'citation_valid_count': existing,
                               'citation_invalid_count': len(citations) - existing,
                               'ranking_available': False}
