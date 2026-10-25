"""Optional real-SN probe; model clients are fake, storage and process isolated."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

pytestmark = pytest.mark.integration


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




@pytest.mark.parametrize('via', ['seed', 'cache'])
def test_real_sn_seed_copy_is_self_contained_and_searchable(tmp_path, via):
    backend = Path(os.environ.get('SN_PREPARATION_BACKEND', '/home/wabiwabi/silicon-notebook/project/backend'))
    if not (backend / 'tests/model_testkit.py').is_file():
        pytest.skip('Set SN_PREPARATION_BACKEND to a compatible SN backend checkout')
    env = {'PATH': os.environ.get('PATH', ''), 'PYTHONPATH': str(Path(__file__).resolve().parents[1] / 'src'),
           'SILICON_NOTEBOOK_ENV_FILE': '', 'EXTENSIONS_CONFIG': '', 'MPLCONFIGDIR': str(tmp_path / 'mpl')}
    child = subprocess.run([sys.executable, str(Path(__file__).resolve()), str(backend), str(tmp_path), via],
                           cwd=tmp_path, env=env, text=True, capture_output=True, timeout=120)
    assert child.returncode == 0, child.stdout + child.stderr
    result = json.loads(child.stdout.splitlines()[-1])
    assert result['rewritten_source_paths'] == result['sources'] == 5
    assert result['paths_inside_copy'] and result['files_readable']
    assert result['chunks'] == result['embedded_chunks'] == 5
    assert result['search_hit_source_in_copy'] and result['source_detail_readable']


def _seed_probe(backend, directory, via='seed'):
    """Prepare with real SN, copy as a seed or through the preparation cache,
    then reopen and search the copy only."""
    sys.path.insert(0, str(backend))
    import shutil
    from app.core.config import Settings
    from app.services.embedding import FakeEmbedder
    from app.services.sqlite_repository import SQLiteRepository
    from tests.model_testkit import RecordingModelProvider
    from rag_eval.benchmark_runtime import load_prepared_notebook, prepare_notebook
    from rag_eval.notebook_runner import install_prepared_seed

    documents = [dict(id=f'doc-{i}', title=f'Title-{i}', text=f'Independent source fact number {i} about herons.')
                 for i in range(5)]

    class Chat:
        configured = True
        model = 'offline-fixture'

        def chat_json(self, messages, schema, **kwargs):
            return json.dumps({'summary': 'Fixture summary.', 'is_paper': False,
                               'name': 'Collection', 'description': 'Fixture metadata.'})

    class Embedder(FakeEmbedder):
        configured = True

        def __init__(self):
            super().__init__(dim=16)

    def repository(run):
        runtime = run / 'runtime'
        settings = Settings(_env_file=None, database_url='sqlite:///' + str(runtime / 'database.db'),
                            storage_dir=str(runtime / 'storage'), model_services_config='', embed_dim=16,
                            event_log_enabled=False, llm_log_enabled=False, llm_cache_enabled=False,
                            llm_cache_path=str(runtime / 'cache.db'), event_log_dir=str(runtime / 'logs'),
                            llm_log_path=str(runtime / 'logs/llm.jsonl'), auth_optional=True,
                            user_upload_document_limit=100, kg_auto_extract=False,
                            chunk_kg_overlay_enabled=False, generated_question_index_mode='off',
                            agent_profile_enabled=False, user_search_profile_enabled=False,
                            retrieval_experience_enabled=False, retrieval_experience_inject_enabled=False,
                            reasoning_consult_memory_enabled=False)
        provider = RecordingModelProvider(
            chat_clients={w: Chat() for w in ('source_summary', 'paper_metadata', 'notebook_metadata')},
            embedding_clients={w: Embedder() for w in
                               ('source_element_embedding', 'chunk_embedding', 'retrieval_query_embedding')})
        return SQLiteRepository(settings, model_provider=provider)

    seed, copy = directory / 'seed', directory / 'copy'
    (seed / 'runtime').mkdir(parents=True)
    (seed / 'product-artifacts').mkdir()
    repo = repository(seed)
    try:
        prepare_notebook(repo, seed / 'product-artifacts', documents, 'alce')
        if via == 'cache':
            # Published while SN still holds its connection, as the runner does.
            from rag_eval.identity import fingerprint
            from rag_eval.preparation_cache import PreparationCache
            cache, material = PreparationCache(directory / 'cache'), {'format': 'probe'}
            assert cache.store(fingerprint(material), material, seed.resolve())['status'] == 'stored'
    finally:
        repo.close()
    (seed / 'manifest.json').write_text('{}')
    (copy / 'runtime').mkdir(parents=True)
    if via == 'cache':
        receipt = cache.install(fingerprint(material), material, copy.resolve())
    else:
        receipt = install_prepared_seed(seed.resolve(), copy.resolve())
    # The copy must not depend on the seed directory at all.
    shutil.rmtree(seed)
    notebook, mapping = load_prepared_notebook(copy / 'product-artifacts', documents)
    repo = repository(copy)
    try:
        db = repo._connect()
        paths = [row[0] for row in db.execute('SELECT file_path FROM sources WHERE notebook_id=?', (notebook,))]
        chunks = db.execute('SELECT count(*) FROM chunks WHERE notebook_id=?', (notebook,)).fetchone()[0]
        embedded = db.execute('SELECT count(*) FROM chunk_embeddings WHERE chunk_id IN '
                              '(SELECT id FROM chunks WHERE notebook_id=?)', (notebook,)).fetchone()[0]
        found = [hit for hit in repo.search_notebook(notebook, 'herons').hits if hit.source_id]
        detail = repo.get_source(mapping['doc-3']['source_id'])
        source_ids = {item['source_id'] for item in mapping.values()}
        print(json.dumps(dict(rewritten_source_paths=receipt['rewritten_source_paths'], sources=len(paths),
                              paths_inside_copy=all(p.startswith(str(copy.resolve())) for p in paths),
                              files_readable=all(Path(p).is_file() for p in paths),
                              chunks=chunks, embedded_chunks=embedded,
                              search_hit_source_in_copy=bool(found) and all(h.source_id in source_ids for h in found),
                              source_detail_readable=detail.id == mapping['doc-3']['source_id'])))
    finally:
        repo.close()


if __name__ == '__main__':
    if sys.argv[3:] in (['seed'], ['cache']):
        _seed_probe(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3])
    else:
        _probe(Path(sys.argv[1]), Path(sys.argv[2]))
