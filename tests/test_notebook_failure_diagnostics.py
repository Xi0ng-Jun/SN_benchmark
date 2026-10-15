"""Offline runner/CLI regressions for failures before and after run ownership."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import ModuleType, SimpleNamespace

import pytest

from rag_eval.notebook_runner import execute
from test_notebook_execution import bundle_fixture

ROOT = Path(__file__).resolve().parents[1]


def arguments(tmp_path):
    return dict(root=ROOT, project=tmp_path / 'product', bundle_dir=tmp_path / 'bundle',
                run=tmp_path / 'run', mode='chunk', partition_id=bundle_fixture(tmp_path))


def test_initialization_failure_retains_sanitized_message_and_traceback(tmp_path, monkeypatch):
    from rag_eval import runtime_environment
    args = arguments(tmp_path)
    config = tmp_path / 'models.toml'
    config.write_text('model = "private-model-value"\n')
    def fail(*a, **k):
        raise ImportError('missing adapter private-model-value https://private.invalid; api_key=hidden')
    monkeypatch.setattr(runtime_environment, 'snapshot_sources', fail)
    with pytest.raises(ImportError):
        execute(**args, model_config=config)
    state = json.loads((args['run'] / 'state.json').read_text())
    assert state['phase'] == 'failed' and state['failed_phase'] == 'initializing'
    assert 'missing adapter' in state['error_message']
    saved = Path(state['diagnostic_path']).read_text()
    assert 'ImportError' in saved and 'in fail' in saved
    for value in ('private-model-value', 'private.invalid', 'hidden'):
        assert value not in saved + json.dumps(state)
    assert not (args['run'] / 'manifest.json').exists()


def test_product_import_failure_records_importing_phase(tmp_path, monkeypatch):
    from rag_eval import runtime_environment
    args = arguments(tmp_path)
    settings = SimpleNamespace(db_path=args['run'] / 'runtime/database.db')
    monkeypatch.setattr(runtime_environment, 'snapshot_sources', lambda *a: {'revision': 'test'})
    monkeypatch.setattr(runtime_environment, 'configure_environment', lambda *a, **k: (
        settings, dict(comparable_settings_sha256='settings', service_config_sha256='services')))
    module = ModuleType('app.services.sqlite_repository')
    module.SQLiteRepository = lambda s: SimpleNamespace(db_path=s.db_path, close=lambda: None)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    # Exercise the real Python import boundary before any model can be used.
    monkeypatch.setitem(sys.modules, 'app.core.llm_logging', None)
    with pytest.raises(ModuleNotFoundError):
        execute(**args)
    state = json.loads((args['run'] / 'state.json').read_text())
    assert state['failed_phase'] == 'importing'
    assert 'app.core.llm_logging' in state['error_message']
    assert 'predictions' in Path(state['diagnostic_path']).read_text()


def test_initial_state_write_failure_still_retains_original_diagnostics(tmp_path, monkeypatch):
    from rag_eval import notebook_runner
    args = arguments(tmp_path)
    update = notebook_runner.update_state
    def fail_initial(run, phase, **fields):
        if phase == 'initializing':
            raise OSError('initial state unavailable')
        return update(run, phase, **fields)
    monkeypatch.setattr(notebook_runner, 'update_state', fail_initial)
    with pytest.raises(OSError) as caught:
        execute(**args)
    details = caught.value._rag_eval_diagnostic_details
    assert details['failed_phase'] == 'initializing'
    assert 'initial state unavailable' in Path(details['diagnostic_path']).read_text()
    state = json.loads((args['run'] / 'state.json').read_text())
    assert state['phase'] == 'failed' and state['error_message'] == 'initial state unavailable'


@pytest.mark.parametrize('cancelled', [False, True])
def test_workbench_cli_emits_sanitized_diagnostic_and_preserves_cancellation(tmp_path, monkeypatch, capsys, cancelled):
    path = ROOT / 'scripts/run_workbench_notebook.py'
    spec = importlib.util.spec_from_file_location('tested_workbench_failure_cli', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    def fail(payload):
        if cancelled:
            raise KeyboardInterrupt('user cancelled')
        raise ValueError('runtime input changed https://private.invalid; password=hidden')
    monkeypatch.setattr(module, 'execute', fail)
    monkeypatch.setattr(sys, 'argv', [str(path), '--payload', json.dumps({'run_dir': str(tmp_path / 'run')})])
    assert module.main() == (130 if cancelled else 2)
    stderr = capsys.readouterr().err
    assert 'user cancelled' in stderr if cancelled else 'runtime input changed' in stderr
    assert 'private.invalid' not in stderr and 'hidden' not in stderr
    diagnostic = Path(stderr.strip().split('diagnostic: ', 1)[1])
    assert diagnostic.is_file() and not (tmp_path / 'run').exists()


def test_partition_cli_recognizes_native_cancellation(tmp_path, monkeypatch, capsys):
    from rag_eval import notebook_runner
    path = ROOT / 'scripts/run_notebook_benchmarks.py'
    spec = importlib.util.spec_from_file_location('tested_partition_failure_cli', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    class AskCancelled(Exception):
        pass
    def cancelled(**kwargs):
        raise AskCancelled('stopped by caller')
    monkeypatch.setattr(notebook_runner, 'execute', cancelled)
    monkeypatch.setattr(sys, 'argv', [str(path), '--bundle', str(tmp_path / 'bundle'),
        '--partition-id', 'p', '--mode', 'chunk', '--run-dir', str(tmp_path / 'run'),
        '--project-root', str(tmp_path / 'product')])
    assert module.main() == 130
    assert 'stopped by caller' in capsys.readouterr().err
    assert not (tmp_path / 'run').exists()


@pytest.mark.parametrize('existing', [False, True])
def test_cli_preflight_error_keeps_run_untouched_and_prints_sidecar_location(tmp_path, existing):
    run = tmp_path / 'run'
    if existing:
        run.mkdir()
        (run / 'state.json').write_text('{"phase":"finished","marker":"keep"}\n')
    before = {str(p.relative_to(run)): p.read_bytes() for p in run.rglob('*') if p.is_file()} if existing else {}
    child = subprocess.run([sys.executable, str(ROOT / 'scripts/run_notebook_benchmarks.py'),
        '--bundle', str(tmp_path / 'missing-bundle'), '--partition-id', 'missing', '--mode', 'chunk',
        '--run-dir', str(run), '--project-root', str(tmp_path / 'product')], capture_output=True, text=True)
    assert child.returncode == 2
    assert 'diagnostic: ' in child.stderr
    diagnostic = Path(child.stderr.strip().split('diagnostic: ', 1)[1])
    assert diagnostic.is_file() and 'Traceback' in diagnostic.read_text()
    if existing:
        assert {str(p.relative_to(run)): p.read_bytes() for p in run.rglob('*') if p.is_file()} == before
        assert 'new run directory' in child.stderr
    else:
        assert not run.exists()
        assert 'FileNotFoundError' in child.stderr
