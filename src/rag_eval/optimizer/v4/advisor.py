"""Bounded pre-run calibration advice. Suggestions never bypass proposal validation."""
from __future__ import annotations

import json
import time
from pathlib import Path

from .. import ledger_io
from ..adapter import EvalResult
from . import calibration_session as session, proposal

TIMEOUT_S = 600


def evidence(adapter, bundle):
    diagnosis = EvalResult(**bundle['results']['diagnosis'])
    allowed = set(bundle['diagnosis'])
    if not set(diagnosis.trajectories) <= allowed:
        raise ValueError('advisor diagnosis contains cases outside D')
    views = adapter.diagnostic_views(diagnosis.trajectories)
    return dict(calibration=session.public_summary(bundle), inputs=bundle['identity']['user_inputs'],
        history=bundle['history'], diagnosis=[dict(row=i + 1, score=diagnosis.per_task_scores.get(task),
            view=views.get(task)) for i, task in enumerate(sorted(allowed))])


def accept(base, raw, bundle):
    if not isinstance(raw, dict) or not isinstance(raw.get('values', {}), dict):
        raise ValueError('advisor output must contain a values object')
    if any(not isinstance(v, dict) for v in raw.get('values', {}).values()):
        raise ValueError('advisor parameter suggestions must be objects')
    classifications = raw.get('failure_modes', [])
    if (not isinstance(classifications, list) or len(classifications) > 30
            or any(not isinstance(v, str) or len(v) > 1000 for v in classifications)):
        raise ValueError('failure_modes must contain at most 30 short descriptions')
    narrative = raw.get('message', '')
    if not isinstance(narrative, str) or len(narrative) > 8000:
        raise ValueError('advisor message exceeds its text limit')
    # Raw observations are never a mechanism for changing a user-edited value.
    protected = {k: item for k, item in base.items.items() if item.source == 'user'}
    suggested = proposal.apply_agent_values(base, raw.get('values', {}))
    suggested.items.update(protected)
    checked = proposal.validate(suggested, bundle['summary'], bundle['history'])
    if not checked['ok']:
        raise ValueError('advisor proposal failed validation')
    out = checked['proposal']
    out.notes['advisor'] = dict(status='complete', failure_modes=classifications, message=narrative,
                                rejected=checked['rejected'])
    return out


def run(adapter, seed, state_dir, *, profile, clock=time.monotonic, timeout=TIMEOUT_S):
    """Use durable bridge request keys; persist the deadline across process resumes."""
    root = Path(state_dir)
    bundle = session.load_bundle(root / session.CALIBRATION_FILE)
    base = proposal.Proposal.from_dict(ledger_io.read_json(root / session.PROPOSAL_FILE))
    if base.notes.get('advisor') or (root / session.RUN_CONFIG_FILE).exists():
        return base
    intent_path = root / 'advisor-intent.json'
    intent = ledger_io.read_json(intent_path) if intent_path.exists() else dict(
        calibration_sha256=bundle['payload_sha256'], started_at=time.time(),
        deadline=time.time() + min(TIMEOUT_S, timeout))
    if intent['calibration_sha256'] != bundle['payload_sha256']:
        raise ValueError('advisor calibration identity changed')
    ledger_io.atomic_write_json(intent_path, intent)
    evidence_path, output = root / 'advisor-evidence.json', root / 'proposal.agent.json'
    key = adapter._request_key('calibrator', bundle['payload_sha256'])
    result = {}
    started = clock()
    remaining = max(0, min(TIMEOUT_S, intent['deadline'] - time.time()))
    stage = 'evidence'
    try:
        ledger_io.atomic_write_json(evidence_path, evidence(adapter, bundle))
        text = (f'Read your calibration evidence at {evidence_path.resolve()}. '
                f'Write your suggestions to {output.resolve()} as '
                '{"values": {"parameter": {"value": ..., "sim_id": ..., "sim_params": {...}, '
                '"reason": ..., "confidence": "high|medium|low"}}, '
                '"failure_modes": ["description"], "message": "budget and risks explained to the user"}. '
                'Use the bound simulate tool for every proposed simulation parameter. '
                'Only D cases are provided. Never inspect P, validation, endpoint, or other run files.')
        while True:
            if clock() - started >= remaining:
                raise TimeoutError('10 minute calibration advisor deadline')
            adapter.check_runtime()
            stage = 'agent_request'
            result = adapter._round_ops.agent('calibrator', adapter.candidate_id(seed),
                iteration_id=bundle['identity']['iteration_id'], key=key, profile=profile,
                text=text, brief='method-v4')
            adapter._track_agent(key, result)
            if result.get('status') == 'idle':
                stage = 'output_read'
                if output.stat().st_size > 128000:
                    raise ValueError('advisor output exceeds 128 KB')
                raw = ledger_io.read_json(output)
                stage = 'output_validation'
                out = accept(base, raw, bundle)
                break
            if result.get('status') not in ('running', 'unknown'):
                raise ValueError('advisor did not finish successfully')
            adapter._runtime_wait(min(2, max(0, remaining - (clock() - started))))
    except Exception as exc:
        from rag_eval.workbench.agent_creations import CreationProfileUnavailable
        out = base
        profile_error = isinstance(exc, CreationProfileUnavailable)
        reason = exc.code if profile_error else type(exc).__name__
        detail = ('校准顾问模型配置未获当前工作区授权，请选择已登记配置；本次使用规则建议'
                  if profile_error else f'校准顾问未给出建议：{type(exc).__name__}（阶段：{stage}）')
        out.notes['advisor'] = dict(status='fallback', reason=reason, stage=stage,
                                    error_type=type(exc).__name__, message=detail, failure_modes=[])
    finally:
        # Close this role only; never cancel the run's completed measurements.
        adapter._cancel_agent(key, result.get('session_id'))
    ledger_io.atomic_write_json(root / session.PROPOSAL_FILE, out.to_dict())
    return out


def tool(options, repository, views):
    """Role and run-scoped tool boundary; the generic analyst dispatcher is never used."""
    from rag_eval.workbench.auto_runs import task_binding
    from rag_eval.workbench.agent_tools import iteration_service
    from rag_eval.workbench.frozen_source import candidate_source
    from .simulate import simulate
    bound = task_binding(repository, options.task)
    if not bound or bound.get('method') != 'method-v4' or options.run_id != bound['run_id']:
        raise ValueError('calibrator needs its automatic method-v4 --run-id binding')
    root = Path(bound['state_dir'])
    if (root / session.RUN_CONFIG_FILE).exists():
        raise ValueError('calibration tools are closed after freeze')
    bundle = session.load_bundle(root / session.CALIBRATION_FILE)
    service = iteration_service(repository, views, initialize=False)
    tree = service.iteration_record(bound['iteration_id'])
    if options.node != tree['root_candidate_id'] or options.parent or options.candidate:
        raise ValueError('calibrator must bind --node to its frozen seed')
    if options.command == 'calibration show':
        return session.public_summary(bundle)
    if options.command == 'simulate':
        return simulate(json.loads(options.params or '{}'), bundle['summary'], bundle['history'])
    if options.command == 'history':
        return bundle['history']
    if options.command == 'source':
        return candidate_source(service, options.task, options.node, path=options.path, query=options.query,
                                start=options.start, lines=options.lines)
    raise ValueError('command not allowed for calibrator')
