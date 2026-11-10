"""Bounded product source reads from immutable Git objects, not a filesystem sandbox.

The allowlist describes the SN product layout. It deliberately excludes config,
fixtures/data and evaluator paths; it cannot detect a secret embedded in source.
"""
from __future__ import annotations

import json
from pathlib import PurePosixPath
import re

from .task_worktree import _git

MAX_BLOB_BYTES = 1024 * 1024
MAX_OUTPUT_BYTES = 32768
MAX_LINES = 200
_ROOTS = ('backend/app/', 'backend/tests/', 'frontend/src/', 'frontend/tests/', 'frontend/__tests__/')
_SUFFIXES = {'.py', '.ts', '.tsx', '.js', '.jsx', '.mjs', '.cjs', '.vue', '.svelte', '.css', '.scss', '.html'}
_PRIVATE = re.compile(r'(?:^|[_.-])(data|datasets?|fixtures?|secrets?|credentials?|tokens?|passwords?|'
                      r'evaluators?|benchmarks?|gold|results?|artifacts?|logs?)(?:$|[_.-])', re.I)


def _source_path(path):
    if (not isinstance(path, str) or not path or len(path) > 512 or '\\' in path
            or any(ord(c) < 32 or ord(c) == 127 for c in path)):
        raise ValueError('source needs a canonical relative product source path')
    parts = path.split('/')
    if any(not p or p in ('.', '..') or p.startswith('.') or _PRIVATE.search(p) for p in parts):
        raise ValueError('source path is private, data, evaluator or noncanonical')
    if not path.startswith(_ROOTS) or PurePosixPath(path).suffix not in _SUFFIXES:
        raise ValueError('source allows product source/tests only')
    return path


def read_frozen_source(root, commit, *, path, query=None, start=1, lines=80):
    """Read one regular text blob; query is literal and line numbers are 1-based.

    In query mode, start is the first source line to search, lines limits matches.
    A dirty checkout (including dirty symlinks) never participates in this read.
    """
    path = _source_path(path)
    if not isinstance(commit, str) or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', commit):
        raise ValueError('source requires an exact frozen commit')
    if type(start) is not int or start < 1 or type(lines) is not int or not 1 <= lines <= MAX_LINES:
        raise ValueError(f'source start must be positive and lines must be 1..{MAX_LINES}')
    if query is not None and (not isinstance(query, str) or not query or len(query) > 256
                              or any(ord(c) < 32 for c in query)):
        raise ValueError('source query must be a nonempty bounded literal')
    resolved = _git(root, '--no-replace-objects', 'rev-parse', '--verify', commit + '^{commit}', limit=128)[0].decode().strip()
    if resolved != commit:
        raise ValueError('source requires an exact frozen commit object')
    # Literal pathspecs are enforced by _git. Tree mode rejects committed symlinks
    # and submodules without following either into another content boundary.
    entry = _git(root, '--no-replace-objects', 'ls-tree', '-z', commit, '--', path, limit=2048)[0].split(b'\0')
    if len(entry) != 2 or not entry[0]:
        raise ValueError('source path is absent from the frozen commit')
    metadata, actual = entry[0].split(b'\t', 1)
    mode, kind, oid = metadata.split()
    if mode not in (b'100644', b'100755') or kind != b'blob' or actual.decode('utf-8') != path:
        raise ValueError('source requires a regular product source file, not a symlink')
    object_id = oid.decode('ascii')
    size = int(_git(root, '--no-replace-objects', 'cat-file', '-s', object_id, limit=32)[0])
    if size > MAX_BLOB_BYTES:
        raise ValueError('source blob exceeds inspection limit')
    blob = _git(root, '--no-replace-objects', 'cat-file', 'blob', object_id, limit=MAX_BLOB_BYTES)[0]
    try:
        source = blob.decode('utf-8')
    except UnicodeDecodeError:
        raise ValueError('source requires UTF-8 text') from None
    if '\0' in source:
        raise ValueError('source does not read binary blobs')
    text_lines = source.splitlines()
    selected = [(n, text) for n, text in enumerate(text_lines, 1) if n >= start
                and (query is None or query in text)]
    out = dict(commit=commit, path=path, query=query, start=start, total_lines=len(text_lines),
               lines=[], truncated=len(selected) > lines)
    for number, text in selected[:lines]:
        # Keep both long individual lines and total JSON small, disclosing clipping.
        clipped = text[:2000]
        out['truncated'] |= clipped != text
        out['lines'].append(dict(number=number, text=clipped))
        if len(json.dumps(out, ensure_ascii=False).encode('utf-8')) >= MAX_OUTPUT_BYTES - 1024:
            out['lines'].pop()
            out['truncated'] = True
            break
    return out


def read_frozen_blob(root, commit, path):
    """The whole UTF-8 text of one product source blob at *commit*, under the same checks as the source command."""
    path = _source_path(path)
    if not isinstance(commit, str) or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', commit):
        raise ValueError('source requires an exact frozen commit')
    entry = _git(root, '--no-replace-objects', 'ls-tree', '-z', commit, '--', path, limit=2048)[0].split(b'\0')
    if len(entry) != 2 or not entry[0]:
        raise ValueError(f'{path} is absent from the frozen parent')
    metadata, actual = entry[0].split(b'\t', 1)
    mode, kind, oid = metadata.split()
    if mode not in (b'100644', b'100755') or kind != b'blob' or actual.decode('utf-8') != path:
        raise ValueError('source requires a regular product source file, not a symlink')
    object_id = oid.decode('ascii')
    if int(_git(root, '--no-replace-objects', 'cat-file', '-s', object_id, limit=32)[0]) > MAX_BLOB_BYTES:
        raise ValueError('source blob exceeds inspection limit')
    blob = _git(root, '--no-replace-objects', 'cat-file', 'blob', object_id, limit=MAX_BLOB_BYTES)[0]
    try:
        text = blob.decode('utf-8')
    except UnicodeDecodeError:
        raise ValueError('source requires UTF-8 text') from None
    if '\0' in text:
        raise ValueError('source does not read binary blobs')
    return text


def candidate_blob(service, task_id, node, path):
    """read_frozen_blob for the node's frozen commit, checking the node belongs to *task_id*."""
    candidate = service.candidate(node)
    if service.iteration_record(candidate['iteration_id'])['task_id'] != task_id:
        raise ValueError('source node belongs to another Task')
    root = getattr(service.trees, 'project_root', None)
    if root is None:
        raise ValueError('source requires configured product repository')
    return read_frozen_blob(root, candidate.get('frozen_commit'), path)


def candidate_source(service, task_id, node, **kwargs):
    if not node:
        raise ValueError('source needs the analyst --node binding')
    candidate = service.candidate(node)
    if service.iteration_record(candidate['iteration_id'])['task_id'] != task_id:
        raise ValueError('source node belongs to another Task')
    root = getattr(service.trees, 'project_root', None)
    if root is None:
        raise ValueError('source requires configured product repository')
    result = read_frozen_source(root, candidate.get('frozen_commit'), **kwargs)
    return dict(result, candidate_id=candidate['id'])
