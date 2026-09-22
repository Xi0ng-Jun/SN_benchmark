"""Local-only configuration shared by live and saved-component evaluation."""
from importlib.metadata import version
import math
import os


def configure_local_sdk(*, task_timeout=None):
    if version('deepeval') != '4.2.2':
        raise RuntimeError('Native agent evaluation requires deepeval==4.2.2')
    if task_timeout is not None and (not math.isfinite(task_timeout) or task_timeout <= 0):
        raise ValueError('Task timeout must be finite and positive')
    os.environ.update(DEEPEVAL_TELEMETRY_OPT_OUT='YES', DEEPEVAL_DISABLE_DOTENV='1',
                      DEEPEVAL_NO_INSPECT_PROMPT='1', CONFIDENT_TRACE_FLUSH='0')
    os.environ.pop('CONFIDENT_API_KEY', None)
    from deepeval import get_settings
    settings = get_settings()
    with settings.edit(persist=False):
        settings.CONFIDENT_API_KEY = None
        settings.CONFIDENT_TRACE_FLUSH = False
        settings.DEEPEVAL_TELEMETRY_OPT_OUT = True
        if task_timeout is not None:
            settings.DEEPEVAL_DISABLE_TIMEOUTS = False
            settings.DEEPEVAL_PER_TASK_TIMEOUT_SECONDS_OVERRIDE = task_timeout


def sdk_timeout_identity():
    from deepeval import get_settings
    settings = get_settings()
    return {'per_task_seconds_override': settings.DEEPEVAL_PER_TASK_TIMEOUT_SECONDS_OVERRIDE,
            'per_task_seconds': settings.DEEPEVAL_PER_TASK_TIMEOUT_SECONDS,
            'timeouts_disabled': settings.DEEPEVAL_DISABLE_TIMEOUTS}
