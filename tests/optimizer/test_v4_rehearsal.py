"""Rehearsal of method-v4 on the REAL v2 services (binding plan W7).

Reuses the v3 rehearsal rig (real worktrees, IterationService, RoundOperations and
measurements, agent creation / message stores, hypothesis store, sealed lineage;
fake bridges, agents and executions). The rig additionally plays the reviewer:
it reads the evidence file the message names (checking its sha256) and writes the
verdict file. Only ``v4.cli.build_services`` is replaced.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from rag_eval.optimizer import ledger_io
from rag_eval.optimizer.v4 import cli as v4cli
from rag_eval.optimizer.v4.loop import STATE_FILE

from .test_sn_rehearsal import Crash, GOOD, WAIT, Rig, card, eight_papers  # noqa: F401  (autouse fixture)
from test_workbench_iterations import env  # noqa: F401  (fixture)

BAD = dict(val=-.3, fresh=-.3)


class V4Rig(Rig):
    def __init__(self, env, tmp_path, monkeypatch):
        super().__init__(env, tmp_path, monkeypatch)
        from rag_eval.optimizer.sn_adapter import SNAdapter
        runtime_wait = SNAdapter._runtime_wait
        def crash_at_poll(adapter, seconds):
            if self.crashed.is_set():
                raise Crash('process killed')
            return runtime_wait(adapter, seconds)
        monkeypatch.setattr(SNAdapter, '_runtime_wait', crash_at_poll)
        # Finish this finite script through the production budget stop, after its round
        # commits. No empty-card analysis is used as a successful termination signal.
        from rag_eval.optimizer.v4.loop import V4Run
        stop_reason = V4Run._stop_reason
        def script_budget(run):
            if run.rounds and run.current is None:
                return 'time_budget'
            return stop_reason(run)
        monkeypatch.setattr(V4Run, '_stop_reason', script_budget)
        self.review_mode = 'none'          # 'none': no pair changed; 'gaming': every pair flagged
        self.reviews: list[dict] = []
        self.preflight_outcomes: dict[str, str] = {}
        self.run_dir = tmp_path / 'v4run'
        self.v4_config = tmp_path / 'v4.json'
        self.v4_config.write_text(json.dumps(dict(
            time_budget_hours=10, b=2, smoke=True, smoke_size=2, minibatch=True, minibatch_min=2,
            smoke_behaviour_check=True,
            adapter=dict(agent_poll_interval=.01, eval_poll_interval=.01, agent_timeout=WAIT,
                         eval_timeout=WAIT, worktree_timeout=WAIT))))
        monkeypatch.setattr(v4cli, 'build_services', self._build4)

    def __enter__(self):
        self._stop.clear()          # the rig may be entered again (run, then test)
        return super().__enter__()

    def _build4(self, config_path, state_root, iteration_id, *, actor, run_id, adapter_settings):
        return v4cli.services_from_operations(self.ops, self.config, iteration_id, actor=actor, run_id=run_id,
                                              adapter_settings=adapter_settings)

    def v4(self, command, *extra):
        return v4cli.main([command, '--config', 'unused.json', '--state', str(self.tmp / 'state'),
                           '--iteration', self.tree['id'], '--actor', 'operator', '--run-id', 'r4',
                           '--state-dir', str(self.run_dir), *extra])

    def run4(self, *extra):
        return self.v4('run', '--v4-config', str(self.v4_config), *extra)

    def _agents(self):
        while self.handled < len(self.messenger.commands):
            command = self.messenger.commands[self.handled]
            review = re.search(r'Review candidate (\S+)\. Your evidence is the file (\S+) \(sha256 ([0-9a-f]{64})\)',
                               command['text'])
            if not review:
                before = self.handled
                # One command at a time through the v3 driver.
                saved = self.messenger.commands
                self.messenger.commands = saved[:before + 1]
                try:
                    super()._agents()
                finally:
                    self.messenger.commands = saved
                continue
            self.handled += 1
            data = Path(review.group(2)).read_bytes()
            assert hashlib.sha256(data).hexdigest() == review.group(3)
            text = data.decode('utf-8')
            verdict = re.search(r'to the file (\S+) and nothing else', command['text']).group(1)
            pairs = re.search(r'^pairs: (.*)$', text, re.M).group(1).split(', ')

            def block(ref):
                body = text.split(f'### {ref}\n', 1)[1]
                return re.split(r'\n### ', body, maxsplit=1)[0]   # without the header line
            rows = []
            for p in pairs:
                # A blind reviewer: it only sees whether the two sides differ (A/A controls never do).
                body = block(p)
                differs = 'A | comparison: identical' not in body
                gaming = self.review_mode == 'gaming' and differs
                rows.append(dict(pair=p, effect=gaming, changed_side='A' if gaming else None, side_effect=False,
                                 gaming=gaming, note='shorter answers, same content' if gaming else ''))
            Path(verdict).write_text(json.dumps(dict(pairs=rows)))
            self.reviews.append(dict(candidate=review.group(1), evidence=text, verdict=verdict))
            self.reads.done.add(command['agentId'])

    def _implementer(self, candidate_id, *, repair=False):
        candidate = self.api.service.candidate(candidate_id)
        title = self.api.hypotheses.get(self.task_id, candidate['proposal_id'])['card']['title']
        skip_reason = self.preflight_outcomes.get(title)
        if not skip_reason:
            super()._implementer(candidate_id, repair=repair)
        message = self.messenger.commands[self.handled - 1]['text']
        named = re.search(r'Write the preflight report to (\S+)\. JSON fields:', message)
        assert named, 'v4 implementer needs a bounded preflight report path'
        Path(named.group(1)).write_text(json.dumps(dict(
            candidate_id=candidate_id, hypothesis_id=candidate['proposal_id'],
            parent_candidate_id=candidate['parent_candidate_id'],
            decision='skip' if skip_reason else 'implement', reason=skip_reason or 'supported',
            summary='Local synthetic source check completed.',
            checks=[dict(kind='source', target='backend/app', observation='Synthetic fixture has a known guard.')],
        )))

    def state4(self):
        return json.loads((self.run_dir / STATE_FILE).read_text())

    def records4(self):
        st = self.state4()
        rounds = st['rounds'] + ([st['current_round']] if st.get('current_round') else [])
        return {r['title']: r for rd in rounds for r in (rd.get('records') or {}).values()}


@pytest.fixture
def rig4(env, tmp_path, monkeypatch):  # noqa: F811
    return V4Rig(env, tmp_path, monkeypatch)


def first_round_only(cards):
    """The first analyst drafts *cards*; every later analyst (new parent or round) drafts none."""
    calls = []

    def analyst(parent):
        calls.append(parent)
        return cards if len(calls) == 1 else []
    return analyst


def test_v4_adopts_reviews_blind_and_evaluates_the_test_set_once(rig4, capsys):
    rig4.effects = {'Fix empty retrievals': GOOD, 'Break synthesis': BAD}
    rig4.analyst_cards = first_round_only([card('Fix empty retrievals'), card('Break synthesis', predicate=False)])
    with rig4:
        assert rig4.run4() == 0
    out = capsys.readouterr().out
    assert 'halt reason: time_budget' in out and 'feasibility' in out
    st = rig4.state4()
    recs = rig4.records4()
    good, bad = recs['Fix empty retrievals'], recs['Break synthesis']
    assert bad['stage'] == 'minibatch_fail' and bad['label'] == 'minibatch_fail'
    assert good['stage'] == 'measured' and good['smoke']['verdict'] == 'pass'
    assert good['mechanism']['mechanical']['status'] == 'changed' and good['mechanism']['tier'] == 'confirmed'
    assert good['label'] == 'adopted' and len(st['lineage']) == 2
    # Blind review: one reviewer Agent bound to the candidate, its evidence names no case id or score.
    assert len(rig4.reviews) == 1 and rig4.reviews[0]['candidate'] == st['versions'][good['candidate']]['candidate_id']
    evidence = rig4.reviews[0]['evidence']
    assert len(re.findall(r'^### p\d+$', evidence, re.M)) == 8     # 6 candidate + 2 control pairs
    assert len(re.findall(r'^[AB] \| ', evidence, re.M)) >= 16
    assert 'qasper:' not in evidence and 'score' not in evidence.lower()
    assert [i['brief'] for i in rig4.intents('reviewer')] == ['method-v4']
    # Measurements: validation subsets only (no base plan, no fresh batch), never above k_base per scope.
    rig4.assert_no_duplicate_measurements()
    scopes = {a.get('batch_id') for c in rig4.candidates() for a in c['attempts']}
    assert None not in scopes and not any(str(s).startswith(('split:validation', 'fresh')) for s in scopes)
    assert any(str(s).startswith('subset:mb:') for s in scopes) and any(str(s).startswith('subset:rest:') for s in scopes)
    # Endpoint: preview claims nothing, --confirm evaluates once, a rerun only prints.
    assert rig4.v4('test') == 2
    assert 'not confirmed' in capsys.readouterr().out
    budget = rig4.tmp / 'budget.json'
    budget.write_text(json.dumps(dict(timeout_seconds=600)))
    with rig4:
        assert rig4.v4('test', '--confirm', '--budget', str(budget)) == 0
    rep = ledger_io.read_json(rig4.run_dir / 'endpoint.json')
    assert rep['status'] == 'finished' and rep['delta_test'] > 0 and len(rep['points']) == 2
    sealed_attempts = [a for c in rig4.candidates() for a in c['attempts'] if str(a.get('batch_id')).startswith('sealed')]
    assert len(sealed_attempts) == 2
    assert rig4.v4('test', '--confirm', '--budget', str(budget)) == 0
    assert len([a for c in rig4.candidates() for a in c['attempts']
                if str(a.get('batch_id')).startswith('sealed')]) == 2


def test_v4_gaming_reviewer_flags_without_vetoing_adoption(rig4, capsys):
    rig4.effects = {'Fix empty retrievals': GOOD}
    rig4.analyst_cards = first_round_only([card('Fix empty retrievals')])
    rig4.review_mode = 'gaming'
    with rig4:
        assert rig4.run4() == 0
    rec = rig4.records4()['Fix empty retrievals']
    # v4.1: gaming → tier unconfirmed + flag needs_human_review, never vetoes
    assert rec['mechanism']['tier'] == 'unconfirmed' and rec['mechanism']['reviewer']['needs_human_review']
    # Adoption decided by Δ vs 1·SE (c=1.0 for unconfirmed); the mock's GOOD gain qualifies
    assert rec['label'] == 'adopted'
    assert len(rig4.state4()['lineage']) == 2


def test_v4_kill_during_stage2_resumes_without_new_agents_or_duplicate_runs(rig4, capsys):
    rig4.effects = {'Fix empty retrievals': GOOD}
    rig4.analyst_cards = first_round_only([card('Fix empty retrievals')])

    def rest_started(candidate):
        return candidate.get('proposal_id') and any(str(a.get('batch_id')).startswith('subset:rest:')
                                                    for a in candidate['attempts'])

    def rest_running(r):
        return any(rest_started(c) for c in r.candidates())
    rig4.crash_when = rest_running
    # Keep the candidate's stage-2 executions running until the kill lands (no same-tick finish).
    rig4.hold = lambda cid: not rig4.crashed.is_set() and bool(rest_started(rig4.api.service.candidate(cid)))
    with rig4:
        with pytest.raises(BaseException):
            rig4.run4()
    agents = len(rig4.creator.commands)
    rig4.crashed.clear()
    rig4.crash_when = None
    rig4.hold = lambda cid: False
    with rig4:
        assert rig4.run4() == 0
    assert len(rig4.state4()['lineage']) == 2
    # The old-parent round-2 speculative analyst is distinct from the adopted-parent round-2 analyst.
    # Restart reuses both existing request identities; it creates no second implementer.
    assert len(rig4.intents('implementer')) == 1 and len(rig4.intents('reviewer')) == 1
    analysts = rig4.intents('analyst')
    assert len(analysts) == 3 and len({i['request_key'] for i in analysts}) == 3
    assert len(rig4.creator.commands) - agents <= 2
    rig4.assert_no_duplicate_measurements()


def test_v4_run_is_served_by_the_workbench_auto_run_route(rig4, capsys):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    rig4.effects = {'Fix empty retrievals': GOOD, 'Break synthesis': BAD}
    rig4.analyst_cards = first_round_only([card('Fix empty retrievals'), card('Break synthesis', predicate=False)])
    with rig4:
        assert rig4.run4() == 0
    app = FastAPI()
    app.include_router(rig4.api.router())
    listed = TestClient(app).get('/api/iterations').json()
    assert next(i for i in listed if i['id'] == rig4.tree['id'])['auto']['run_id'] == 'r4'
    view = TestClient(app).get(f"/api/iterations/{rig4.tree['id']}/auto-run").json()
    assert view['method'] == 'v4' and view['run']['status'] == 'halted' and view['next_action'] == 'test'
    assert view['calibration']['k_val'] in (1, 2) and view['feasibility']['per_candidate_seconds'] >= 0
    assert [n['kind'] for n in view['lineage']] == ['seed', 'candidate']
    stages = {r['title']: r for r in view['rounds'][0]['records']}
    good = stages['Fix empty retrievals']
    assert good['label'] == 'adopted' and good['mechanism']['tier'] == 'confirmed'
    assert {v['kind'] for v in good['mechanism']['reviewer']['verdicts']} == {'candidate', 'control'}
    assert stages['Break synthesis']['stage'] == 'minibatch_fail'
    assert view['rounds'][0]['minibatch_size'] >= 2 and view['ablation']['status'] in ('done', 'skipped')
    # Counts only: no case id anywhere in the projection.
    assert 'qasper:' not in json.dumps(view)


def test_v4_preflight_skip_keeps_unfrozen_candidate_and_no_execution(rig4):
    rig4.effects = {'Already handled': GOOD, 'Useful fix': GOOD}
    rig4.preflight_outcomes = {'Already handled': 'already_satisfied'}
    rig4.analyst_cards = first_round_only([card('Already handled'), card('Useful fix')])
    with rig4:
        assert rig4.run4() == 0
    rec = rig4.records4()['Already handled']
    assert rec['stage'] == 'preflight_skipped' and rec['label'] == 'preflight_skipped'
    candidate = rig4.api.service.candidate(rec['preflight']['candidate_id'])
    assert candidate['status'] != 'frozen' and not candidate['attempts']
    assert len(rig4.state4()['lineage']) == 2
    assert any(e['label'] == 'preflight_skipped' for e in rig4.state4()['history'])
