"""Step 2: the append-only ledger finds edits, deletions, reordering and truncation."""
import json

import pytest

from harness_builder.ledger import Ledger, LedgerError


@pytest.fixture
def ledger(tmp_path):
    lg = Ledger(tmp_path / "ledger")
    for i in range(4):
        lg.append("thing", n=i, text=f"entry {i}")
    return lg


def lines(lg):
    return lg.path.read_text().splitlines()


def write(lg, ls):
    lg.path.write_text("".join(l + "\n" for l in ls))


def test_intact_ledger_verifies(ledger):
    assert ledger.verify() == []
    es = ledger.entries()
    assert [e["seq"] for e in es] == [0, 1, 2, 3]
    assert [e["data"]["n"] for e in es] == [0, 1, 2, 3]
    assert es[1]["prev"] == es[0]["hash"]
    assert ledger.last("thing")["data"]["n"] == 3
    ledger.check()


def test_reopened_ledger_continues_the_chain(ledger, tmp_path):
    again = Ledger(tmp_path / "ledger")
    again.append("more")
    assert again.verify() == []
    assert len(again.entries()) == 5


def test_edited_entry_is_found(ledger):
    ls = lines(ledger)
    e = json.loads(ls[1])
    e["data"]["text"] = "rewritten"
    ls[1] = json.dumps(e)
    write(ledger, ls)
    assert any("line 2" in p and "hash" in p for p in ledger.verify())
    with pytest.raises(LedgerError):
        ledger.check()


def test_deleted_middle_entry_is_found(ledger):
    ls = lines(ledger)
    write(ledger, ls[:1] + ls[2:])
    assert ledger.verify()


def test_deleted_last_entry_is_found(ledger):
    write(ledger, lines(ledger)[:-1])
    assert any("head" in p for p in ledger.verify())


def test_reordered_entries_are_found(ledger):
    ls = lines(ledger)
    ls[1], ls[2] = ls[2], ls[1]
    write(ledger, ls)
    assert ledger.verify()


def test_garbage_line_is_found(ledger):
    write(ledger, lines(ledger) + ["not json"])
    assert any("not JSON" in p for p in ledger.verify())
