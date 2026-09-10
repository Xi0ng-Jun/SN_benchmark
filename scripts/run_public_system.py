#!/usr/bin/env python3
"""Experimental public-data run using an isolated Silicon Notebook runtime."""
import argparse
import functools
import hashlib
import fcntl
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import tomllib
from datetime import datetime, timezone
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from rag_eval.artifacts import digest, save_json, save_jsonl
from rag_eval.datasets import document_text, iter_jsonl, load_dataset
from rag_eval.metrics import score_ranking
from rag_eval.protocol import capture_context


def read_json(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def save_metadata(path, update):
    # Different stages may finish independent notebooks concurrently.
    with path.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        current = read_json(path, {})
        datasets = current.setdefault('datasets', {})
        for name, state in update.get('datasets', {}).items():
            datasets.setdefault(name, {}).update(state)
        current.update({k: v for k, v in update.items() if k != 'datasets'})
        save_json(path, current)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root', type=Path, default=ROOT.parent / 'project')
    parser.add_argument('--datasets-root', type=Path, default=Path('/home/wabiwabi/rag_benchmark/.ragbench/datasets'))
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--limit', type=int, default=50)
    parser.add_argument('--stage', choices=['prepare', 'ask', 'judge', 'all'], default='all')
    args = parser.parse_args()
    run = args.run_dir.resolve()
    project = args.project_root.resolve()
    datasets = args.datasets_root.resolve()
    if run == project or project in run.parents:
        raise ValueError('Run directory must be outside production project')
    if args.limit < 1:
        raise ValueError('limit must be positive')
    run.mkdir(parents=True, exist_ok=True)
    private = run / 'runtime'
    private.mkdir(exist_ok=True)
    # Override every mutable runtime location before importing project modules.
    os.environ.update(DATABASE_URL='sqlite:///' + str(private / 'database.db'),
                      SILICON_NOTEBOOK_STORAGE_DIR=str(private / 'storage'),
                      LLM_CACHE_PATH=str(private / 'llm-cache.db'),
                      EVENT_LOG_DIR=str(private / 'logs'),
                      LLM_LOG_PATH=str(private / 'logs/llm.jsonl'),
                      MODEL_SERVICES_CONFIG=str(project / '.local/model-services.toml'))
    sys.path.insert(0, str(project / 'backend'))
    os.chdir(run)
    from app.core.config import Settings
    from app.models.notebooks import NotebookCreate
    from app.models.ask import AskRequest
    from app.services.sqlite_repository import SQLiteRepository
    from app.services.repository import UploadedSourceFile
    from app.services.batch_ingest import backfill_chunk_embeddings
    from rag_eval.cases import to_test_case
    from rag_eval.deepeval_runner import build_metrics
    from rag_eval.project_judge import ProjectJudge

    settings = Settings(_env_file=project / '.env')
    assert Path(settings.storage_dir).resolve().is_relative_to(private)
    assert Path(settings.llm_cache_path).resolve().is_relative_to(private)
    assert Path(settings.llm_log_path).resolve().is_relative_to(private)
    assert Path(settings.event_log_dir).resolve().is_relative_to(private)
    repo = SQLiteRepository(settings)
    assert repo.db_path.resolve() == private / 'database.db'
    meta_path = run / 'run.json'
    revision = subprocess.check_output(['git', '-C', str(project), 'rev-parse', 'HEAD'], text=True).strip()
    meta = read_json(meta_path, {'experimental': True, 'started_at': datetime.now(timezone.utc).isoformat(),
                               'project_revision': revision, 'limit': args.limit, 'datasets': {}})
    if meta['project_revision'] != revision or meta['limit'] != args.limit:
        raise ValueError('Resume requires same project revision and sample count')
    config_hash = digest(project / '.local/model-services.toml')
    if meta.get('model_config_sha256', config_hash) != config_hash:
        raise ValueError('Model configuration changed; use a new run directory')
    meta.update(model_config_sha256=config_hash,
                deepeval_version=importlib.metadata.version('deepeval'),
                database=str(repo.db_path), outer_timeout=None,
                corpus_policy='union of positive evidence documents of first N questions',
                retrieval_mode='chunk', deterministic_k=10)
    model_config = tomllib.loads((project / '.local/model-services.toml').read_text())
    meta['models'] = {name: {k: v for k, v in service.items() if k in ('model', 'kind', 'protocol', 'max_concurrency')}
                      for name, service in model_config['services'].items()}
    meta['bindings'] = model_config['bindings']
    meta['thinking'] = model_config.get('thinking', {})
    meta['retrieval_settings'] = {k: getattr(settings, k) for k in (
        'chunk_mmr_k', 'chunk_mmr_lambda', 'chunk_recall', 'chunk_answer_budget_chars',
        'query_rewrite_enabled', 'generated_question_index_mode')}
    invocation = {'started_at': datetime.now(timezone.utc).isoformat(), 'stage': args.stage,
                  'project_revision': revision, 'script_sha256': digest(__file__),
                  'module_sha256': {p.name: digest(p) for p in (ROOT / 'src/rag_eval').glob('*.py')},
                  'model_config_sha256': config_hash, 'retrieval_settings': meta['retrieval_settings'],
                  'log_paths': [settings.llm_log_path, settings.event_log_dir]}
    save_json(run / 'invocations' / (uuid4().hex + '.json'), invocation)
    save_metadata(meta_path, meta)
    try:
        for name in ('multihop-rag', 'scifact'):
            out = run / name
            out.mkdir(exist_ok=True)
            data = datasets / name / 'full'
            questions = load_dataset(data)[:args.limit]
            required = {identity for q in questions for identity in q['gold_document_ids']}
            manifest = read_json(data / 'manifest.json', {})
            file_hashes = {p.name: digest(p) for p in data.glob('*.jsonl')}
            state = meta['datasets'].setdefault(name, {'manifest': manifest, 'file_sha256': file_hashes})
            if state['file_sha256'] != file_hashes:
                raise ValueError('Dataset changed during resume')
            save_jsonl(out / 'questions.jsonl', questions)
            if 'notebook_id' not in state:
                state['notebook_id'] = repo.create_notebook(NotebookCreate(name='Public eval ' + name)).id
                save_metadata(meta_path, meta)
            notebook = state['notebook_id']
            mapping = read_json(out / 'document-map.json', {})
            if args.stage in ('prepare', 'all'):
                for doc in iter_jsonl(data / 'documents.jsonl'):
                    identity = str(doc['id'])
                    if identity not in required or identity in mapping:
                        continue
                    content = document_text(doc).encode('utf-8')
                    filename = 'doc-' + hashlib.sha256(identity.encode()).hexdigest()[:20] + '.md'
                    t = time.monotonic()
                    sources = repo.upload_sources(notebook, [UploadedSourceFile(file_name=filename, content_type='text/markdown', content=content)], scheduler=None)
                    mapping[identity] = {'source_id': sources[0].id, 'filename': filename,
                                         'text_sha256': hashlib.sha256(content).hexdigest(),
                                         'ingest_seconds': time.monotonic()-t}
                    save_json(out / 'document-map.json', mapping)
                    print(name, 'import', len(mapping), '/', len(required), flush=True)
                backfill_chunk_embeddings(repo, notebook, missing_only=True)
                rows = repo._connect().execute('SELECT id, source_id FROM chunks WHERE notebook_id=? ORDER BY id', (notebook,)).fetchall()
                for item in mapping.values():
                    item['chunk_ids'] = [str(row[0]) for row in rows if str(row[1]) == item['source_id']]
                if any(not item['chunk_ids'] for item in mapping.values()):
                    raise ValueError('Imported document has no chunks')
                save_json(out / 'document-map.json', mapping)
                state.update(documents=len(mapping), chunks=len(rows), prepared=True)
                state['embedded_chunks'] = repo._connect().execute(
                    'SELECT count(*) FROM chunk_embeddings WHERE chunk_id IN (SELECT id FROM chunks WHERE notebook_id=?)',
                    (notebook,)).fetchone()[0]
                if state['embedded_chunks'] != len(rows):
                    raise ValueError('Missing chunk embeddings; cannot report vector retrieval as prepared')
                save_metadata(meta_path, meta)
            if args.stage in ('ask', 'all'):
                if not state.get('prepared'):
                    raise ValueError('Prepare must complete before Ask')
                if not state.get('scale_index_built'):
                    repo.build_scale_index(notebook)
                    state['scale_index_built'] = True
                    save_metadata(meta_path, meta)
                records = list(iter_jsonl(out / 'answers.jsonl')) if (out / 'answers.jsonl').exists() else []
                done = {row['id'] for row in records}
                source_to_public = {v['source_id']: k for k, v in mapping.items()}
                service = repo._runtime.ask_component
                original = service._answer_chunks
                captured = []

                @functools.wraps(original)
                def capture(*a, **kw):
                    sink = kw.setdefault('baseline_sink', {})
                    try:
                        return original(*a, **kw)
                    finally:
                        # The project fills this before calling the answer model,
                        # so failed synthesis still has an auditable context.
                        if 'context_block' in sink:
                            captured.append(capture_context(sink))

                service._answer_chunks = capture
                try:
                    for q in questions:
                        if q['id'] in done:
                            continue
                        captured.clear()
                        t = time.monotonic()
                        try:
                            response = repo.ask(notebook, AskRequest(question=q['question'], mode='chunk'))
                        except Exception as exc:
                            save_json(out / 'ask-error.json', {'id': q['id'], 'error_type': type(exc).__name__, 'stage': 'ask'})
                            raise
                        if not captured:
                            raise ValueError('Ask did not expose a synthesis context; inspect response before scoring')
                        context = captured[-1]
                        public_ids = [source_to_public[s] for s in context['source_ids']]
                        record = {**q, **context, 'answer': response.answer,
                                  'response': response.model_dump(mode='json'), 'retrieved_document_ids': public_ids,
                                  'latency_seconds': time.monotonic()-t,
                                  'metrics_at_10': score_ranking(public_ids, q['relevance'], 10)}
                        records.append(record)
                        save_jsonl(out / 'answers.jsonl', records)
                        save_jsonl(out / 'retrieval-results.jsonl', [{k: v for k, v in r.items() if k not in ('answer', 'response')} for r in records])
                        print(name, 'ask', len(records), '/', len(questions), flush=True)
                finally:
                    service._answer_chunks = original
                scores = [r['metrics_at_10'] for r in records if r['metrics_at_10'] is not None]
                save_json(out / 'deterministic-metrics.json', {'questions': len(records), 'with_gold': len(scores),
                          'metrics_at_10': {key: sum(s[key] for s in scores)/len(scores) for key in scores[0]} if scores else {}})
            if args.stage in ('judge', 'all'):
                records = list(iter_jsonl(out / 'answers.jsonl'))
                results = read_json(out / 'deepeval-results.json', [])
                done = {r['id'] for r in results}
                for record in records:
                    if record['id'] in done:
                        continue
                    if not record['answer'].strip():
                        results.append({'id': record['id'], 'metrics': [],
                                        'skipped_no_actual_answer': True,
                                        'model_errors': record['response']['model_errors']})
                        save_json(out / 'deepeval-results.json', results)
                        continue
                    metrics = build_metrics(model=ProjectJudge(repo))
                    skipped = []
                    if not record.get('expected_answer'):
                        skipped = ['Contextual Recall', 'Contextual Precision']
                        metrics = metrics[2:]
                    case = to_test_case(record)
                    evaluated = []
                    for metric in metrics:
                        t = time.monotonic()
                        try:
                            metric.measure(case)
                            evaluated.append({'metric': metric.__name__, 'score': metric.score, 'reason': metric.reason,
                                              'seconds': time.monotonic()-t, 'error': metric.error})
                        except Exception as exc:
                            evaluated.append({'metric': metric.__name__, 'score': None, 'error': type(exc).__name__,
                                              'seconds': time.monotonic()-t})
                        print(name, record['id'], metric.__name__, round(time.monotonic()-t, 1), 'seconds', flush=True)
                    results.append({'id': record['id'], 'metrics': evaluated, 'skipped_missing_reference': skipped})
                    save_json(out / 'deepeval-results.json', results)
                    print(name, 'judge', len(results), '/', len(records), flush=True)
    finally:
        repo.close()


if __name__ == '__main__':
    main()
