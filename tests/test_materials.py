"""Step 4: materials only grow and stay as added; questions and answers are archived."""
import os

import pytest

from harness_builder.ledger import Ledger
from harness_builder.materials import Materials, MaterialsError, Questions


@pytest.fixture
def ledger(tmp_path):
    return Ledger(tmp_path / "ledger")


@pytest.fixture
def materials(tmp_path, ledger):
    return Materials(tmp_path / "materials", ledger)


def test_additions_are_separate_and_recorded(materials, tmp_path):
    src = tmp_path / "notes"
    (src / "sub").mkdir(parents=True)
    (src / "a.md").write_text("first")
    (src / "sub" / "b.txt").write_text("second")
    one = materials.add(src, note="from the researcher")
    f = tmp_path / "paper.txt"
    f.write_text("a paper")
    two = materials.add(f)
    three = materials.add_text("tip from another model", "tip.md", "check the units")
    assert [one["material"], two["material"], three["material"]] == [
        "0001-notes", "0002-paper.txt", "0003-tip-from-another-model"]
    assert (materials.root / "0001-notes" / "sub" / "b.txt").read_text() == "second"
    assert (materials.root / "0002-paper.txt" / "paper.txt").read_text() == "a paper"
    assert not os.access(materials.root / "0001-notes" / "a.md", os.W_OK)
    assert [e["material"] for e in materials.entries()] == [
        "0001-notes", "0002-paper.txt", "0003-tip-from-another-model"]
    assert materials.verify() == []
    (src / "a.md").write_text("changed at the source")
    assert materials.verify() == []  # the material is a copy


def test_changes_to_materials_are_found(materials, tmp_path):
    src = tmp_path / "notes"
    src.mkdir()
    (src / "a.md").write_text("first")
    materials.add(src)
    materials.add_text("x", "x.md", "x")
    p = materials.root / "0001-notes" / "a.md"
    os.chmod(p.parent, 0o755)
    os.chmod(p, 0o644)
    p.write_text("edited")
    os.chmod(materials.root / "0002-x", 0o755)
    (materials.root / "0002-x" / "x.md").unlink()
    (materials.root / "0003-sneaked-in").mkdir()
    problems = materials.verify()
    assert any("0001-notes" in p and "changed" in p for p in problems)
    assert any("0002-x" in p for p in problems)
    assert any("0003-sneaked-in" in p and "not on record" in p for p in problems)


def test_missing_source_is_refused(materials, tmp_path):
    with pytest.raises(MaterialsError):
        materials.add(tmp_path / "nope")


def test_questions_and_answers(materials, ledger, tmp_path):
    qs = Questions(tmp_path / "questions", ledger, materials)
    q = qs.ask("What unit?", "Not in the materials.", "Assume metres.")
    assert q["id"] == "q0001"
    with pytest.raises(MaterialsError):
        qs.ask("Why?", "", "none")
    assert qs.list()[0]["answers"] == []
    m = qs.answer("q0001", "Millimetres.")
    text = (materials.root / m["material"] / "answer.md").read_text()
    assert "What unit?" in text and "Millimetres." in text
    assert qs.list()[0]["answers"] == [m["material"]]
    with pytest.raises(MaterialsError):
        qs.answer("q0009", "?")
    kinds = [e["kind"] for e in ledger.entries()]
    assert kinds == ["question", "material_added", "answer"]
    assert materials.verify() == []
