"""SN document ingestion and observed evidence checks for isolated Notebook runs."""
import hashlib
import json
from pathlib import Path
import time

from .artifacts import save_json


def read_json(path, default=None):
    return json.loads(Path(path).read_text()) if Path(path).exists() else default

def prepare_notebook(repo, cell, documents, dataset):
    from app.models.notebooks import NotebookCreate
    from app.services.repository import UploadedSourceFile
    from app.services.batch_ingest import backfill_chunk_embeddings
    state = read_json(cell / 'state.json', {})
    if not state.get('notebook_id'):
        state['notebook_id'] = repo.create_notebook(NotebookCreate(name='Benchmark ' + dataset)).id
        save_json(cell / 'state.json', state)
    notebook = state['notebook_id']
    mapping = read_json(cell / 'document-map.json', {})
    capacity = repo.effective_document_limit(None)
    if capacity < len(documents):
        raise ValueError('Evaluation document capacity is smaller than frozen corpus')
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
        state.update(prepared=True, documents=len(mapping), chunks=len(rows), embedded_chunks=embedded,
                     kg_present=False, effective_document_limit=capacity)
        save_json(cell / 'state.json', state)
    return notebook, mapping

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
