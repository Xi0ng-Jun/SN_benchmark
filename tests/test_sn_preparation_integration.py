"""Optional real-SN probe; model clients are fake, storage and process isolated."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


def test_real_sn_preparation_keeps_source_work_and_reduces_metadata_calls(tmp_path):
    backend = Path(os.environ.get('SN_PREPARATION_BACKEND', '/home/wabiwabi/silicon-notebook/project/backend'))
    if not (backend / 'tests/model_testkit.py').is_file():
        pytest.skip('Set SN_PREPARATION_BACKEND to a compatible SN backend checkout')
    # Do not inherit credentials, dotenv selection or production paths. Import
    # real SN in a child so app.* cannot contaminate captured-source tests.
    env = {'PATH': os.environ.get('PATH', ''), 'PYTHONPATH': str(Path(__file__).resolve().parents[1] / 'src'),
           'SILICON_NOTEBOOK_ENV_FILE': '', 'EXTENSIONS_CONFIG': '', 'MPLCONFIGDIR': str(tmp_path / 'mpl')}
    child = subprocess.run([sys.executable, str(Path(__file__).resolve()), str(backend), str(tmp_path)],
                           cwd=tmp_path, env=env, text=True, capture_output=True, timeout=120)
    assert child.returncode == 0, child.stdout + child.stderr
    result = json.loads(child.stdout.splitlines()[-1])
    assert result['per-source-metadata-v1'] == {'source_summary': 100, 'paper_metadata': 100,
                                               'notebook_metadata': 448,
                                               'source_element_embedding': 100, 'chunk_embedding': 100}
    assert result['deferred-notebook-metadata-v1'] == {'source_summary': 100, 'paper_metadata': 100,
                                                     'notebook_metadata': 8,
                                                     'source_element_embedding': 100, 'chunk_embedding': 100}


def _probe(backend, directory):
    sys.path.insert(0, str(backend))
    from collections import Counter
    from app.core.config import Settings
    from app.services.embedding import FakeEmbedder
    from app.services.notebook_metadata import notebook_metadata_refresh_suppressed
    from app.services.sqlite_repository import SQLiteRepository
    from tests.model_testkit import RecordingModelProvider
    from rag_eval.benchmark_runtime import prepare_notebook

    documents = [dict(id=f'doc-{i:03d}', title=f'Title-{i:03d}',
                      text=f'# Title-{i:03d}\n\nIndependent source fact {i:03d}.') for i in range(100)]
    results = {}
    for policy in ('per-source-metadata-v1', 'deferred-notebook-metadata-v1'):
        root = directory / policy
        root.mkdir()
        counts = Counter()
        metadata_prompts = []

        class Chat:
            configured = True
            model = 'offline-fixture'

            def __init__(self, workload):
                self.workload = workload

            def chat_json(self, messages, schema, **kwargs):
                counts[self.workload] += 1
                if self.workload == 'source_summary':
                    return json.dumps({'summary': 'Fixture summary.'})
                if self.workload == 'paper_metadata':
                    return json.dumps({'is_paper': False})
                metadata_prompts.append(messages[0]['content'])
                return json.dumps({'name': 'Collection', 'description': 'Complete fixture metadata.'})

        class Embedder(FakeEmbedder):
            configured = True

            def __init__(self, workload):
                super().__init__(dim=16)
                self.workload = workload

            def embed_texts(self, texts):
                counts[self.workload] += 1
                return super().embed_texts(texts)

        provider = RecordingModelProvider(
            chat_clients={workload: Chat(workload) for workload in
                          ('source_summary', 'paper_metadata', 'notebook_metadata')},
            embedding_clients={workload: Embedder(workload) for workload in
                               ('source_element_embedding', 'chunk_embedding', 'retrieval_query_embedding')})
        settings = Settings(_env_file=None, database_url='sqlite:///' + str(root / 'database.db'),
                            storage_dir=str(root / 'storage'), model_services_config='', embed_dim=16,
                            event_log_enabled=False, llm_log_enabled=False, llm_cache_enabled=False,
                            llm_cache_path=str(root / 'cache.db'), event_log_dir=str(root / 'logs'),
                            llm_log_path=str(root / 'logs/llm.jsonl'), auth_optional=True,
                            user_upload_document_limit=100, kg_auto_extract=False,
                            chunk_kg_overlay_enabled=False, generated_question_index_mode='off',
                            agent_profile_enabled=False, user_search_profile_enabled=False,
                            retrieval_experience_enabled=False, retrieval_experience_inject_enabled=False,
                            reasoning_consult_memory_enabled=False)
        repo = SQLiteRepository(settings, model_provider=provider)
        try:
            notebook, mapping = prepare_notebook(repo, root, documents, 'alce', preparation_policy=policy)
            assert notebook_metadata_refresh_suppressed() is False
            assert len(mapping) == 100
            assert all(item['chunk_ids'] for item in mapping.values())
            state = json.loads((root / 'state.json').read_text())
            assert state['prepared'] and state['documents'] == 100
            assert state['chunks'] == state['embedded_chunks'] == 100
            db = repo._connect()
            assert db.execute('SELECT count(*) FROM sources WHERE notebook_id=? AND summary=?',
                              (notebook, 'Fixture summary.')).fetchone()[0] == 100
            assert db.execute('SELECT count(*) FROM source_paper_meta WHERE notebook_id=?',
                              (notebook,)).fetchone()[0] == 100
            assert db.execute('SELECT purpose FROM notebooks WHERE id=?',
                              (notebook,)).fetchone()[0] == 'Complete fixture metadata.'
            assert all(document['title'] in '\n'.join(metadata_prompts) for document in documents)
            results[policy] = dict(counts)
        finally:
            repo.close()
    print(json.dumps(results))


if __name__ == '__main__':
    _probe(Path(sys.argv[1]), Path(sys.argv[2]))
