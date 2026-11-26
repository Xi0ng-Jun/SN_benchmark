"""Step 2: harness versions are frozen copies identified by content."""
import os
import shutil

import pytest

from harness_builder.harness_repo import HarnessError, HarnessRepo, tree_hash
from harness_builder.ledger import Ledger


@pytest.fixture
def repo(tmp_path):
    return HarnessRepo(tmp_path / "repo", Ledger(tmp_path / "ledger"))


@pytest.fixture
def folder(tmp_path, make_harness):
    return make_harness("correct")


def test_save_freezes_a_copy(repo, folder):
    v = repo.save(folder, note="first")
    assert v["version"] == "v0" and v["note"] == "first"
    p = repo.path("v0")
    assert (p / "settings.json").read_text() == (folder / "settings.json").read_text()
    assert os.access(p / "start", os.X_OK)
    assert not os.access(p / "settings.json", os.W_OK)
    assert not os.access(p, os.W_OK)
    assert repo.verify("v0")
    (folder / "settings.json").write_text('{"answer": "wrong"}')
    assert repo.verify("v0")
    assert "correct" in (p / "settings.json").read_text()
    assert [e["kind"] for e in repo.ledger.entries()] == ["harness_saved"]


def test_same_content_gives_same_version(repo, folder, tmp_path):
    a = repo.save(folder)
    other = tmp_path / "other"
    shutil.copytree(folder, other)
    (other / "__pycache__").mkdir()
    (other / "__pycache__" / "x.pyc").write_bytes(b"junk")
    assert repo.save(other) == a
    assert len(repo.versions()) == 1
    assert len(repo.ledger.entries()) == 1


def test_changes_give_new_versions(repo, folder):
    repo.save(folder)
    (folder / "notes.md").write_text("hello")
    assert repo.save(folder)["version"] == "v1"
    os.chmod(folder / "notes.md", 0o755)
    assert repo.save(folder)["version"] == "v2"
    os.symlink("notes.md", folder / "link")
    v3 = repo.save(folder)
    assert v3["version"] == "v3"
    assert os.readlink(repo.path("v3") / "link") == "notes.md"
    assert [m["version"] for m in repo.versions()] == ["v0", "v1", "v2", "v3"]


def test_saving_a_stored_version_again_works(repo, folder):
    repo.save(folder)
    assert repo.save(repo.path("v0"))["version"] == "v0"
    assert not [p for p in repo.versions_dir.iterdir() if p.name.startswith(".incoming")]


def test_tampering_with_a_version_is_found(repo, folder):
    repo.save(folder)
    p = repo.path("v0") / "settings.json"
    os.chmod(p, 0o644)
    p.write_text('{"answer": "wrong"}')
    assert not repo.verify("v0")


def test_unknown_version_is_refused(repo):
    for bad in ("v9", "x", "v", "../v0"):
        with pytest.raises(HarnessError):
            repo.get(bad)


def test_tree_hash_sees_exec_bit_and_content(tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    (d / "f").write_text("a")
    h1 = tree_hash(d)
    os.chmod(d / "f", 0o755)
    h2 = tree_hash(d)
    (d / "f").write_text("b")
    assert len({h1, h2, tree_hash(d)}) == 3


def test_read_only_folder_can_be_saved(repo, folder, tmp_path):
    repo.save(folder)
    ro = tmp_path / "ro"
    shutil.copytree(repo.path("v0"), ro)  # keeps the read-only modes
    os.chmod(ro, 0o755)
    (ro / "extra.txt").write_text("x")
    os.chmod(ro, 0o555)
    assert repo.save(ro)["version"] == "v1"
    os.chmod(ro, 0o755)
