"""An explicit, immutable budget for new work in an existing calibration.

The original calibration identity and previously dispatched execution budgets
are preserved. This receipt authorizes no extra retry slots or new samples.
"""
from pathlib import Path

from . import calibration_session as session
from .config import ADAPTER_DEFAULTS

FILE = 'calibration-recovery.json'


def configure(root, requested=None, *, write=True):
    root = Path(root)
    path = root / FILE
    saved = session._load(path) if path.exists() else None
    if saved is None and requested is None:
        return None
    calibration = root / session.CALIBRATION_FILE
    if not calibration.exists():
        raise ValueError('恢复时限只能用于已有的未完成校准')
    bundle = session._load(calibration)
    identity = session.canonical_hash(bundle['identity'])
    if saved and saved['calibration_identity_sha256'] != identity:
        raise ValueError('恢复配置与原校准身份不一致')
    if requested is not None:
        original = bundle['identity'].get('adapter_settings', {}).get(
            'execution_timeout_seconds', ADAPTER_DEFAULTS['execution_timeout_seconds'])
        if type(requested) is not int or not original < requested <= 86400:
            raise ValueError('恢复执行时限必须大于原时限，且不超过 86400 秒')
        if saved and saved['execution_timeout_seconds'] != requested:
            raise ValueError('恢复执行时限已登记，请使用原恢复时限')
        if not saved:
            if bundle.get('complete') or (root / session.RUN_CONFIG_FILE).exists():
                raise ValueError('恢复时限只能用于未完成校准')
            saved = dict(calibration_identity_sha256=identity, execution_timeout_seconds=requested)
            if write:
                session._save(path, saved)
    return saved['execution_timeout_seconds']
