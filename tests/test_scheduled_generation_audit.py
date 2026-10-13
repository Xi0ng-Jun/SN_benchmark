"""Pure saved-row audit tests; no SN, host, network, scorer or Job imports."""
import copy
import json

import pytest

from rag_eval.scheduled_generation_audit import (
    ATTEMPT_KIND,
    FORMAT,
    TERMINAL_KIND,
    ScheduledGenerationAuditError,
    audit_scheduled_generation,
)


def service(**changes):
    value = {
        "service_id": "chat",
        "kind": "chat",
        "protocol": "openai",
        "model": "synthetic_a",
        "endpoint_sha256": "b" * 64,
        "credential_variable_name": "SYNTHETIC_FIXTURE_KEY",
        "credential_variable_configured": True,
        "max_concurrency": 2,
        "top_p": None,
        "generation": "a" * 64,
    }
    value.update(changes)
    return value


def context(**changes):
    value = {"workload_id": "ask_answer", "support_id": "mdl-synthetic", "priority": "interactive"}
    value.update(changes)
    return value


def attempt(call_id=1, **changes):
    row = {
        "kind": ATTEMPT_KIND,
        "call_id": call_id,
        "service": service(),
        "context": context(),
        "snapshot_sha256": "c" * 64,
        "phase": "submission",
        "recorded_at": "2026-10-12T17:52:37.552414+00:00",
    }
    row.update(changes)
    return row


def terminal(call_id=1, *, status="completed", phase="future", error_type=None, **changes):
    row = {
        "kind": TERMINAL_KIND,
        "call_id": call_id,
        "service": service(),
        "context": context(),
        "snapshot_sha256": "c" * 64,
        "phase": phase,
        "status": status,
        "error_type": error_type,
        "recorded_at": "2026-10-12T17:52:37.556625+00:00",
    }
    row.update(changes)
    return row


def paired(call_id=1, *, status="completed", phase="future", error_type=None):
    entry = attempt(call_id)
    return entry, terminal(call_id, status=status, phase=phase, error_type=error_type,
                           service=copy.deepcopy(entry["service"]),
                           context=copy.deepcopy(entry["context"]))


def audit(rows, *, present=True, truncated=False):
    return audit_scheduled_generation(rows, journal_present=present, truncated_tail=truncated)


def test_absent_journal_is_no_observation_and_does_not_duplicate_reader_warning():
    assert issubclass(ScheduledGenerationAuditError, ValueError)
    result = audit([], present=False)
    assert result["format"] == FORMAT
    assert result["observation_state"] == "no_observation"
    assert result["attempt_count"] == result["terminal_count"] == result["paired_count"] == 0
    assert result["unfinished"] == [] and result["pairs"] == [] and result["warnings"] == []
    assert result["legacy_row_count"] == 0
    assert result["completeness"] == {
        "pair_state": "not_observed", "unpaired_attempts": 0, "orphan_terminals": 0,
        "truncated_tail": False, "history_exhaustive": False, "scope": "observed_journal_only"}


def test_present_zero_rows_is_observed_empty_and_distinct_from_absent():
    result = audit([])
    assert result["observation_state"] == "observed_empty"
    assert result["completeness"]["pair_state"] == "empty"
    assert result["completeness"]["history_exhaustive"] is False
    assert len(result["warnings"]) == 1 and "empty" in result["warnings"][0]
    assert result["legacy_row_count"] == 0


def test_legacy_only_rows_are_counted_and_never_reinterpreted():
    rows = [
        {"event": "started", "call_id": "legacy-1", "case_id": "qasper_0001",
         "raw_client_response": None},
        {"event": "completed", "call_id": "legacy-1", "case_id": "qasper_0001"},
        {"event": "failed", "call_id": "legacy-2", "case_id": "qasper_0002",
         "raw_client_response": "private answer body"},
        {"event": "custom", "case_id": "qasper_0003", "note": "ignored, not scheduled"},
    ]
    result = audit(rows)
    assert result["observation_state"] == "legacy_only"
    assert result["legacy_row_count"] == 3
    assert result["attempt_count"] == result["terminal_count"] == result["paired_count"] == 0
    assert result["completeness"]["pair_state"] == "empty"
    assert "unfinished_model_calls" not in result
    text = json.dumps(result)
    assert "qasper_0001" not in text and "private answer body" not in text


def test_interleaved_calls_pair_by_call_id_not_adjacent_rows():
    rows = [attempt(1), attempt(2), terminal(2), terminal(1)]
    result = audit(rows)
    assert result["observation_state"] == "scheduled_observed"
    assert result["attempt_count"] == result["terminal_count"] == result["paired_count"] == 2
    assert result["unfinished"] == []
    assert [(pair["call_id"], pair["attempt_row"], pair["terminal_row"])
            for pair in result["pairs"]] == [(1, 0, 3), (2, 1, 2)]
    assert result["completeness"]["pair_state"] == "complete"
    assert result["completeness"]["history_exhaustive"] is False


def test_future_terminal_completed_error_and_cancelled_statuses():
    rows = []
    for call_id, status, error_type in [(1, "completed", None), (2, "error", "RuntimeError"),
                                        (3, "cancelled", None)]:
        entry, done = paired(call_id, status=status, error_type=error_type)
        rows.extend([entry, done])
    result = audit(rows)
    assert [(pair["terminal_kind"], pair["status"]) for pair in result["pairs"]] == [
        ("returned_future", "completed"), ("returned_future", "error"), ("returned_future", "cancelled")]
    assert result["completeness"]["pair_state"] == "complete"
    assert "private exception body" not in json.dumps(result)


def test_sync_refusal_is_submission_error_not_pending():
    entry, done = paired(9, phase="submission", status="error", error_type="ValueError")
    result = audit([entry, done])
    assert result["unfinished"] == []
    assert result["paired_count"] == 1
    pair = result["pairs"][0]
    assert pair["terminal_kind"] == "sync_refusal" and pair["status"] == "error"
    assert result["completeness"]["pair_state"] == "complete"


def test_attempt_without_terminal_is_pending_with_incomplete_warning():
    result = audit([attempt(3)])
    assert result["attempt_count"] == 1 and result["terminal_count"] == 0 and result["paired_count"] == 0
    assert result["unfinished"] == [{"call_id": 3, "state": "pending"}]
    assert result["completeness"]["pair_state"] == "incomplete"
    assert result["completeness"]["unpaired_attempts"] == 1
    assert any("without a terminal" in warning for warning in result["warnings"])
    assert "unfinished_model_calls" not in result


def test_attempts_sharing_support_id_are_never_merged():
    first = attempt(1)
    second = attempt(2, service=copy.deepcopy(first["service"]), context=copy.deepcopy(first["context"]))
    result = audit([first, second])
    assert result["attempt_count"] == 2 and result["paired_count"] == 0
    assert result["unfinished"] == [{"call_id": 1, "state": "pending"},
                                    {"call_id": 2, "state": "pending"}]
    assert result["completeness"]["unpaired_attempts"] == 2
    assert result["completeness"]["pair_state"] == "incomplete"


@pytest.mark.parametrize("field", ["service", "context", "snapshot_sha256"])
def test_identity_mismatch_rejects_and_names_the_field(field):
    entry = attempt(5)
    diff = {
        "service": {"service": service(generation="f" * 64)},
        "context": {"context": context(support_id="other-support")},
        "snapshot_sha256": {"snapshot_sha256": "d" * 64},
    }[field]
    done = terminal(5, **diff)
    with pytest.raises(ScheduledGenerationAuditError, match=field) as caught:
        audit([entry, done])
    assert "pair 5" in str(caught.value)


def test_support_id_difference_rejects_without_creating_case_identity():
    entry, done = paired(7)
    done["context"] = context(support_id="different-support")
    with pytest.raises(ScheduledGenerationAuditError, match="context"):
        audit([entry, done])
    result = audit([entry, paired(7)[1]])
    assert result["pairs"][0]["support_id"] == "mdl-synthetic"
    assert "case_id" not in json.dumps(result)


def test_duplicate_attempt_rejects():
    with pytest.raises(ScheduledGenerationAuditError, match="Duplicate.*attempt"):
        audit([attempt(1), attempt(1)])


def test_duplicate_terminal_rejects():
    entry, done = paired(1)
    with pytest.raises(ScheduledGenerationAuditError, match="Duplicate.*terminal"):
        audit([entry, done, copy.deepcopy(done)])


def test_orphan_terminal_rejects():
    with pytest.raises(ScheduledGenerationAuditError, match="without attempt"):
        audit([terminal(4)])


@pytest.mark.parametrize("key", ["call_id", "service", "context", "snapshot_sha256", "phase", "recorded_at"])
def test_missing_attempt_field_rejects(key):
    row = attempt(1)
    del row[key]
    with pytest.raises(ScheduledGenerationAuditError, match="schema|Invalid"):
        audit([row])


@pytest.mark.parametrize("key", ["status", "error_type"])
def test_missing_terminal_field_rejects(key):
    entry, done = paired(1)
    del done[key]
    with pytest.raises(ScheduledGenerationAuditError, match="schema"):
        audit([entry, done])


def test_extra_unknown_field_rejects():
    with pytest.raises(ScheduledGenerationAuditError, match="schema"):
        audit([attempt(1, case_id="qasper_0001")])
    entry, done = paired(1)
    done["http_status"] = 200
    with pytest.raises(ScheduledGenerationAuditError, match="schema"):
        audit([entry, done])


def test_unknown_model_service_kind_rejects():
    for kind in ("model_service_request_count", "model_service_submission_attempt_v2",
                 "model_service_", "model_service_unknown"):
        with pytest.raises(ScheduledGenerationAuditError, match="Unknown model_service"):
            audit([{"kind": kind, "call_id": 1}])


def test_attempt_phase_must_be_submission():
    with pytest.raises(ScheduledGenerationAuditError, match="phase"):
        audit([attempt(1, phase="future")])


@pytest.mark.parametrize("phase,status,error_type", [
    ("submission", "completed", None),
    ("submission", "cancelled", None),
    ("result", "completed", None),
    ("future", "pending", None),
    ("future", "submission", None),
])
def test_illegal_terminal_phase_status_rejects(phase, status, error_type):
    entry, _ = paired(1)
    done = terminal(1, phase=phase, status=status, error_type=error_type,
                    service=copy.deepcopy(entry["service"]),
                    context=copy.deepcopy(entry["context"]))
    with pytest.raises(ScheduledGenerationAuditError, match="phase|status"):
        audit([entry, done])


def test_error_terminal_requires_only_a_safe_error_type():
    entry, _ = paired(1)
    for status, error_type in [(None, None), ("error", None), ("error", "private exception body"),
                               ("error", "x" * 129), ("completed", "RuntimeError"),
                               ("cancelled", "RuntimeError")]:
        done = terminal(1, status=status, error_type=error_type,
                        service=copy.deepcopy(entry["service"]),
                        context=copy.deepcopy(entry["context"]))
        with pytest.raises(ScheduledGenerationAuditError, match="error_type|status"):
            audit([entry, done])
    done = terminal(1, status="error", error_type="RuntimeError",
                    service=copy.deepcopy(entry["service"]),
                    context=copy.deepcopy(entry["context"]))
    result = audit([entry, done])
    assert result["pairs"][0]["status"] == "error"
    assert "private exception body" not in json.dumps(result)


def test_service_and_context_nested_schema_rejects():
    missing = attempt(1)
    del missing["service"]["generation"]
    with pytest.raises(ScheduledGenerationAuditError, match="service schema"):
        audit([missing])
    extra = attempt(1)
    extra["service"]["endpoint"] = "https://private.invalid/v1"
    with pytest.raises(ScheduledGenerationAuditError, match="service schema"):
        audit([extra])
    bad_context = attempt(1)
    bad_context["context"]["case_id"] = "qasper_0001"
    with pytest.raises(ScheduledGenerationAuditError, match="context schema"):
        audit([bad_context])
    bad_generation = attempt(1)
    bad_generation["service"]["generation"] = "not-a-hash"
    with pytest.raises(ScheduledGenerationAuditError, match="generation"):
        audit([bad_generation])


def test_non_object_rows_and_bad_reader_facts_reject():
    with pytest.raises(ScheduledGenerationAuditError):
        audit(["not an object"])
    with pytest.raises(ScheduledGenerationAuditError):
        audit([attempt(1), "not an object"])
    with pytest.raises(ScheduledGenerationAuditError):
        audit([], present=1)
    with pytest.raises(ScheduledGenerationAuditError):
        audit([], truncated="yes")
    with pytest.raises(ScheduledGenerationAuditError):
        audit([attempt(1)], present=False)


def test_truncated_tail_forces_incomplete_even_for_a_complete_prefix():
    entry, done = paired(1)
    result = audit([entry, done], truncated=True)
    assert result["paired_count"] == 1 and result["unfinished"] == []
    assert result["completeness"]["pair_state"] == "incomplete"
    assert result["completeness"]["truncated_tail"] is True
    assert any("truncated" in warning for warning in result["warnings"])
    assert result["completeness"]["history_exhaustive"] is False


def test_truncated_tail_with_empty_prefix_stays_incomplete():
    result = audit([], truncated=True)
    assert result["observation_state"] == "observed_empty"
    assert result["completeness"]["pair_state"] == "incomplete"
    assert any("truncated" in warning for warning in result["warnings"])


def test_legacy_rows_mixed_with_scheduled_rows_do_not_change_pairing():
    entry, done = paired(1)
    rows = [{"event": "started", "call_id": "legacy-1"}, entry,
            {"event": "completed", "call_id": "legacy-1"}, done]
    result = audit(rows)
    assert result["observation_state"] == "scheduled_observed"
    assert result["legacy_row_count"] == 2
    assert result["pairs"] == [{
        "call_id": 1, "terminal_kind": "returned_future", "status": "completed",
        "workload_id": "ask_answer", "support_id": "mdl-synthetic", "priority": "interactive",
        "service_id": "chat", "generation": "a" * 64, "snapshot_sha256": "c" * 64,
        "attempt_row": 1, "terminal_row": 3}]


def test_output_is_deterministic_serializable_and_has_no_sensitive_projection():
    entry, done = paired(1)
    legacy = {"event": "failed", "call_id": "legacy-1", "case_id": "qasper_0001",
              "raw_client_response": "private answer body", "http_status": 200,
              "job_id": "job-7", "scorer": "deepeval", "endpoint": "https://private.invalid/v1"}
    rows = [legacy, entry, done]
    first = audit(rows)
    assert first == audit(rows)
    assert json.loads(json.dumps(first)) == first

    def keys_of(value):
        found = set()
        if isinstance(value, dict):
            found.update(value)
            for item in value.values():
                found |= keys_of(item)
        elif isinstance(value, list):
            for item in value:
                found |= keys_of(item)
        return found

    keys = keys_of(first)
    assert not ({"case_id", "answer", "response", "raw_client_response", "endpoint",
                 "endpoint_sha256", "credential_variable_name", "http_status", "scorer",
                 "job_id"} & keys)
    text = json.dumps(first)
    for sentinel in ("qasper_0001", "private answer body", "private.invalid", "job-7",
                     "deepeval", "SYNTHETIC_FIXTURE_KEY"):
        assert sentinel not in text
    assert first["pairs"][0]["support_id"] == "mdl-synthetic"
