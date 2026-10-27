"""Frozen LCD acceptance arithmetic; no execution or publication side effects."""
from __future__ import annotations

import math

from .noise import difference_band

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
