"""Explicit recovery of the current analyst's known pre-dispatch refusal.

Preview and confirmation make no model calls. The store prepares a successor;
the ordinary run/resume path later dispatches it under the original logical key.
"""
import json
from pathlib import Path

from . import cli
from .agent_profiles import preflight
from .. import run_view
from rag_eval.workbench import auto_runs


def command(args):
    cli._require(args, 'iteration', 'actor', 'run_id', 'state_dir', 'intent_id')
    root = Path(args.state_dir)
    if not (root / cli.IDENTITY_FILE).is_file():
        raise cli.UsageError('Agent recovery requires an existing optimization run')
    try:
        with run_view.hold_run_lock(root):
            return _locked(args, root)
    except (run_view.RunLocked, ValueError) as exc:
        raise cli.UsageError(str(exc)) from None


def _locked(args, root):
    identity = cli._read(root / cli.IDENTITY_FILE, 'run identity')
    state = cli._read(root / cli.STATE_FILE, 'run state')
    for name, value in (('iteration_id', args.iteration), ('run_id', args.run_id), ('actor', args.actor)):
        if identity.get(name) != value:
            raise cli.UsageError('Agent recovery must use the original ' + name)
    if state.get('halt_reason') not in (None, 'process_failure'):
        raise cli.UsageError('This run is not waiting for Agent recovery')
    rd = state.get('current_round') or {}
    if not rd or rd.get('cards') is not None:
        raise cli.UsageError('Recovery is limited to the current round before analysis cards are saved')
    loaded = cli.load(root / cli.CONFIG_COPY_FILE)
    if loaded.sha256 != identity['config_sha256']:
        raise cli.UsageError('Frozen run configuration changed')
    services = cli.build_services(args.config, args.state, args.iteration, actor=args.actor,
                                 run_id=args.run_id, adapter_settings=loaded.adapter_settings)
    binding = auto_runs.binding(services.repository, args.iteration)
    if (not binding or binding['run_id'] != args.run_id or binding.get('method') != 'method-v4'
            or Path(binding['state_dir']).resolve() != root.resolve()
            or services.task_id != identity['task_id']):
        raise cli.UsageError('Agent recovery does not match the registered run')
    adapter, ops = services.adapter, services.adapter._round_ops
    store = ops.creations.store
    parent_id = (state.get('versions', {}).get(rd['parent']) or {}).get('candidate_id')
    if not parent_id:
        raise cli.UsageError('Current analysis has no bound parent candidate')
    key = adapter._request_key('analyse', rd['round'], parent_id)
    request_key = ops._key('agent-create', key)
    owner = ops._get(ops._key('agent-actor', key))
    if not owner or not owner.get('actor'):
        raise cli.UsageError('Current analysis has no persisted request owner')
    actor = owner['actor']
    old = store.get(services.task_id, args.intent_id, actor=actor)
    # Walk the durable successor chain from this round's original request, not
    # from arbitrary caller-supplied role/candidate/cwd data.
    original = next((r for r in store.list(services.task_id, actor=actor)
                     if r['request_key'] == request_key), None)
    if not original:
        raise cli.UsageError('No analyst request exists for the current round')
    with services.repository.connect() as conn:
        current, seen = original['id'], set()
        while current != old['id'] and current not in seen:
            seen.add(current)
            link = conn.execute('SELECT successor_id FROM agent_creation_retries WHERE predecessor_id=?',
                                (current,)).fetchone()
            if not link:
                break
            current = link['successor_id']
    if (current != old['id'] or old.get('role') != 'analyst' or old.get('candidate_id') != parent_id
            or old.get('brief') != 'method-v4'):
        raise cli.UsageError('The intent is not this round\'s analyst creation')
    store._require_retryable(old)
    preflight(adapter, services.task_id, loaded.adapter_settings, ('analyst', 'implementer', 'reviewer'))
    effective = store.find_request(services.task_id, request_key, actor=actor)
    if args.confirm:
        effective = store.retry_not_attempted(services.task_id, old['id'], actor=actor)
    print(json.dumps(dict(action='recover-agent-creation', confirmed=args.confirm, round=rd['round'],
        source_intent_id=old['id'], source_code=old['envelope']['result']['code'],
        effective_intent_id=effective['id'], effective_status=effective['status'],
        creates_agent=False, next_step='resume the original run without --retry-failed'), ensure_ascii=False))
    return cli.EXIT_OK
