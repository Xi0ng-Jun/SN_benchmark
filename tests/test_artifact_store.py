from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

import pytest

from rag_eval.artifact_store import ArtifactStore


def test_content_addressed_one_copy_and_source_unchanged(tmp_path):
    source = tmp_path / 'data.txt'
    source.write_text('actual immutable bytes')
    store = ArtifactStore(tmp_path / 'store')
    first = store.install_files('bundle', {'data': source}, identity={'source': 'test'})
    second = store.install_files('bundle', {'data': source}, identity={'source': 'test'})
    assert first == second
    assert source.read_text() == 'actual immutable bytes'
    assert len(list((tmp_path / 'store/objects/bundle').iterdir())) == 1
    assert (store.resolve(first) / 'data').read_text() == source.read_text()


def test_concurrent_publication_same_content(tmp_path):
    source = tmp_path / 'data'; source.write_bytes(b'a' * 4096)
    store = ArtifactStore(tmp_path / 'store')
    with ThreadPoolExecutor(max_workers=4) as pool:
        refs = list(pool.map(lambda _: store.install_files('bundle', {'data': source}, identity={}), range(8)))
    assert all(ref == refs[0] for ref in refs)
    assert len(list((tmp_path / 'store/objects/bundle').iterdir())) == 1


def test_tampered_object_rejected_even_with_edited_manifest(tmp_path):
    store = ArtifactStore(tmp_path / 'store')
    ref = store.install_bytes('index', {'part.json': b'{"actual":1}'}, identity={'revision': 1})
    target = store.resolve(ref)
    (target / 'part.json').write_bytes(b'{"actual":2}')
    with pytest.raises(ValueError, match='hash|changed'):
        store.resolve(ref)
    manifest = target / 'object.json'
    parsed = json.loads(manifest.read_text()); parsed['identity']['revision'] = 2
    manifest.write_text(json.dumps(parsed))
    with pytest.raises(ValueError, match='hash|changed'):
        store.resolve(ref)


@pytest.mark.parametrize('name', ['../escape', '/absolute', 'a/../b', 'object.json'])
def test_file_names_reject_escape_and_reserved_manifest(tmp_path, name):
    store = ArtifactStore(tmp_path / 'store')
    with pytest.raises(ValueError):
        store.install_bytes('index', {name: b'bytes'}, identity={})


def test_external_symlink_is_not_installed(tmp_path):
    source = tmp_path / 'actual'; source.write_text('secret')
    link = tmp_path / 'link'; link.symlink_to(source)
    with pytest.raises(ValueError, match='link'):
        ArtifactStore(tmp_path / 'store').install_files('bundle', {'raw': link}, identity={})


def test_missing_object_and_invalid_id_fail_without_creating_it(tmp_path):
    store = ArtifactStore(tmp_path / 'store')
    for ref in ({'kind': 'bundle', 'id': '../escape', 'manifest_sha256': 'a'*64},
                {'kind': 'bundle', 'id': 'a'*64, 'manifest_sha256': 'b'*64}):
        with pytest.raises(ValueError):
            store.resolve(ref)


def test_interrupted_install_never_publishes_partial_object(tmp_path, monkeypatch):
    from rag_eval import artifact_store
    source = tmp_path/'source'; source.write_text('unchanged')
    original = artifact_store.shutil.copyfile
    def fail(source, destination):
        Path(destination).write_bytes(b'partial')
        raise OSError('disk failed')
    monkeypatch.setattr(artifact_store.shutil, 'copyfile', fail)
    store = ArtifactStore(tmp_path/'store')
    with pytest.raises(OSError, match='disk failed'):
        store.install_files('bundle', {'raw': source}, identity={})
    assert not list((tmp_path/'store/objects/bundle').iterdir())
    assert source.read_text() == 'unchanged'
    monkeypatch.setattr(artifact_store.shutil, 'copyfile', original)
    store.resolve(store.install_files('bundle', {'raw': source}, identity={}))


def test_object_with_linked_ancestor_rejected(tmp_path):
    store = ArtifactStore(tmp_path/'store')
    ref = store.install_bytes('index', {'nested/data': b'value'}, identity={})
    path = store.resolve(ref)
    backup = tmp_path/'outside'; (path/'nested').rename(backup)
    (path/'nested').symlink_to(backup, target_is_directory=True)
    with pytest.raises(ValueError, match='link'):
        store.resolve(ref)


def test_shared_source_snapshot_preserves_identity_and_archives_once(tmp_path, monkeypatch):
    import subprocess
    from rag_eval.runtime_environment import snapshot_sources
    from rag_eval.artifact_store import write_run_refs
    root = tmp_path/'eval'; (root/'src/rag_eval').mkdir(parents=True); (root/'scripts').mkdir()
    (root/'src/rag_eval/a.py').write_text('VERSION=1\n'); (root/'scripts/a.py').write_text('pass\n')
    (root/'pyproject.toml').write_text('[project]\nname="synthetic"\nversion="1"\n')
    product = tmp_path/'product'; product.mkdir(); (product/'code.py').write_text('pass\n')
    subprocess.run(['git','init','-q',str(product)],check=True)
    subprocess.run(['git','-C',str(product),'add','code.py'],check=True)
    subprocess.run(['git','-C',str(product),'-c','user.email=test@example.invalid','-c','user.name=test','commit','-qm','synthetic source'],check=True)
    store = ArtifactStore(tmp_path/'store')
    bundle = store.install_bytes('bundle',{'raw': b'raw'},identity={})
    index = store.install_bytes('index',{'index.json': b'{}'},identity={})
    real_run = subprocess.run; archives = []
    def count(args, **kwargs):
        if 'archive' in args:
            archives.append(args)
        return real_run(args, **kwargs)
    monkeypatch.setattr(subprocess, 'run', count)
    identities=[]
    for name in ('first','second'):
        run = tmp_path/name; run.mkdir()
        write_run_refs(run,store.root,{'bundle':bundle,'index':index})
        identities.append(snapshot_sources(root,product,run,artifact_root=store.root))
        assert not (run/'source').exists()
        assert not (run/'product-source.tar').exists()
    assert identities[0] == identities[1]
    assert len(archives)==1
    assert len(list((store.root/'objects/evaluator').iterdir()))==1
    assert len(list((store.root/'objects/sn').iterdir()))==1


def test_run_reference_roles_reject_wrong_source_kind(tmp_path):
    from rag_eval.artifact_store import read_run_refs, write_run_refs
    store = ArtifactStore(tmp_path/'artifacts')
    bundle = store.install_bytes('bundle', {'manifest.json': b'{}'}, identity={})
    index = store.install_bytes('index', {'index.json': b'{}'}, identity={})
    run = tmp_path/'run'; run.mkdir()
    write_run_refs(run, store.root, {'bundle': bundle, 'index': index, 'sources': {'sn': bundle}})
    with pytest.raises(ValueError, match='source|role'):
        read_run_refs(run)


def test_shared_source_bytes_must_match_original_code_identity(tmp_path):
    from rag_eval.artifact_store import write_run_refs
    from rag_eval.run_reader import RunReadContext
    store = ArtifactStore(tmp_path/'artifacts')
    evaluator = store.install_bytes('evaluator', {'src/rag_eval/x.py': b'code'}, identity={})
    sn = store.install_bytes('sn', {'product-source.tar': b'archive'}, identity={'product_revision': 'r'})
    reader = RunReadContext()
    refs = {'sources': {'evaluator': evaluator, 'sn': sn}}
    code = {'benchmark_source_hashes': {'src/rag_eval/x.py': 'wrong'},
            'product_revision': 'r', 'product_archive_sha256': 'wrong'}
    with pytest.raises(ValueError, match='source|code'):
        reader.validate_sources(store, refs, code)


def test_publication_cannot_write_inside_shared_object(tmp_path):
    from rag_eval.benchmark_submission import write_submission
    from test_notebook_execution import bundle_fixture
    from test_benchmark_submission import method
    from rag_eval.bundle_index import install_bundle
    bundle_fixture(tmp_path)
    refs = install_bundle(tmp_path/'bundle', tmp_path/'artifacts')
    store = ArtifactStore(tmp_path/'artifacts'); directory = store.resolve(refs['index'])
    with pytest.raises(ValueError, match='artifact store'):
        write_submission(tmp_path/'bundle', directory/'submission', method=method(), predictions=[])
    assert not (directory/'submission').exists()
    store.resolve(refs['index'])
