import json
from pathlib import Path

import pytest

from rag_eval.artifact_store import ArtifactStore
from rag_eval.bundle_index import install_bundle
from rag_eval.notebook_bundle import prepare


def _bundle(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    raw = {
        "paper": {
            "title": "Offline rehearsal",
            "abstract": "A synthetic paper.",
            "full_text": [{"section_name": "Results", "paragraphs": ["A fact."]}],
            "qas": [{
                "question_id": "q1",
                "question": "What is the fact?",
                "answers": [{"answer": {
                    "unanswerable": False,
                    "extractive_spans": ["A fact"],
                    "free_form_answer": "",
                    "yes_no": None,
                    "evidence": ["A fact."],
                }}],
            }],
        }
    }
    raw_path = tmp_path / "raw.json"
    source_path = tmp_path / "source.json"
    raw_path.write_text(json.dumps(raw), encoding="utf-8")
    source_path.write_text(json.dumps({
        "dataset": "qasper", "split": "test", "revision": "synthetic",
        "source_url": "https://example.invalid/qasper", "license": "CC-BY-4.0",
    }), encoding="utf-8")
    bundle_dir = tmp_path / "bundle"
    bundle = prepare("qasper", raw_path, source_path, bundle_dir)
    artifact_root = tmp_path / "artifacts"
    refs = install_bundle(bundle_dir, artifact_root)
    sn_ref = ArtifactStore(artifact_root).install_bytes(
        "sn", {"product-source.tar": b"synthetic-captured-source"},
        identity={"source_format": "sn-source-capture-v1", "synthetic": True},
    )
    return bundle["partitions"][0]["partition_id"], bundle_dir, artifact_root, refs, sn_ref


def _inputs(tmp_path):
    partition_id, bundle_dir, artifact_root, refs, sn_ref = _bundle(tmp_path)
    project = tmp_path / "synthetic-project"
    (project / ".local").mkdir(parents=True)
    (project / ".local/model-services.toml").write_text("# credential-free synthetic catalog\n", encoding="utf-8")
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("pytest>=8\n", encoding="utf-8")
    return dict(
        bundle_dir=bundle_dir,
        artifact_root=artifact_root,
        artifact_index_id=refs["index"]["id"],
        partition_id=partition_id,
        case_id="qasper:q1",
        project=project,
        owner_evidence_dir=tmp_path / "owner",
        cli_run_dir=tmp_path / "cli-run",
        sn_source_ref=sn_ref,
        runtime_refs={"requirements": requirements.name, "interpreter": {"executable": "/usr/bin/python3", "prefix": "/usr"}},
        dependency_requirements=requirements,
        expected_interpreter={"executable": "/usr/bin/python3", "prefix": "/usr"},
    )


class _Owner:
    def __init__(self, result):
        self.result = result

    def run_owned_command(self, command, *, run_dir, **kwargs):
        run_dir = Path(run_dir)
        run_dir.mkdir()
        result = self.result
        (run_dir / "result.json").write_text(json.dumps(result), encoding="utf-8")
        (run_dir / "diagnostic.json").write_text(json.dumps({"drain_complete": result["outputs"]["drain_complete"]}), encoding="utf-8")
        return result


def _owner_result(*, confirmed=True, guard=True, pidfd=True, returncode=0):
    return {
        "spawn": {"started": True, "pid": 123, "pidfd_error": None if pidfd else {"error": "OSError"}},
        "exit": {"confirmed": confirmed, "status": "confirmed" if confirmed else "unknown",
                  "returncode": returncode if confirmed else None, "termination_signal": None},
        "outputs": {"drain_complete": confirmed, "drain_status": "complete" if confirmed else "incomplete",
                     "stdout_observed_prefix_bytes": 10, "stderr_observed_prefix_bytes": 0},
        "network_guard": {"requested": True, "available": guard, "mechanism": "unshare-user-net" if guard else None},
        "residuals": {"completeness": "observed_process_group_snapshot", "clean_claimable": True},
    }


def test_success_writes_evidence_only_contract(tmp_path, monkeypatch):
    from rag_eval import captured_cli_rehearsal as module

    args = _inputs(tmp_path)
    monkeypatch.setattr(module, "_load_owner_driver", lambda path: _Owner(_owner_result()))
    result = module.run_captured_cli_rehearsal(**args)

    assert result["ok"] is True
    run = args["cli_run_dir"]
    state = json.loads((run / "state.json").read_text())
    assert state["phase"] == "diagnostic_complete"
    assert state["phase"] != "finished"
    assert state["offline"] is True and state["evidence_only"] is True
    assert state["job_executable"] is False
    row = json.loads((run / "outputs.jsonl").read_text().splitlines()[0])
    assert row["case_id"] == args["case_id"]
    assert row["status"] == "rehearsal_only"
    diagnostic = json.loads((run / "diagnostic-score.json").read_text())
    assert diagnostic["score_kind"] == "diagnostic"
    assert diagnostic["official"] is False
    pending = json.loads((run / "official-score-pending.json").read_text())
    assert pending["status"] == "pending"
    assert pending["official_score"] is None
    assert (run / "wrapper-facts.json").is_file()
    assert (args["owner_evidence_dir"] / "result.json").is_file()


@pytest.mark.parametrize("which", ["owner_evidence_dir", "cli_run_dir"])
def test_nested_or_existing_directories_rejected(tmp_path, monkeypatch, which):
    from rag_eval import captured_cli_rehearsal as module

    args = _inputs(tmp_path)
    monkeypatch.setattr(module, "_load_owner_driver", lambda path: _Owner(_owner_result()))
    if which == "owner_evidence_dir":
        args[which] = args["cli_run_dir"] / "nested"
    else:
        args[which] = args["owner_evidence_dir"] / "nested"
    with pytest.raises(ValueError, match="nested|fresh|absolute"):
        module.run_captured_cli_rehearsal(**args)

    second = tmp_path / "second"
    second.mkdir()
    args = _inputs(second)
    monkeypatch.setattr(module, "_load_owner_driver", lambda path: _Owner(_owner_result()))
    args[which].mkdir(parents=True)
    with pytest.raises(ValueError, match="fresh|exists"):
        module.run_captured_cli_rehearsal(**args)


def test_missing_ref_rejected_before_launch(tmp_path):
    from rag_eval import captured_cli_rehearsal as module

    args = _inputs(tmp_path)
    args.pop("sn_source_ref")
    with pytest.raises(ValueError, match="sn_source_ref"):
        module.run_captured_cli_rehearsal(**args)
    assert not args["owner_evidence_dir"].exists()
    assert not args["cli_run_dir"].exists()


@pytest.mark.parametrize("owner", [_owner_result(confirmed=False, pidfd=False), _owner_result(confirmed=True, guard=False)])
def test_owner_unknown_or_guard_blocked_is_diagnostic_failure(tmp_path, monkeypatch, owner):
    from rag_eval import captured_cli_rehearsal as module

    args = _inputs(tmp_path)
    monkeypatch.setattr(module, "_load_owner_driver", lambda path: _Owner(owner))
    result = module.run_captured_cli_rehearsal(**args)
    assert result["ok"] is False
    state = json.loads((args["cli_run_dir"] / "state.json").read_text())
    assert state["phase"] == "blocked"
    assert state["job_executable"] is False
    pending = json.loads((args["cli_run_dir"] / "official-score-pending.json").read_text())
    assert pending["status"] == "pending"
    assert pending["reason"] in {"owner_status_unknown", "network_guard_unavailable"}


def test_timeout_receipt_stays_unknown(tmp_path, monkeypatch):
    from rag_eval import captured_cli_rehearsal as module

    args = _inputs(tmp_path)
    owner = _owner_result(confirmed=False, pidfd=True)
    owner["control"] = {"timeout_requested": True}
    monkeypatch.setattr(module, "_load_owner_driver", lambda path: _Owner(owner))
    result = module.run_captured_cli_rehearsal(**args)
    assert result["ok"] is False
    receipt = json.loads((args["owner_evidence_dir"] / "result.json").read_text())
    assert receipt["exit"]["status"] == "unknown"
    assert json.loads((args["cli_run_dir"] / "diagnostic-score.json").read_text())["official"] is False
