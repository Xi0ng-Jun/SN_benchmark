"""Failures retain actionable evidence without exporting request/config contents."""
import json
from pathlib import Path

import pytest


def test_exception_chain_retains_frames_messages_but_never_source_locals_or_notes(tmp_path):
    from rag_eval.execution_diagnostics import exception_details
    private_local = 'LOCAL-ONLY-CONTENT'
    try:
        try:
            raise ImportError('missing optional adapter https://private.invalid/v1?key=abc')
        except ImportError as cause:
            error = RuntimeError('initialization failed; token=token-value')
            error.add_note('PRIVATE-NOTE')
            raise error from cause
    except RuntimeError as exc:
        details = exception_details(exc, phase='initializing', directory=tmp_path)
    assert details['error_type'] == 'RuntimeError'
    assert details['failed_phase'] == 'initializing'
    assert 'initialization failed' in details['error_message']
    path = Path(details['diagnostic_path'])
    saved = path.read_text()
    assert 'ImportError: missing optional adapter' in saved
    assert 'RuntimeError: initialization failed' in saved
    assert 'test_exception_chain_retains_frames_messages' in saved
    assert 'direct cause' in saved and 'line ' in saved
    for value in ('private.invalid', 'token-value', private_local, 'PRIVATE-NOTE', 'raise ImportError'):
        assert value not in saved
    assert path.stat().st_mode & 0o777 == 0o600


def test_known_private_values_and_bare_service_addresses_are_scrubbed(tmp_path, monkeypatch):
    from rag_eval.execution_diagnostics import exception_details, sanitize_diagnostic
    monkeypatch.setenv('MODEL_API_KEY', 'unlabelled-key-value')
    exc = ValueError('bad prompt SECRET-QUESTION; gold SECRET-GOLD; config PRIVATE-MODEL; '
                     'unlabelled-key-value; connect 192.168.3.4:8080 or model.private.internal:9000')
    details = exception_details(exc, phase='importing', directory=tmp_path,
                                sensitive_values=('SECRET-QUESTION', 'SECRET-GOLD', 'PRIVATE-MODEL'))
    text = json.dumps(details) + Path(details['diagnostic_path']).read_text()
    for secret in ('SECRET-QUESTION', 'SECRET-GOLD', 'PRIVATE-MODEL', 'unlabelled-key-value',
                   '192.168.3.4', 'model.private.internal'):
        assert secret not in text
    assert 'bad' in details['error_message']
    assert 'safe failure' in sanitize_diagnostic('safe failure\nAuthorization: Bearer abc')


def test_sidecar_does_not_initialize_run_or_overwrite_an_existing_artifact(tmp_path):
    from rag_eval.execution_diagnostics import exception_details
    run = tmp_path / 'must-stay-absent'
    first = exception_details(ValueError('invalid selection'), phase='validation')
    second = exception_details(ValueError('invalid selection'), phase='validation')
    assert not run.exists()
    assert first['diagnostic_path'] != second['diagnostic_path']
    assert Path(first['diagnostic_path']).is_file()
    assert Path(second['diagnostic_path']).is_file()


def test_diagnostic_write_failure_keeps_primary_message(tmp_path):
    from rag_eval.execution_diagnostics import exception_details
    details = exception_details(ValueError('primary initialization failure'), phase='initializing',
                                directory=tmp_path / 'missing')
    assert details['error_type'] == 'ValueError'
    assert details['error_message'] == 'primary initialization failure'
    assert details['diagnostic_path'] is None
    assert details['diagnostic_write_error'] == 'FileNotFoundError'


def test_invalid_unicode_message_cannot_replace_primary_failure(tmp_path):
    from rag_eval.execution_diagnostics import exception_details
    details = exception_details(RuntimeError('invalid provider bytes \udcff'), phase='asking', directory=tmp_path)
    assert details['error_type'] == 'RuntimeError'
    assert 'invalid provider bytes' in details['error_message']
    assert 'RuntimeError' in Path(details['diagnostic_path']).read_text()


@pytest.mark.parametrize('encoding', ['repr', 'json', 'json-ascii'])
def test_escaped_private_values_are_redacted_from_message_and_traceback(tmp_path, monkeypatch, encoding):
    from rag_eval.execution_diagnostics import exception_details
    question = 'PRIVATE-QUESTION-LINE1\nPRIVATE-QUESTION-LINE2 秘密 \\ path "quoted"'
    credential = 'ENV-VALUE-LINE1\nSECOND-LINE 私密'
    monkeypatch.setenv('MODEL_API_KEY', credential)
    payload = {'question': question, 'value': credential}
    message = (repr(payload) if encoding == 'repr' else
               json.dumps(payload, ensure_ascii=encoding == 'json-ascii'))
    details = exception_details(ValueError(message), phase='asking', directory=tmp_path,
                                sensitive_values=(question,))
    saved = Path(details['diagnostic_path']).read_text() + details['error_message']
    assert 'ValueError' in saved and '[redacted-value]' in saved
    for fragment in ('PRIVATE-QUESTION', 'ENV-VALUE-LINE1', 'SECOND-LINE', '秘密', '私密',
                     '\\u79d8\\u5bc6', '\\u79c1\\u5bc6'):
        assert fragment not in saved
