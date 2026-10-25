"""The single-partition CLI passes captured startup inputs through; readiness receipts are retired."""
import importlib.util
import json
from pathlib import Path
import sys

import pytest

from test_prepared_seed_retry import fake_sn  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]


def _script():
    spec = importlib.util.spec_from_file_location('tested_notebook_cli', ROOT / 'scripts' / 'run_notebook_benchmarks.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def captured_argv(tmp_path):
    ref = dict(kind='sn', id='a' * 64, manifest_sha256='b' * 64)
    (tmp_path / 'source-ref.json').write_text(json.dumps(ref))
    (tmp_path / 'expectation.json').write_text(json.dumps({'executable': sys.executable, 'prefix': sys.prefix}))
    (tmp_path / 'requirements.txt').write_text('pydantic>=2\n')
    return ref, ['run_notebook_benchmarks.py', '--artifact-root', str(tmp_path / 'store'),
                 '--artifact-index-id', 'c' * 64, '--bundle', str(tmp_path / 'bundle'),
                 '--partition-id', 'part', '--mode', 'chunk', '--run-dir', str(tmp_path / 'run'),
                 '--project-root', str(tmp_path / 'product'), '--case-id', 'qasper:q',
                 '--sn-source-ref', str(tmp_path / 'source-ref.json'),
                 '--dependency-requirements', str(tmp_path / 'requirements.txt'),
                 '--runtime-expectation', str(tmp_path / 'expectation.json')]


def test_captured_launch_reaches_the_runner_with_its_inputs(tmp_path, monkeypatch, capsys):
    from rag_eval import notebook_runner
    ref, argv = captured_argv(tmp_path)
    calls = []
    monkeypatch.setattr(notebook_runner, 'execute', lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr(sys, 'argv', argv)
    assert _script().main() == 0
    [call] = calls
    assert call['sn_source_ref'] == ref and call['case_ids'] == ['qasper:q']
    assert call['request_revision'] == 'notebook-request-v3'
    assert call['dependency_requirements'] == (tmp_path / 'requirements.txt').resolve()
    assert capsys.readouterr().err == ''


def test_readiness_receipt_option_is_gone(tmp_path, monkeypatch):
    _, argv = captured_argv(tmp_path)
    monkeypatch.setattr(sys, 'argv', argv + ['--readiness-receipt', str(tmp_path / 'receipt.json')])
    with pytest.raises(SystemExit) as exited:
        _script().main()
    assert exited.value.code == 2


def test_one_case_id_parser_serves_preflight_and_execution(tmp_path):
    from rag_eval.notebook_runner import load_case_ids_file
    path = tmp_path / 'ids.txt'
    path.write_text('qasper:a\nqasper:b\n')
    preflight = _campaign_script()
    assert load_case_ids_file(path) == preflight._ids(path) == ['qasper:a', 'qasper:b']
    # Preflight used to skip comments and blank lines the runner then rejected.
    for text, message in [('# selection\nqasper:a\n', 'whitespace'), ('qasper:a\n\nqasper:b\n', 'blank'),
                          (' qasper:a\n', 'whitespace'), ('qasper:a\nqasper:a\n', 'duplicate'),
                          ('', 'at least one')]:
        path.write_text(text)
        with pytest.raises(ValueError, match=message):
            load_case_ids_file(path)
        with pytest.raises(ValueError, match=message):
            preflight._ids(path)


def _campaign_script():
    spec = importlib.util.spec_from_file_location('tested_campaign', ROOT / 'scripts' / 'validate_external_campaign.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_execute_defaults_to_the_gold_free_request(fake_sn):
    run = fake_sn.run('default-request')
    manifest = json.loads((run / 'manifest.json').read_text())
    assert manifest['identity']['notebook_context']['request_revision'] == 'notebook-request-v3'
