"""Local check probes interpreter identity without requiring execution setup."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import configure_workbench_server as setup


@pytest.mark.parametrize('mismatch', [None, 'executable', 'prefix'])
def test_check_interpreter_without_execution(tmp_path, capsys, mismatch):
    expected = dict(executable=sys.executable, prefix=sys.prefix)
    if mismatch:
        expected[mismatch] = str(tmp_path / ('other-' + mismatch))
    expectation = tmp_path / 'runtime.json'
    expectation.write_text(json.dumps(expected))
    config = tmp_path / 'config.json'
    config.write_text(json.dumps(dict(workspace=dict(location=str(setup.ROOT)),
        job_execution=dict(python=sys.executable, runtime_expectation=str(expectation)))))
    args = SimpleNamespace(config=config, execution=False)
    if mismatch:
        with pytest.raises(ValueError) as error:
            setup.check(args)
        assert expected[mismatch] in str(error.value)
        assert (sys.executable if mismatch == 'executable' else sys.prefix) in str(error.value)
        assert 'expected executable=' in str(error.value) and 'selected executable=' in str(error.value)
    else:
        setup.check(args)
        assert json.loads(capsys.readouterr().out)['execution_files_checked'] is False


def test_check_without_execution_configuration(tmp_path, capsys):
    config = tmp_path / 'config.json'
    config.write_text(json.dumps(dict(workspace=dict(location=str(setup.ROOT)))))
    setup.check(SimpleNamespace(config=config, execution=False))
    assert json.loads(capsys.readouterr().out)['status'] == 'local_files_checked'


def test_partial_config_resolves_relative_paths_and_preserves_invocation_symlink(tmp_path, monkeypatch):
    import os
    invocation = tmp_path / 'venv/bin/python'
    invocation.parent.mkdir(parents=True)
    invocation.symlink_to(sys.executable)
    expectation = tmp_path / 'runtime.json'
    expectation.write_text(json.dumps(dict(executable=str(invocation), prefix=sys.prefix)))
    config = tmp_path / 'config.json'
    config.write_text(json.dumps(dict(workspace=dict(location=str(setup.ROOT)),
        job_execution=dict(python='venv/bin/python', runtime_expectation='runtime.json'))))
    observed = []
    def probe(argv, **kwargs):
        observed.append(argv[0])
        return SimpleNamespace(stdout=json.dumps(dict(executable=str(invocation), prefix=sys.prefix)))
    monkeypatch.setattr(setup.subprocess, 'run', probe)
    setup.check(SimpleNamespace(config=config, execution=False))
    assert observed == [os.path.abspath(invocation)]
    assert observed[0] != str(invocation.resolve())


@pytest.mark.parametrize('error', [OSError('cannot execute'), setup.subprocess.CalledProcessError(1, ['python']),
                                 setup.subprocess.TimeoutExpired(['python'], 15)])
def test_failed_probe_has_actionable_local_error(tmp_path, monkeypatch, error):
    expectation = tmp_path / 'runtime.json'
    expectation.write_text(json.dumps(dict(executable=sys.executable, prefix=sys.prefix)))
    config = tmp_path / 'config.json'
    config.write_text(json.dumps(dict(workspace=dict(location=str(setup.ROOT)),
        job_execution=dict(python=sys.executable, runtime_expectation=str(expectation)))))
    def probe(*a, **kw):
        raise error
    monkeypatch.setattr(setup.subprocess, 'run', probe)
    with pytest.raises(ValueError, match='Cannot probe job_execution.python'):
        setup.check(SimpleNamespace(config=config, execution=False))
