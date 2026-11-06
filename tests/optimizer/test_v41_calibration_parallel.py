"""Bounded calibration scheduling; synthetic work, no models or servers."""
import dataclasses
import hashlib
import json
import threading

import pytest

from rag_eval.optimizer.v4 import calibration_session as session, decide
from .test_v41_calibration_session import calibration_env, env, eight_papers  # noqa: F401


def run(rig, concurrency=2):
    service, tree, adapter, root = rig
    return session.run_calibration(adapter, adapter.seed, service, tree['id'], 'alice', root,
                                   dict(time_budget_hours=2, calibration_k=3), concurrency=concurrency)


def test_probe_and_diagnosis_overlap_and_growth_only_measures_new_cases(calibration_env, monkeypatch):
    _, _, adapter, _ = calibration_env
    barrier = threading.Barrier(2, timeout=3)
    subset, search = adapter.evaluate_subset, adapter.evaluate_search
    diagnosis_calls, probe_k = [], []

    def probe(version, reference, kind, ids, k):
        if kind == 'probe':
            probe_k.append(k)
            if k == 1:
                barrier.wait()
        return subset(version, reference, kind, ids, k)

    def diagnosis(version, ids, k):
        if not diagnosis_calls:
            barrier.wait()
        diagnosis_calls.append(set(ids))
        return search(version, ids, k)

    monkeypatch.setattr(adapter, 'evaluate_subset', probe)
    monkeypatch.setattr(adapter, 'evaluate_search', diagnosis)
    bundle = run(calibration_env, 3)
    assert probe_k == [1, 3]  # warm roots are submitted together, never concurrent cumulative requests
    assert len(diagnosis_calls) == 2
    assert diagnosis_calls[0].isdisjoint(diagnosis_calls[1])
    assert set.union(*diagnosis_calls) == set(bundle['diagnosis'])
    assert {len(v) for v in decide.attempts_by_task(
        session.EvalResult(**bundle['results']['probe'])).values()} == {3}
    before = dict(adapter.calls)
    assert run(calibration_env, 3) == bundle
    assert adapter.calls == before
    with pytest.raises(ValueError, match='concurrency'):
        run(calibration_env, 2)


def test_failed_d_growth_keeps_completed_stages_and_resumes_only_missing_batch(calibration_env, monkeypatch):
    _, _, adapter, root = calibration_env
    search = adapter.evaluate_search
    calls = []

    def fail_growth(version, ids, k):
        calls.append(set(ids))
        if len(calls) == 2:
            raise RuntimeError('synthetic growth interruption')
        return search(version, ids, k)

    monkeypatch.setattr(adapter, 'evaluate_search', fail_growth)
    with pytest.raises(RuntimeError, match='growth interruption'):
        run(calibration_env)
    saved = session._load(root / session.CALIBRATION_FILE)
    assert 'probe' in saved['results']
    assert saved['schedule']['diagnosis_batches'][0]['result']
    probe_calls = adapter.calls['subset:probe']
    resumed = []
    monkeypatch.setattr(adapter, 'evaluate_search', lambda v, ids, k: (resumed.append(set(ids)), search(v, ids, k))[1])
    bundle = run(calibration_env)
    assert resumed == [calls[1]]
    assert adapter.calls['subset:probe'] == probe_calls
    assert set(bundle['results']['diagnosis']['per_task_scores']) == set.union(*calls)


def test_serial_option_never_overlaps_and_legacy_resume_preserves_identity(calibration_env, monkeypatch):
    _, _, adapter, root = calibration_env
    active = threading.Lock()
    original = adapter.evaluate_subset

    def exclusive(*args, **kwargs):
        assert active.acquire(blocking=False)
        try:
            return original(*args, **kwargs)
        finally:
            active.release()

    monkeypatch.setattr(adapter, 'evaluate_subset', exclusive)
    bundle = run(calibration_env, 1)
    assert bundle['schedule']['concurrency'] == 1
    # Old deliveries had no schedule. Preserve their original identity and results.
    legacy = dict(bundle)
    legacy.pop('schedule')
    legacy = session._save(root / session.CALIBRATION_FILE, legacy)
    before = dict(adapter.calls)
    assert session.calibration_concurrency(root, 2) == 1
    assert run(calibration_env) == legacy
    assert adapter.calls == before


def test_calibration_option_does_not_change_legacy_optimization_digest(tmp_path):
    from rag_eval.optimizer.v4.config import V4Config, digest, load
    cfg = V4Config(time_budget_hours=2)
    legacy = dataclasses.asdict(cfg)
    assert digest(cfg) == hashlib.sha256(json.dumps(legacy, sort_keys=True).encode()).hexdigest()
    path = tmp_path / 'config.json'
    path.write_text(json.dumps(dict(time_budget_hours=2, calibration_concurrency=4)))
    loaded = load(path)
    assert loaded.calibration_concurrency == 4
    assert digest(loaded.config) == digest(cfg)


def test_crash_after_registering_growth_replays_without_shrinking_d(calibration_env, monkeypatch):
    service, _, adapter, root = calibration_env
    register = service.register_calibration_samples
    before = service.iteration(calibration_env[1]['id'])['protocol']['splits']['diagnosis']
    def crash_after_growth(iid, probe, diagnosis, **kwargs):
        result = register(iid, probe, diagnosis, **kwargs)
        if len(diagnosis) > len(before):
            raise RuntimeError('crash after registration')
        return result
    monkeypatch.setattr(service, 'register_calibration_samples', crash_after_growth)
    with pytest.raises(RuntimeError, match='after registration'):
        run(calibration_env)
    calls = dict(adapter.calls)
    monkeypatch.setattr(service, 'register_calibration_samples', register)
    bundle = run(calibration_env)
    assert len(bundle['diagnosis']) > len(before)
    assert adapter.calls['subset:probe'] == calls['subset:probe']


@pytest.mark.parametrize('physical_stop', [False, True])
def test_failure_wakes_sibling_before_joining(calibration_env, monkeypatch, physical_stop):
    from rag_eval.optimizer.adapter import EvaluationCancelled
    from rag_eval.optimizer.v4.loop import RunStopRequested
    _, _, adapter, _ = calibration_env
    entered, wake = threading.Event(), threading.Event()
    stopped = []
    def probe(*args, **kwargs):
        entered.set()
        assert wake.wait(3), 'coordinator joined before stopping sibling poller'
        raise EvaluationCancelled('sibling stopped')
    def diagnosis(*args, **kwargs):
        assert entered.wait(3)
        if physical_stop:
            raise RunStopRequested()
        raise RuntimeError('local interruption')
    monkeypatch.setattr(adapter, 'evaluate_subset', probe)
    monkeypatch.setattr(adapter, 'evaluate_search', diagnosis)
    monkeypatch.setattr(adapter, 'cancel_pending', lambda: (stopped.append('cancel'), wake.set()))
    monkeypatch.setattr(adapter, 'stop_polling', lambda: (stopped.append('polling'), wake.set()))
    with pytest.raises(RunStopRequested if physical_stop else RuntimeError):
        run(calibration_env)
    assert stopped == ['cancel' if physical_stop else 'polling']
