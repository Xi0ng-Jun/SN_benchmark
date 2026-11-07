"""Recovery keeps the original calibration identity and extends only new executions."""
import pytest

from rag_eval.optimizer.v4 import calibration_session as session
from .test_v41_calibration_session import calibration_env, env, eight_papers, measure  # noqa: F401


def test_recovery_setting_is_explicit_durable_and_bound_to_original_identity(calibration_env):
    from rag_eval.optimizer.v4.calibration_recovery import configure
    _, _, _, root = calibration_env
    with pytest.raises(ValueError):
        configure(root, 14400)
    bundle = measure(calibration_env)
    bundle['complete'] = False
    session._save(root / session.CALIBRATION_FILE, bundle)
    before = (root / session.CALIBRATION_FILE).read_bytes()
    assert configure(root, 14400) == 14400
    assert configure(root, None) == 14400
    assert (root / session.CALIBRATION_FILE).read_bytes() == before
    with pytest.raises(ValueError):
        configure(root, 18000)
    bundle['identity']['actor'] = 'different-operator'
    session._save(root / session.CALIBRATION_FILE, bundle)
    with pytest.raises(ValueError):
        configure(root, None)


@pytest.mark.parametrize('seconds', [0, 3600, 86401, True, 10000.5])
def test_invalid_recovery_budget_refused_before_writing(calibration_env, seconds):
    from rag_eval.optimizer.v4.calibration_recovery import configure
    _, _, _, root = calibration_env
    bundle = measure(calibration_env)
    bundle['complete'] = False
    session._save(root / session.CALIBRATION_FILE, bundle)
    with pytest.raises(ValueError):
        configure(root, seconds)
    assert not (root / 'calibration-recovery.json').exists()


def test_polling_covers_extended_execution_and_its_retry(monkeypatch):
    from types import SimpleNamespace
    from rag_eval.optimizer.sn_adapter import SNAdapter
    from rag_eval.optimizer import sn_adapter
    clock = [0.]
    monkeypatch.setattr(sn_adapter, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    adapter = object.__new__(SNAdapter)
    adapter._infra_retry = 1
    adapter.set_key_generation(0)
    adapter._execution_timeout_seconds = 7200
    adapter._recovery_execution_timeout_seconds = 14400
    adapter._eval_timeout, adapter._eval_poll_interval = 7200, 1000
    adapter._wait_for_running, adapter._runtime_remaining = False, None
    adapter.check_runtime = lambda: None
    adapter._first_call = lambda ids, call: call()
    adapter._runtime_wait = lambda seconds: clock.__setitem__(0, clock[0] + seconds)
    # Two valid 10000-second executions fit their individual 14400-second limits.
    adapter._await({'work': ('key', lambda: dict(status='complete' if clock[0] >= 20000 else 'running'))})
    assert clock[0] == 20000
