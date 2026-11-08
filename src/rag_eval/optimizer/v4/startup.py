"""Safe deployment diagnostics, separate from frozen optimization state.

HTTP can succeed before its child rechecks deployment. A failed child publishes
this receipt; readers only show it for the registered identity while inactive.
"""
from pathlib import Path
import os
import time

from .. import ledger_io
from ..run_view import _optional_json
from rag_eval.workbench import auto_runs
from rag_eval.workbench.agent_creations import CreationProfileUnavailable
from rag_eval.workbench.agent_preflight import AgentPreflightError

FILE = 'startup-error.json'
IDENTITY_KEYS = ('run_id', 'iteration_id', 'actor')


def clear(root):
    (Path(root) / FILE).unlink(missing_ok=True)


def preflight(services, args, settings, roles=('analyst', 'implementer', 'reviewer', 'calibrator')):
    from .agent_profiles import preflight as check
    root = Path(args.state_dir)
    identity = dict(run_id=args.run_id, iteration_id=args.iteration, actor=args.actor)
    bound = auto_runs.binding(services.repository, args.iteration) if services.repository else None
    saved = _optional_json(root / 'cli-run.json')
    matching = all(not other or all(other.get(k) == identity[k] for k in IDENTITY_KEYS)
                   for other in (bound, saved))
    matching = matching and (not bound or Path(bound['state_dir']).resolve() == root.resolve())
    try:
        result = check(services.adapter, services.task_id, settings, roles,
                       expected=args.expected_agent_profiles_sha256)
    except (AgentPreflightError, CreationProfileUnavailable) as exc:
        if matching and root.is_dir():
            ledger_io.atomic_write_json(root / FILE, dict(identity, command=args.command,
                code=exc.code if isinstance(exc, AgentPreflightError) else 'PROFILE_UNAVAILABLE',
                pid=os.getpid(), at=time.time()))
        raise
    if matching:
        clear(root)
    return result


def read(root, identity, *, alive):
    if alive or not identity:
        return None
    receipt = _optional_json(Path(root) / FILE)
    if not isinstance(receipt, dict) or any(receipt.get(k) != identity.get(k) for k in IDENTITY_KEYS):
        return None
    if receipt.get('command') not in ('run', 'calibrate'):
        return None
    code = receipt.get('code')
    message = ('Agent 模型配置不可用于当前工作区，请核查角色配置与授权'
               if code == 'PROFILE_UNAVAILABLE' else str(AgentPreflightError(code)))
    return dict(code=code, message=message, command=receipt['command'], at=receipt.get('at'))
