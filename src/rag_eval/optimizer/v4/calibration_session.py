"""Durable calibration, immutable proposal freeze and baseline reuse (v4.1).

Private artifacts carry case identities. Public views are constructed explicitly;
they never expose those identities or the endpoint selection.
"""
from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import math
import statistics
import time
from pathlib import Path

from .. import ledger_io
from ..adapter import EvalResult
from . import calibrate, decide, proposal
from .simulate import CalibrationSummary, History, quantile, simulate, _history

CALIBRATION_FILE = 'calibration.json'
PROPOSAL_FILE = 'proposal.json'
RUN_CONFIG_FILE = 'run-config.json'


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def _save(path, value):
    value = copy.deepcopy(value)
    value.pop('payload_sha256', None)
    value['payload_sha256'] = canonical_hash(value)
    ledger_io.atomic_write_json(path, value)
    return value


def _load(path):
    saved = ledger_io.read_json(path)
    payload = {k: v for k, v in saved.items() if k != 'payload_sha256'}
    if saved.get('payload_sha256') != canonical_hash(payload):
        raise ValueError(f'{Path(path).name}: artifact identity changed')
    return saved


def load_bundle(path):
    saved = _load(path)
    if saved.get('kind') != 'method-v4.1-calibration' or not saved.get('complete'):
        raise ValueError('calibration is not complete')
    CalibrationSummary.from_dict(saved['summary'])
    return saved


def _papers(service, iteration_id):
    tree = service.iteration(iteration_id)
    split = tree['protocol']['splits']
    pool = split.get('pool') or sorted(split['search'] + split['validation'])
    partitions = service.plans.inputs.describe(tree['protocol']['input']['input_id'])['partitions']
    wanted = set(pool)
    groups = [sorted(set(p['case_ids']) & wanted) for p in partitions]
    groups = [g for g in groups if g]
    if set(c for g in groups for c in g) != wanted or sum(map(len, groups)) != len(wanted):
        raise ValueError('pool papers do not form a unique partition')
    # Stable seed-based whole-paper order; neither scores nor output order affect selection.
    groups.sort(key=lambda g: canonical_hash([split['seed'], g]))
    return tree, groups


def _take(groups, target, *, leave=0):
    out = []
    for group in groups[:len(groups) - leave if leave else None]:
        out.extend(group)
        if len(out) >= target:
            break
    return sorted(out)


def _timing(profiles, fallback, n, k):
    cold = [p['wall_s'] for p in profiles if p.get('cache_misses', 0) > 0]
    warm = [p['wall_s'] for p in profiles if p.get('cache_hits', 0) > 0 and not p.get('cache_misses')]
    usable = [p for p in profiles if p.get('cases', 0) > 0 and p.get('wall_s', -1) >= 0]
    # Fit only if distinct sizes are observed. With one size the intercept is
    # unidentifiable; preserve that fact instead of calling zero a measurement.
    samples = [p for p in usable if p.get('cache_hits', 0) and not p.get('cache_misses')]
    xs, ys = [p['cases'] for p in samples], [p['wall_s'] for p in samples]
    denominator = sum((x - statistics.mean(xs)) ** 2 for x in xs) if xs else 0
    intercept, slope = 0., fallback / max(1, n * k)
    source = 'assumed zero fixed overhead; observed aggregate wall per task'
    if usable:
        slope = statistics.mean(p['wall_s'] / p['cases'] for p in samples or usable)
        source = 'assumed zero fixed overhead; observed execution wall per task'
    if denominator:
        fitted = sum((x - statistics.mean(xs)) * (y - statistics.mean(ys)) for x, y in zip(xs, ys)) / denominator
        fixed = statistics.mean(ys) - fitted * statistics.mean(xs)
        if fitted >= 0 and fixed >= 0:
            intercept, slope, source = fixed, fitted, 'fit to observed warm executions at different sizes'
    return dict(exec_overhead_s=intercept, per_task_s=slope,
                p95_exec_s=quantile(sorted(p['wall_s'] for p in usable), .95) if usable else fallback / k,
                cold_wall_s=statistics.mean(cold) if cold else None,
                warm_wall_s=statistics.mean(warm) if warm else None), source


def run_calibration(adapter, seed, service, iteration_id, actor, state_dir, user_inputs,
                    adapter_settings=None, history=None):
    """Called under the run-directory lock. Completed stages are never re-executed."""
    inputs = proposal.normalize_inputs(user_inputs)
    path = Path(state_dir) / CALIBRATION_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tree, groups = _papers(service, iteration_id)
    split = tree['protocol']['splits']
    pool = sorted(c for g in groups for c in g)
    identity = dict(iteration_id=iteration_id, actor=actor, seed=seed.content_hash,
                    calibration_k=inputs['calibration_k'], user_inputs=inputs,
                    input=tree['protocol']['input'], mode=tree['protocol']['mode'],
                    pool_sha256=canonical_hash(pool), papers_sha256=canonical_hash(groups),
                    adapter_settings=adapter_settings or {})
    saved = _load(path) if path.exists() else None
    if saved and saved['identity'] != identity:
        raise ValueError('calibration identity differs; use its original inputs and seed')
    if saved and saved.get('complete'):
        # Recover the crash window between the complete calibration and proposal writes.
        if not (path.parent / PROPOSAL_FILE).exists():
            ledger_io.atomic_write_json(path.parent / PROPOSAL_FILE,
                proposal.rule_defaults(saved['summary'], inputs, saved['history']).to_dict())
        return saved
    if split.get('status') != 'pending':
        raise ValueError('calibration requires a new deferred iteration')
    if len(groups) < 2 or len(pool) < 3:
        raise ValueError('calibration needs two whole papers and at least two probe cases plus diagnosis')
    if saved is None:
        probe = split.get('probe') or _take(groups, 40 if len(pool) >= 134 else max(2, math.ceil(.3 * len(pool))), leave=1)
        if len(probe) < 2:
            raise ValueError('whole-paper probe needs at least two cases')
        remaining = [g for g in groups if not set(g) & set(probe)]
        diagnosis = split.get('diagnosis') or remaining[0]
        saved = _save(path, dict(kind='method-v4.1-calibration', complete=False, identity=identity,
            probe=probe, diagnosis=diagnosis, history=dataclasses.asdict(_history(history)),
            results={}, walls={}, profiles={}))
    service.register_calibration_samples(iteration_id, saved['probe'], saved['diagnosis'], actor=actor)
    probe = saved['probe']
    if 'probe' not in saved['results']:
        t0 = time.monotonic()
        # Sequential increments establish real cold/warm order and durable roots;
        # the adapter tops up k rather than running the previous roots again.
        for k in range(1, inputs['calibration_k'] + 1):
            measured = adapter.evaluate_subset(seed, None, 'probe', probe, k)[0]
        saved['results']['probe'] = dataclasses.asdict(measured)
        saved['walls']['probe'] = time.monotonic() - t0
        saved['profiles']['probe'] = getattr(adapter, 'timing_profile', lambda r: [])(measured)
        saved = _save(path, saved)
    measured = EvalResult(**saved['results']['probe'])
    aa = calibrate._aa(measured)
    if aa['rerun_var'] is None:
        raise ValueError('calibration needs two probe tasks with at least two official scores')
    scores = list(measured.per_task_scores.values())
    failure_rate = sum(x < .5 for x in scores) / len(scores)
    remaining = [g for g in groups if not set(g) & set(probe)]
    want = math.ceil(22.5 / failure_rate) if failure_rate else 15
    diagnosis = sorted(set(saved['diagnosis']) | set(_take(remaining, want)))
    service.register_calibration_samples(iteration_id, probe, diagnosis, actor=actor)
    saved['diagnosis'] = diagnosis
    if 'diagnosis' not in saved['results']:
        saved = _save(path, saved)
        t0 = time.monotonic()
        result = adapter.evaluate_search(seed, diagnosis, 1)
        saved['results']['diagnosis'] = dataclasses.asdict(result)
        saved['walls']['diagnosis'] = time.monotonic() - t0
        saved['profiles']['diagnosis'] = getattr(adapter, 'timing_profile', lambda r: [])(result)
        saved = _save(path, saved)
    sg = adapter.observe_safeguards(measured, measured)
    noise = decide.safeguard_noise(sg)
    timing, timing_note = _timing(saved['profiles']['probe'] + saved['profiles']['diagnosis'],
                                 saved['walls']['probe'], len(probe), inputs['calibration_k'])
    tokens = (sg.get('tokens_per_task_ratio') or {}).get('means', {}).get('subject')
    remaining = [g for g in remaining if not set(g) & set(diagnosis)]
    summary = CalibrationSummary(rerun_var=aa['rerun_var'], n_probe=len(probe), k=inputs['calibration_k'],
        seed_fail_rate=failure_rate, constant_fraction=aa['constant_fraction'],
        infra_fail_rate=sum(v == 'infra_exhausted' for v in measured.error_classes.values()) / len(probe),
        tokens_per_task=tokens, safeguard_var={k: v['var'] for k, v in noise.items() if v['var'] is not None},
        pool_size=len(pool), n_diagnosis=len(diagnosis),
        remaining_paper_sizes=tuple(map(len, remaining)), **timing)
    saved.update(complete=True, summary=summary.canonical(),
        assumptions=[timing_note, 'concurrency scaling not measured'] + ([] if tokens is not None else ['token usage unavailable; spend forecast unavailable']),
        calibration=dict(rerun_var=aa['rerun_var'], validation=aa, safeguard_noise=noise,
                         walls=saved['walls'], safeguards=sg))
    saved = _save(path, saved)
    suggested = proposal.rule_defaults(summary, inputs, saved['history'])
    suggested.notes['measurement_assumptions'] = saved['assumptions']
    ledger_io.atomic_write_json(path.parent / PROPOSAL_FILE, suggested.to_dict())
    return saved


def materialize_split(bundle, groups, validation_size):
    """Deterministic whole-paper subset sum; explicit sizes are never rounded."""
    probe, diagnosis = bundle['probe'], bundle['diagnosis']
    remaining = [g for g in groups if not set(g) & set(probe + diagnosis)]
    choices = {0: []}
    for group in remaining:
        for n, selected in list(choices.items()):
            choices.setdefault(n + len(group), selected + group)
    extra = validation_size - len(probe)
    if extra not in choices:
        nearest = sorted((len(probe) + n for n in choices), key=lambda n: (abs(n - validation_size), n))[:2]
        raise ValueError(f'validation_size cannot split papers; nearest feasible sizes: {nearest}')
    validation = sorted(probe + choices[extra])
    search = sorted({c for g in groups for c in g} - set(validation))
    return search, validation


def freeze_run(service, iteration_id, actor, state_dir, proposed=None):
    """Write intent before the idempotent service freeze; recover either crash window."""
    root = Path(state_dir)
    bundle = load_bundle(root / CALIBRATION_FILE)
    if bundle['identity']['iteration_id'] != iteration_id or bundle['identity']['actor'] != actor:
        raise ValueError('calibration belongs to a different iteration or actor')
    proposed = proposed or proposal.Proposal.from_dict(ledger_io.read_json(root / PROPOSAL_FILE))
    frozen = proposal.freeze(proposed, bundle['summary'], bundle['history'])
    tree, groups = _papers(service, iteration_id)
    if (tree['protocol']['input'] != bundle['identity']['input']
            or canonical_hash(groups) != bundle['identity']['papers_sha256']):
        raise ValueError('pool or source changed since calibration')
    search, validation = materialize_split(bundle, groups, frozen['config']['split']['validation_size'])
    if frozen['config']['diagnose_size'] > len(search):
        raise ValueError('diagnose_size exceeds the frozen search set')
    frozen.update(calibration_sha256=bundle['payload_sha256'], proposal_sha256=canonical_hash(proposed.to_dict()),
                  iteration_id=iteration_id, seed=bundle['identity']['seed'],
                  split=dict(search=search, validation=validation))
    path = root / RUN_CONFIG_FILE
    if path.exists():
        old = _load(path)
        if {k: v for k, v in old.items() if k != 'payload_sha256'} != frozen:
            raise ValueError('frozen configuration or proposal changed; resume uses the original values')
        frozen = old
    else:
        frozen = _save(path, frozen)
    service.freeze_split(iteration_id, search, validation, actor=actor)
    return frozen, bundle


def runtime_config(frozen, bundle):
    config = copy.deepcopy(frozen['config'])
    config.pop('split')
    config.update(time_budget_hours=frozen['inputs']['time_budget_hours'],
                  min_meaningful_improvement=frozen['inputs'].get('delta'))
    config['adapter'] = dict(bundle['identity'].get('adapter_settings') or {}, **config.get('adapter', {}))
    config['adapter']['max_concurrent_measurements'] = config['max_concurrent_executions']
    # Polling cannot terminate an execution earlier than its frozen timeout.
    config['adapter']['eval_timeout'] = config['adapter']['execution_timeout_seconds'] + 60
    return config


def import_baseline(adapter, seed, cfg, partitions, bundle, state_dir):
    if seed.content_hash != bundle['identity']['seed'] or cfg.calibration_k != bundle['identity']['calibration_k']:
        raise ValueError('calibration seed or k differs from the run')
    probe, diagnosis = set(bundle['probe']), set(bundle['diagnosis'])
    if not probe <= set(partitions['validation']) or not diagnosis <= set(partitions['search']):
        raise ValueError('frozen split violates the calibration samples')
    base = EvalResult(**bundle['results']['probe'])
    remaining = sorted(set(partitions['validation']) - probe)
    if remaining:
        path = Path(state_dir) / 'baseline-remainder.json' if state_dir else None
        identity = dict(seed=seed.content_hash, tasks=remaining, calibration=bundle.get('payload_sha256'))
        saved = _load(path) if path and path.exists() else None
        if saved and saved['identity'] != identity:
            raise ValueError('baseline remainder identity changed')
        if saved:
            extra = EvalResult(**saved['result'])
        else:
            extra = adapter.evaluate_subset(seed, None, 'rest', remaining, 1)[0]
            if path:
                _save(path, dict(identity=identity, result=dataclasses.asdict(extra)))
        base = decide.combine(base, extra)
    return dict(search=EvalResult(**bundle['results']['diagnosis']),
                mb=decide.restrict(base, partitions['seed_minibatch']),
                rest=decide.restrict(base, partitions['seed_rest']))


def forecast(frozen, bundle, ledger=None):
    params = {proposal.SIM_KEYS[k]: v for k, v in frozen['config'].items() if k in proposal.SIM_KEYS}
    params['validation_size'] = frozen['config']['split']['validation_size']
    params.update({k: v for k, v in frozen['inputs'].items() if k in proposal.INPUT_SIM_KEYS})
    history = _history(copy.deepcopy(bundle.get('history')))
    for rd in (ledger or {}).get('rounds', []):
        if rd.get('analysis_s') is not None:
            history.analysis_s.append(rd['analysis_s'])
        records = rd.get('records') or {}
        for rec in records.values() if isinstance(records, dict) else records:
            timings = rec.get('timings') or {}
            if timings.get('agent_s') is not None:
                history.agent_s.append(timings['agent_s'])
            for stage in ('smoke', 'minibatch'):
                if stage + '_s' in timings:
                    history.reached[stage] = history.reached.get(stage, 0) + 1
                    history.eliminated[stage] = history.eliminated.get(stage, 0) + int(rec.get('stage') == stage + '_fail')
    return simulate(params, bundle['summary'], history)


def public_summary(bundle):
    result = EvalResult(**bundle['results']['probe'])
    tasks = decide.attempts_by_task(result)
    rows = sorted(tasks.values(), key=statistics.mean)
    return dict(complete=bundle['complete'], summary=bundle['summary'], assumptions=bundle.get('assumptions', []),
        heatmap=[dict(row=i + 1, scores=v, mean=statistics.mean(v)) for i, v in enumerate(rows)],
        attempt_means=[statistics.mean(a.values()) if a else None for a in result.attempt_scores],
        aa_differences=[v[0] - statistics.mean(v[1:]) for v in rows if len(v) >= 2],
        aa_se=bundle['calibration']['validation'].get('aa_se'))
