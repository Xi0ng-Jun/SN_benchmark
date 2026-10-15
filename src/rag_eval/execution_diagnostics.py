"""Private, sanitized failure evidence shared by runners and supervisors.

Only exception messages and frame locations are recorded: never frame locals,
source lines, exception notes, request objects, or configuration dictionaries.
Callers supply known private strings when an exception can echo input values.
"""
from __future__ import annotations

import os
import json
from pathlib import Path
import re
import tempfile
import tomllib


_CREDENTIAL = r'authorization|api[_-]?key|access[_-]?token|token|password|secret|credential'


def sensitive_strings(value):
    """Extract string values for redaction without serializing their containers."""
    if isinstance(value, str):
        return (value,) if value else ()
    if isinstance(value, dict):
        return tuple(part for item in value.values() for part in sensitive_strings(item))
    if isinstance(value, (list, tuple)):
        return tuple(part for item in value for part in sensitive_strings(item))
    return ()


def config_sensitive_values(path):
    """Known config values are redaction inputs, never diagnostic artifacts."""
    if path is None:
        return ()
    try:
        text = Path(path).read_text(encoding='utf-8')
    except (OSError, UnicodeError):
        return ()
    try:
        parsed = json.loads(text) if Path(path).suffix == '.json' else tomllib.loads(text)
    except ValueError:
        # Invalid config can fail before parsing; still remove quoted values.
        return (text, *re.findall(r'[\'\"]([^\'\"\n]+)[\'\"]', text))
    return (text, *sensitive_strings(parsed))


def sanitize_diagnostic(value, *, sensitive_values=()):
    """Scrub addresses, credentials and known input values before persistence."""
    value = str(value).encode('utf-8', errors='backslashreplace').decode('utf-8')
    private = {item for item in sensitive_values if isinstance(item, str) and item}
    private.update(v for k, v in os.environ.items()
                   if v and re.search(_CREDENTIAL, k, re.IGNORECASE))
    # Exception messages commonly contain repr(dict) or JSON, which escape
    # newlines, backslashes and Unicode. Match those representations as well
    # as literal input; never decode or reinterpret untrusted error text.
    representations = set()
    for item in private:
        representations.update((item, repr(item)[1:-1], ascii(item)[1:-1],
                                json.dumps(item, ensure_ascii=False)[1:-1],
                                json.dumps(item, ensure_ascii=True)[1:-1]))
    representations = {item.encode('utf-8', errors='backslashreplace').decode('utf-8')
                       for item in representations}
    for item in sorted(representations, key=len, reverse=True):
        if len(item) < 4:
            value = re.sub(r'(?<!\w)' + re.escape(item) + r'(?!\w)', '[redacted-value]', value)
        else:
            value = value.replace(item, '[redacted-value]')
    value = re.sub(r'(?i)\b[a-z][a-z0-9+.-]*://[^\s\'\"<>]+', '[redacted-address]', value)
    value = re.sub(r'(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?', '[redacted-address]', value)
    value = re.sub(r'(?i)\b(?:[a-z0-9-]+\.)+[a-z][a-z0-9-]*:\d+\b', '[redacted-address]', value)
    value = re.sub(r'(?im)\b(?:' + _CREDENTIAL + r')\b[^\n]*', '[redacted-credential]', value)
    value = re.sub(r'(?im)\b(?:prompt|gold|request_body|response_body|config(?:uration)?)'
                   r'\s*[=:][^\n]*', '[redacted-content]', value)
    # A child may emit Python's default traceback, which includes source text.
    # Keep its frame headers and exception messages, discard indented code/locals.
    lines, in_traceback = [], False
    for line in value.splitlines(keepends=True):
        if 'Traceback (most recent call last)' in line:
            in_traceback = True
        if in_traceback and line.startswith(('    ', '\t')):
            continue
        lines.append(line)
    return ''.join(lines)


def _message(exc):
    try:
        return str(exc)
    except Exception:
        return '[exception message unavailable]'


def _traceback_text(exc, sensitive_values, seen=None):
    seen = set() if seen is None else seen
    if id(exc) in seen:
        return ''
    seen.add(id(exc))
    text = ''
    if exc.__cause__ is not None:
        text += _traceback_text(exc.__cause__, sensitive_values, seen)
        text += '\nThe above exception was the direct cause of the following exception:\n\n'
    elif exc.__context__ is not None and not exc.__suppress_context__:
        text += _traceback_text(exc.__context__, sensitive_values, seen)
        text += '\nDuring handling of the above exception, another exception occurred:\n\n'
    text += 'Traceback (most recent call last):\n'
    frame = exc.__traceback__
    while frame is not None:
        code = frame.tb_frame.f_code
        text += f'  File "{code.co_filename}", line {frame.tb_lineno}, in {code.co_name}\n'
        frame = frame.tb_next
    text += type(exc).__name__ + ': ' + _message(exc) + '\n'
    if isinstance(exc, BaseExceptionGroup):
        for index, child in enumerate(exc.exceptions, 1):
            text += f'\nContained exception {index}:\n' + _traceback_text(child, sensitive_values, seen)
    return sanitize_diagnostic(text, sensitive_values=sensitive_values)


def persist_stderr(text, *, directory, sensitive_values=()):
    """Write complete sanitized stderr to a new private file in an owned directory."""
    text = sanitize_diagnostic(text, sensitive_values=sensitive_values)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=directory,
                                     prefix='stderr-', suffix='.log', delete=False) as stream:
        stream.write(text)
        return Path(stream.name).resolve()


def exception_details(exc, *, phase, directory=None, sensitive_values=()):
    """Save a traceback and return bounded state fields, preserving the exception.

    ``directory`` must already exist and belong to this invocation. With no
    directory, use a private unique sidecar in the OS temporary directory; this
    keeps failed preflight checks from creating or altering a requested run.
    Diagnostic write errors are returned as facts and never replace ``exc``.
    """
    sensitive_values = tuple(sensitive_values)
    details = dict(error_type=type(exc).__name__,
                   error_message=sanitize_diagnostic(_message(exc), sensitive_values=sensitive_values)[:2048],
                   failed_phase=phase, diagnostic_path=None)
    try:
        if directory is None:
            directory = tempfile.mkdtemp(prefix='sn-eval-diagnostics-')
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=directory,
                                         prefix='failure-', suffix='.log', delete=False) as stream:
            stream.write('Failure phase: ' + str(phase) + '\n\n')
            stream.write(_traceback_text(exc, sensitive_values))
            details['diagnostic_path'] = str(Path(stream.name).resolve())
    except OSError as write_error:
        details['diagnostic_write_error'] = type(write_error).__name__
    try:
        exc._rag_eval_diagnostic_details = details
    except (AttributeError, TypeError):
        pass
    return details


def failure_summary(details):
    """A short stderr line linking the full saved failure evidence."""
    message = ' '.join(details.get('error_message', '').split())[:600]
    location = details.get('diagnostic_path') or 'unavailable'
    return (f"{details['error_type']} during {details['failed_phase']}: {message}; "
            f'diagnostic: {location}')
