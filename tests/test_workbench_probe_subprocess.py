"""The adapter exposes fixed bounded helpers, never arbitrary commands."""
import pytest
import os
import time
import json
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor


def test_fixed_helper_has_private_directory_and_actual_waited_exit():
    from rag_eval.workbench.probe_subprocess import ProbeSubprocess
    adapter = ProbeSubprocess()
    assert adapter.directory.parent.as_posix() == '/tmp'
    assert adapter.directory.stat().st_mode & 0o777 == 0o700
    identity = adapter.start('fixture', 'normal')
    observation = adapter.observe('fixture', identity, cancel=False, wait=True)
    assert observation['returncode'] == 0 and observation['identity'] == identity


@pytest.mark.parametrize('mode', [None, [], True, 'shell', ' normal '])
def test_adapter_choices_are_typed_and_never_create_a_process(mode):
    from rag_eval.workbench.probe_subprocess import ProbeSubprocess
    from rag_eval.workbench.evaluation_jobs import JobRejected
    adapter = ProbeSubprocess()
    with pytest.raises(JobRejected):
        adapter.start('fixture', mode)
    assert list(adapter.directory.iterdir()) == []


def test_helper_environment_has_no_application_or_model_environment(monkeypatch):
    from rag_eval.workbench.probe_subprocess import ProbeSubprocess
    monkeypatch.setenv('OPENAI_API_KEY', 'SYNTHETIC-NOT-A-KEY')
    monkeypatch.setenv('PYTHONPATH', '/synthetic/forbidden')
    original = subprocess.Popen
    observed = []
    def inspect(*args, **kwargs):
        observed.append(kwargs['env'])
        assert kwargs['shell'] is False if 'shell' in kwargs else True
        return original(*args, **kwargs)
    monkeypatch.setattr(subprocess, 'Popen', inspect)
    adapter = ProbeSubprocess()
    identity = adapter.start('fixture', 'normal')
    assert adapter.observe('fixture', identity, wait=True)['returncode'] == 0
    assert observed == [{'LANG': 'C.UTF-8'}]


@pytest.mark.parametrize('field,value', [('pid', 1), ('token', 'fake'), ('owner_id', 'fake'),
    ('probe_id', 'fake'), ('helper_hash', '0' * 64)])
def test_false_process_identity_never_sends_a_signal(field, value, monkeypatch):
    from rag_eval.workbench.probe_subprocess import ProbeSubprocess
    adapter = ProbeSubprocess()
    identity = adapter.start('fixture', 'normal')
    def forbidden(*args, **kwargs):
        raise AssertionError('unmatched identity must not signal any PID')
    monkeypatch.setattr(os, 'kill', forbidden)
    assert adapter.observe('fixture', dict(identity, **{field: value}), cancel=True) is None
    assert adapter.observe('fixture', identity, wait=True)['returncode'] == 0


def test_no_signal_after_reap_even_if_saved_pid_is_reused(monkeypatch):
    from rag_eval.workbench.probe_subprocess import ProbeSubprocess
    adapter = ProbeSubprocess()
    identity = adapter.start('fixture', 'normal')
    assert adapter.observe('fixture', identity, wait=True)['returncode'] == 0
    def forbidden(*args, **kwargs):
        raise AssertionError('reaped process identity cannot authorize a signal')
    monkeypatch.setattr(os, 'kill', forbidden)
    assert adapter.observe('fixture', identity, cancel=True)['returncode'] == 0
    barrier = threading.Barrier(2)
    def observe(cancel):
        barrier.wait(timeout=5)
        return adapter.observe('fixture', identity, cancel=cancel, wait=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert all(x['returncode'] == 0 for x in pool.map(observe, [True, False]))


def test_markers_and_ttl_are_not_os_exit_observations():
    from rag_eval.workbench.probe_subprocess import ProbeSubprocess
    adapter = ProbeSubprocess()
    identity = adapter.start('fixture', 'wait_cancel')
    directory = adapter.directory / identity['token']
    (directory / 'exit.json').write_text(json.dumps({'exit_code': 0, 'complete': True}))
    (directory / 'started.json').unlink()
    assert adapter.observe('fixture', identity)['returncode'] is None
    assert adapter.observe('fixture', identity, cancel=True, wait=True)['returncode'] < 0


def test_popen_creation_failure_is_known_but_unrelated_file_error_is_raw(monkeypatch):
    from rag_eval.workbench.probe_subprocess import ProbeSubprocess, ProbeStartFailed
    adapter = ProbeSubprocess()
    error = FileNotFoundError('synthetic process creation failure')
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(subprocess, 'Popen', fail)
    with pytest.raises(ProbeStartFailed) as caught:
        adapter.start('fixture', 'normal')
    assert caught.value.__cause__ is error


def test_owner_and_helper_identity_are_readonly_for_handle_lifetime():
    from rag_eval.workbench.probe_subprocess import ProbeSubprocess
    adapter = ProbeSubprocess()
    for field in ('owner_id', 'helper_hash'):
        with pytest.raises(AttributeError):
            setattr(adapter, field, 'spoofed')


def test_changed_helper_bytes_cannot_launch_under_the_old_hash(monkeypatch):
    from rag_eval.workbench import probe_subprocess
    from rag_eval.workbench.evaluation_jobs import JobRejected
    adapter = probe_subprocess.ProbeSubprocess()
    monkeypatch.setattr(probe_subprocess, '_HELPER', "raise RuntimeError('altered helper')")
    with pytest.raises(JobRejected):
        adapter.start('fixture', 'normal')
    assert list(adapter.directory.iterdir()) == []


def test_externally_reaped_status_is_unavailable_even_when_cancelled(monkeypatch):
    from rag_eval.workbench.probe_subprocess import ProbeSubprocess
    adapter = ProbeSubprocess()
    identity = adapter.start('fixture', 'nonzero')
    pid, status = os.waitpid(identity['pid'], 0)
    assert pid == identity['pid'] and os.waitstatus_to_exitcode(status) == 7
    def forbidden(*args, **kwargs):
        raise AssertionError('lost status cannot authorize PID signalling')
    monkeypatch.setattr(os, 'kill', forbidden)
    assert adapter.observe('fixture', identity, cancel=True, wait=True) is None
    assert adapter.observe('fixture', identity, cancel=True) is None


def test_external_reap_between_live_observation_and_cancel_cannot_signal_pid(monkeypatch):
    from rag_eval.workbench.probe_subprocess import ProbeSubprocess
    adapter = ProbeSubprocess()
    identity = adapter.start('fixture', 'nonzero')
    original = os.waitpid
    reaped = []
    def competing_reaper(pid, flags):
        result = original(pid, flags)
        if pid == identity['pid'] and result[0] == 0 and not reaped:
            child, status = original(pid, 0)
            assert child == pid
            reaped.append(os.waitstatus_to_exitcode(status))
        return result
    def forbidden(*args, **kwargs):
        raise AssertionError('a live poll does not authorize later PID signalling')
    monkeypatch.setattr(os, 'waitpid', competing_reaper)
    monkeypatch.setattr(os, 'kill', forbidden)
    try:
        assert adapter.observe('fixture', identity, cancel=True, wait=True) is None
        assert reaped == [7]
    finally:
        if not reaped:
            original(identity['pid'], 0)


@pytest.mark.parametrize('mode,code', [('normal', 0), ('nonzero', 7)])
def test_verified_status_is_cached_without_popen_status_or_signal_methods(mode, code, monkeypatch):
    from rag_eval.workbench.probe_subprocess import ProbeSubprocess
    adapter = ProbeSubprocess()
    identity = adapter.start('fixture', mode)
    def forbidden(*args, **kwargs):
        raise AssertionError('Popen may fabricate exit zero or signal a reused PID')
    for method in ('poll', 'wait', 'terminate'):
        monkeypatch.setattr(subprocess.Popen, method, forbidden)
    assert adapter.observe('fixture', identity, wait=True)['returncode'] == code
    assert adapter.observe('fixture', identity, cancel=True, wait=True)['returncode'] == code


@pytest.mark.parametrize('error_type', [ValueError, FileNotFoundError, ChildProcessError])
def test_os_observation_internal_failure_is_the_original_object(error_type, monkeypatch):
    from rag_eval.workbench.probe_subprocess import ProbeSubprocess
    adapter = ProbeSubprocess()
    identity = adapter.start('fixture', 'normal')
    error = error_type('synthetic waitpid implementation failure')
    original = os.waitpid
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(os, 'waitpid', fail)
    try:
        with pytest.raises(error_type) as caught:
            adapter.observe('fixture', identity, wait=True)
        assert caught.value is error
    finally:
        monkeypatch.setattr(os, 'waitpid', original)
        adapter.observe('fixture', identity, wait=True)
