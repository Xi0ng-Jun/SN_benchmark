import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile

import pytest


def _git(path: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()


def _repo(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    (project / "backend/app/core/cache").mkdir(parents=True)
    (project / "backend/app/core/__init__.py").write_text("\n")
    (project / "backend/app/core/cache/__init__.py").write_text("CACHE = True\n")
    (project / "backend/app/core/cache/policy.py").write_text("POLICY = 'source'\n")
    (project / "backend/app/core/cache/backend.py").write_text("class Backend: pass\n")
    (project / "backend/app/core/cache/sqlite_backend.py").write_text("DB = 'source'\n")
    (project / "backend/app/core/cache/schema.json").write_text('{"kind":"resource"}\n')
    (project / "backend/app/core/cache/runtime.db").write_bytes(b"private runtime")
    (project / "backend/app/core/token.py").write_text("TOKEN = 'implementation'\n")
    (project / "backend/app/core/credentials.py").write_text("class Credentials: pass\n")
    (project / "backend/app/core/key.py").write_text("KEY = 'implementation'\n")
    (project / "backend/app/core/.env.secret").write_text("PRIVATE=1\n")
    (project / "backend/app/core/.env.example").write_text("PUBLIC=example\n")
    (project / "backend/app/core/app.log").write_text("private log\n")
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    subprocess.run(["git", "-C", str(project), "add", "."], check=True)
    subprocess.run([
        "git", "-C", str(project), "-c", "user.email=test@example.invalid",
        "-c", "user.name=test", "commit", "-qm", "fixture",
    ], check=True)
    return project


def _capture(project, store, **kwargs):
    from rag_eval.artifact_store import ArtifactStore
    from rag_eval.sn_source_capture import capture_sn_source

    return capture_sn_source(project, ArtifactStore(store), **kwargs)


def _tar_files(ref, store):
    from rag_eval.artifact_store import ArtifactStore

    archive = ArtifactStore(store).resolve(ref) / "product-source.tar"
    with tarfile.open(archive, "r:") as tar:
        return {member.name: tar.extractfile(member).read() for member in tar.getmembers()}


def test_capture_uses_dirty_and_untracked_worktree_bytes_and_keeps_source(tmp_path):
    from rag_eval.artifact_store import ArtifactStore

    project = _repo(tmp_path)
    tracked = project / "backend/app/core/token.py"
    tracked.write_text("TOKEN = 'dirty'\n")
    (project / "backend/app/core/new_resource.yaml").write_text("answer: actual\n")
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest()
              for path in project.rglob("*") if path.is_file()}
    result = _capture(project, tmp_path / "store")
    files = _tar_files(result.artifact_ref, tmp_path / "store")
    assert files["backend/app/core/token.py"] == b"TOKEN = 'dirty'\n"
    assert files["backend/app/core/new_resource.yaml"] == b"answer: actual\n"
    assert result.capture_status == "captured_unbound"
    assert result.dependencies_status == result.config_status == "not_captured"
    assert result.modified_paths == ("backend/app/core/token.py",)
    assert result.added_paths == ("backend/app/core/new_resource.yaml",)
    assert result.deleted_paths == ()
    after = {path: hashlib.sha256(path.read_bytes()).hexdigest()
             for path in project.rglob("*") if path.is_file()}
    assert before == after


def test_capture_preserves_real_cache_package_and_resources_but_excludes_private_files(tmp_path):
    from rag_eval.artifact_store import ArtifactStore

    project = _repo(tmp_path)
    result = _capture(project, tmp_path / "store")
    files = _tar_files(result.artifact_ref, tmp_path / "store")
    assert "backend/app/core/cache/__init__.py" in files
    assert "backend/app/core/cache/policy.py" in files
    assert "backend/app/core/cache/backend.py" in files
    assert "backend/app/core/cache/sqlite_backend.py" in files
    assert "backend/app/core/cache/schema.json" in files
    assert "backend/app/core/cache/runtime.db" not in files
    assert "backend/app/core/.env.secret" not in files
    assert "backend/app/core/app.log" not in files
    for name in ("token.py", "credentials.py", "key.py"):
        assert f"backend/app/core/{name}" in files


def test_capture_reports_deleted_and_does_not_archive_deleted_bytes(tmp_path):
    from rag_eval.artifact_store import ArtifactStore

    project = _repo(tmp_path)
    deleted = project / "backend/app/core/key.py"
    deleted.unlink()
    result = _capture(project, tmp_path / "store")
    assert "backend/app/core/key.py" in result.deleted_paths
    assert "backend/app/core/key.py" not in _tar_files(result.artifact_ref, tmp_path / "store")


def test_capture_is_deterministic_for_same_bytes_and_provenance(tmp_path):
    from rag_eval.artifact_store import ArtifactStore

    project = _repo(tmp_path)
    first = _capture(project, tmp_path / "store")
    second = _capture(project, tmp_path / "store")
    assert first.artifact_ref == second.artifact_ref
    assert first.source_manifest_sha256 == second.source_manifest_sha256
    assert first.actual_manifest == second.actual_manifest
    object_json = json.loads((ArtifactStore(tmp_path / "store").resolve(first.ref) / "object.json").read_text())
    assert object_json["identity"]["source_manifest_sha256"] == first.source_manifest_sha256
    assert object_json["identity"]["actual_manifest"] == first.actual_manifest
    directory = ArtifactStore(tmp_path / "store").resolve(first.ref)
    with tarfile.open(directory / "product-source.tar", "r:") as archive:
        members = archive.getmembers()
        assert [member.name for member in members] == sorted(first.actual_manifest)
        for member in members:
            assert member.isreg()
            assert member.mode == 0o600
            assert (member.mtime, member.uid, member.gid, member.uname, member.gname) == (0, 0, 0, "", "")
            data = archive.extractfile(member).read()
            assert first.actual_manifest[member.name] == {
                "sha256": hashlib.sha256(data).hexdigest(), "size": len(data),
            }
    encoded = json.dumps(first.actual_manifest, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
    assert first.source_manifest_sha256 == hashlib.sha256(encoded).hexdigest()
    assert str(project) not in json.dumps(object_json)


@pytest.mark.parametrize("relative", ["link.py", "fifo"])
def test_capture_rejects_symlink_and_nonregular_entries(tmp_path, relative):
    project = _repo(tmp_path)
    target = project / "backend/app/core" / relative
    if relative == "link.py":
        target.symlink_to(project / "backend/app/core/token.py")
    else:
        os.mkfifo(target)
    with pytest.raises(ValueError, match="symlink|regular|special"):
        _capture(project, tmp_path / "store")


def test_capture_rejects_store_overlap_and_ambiguous_generated_resource(tmp_path):
    from rag_eval.artifact_store import ArtifactStore

    project = _repo(tmp_path)
    with pytest.raises(ValueError, match="overlap"):
        _capture(project, project / "backend/app/store")
    ambiguous = project / "backend/app/runtime"
    ambiguous.mkdir()
    (ambiguous / "settings.json").write_text('{"private":true}\n')
    with pytest.raises(ValueError, match="ambiguous|selection"):
        _capture(project, tmp_path / "store")


@pytest.mark.parametrize("selection", [".", "/backend/app", "backend/./app", "backend//app", "../backend/app"])
def test_capture_rejects_root_alias_or_escape(tmp_path, selection):
    project = _repo(tmp_path)
    with pytest.raises(ValueError, match="relative|alias|dot"):
        _capture(project, tmp_path / "store", selected_roots=(selection,))


def test_capture_rejects_mutation_without_publishing(tmp_path, monkeypatch):
    from rag_eval import sn_source_capture

    project = _repo(tmp_path)
    original = sn_source_capture._read_stable_file
    changed = project / "backend/app/core/token.py"
    mutated = False

    def mutate(path):
        nonlocal mutated
        data = original(path)
        if path == changed and not mutated:
            changed.write_text("TOKEN = 'changed during capture'\n")
            mutated = True
        return data

    monkeypatch.setattr(sn_source_capture, "_read_stable_file", mutate)
    store = tmp_path / "store"
    with pytest.raises(sn_source_capture.SourceChangedDuringCapture):
        _capture(project, store)
    assert not (store / "objects/sn").exists() or not list((store / "objects/sn").iterdir())


def test_capture_rejects_tampered_artifact_on_resolve(tmp_path):
    from rag_eval.artifact_store import ArtifactStore

    project = _repo(tmp_path)
    result = _capture(project, tmp_path / "store")
    target = ArtifactStore(tmp_path / "store").resolve(result.artifact_ref)
    (target / "product-source.tar").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="hash|changed"):
        ArtifactStore(tmp_path / "store").resolve(result.artifact_ref)


def test_source_resources_and_source_named_build_are_not_generated_output(tmp_path):
    project = _repo(tmp_path)
    resource = project / "backend/app/core/prompts.jsonl"
    resource.write_bytes(b'{"role":"example"}\n')
    package = project / "backend/app/core/build"
    package.mkdir()
    (package / "__init__.py").write_text("BUILD = True\n")
    result = _capture(project, tmp_path / "store")
    files = _tar_files(result.ref, tmp_path / "store")
    assert files["backend/app/core/prompts.jsonl"] == b'{"role":"example"}\n'
    assert files["backend/app/core/build/__init__.py"] == b"BUILD = True\n"


def test_excluded_private_files_are_never_opened_in_worktree_or_git(tmp_path, monkeypatch):
    from rag_eval import sn_source_capture

    project = _repo(tmp_path)
    (project / "backend/app/core/.local").mkdir()
    (project / "backend/app/core/.local/private.json").write_text("private")
    (project / "backend/app/core/cache/entries.db-wal").write_text("private cache")
    real_open, real_git = os.open, subprocess.check_output

    def guarded_open(path, *args, **kwargs):
        assert Path(path).name not in {".env.secret", ".env.example", "runtime.db", "app.log", "private.json", "entries.db-wal"}
        return real_open(path, *args, **kwargs)

    def guarded_git(command, **kwargs):
        if "show" in command:
            assert not command[-1].endswith((".env.secret", ".env.example", "runtime.db", "app.log"))
        return real_git(command, **kwargs)

    monkeypatch.setattr(sn_source_capture.os, "open", guarded_open)
    monkeypatch.setattr(sn_source_capture.subprocess, "check_output", guarded_git)
    result = _capture(project, tmp_path / "store")
    assert "backend/app/core/cache/entries.db-wal" not in result.actual_manifest


def test_replaced_ancestor_cannot_be_followed_before_reading(tmp_path, monkeypatch):
    from rag_eval import sn_source_capture

    project = _repo(tmp_path)
    core = project / "backend/app/core"
    original = sn_source_capture._read_stable_file
    swapped = False

    def swap_before_read(path):
        nonlocal swapped
        if not swapped:
            outside = tmp_path / "outside"
            core.rename(outside)
            core.symlink_to(outside, target_is_directory=True)
            swapped = True
        return original(path)

    monkeypatch.setattr(sn_source_capture, "_read_stable_file", swap_before_read)
    store = tmp_path / "store"
    with pytest.raises(sn_source_capture.SourceChangedDuringCapture):
        _capture(project, store)
    assert not store.exists()


def test_untracked_ignored_module_and_sql_resource_are_still_actual_source(tmp_path):
    project = _repo(tmp_path)
    (project / ".gitignore").write_text("*.sql\nignored_module.py\n")
    (project / "backend/app/core/ignored_module.py").write_text("ACTUAL = True\n")
    (project / "backend/app/core/migration.sql").write_text("SELECT 1;\n")
    result = _capture(project, tmp_path / "store")
    files = _tar_files(result.ref, tmp_path / "store")
    assert files["backend/app/core/ignored_module.py"] == b"ACTUAL = True\n"
    assert files["backend/app/core/migration.sql"] == b"SELECT 1;\n"
    assert result.added_paths == ("backend/app/core/ignored_module.py", "backend/app/core/migration.sql")


@pytest.mark.parametrize("change", ["replace", "add", "delete", "head", "ancestry"])
def test_changes_during_archive_preparation_never_publish(tmp_path, monkeypatch, change):
    from rag_eval import sn_source_capture

    project = _repo(tmp_path)
    original = sn_source_capture._archive

    def mutate(files):
        data = original(files)
        target = project / "backend/app/core/token.py"
        if change == "replace":
            replacement = target.with_suffix(".replacement")
            replacement.write_bytes(target.read_bytes())
            replacement.replace(target)
        elif change == "add":
            (target.parent / "added.py").write_text("ADDED = True\n")
        elif change == "delete":
            target.unlink()
        elif change == "ancestry":
            outside = tmp_path / "outside"
            target.parent.rename(outside)
            target.parent.symlink_to(outside, target_is_directory=True)
        else:
            _git(project, "-c", "user.email=test@example.invalid", "-c", "user.name=test",
                 "commit", "--allow-empty", "-qm", "head moved")
        return data

    monkeypatch.setattr(sn_source_capture, "_archive", mutate)
    store = tmp_path / "store"
    with pytest.raises(sn_source_capture.SourceChangedDuringCapture):
        _capture(project, store)
    assert not store.exists()


def test_explicit_ambiguous_selection_requires_deliberate_scope(tmp_path):
    project = _repo(tmp_path)
    ambiguous = project / "backend/app/runtime"
    ambiguous.mkdir()
    (ambiguous / "schema.sql").write_text("SELECT 1;\n")
    result = _capture(project, tmp_path / "store", selected_roots=("backend/app/runtime",))
    assert set(result.actual_manifest) == {"backend/app/runtime/schema.sql"}


@pytest.mark.parametrize("target", [".env.link", ".local", "__pycache__", "ancestor"])
def test_symlinks_are_rejected_even_on_excluded_paths(tmp_path, target):
    project = _repo(tmp_path)
    if target == "ancestor":
        (project / "backend").rename(project / "real-backend")
        (project / "backend").symlink_to(project / "real-backend", target_is_directory=True)
    else:
        (project / "backend/app" / target).symlink_to(tmp_path / "missing")
    with pytest.raises(ValueError, match="symlink"):
        _capture(project, tmp_path / "store")


@pytest.mark.parametrize("roots", [(), ("backend/app", "backend/app/core"), ("backend/app", "backend/app")])
def test_empty_or_overlapping_selection_is_rejected_before_writes(tmp_path, roots):
    project = _repo(tmp_path)
    store = tmp_path / "store"
    with pytest.raises(ValueError):
        _capture(project, store, selected_roots=roots)
    assert not store.exists()
