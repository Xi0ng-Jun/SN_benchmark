"""Read-only adapter for the checked-out Silicon Notebook retrieval runtime."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _bootstrap(root: Path):
    backend = root / 'backend'
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))
    os.chdir(root)
    from app.core.config import Settings
    from app.services.sqlite_repository import SQLiteRepository
    return Settings, SQLiteRepository


def open_repository(project_root: str | Path):
    Settings, SQLiteRepository = _bootstrap(Path(project_root).resolve())
    return SQLiteRepository(Settings())


def load_chunks(repo, notebook_id: str, limit: int = 100):
    """Load source chunks through the repository's read port for gold generation."""
    connection = repo._connect()
    try:
        rows = connection.execute(
            'SELECT id, source_id, text, section_path FROM chunks WHERE notebook_id = ? ORDER BY id LIMIT ?',
            (notebook_id, limit),
        ).fetchall()
    finally:
        repo.close_local()
    return [dict(id=str(r[0]), source_id=str(r[1]), text=str(r[2]),
                 section_path=str(r[3] or ''), notebook_id=notebook_id) for r in rows]


def retrieve(repo, notebook_id: str, question: str, top_k: int = 10):
    if not repo.configured('retrieval_query_embedding'):
        raise RuntimeError('retrieval_query_embedding is not configured')
    hits, _, _ = repo.retrieval.retrieve_chunk_candidates(notebook_id, question)
    records = []
    for hit in list(hits)[:top_k]:
        records.append({
            'id': str(getattr(hit, 'chunk_id', '')),
            'source_id': str(getattr(hit, 'source_id', '')),
            'text': str(getattr(hit, 'text', '')),
            'score': float(getattr(hit, 'relevance', 0.0) or 0.0),
        })
    return records


def answer(repo, question: str, contexts: list[str]):
    client = repo.chat('ask_answer')
    raw = client.chat_json([{'role': 'user', 'content': (
        '你是 Silicon Notebook 的评测回答器。只能依据“证据”回答问题；证据不足时明确说无法确定。'
        '每个事实后用 [kN] 标记引用。只返回 JSON：{"answer":"...","grounded":true或false}。\n'
        f'问题：{question}\n证据：\n' + '\n'.join(f'[k{i + 1}] {text}' for i, text in enumerate(contexts))
    )}], {'answer': 'string', 'grounded': 'boolean'})
    data = json.loads(raw)
    return str(data.get('answer') or ''), bool(data.get('grounded'))
