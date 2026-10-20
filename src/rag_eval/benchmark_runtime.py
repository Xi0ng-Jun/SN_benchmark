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

def prepare_notebook(repo, cell, documents, dataset, *, preparation_policy=DEFAULT_PREPARATION_POLICY):
    validate_preparation_policy(preparation_policy)
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
                                  'filename': filename, 'ingest_seconds': time.monotonic() - started}
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

def load_prepared_notebook(cell, documents, *, preparation_policy=DEFAULT_PREPARATION_POLICY):
    """Accept a copied, fully prepared notebook without importing documents again."""
    validate_preparation_policy(preparation_policy)
    state = read_json(cell / 'state.json', {})
    mapping = read_json(cell / 'document-map.json', {})
    if not state.get('prepared') or not state.get('notebook_id'):
        raise ValueError('Seed notebook was not fully prepared')
    if state.get('preparation_policy', DEFAULT_PREPARATION_POLICY) != preparation_policy:
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
