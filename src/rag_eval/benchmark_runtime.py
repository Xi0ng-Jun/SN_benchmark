"""Isolated runtime, immutable inputs and auditable native Ask attempts."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import tomllib
import traceback
from datetime import datetime, timezone
from uuid import uuid4

from .artifacts import digest, save_json, save_jsonl
from .datasets import iter_jsonl
from .system_capture import capture_synthesis, final_context
from .usage_capture import capture_usage


def read_json(path, default=None):
    return json.loads(Path(path).read_text()) if Path(path).exists() else default


def append_jsonl(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as handle:
        os.chmod(path, 0o600)
        handle.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + '\n')
        handle.flush()
        os.fsync(handle.fileno())


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def open_runtime(project, cell):
    import sys
    private = cell / 'runtime'
    private.mkdir(parents=True, exist_ok=True)
    overrides = dict(
        DATABASE_URL='sqlite:///' + str(private / 'database.db'),
        SILICON_NOTEBOOK_STORAGE_DIR=str(private / 'storage'),
        LLM_CACHE_PATH=str(private / 'llm-cache.db'),
        EVENT_LOG_DIR=str(private / 'logs'), LLM_LOG_PATH=str(private / 'logs/llm.jsonl'),
        MODEL_SERVICES_CONFIG=str(project / '.local/model-services.toml'),
        USER_UPLOAD_DOCUMENT_LIMIT='200', LLM_CACHE_ENABLED='false',
        AGENT_PROFILE_ENABLED='false', USER_SEARCH_PROFILE_ENABLED='false',
        RETRIEVAL_EXPERIENCE_ENABLED='false', RETRIEVAL_EXPERIENCE_INJECT_ENABLED='false',
        REASONING_CONSULT_MEMORY_ENABLED='false', GENERATED_QUESTION_INDEX_MODE='off',
        CHUNK_KG_OVERLAY_ENABLED='false', KG_AUTO_EXTRACT='false', DEEPEVAL_DISABLE_DOTENV='1',
        DEEPEVAL_TELEMETRY_OPT_OUT='YES', LLM_LOG_ENABLED='true')
    os.environ.update(overrides)
    sys.path.insert(0, str(project / 'backend'))
    os.chdir(cell)
    from app.core.config import Settings
    from app.services.sqlite_repository import SQLiteRepository
    settings = Settings(_env_file=project / '.env')
    for field in ('storage_dir', 'llm_cache_path', 'event_log_dir', 'llm_log_path'):
        if not Path(getattr(settings, field)).resolve().is_relative_to(private):
            raise ValueError('Runtime path isolation failed: ' + field)
    repo = SQLiteRepository(settings)
    if repo.db_path.resolve() != private / 'database.db':
        raise ValueError('Database isolation failed')
    return repo, settings, overrides


def snapshot_invocation(root, project, cell, bundle, mode, settings, overrides):
    revision = subprocess.check_output(['git', '-C', str(project), 'rev-parse', 'HEAD'], text=True).strip()
    tracked = subprocess.check_output(['git', '-C', str(project), 'diff', 'HEAD', '--name-only'], text=True)
    if tracked.strip():
        raise ValueError('Product tracked changes require a new reviewed source snapshot')
    model_path = project / '.local/model-services.toml'
    model_config = tomllib.loads(model_path.read_text())
    inputs = {str(p.relative_to(bundle)): digest(p) for p in sorted(bundle.rglob('*.json*')) if 'raw' not in p.parts}
    settings_json = settings.model_dump(mode='json')
    settings_hash = hashlib.sha256(json.dumps(settings_json, sort_keys=True).encode()).hexdigest()
    identity = {'project_revision': revision, 'model_config_sha256': digest(model_path),
                'settings_sha256': settings_hash,
                'input_hashes': inputs, 'mode': mode}
    existing = read_json(cell / 'identity.json')
    if existing and existing != identity:
        raise ValueError('Run identity changed; use a new run directory')
    save_json(cell / 'identity.json', identity)
    invocation = cell / 'invocations' / uuid4().hex
    invocation.mkdir(parents=True)
    for directory in ('src/rag_eval', 'scripts', 'configs', 'tests'):
        source = root / directory
        if source.exists():
            shutil.copytree(source, invocation / directory, ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copy2(root / 'pyproject.toml', invocation / 'pyproject.toml')
    if not (cell / 'project-source.tar').exists():
        with (cell / 'project-source.tar').open('wb') as output:
            subprocess.run(['git', '-C', str(project), 'archive', revision], stdout=output, check=True)
    save_json(invocation / 'invocation.json', {
        **identity, 'started_at': utc_now(), 'outer_timeout': None,
        'settings_sha256': settings_hash,
        'overrides': overrides,
        'source_hashes': {str(p.relative_to(invocation)): digest(p) for p in invocation.rglob('*.py')},
        'models': {k: {f: v for f, v in s.items() if f in ('kind', 'model', 'protocol', 'max_concurrency')}
                   for k, s in model_config['services'].items()},
        'bindings': model_config['bindings'], 'thinking': model_config.get('thinking', {}),
        'versions': {d.metadata['Name']: d.version for d in importlib.metadata.distributions()},
        'same_generation_and_judge_model': True,
    })
    return identity


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


def run_questions(repo, cell, notebook, mapping, questions, mode, repeat=0, documents=None):
    from app.models.ask import AskRequest
    from app.core.llm_logging import LLMInteractionLogger
    path = cell / ('outputs.jsonl' if repeat == 0 else f'product-repeat-{repeat}.jsonl')
    done = {r['id'] for r in iter_jsonl(path)} if path.exists() else set()
    for question in questions:
        if question['id'] in done:
            continue
        attempt_id = uuid4().hex
        start = time.monotonic()
        append_jsonl(cell / 'attempts.jsonl', {'event': 'started', 'attempt_id': attempt_id,
                      'id': question['id'], 'dataset': question['dataset'], 'mode': mode, 'repeat': repeat, 'at': utc_now()})
        record = {**question, 'mode': mode, 'attempt_id': attempt_id, 'repeat': repeat,
                  'answer': '', 'response': {}, 'status': 'product_error'}
        with capture_usage(LLMInteractionLogger) as usage, capture_synthesis(repo._runtime.ask_component) as calls:
            try:
                response = repo.ask(notebook, AskRequest(question=question['question'], mode=mode))
                record.update(answer=response.answer, response=response.model_dump(mode='json'),
                              status='success' if response.answer.strip() else 'product_error')
                if response.mode != mode:
                    raise ValueError('Native mode mismatch')
                stored = repo._connect().execute('SELECT question, payload FROM answers WHERE id=? AND notebook_id=?',
                    (response.answer_id, notebook)).fetchone()
                record['persistence_verified'] = bool(stored and stored[0] == question['question'].strip()
                    and json.loads(stored[1]).get('answer') == response.answer)
                if not record['persistence_verified']:
                    raise ValueError('Native persisted answer mismatch')
            except Exception as exc:
                record.update(status='product_error', error_type=type(exc).__name__)
                record['error_support_id'] = str(getattr(exc, 'support_id', ''))
                record['error_frames'] = [{'file': Path(frame.filename).name, 'function': frame.name,
                                           'line': frame.lineno} for frame in traceback.extract_tb(exc.__traceback__)]
        record.update(captures=calls, usage=usage, latency_seconds=time.monotonic() - start, **final_context(calls))
        evidence_checks(repo, record, mapping)
        from .span_evidence import check_squad_spans
        record['squad_span_evidence'] = check_squad_spans(
            {**record, 'source_to_document': {m['source_id']: key for key, m in mapping.items()}},
            documents or {})
        append_jsonl(path, record)
        append_jsonl(cell / 'attempts.jsonl', {'event': 'finished', 'attempt_id': attempt_id,
                      'id': question['id'], 'dataset': question['dataset'], 'mode': mode,
                      'repeat': repeat, 'status': record['status'], 'latency_seconds': record['latency_seconds'], 'at': utc_now()})
        print(question['dataset'], mode, question['split'], question['id'], record['status'],
              round(record['latency_seconds'], 1), 'seconds', flush=True)
