import hashlib
import json
import subprocess
import sys
import tarfile
from io import BytesIO
from pathlib import Path

import pytest


@pytest.mark.parametrize('fault', ['missing_both', 'empty', 'bad_digest', 'different_record', 'inconsistent_record',
                                   None])
def test_reader_rejects_malformed_capture_identity(tmp_path, fault):
    from rag_eval.artifact_store import ArtifactStore
    from rag_eval.identity import fingerprint
    from rag_eval.run_reader import RunReadContext
    store = ArtifactStore(tmp_path / 'store')
    manifest = {'backend/app/m.py': {'sha256': hashlib.sha256(b'x').hexdigest(), 'size': 1}}
    identity = dict(source_format='sn-source-capture-v1', actual_manifest=manifest,
                    source_manifest_sha256=fingerprint(manifest))
    code = dict(identity)
    if fault == 'missing_both':
        identity.pop('actual_manifest')
        code.pop('actual_manifest')
    elif fault is None:
        # Runs now record only the manifest digest; the object keeps the manifest.
        code.pop('actual_manifest')
    elif fault == 'inconsistent_record':
        code['actual_manifest'] = {'backend/app/other.py': manifest['backend/app/m.py']}
    elif fault == 'empty':
        identity.update(actual_manifest={}, source_manifest_sha256=fingerprint({}))
        code = dict(identity)
    elif fault == 'bad_digest':
        identity['source_manifest_sha256'] = code['source_manifest_sha256'] = 'a' * 64
    else:
        code['actual_manifest'] = {'backend/app/other.py': manifest['backend/app/m.py']}
        code['source_manifest_sha256'] = fingerprint(code['actual_manifest'])
    sn = store.install_bytes('sn', {'product-source.tar': b'archive'}, identity=identity)
    evaluator = store.install_bytes('evaluator', {'eval.py': b'x'}, identity={})
    code.update(product_archive_sha256=hashlib.sha256(b'archive').hexdigest(),
                benchmark_source_hashes={'eval.py': hashlib.sha256(b'x').hexdigest()})
    if fault is None:
        RunReadContext().validate_sources(store, {'sources': {'sn': sn, 'evaluator': evaluator}}, code)
        return
    with pytest.raises(ValueError, match='[Mm]anifest|identity|source'):
        RunReadContext().validate_sources(store, {'sources': {'sn': sn, 'evaluator': evaluator}}, code)


def test_destination_ancestor_symlink_rejected_before_any_external_write(tmp_path):
    from rag_eval.artifact_store import ArtifactStore
    from rag_eval.sn_source_capture import capture_sn_source
    from rag_eval.runtime_environment import materialize_captured_sn_source
    project = _fixture_repo(tmp_path)
    store = ArtifactStore(tmp_path / 'store')
    capture = capture_sn_source(project, store)
    outside = tmp_path / 'outside'
    outside.mkdir()
    (tmp_path / 'link').symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match='symlink|ancestor'):
        materialize_captured_sn_source(store, capture.ref, tmp_path / 'link/new/nested/source')
    assert list(outside.iterdir()) == []


def _fixture_repo(tmp_path):
    project = tmp_path / "project"
    package = project / "backend/app/core"
    package.mkdir(parents=True)
    (project / "backend/app/__init__.py").write_text("")
    (project / "backend/app/core/__init__.py").write_text("")
    (package / "text_whitespace.py").write_text("VALUE = 'captured-v1'\n")
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    subprocess.run(["git", "-C", str(project), "add", "."], check=True)
    subprocess.run([
        "git", "-C", str(project), "-c", "user.email=test@example.invalid",
        "-c", "user.name=test", "commit", "-qm", "fixture",
    ], check=True)
    return project


def test_materialized_source_survives_original_change_and_reports_loaded_identity(tmp_path, monkeypatch):
    from rag_eval.artifact_store import ArtifactStore
    from rag_eval.runtime_environment import materialize_captured_sn_source, module_source_identity
    from rag_eval.sn_source_capture import capture_sn_source

    project = _fixture_repo(tmp_path)
    store = ArtifactStore(tmp_path / "store")
    captured = capture_sn_source(project, store)
    destination = tmp_path / "run" / "captured-source"
    materialized = materialize_captured_sn_source(store, captured.artifact_ref, destination)
    (project / "backend/app/core/text_whitespace.py").write_text("VALUE = 'mutable-after-capture'\n")

    script = """
import hashlib, json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import app.core.text_whitespace as module
print(json.dumps({
    'value': module.VALUE,
    'origin': str(Path(module.__file__).resolve()),
    'sha256': hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest(),
}))
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(materialized.backend_root)],
        check=True, capture_output=True, text=True,
    )
    loaded = json.loads(result.stdout)
    expected_path = destination / "backend/app/core/text_whitespace.py"
    assert loaded["value"] == "captured-v1"
    assert Path(loaded["origin"]) == expected_path
    assert loaded["sha256"] == hashlib.sha256(expected_path.read_bytes()).hexdigest()
    monkeypatch.syspath_prepend(str(materialized.backend_root))
    previous = {name: module for name, module in sys.modules.items()
                if name == 'app' or name.startswith('app.')}
    for name in previous:
        del sys.modules[name]
    try:
        identity = module_source_identity(
            "app.core.text_whitespace", materialized,
            expected_manifest=captured.actual_manifest,
        )
    finally:
        for name in list(sys.modules):
            if name == 'app' or name.startswith('app.'):
                del sys.modules[name]
        sys.modules.update(previous)
    assert identity == {
        "module": "app.core.text_whitespace",
        "origin": "backend/app/core/text_whitespace.py",
        "sha256": captured.actual_manifest["backend/app/core/text_whitespace.py"]["sha256"],
        "size": captured.actual_manifest["backend/app/core/text_whitespace.py"]["size"],
    }


def test_materializer_rejects_archive_path_escape_without_creating_destination(tmp_path):
    from rag_eval.artifact_store import ArtifactStore
    from rag_eval.runtime_environment import materialize_captured_sn_source

    stream = BytesIO()
    with tarfile.open(fileobj=stream, mode="w:") as archive:
        data = b"VALUE = 'escape'\n"
        member = tarfile.TarInfo("../escape.py")
        member.size = len(data)
        archive.addfile(member, BytesIO(data))
    store = ArtifactStore(tmp_path / "store")
    from rag_eval.identity import fingerprint
    expected = {"../escape.py": {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}}
    ref = store.install_bytes(
        "sn", {"product-source.tar": stream.getvalue()},
        identity={"source_format": "sn-source-capture-v1", "actual_manifest": expected,
                  "source_manifest_sha256": fingerprint(expected)},
    )
    destination = tmp_path / "run" / "captured-source"
    with pytest.raises(ValueError, match="archive|path|member"):
        materialize_captured_sn_source(store, ref, destination)
    assert not destination.exists()


def test_shared_snapshot_binds_captured_ref_and_materializes_private_source(tmp_path):
    from rag_eval.artifact_store import ArtifactStore, read_run_refs, write_run_refs
    from rag_eval.runtime_environment import snapshot_sources
    from rag_eval.sn_source_capture import capture_sn_source

    project = _fixture_repo(tmp_path)
    (project / "backend/app/core/text_whitespace.py").write_text("VALUE = 'dirty-captured'\n")
    store = ArtifactStore(tmp_path / "store")
    captured = capture_sn_source(project, store)
    bundle = store.install_bytes("bundle", {"manifest.json": b"{}"}, identity={})
    index = store.install_bytes("index", {"index.json": b"{}"}, identity={})
    run = tmp_path / "run"
    run.mkdir()
    write_run_refs(run, store.root, {"bundle": bundle, "index": index})

    identity = snapshot_sources(
        Path(__file__).resolve().parents[1], project, run,
        artifact_root=store.root, captured_source_ref=captured.artifact_ref,
    )

    assert identity["source_format"] == "sn-source-capture-v1"
    assert identity["source_manifest_sha256"] == captured.source_manifest_sha256
    assert (run / "captured-source/backend/app/core/text_whitespace.py").read_text() == "VALUE = 'dirty-captured'\n"
    refs = read_run_refs(run)
    assert refs["sources"]["sn"] == captured.artifact_ref
    assert refs["sources"]["evaluator"]["kind"] == "evaluator"
