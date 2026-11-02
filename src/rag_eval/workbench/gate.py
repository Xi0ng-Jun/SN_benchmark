"""Frozen LCD acceptance arithmetic; no execution or publication side effects."""
from __future__ import annotations

import math

from .clusters import FIELDS, _matches, validate_signature
from .noise import difference_band, paired_means

DEFAULT_RULE = dict(id='lcd-v1', tau=.5, eta='rerun_se', band='jeffreys90')


def validate_contract(value):
    if (not isinstance(value, dict) or set(value) - {'cluster_id', 'predicted_flips', 'guards', 'tau'}
            or not {'predicted_flips', 'guards'} <= set(value)):
        raise ValueError('Contract needs predicted_flips and guards, with optional cluster_id and tau')
    out = dict(value)
    for field in ('predicted_flips', 'guards'):
        ids = out[field]
        if (not isinstance(ids, list) or any(type(c) is not str or not c.strip() for c in ids)
                or len(set(ids)) != len(ids) or len(ids) > 200):
            raise ValueError('Contract case lists must contain distinct identities')
    if not out['predicted_flips'] or set(out['predicted_flips']) & set(out['guards']):
        raise ValueError('Contract flips must be nonempty and separate from guards')
    if 'cluster_id' in out and (type(out['cluster_id']) is not str or not out['cluster_id'].strip()):
        raise ValueError('Invalid cluster_id')
    if 'tau' in out and (type(out['tau']) not in (int, float) or not math.isfinite(out['tau']) or not 0 <= out['tau'] <= 1):
        raise ValueError('Contract tau must be finite in [0, 1]')
    return out


def judge(comparison, noise, contract, rule):
    """Return the full arithmetic trace, preserving missing evidence as unknown."""
    contract = validate_contract(contract)
    if (not isinstance(rule, dict) or type(rule.get('tau')) not in (int, float)
            or not math.isfinite(rule['tau']) or not 0 <= rule['tau'] <= 1
            or rule != dict(DEFAULT_RULE, tau=rule['tau'])):
        raise ValueError('Unsupported gate rule')
    threshold = noise.get('reference', {}).get('success_threshold', .5)
    tau, eta = contract.get('tau', rule['tau']), noise.get('eta')
    rows = {r['case_id']: r for r in comparison.get('rows', [])}
    delta = comparison.get('validation', comparison.get('mean', {})).get('delta')
    result = dict(rule_id=rule['id'], rule=dict(rule), tau=tau, eta=eta, delta=delta,
                  success_threshold=threshold, rho=None, flips=[], guards=[], guard_broken=[],
                  infrastructure=None, verdict='unmeasurable', reasons=[],
                  # Disclosure only: in-attempt re-asks of each side never change the verdict.
                  # The infrastructure check below still counts first-answer failures,
                  # including those a re-ask later answered.
                  case_retry=comparison.get('retry_summary'),
                  evidence=dict(noise=noise, mean=comparison.get('mean'),
                                validation=comparison.get('validation'), rows=comparison.get('rows', []),
                                attempts_used=comparison.get('attempts_used')))
    incomplete = False
    for field, ids in [('flips', contract['predicted_flips']), ('guards', contract['guards'])]:
        for cid in ids:
            row = rows.get(cid, {})
            left = noise.get('reference', {}).get('cases', {}).get(cid, {})
            right = noise.get('subject', {}).get('cases', {}).get(cid, {})
            known = all(v is not None for v in (row.get('left'), row.get('right'), row.get('delta')))
            known = known and left.get('n', 0) >= noise.get('reference', {}).get('k_base', 2)
            known = known and right.get('n', 0) >= noise.get('subject', {}).get('k_base', 2)
            band = difference_band(left['successes'], left['n'], right['successes'], right['n']) if known else None
            held = (row['left'] < threshold <= row['right'] and row['delta'] > band) if known else None
            broken = row['delta'] < -band if known else None
            result[field].append(dict(case_id=cid, left=row.get('left'), right=row.get('right'),
                                      delta=row.get('delta'), reference=left, subject=right, band=band,
                                      held=held if field == 'flips' else not broken if known else None))
            incomplete |= not known
            if field == 'guards' and broken:
                result['guard_broken'].append(cid)
    if all(r['held'] is not None for r in result['flips']):
        result['rho'] = sum(r['held'] for r in result['flips']) / len(result['flips'])
    infra = comparison.get('infrastructure')
    if infra:
        result['infrastructure'] = dict(reference=infra['reference'], subject=infra['subject'], delta=None, band=None)
    if infra and all(infra[s]['total'] for s in ('reference', 'subject')):
        left, right = infra['reference'], infra['subject']
        change = right['failed'] / right['total'] - left['failed'] / left['total']
        band = difference_band(left['failed'], left['total'], right['failed'], right['total'])
        result['infrastructure'] = dict(reference=left, subject=right, delta=change, band=band)
        if change > band:
            result['reasons'].append('infrastructure_failure_increase')
    if infra and any(infra[s].get('unobserved') for s in ('reference', 'subject')):
        result['reasons'].append('unobserved_infrastructure_outcomes')
    if (incomplete or any(r.get('delta') is None for r in comparison.get('rows', [])) or eta is None or delta is None or comparison.get('validation', {}).get('incomplete')
            or (comparison.get('admission') or {}).get('status') != 'admitted'
            or any(noise.get(s, {}).get('k_below_base', True) for s in ('reference', 'subject'))):
        result['reasons'].append('insufficient_paired_reruns')
    if result['reasons']:
        return result
    rho = result['rho']
    if delta >= -eta and rho >= tau and not result['guard_broken']:
        result.update(verdict='accept', reasons=['contract_fulfilled_without_guard_regression'])
    elif delta > eta and rho < tau:
        result.update(verdict='audit', reasons=['gain_outside_contract'])
    else:
        result.update(verdict='reject', reasons=['guard_broken' if result['guard_broken'] else 'contract_or_global_delta_failed'])
    return result


# ---------------------------------------------------------------- lcd-v2
# Adoption is the paired raw-score net utility on independent tasks; the mechanism
# hypothesis only earns an attribution label. Selected per iteration protocol.
RULE_V2 = dict(id='lcd-v2', z_screen=.84, z_confirm=1.65, statistic='paired_raw', safeguards='required')
# Fixed safeguard slots; every other pre-registered key is an anti-gaming metric (at least one).
SAFEGUARD_SLOTS = ('infrastructure_failure_rate', 'cost_per_task')
# Parent-trajectory behaviour fields: no case identity, score or gold-derived value.
TRAJECTORY_FIELDS = (FIELDS | {'retrieval_context.*.text'}) - {'deterministic.evidence_hit',
    'stage_metrics.gold_evidence_recall.value', 'stage_metrics.context_precision.value',
    'stage_metrics.citation_precision.value'}


def _predicate(value, name):
    """A closed cluster-signature predicate restricted to behaviour fields."""
    try:
        value = validate_signature(value)
    except ValueError as exc:
        raise ValueError(f'contract invalid: {name}: {exc}') from exc
    def fields(s):
        children = s.get('all_of', s.get('any_of', [s['not']] if 'not' in s else []))
        return [f for c in children for f in fields(c)] + ([s['field']] if 'field' in s else [])
    if set(fields(value)) - TRAJECTORY_FIELDS:
        raise ValueError(f'contract invalid: {name} may only read parent-trajectory behaviour fields')
    return value


def validate_mechanism(value):
    """Mechanism hypothesis: slice predicate P, manipulation prediction M, slice effect E."""
    if not isinstance(value, dict) or set(value) != {'slice_predicate', 'manipulation', 'slice_effect'}:
        raise ValueError('contract invalid: needs exactly slice_predicate, manipulation and slice_effect')
    out = dict(slice_predicate=_predicate(value['slice_predicate'], 'slice_predicate'))
    m, e = value['manipulation'], value['slice_effect']
    if (not isinstance(m, dict) or not {'direction', 'threshold'} <= set(m) <= {'feature', 'direction', 'threshold'}
            or m['direction'] not in ('decrease', 'increase') or type(m['threshold']) not in (int, float)
            or not 0 <= m['threshold'] <= 1):
        raise ValueError('contract invalid: manipulation is {feature?, direction: decrease|increase, threshold in [0, 1]}')
    # The measured feature defaults to P itself and is stored explicitly.
    out['manipulation'] = dict(feature=_predicate(m['feature'], 'manipulation.feature') if 'feature' in m
                               else out['slice_predicate'], direction=m['direction'], threshold=float(m['threshold']))
    magnitude = e.get('magnitude') if isinstance(e, dict) else None
    if (not isinstance(e, dict) or not {'direction'} <= set(e) <= {'direction', 'magnitude'} or e['direction'] != 'positive'
            or magnitude is not None and (type(magnitude) is not list or len(magnitude) != 2
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in magnitude) or magnitude[0] > magnitude[1])):
        raise ValueError('contract invalid: slice_effect is {direction: positive, magnitude?: [lo, hi] | null}')
    out['slice_effect'] = dict(direction='positive', magnitude=[float(v) for v in magnitude] if magnitude else None)
    return out


def validate_safeguards(value):
    """Pre-registered non-inferiority margins {key, direction, margin} in metric units."""
    if not isinstance(value, list) or any(not isinstance(s, dict) or set(s) != {'key', 'direction', 'margin'}
            or type(s['key']) is not str or not s['key'].strip()
            or s['direction'] not in ('higher_is_better', 'lower_is_better')
            or type(s['margin']) not in (int, float) or not 0 <= s['margin'] < math.inf for s in value):
        raise ValueError('Safeguards are [{key, direction: higher_is_better|lower_is_better, margin >= 0}]')
    keys = [s['key'] for s in value]
    if len(set(keys)) != len(keys) or not set(SAFEGUARD_SLOTS) < set(keys):
        raise ValueError('Safeguards need ' + ', '.join(SAFEGUARD_SLOTS) + ' and at least one anti-gaming metric')
    return [dict(s) for s in value]


def safeguard_checks(observed, infra, safeguards):
    """Pre-registered safeguards against one pair: (per-safeguard rows, blocking reasons).

    *observed*: key -> {reference, subject} (``comparisons.observe_safeguards``);
    *infra*: side -> {total, failed, unobserved} (``ComparisonService._infrastructure``),
    from which the infrastructure failure rate is derived when every execution
    published per-case results. A safeguard whose two sides are not both observed
    is ``safeguard_unobserved``; a harm beyond its margin is ``safeguard_breached``.
    Either blocks a verdict (unmeasurable).
    """
    specs, sides = validate_safeguards(safeguards), ('reference', 'subject')
    observed = dict(observed or {})
    if 'infrastructure_failure_rate' not in observed and infra and all(
            infra[s].get('total') and not infra[s].get('unobserved') for s in sides):
        observed['infrastructure_failure_rate'] = {s: infra[s]['failed'] / infra[s]['total'] for s in sides}
    rows, reasons = [], []
    for spec in specs:
        value = observed.get(spec['key']) or {}
        known = all(type(value.get(s)) in (int, float) and math.isfinite(value[s]) for s in sides)
        change = value['subject'] - value['reference'] if known else None
        harm = (-change if spec['direction'] == 'higher_is_better' else change) if known else None
        rows.append(dict(spec, reference=value.get('reference'), subject=value.get('subject'),
                         change=change, breached=harm > spec['margin'] if known else None))
        if not known or harm > spec['margin']:
            reasons.append(('safeguard_breached:' if known else 'safeguard_unobserved:') + spec['key'])
    return rows, reasons


def _v2(comparison, rule, z_key, safeguards):
    """Shared screen/confirm trace: safeguards, admission, then the paired statistic."""
    if (not isinstance(rule, dict) or set(rule) != set(RULE_V2)
            or any(rule[k] != RULE_V2[k] for k in ('id', 'statistic', 'safeguards'))
            or any(type(rule[k]) not in (int, float) or not 0 < rule[k] < math.inf for k in ('z_screen', 'z_confirm'))):
        raise ValueError('Unsupported gate rule')
    sides, infra = ('reference', 'subject'), comparison.get('infrastructure')
    checks, blocking = safeguard_checks(comparison.get('safeguards'), infra, safeguards)
    rows = [r for r in comparison.get('rows', []) if 'validation' not in comparison or r.get('role') == 'validation']
    noise = comparison.get('noise') or {}
    k = min((noise.get(s) or {}).get('k') or 0 for s in sides) or None
    # Families are the comparison's task families (e.g. papers); without them every task is its own cluster.
    paired = paired_means({r['case_id']: r.get('left') for r in rows}, {r['case_id']: r.get('right') for r in rows},
                          z=rule[z_key], families=comparison.get('families'), k=k)
    result = dict(rule_id=rule['id'], rule=dict(rule), z=rule[z_key], verdict='unmeasurable', reasons=[],
                  **{key: paired[key] for key in ('delta', 'se', 'lower', 'upper', 'n', 'k')}, safeguards=[],
                  case_retry=comparison.get('retry_summary'),
                  evidence=dict(paired=paired, mean=comparison.get('mean'), validation=comparison.get('validation'),
                                rows=rows, infrastructure=infra, attempts_used=comparison.get('attempts_used'), noise=noise))
    result['safeguards'].extend(checks)
    result['reasons'].extend(blocking)
    if (comparison.get('admission') or {}).get('status') != 'admitted':
        result['reasons'].append('pair_not_admitted')
    if (comparison.get('validation') or {}).get('incomplete') or any(r.get('delta') is None for r in rows):
        result['reasons'].append('incomplete_pairs')
    if paired['se'] is None:
        result['reasons'].append('insufficient_paired_cases')
    return result


def screen(comparison, rule, *, safeguards):
    """Validation pool: the lenient one-sided lower bound must clear zero."""
    result = _v2(comparison, rule, 'z_screen', safeguards)
    if not result['reasons']:
        passed = result['lower'] > 0
        result.update(verdict='pass' if passed else 'fail', reasons=['lower_bound_above_zero' if passed else 'lower_bound_not_above_zero'])
    return dict(result, stage='screen')


def confirm(comparison, rule, *, safeguards, mechanism=None, n_min=5):
    """Fresh batch: holds / does_not_hold / insufficient_evidence; attribution is recorded, never used."""
    result = _v2(comparison, rule, 'z_confirm', safeguards)
    if not result['reasons']:
        verdict = 'holds' if result['lower'] > 0 else 'does_not_hold' if result['upper'] <= 0 else 'insufficient_evidence'
        result.update(verdict=verdict, reasons=[dict(holds='lower_bound_above_zero', does_not_hold='upper_bound_not_above_zero',
                                                     insufficient_evidence='bounds_straddle_zero')[verdict]])
    result['attribution'] = attribute(comparison, mechanism, n_min=n_min) if mechanism is not None else None
    return dict(result, stage='confirm')


def attribute(comparison, mechanism, *, n_min=5):
    """Label the mechanism on parent-fixed slice membership; no verdict reads this."""
    mechanism = validate_mechanism(mechanism)
    traces = comparison.get('trajectories') or {}
    parent, child = traces.get('reference') or {}, traces.get('subject') or {}
    deltas = {r['case_id']: r['delta'] for r in comparison.get('rows', []) if r.get('delta') is not None
              and ('validation' not in comparison or r.get('role') == 'validation')}
    unobserved = sorted(cid for cid in deltas if not parent.get(cid))
    # Union over parent attempts, as in cluster scans; candidate outputs never re-select members.
    members = sorted(cid for cid in deltas if any(_matches(mechanism['slice_predicate'], o) for o in parent.get(cid, [])))
    outside = sorted(set(deltas) - set(members) - set(unobserved))
    feature, direction = mechanism['manipulation']['feature'], mechanism['manipulation']['direction']
    def rate(side):
        hits = [_matches(feature, o) for cid in members for o in side.get(cid, [])]
        return sum(hits) / len(hits) if hits else None
    def mean(ids):
        return sum(deltas[c] for c in ids) / len(ids) if ids else None
    r_p, r_c, d_s, d_o = rate(parent), rate(child), mean(members), mean(outside)
    change = None if r_p is None or r_c is None else r_p - r_c if direction == 'decrease' else r_c - r_p
    manipulated = None if change is None else change > 0 and change >= mechanism['manipulation']['threshold']
    if unobserved or len(members) < n_min or manipulated is None:
        label = 'insufficient'
    elif not manipulated or d_s <= 0 or d_o is not None and len(outside) * d_o > len(members) * d_s:
        label = 'refuted'
    else:
        label = 'supported' if d_o is None or d_s > d_o else 'insufficient'
    return dict(label=label, manipulation_rate_parent=r_p, manipulation_rate_candidate=r_c, manipulation_change=change,
                manipulated=manipulated, delta_slice=d_s, delta_outside=d_o, slice_size=len(members),
                outside_size=len(outside), n_min=n_min, slice=members, unobserved=unobserved, mechanism=mechanism)
