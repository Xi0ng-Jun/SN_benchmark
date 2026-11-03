"""Rehearsal of the first server run: ``cli run`` → SNAdapter → run_optimization over the REAL v2 services.

Real: git worktrees, IterationService, ComparisonService and admission, RoundOperations and its
measurements (measure / smoke / segment / fresh reconcile), agent creation and message stores, the
hypothesis store, the analyst's ``sn-eval proposal draft`` command line, auto_runs, the sealed
lineage service, and the CLI with its checks (k_screen == k_base, unused fresh batches, smoke sample,
run lock, auto_runs binding).

Fake: the Paseo bridges, the agents' behaviour and the executions. A driver thread plays the agents
(when the analyst's message was sent it drafts lcd-v2 cards under the parent through ``sn-eval``;
when an implementer's message was sent it edits that candidate's worktree; a smoke-repair implementer
reads its feedback file, then edits) and finishes every running
execution with synthetic per-case scores and run rows (``outputs.jsonl``) chosen by the scenario.
Only ``cli.build_services`` is replaced: by ``cli.services_from_operations`` over the test's
RoundOperations, i.e. the same SNAdapter wiring the server uses.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

import pytest

import test_workbench_plan_inputs
from rag_eval.optimizer import cli, ledger_io, run_view, sn_adapter
from rag_eval.workbench import agent_tools, auto_runs
from rag_eval.workbench.context import resolve_context
from rag_eval.workbench.creation_api import CreationAPI
from rag_eval.workbench.iterations_api import IterationAPI
from rag_eval.workbench.loop_services import RoundOperations
from rag_eval.workbench.message_api import MessageAPI
from rag_eval.workbench.sealed import SealedService
from rag_eval.workbench.sessions import TaskSessionReader
from rag_eval.workbench.splits import batches
from test_notebook_data_v3 import freeze, qasper_raw
from test_workbench_agent_creations import Bridge, created
from test_workbench_agent_messages import acknowledged
from test_workbench_clusters import observation
from test_workbench_comparisons import Views
from test_workbench_iterations import env, iteration  # noqa: F401  (fixture)
from test_workbench_loop_services import SAFEGUARDS

PAPERS, QUESTIONS = 8, 3          # 5 selected (3 search, 2 validation) + 2 fresh + 1 sealed paper
TICK = 0.02
WAIT = 60                         # seconds: a hung rehearsal fails instead of blocking the suite
SLICE = dict(kind='range', field='trace.n_empty_retrievals', min=1)


def protocol(n_fresh=QUESTIONS):
    """The server smoke protocol; adapter poll intervals near 0 for the rehearsal."""
    return {"mde": 0.03, "sigma_d": 0.1, "ladder_c": 1.0, "K_max": 10, "T": 0.001, "n_fresh": n_fresh,
            "z_screen": 0.0, "b_cards": 2, "analyst_cards": 3, "k_screen": 2, "max_confirmations": 2,
            "max_rounds": 1, "smoke_sample_size": 2,
            "agent_poll_interval": 0.01, "eval_poll_interval": 0.01, "agent_timeout": WAIT,
            "eval_timeout": WAIT, "worktree_timeout": WAIT}


def _bundle(tmp_path, suite='qasper'):
    raw = qasper_raw()
    template = raw.pop('p')
    for p in range(PAPERS):
        paper = copy.deepcopy(template)
        paper['title'] = f'Paper {p}'
        paper['qas'] = []
        for q in range(QUESTIONS):
            qa = copy.deepcopy(template['qas'][0])
            qa.update(question_id=f'p{p}q{q}', question=f'What flies in study {p} part {q}?')
            paper['qas'].append(qa)
        raw[f'p{p}'] = paper
    return freeze(tmp_path, suite, raw)


@pytest.fixture(autouse=True)
def eight_papers(monkeypatch):
    """Runs before ``env``: its synthetic bundle gets enough papers for every split."""
    monkeypatch.setattr(test_workbench_plan_inputs, 'canonical_fixture', _bundle)


def card(title, *, predicate=True):
    """A v2-legal lcd-v2 card; the contract's slice predicate reads a v3 trace feature."""
    out = dict(title=title, component='retrieval', mechanism='empty retrieval calls leave synthesis without evidence',
               falsification='empty retrievals unchanged or the validation score drops',
               implementer_brief='Retry an empty retrieval once with the expanded sub-query before synthesis.',
               predictions=[], change_scope=['backend/app'])
    if predicate:
        out['contract'] = dict(slice_predicate=SLICE, manipulation=dict(direction='decrease', threshold=.5),
                               slice_effect=dict(direction='positive'))
    return out


class SynthViews(Views):
    """Per-execution case scores plus a run directory per partition (the analyst's evidence)."""

    def __init__(self, runs_root):
        super().__init__()
        self.runs_root = Path(runs_root)
        self.tokens = {}          # execution -> product tokens per task (cost_per_task safeguard)

    def execution_runs(self, execution_id):
        return [SimpleNamespace(id=f'{execution_id}-{i}', suite='qasper', mode='chunk', case_ids=tuple(cases),
                                run_dir=self.runs_root / f'{execution_id}-{i}')
                for i, cases in enumerate(self.partitions.get(execution_id, []), 1)]

    def _eid(self, run_id):
        return run_id.rsplit('-', 1)[0]

    def observation(self, run_id, case_id):
        eid = self._eid(run_id)
        return dict(observation(case_id, self.scores[eid][case_id]), usage=dict(total_tokens=self.tokens.get(eid, 100)))

    def cases(self, run_id):
        eid, index = self._eid(run_id), int(run_id.rsplit('-', 1)[1])
        return dict(cases=[dict(case_id=c, status='success') for c in self.partitions[eid][index - 1]])

    def run(self, run_id):
        return dict(official=dict(primary_metric='answer_f1', metrics=dict(answer_f1=.2)))

    def task_runs_context(self, task_id):
        return 'runs', json.dumps(dict(runs=[dict(kind='execution', id=e) for e in list(self.partitions)])), {}

    def _load(self, run_id):
        return dict(_bundle=dict(cases=[]))

    def _binding_read(self, task_id):
        return {'files': []}, None


class AgentReads:
    """Paseo agent reads: running at first, idle once the driver finished that agent's work."""

    def __init__(self, creations, crashed):
        self.creations, self.crashed, self.done, self.reads = creations, crashed, set(), {}

    def call(self, command):
        agent_id = command['agentId']
        original = next(c for c in self.creations.commands if c['agentId'] == agent_id)
        agent = created(original)['result']['agent']
        self.reads[agent_id] = self.reads.get(agent_id, 0) + 1
        idle = agent_id in self.done and self.reads[agent_id] > 1 and not self.crashed.is_set()
        agent['status'] = 'idle' if idle else 'running'
        return dict(result=dict(status='ok', agent=agent), release=dict(status='ok'))


class Crash(BaseException):
    """A kill of the run process: not an Exception, so nothing in the loop or CLI handles it."""


class Rig:
    """Real v2 services over the ``env`` fixture, plus the scenario's synthetic world."""

    def __init__(self, env, tmp_path, monkeypatch):
        self.env, self.tmp = env, tmp_path
        service, repo = env['service'], env['repo']
        self.repo = repo
        self.views = views = SynthViews(tmp_path / 'runs')
        views._worktree_service = service.trees
        self.config = dict(task_worktrees=dict(create_candidates=True), job_preparation=service.preparation_config,
                           job_execution=service.execution_config,
                           agent_creation=dict(workspaceId='fixture-evaluator', profiles={
                               'pi-flash': dict(provider='codex/model-example', modeId='full-access')}))
        self.api = api = IterationAPI(repo, env['workspace'], service.trees, plans=service.plans,
                                      job_api=SimpleNamespace(store=service.jobs, execution=service.executions),
                                      actor='alice', config=self.config, run_views=views, creator=env['creator'])
        self.creator, self.messenger = Bridge(created), Bridge(acknowledged)
        creation = CreationAPI(repo, env['workspace'], self.config, actor='alice', bridge=self.creator)
        # As create_app wires them.
        creation.store.evaluated_count = lambda task_id: sum(
            c['state'] == 'evaluated' for i in api.service.iterations() if i['task_id'] == task_id
            for c in api.service.iteration(i['id'])['candidates'])
        creation.store.candidate_state = lambda cid: api.service.candidate(cid)['state']
        self.creation = creation
        messages = MessageAPI(repo, env['workspace'], self.config, actor='alice', bridge=self.messenger)
        self.crashed = threading.Event()
        self.reads = AgentReads(self.creator, self.crashed)
        self.ops = RoundOperations(api, creation, messages, TaskSessionReader(repo, self.reads))
        service.preparation_config['sealed_selections']['synthetic-holdout']['case_ids'] = [
            f'qasper:p{PAPERS - 1}q{q}' for q in range(QUESTIONS)]
        self.case_ids = [f'qasper:p{p}q{q}' for p in range(5) for q in range(QUESTIONS)]
        self.tree = iteration(env, case_ids=self.case_ids, fresh_batch_count=2, fresh_papers_per_batch=1,
                              budget=dict(limit_cny=1000, max_runs_per_candidate=60),
                              gate_rule='lcd-v2', safeguards=SAFEGUARDS, k_base=2, k_sealed=1)
        self.protocol = self.tree['protocol']
        self.task_id = self.tree['task_id']
        self.role = {c: 'search' for c in self.protocol['splits']['search']}
        self.role.update({c: 'validation' for c in self.protocol['splits']['validation']})
        for b in self.protocol['fresh_batches']:
            self.role.update({c: 'fresh' for c in b['case_ids']})
        self.role.update({c: 'sealed' for c in self.protocol['splits']['sealed']})
        self.run_dir = tmp_path / 'v3run'
        self.protocol_file = tmp_path / 'protocol.json'
        self.protocol_file.write_text(json.dumps(protocol(len(self.protocol['fresh_batches'][0]['case_ids']))))
        # Scenario knobs.
        self.analyst_cards: list[dict] = []
        self.effects: dict[str, dict] = {}       # card title -> val / fresh / fix_empty / empty
        self.hold = lambda candidate_id: False   # True: leave this candidate's executions running
        self.fail = lambda candidate_id, attempt: False   # True: the execution crashes (infrastructure)
        self.crash_when = None                   # callable(rig) -> bool: arm the crash
        self.kill_during_analysis = False        # the process dies while the analyst drafts
        self.language = 'zh'                     # of the synthetic SN answers and questions
        self.errors: list[str] = []
        self.finished: set[str] = set()
        self.handled = 0
        self.evidence_read: list[tuple[str, str]] = []
        self.repairs: list[dict] = []            # one per repair message: candidate, feedback file, text
        self.repaired: set[str] = set()          # candidates a repair implementer edited
        self.titles: dict[str, list[str]] = {}
        self._stop = threading.Event()
        self._thread = None
        monkeypatch.setattr(cli, 'build_services', self._build)
        monkeypatch.setattr(agent_tools, 'build', lambda state, config: (self.repo, self.views))
        monkeypatch.setattr(SealedService, '_case_scores', lambda _self, ids, execution: dict(
            metric='answer_f1', scores={c: self.views.scores[execution['execution_id']][c] for c in ids},
            families={c: 'sealed' for c in ids}))
        rig = self

        class _Time:      # SNAdapter's clock: a crash lands while some thread polls v2
            monotonic = staticmethod(time.monotonic)

            @staticmethod
            def sleep(seconds):
                if rig.crashed.is_set():
                    raise Crash('process killed')
                time.sleep(seconds)
        monkeypatch.setattr(sn_adapter, 'time', _Time)

    # -- the boundary the CLI uses -------------------------------------------------------
    def _build(self, config_path, state_root, iteration_id, *, actor, run_id, adapter_settings):
        self.built_settings = dict(adapter_settings)
        return cli.services_from_operations(self.ops, self.config, iteration_id, actor=actor, run_id=run_id,
                                            adapter_settings=adapter_settings)

    def args(self, command, *extra, run_id='r1'):
        return [command, '--config', 'unused.json', '--state', str(self.tmp / 'state'), '--iteration', self.tree['id'],
                '--actor', 'operator', '--run-id', run_id, '--state-dir', str(self.run_dir), *extra]

    def run(self, *extra):
        return cli.main(self.args('run', '--protocol', str(self.protocol_file), *extra))

    # -- driver ---------------------------------------------------------------------------
    def __enter__(self):
        self._thread = threading.Thread(target=self._drive, name='rehearsal-driver', daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(timeout=10)
        assert not self.errors, self.errors[0]

    def _drive(self):
        deadline = time.monotonic() + WAIT
        while not self._stop.is_set() and time.monotonic() < deadline:
            try:
                self._agents()
                if self.crash_when is not None and not self.crashed.is_set() and self.crash_when(self):
                    self.crashed.set()
                if not self.crashed.is_set():
                    self._settle()
            except Exception:  # noqa: BLE001 - surfaced by __exit__
                self.errors.append(traceback.format_exc())
                self.crashed.set()     # stop the run at its next poll instead of waiting for timeouts
                return
            time.sleep(TICK)

    def _agents(self):
        while self.handled < len(self.messenger.commands):
            command = self.messenger.commands[self.handled]
            self.handled += 1
            text = command['text']
            parent = re.search(r'proposal draft --parent (\S+) ', text)
            implement = re.search(r'Implement the approved card \(hypothesis (\S+)\) in candidate (\S+),', text)
            repair = re.search(r'Repair the approved card \(hypothesis (\S+)\) in candidate (\S+),', text)
            if parent:
                named = re.search(r'Your evidence is the file (\S+) \(sha256 ([0-9a-f]{64})\)', text)
                assert named, 'the analyst message names no evidence file'
                data = Path(named.group(1)).read_bytes()      # the analyst reads its only input
                assert hashlib.sha256(data).hexdigest() == named.group(2)
                self.evidence_read.append((named.group(1), named.group(2)))
                self._analyst(parent.group(1))
            elif implement:
                self._implementer(implement.group(2))
            elif repair:
                named = re.search(r'The file (\S+) \(sha256 ([0-9a-f]{64})\)', text)
                assert named, 'the repair message names no feedback file'
                data = Path(named.group(1)).read_bytes()      # the repair implementer reads it
                assert hashlib.sha256(data).hexdigest() == named.group(2)
                self.repairs.append(dict(candidate_id=repair.group(2), path=named.group(1),
                                         feedback=data.decode('utf-8'), text=text))
                self._implementer(repair.group(2), repair=True)
            else:
                raise AssertionError('unexpected agent message: ' + text[:200])
            self.reads.done.add(command['agentId'])

    def _analyst(self, parent):
        cards = self.analyst_cards(parent) if callable(self.analyst_cards) else self.analyst_cards
        for i, c in enumerate(cards):
            path = self.tmp / f'card-{parent}-{i}.json'
            path.write_text(json.dumps(c))
            before = len(self.api.hypotheses.list(self.task_id))
            # The analyst's own command line, as its brief tells it (prints the saved card).
            agent_tools.main(['--state', 'unused', '--config', 'unused.json', '--task', self.task_id,
                              '--role', 'analyst', '--node', parent, 'proposal', 'draft',
                              '--parent', parent, '--file', str(path)])
            assert len(self.api.hypotheses.list(self.task_id)) == before + 1
            if self.kill_during_analysis:
                # Killed while the Agent works: the Agent keeps drafting, the run process is gone.
                self.kill_during_analysis = False
                self.crashed.set()

    def _implementer(self, candidate_id, *, repair=False):
        candidate = self.api.service.candidate(candidate_id)
        title = self.api.hypotheses.get(self.task_id, candidate['proposal_id'])['card']['title']
        if self.effects[title].get('empty') and not repair:
            return
        slug = re.sub(r'\W+', '_', title.lower()) + ('_repair' if repair else '')
        Path(candidate['worktree']['path'], 'backend/app', slug + '.py').write_text(f'ENABLED = True  # {title}\n')
        if repair:
            self.repaired.add(candidate_id)

    def repaired_lineage(self, candidate_id):
        """True when the candidate or one of its ancestors was edited by a repair implementer."""
        cursor = self.api.service.candidate(candidate_id)
        while cursor:
            if cursor['id'] in self.repaired:
                return True
            parent = cursor.get('parent_candidate_id')
            cursor = self.api.service.candidate(parent) if parent else None
        return False

    def chain(self, candidate_id):
        """Card titles from the candidate up to (excluding) the root."""
        if candidate_id not in self.titles:
            out, cursor = [], self.api.service.candidate(candidate_id)
            while cursor.get('parent_candidate_id'):
                out.append(self.api.hypotheses.get(self.task_id, cursor['proposal_id'])['card']['title'])
                cursor = self.api.service.candidate(cursor['parent_candidate_id'])
            self.titles[candidate_id] = out
        return self.titles[candidate_id]

    def score(self, candidate_id, case_id):
        role = self.role[case_id]
        index = int(case_id[-1])
        value = {'search': .2 if index < 2 else .8}.get(role, .4)
        for title in self.chain(candidate_id):
            effect = self.effects[title]
            if role == 'validation':
                value += effect.get('val', 0)
            elif role in ('fresh', 'sealed'):
                value += effect.get('fresh', 0) * (1 + .1 * index)
        return round(value, 6)

    def _row(self, candidate_id, case_id):
        # fix_empty: True (the change fixes empty retrievals) or 'after_repair' (only once repaired).
        fixes = [self.effects[t].get('fix_empty') for t in self.chain(candidate_id)]
        fixed = True in fixes or ('after_repair' in fixes and self.repaired_lineage(candidate_id))
        spans = [dict(step_type='plan', summary='p', detail=dict(sub_queries=['q1'], source='intent')),
                 dict(step_type='retrieve', summary='r', detail=dict(count=3 if fixed else 0, result_ids=[])),
                 dict(step_type='synthesis', summary='y', detail=dict(citations=1, anchors=1))]
        # Server-sized rows: SN answers and questions are long; Chinese takes 3 UTF-8 bytes a char.
        answer, question = (('根据检索到的段落，论文报告的结果如下。' * 24, '这篇论文在实验部分使用了哪些数据集和评价指标？' * 3)
                            if self.language == 'zh' else
                            ('According to the retrieved passages, the paper reports the following. ' * 12,
                             'Which datasets and metrics does the experimental section of this paper use? ' * 2))
        return dict(case_id=case_id, status='success', prediction=answer,
                    behavior=dict(kind='answer_returned'),
                    trace=dict(trace_id='t', completeness='partial', spans=spans),
                    product_record=dict(question=question, response=dict(grounded=True),
                                        usage=dict(total_tokens=100)))

    def _settle(self):
        for candidate in self.api.service.iteration(self.tree['id'])['candidates']:
            if self.hold(candidate['id']):
                continue
            for attempt in candidate['attempts']:
                eid = attempt.get('execution_id')
                if attempt.get('execution_state') != 'running' or not eid or eid in self.finished:
                    continue
                if self.fail(candidate['id'], attempt):
                    self.env['executions']._finish(eid, state='failed', exit_code=1, error_type='RuntimeError',
                                                   run_state=dict(phase='failed'))
                    self.finished.add(eid)
                    continue
                job = self.api.service.jobs.get(self.task_id, attempt['job_id'])
                ids = job['plan_snapshot']['content']['selection']['case_ids']
                papers: dict[str, list[str]] = {}
                for c in ids:
                    papers.setdefault(c.rsplit('q', 1)[0], []).append(c)   # one partition run per paper
                parts = [tuple(v) for v in papers.values()]
                for i, part in enumerate(parts, 1):
                    run = self.views.runs_root / f'{eid}-{i}'
                    run.mkdir(parents=True, exist_ok=True)
                    (run / 'outputs.jsonl').write_text(''.join(json.dumps(self._row(candidate['id'], c)) + '\n'
                                                               for c in part))
                    (run / 'state.json').write_text(json.dumps(dict(phase='finished')))
                self.views.scores[eid] = {c: self.score(candidate['id'], c) for c in ids}
                cost = 1.0
                for title in self.chain(candidate['id']):
                    cost *= self.effects[title].get('cost', 1.0)
                self.views.tokens[eid] = round(100 * cost)
                self.views.partitions[eid] = parts
                self.env['executions']._finish(eid, state='finished', exit_code=0, error_type=None, run_state=dict(
                    phase='finished', cost_observation=dict(currency='CNY', complete=True, total_cny=.01)))
                self.finished.add(eid)

    # -- reads for assertions --------------------------------------------------------------
    def state(self):
        return json.loads((self.run_dir / ledger_io.STATE_FILE).read_text())

    def candidates(self):
        return self.api.service.iteration(self.tree['id'])['candidates']

    def intents(self, role):
        return [i for i in self.creation.store.list(self.task_id, actor='alice') if i.get('role') == role]

    def by_title(self):
        out = {}
        for c in self.candidates():
            if c.get('proposal_id'):
                out.setdefault(self.api.hypotheses.get(self.task_id, c['proposal_id'])['card']['title'], []).append(c)
        return out

    def assert_no_duplicate_measurements(self):
        """Every scope holds exactly the attempts v2 planned: k_base (smoke 1, sealed k_sealed), never more."""
        k = self.protocol['k_base']
        for c in self.candidates():
            per_scope: dict = {}
            for a in c['attempts']:
                per_scope.setdefault(a.get('batch_id'), []).append(a)
            for scope, attempts in per_scope.items():
                limit = 1 if str(scope).startswith(('smoke:', 'sealed:')) else k
                assert len(attempts) <= limit, (c['id'], c['id'] == self.tree['root_candidate_id'], scope, len(attempts))
                assert all(a['execution_state'] == 'finished' for a in attempts), (c['id'], scope)


@pytest.fixture
def rig(env, tmp_path, monkeypatch):  # noqa: F811
    return Rig(env, tmp_path, monkeypatch)


def records(rig):
    st = rig.state()
    rounds = st['rounds'] + ([st['current_round']['round']] if st.get('current_round') else [])
    return {c['title']: c for r in rounds for c in r['candidates']}, rounds


GOOD = dict(val=.2, fresh=.2, fix_empty=True)


# ---------------------------------------------------------------------------
# (a) screen_pass -> milestone on fresh f1 via v2 segment -> certified; sealed end step
# ---------------------------------------------------------------------------

def test_improvement_is_certified_on_fresh_and_measured_once_on_sealed(rig, capsys):
    rig.analyst_cards = [card('Retry empty retrievals'), card('Shorter synthesis prompt', predicate=False)]
    rig.effects = {'Retry empty retrievals': dict(GOOD), 'Shorter synthesis prompt': dict(val=-.1)}
    # B's executions wait until A's milestone completed fresh batch f1: B then decides against a
    # stale parent, so it is rebased onto A and re-measured (deterministic order).
    rig.hold = lambda cid: (rig.chain(cid)[:1] == ['Shorter synthesis prompt']
                            and len(rig.chain(cid)) == 1 and not batches(rig.repo, rig.tree['id'], completed=True))
    with rig:
        code = rig.run()
        out = capsys.readouterr().out
        assert code == 0, out
        assert 'halt reason: max_rounds' in out and 'certified nodes: 2' in out
        assert rig.built_settings['eval_poll_interval'] == .01

        st = rig.state()
        cards, rounds = records(rig)
        assert cards['Retry empty retrievals']['verdict'] == 'screen_pass'
        assert cards['Retry empty retrievals']['smoke_verdict'] == 'pass'
        assert cards['Shorter synthesis prompt']['verdict'] == 'screen_fail'
        assert cards['Shorter synthesis prompt']['rebased_from']        # rebased onto the new parent
        conf = rounds[0]['confirmations']
        assert [c['verdict'] for c in conf] == ['confirmed'] and conf[0]['fresh_batch_index'] == 0
        assert conf[0]['delta_fresh'] > 0 and conf[0]['n_fresh'] == QUESTIONS
        f1 = rig.protocol['fresh_batches'][0]
        assert st['validation_pool'] == rig.protocol['splits']['validation'] + f1['case_ids']
        assert [b['batch_id'] for b in batches(rig.repo, rig.tree['id'], completed=True)] == ['f1']
        assert st['milestone']['best_score'] == pytest.approx(conf[0]['best_score'])
        nodes = st['lineage']
        assert [n['state'] for n in nodes] == ['certified', 'certified']
        a = rig.by_title()['Retry empty retrievals'][0]
        assert st['versions'][nodes[1]['version_hash']]['candidate_id'] == a['id']
        assert (rig.run_dir / ledger_io.LEDGER_FILE).exists()
        assert len((rig.run_dir / ledger_io.ROUNDS_FILE).read_text().splitlines()) == 1
        assert auto_runs.binding(rig.repo, rig.tree['id'])['run_id'] == 'r1'
        view = run_view.read_run(rig.run_dir)
        assert [s['status'] for s in view['segments']] == ['certified']
        assert view['run']['status'] == 'halted' and not view['run']['alive']
        # v2 records: a node analyst of C0, one implementer per derived candidate.
        analysts = rig.intents('analyst')
        assert [(i.get('candidate_id'), i.get('scope')) for i in analysts] == [(rig.tree['root_candidate_id'], None)]
        rig.assert_no_duplicate_measurements()
        # Safeguards: the iteration's pre-registered v2 safeguards, v2's rule, on every paired decision.
        assert st['safeguards'] == dict(source='iteration_protocol', specs=SAFEGUARDS,
                                        infrastructure_rule=sn_adapter.V3_INFRASTRUCTURE_RULE)
        end = conf[0]['safeguards_end']
        assert end['ok'] and end['source'] == 'iteration_protocol' and end['reasons'] == []
        assert {c['key']: (c['reference'], c['subject'], c['breached']) for c in end['checks']} == {
            'answer_language_match': (1.0, 1.0, False), 'infrastructure_failure_rate': (0.0, 0.0, False),
            'cost_per_task': (1.0, 1.0, False)}
        assert cards['Retry empty retrievals']['safeguards']['ok']
        ledger = json.loads((rig.run_dir / ledger_io.LEDGER_FILE).read_text())
        assert ledger['utility']['certified_milestones'][0]['safeguards_end'] == end
        assert ledger['context']['safeguards'] == st['safeguards']
        assert view['segments'][0]['confirmation']['safeguards_end'] == end and view['safeguards'] == st['safeguards']
        # Implementers got the v3 implementer brief.
        implementer_bodies = [json.loads(c['text']) for c in rig.messenger.commands if 'Implement the approved' in c['text']]
        assert implementer_bodies and all(b['context'][0]['title'].startswith('Method-v3 implementer brief')
                                          and len(b['context'][0]['text']) <= 2000 for b in implementer_bodies)

        budget = rig.tmp / 'budget.json'
        budget.write_text(json.dumps(dict(timeout_seconds=600)))
        sealed_args = rig.args('sealed', '--budget', str(budget))
        assert cli.main(sealed_args) == 2
        out = capsys.readouterr().out
        assert 'sealed node set: 2 node(s)' in out and 'not confirmed' in out
        assert SealedService(rig.repo, rig.api.service, rig.views).lineage(rig.tree['id']) is None
        assert cli.main(sealed_args + ['--confirm']) == 0, capsys.readouterr()
        curve = json.loads((rig.run_dir / cli.SEALED_CURVE_FILE).read_text())
        assert curve['status'] == 'finished' and curve['k'] == 1 and len(curve['points']) == 2
        assert curve['points'][1]['delta_vs_seed'] > 0
        assert [p['style'] for p in curve['points']] == ['solid', 'solid']


# ---------------------------------------------------------------------------
# (b) improves on validation, not on fresh: rollback
# ---------------------------------------------------------------------------

def test_validation_gain_that_fails_on_fresh_is_rolled_back(rig, capsys):
    rig.analyst_cards = [card('Overfit retrieval cache')]
    rig.effects = {'Overfit retrieval cache': dict(val=.2, fresh=-.2, fix_empty=True)}
    with rig:
        assert rig.run() == 0, capsys.readouterr()
        st = rig.state()
        cards, rounds = records(rig)
        assert cards['Overfit retrieval cache']['verdict'] == 'screen_pass'
        assert rounds[0]['confirmation_verdict'] == 'rejected' and rounds[0]['rolled_back']
        assert [n['state'] for n in st['lineage']] == ['certified', 'rolled_back']
        assert st['halt_reason'] == 'max_rounds' and st['fresh_batches_used'] == 1
        assert len(st['validation_pool']) == len(rig.protocol['splits']['validation']) + QUESTIONS
        root = rig.tree['root_candidate_id']
        assert st['versions'][st['milestone']['current_provisional_id']]['candidate_id'] == root
        assert [s['status'] for s in run_view.read_run(rig.run_dir)['segments']] == ['rolled_back']
        rig.assert_no_duplicate_measurements()


# ---------------------------------------------------------------------------
# (c) an empty implementation and a smoke failure are recorded; the third card refills
# ---------------------------------------------------------------------------

def test_a_gain_that_breaches_cost_per_task_is_unmeasurable_at_screening(rig, capsys):
    rig.analyst_cards = [card('Rerank with a bigger model')]
    rig.effects = {'Rerank with a bigger model': dict(val=.2, fresh=.2, fix_empty=True, cost=2.0)}
    with rig:
        assert rig.run() == 0, capsys.readouterr()
        cards, rounds = records(rig)
        rec = cards['Rerank with a bigger model']
        assert rec['verdict'] == 'unmeasurable' and rec['delta_val'] == pytest.approx(.2)
        check = rec['safeguards']
        assert not check['ok'] and check['reasons'] == ['safeguard_breached:cost_per_task']
        cost = next(c for c in check['checks'] if c['key'] == 'cost_per_task')
        assert (cost['reference'], cost['subject'], cost['breached']) == (1.0, 2.0, True)
        assert rounds[0]['screen_passed'] == 0 and [n['state'] for n in rig.state()['lineage']] == ['certified']
        assert not batches(rig.repo, rig.tree['id'])                     # no milestone, no fresh batch
        view = run_view.read_run(rig.run_dir)
        assert view['rounds'][0]['attempts'][0]['safeguards'] == check


def test_empty_change_and_smoke_failure_are_recorded_and_refilled(rig, capsys):
    rig.analyst_cards = [card('Nothing to change'), card('Rerank without effect'),
                         card('Plain prompt tweak', predicate=False)]
    rig.effects = {'Nothing to change': dict(empty=True), 'Rerank without effect': dict(val=.2),
                   'Plain prompt tweak': dict(val=-.05)}
    with rig:
        assert rig.run() == 0, capsys.readouterr()
        cards, rounds = records(rig)
        assert cards['Nothing to change']['verdict'] == 'error'
        assert 'materialize' in cards['Nothing to change']['reason'] and 'no changes' in cards['Nothing to change']['reason']
        assert cards['Rerank without effect']['verdict'] == 'smoke_fail'
        assert cards['Rerank without effect']['smoke_verdict'] == 'fail'
        assert cards['Plain prompt tweak']['verdict'] == 'screen_fail'
        assert cards['Plain prompt tweak']['smoke_verdict'] == 'pass'          # generic smoke: it ran
        assert cards['Plain prompt tweak']['card_index'] == 2
        assert rounds[0]['cards_generated'] == 3 and rounds[0]['screen_passed'] == 0
        assert [n['state'] for n in rig.state()['lineage']] == ['certified']
        rig.assert_no_duplicate_measurements()


# ---------------------------------------------------------------------------
# (d) resume after a kill mid-round: no second analyst, no execution twice
# ---------------------------------------------------------------------------

def test_rerun_after_a_kill_resumes_without_new_agents_or_executions(rig, capsys):
    rig.analyst_cards = [card('Retry empty retrievals'), card('Shorter synthesis prompt', predicate=False)]
    rig.effects = {'Retry empty retrievals': dict(GOOD), 'Shorter synthesis prompt': dict(val=-.1)}
    rig.hold = lambda cid: (rig.chain(cid) == ['Shorter synthesis prompt']
                            and not batches(rig.repo, rig.tree['id'], completed=True))

    def smoke_running(r):
        # Both implementers ran and a smoke execution is dispatched but not finished.
        return len(r.intents('implementer')) == 2 and any(
            a['execution_state'] == 'running' and str(a.get('batch_id')).startswith('smoke:')
            for c in r.candidates() for a in c['attempts'])
    rig.crash_when = smoke_running
    with rig:
        with pytest.raises(Crash):
            rig.run()
        st = rig.state()
        assert st['current_round']['cards'] is not None and st['halt_reason'] is None
        assert sorted(st['current_round']['inflight']) == ['0', '1']
        assert not run_view.run_alive(rig.run_dir)                  # the lock died with the process
        agents = dict(analysts=len(rig.intents('analyst')), implementers=len(rig.intents('implementer')),
                      messages=len(rig.messenger.commands), creations=len(rig.creator.commands))
        assert agents['analysts'] == 1 and agents['implementers'] == 2
        executions = {e['execution_id'] for e in rig.env['executions'].records(rig.env['workspace']['id'])}
        running = {a['execution_id'] for c in rig.candidates() for a in c['attempts']
                   if a['execution_state'] == 'running'}
        assert running
        rig.crash_when = None
        rig.crashed.clear()
        capsys.readouterr()

        assert rig.run() == 0, capsys.readouterr()
        assert 'resume:' in capsys.readouterr().out
        assert dict(analysts=len(rig.intents('analyst')), implementers=len(rig.intents('implementer')),
                    messages=len(rig.messenger.commands), creations=len(rig.creator.commands)) == agents
        cards, rounds = records(rig)
        assert cards['Retry empty retrievals']['verdict'] == 'screen_pass' and cards['Retry empty retrievals']['resumed']
        assert cards['Shorter synthesis prompt']['verdict'] == 'screen_fail'
        assert cards['Shorter synthesis prompt']['rebased_from']    # v2 derive + rebase: no Agent involved
        assert rounds[0]['confirmation_verdict'] == 'confirmed'
        assert rig.state()['halt_reason'] == 'max_rounds' and len(rig.state()['sessions']) == 2
        # The executions in flight at the kill were finished, not dispatched again.
        assert running <= rig.finished
        after = {e['execution_id'] for e in rig.env['executions'].records(rig.env['workspace']['id'])}
        assert executions <= after
        rig.assert_no_duplicate_measurements()


def test_second_round_analyst_is_bound_to_the_certified_pointer(rig, capsys):
    root = rig.tree['root_candidate_id']
    rig.analyst_cards = lambda parent: ([card('Retry empty retrievals')] if parent == root
                                        else [card('Plain prompt tweak', predicate=False)])
    rig.effects = {'Retry empty retrievals': dict(GOOD), 'Plain prompt tweak': dict(val=-.05)}
    rig.protocol_file.write_text(json.dumps(dict(json.loads(rig.protocol_file.read_text()), max_rounds=2)))
    with rig:
        assert rig.run() == 0, capsys.readouterr()
        st = rig.state()
        a = rig.by_title()['Retry empty retrievals'][0]['id']
        assert [(i.get('candidate_id'), i.get('scope')) for i in rig.intents('analyst')] == [(root, None), (a, None)]
        assert [r['round_id'] for r in st['rounds']] == [1, 2] and st['halt_reason'] == 'max_rounds'
        cards, rounds = records(rig)
        assert cards['Plain prompt tweak']['verdict'] == 'screen_fail'
        assert st['versions'][cards['Plain prompt tweak']['parent']]['candidate_id'] == a
        assert rounds[1]['val_pool_size'] == len(rig.protocol['splits']['validation']) + QUESTIONS
        rig.assert_no_duplicate_measurements()


def test_kill_during_the_fresh_segment_resumes_the_same_claim(rig, capsys):
    rig.analyst_cards = [card('Retry empty retrievals')]
    rig.effects = {'Retry empty retrievals': dict(GOOD)}
    rig.crash_when = lambda r: any(a['execution_state'] == 'running' and a.get('batch_id') == 'f1'
                                   for c in r.candidates() for a in c['attempts'])
    with rig:
        with pytest.raises(Crash):
            rig.run()
        claims = batches(rig.repo, rig.tree['id'])
        assert [b['batch_id'] for b in claims] == ['f1'] and claims[0]['completed_at'] is None
        st = rig.state()
        assert st['fresh_batches_used'] == 0 and [n['state'] for n in st['lineage']] == ['certified', 'provisional']
        rig.crash_when = None
        rig.crashed.clear()
        assert rig.run() == 0, capsys.readouterr()
        st = rig.state()
        assert [n['state'] for n in st['lineage']] == ['certified', 'certified']
        assert st['fresh_batches_used'] == 1
        assert [(b['batch_id'], b['request_key']) for b in batches(rig.repo, rig.tree['id'], completed=True)] == [
            (claims[0]['batch_id'], claims[0]['request_key'])]
        rig.assert_no_duplicate_measurements()


def test_retry_failed_after_a_baseline_process_failure_measures_again(rig, capsys):
    """Runner down at the baseline: process_failure (exit 3); the operator fixes it and retries."""
    rig.analyst_cards = [card('Plain prompt tweak', predicate=False)]
    rig.effects = {'Plain prompt tweak': dict(val=-.05)}
    down = threading.Event()
    down.set()
    rig.fail = lambda cid, attempt: down.is_set()
    with rig:
        assert rig.run() == 3, capsys.readouterr()
        st = rig.state()
        assert st['halt_reason'] == 'process_failure' and not st['baseline_done']
        assert 'retry-failed' in capsys.readouterr().out
        down.clear()
        assert rig.run() == 3                     # a plain rerun replays v2's stored 'failed' result
        assert rig.run('--retry-failed') == 0, capsys.readouterr()
        st = rig.state()
        assert st['key_generation'] == 1 and st['baseline_done'] and st['halt_reason'] == 'max_rounds'
        # k_base slots, each re-run only along v2's bounded infra-retry chain: infra_retry=1 per generation.
        root = next(c for c in rig.candidates() if c['id'] == rig.tree['root_candidate_id'])
        base = [a for a in root['attempts'] if a.get('batch_id') is None]
        assert [a['execution_state'] for a in base] == ['failed'] * 4 + ['finished'] * 2
        # v3 rule (user decision; v2 would call it unobserved -> unmeasurable): the root's four dead
        # attempts count every case their plan scheduled as failed, so the screen decides normally.
        cards, _ = records(rig)
        check = cards['Plain prompt tweak']['safeguards']
        assert cards['Plain prompt tweak']['verdict'] == 'screen_fail' and check['ok']
        planned = len(rig.protocol['splits']['search']) + len(rig.protocol['splits']['validation'])
        ref = check['infrastructure']['reference']
        dead = [a for a in ref['attempts'] if a['counted_from_plan']]
        assert len(dead) == 4 and all(a['total'] == a['failed'] == planned and a['state'] == 'failed' for a in dead)
        assert (ref['total'], ref['failed'], ref['unobserved']) == (6 * planned, 4 * planned, 0)
        rate = next(c for c in check['checks'] if c['key'] == 'infrastructure_failure_rate')
        assert rate['reference'] == pytest.approx(4 / 6) and rate['subject'] == 0.0 and rate['breached'] is False
        ledger = json.loads((rig.run_dir / ledger_io.LEDGER_FILE).read_text())
        assert ledger['selection']['screened_candidates'][0]['safeguards'] == check
        assert 'v2 treats it as unobserved' in ledger['context']['safeguards']['infrastructure_rule']


def test_a_candidate_whose_own_attempt_died_breaches_infrastructure_failure_rate(rig, capsys):
    rig.analyst_cards = [card('Retry empty retrievals')]
    rig.effects = {'Retry empty retrievals': dict(GOOD)}
    died = set()

    def first_screen_attempt_dies(cid, attempt):
        if rig.chain(cid) == ['Retry empty retrievals'] and attempt.get('batch_id') is None and not died:
            died.add(attempt['execution_id'])
        return attempt['execution_id'] in died
    rig.fail = first_screen_attempt_dies
    with rig:
        assert rig.run() == 0, capsys.readouterr()
        cards, rounds = records(rig)
        rec = cards['Retry empty retrievals']
        check = rec['safeguards']
        planned = len(rig.protocol['splits']['search']) + len(rig.protocol['splits']['validation'])
        sub = check['infrastructure']['subject']
        assert [a['execution_id'] for a in sub['attempts'] if a['counted_from_plan']] == sorted(died)
        assert (sub['total'], sub['failed']) == (3 * planned, planned)       # v2 retried the dead slot once
        rate = next(c for c in check['checks'] if c['key'] == 'infrastructure_failure_rate')
        assert rate['subject'] == pytest.approx(1 / 3) and rate['reference'] == 0.0 and rate['breached']
        assert rec['verdict'] == 'unmeasurable' and check['reasons'] == ['safeguard_breached:infrastructure_failure_rate']
        assert rounds[0]['screen_passed'] == 0 and not batches(rig.repo, rig.tree['id'])


def test_kill_while_the_analyst_drafts_keeps_its_cards_on_rerun(rig, capsys):
    """The analyst works up to agent_timeout (90 min on the server): the likeliest kill window."""
    rig.analyst_cards = [card('Overfit retrieval cache'), card('Plain prompt tweak', predicate=False)]
    rig.effects = {'Overfit retrieval cache': dict(val=.2, fresh=-.2, fix_empty=True),
                   'Plain prompt tweak': dict(val=-.05)}
    rig.kill_during_analysis = True
    with rig:
        with pytest.raises(Crash):
            rig.run()
        assert rig.state()['current_round']['cards'] is None      # killed before the cards were saved
        rig.crashed.clear()
        assert rig.run() == 0, capsys.readouterr()
        cards, rounds = records(rig)
        assert rounds[0]['cards_generated'] == 2
        assert set(cards) == {'Overfit retrieval cache', 'Plain prompt tweak'}
        # The rerun named the same evidence file to the same (replayed) Agent; it was not rewritten.
        [(path, digest)] = set(rig.evidence_read)
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest
        assert len(rig.intents('analyst')) == 1 and len(rig.messenger.commands) == 1 + 2
        rig.assert_no_duplicate_measurements()


# ---------------------------------------------------------------------------
# analyse-only (server stage 1) against the same real services
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('language', ['zh', 'en'])
def test_analyse_only_dispatches_one_node_analyst_and_binds_nothing(rig, capsys, language):
    rig.analyst_cards = [card('Retry empty retrievals'), card('Plain prompt tweak', predicate=False)]
    rig.effects = {'Retry empty retrievals': dict(GOOD), 'Plain prompt tweak': dict(val=-.05)}
    rig.language = language
    with rig:
        code = cli.main(rig.args('analyse-only', '--protocol', str(rig.protocol_file), '--n-cards', '2',
                                 run_id='stage1'))
        out = capsys.readouterr().out
        assert code == 0, out
        assert 'cards: 2' in out and 'predicate: yes (decrease) fields: trace.n_empty_retrievals' in out
        # The evidence is a file in the run directory, named (path + sha256) by a short message.
        [sent] = rig.messenger.commands
        body = json.loads(sent['text'])
        path, digest = re.search(r'analyst evidence: (\S+) sha256 ([0-9a-f]{64})', out).groups()
        assert Path(path).parent == (rig.run_dir / cli.EVIDENCE_DIR).resolve() and Path(path).is_file()
        data = Path(path).read_bytes()
        assert hashlib.sha256(data).hexdigest() == digest and rig.evidence_read == [(path, digest)]
        assert path in body['message'] and digest in body['message']
        assert len(body['message'].encode('utf-8')) < 2000 and len(sent['text']) < 6000
        evidence = data.decode('utf-8')
        assert '[failure]' in evidence and 'qasper:' not in evidence and 'GOLD' not in evidence
        stats = dict(re.findall(r'(\w+)=(\S+)', out.split('last_analysis: ')[1].splitlines()[0]))
        assert int(stats['budget']) == 12000 and int(stats['shown']) + int(stats['omitted']) == int(stats['with_view'])
        if language == 'zh':
            assert len(data) > 8192                    # more than v2's message text cap would allow
        # v3 Agents get the short v3 brief; a manual Agent of the same task keeps the workbench brief.
        [brief] = body['context']
        assert brief['reference']['kind'] == 'brief' and brief['title'].startswith('Method-v3 analyst brief')
        assert len(brief['text']) <= 2500 and 'proposal draft --parent' in brief['text']
        assert 'node run' not in brief['text'] and 'iteration ledger' not in brief['text']
        manual = rig.creation.store.prepare(rig.task_id, 'pi-flash', actor='alice', request_key='manual-analyst',
                                            role='analyst', candidate_id=rig.tree['root_candidate_id'])
        manual = rig.creation.store.attempt(rig.task_id, manual['id'], actor='alice', bridge=rig.creator)
        v2_brief = resolve_context(rig.repo, rig.task_id, dict(kind='brief', session_id=manual['session_id']),
                                   run_views=rig.views)
        assert v2_brief['title'].startswith('分析 Agent 工作说明') and 'Method-v3' not in v2_brief['text']
        assert auto_runs.binding(rig.repo, rig.tree['id']) is None
        root = rig.tree['root_candidate_id']
        assert [(i.get('candidate_id'), i.get('scope'), i.get('brief')) for i in rig.intents('analyst')] == [
            (root, None, 'method-v3'), (root, None, None)]           # v3's node analyst, then the manual one
        assert [c['id'] for c in rig.candidates()] == [root]          # nothing derived
        assert not batches(rig.repo, rig.tree['id'])                   # no fresh batch touched
        rig.assert_no_duplicate_measurements()


# ---------------------------------------------------------------------------
# method-v3 §2.1 失败后的处理 (amendment 2026-11-03) against the real v2 services
# ---------------------------------------------------------------------------

def _protocol_with(rig, **changes):
    rig.protocol_file.write_text(json.dumps(dict(json.loads(rig.protocol_file.read_text()), **changes)))


def _attempt_rows(st):
    return [c for r in st['rounds'] + ([st['current_round']['round']] if st.get('current_round') else [])
            for c in r['candidates']]


def test_a_behavioural_smoke_failure_is_repaired_on_a_sibling_and_the_repair_passes(rig, capsys):
    rig.analyst_cards = [card('Retry empty retrievals')]
    rig.effects = {'Retry empty retrievals': dict(val=.2, fresh=.2, fix_empty='after_repair')}
    with rig:
        assert rig.run() == 0, capsys.readouterr()
        st = rig.state()
        rows = _attempt_rows(st)
        assert [(c['attempt'], c['verdict'], c['superseded'], c['smoke_verdict']) for c in rows] == [
            (1, 'smoke_fail', True, 'fail'), (2, 'screen_pass', False, 'pass')]
        first, second = rows
        assert second['repair_of'] == first['candidate'] and second['repaired']
        assert st['rounds'][0]['confirmation_verdict'] == 'confirmed'
        # v2: the repair is a sibling under the same parent that starts from the failed change.
        root = rig.tree['root_candidate_id']
        v2 = {c['id']: c for c in rig.candidates()}
        failed, repaired = v2[first['candidate_id']], v2[second['candidate_id']]
        assert failed['parent_candidate_id'] == repaired['parent_candidate_id'] == root
        assert failed['status'] == repaired['status'] == 'frozen'
        assert [(i['candidate_id'], i['method']) for i in repaired['integrated_from']] == [(failed['id'], 'cherry-pick')]
        refiled = rig.api.hypotheses.get(rig.task_id, repaired['proposal_id'])
        assert repaired['proposal_id'] != failed['proposal_id'] and refiled['status'] in ('approved', 'implementing')
        assert refiled['card']['title'] == 'Retry empty retrievals' and refiled['parent_candidate_id'] == root
        # The repair implementer: a new v3 implementer Agent of the sibling, reading one feedback file.
        [rep] = rig.repairs
        assert rep['candidate_id'] == repaired['id']
        assert Path(rep['path']).parent == (rig.run_dir / cli.EVIDENCE_DIR).resolve()
        fb = rep['feedback']
        assert fb.count('predicate parent=true, previous attempt=true') == 2 and 'should decrease' in fb
        assert '[parent]' in fb and '[previous attempt]' in fb
        assert 'qasper:' not in fb and 'GOLD' not in fb and 'answer_f1' not in fb and 'score' not in fb
        body = json.loads(rep['text'])
        assert body['context'][0]['title'].startswith('Method-v3 implementer brief · ' + repaired['id'])
        assert 'feedback file' in body['context'][0]['text'] and len(body['message'].encode()) < 4000
        assert [i.get('candidate_id') for i in rig.intents('implementer')] == [failed['id'], repaired['id']]
        # History: the adopted (then confirmed) hypothesis is marked as repaired.
        assert [(h['label'], h.get('repaired')) for h in st['history']] == [('confirmed', True)]
        view = run_view.read_run(rig.run_dir)
        assert [a['key'] for a in view['rounds'][0]['attempts']] == ['1:0', '1:0:a2']
        rig.assert_no_duplicate_measurements()      # the parent's smoke attempt is shared, not repeated


def test_a_repair_that_fails_again_reaches_the_next_analyst_as_smoke_fail_after_repair(rig, capsys):
    root = rig.tree['root_candidate_id']
    calls = []

    def cards(parent):
        calls.append(parent)
        return ([card('Rerank without effect'), card('Plain prompt tweak', predicate=False)] if len(calls) == 1
                else [card('Another prompt tweak', predicate=False)])
    rig.analyst_cards = cards
    rig.effects = {'Rerank without effect': dict(val=.2), 'Plain prompt tweak': dict(val=-.05),
                   'Another prompt tweak': dict(val=-.05)}
    _protocol_with(rig, max_rounds=2, analyst_cards=2)
    with rig:
        assert rig.run() == 0, capsys.readouterr()
        st = rig.state()
        rerank = [(c['attempt'], c['verdict'], c['superseded']) for c in _attempt_rows(st)
                  if c['title'] == 'Rerank without effect']
        assert rerank == [(1, 'smoke_fail', True), (2, 'smoke_fail', False)]
        assert len(rig.repairs) == 1 and calls == [root, root]
        evidence = Path(rig.evidence_read[1][0]).read_text()
        assert '- Rerank without effect -> smoke_fail; after repair\n' in evidence
        assert '- Plain prompt tweak -> screen_fail; smoke: behaviour unchecked\n' in evidence
        assert 'no adoption under this parent for 1 consecutive round(s)' in evidence
        rig.assert_no_duplicate_measurements()


def test_every_hypothesis_of_a_rolled_back_segment_reaches_the_certified_node(rig, capsys):
    a, b, c = 'Retry empty retrievals', 'Expand sub-queries', 'Plain prompt tweak'
    calls = []

    def cards(parent):
        calls.append(parent)
        return [card(a), card(b, predicate=False), card(c, predicate=False)] if len(calls) == 1 else []
    rig.analyst_cards = cards
    rig.effects = {a: dict(val=.2, fresh=-.1, fix_empty=True), b: dict(val=.2, fresh=-.1), c: dict(val=-.1)}
    _protocol_with(rig, max_rounds=2, b_cards=3, analyst_cards=3, T=0.3)

    def lineage_len():
        try:
            return len(rig.state()['lineage'])
        except (OSError, ValueError, KeyError):
            return 0

    def decided(title):
        try:
            return any(x['title'] == title for x in _attempt_rows(rig.state()))
        except (OSError, ValueError, KeyError):
            return False
    # C is decided after A's adoption (rebased onto the provisional A); B (on the seed) waits until
    # C is decided, is then rebased onto A and adopted: a two-node segment A -> B reaching T.
    rig.hold = lambda cid: ((rig.chain(cid) == [c] and lineage_len() < 2)
                            or (rig.chain(cid) == [b] and not decided(c)))
    with rig:
        assert rig.run() == 0, capsys.readouterr()
        st = rig.state()
        rows = {x['title']: x for x in _attempt_rows(st)}
        a_hash = rows[a]['candidate']
        assert rows[c]['verdict'] == 'screen_fail' and rows[c]['parent'] == a_hash
        assert rows[b]['verdict'] == 'screen_pass' and rows[b]['parent'] == a_hash
        conf = st['rounds'][0]['confirmations']
        assert [x['verdict'] for x in conf] == ['rejected'] and conf[0]['segment_steps'] == 2
        assert [n['state'] for n in st['lineage']] == ['certified', 'rolled_back', 'rolled_back']
        root_hash = st['lineage'][0]['version_hash']
        assert {h['title']: (h['parent'], h.get('tried_under')) for h in st['history']} == {
            a: (root_hash, None), c: (root_hash, a_hash), b: (root_hash, a_hash)}
        # Round 2 diagnoses the certified node: all three hypotheses, the moved ones marked.
        evidence = Path(rig.evidence_read[1][0]).read_text()
        lines = [line for line in evidence.splitlines() if line.startswith('- ')]
        assert lines == [f'- {a} -> rejected',
                         f'- {c} -> screen_fail; smoke: behaviour unchecked; tried in a rolled-back segment',
                         f'- {b} -> rejected; tried in a rolled-back segment; attribution: unattributed']
        assert st['rounds'][1]['analysis']['no_adoption_rounds'] == 0     # the pointer moved: first tier
        rig.assert_no_duplicate_measurements()


def test_no_adoption_rounds_rotate_the_cases_and_step_up_the_evidence(rig, capsys):
    calls = []

    def cards(parent):
        calls.append(parent)
        return [card(f'Prompt tweak {len(calls)}', predicate=False)]
    rig.analyst_cards = cards
    rig.effects = {f'Prompt tweak {i}': dict(val=-.05) for i in (1, 2, 3)}
    _protocol_with(rig, max_rounds=3, b_cards=1, analyst_cards=1, max_rounds_without_screen_pass=3,
                   analyst_max_failures=2, analyst_max_failures_step=1, analyst_view_budget_step=1000)
    with rig:
        assert rig.run() == 0, capsys.readouterr()
        st = rig.state()
        root_hash = st['lineage'][0]['version_hash']
        analysis = [r['analysis'] for r in st['rounds']]
        assert [x['no_adoption_rounds'] for x in analysis] == [0, 1, 2]
        assert [x['max_failures'] for x in analysis] == [2, 3, 4]
        assert [x['failures_new'] for x in analysis] == [2, 3, 1]       # 6 failing search cases
        assert [x['failures_shown'] for x in analysis] == [2, 3, 4]
        assert [x['adapter']['budget'] for x in analysis] == [12000, 13000, 14000]
        assert st['no_adoption'] == dict(parent=root_hash, rounds=3)
        failing = sorted(c for c in rig.protocol['splits']['search'] if int(c[-1]) < 2)
        assert sorted(st['analyst_shown'][root_hash]) == failing
        texts = [json.loads(m['text'])['message'] for m in rig.messenger.commands if 'proposal draft' in m['text']]
        assert len(texts) == 3 and 'consecutive' not in texts[0]
        assert 'for 1 consecutive round(s): change direction' in texts[1]
        assert 'for 2 consecutive round(s): change direction' in texts[2]
        third = Path(rig.evidence_read[2][0]).read_text()
        assert 'cases: 7 with a view of 7 selected' in third and '(budget 14000 chars)' in third   # + 3 controls
        assert '- Prompt tweak 1 -> screen_fail; smoke: behaviour unchecked' in third
        rig.assert_no_duplicate_measurements()
