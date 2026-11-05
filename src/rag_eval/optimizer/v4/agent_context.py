"""Small, provenance-aware feedback for the next diagnosis; no validation text."""
from __future__ import annotations

import hashlib

OUTCOMES = {
    'hypothesis_refuted': 'implementer reports the hypothesis did not hold in source/local checks',
    'already_satisfied': 'implementer reports the proposed behavior already exists',
    'hypothesis_inconclusive': 'local checks were inconclusive; the hypothesis is not disproved',
    'mechanism_changed_gain_insufficient': 'the mechanical behavior check changed as predicted, but gain was insufficient',
    'mechanism_not_observed': 'the mechanical behavior check did not show the predicted change',
    'mechanism_unknown': 'mechanism was not established; insufficient gain alone does not refute the hypothesis',
    'execution_unavailable': 'evaluation did not produce usable evidence; the hypothesis is untested',
    'implementation_failed': 'implementation did not finish; the hypothesis is untested',
    'guardrail_breached': 'a pre-registered safeguard blocked adoption',
}


def feedback(rec: dict, label: str) -> dict:
    """Whitelist discrete validation conclusions and pre-measurement implementer reports only."""
    out = {}
    if label == 'preflight_skipped':
        report = rec.get('preflight') or {}
        reason = report.get('reason')
        out['outcome'] = {'refuted': 'hypothesis_refuted', 'already_satisfied': 'already_satisfied',
                          'inconclusive': 'hypothesis_inconclusive'}.get(reason, 'hypothesis_inconclusive')
        out['implementation_feedback'] = {
            'source': 'implementer_report_before_measurement',
            'summary': str(report.get('summary') or '')[:1200],
            'checks': [{k: str(c.get(k) or '')[:600] for k in ('kind', 'target', 'observation')}
                       for c in (report.get('checks') or [])[:4] if isinstance(c, dict)],
        }
    elif label == 'run_failed':
        out['outcome'] = 'execution_unavailable'
    elif label == 'implementation_failed':
        out['outcome'] = 'implementation_failed'
    elif label == 'safeguard_breached':
        out['outcome'] = 'guardrail_breached'
    elif label in ('below_threshold', 'smoke_fail', 'minibatch_fail'):
        status = (((rec.get('mechanism') or {}).get('mechanical') or {}).get('status'))
        if status == 'changed':
            out['outcome'] = 'mechanism_changed_gain_insufficient'
        elif status == 'unchanged' or (rec.get('smoke') or {}).get('verdict') == 'fail':
            out['outcome'] = 'mechanism_not_observed'
        else:
            out['outcome'] = 'mechanism_unknown'
    return out


def feedback_text(entry: dict) -> str:
    text = OUTCOMES.get(entry.get('outcome'), '')
    # Only source/local-check reports emitted before any measurement can carry free text.
    report = entry.get('implementation_feedback') or {}
    if entry.get('label') == 'preflight_skipped' and report.get('source') == 'implementer_report_before_measurement':
        summary = ' '.join(str(report.get('summary') or '').split())[:500]
        checks = [' '.join(f"{c.get('kind')}: {c.get('target')}: {c.get('observation')}".split())[:300]
                  for c in report.get('checks', [])[:2] if c.get('kind') in ('source', 'test', 'probe')]
        text += '; implementer-reported: ' + summary
        if checks:
            text += '; checks: ' + ' | '.join(checks)
    return text


def analysis_history(adapter, history: list[dict], budget: int, parent_id: str):
    """Keep the complete adopted inventory in its own immutable file; trim only old trials."""
    from ..sn_adapter import _render_history
    current = [e for e in history if e.get('label') == 'current_change']
    recent = [e for e in history if e.get('label') != 'current_change']
    # Parent identifiers are not filesystem paths, even for non-SN adapters.
    tag = hashlib.sha256(parent_id.encode()).hexdigest()[:20]
    lines = ['# Changes included in this parent',
             'This inventory follows the adopted lineage, not a history of rejected proposals.',
             'Descriptions record the intended changes; acceptance does not prove every claimed mechanism.', '']
    for entry in current:
        title = ' '.join(str(entry.get('title') or '').split())[:160]
        change = ' '.join(str(entry.get('change') or '').split())[:600]
        lines.append(f'- {title}: {change}')
    if not current:
        lines.append('No changes have been adopted in this run yet.')
    path, digest, _ = adapter._write_evidence(f'adopted-{tag}.md', '\n'.join(lines) + '\n')
    pointer = f'Current adopted changes: read {path} (sha256 {digest}). This inventory is not trimmed.\n\n'
    text, shown, omitted = _render_history(recent, max(0, budget - len(pointer)), vocabulary='v4')
    return pointer + text, shown, omitted
