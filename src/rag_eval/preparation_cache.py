"""Content-addressed reuse of prepared SN notebooks.

A cache entry is the SN database, storage and preparation state saved right
after a run finished importing its partition and before any question was asked.
Its key binds everything that determines that state: the partition corpus, the
preparation policy and document capacity, the SN source files on the
preparation path, the preparation-relevant effective Settings, the model
service configuration and the evaluator's preparation code.  Answer-path code
and settings are deliberately outside the key, so a candidate that changes only
how questions are answered reuses the notebook, and one that changes
preparation code misses and imports again.

Entries are read-only after publication and every byte is verified while it is
copied into a run.  Each run still owns a private copy of the database and
storage; the cache never serves a live runtime.  A hit is disclosed in the run
manifest (``prepared_from``) and never enters the comparison identity.
"""
from __future__ import annotations

import ast
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import time
import uuid

from .identity import fingerprint

FORMAT = 'sn-preparation-cache-v1'
# Bump when the declared lists below change, so older entries stop matching.
KEY_VERSION = 'sn-preparation-key-v1'

# SN source on the preparation path, relative to the captured source root.
# A trailing slash names a directory.  Shared modules list only the
# definitions preparation uses (``path::name``); a file change outside them
# does not invalidate prepared notebooks.  Over-inclusion only costs a miss.
PREPARATION_SOURCES = (
    'backend/app/services/batch_ingest.py',
    'backend/app/services/source_ingestion.py',
    'backend/app/services/source_chunking.py',
    'backend/app/services/chunking.py',
    'backend/app/services/source_embedding.py',
    'backend/app/services/embedding.py',
    'backend/app/services/embedding_dashscope.py',
    'backend/app/services/cached_embedder.py',
    'backend/app/services/notebook_metadata.py',
    'backend/app/services/paper_meta.py',
    'backend/app/services/structural_markdown.py',
    'backend/app/services/parsers.py',
    'backend/app/services/parser_registry.py',
    'backend/app/services/parser_chain_execution.py',
    'backend/app/services/indexing_pipeline.py',
    'backend/app/services/source_element_enrichment.py',
    'backend/app/services/source_element_selection.py',
    'backend/app/services/extraction_profiles.py',
    'backend/app/services/offline_batch.py',
    'backend/app/services/image_backfill.py',
    'backend/app/services/image_backfill_phase.py',
    'backend/app/services/source_image_persist.py',
    'backend/app/services/source_format_admission.py',
    'backend/app/services/mineru_client.py',
    'backend/app/services/mineru_cloud_client.py',
    'backend/app/services/mineru_http_retry.py',
    'backend/app/services/vector_index.py',
    'backend/app/services/scale_index_builder.py',
    'backend/app/services/knowledge_lifecycle.py',
    'backend/app/services/model_work.py',
    'backend/app/services/generated_question_contribution.py',
    'backend/app/services/kg_ingest.py',
    'backend/app/services/prompts.py',
    'backend/app/services/repository.py',
    'backend/app/services/repository_runtime.py',
    'backend/app/services/repository_facade.py',
    'backend/app/services/sqlite_repository.py',
    'backend/app/services/kg/',
    'backend/app/services/knowhow/',
    'backend/app/repositories/',
    'backend/app/models/',
    'backend/app/migration/',
    'backend/app/domain/structural_markdown.py',
    'backend/app/domain/indexing_pipeline.py',
    'backend/app/domain/element_enrichment.py',
    'backend/app/domain/extensions.py',
    'backend/app/core/llm.py',
    'backend/app/core/model_values.py',
    'backend/app/core/cache/',
    'backend/app/services/retrieval.py::_payload_text',
    'backend/app/services/retrieval.py::_PAYLOAD_SKIP_KEYS',
    'backend/app/services/retrieval.py::relation_embed_text',
    'backend/app/services/retrieval.py::RELATION_NAME_EMBED_CHARS',
)

# Effective Settings that shape the prepared notebook.  Families are listed by
# prefix; answer-path settings (retrieval, reasoning, answer budgets) are not.
PREPARATION_SETTINGS = (
    'chunk_target_chars', 'chunk_overlap_chars',
    'embed_dim', 'embed_runtime_dim', 'embed_truncate_chars', 'embed_batch_size', 'embed_persist_chunk',
    'paper_meta_enabled', 'paper_meta_head_chars',
    'kg_auto_extract', 'generated_question_index_mode', 'generated_question_questions_per_chunk',
    'user_upload_document_limit', 'source_upload_max_mb', 'openai_compat_max_tokens',
    'model_bindings_strict', 'hnsw_ef_construction',
)
PREPARATION_SETTING_PREFIXES = (
    'notebook_metadata_', 'indexing_pipeline_', 'source_element_enricher_', 'mineru_', 'kg_window_',
)

_DESTINATIONS = (('database.db', 'runtime/database.db'), ('storage/', 'runtime/storage/'),
                 ('cell/', 'product-artifacts/'))
_CELL_FILES = ('state.json', 'document-map.json')


class CacheRejected(ValueError):
    """A cache entry failed verification; the run prepares from scratch."""


def _symbol_source(path, names):
    tree = ast.parse(Path(path).read_text(encoding='utf-8'))
    found = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            targets = [node.name]
        elif isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target.id]
        else:
            continue
        for name in targets:
            if name in names:
                found[name] = ast.unparse(node)
    return {name: found.get(name) for name in sorted(names)}


def preparation_source_identity(code, source_root=None):
    """Hashes of the declared preparation sources in an actual loaded manifest.

    Without a captured file manifest the whole source identity is used, so any
    change misses.  A shared module whose bytes are not available is hashed as
    a whole file, which is again only stricter.
    """
    manifest = (code or {}).get('actual_manifest')
    if not isinstance(manifest, dict):
        return dict(whole_source=fingerprint({k: (code or {}).get(k) for k in sorted(code or {})
                                              if k not in ('versions', 'benchmark_source_hashes')}))
    files, symbols = [], {}
    for entry in PREPARATION_SOURCES:
        path, _, name = entry.partition('::')
        if name:
            symbols.setdefault(path, set()).add(name)
        else:
            files.append(path)
    selected = {}
    for path, item in manifest.items():
        if any(path == f or (f.endswith('/') and path.startswith(f)) for f in files):
            selected[path] = item['sha256']
    for path, names in symbols.items():
        if path not in manifest:
            continue
        local = Path(source_root) / PurePosixPath(path) if source_root is not None else None
        if local is not None and local.is_file() and not local.is_symlink() \
                and hashlib.sha256(local.read_bytes()).hexdigest() == manifest[path]['sha256']:
            selected[path + '::' + ','.join(sorted(names))] = fingerprint(_symbol_source(local, names))
        else:
            selected[path] = manifest[path]['sha256']
    return dict(files=selected)


def preparation_settings(settings, run):
    values = {}
    for name in sorted(getattr(type(settings), 'model_fields', None) or vars(settings)):
        if name in PREPARATION_SETTINGS or name.startswith(PREPARATION_SETTING_PREFIXES):
            values[name] = getattr(settings, name, None)
    for name in PREPARATION_SETTINGS:
        values.setdefault(name, getattr(settings, name, None))
    # Run-local paths differ per run; secrets never leave the hash below.
    return json.loads(json.dumps(values, sort_keys=True, default=str).replace(str(run), '<RUN>'))


def preparation_key(*, partition_id, product_bundle, document_limit, preparation_policy, code,
                    source_root, settings, run, service_config_sha256, requirements_sha256,
                    evaluator_preparation_sha256):
    """Return (key, material); material holds only hashes and public coordinates."""
    material = dict(
        format=KEY_VERSION,
        partition=dict(partition_id=partition_id, product_bundle_sha256=fingerprint(product_bundle),
                       document_limit=document_limit),
        preparation_policy=preparation_policy,
        sn_preparation_source_sha256=fingerprint(preparation_source_identity(code, source_root)),
        preparation_settings_sha256=fingerprint(preparation_settings(settings, run)),
        model_services_sha256=service_config_sha256,
        dependency_requirements_sha256=requirements_sha256,
        evaluator_preparation_sha256=evaluator_preparation_sha256,
    )
    return fingerprint(material), material


def _file_digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def _copy_verified(source, target, expected):
    h, size = hashlib.sha256(), 0
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(source, 'rb') as reader, open(target, 'xb') as writer:
        for block in iter(lambda: reader.read(1 << 20), b''):
            h.update(block)
            size += len(block)
            writer.write(block)
    if h.hexdigest() != expected['sha256'] or size != expected['size']:
        raise CacheRejected('Cached file bytes differ from the entry manifest: ' + str(source.name))
    return size


def rewrite_storage_paths(database, old_prefix, new_prefix, forbidden, *, what='source run'):
    """Point ``sources.file_path`` at the run's storage; refuse other references.

    Only text values are searched: embeddings and other blobs cannot hold a
    readable path, and casting them to text dominated the cost of this check.
    """
    rewritten = 0
    db = sqlite3.connect(database)
    try:
        with db:
            columns = {row[1] for row in db.execute("PRAGMA table_info('sources')")}
            if 'file_path' in columns:
                for row_id, path in db.execute('SELECT rowid, file_path FROM sources').fetchall():
                    if isinstance(path, str) and path.startswith(old_prefix):
                        db.execute('UPDATE sources SET file_path=? WHERE rowid=?',
                                   (new_prefix + path[len(old_prefix):], row_id))
                        rewritten += 1
        for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            for _, column, declared, *_ in db.execute(f'PRAGMA table_info("{table}")').fetchall():
                kind = (declared or '').upper()
                if 'INT' in kind or 'BLOB' in kind or any(t in kind for t in ('REAL', 'FLOA', 'DOUB')):
                    continue
                hit = db.execute(f'SELECT 1 FROM "{table}" WHERE typeof("{column}")=\'text\' '
                                 f'AND instr("{column}", ?) LIMIT 1', (forbidden,)).fetchone()
                if hit:
                    raise ValueError(f'Database copy references the {what} outside source file paths')
    finally:
        db.close()
    return rewritten


class KeyLock:
    """Exclusive per-key claim; the OS releases it if the process dies."""

    def __init__(self, path):
        self._fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        started = time.monotonic()
        fcntl.flock(self._fd, fcntl.LOCK_EX)
        self.waited_seconds = round(time.monotonic() - started, 3)

    def release(self):
        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None


class PreparationCache:
    def __init__(self, root):
        self.root = Path(root).resolve()

    def lock(self, key):
        """Serialize runs that would prepare the same notebook.

        Concurrent attempts of one candidate share a key; the first prepares
        and publishes while the others wait, then copy instead of importing.
        """
        locks = self.root / '.locks'
        locks.mkdir(parents=True, exist_ok=True, mode=0o700)
        return KeyLock(locks / (self._entry(key).name + '.lock'))

    def _entry(self, key):
        if len(key) != 64 or any(c not in '0123456789abcdef' for c in key):
            raise ValueError('Invalid preparation cache key')
        return self.root / key

    def install(self, key, material, run):
        """Copy a verified entry into ``run``; return its receipt or None on a miss.

        The run's runtime database, storage and product cell must not exist
        yet. A rejected entry leaves nothing behind and raises CacheRejected.
        """
        entry = self._entry(key)
        if not entry.is_dir():
            return None
        runtime, cell = run / 'runtime', run / 'product-artifacts'
        if (runtime / 'database.db').exists() or (runtime / 'storage').exists() or cell.exists():
            raise ValueError('Cached preparation needs an empty runtime')
        try:
            if entry.is_symlink() or (entry / 'entry.json').is_symlink():
                raise CacheRejected('Cache entry must not be a symlink')
            raw = (entry / 'entry.json').read_bytes()
            record = json.loads(raw)
            if (record.get('format') != FORMAT or record.get('key') != key
                    or record.get('material') != material or fingerprint(record['material']) != key):
                raise CacheRejected('Cache entry does not match its key')
            files = record['files']
            if 'database.db' not in files or not any(name.startswith('cell/') for name in files):
                raise CacheRejected('Cache entry is incomplete')
            total = 0
            (runtime / 'storage').mkdir(parents=True)
            for name, expected in sorted(files.items()):
                relative = PurePosixPath(name)
                if relative.is_absolute() or '..' in relative.parts:
                    raise CacheRejected('Cache entry names a path outside itself')
                prefix, target = next(((p, t) for p, t in _DESTINATIONS if name == p or name.startswith(p)),
                                      (None, None))
                if prefix is None:
                    raise CacheRejected('Cache entry names an unknown file')
                source = entry / relative
                if source.is_symlink():
                    raise CacheRejected('Cache entry contains a symlink')
                total += _copy_verified(source, run / (target + name[len(prefix):] if prefix.endswith('/') else target),
                                        expected)
            rewritten = rewrite_storage_paths(runtime / 'database.db', record['storage_prefix'],
                                              str(runtime / 'storage') + '/', record['source_run_dir'])
        except (CacheRejected, OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
            for path in (runtime / 'database.db', runtime / 'storage', cell):
                if path.is_dir() and not path.is_symlink():
                    shutil.rmtree(path)
                elif path.exists() or path.is_symlink():
                    path.unlink()
            if isinstance(exc, CacheRejected):
                raise
            raise CacheRejected(type(exc).__name__ + ': ' + str(exc)[:200]) from None
        return dict(format=FORMAT, status='hit', cache_key=key, source_run=record['source_run_id'],
                    entry_sha256=hashlib.sha256(raw).hexdigest(), files=len(files), verified_bytes=total,
                    rewritten_source_paths=rewritten)

    def store(self, key, material, run):
        """Publish ``run``'s freshly prepared notebook under ``key`` (read-only).

        Call after preparation and before asking. An existing entry wins.
        """
        entry = self._entry(key)
        if entry.exists():
            return dict(status='already_cached', cache_key=key)
        runtime, cell = run / 'runtime', run / 'product-artifacts'
        storage = runtime / 'storage'
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        incoming = self.root / ('.incoming-' + uuid.uuid4().hex)
        incoming.mkdir(mode=0o700)
        try:
            source = sqlite3.connect((runtime / 'database.db').as_uri() + '?mode=ro', uri=True)
            try:
                target = sqlite3.connect(incoming / 'database.db')
                try:
                    source.backup(target)
                    # A read-only entry cannot host -wal/-shm files.
                    target.execute('PRAGMA journal_mode=DELETE')
                finally:
                    target.close()
            finally:
                source.close()
            if storage.is_dir():
                for path in storage.rglob('*'):
                    if path.is_symlink():
                        raise ValueError('Prepared storage contains a symlink')
                shutil.copytree(storage, incoming / 'storage')
            else:
                (incoming / 'storage').mkdir()
            (incoming / 'cell').mkdir()
            for name in _CELL_FILES:
                shutil.copyfile(cell / name, incoming / 'cell' / name)
            files = {}
            for path in sorted(incoming.rglob('*')):
                if path.is_file():
                    files[path.relative_to(incoming).as_posix()] = dict(sha256=_file_digest(path),
                                                                        size=path.stat().st_size)
            record = dict(format=FORMAT, key=key, material=material, source_run_id=run.name,
                          source_run_dir=str(run), storage_prefix=str(storage) + '/',
                          created_at=datetime.now(timezone.utc).isoformat(), files=files)
            (incoming / 'entry.json').write_text(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + '\n',
                                                 encoding='utf-8')
            for path in sorted(incoming.rglob('*'), key=lambda p: len(p.parts), reverse=True):
                path.chmod(0o555 if path.is_dir() else 0o444)
            incoming.chmod(0o555)
            try:
                os.rename(incoming, entry)
            except OSError:
                if entry.is_dir():
                    _remove(incoming)
                    return dict(status='already_cached', cache_key=key)
                raise
        except BaseException:
            _remove(incoming)
            raise
        return dict(status='stored', cache_key=key, files=len(files),
                    entry_sha256=_file_digest(entry / 'entry.json'))


def _remove(path):
    if not path.exists():
        return
    for item in [path, *path.rglob('*')]:
        if item.is_dir() and not item.is_symlink():
            item.chmod(0o700)
    shutil.rmtree(path)
