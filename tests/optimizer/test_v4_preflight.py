"""V4 implementation feedback is candidate-bound and precedes materialization."""
import json
from types import SimpleNamespace

import pytest

from rag_eval.optimizer.adapter import Card
from rag_eval.optimizer.v4.sn_adapter_v4 import SNAdapterV4
from rag_eval.optimizer.sn_trajectory import diagnostic_view, render_views
from rag_eval.workbench.hypotheses import validate_card
from .test_sn_versions import _FakeService, _FakeRoundOps, CONFIG, CARD


def setup_adapter(tmp_path, monkeypatch):
    service = _FakeService(ready_after=1)
    ops = _FakeRoundOps(service)
    adapter = SNAdapterV4(CONFIG, round_operations=ops, evidence_dir=tmp_path, agent_poll_interval=0)
    adapter._approve = lambda *args: {'status': 'approved'}
    ws = adapter.workspace(adapter.version_for('root'), CARD)
    monkeypatch.setattr('rag_eval.optimizer.sn_adapter._extract_worktree_diff', lambda _: 'actual diff')
    return adapter, ws, service


def report(ws, **changes):
    out = dict({key: ws[key] for key in ('candidate_id', 'hypothesis_id', 'parent_candidate_id')},
               decision='implement', reason='supported', summary='Query branch reproduced locally.',
               checks=[dict(kind='source', target='backend/query.py:19', observation='No retry branch exists.')])
    out.update(changes)
    return out


def test_skip_after_idle_precedes_diff_extraction(tmp_path, monkeypatch):
    from rag_eval.optimizer import adapter as api
    adapter, ws, service = setup_adapter(tmp_path, monkeypatch)
    data = report(ws, decision='skip', reason='already_satisfied')
    def idle(*args):
        adapter._preflight_path(ws).write_text(json.dumps(data))
    monkeypatch.setattr(adapter, '_poll_agent', idle)
    monkeypatch.setattr('rag_eval.optimizer.sn_adapter._extract_worktree_diff', lambda _: pytest.fail('skip read diff'))
    with pytest.raises(api.ImplementationDeclined) as caught:
        adapter.implement(CARD, ws)
    assert caught.value.report['decision'] == 'skip'
    assert not service.freezes


@pytest.mark.parametrize('change', [None, {'checks': []}, {'decision': 'skip', 'checks': []}, {'candidate_id': 'foreign'},
    {'hypothesis_id': 'other'}, {'parent_candidate_id': 'other'}, {'decision': 'implement', 'reason': 'refuted'},
    {'checks': [dict(kind='probe', target='', observation='done')]}, {'unexpected': True}])
def test_bad_or_missing_report_fails_explicitly(tmp_path, monkeypatch, change):
    adapter, ws, _ = setup_adapter(tmp_path, monkeypatch)
    def idle(*args):
        if change is not None:
            adapter._preflight_path(ws).write_text(json.dumps(report(ws, **change)))
    monkeypatch.setattr(adapter, '_poll_agent', idle)
    with pytest.raises((ValueError, RuntimeError), match='preflight'):
        adapter.implement(CARD, ws)


def test_implemented_report_survives_frozen_replay_and_mutable_report(tmp_path, monkeypatch):
    adapter, ws, service = setup_adapter(tmp_path, monkeypatch)
    monkeypatch.setattr(adapter, '_poll_agent', lambda *args: adapter._preflight_path(ws).write_text(json.dumps(report(ws))))
    first = adapter.implement(CARD, ws)
    assert first.metadata['preflight']['summary'] == report(ws)['summary']
    service.candidates[ws['candidate_id']]['status'] = 'frozen'
    adapter._preflight_path(ws).write_text(json.dumps(report(ws, summary='late mutable output')))
    monkeypatch.setattr(adapter, '_poll_agent', lambda *args: pytest.fail('redispatched'))
    monkeypatch.setattr('rag_eval.optimizer.sn_adapter._extract_frozen_diff', lambda _: 'actual diff')
    second = adapter.implement(CARD, ws)
    assert second == first and second.metadata == first.metadata


def test_search_refs_are_opaque_optional_and_unique():
    card = dict(title='t', component='retrieval', mechanism='m', predictions=[], falsification='f', implementer_brief='b')
    assert 'search_refs' not in validate_card(card, {'id': 'lcd-v2'})
    assert validate_card(dict(card, search_refs=['c1', 'c8']), {'id': 'lcd-v2'})['search_refs'] == ['c1', 'c8']
    for refs in (['qasper:raw'], ['c1', 'c1'], 'c1', ['c0']):
        with pytest.raises(ValueError, match='search_refs'):
            validate_card(dict(card, search_refs=refs), {'id': 'lcd-v2'})


def evidence(adapter, tmp_path):
    items = [(f'c{i}', 'failure', diagnostic_view(dict(status='success', prediction=f'answer-{i}',
              product_record=dict(question=f'question-{i}')))) for i in (1, 2, 3)]
    shown, _, _ = render_views(items[:2], 50000)
    path, digest, reused = adapter._write_analysis_evidence(
        'round-1-parent.md', '# evidence\n' + shown, items, ['raw-1', 'raw-2', 'raw-3'], 2)
    adapter._analysis_evidence_ready(path, digest, items, ['raw-1', 'raw-2', 'raw-3'], 2, reused)
    return path, digest, items


def test_cited_only_evidence_and_immutable_round_mapping(tmp_path):
    adapter = SNAdapterV4(CONFIG, evidence_dir=tmp_path)
    raw = {'search_refs': ['c2']}
    adapter._hypotheses = SimpleNamespace(get=lambda *args: {'card': raw})
    path, digest, items = evidence(adapter, tmp_path)
    card = Card('failure', 'change', ref='h')
    adapter._cards_analysed([card], path, digest)
    index = json.loads((tmp_path / 'card-evidence.json').read_text())['h']
    text = __import__('pathlib').Path(index['path']).read_text()
    assert 'question-2' in text and 'question-1' not in text and 'question-3' not in text
    assert 'raw-2' not in text
    assert index['search_refs'] == ['c2']
    # Replaying with a reordered live input must retain the original mapping.
    adapter._analysis_evidence_ready(path, digest, items[::-1], ['changed'] * 3, 2, True)
    adapter._cards_analysed([card], path, digest)
    assert json.loads((tmp_path / 'card-evidence.json').read_text())['h'] == index
    # An omitted reference is not eligible merely because it was selected.
    raw['search_refs'] = ['c3']
    with pytest.raises(ValueError, match='search_refs'):
        adapter._cards_analysed([Card('x', 'y', ref='other')], path, digest)


@pytest.mark.parametrize('decision', [[], {}, None])
def test_malformed_decision_is_a_report_validation_error(tmp_path, monkeypatch, decision):
    adapter, ws, _ = setup_adapter(tmp_path, monkeypatch)
    monkeypatch.setattr(adapter, '_poll_agent', lambda *args: adapter._preflight_path(ws).write_text(
        json.dumps(report(ws, decision=decision))))
    with pytest.raises(ValueError, match='preflight'):
        adapter.implement(CARD, ws)


def test_repair_reports_use_refiled_hypothesis_and_replay(tmp_path, monkeypatch):
    from rag_eval.optimizer.adapter import EvalResult, Version
    adapter, ws, service = setup_adapter(tmp_path, monkeypatch)
    service.candidates['repair'] = dict(id='repair', status='ready', parent_candidate_id='root',
                                       proposal_id='copied-H1', worktree=dict(path='/unused', status='ready'))
    adapter._round_ops.derive = lambda *args, **kwargs: 'repair'
    monkeypatch.setattr(adapter, '_apply_source', lambda *args: {'status': 'applied'})
    repair_ws = dict(candidate_id='repair', parent_candidate_id='root', hypothesis_id='copied-H1')
    calls = []
    def poll(role, cid, call):
        calls.append(call)
        adapter._preflight_path(repair_ws).write_text(json.dumps(report(repair_ws)))
    monkeypatch.setattr(adapter, '_poll_agent', poll)
    empty = EvalResult({}, {}, {}, {})
    kwargs = dict(smoke=None, candidate_smoke=empty, parent_smoke=empty, attempt=1)
    failed = Version('failed', metadata={'candidate_id': ws['candidate_id']})
    first = adapter.repair(CARD, adapter.version_for('root'), failed, **kwargs)
    assert first.metadata['preflight']['hypothesis_id'] == 'copied-H1'
    assert str(adapter._preflight_path(repair_ws)) in calls[0]['text']
    assert 'read no other file under var/' not in calls[0]['text']
    service.candidates['repair']['status'] = 'frozen'
    monkeypatch.setattr(adapter, '_poll_agent', lambda *args: pytest.fail('repair replay redispatch'))
    monkeypatch.setattr('rag_eval.optimizer.sn_adapter._extract_frozen_diff', lambda _: 'actual diff')
    again = adapter.repair(CARD, adapter.version_for('root'), failed, **kwargs)
    assert again.metadata == first.metadata and again.content == first.content


def test_report_bytes_bound_and_invalid_json_are_explicit(tmp_path, monkeypatch):
    adapter, ws, _ = setup_adapter(tmp_path, monkeypatch)
    for content in ('{bad json', 'x' * 16385):
        monkeypatch.setattr(adapter, '_poll_agent', lambda *args: adapter._preflight_path(ws).write_text(content))
        with pytest.raises(ValueError, match='preflight'):
            adapter.implement(CARD, ws)


def test_round_manifest_is_created_before_dispatch_and_reused(tmp_path, monkeypatch):
    from .test_sn_trajectory import _FakeHypotheses, _FakeRoundOps as AnalysisOps
    hyps = _FakeHypotheses()
    hyps.get = lambda task, ref: next(row for row in hyps.records if row['id'] == ref)
    ops = AnalysisOps(hyps)
    original_agent = ops.agent
    def agent(*args, **kwargs):
        assert (tmp_path / 'round-1-P.cases.json').exists()
        result = original_agent(*args, **kwargs)
        for row in hyps.records:
            if row['id'] == 'new1':
                row['card']['search_refs'] = ['c1']
        return result
    ops.agent = agent
    adapter = SNAdapterV4(dict(CONFIG, parent_candidate_id='P'), evidence_dir=tmp_path,
                          hypotheses=hyps, round_operations=ops)
    monkeypatch.setattr(adapter, 'diagnostic_views', lambda _: {'raw': diagnostic_view(dict(
        status='success', prediction='answer', product_record=dict(question='seen-question')))})
    adapter.analyse({'raw': []}, 1, round_id=1)
    assert 'bounded `source' in ops.texts[0] and 'search_refs' in ops.texts[0]
    assert 'It is your only input:' not in ops.texts[0]
    assert 'observations' in ops.texts[0] and 'falsification' in ops.texts[0]
    bound = json.loads((tmp_path / 'card-evidence.json').read_text())
    monkeypatch.setattr(adapter, 'diagnostic_views', lambda _: {'changed': diagnostic_view(dict(
        status='success', prediction='answer', product_record=dict(question='not-seen')))})
    adapter.analyse({'changed': []}, 1, round_id=1)
    assert json.loads((tmp_path / 'card-evidence.json').read_text()) == bound


def test_accepted_skip_cannot_be_changed_by_late_agent_output(tmp_path, monkeypatch):
    from rag_eval.optimizer.adapter import ImplementationDeclined
    adapter, ws, _ = setup_adapter(tmp_path, monkeypatch)
    data = report(ws, decision='skip', reason='refuted')
    monkeypatch.setattr(adapter, '_poll_agent', lambda *args: adapter._preflight_path(ws).write_text(json.dumps(data)))
    with pytest.raises(ImplementationDeclined) as first:
        adapter.implement(CARD, ws)
    data = report(ws)
    with pytest.raises(ImplementationDeclined) as second:
        adapter.implement(CARD, ws)
    assert second.value.report == first.value.report


def test_evidence_provenance_cannot_be_rebound_or_silently_edited(tmp_path):
    adapter = SNAdapterV4(CONFIG, evidence_dir=tmp_path)
    adapter._hypotheses = SimpleNamespace(get=lambda *args: {'card': {'search_refs': ['c1']}})
    path, digest, items = evidence(adapter, tmp_path)
    card = Card('failure', 'change', ref='h')
    adapter._cards_analysed([card], path, digest)
    other, other_digest, _ = adapter._write_evidence('round-2-parent.md', 'different round')
    with pytest.raises(ValueError, match='another round'):
        adapter._cards_analysed([card], other, other_digest)
    path.write_text('edited')
    with pytest.raises(ValueError, match='evidence file changed'):
        adapter._cards_analysed([card], path, digest)


def test_legacy_missing_refs_are_explicitly_labelled(tmp_path):
    adapter = SNAdapterV4(CONFIG, evidence_dir=tmp_path)
    adapter._hypotheses = SimpleNamespace(get=lambda *args: {'card': {}})
    path, digest, _ = evidence(adapter, tmp_path)
    card = Card('failure', 'change', ref='h')
    adapter._cards_analysed([card], path, digest)
    text = adapter._implementer_extra_text(card, {})
    assert 'Legacy fallback' in text and 'no search_refs' in text
    entry = json.loads((tmp_path / 'card-evidence.json').read_text())['h']
    assert 'search_refs' not in entry and entry['legacy'] is True


def test_new_card_with_refs_cannot_fall_back_when_binding_is_missing(tmp_path):
    adapter = SNAdapterV4(CONFIG, evidence_dir=tmp_path)
    adapter._hypotheses = SimpleNamespace(get=lambda *args: {'card': {'search_refs': ['c1']}})
    with pytest.raises(ValueError, match='search_refs.*binding'):
        adapter._implementer_extra_text(Card('failure', 'change', ref='h'), {})


def test_staged_evidence_counts_as_published_for_staleness(tmp_path, monkeypatch):
    adapter = SNAdapterV4(CONFIG, evidence_dir=tmp_path)
    assert adapter._published_evidence('round-1-P.md') is None
    monkeypatch.setattr(adapter, '_write_evidence', lambda *args: (_ for _ in ()).throw(RuntimeError('crash')))
    items = [('c1', 'failure', diagnostic_view(dict(status='success', prediction='a', product_record=dict(question='q'))))]
    with pytest.raises(RuntimeError):
        adapter._write_analysis_evidence('round-1-P.md', 'staged text', items, ['id'], 1)
    assert adapter._published_evidence('round-1-P.md') == b'staged text'   # what a recovery would publish


def test_analysis_recovers_staged_evidence_after_crash_before_main_publication(tmp_path, monkeypatch):
    adapter = SNAdapterV4(CONFIG, evidence_dir=tmp_path)
    original = [('c1', 'failure', diagnostic_view(dict(status='success', prediction='original-answer',
                 product_record=dict(question='original-question'))))]
    rendered = render_views(original, 10000)[0]
    original_write = adapter._write_evidence
    monkeypatch.setattr(adapter, '_write_evidence', lambda *args: (_ for _ in ()).throw(RuntimeError('crash')))
    with pytest.raises(RuntimeError, match='crash'):
        adapter._write_analysis_evidence('round-1-P.md', rendered, original, ['original-id'], 1)
    assert not (tmp_path / 'round-1-P.md').exists()
    assert (tmp_path / 'round-1-P.cases.json').exists()
    monkeypatch.setattr(adapter, '_write_evidence', original_write)
    changed = [('c1', 'failure', diagnostic_view(dict(status='success', prediction='changed-answer',
                 product_record=dict(question='changed-question'))))]
    path, digest, reused = adapter._write_analysis_evidence('round-1-P.md', 'changed', changed, ['changed-id'], 1)
    assert path.read_text() == rendered
    saved = json.loads((tmp_path / 'round-1-P.cases.json').read_text())
    assert saved['cases']['c1']['case_id'] == 'original-id'
    assert saved['evidence_sha256'] == digest
    adapter._hypotheses = SimpleNamespace(get=lambda *args: {'card': {'search_refs': ['c1']}})
    adapter._cards_analysed([Card('failure', 'change', ref='h')], path, digest)
    entry = json.loads((tmp_path / 'card-evidence.json').read_text())['h']
    from pathlib import Path
    assert 'original-question' in Path(entry['path']).read_text()
    assert 'changed-question' not in Path(entry['path']).read_text()
