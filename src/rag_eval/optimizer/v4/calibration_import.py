"""Reuse a completed v4.1 calibration in a new deferred iteration, without re-measuring.

The seed's probe/diagnosis measurements (and the validation-remainder baseline)
are expensive; a new iteration with the same seed commit, input, pool, split
seed, k and adapter settings can reuse them. ``plan`` is read-only and reports
every identity check; ``apply`` performs the import idempotently:

1. intent receipt (``calibration-import.json``) before any effect;
2. register P/D on the new iteration; bind it to its v4 run directory;
3. link the source root's executions used by the calibration to the new root
   (``candidate_links``, basis ``calibration_import``), so the root is evaluated
   and leak scans see those cases;
4. copy the proposal (the user's edits stay ``user``; advisor notes kept),
   launch/input receipts and a staged remainder baseline;
5. write the calibration bundle under the new iteration identity, then mark
   the receipt complete.

``extend_validation=N`` additionally grows the target pool by N questions outside the
source pool (whole papers in split-seed hash order, never sealed). They all join the
validation set: the frozen split is the source's validation ∪ the extension, search is
unchanged, so P and D keep their calibration roles. The proposal is recomputed for the
larger pool (user edits kept, validation_size set to the new size) and the seed is
measured once on the extension at k=1 when the run starts.

The source iteration, its run directory and its receipts are only read.
"""
from __future__ import annotations

import copy
import hashlib
from datetime import datetime, timezone
from pathlib import Path

from .. import ledger_io
from . import calibration_session as session, proposal

IMPORT_FILE = 'calibration-import.json'
REMAINDER_IMPORT_FILE = 'baseline-remainder.import.json'
REMAINDER_FILE = 'baseline-remainder.json'
COPIED = ('calibration-input.json', 'launch.json')
# Protocol fields that decide what the seed measured; equal on both sides.
PROTOCOL_KEYS = ('mode', 'request_revision', 'reasoning_protocol', 'input', 'bundle_ref', 'index_ref', 'scorer',
                 'k_base', 'success_threshold', 'case_retry', 'execution', 'budget')
SPLIT_KEYS = ('pool', 'sealed_ref', 'sealed', 'seed')
TERMINAL = ('finished', 'failed', 'timeout')


class ImportRefused(ValueError):
    """An identity check failed; nothing was written."""


def default_dir(state_root, iteration_id):
    """The run directory the workbench control uses for an unbound iteration."""
    return Path(state_root) / 'automatic-v41' / hashlib.sha256(iteration_id.encode()).hexdigest()[:24]


def require_complete(root):
    """Refuse to calibrate or run over a started but unfinished import."""
    path = Path(root) / IMPORT_FILE
    if path.exists() and ledger_io.read_json(path).get('status') != 'complete':
        raise ImportRefused('calibration import is incomplete; repeat the import before running')


def _execution_ids(result):
    return {row['execution_id'] for rows in (result or {}).get('trajectories', {}).values()
            for row in rows if isinstance(row, dict) and row.get('execution_id')}


def _split_value(split, key):
    """A frozen split no longer stores its pool: it is search plus validation."""
    if key == 'pool' and 'pool' not in split:
        return sorted(set(split.get('search') or ()) | set(split.get('validation') or ()))
    return split.get(key)


def _extension(service, protocol, pool, n):
    """N or more questions outside *pool* and the sealed papers: whole papers in split-seed hash order."""
    split = protocol.get('splits') or {}
    partitions = service.plans.inputs.describe(protocol['input']['input_id'])['partitions']
    sealed = set(split.get('sealed') or ())
    groups = [sorted(p['case_ids']) for p in partitions
              if p['case_ids'] and not set(p['case_ids']) & (set(pool) | sealed)]
    groups.sort(key=lambda g: session.canonical_hash([split.get('seed'), g]))
    out = []
    for group in groups:
        if len(out) >= n:
            break
        out.extend(group)
    return sorted(out), sum(len(g) for g in groups)


def plan(service, repository, *, source_iteration, target_iteration, actor, target_dir, extend_validation=0):
    """Every identity check with its outcome, the links to add and the plan digest. Read-only."""
    from rag_eval.workbench import auto_runs
    if type(extend_validation) is not int or extend_validation < 0:
        raise ImportRefused('extend_validation must be a non-negative integer')
    checks = []

    def check(name, ok, detail=None):
        checks.append(dict(check=name, ok=bool(ok), **({'detail': detail} if detail is not None else {})))
        return ok

    target_dir = Path(target_dir).resolve()
    bound = auto_runs.binding(repository, source_iteration)
    if not check('source is a method-v4 automatic run', bound and bound.get('method') == 'method-v4'):
        raise ImportRefused('the source iteration is not bound to a method-v4 run')
    source_dir = Path(bound['state_dir'])
    try:
        bundle = session.load_bundle(source_dir / session.CALIBRATION_FILE)
    except (OSError, ValueError) as exc:
        raise ImportRefused(f'source calibration unusable: {exc}') from None
    identity = bundle['identity']
    check('source calibration belongs to the source iteration', identity['iteration_id'] == source_iteration)
    proposed = proposal.Proposal.from_dict(ledger_io.read_json(source_dir / session.PROPOSAL_FILE))
    check('source proposal is valid', proposal.validate(proposed, bundle['summary'], bundle['history'])['ok'])
    remainder = None
    if (source_dir / REMAINDER_FILE).exists():
        remainder = session._load(source_dir / REMAINDER_FILE)
        check('source remainder baseline belongs to this calibration',
              remainder['identity'].get('calibration') == bundle['payload_sha256']
              and remainder['identity'].get('seed') == identity['seed'])

    receipt = (ledger_io.read_json(target_dir / IMPORT_FILE) if (target_dir / IMPORT_FILE).exists() else None)
    # A replayed import already grew the pool: compare the source with the target's base pool.
    stored = set(((receipt or {}).get('extension') or {}).get('cases') or ())
    source_tree, target = service.iteration(source_iteration), service.iteration(target_iteration)
    old_root, new_root = (service.candidate(source_tree['root_candidate_id']),
                          service.candidate(target['root_candidate_id']))
    split = (target['protocol'] or {}).get('splits') or {}
    check('target is native', target.get('origin') == 'native')
    check('target split is pending', split.get('status') == 'pending')
    check('target P/D are unregistered or equal',
          ('probe' not in split and 'diagnosis' not in split)
          or (split.get('probe') == sorted(bundle['probe']) and split.get('diagnosis') == sorted(bundle['diagnosis'])))
    run_id = 'v41-' + target_iteration
    target_bound = auto_runs.binding(repository, target_iteration)
    check('target is unbound or bound to its import run',
          target_bound is None or (target_bound['run_id'] == run_id and target_bound.get('method') == 'method-v4'
                                   and Path(target_bound['state_dir']).resolve() == target_dir))
    check('actor is the calibration actor', actor == identity['actor'], dict(source=identity['actor'], target=actor))
    check('start commit', source_tree['start_commit'] == target['start_commit'])
    check('seed commit', new_root.get('status') == 'frozen' and new_root.get('frozen_commit') == identity['seed'])
    for key in PROTOCOL_KEYS:
        check('protocol ' + key, (source_tree['protocol'] or {}).get(key) == (target['protocol'] or {}).get(key))
    source_split = (source_tree['protocol'] or {}).get('splits') or {}
    for key in SPLIT_KEYS:
        value = _split_value(split, key)
        if key == 'pool' and value is not None:
            value = sorted(set(value) - stored)
        check('split ' + key, _split_value(source_split, key) == value)
    groups = []
    try:
        _, groups = session._papers(service, target_iteration)
        groups = [g for g in groups if not set(g) & stored]
        pool = sorted(c for g in groups for c in g)
        check('pool hash', session.canonical_hash(pool) == identity['pool_sha256'])
        check('paper order hash', session.canonical_hash(groups) == identity['papers_sha256'])
    except ValueError as exc:
        check('target pool forms whole papers', False, str(exc))

    # Links: exactly the source root's executions the calibration results use.
    used = (_execution_ids(bundle['results'].get('probe')) | _execution_ids(bundle['results'].get('diagnosis'))
            | _execution_ids((remainder or {}).get('result')))
    attempts = {a['execution_id']: a for a in old_root['attempts'] if a.get('execution_id')}
    unknown = sorted(used - set(attempts))
    check('calibration executions are source-root attempts', not unknown, unknown[:5] or None)
    unsettled = sorted(e for e in used & set(attempts) if attempts[e].get('execution_state') not in TERMINAL)
    check('calibration executions are terminal', not unsettled, unsettled[:5] or None)
    check('a calibration execution is admitted',
          any((attempts[e].get('admission') or {}).get('status') == 'admitted' for e in used & set(attempts)))
    links = [dict(execution_id=e, task_id=attempts[e]['task_id'],
                  basis=dict(from_iteration=source_iteration, from_candidate=old_root['id'],
                             scope=attempts[e].get('batch_id'), attempt=attempts[e].get('attempt'),
                             calibration_sha256=bundle['payload_sha256']))
             for e in sorted(used & set(attempts))]
    ours = {link['execution_id'] for link in links}
    foreign_links = [l['execution_id'] for l in new_root.get('links') or [] if l['execution_id'] not in ours]
    check('target root has no measurements of its own', not new_root['attempts'] and not foreign_links)

    extension = []
    if receipt is not None and 'extension' in receipt:
        extension = sorted(stored)
        check('extension size matches the earlier import', receipt['extension'].get('requested') == extend_validation)
    elif extend_validation:
        extension, available = _extension(service, target['protocol'] or {},
                                          sorted(c for g in groups for c in g), extend_validation)
        check('extension questions available outside the pool', len(extension) >= extend_validation,
              dict(requested=extend_validation, available=available))
    base_validation = sorted(source_split.get('validation') or ())
    if extension:
        check('source split is frozen (its validation set is extended)',
              base_validation and set(bundle['probe']) <= set(base_validation))
    source = dict(iteration_id=source_iteration, candidate_id=old_root['id'], state_dir=str(source_dir),
                  calibration_sha256=bundle['payload_sha256'],
                  proposal_sha256=session.canonical_hash(proposed.to_dict()),
                  remainder_sha256=(remainder or {}).get('payload_sha256'))
    digest = session.canonical_hash(dict(source=source, target=dict(iteration_id=target_iteration,
                                         candidate_id=new_root['id'], run_id=run_id, state_dir=str(target_dir)),
                                         actor=actor, links=links,
                                         **({'extension': extension} if extension else {})))
    if receipt is not None:
        check('an earlier import of this directory is the same plan', receipt.get('plan_sha256') == digest)
    else:
        existing = [n for n in (session.CALIBRATION_FILE, session.RUN_CONFIG_FILE, 'state.json')
                    if (target_dir / n).exists()]
        check('target run directory has no calibration or run', not existing, existing or None)
    edited = sorted(k for k, item in proposed.items.items() if item.source == 'user')
    return dict(ok=all(c['ok'] for c in checks), checks=checks, plan_sha256=digest, source=source,
                target=dict(iteration_id=target_iteration, candidate_id=new_root['id'], run_id=run_id,
                            state_dir=str(target_dir)),
                actor=actor, links=links, probe=len(bundle['probe']), diagnosis=len(bundle['diagnosis']),
                remainder_tasks=len((remainder or {}).get('identity', {}).get('tasks') or []),
                proposal=dict(values=proposed.values(), user_edited=edited,
                              advisor=(proposed.notes.get('advisor') or {}).get('status')),
                extension=dict(requested=extend_validation, added=len(extension),
                               validation=len(base_validation) + len(extension) if extension else None,
                               cases_sha256=session.canonical_hash(extension)) if extend_validation else None,
                extension_cases=extension, base_validation=base_validation if extension else None,
                complete=bool(receipt and receipt.get('status') == 'complete'))


def extended_proposal(old, summary, history, validation_size, notes):
    """Rule defaults for the extended pool with the user's edits re-applied; validation_size set by the import."""
    fresh = proposal.rule_defaults(summary, old.inputs, history)
    skipped = []
    for key, item in sorted(old.items.items()):
        if item.source != 'user' or key == 'validation_size':
            continue
        try:
            fresh = proposal.apply_user_edit(fresh, key, item.value)
        except (KeyError, ValueError) as exc:
            skipped.append(dict(key=key, reason=str(exc)[:200]))
    fresh = proposal.apply_user_edit(fresh, 'validation_size', validation_size)
    fresh.items['validation_size'].reason = 'calibration import: source validation set plus the extension'
    fresh.notes = dict(fresh.notes, **notes, **({'skipped_user_edits': skipped} if skipped else {}))
    return fresh


def apply(service, repository, planned, *, confirm):
    """Perform a checked plan; every step is idempotent so a crash can be replayed."""
    from rag_eval.workbench import auto_runs
    from ..run_view import hold_run_lock
    if confirm != planned['plan_sha256']:
        raise ImportRefused('confirmation does not match the previewed plan')
    if not planned['ok']:
        failed = [c['check'] for c in planned['checks'] if not c['ok']]
        raise ImportRefused('identity checks failed: ' + ', '.join(failed))
    source, target = Path(planned['source']['state_dir']), Path(planned['target']['state_dir'])
    target.mkdir(parents=True, exist_ok=True)
    with hold_run_lock(target):
        receipt_path = target / IMPORT_FILE
        receipt = ledger_io.read_json(receipt_path) if receipt_path.exists() else None
        if receipt is not None and receipt.get('plan_sha256') != planned['plan_sha256']:
            raise ImportRefused('a different import was started in this run directory')
        if receipt is None:
            receipt = dict(kind='method-v4.1-calibration-import', status='intent', plan_sha256=planned['plan_sha256'],
                           source=planned['source'], target=planned['target'], actor=planned['actor'],
                           links=len(planned['links']), at=datetime.now(timezone.utc).isoformat())
            if planned.get('extension_cases'):
                receipt['extension'] = dict(requested=planned['extension']['requested'],
                                            cases=planned['extension_cases'])
            ledger_io.atomic_write_json(receipt_path, receipt)
        if receipt['status'] == 'complete':
            return receipt
        bundle = session.load_bundle(source / session.CALIBRATION_FILE)
        if bundle['payload_sha256'] != planned['source']['calibration_sha256']:
            raise ImportRefused('source calibration changed since the preview')
        target_id = planned['target']['iteration_id']
        service.register_calibration_samples(target_id, bundle['probe'], bundle['diagnosis'], actor=planned['actor'])
        auto_runs.register(repository, target_id, run_id=planned['target']['run_id'],
                           state_dir=planned['target']['state_dir'], actor=planned['actor'], method='method-v4')
        service.link_calibration_import(planned['target']['candidate_id'], planned['links'], actor=planned['actor'])
        extension = (receipt.get('extension') or {}).get('cases') or []
        if extension:
            service.extend_pending_pool(target_id, extension, actor=planned['actor'])
        # The proposal precedes the bundle: a crash between them must not let the
        # calibration crash-recovery branch write rule defaults over the user's values.
        old = ledger_io.read_json(source / session.PROPOSAL_FILE)
        if session.canonical_hash(proposal.Proposal.from_dict(old).to_dict()) != planned['source']['proposal_sha256']:
            raise ImportRefused('source proposal changed since the preview')
        origin = dict(iteration_id=planned['source']['iteration_id'], proposal_sha256=planned['source']['proposal_sha256'])
        moved = copy.deepcopy(bundle)
        moved['identity'] = dict(bundle['identity'], iteration_id=target_id, actor=planned['actor'])
        moved['imported_from'] = planned['source']
        if extension:
            _, groups = session._papers(service, target_id)
            added = [g for g in groups if set(g) <= set(extension)]
            summary = dict(bundle['summary'], pool_size=bundle['summary']['pool_size'] + len(extension))
            if summary.get('remaining_paper_sizes') is not None:
                summary['remaining_paper_sizes'] = list(summary['remaining_paper_sizes']) + [len(g) for g in added]
            moved['summary'] = summary
            moved['identity'].update(pool_sha256=session.canonical_hash(sorted(c for g in groups for c in g)),
                                     papers_sha256=session.canonical_hash(groups))
            moved['extension'] = dict(cases=sorted(extension), base_validation=planned['base_validation'],
                                      source_pool_sha256=bundle['identity']['pool_sha256'])
            fresh = extended_proposal(proposal.Proposal.from_dict(old), summary, bundle['history'],
                                      len(planned['base_validation']) + len(extension),
                                      dict(imported_from=origin, extension=dict(added=len(extension))))
            imported = fresh.to_dict()
        else:
            imported = copy.deepcopy(old)
            imported['notes'] = dict(imported.get('notes') or {}, imported_from=origin)
        ledger_io.atomic_write_json(target / session.PROPOSAL_FILE, imported)
        for name in COPIED:
            if (source / name).exists():
                ledger_io.atomic_write_json(target / name, ledger_io.read_json(source / name))
        if (source / REMAINDER_FILE).exists():
            remainder = session._load(source / REMAINDER_FILE)
            session._save(target / REMAINDER_IMPORT_FILE, dict(source=planned['source'], identity=remainder['identity'],
                                                               result=remainder['result']))
        session._save(target / session.CALIBRATION_FILE, moved)
        receipt = dict(receipt, status='complete', completed_at=datetime.now(timezone.utc).isoformat())
        ledger_io.atomic_write_json(receipt_path, receipt)
        return receipt
