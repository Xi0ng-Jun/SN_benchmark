"""Iterations and candidate trees; identity is written where it is produced.

An iteration fixes a start commit and one frozen plan revision inside its own
backing Task, so plans, Jobs and executions keep their Task-scoped records.
Candidates form a single-parent tree. A child worktree starts at its parent's
frozen commit, never at the project HEAD. The coordinator, not an Agent,
freezes a candidate: it commits the worktree and captures the committed bytes.
Later edits need a derived child. Each run of a frozen candidate is a numbered
attempt bound to one Job and its verified preparation, and the runner receives
that identity explicitly. Browsing never selects what runs.

Legacy Tasks map to iterations with evidenced fields only. Unknown start,
parent or run ownership stays unknown; manual links record their basis and are
never inferred from Git ancestry, time, paths or a shared Task.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re

from ..sn_source_capture import capture_sn_source
from .hypothesis_worktrees import PaseoWorkspaceCreator, _has_table, _workspace
from .repository import Conflict, UnknownIdentity, _text, now, uid
from .task_worktree import TaskWorktreeService, _branch, _private, _status, _text_git, _validate_worktree

_UNKNOWN = 'Workspace creation is uncertain; retry performs read-only reconciliation.'
_COMMIT = re.compile('[0-9a-f]{40}')
_IDENTITY = re.compile('[0-9a-f]{32}')
COORDINATOR = ('sn-eval-coordinator', 'sn-eval-coordinator@localhost')
DECISIONS = ('adopted', 'rejected', 'undecided')
INTEGRATION_METHODS = ('merge', 'cherry-pick', 'rewrite')


# Branches the workbench creates for candidates and legacy hypotheses; not offered as start refs.
_CANDIDATE_REFS = ('sn-cand-', 'sn-hyp-')


def initialize_iterations(repository):
    with repository.connect(write=True) as conn:
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS iterations(
                id TEXT PRIMARY KEY, task_id TEXT NOT NULL UNIQUE REFERENCES tasks(id),
                origin TEXT NOT NULL, title TEXT NOT NULL, start_commit TEXT,
                plan_id TEXT, plan_revision TEXT, plan_content_hash TEXT, selection_hash TEXT,
                protocol_json TEXT, root_candidate_id TEXT, seeded_from_json TEXT,
                unknown_json TEXT NOT NULL, created_by TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS candidates(
                id TEXT PRIMARY KEY, iteration_id TEXT NOT NULL REFERENCES iterations(id),
                parent_candidate_id TEXT REFERENCES candidates(id), parent_commit TEXT,
                proposal_id TEXT, origin TEXT NOT NULL, status TEXT NOT NULL,
                frozen_commit TEXT, source_ref_json TEXT, integrated_from_json TEXT NOT NULL,
                inspired_by_json TEXT NOT NULL, decision_json TEXT, note TEXT,
                unknown_json TEXT NOT NULL, created_by TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS candidate_worktrees(
                candidate_id TEXT PRIMARY KEY REFERENCES candidates(id),
                status TEXT NOT NULL, branch TEXT NOT NULL, base_revision TEXT NOT NULL,
                project_common_dir TEXT NOT NULL, workspace_json TEXT, binding_json TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS candidate_attempts(
                candidate_id TEXT NOT NULL REFERENCES candidates(id), attempt INTEGER NOT NULL,
                task_id TEXT NOT NULL REFERENCES tasks(id), job_id TEXT NOT NULL UNIQUE,
                preparation_id TEXT NOT NULL, execution_id TEXT,
                created_by TEXT NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY(candidate_id, attempt));
            CREATE TABLE IF NOT EXISTS candidate_links(
                candidate_id TEXT NOT NULL REFERENCES candidates(id), execution_id TEXT NOT NULL,
                task_id TEXT NOT NULL REFERENCES tasks(id), basis_json TEXT NOT NULL,
                actor TEXT NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY(candidate_id, execution_id));
        ''')
        if _has_table(conn, 'hypotheses') and 'parent_candidate_id' not in {
                r['name'] for r in conn.execute('PRAGMA table_info(hypotheses)')}:
            conn.execute('ALTER TABLE hypotheses ADD COLUMN parent_candidate_id TEXT')
        if 'candidate_id' not in {r['name'] for r in conn.execute('PRAGMA table_info(task_sessions)')}:
            conn.execute('ALTER TABLE task_sessions ADD COLUMN candidate_id TEXT')

    from .splits import initialize
    initialize(repository)
    from .execution_scopes import initialize as initialize_scopes
    initialize_scopes(repository)


def _json(value):
    return None if value is None else json.loads(value)


def _commit(value, field='commit'):
    if type(value) is not str or not _COMMIT.fullmatch(value):
        raise ValueError('Invalid ' + field + ': a full 40-character commit is required')
    return value


def _identity(value, field):
    if type(value) is not str or not _IDENTITY.fullmatch(value):
        raise ValueError('Invalid ' + field)
    return value


def _candidate_record(row):
    record = dict(row)
    for key in ('source_ref', 'integrated_from', 'inspired_by', 'decision', 'unknown'):
        record[key] = _json(record.pop(key + '_json'))
    return record


def _iteration_record(row):
    record = dict(row)
    for key in ('protocol', 'seeded_from', 'unknown'):
        record[key] = _json(record.pop(key + '_json'))
    return record


def candidate_binding(conn, candidate_id):
    """The immutable start of a candidate's worktree, or None when uncreated."""
    if not _has_table(conn, 'candidate_worktrees'):
        return None
    row = conn.execute('SELECT status,binding_json FROM candidate_worktrees WHERE candidate_id=?',
                       (candidate_id,)).fetchone()
    if row is None:
        return None
    if row['status'] != 'ready' or not row['binding_json']:
        raise Conflict('Candidate worktree creation is not confirmed')
    return json.loads(row['binding_json'])


class _CandidateWorktreeView(TaskWorktreeService):
    """Read a candidate's diff against its parent commit with the Task reader."""
    def __init__(self, trees, task_id, candidate_id):
        super().__init__(trees.repository, trees.store, project_root=trees.project_root)
        self.task_id, self.candidate_id = task_id, candidate_id

    def binding(self, task_id):
        if task_id != self.task_id:
            raise ValueError('Candidate worktree view belongs to a different Task')
        with self.repository.connect() as conn:
            return candidate_binding(conn, self.candidate_id)

    def bind(self, *args, **kwargs):
        raise Conflict('Candidate worktree bindings are immutable')


class IterationService:
    def __init__(self, repository, task_worktree_service, *, plans=None, jobs=None, executions=None,
                 creator=None, preparation_config=None, execution_config=None, build_spec=None, admission=None,
                 initialize=True):
        self.repository = repository
        self.trees = task_worktree_service
        self.plans = plans
        self.jobs = jobs
        self.executions = executions
        self.creator = creator if creator is not None else PaseoWorkspaceCreator()
        self.preparation_config = preparation_config
        self.execution_config = execution_config
        self.build_spec = build_spec
        # Judges one measurement (attempt or evidence link) on its own; without
        # it no candidate counts as evaluated.
        self.admission = admission
        # A reader inside another write transaction (an Agent brief resolved while a
        # message is prepared) must not open a second writer.
        if initialize:
            initialize_iterations(repository)

    # ------------------------------------------------------------ reading
    def _iteration(self, conn, iteration_id):
        row = conn.execute('SELECT * FROM iterations WHERE id=?', (iteration_id,)).fetchone()
        if row is None:
            raise UnknownIdentity('Unknown iteration identity')
        return _iteration_record(row)

    def _candidate(self, conn, candidate_id):
        row = conn.execute('SELECT * FROM candidates WHERE id=?', (candidate_id,)).fetchone()
        if row is None:
            raise UnknownIdentity('Unknown candidate identity')
        return _candidate_record(row)

    def _attempts(self, conn, candidate_id):
        return [dict(r) for r in conn.execute(
            'SELECT * FROM candidate_attempts WHERE candidate_id=? ORDER BY attempt', (candidate_id,))]

    def _execution(self, task_id, execution_id):
        if execution_id is None or self.executions is None:
            return None
        try:
            return self.executions.get(task_id, execution_id)
        except ValueError:
            return None

    def _measured(self, row):
        execution = self._execution(row['task_id'], row['execution_id'])
        measured = dict(row, execution_state=execution['state'] if execution else None,
                        run_dir=execution['run_dir'] if execution else None)
        if str(measured.get('batch_id') or '').startswith('sealed:'):
            measured['admission'] = dict(status='protected', reason='sealed measurement is aggregate-only')
        else:
            measured['admission'] = self.admission(measured) if self.admission else None
        return measured

    def _view(self, conn, candidate):
        attempts = [self._measured(a) for a in self._attempts(conn, candidate['id'])]
        links = []
        for row in conn.execute('SELECT * FROM candidate_links WHERE candidate_id=? ORDER BY created_at',
                                (candidate['id'],)):
            link = dict(row)
            link['basis'] = json.loads(link.pop('basis_json'))
            links.append(self._measured(link))
        # The first admitted measurement, in recorded order and regardless of score.
        reference = next((dict(attempt=m.get('attempt'), execution_id=m['execution_id'])
                          for m in attempts + links if (m['admission'] or {}).get('status') == 'admitted'), None)
        tree = conn.execute('SELECT status,branch,base_revision,binding_json FROM candidate_worktrees '
                            'WHERE candidate_id=?', (candidate['id'],)).fetchone()
        worktree = None
        if tree is not None:
            binding = _json(tree['binding_json'])
            worktree = dict(status=tree['status'], branch=tree['branch'], base_revision=tree['base_revision'],
                            path=binding['worktree_path'] if binding else None,
                            detail=None if tree['status'] == 'ready' else _UNKNOWN)
        states = [a['execution_state'] for a in attempts]
        if candidate['origin'].startswith('legacy'):
            state = 'legacy'
        elif candidate['status'] != 'frozen':
            state = {'provisioning': 'provisioning', 'ready': 'implementing'}[candidate['status']]
        elif 'running' in states:
            state = 'running'
        elif reference is not None:
            state = 'evaluated'
        elif states:
            state = 'failed'
        else:
            state = 'frozen'
        return dict(candidate, state=state, attempts=attempts, links=links, worktree=worktree, reference=reference)

    def _evaluated(self, conn, candidate_id):
        return self._view(conn, self._candidate(conn, candidate_id))['reference'] is not None

    def iterations(self, workspace_id=None):
        with self.repository.connect() as conn:
            query = 'SELECT i.* FROM iterations i JOIN tasks t ON t.id=i.task_id'
            rows = conn.execute(query + (' WHERE t.workspace_id=?' if workspace_id else '') +
                                ' ORDER BY i.created_at,i.id', (workspace_id,) if workspace_id else ()).fetchall()
            return [_iteration_record(r) for r in rows]

    def iteration(self, iteration_id):
        """The iteration, its candidates in tree order, and its proposals."""
        with self.repository.connect() as conn:
            iteration = self._iteration(conn, iteration_id)
            rows = [_candidate_record(r) for r in conn.execute(
                'SELECT * FROM candidates WHERE iteration_id=? ORDER BY created_at,id', (iteration_id,))]
            children = {}
            for row in rows:
                children.setdefault(row['parent_candidate_id'], []).append(row)
            ordered, known = [], {row['id'] for row in rows}

            def visit(node, depth):
                ordered.append(dict(self._view(conn, node), depth=depth))
                for child in children.get(node['id'], []):
                    visit(child, depth + 1)
            for row in rows:
                if row['parent_candidate_id'] is None or row['parent_candidate_id'] not in known:
                    visit(row, 0)
            proposals = []
            if _has_table(conn, 'hypotheses'):
                for r in conn.execute('SELECT id,status,card,parent_candidate_id,created_at FROM hypotheses '
                                      'WHERE task_id=? ORDER BY created_at,id', (iteration['task_id'],)):
                    card = json.loads(r['card'])
                    proposals.append(dict(id=r['id'], status=r['status'], title=card.get('title'),
                                          parent_candidate_id=r['parent_candidate_id'], created_at=r['created_at']))
            unassigned = self._unassigned(conn, iteration)
        from .splits import batches, roles
        used = batches(self.repository, iteration_id)
        pool = roles(iteration.get('protocol'), {b['batch_id'] for b in used if b['completed_at']})
        return dict(iteration, candidates=ordered, proposals=proposals, unassigned_executions=unassigned,
                    fresh_usage=used, validation_pool=[c for c, r in (pool or {}).items() if r == 'validation'])

    def _unassigned(self, conn, iteration):
        owned = {r['execution_id'] for r in conn.execute(
            'SELECT a.execution_id FROM candidate_attempts a WHERE a.task_id=? AND a.execution_id IS NOT NULL '
            'UNION SELECT l.execution_id FROM candidate_links l WHERE l.task_id=?',
            (iteration['task_id'], iteration['task_id']))}
        return [r['object_id'] for r in conn.execute(
            "SELECT object_id FROM history WHERE task_id=? AND kind='job.execution_started' ORDER BY seq",
            (iteration['task_id'],)) if r['object_id'] not in owned]

    def candidate(self, candidate_id):
        with self.repository.connect() as conn:
            return self._view(conn, self._candidate(conn, candidate_id))

    def candidate_for_job(self, task_id, job_id):
        with self.repository.connect() as conn:
            row = conn.execute('SELECT candidate_id FROM candidate_attempts WHERE task_id=? AND job_id=?',
                               (task_id, job_id)).fetchone()
        return None if row is None else row['candidate_id']

    def view(self, candidate_id):
        with self.repository.connect() as conn:
            candidate = self._candidate(conn, candidate_id)
            task_id = self._iteration(conn, candidate['iteration_id'])['task_id']
        return _CandidateWorktreeView(self.trees, task_id, candidate_id)

    # ------------------------------------------------------------ creation
    def _receipt(self, scope, request_key, payload):
        with self.repository.connect() as conn:
            return self.repository._prior(conn, scope, request_key, payload)

    def _project_commit(self, commit):
        project = self.trees.project_root
        if _text_git(project, 'rev-parse', '--show-toplevel') != str(project):
            raise ValueError('Trusted project must be a Git repository root')
        try:
            resolved = _text_git(project, 'rev-parse', '--verify', '--end-of-options', commit + '^{commit}')
        except ValueError:
            raise ValueError('Start commit is not in the trusted project repository') from None
        if resolved != commit:
            raise ValueError('Start commit is not in the trusted project repository')
        return commit

    def project_commits(self, workspace_id, *, query=None, limit=30):
        """Start-commit choices: HEAD, branch and tag tips, recent history — or one commit resolved
        from a short hash, branch or tag — plus frozen candidates a new iteration can be seeded from.

        Read-only; candidate branches are left out of the refs, they appear as seeds instead.
        """
        project = self.trees.project_root
        fmt = '--format=%H%x1f%s%x1f%cI%x1f%D'

        def log(*args):
            text = _text_git(project, 'log', fmt, *args)
            return [dict(zip(('commit', 'subject', 'date', 'refs'), line.split('\x1f'))) for line in text.splitlines() if line]
        if query is not None:
            query = _text(query, 'commit query', limit=200).strip()
            try:
                if query.startswith('-'):
                    raise ValueError
                found = _text_git(project, 'rev-parse', '--verify', '--end-of-options', query + '^{commit}')
            except ValueError:
                raise ValueError('在 SN 仓库里找不到这个 commit、分支或 tag') from None
            rows = log('--no-walk', found)
        else:
            tips = [line.split('\x1f') for line in _text_git(
                project, 'for-each-ref', '--format=%(objectname)\x1f%(*objectname)\x1f%(refname:short)', 'refs/heads', 'refs/tags').splitlines()]
            tips = [peeled or obj for obj, peeled, name in tips if not name.startswith(_CANDIDATE_REFS)]
            head = _text_git(project, 'rev-parse', 'HEAD')
            rows = log('--no-walk', head) + (log('--no-walk=sorted', *dict.fromkeys(tips)) if tips else []) + log('-n', str(limit), 'HEAD')
        seen, commits = set(), []
        for row in rows:
            if row['commit'] in seen:
                continue
            seen.add(row['commit'])
            decorations = [r for r in row.pop('refs').split(', ') if r]
            refs = [r.removeprefix('HEAD -> ') for r in decorations if r != 'HEAD']
            commits.append(dict(row, head=query is None and not commits,
                                refs=[r for r in refs if not r.removeprefix('tag: ').startswith(_CANDIDATE_REFS)]))
        with self.repository.connect() as conn:
            seeds = [dict(iteration_id=r['iteration_id'], iteration_title=r['title'], candidate_id=r['id'],
                          root=r['id'] == r['root_candidate_id'], frozen_commit=r['frozen_commit'], note=r['note'],
                          decision=json.loads(r['decision_json']).get('decision') if r['decision_json'] else None)
                     for r in conn.execute(
                         "SELECT c.id, c.iteration_id, c.frozen_commit, c.note, c.decision_json, c.created_at, i.title, "
                         "i.root_candidate_id FROM candidates c JOIN iterations i ON i.id=c.iteration_id "
                         "JOIN tasks t ON t.id=i.task_id WHERE t.workspace_id=? AND i.origin='native' "
                         "AND c.status='frozen' AND c.frozen_commit IS NOT NULL ORDER BY c.created_at DESC", (workspace_id,))]
        seeds.sort(key=lambda s: s['decision'] != 'adopted')
        return dict(commits=commits, seeds=seeds[:20])

    def create_iteration(self, workspace_id, *, title, start_commit, input_id, expected_bundle_id,
                         case_ids, mode, actor, request_key, seeded_from=None, sealed_ref=None,
                         split_seed=0, fresh_batch_count=0, fresh_papers_per_batch=8, budget=None):
        """Create the backing Task, freeze its plan, then provision root C0.

        Retrying the same request key completes an interrupted creation: each
        step below has its own derived idempotency key.
        """
        actor = _text(actor, 'actor', limit=200)
        title = _text(title, 'iteration title', limit=200)
        _text(request_key, 'request key', limit=200)
        _commit(start_commit, 'start commit')
        if self.plans is None:
            raise ValueError('Iteration plans are not configured')
        from .budgets import validate_policy
        budget = validate_policy(budget)
        binding = ((self.preparation_config or {}).get('inputs') or {}).get(input_id)
        if not isinstance(binding, dict) or set(binding) != {'bundle_ref', 'index_ref'}:
            raise ValueError('No trusted artifact binding for this iteration input')
        payload = dict(workspace_id=workspace_id, title=title, start_commit=start_commit, input_id=input_id,
                       expected_bundle_id=expected_bundle_id, case_ids=case_ids, mode=mode,
                       seeded_from=seeded_from, actor=actor, sealed_ref=sealed_ref, split_seed=split_seed,
                       fresh_batch_count=fresh_batch_count, fresh_papers_per_batch=fresh_papers_per_batch)
        if budget is not None:
            payload['budget'] = budget
        scope = 'iteration.create:' + workspace_id
        prior = self._receipt(scope, request_key, payload)
        if prior is not None:
            self._settle_root(prior['id'])
            return self.iteration(prior['id'])
        if seeded_from is not None:
            if type(seeded_from) is not dict or set(seeded_from) != {'iteration_id', 'candidate_id'}:
                raise ValueError('seeded_from names an iteration and one of its candidates')
            with self.repository.connect() as conn:
                source = self._candidate(conn, seeded_from['candidate_id'])
            if source['iteration_id'] != seeded_from['iteration_id'] or source['status'] != 'frozen':
                raise ValueError('An iteration is seeded only from a frozen candidate of that iteration')
            if source['frozen_commit'] != start_commit:
                raise ValueError("A seeded iteration starts at the source candidate's frozen commit")
        from .splits import partition
        ref = sealed_ref or (self.preparation_config or {}).get('sealed_ref', 'selection:20261013:holdout')
        registered = (self.preparation_config or {}).get('sealed_selections', {}).get(ref)
        if (not isinstance(registered, dict) or registered.get('bundle_id') != expected_bundle_id
                or not isinstance(registered.get('case_ids'), list)):
            raise ValueError('Unknown or mismatched trusted sealed selection')
        catalogue = self.plans.inputs.describe(input_id)
        split = partition(catalogue['partitions'], case_ids, sealed=registered['case_ids'], seed=split_seed,
                          batches=fresh_batch_count, papers_per_batch=fresh_papers_per_batch)
        role_map = {c: r for r in ('search', 'validation') for c in split[r]}
        self._project_commit(start_commit)
        task = self.repository.create_task(workspace_id, title, '迭代：起点 ' + start_commit[:12] + '，冻结题单',
                                           request_key='iteration:' + request_key)
        plan = self.plans.save(task_id=task['id'], input_id=input_id, expected_bundle_id=expected_bundle_id,
                               case_ids=case_ids, title=title, mode=mode, actor=actor,
                               request_key='iteration:' + request_key, roles=role_map)
        fresh = []
        for index, ids in enumerate(split['batches'], 1):
            p = self.plans.save(task_id=task['id'], input_id=input_id, expected_bundle_id=expected_bundle_id,
                                case_ids=ids, title=f'{title} fresh {index}', mode=mode, actor=actor,
                                request_key=f'iteration:{request_key}:fresh:{index}', roles={c: 'validation' for c in ids})
            fresh.append(dict(batch_id=f'f{index}', plan_id=p['plan_id'], plan_revision=p['revision'],
                              plan_content_hash=p['content_hash'], selection_hash=p['content']['selection']['selection_hash'],
                              case_ids=ids, status='unused'))
        content = plan['content']
        protocol = dict(mode=content['execution_intent']['mode'],
                        request_revision=content['execution_intent']['request_revision'],
                        reasoning_protocol='reasoning-v1', input=content['input'],
                        bundle_ref=binding['bundle_ref'], index_ref=binding['index_ref'],
                        scorer='recorded-per-run', k_base=2, success_threshold=.5,
                        splits=dict(search=split['search'], validation=split['validation'],
                                    sealed_ref=ref, sealed=registered['case_ids'], seed=split_seed), fresh_batches=fresh,
                        gate_rule=dict(id='lcd-v1', tau=.5, eta='rerun_se', band='jeffreys90'))
        if budget is not None:
            protocol['budget'] = budget
        project = self.trees.project_root
        common = str(Path(_text_git(project, 'rev-parse', '--path-format=absolute', '--git-common-dir')).resolve())
        iteration_id, root_id, created = uid(), uid(), now()
        with self.repository.connect(write=True) as conn:
            prior = self.repository._prior(conn, scope, request_key, payload)
            if prior is not None:
                iteration_id = prior['id']
            else:
                conn.execute('INSERT INTO iterations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                             (iteration_id, task['id'], 'native', title, start_commit, plan['plan_id'],
                              plan['revision'], plan['content_hash'], content['selection']['selection_hash'],
                              json.dumps(protocol, ensure_ascii=False), root_id,
                              json.dumps(seeded_from) if seeded_from else None, '[]', actor, created))
                self._insert_candidate(conn, root_id, iteration_id, parent=None, parent_commit=None,
                                       base=start_commit, common=common, origin='root', proposal_id=None,
                                       note='起点 C0', integrated_from=[], actor=actor)
                record = dict(id=iteration_id, task_id=task['id'], root_candidate_id=root_id)
                self.repository._event(conn, task['id'], 'iteration.created', iteration_id,
                                       dict(record, start_commit=start_commit, plan_id=plan['plan_id'],
                                            plan_revision=plan['revision'], seeded_from=seeded_from, actor=actor))
                self.repository._receipt(conn, scope, request_key, payload, record)
        self._settle_root(iteration_id)
        return self.iteration(iteration_id)

    def _insert_candidate(self, conn, candidate_id, iteration_id, *, parent, parent_commit, base, common,
                          origin, proposal_id, note, integrated_from, actor):
        created = now()
        branch = 'sn-cand-' + candidate_id
        conn.execute('INSERT INTO candidates VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                     (candidate_id, iteration_id, parent, parent_commit, proposal_id, origin, 'provisioning',
                      None, None, json.dumps(integrated_from, ensure_ascii=False), '[]', None, note, '[]',
                      actor, created, created))
        conn.execute('INSERT INTO candidate_worktrees(candidate_id,status,branch,base_revision,project_common_dir,'
                     'created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
                     (candidate_id, 'unknown', branch, base, common, created, created))
        task_id = self._iteration(conn, iteration_id)['task_id']
        self.repository._event(conn, task_id, 'candidate.requested', candidate_id,
                               dict(iteration_id=iteration_id, parent_candidate_id=parent, parent_commit=parent_commit,
                                    base_revision=base, branch=branch, proposal_id=proposal_id, origin=origin,
                                    integrated_from=integrated_from, actor=actor))

    def _settle_root(self, iteration_id):
        with self.repository.connect() as conn:
            root = self._iteration(conn, iteration_id)['root_candidate_id']
        if root is not None:
            self.provision(root)
            if self.candidate(root)['status'] == 'ready':
                self._freeze_root(root)

    def derive(self, parent_candidate_id, *, actor, request_key, proposal_id=None, note=None,
               integrated_from=()):
        """Create a child at the parent's frozen commit, from a proposal or a note."""
        actor = _text(actor, 'actor', limit=200)
        _text(request_key, 'request key', limit=200)
        _identity(parent_candidate_id, 'parent candidate identity')
        if proposal_id is None:
            note = _text(note, 'derivation note', limit=2000)
        else:
            _identity(proposal_id, 'proposal identity')
        integrated = [dict(item) for item in integrated_from]
        payload = dict(parent=parent_candidate_id, proposal_id=proposal_id, note=note,
                       integrated_from=integrated, actor=actor)
        scope = 'candidate.derive:' + parent_candidate_id
        candidate_id = uid()
        with self.repository.connect(write=True) as conn:
            prior = self.repository._prior(conn, scope, request_key, payload)
            if prior is not None:
                candidate_id = prior['id']
            else:
                parent = self._candidate(conn, parent_candidate_id)
                iteration = self._iteration(conn, parent['iteration_id'])
                if parent['status'] != 'frozen':
                    raise Conflict('Only a frozen candidate can be a parent')
                if proposal_id is not None:
                    card = conn.execute('SELECT status,parent_candidate_id FROM hypotheses WHERE id=? AND task_id=?',
                                        (proposal_id, iteration['task_id'])).fetchone()
                    if card is None or card['parent_candidate_id'] != parent_candidate_id:
                        raise ValueError('The proposal is not drafted under this parent candidate')
                    if card['status'] not in ('approved', 'implementing'):
                        raise Conflict('An approved proposal is required to derive a candidate')
                for item in integrated:
                    if set(item) != {'candidate_id', 'method', 'note'} or item['method'] not in INTEGRATION_METHODS:
                        raise ValueError('integrated_from items name candidate_id, method and note')
                    source = self._candidate(conn, item['candidate_id'])
                    if source['iteration_id'] != parent['iteration_id'] or source['status'] != 'frozen':
                        raise ValueError('Integrated sources are frozen candidates of the same iteration')
                    item['frozen_commit'] = source['frozen_commit']
                common = conn.execute('SELECT project_common_dir FROM candidate_worktrees WHERE candidate_id=?',
                                      (parent_candidate_id,)).fetchone()
                common = common['project_common_dir'] if common else str(Path(_text_git(
                    self.trees.project_root, 'rev-parse', '--path-format=absolute', '--git-common-dir')).resolve())
                self._insert_candidate(conn, candidate_id, parent['iteration_id'], parent=parent_candidate_id,
                                       parent_commit=parent['frozen_commit'], base=parent['frozen_commit'],
                                       common=common, origin='proposal' if proposal_id else 'manual',
                                       proposal_id=proposal_id, note=note, integrated_from=integrated, actor=actor)
                self.repository._receipt(conn, scope, request_key, payload, dict(id=candidate_id))
        self.provision(candidate_id)
        return self.candidate(candidate_id)

    @contextmanager
    def _lock(self, candidate_id):
        directory = self.repository.root / 'candidate-locks'
        directory.mkdir(mode=0o700, exist_ok=True)
        if directory.is_symlink():
            raise ValueError('Candidate lock directory cannot be a symlink')
        name = hashlib.sha256(candidate_id.encode()).hexdigest()
        descriptor = os.open(directory / name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            os.close(descriptor)

    def _validate_created(self, workspace, intent):
        root = _validate_worktree(self.trees.project_root, workspace['path'])
        common = Path(_text_git(root, 'rev-parse', '--path-format=absolute', '--git-common-dir')).resolve()
        if (str(common) != intent['project_common_dir'] or _branch(root) != intent['branch']
                or _text_git(root, 'rev-parse', 'HEAD') != intent['base_revision'] or _status(root)):
            raise ValueError('Workspace does not match immutable creation intent')
        return root

    def provision(self, candidate_id):
        """Dispatch a recorded worktree intent once; every later call only reconciles."""
        with self._lock(candidate_id):
            with self.repository.connect() as conn:
                candidate = self._candidate(conn, candidate_id)
                intent = conn.execute('SELECT * FROM candidate_worktrees WHERE candidate_id=?',
                                      (candidate_id,)).fetchone()
                task_id = self._iteration(conn, candidate['iteration_id'])['task_id']
            if intent is None or intent['status'] == 'ready':
                return self.candidate(candidate_id)
            intent = dict(intent)
            try:
                expected = json.loads(intent['workspace_json']) if intent['workspace_json'] else None
                if intent['workspace_json'] is None:
                    with self.repository.connect(write=True) as conn:
                        # Mark dispatch before the external call: a crash after
                        # this point leaves unknown, never a second creation.
                        claimed = conn.execute("UPDATE candidate_worktrees SET workspace_json='null',updated_at=? "
                                               "WHERE candidate_id=? AND workspace_json IS NULL",
                                               (now(), candidate_id)).rowcount
                    if claimed:
                        expected = _workspace(self.creator.create(self.trees.project_root, intent['branch'],
                                                                  intent['base_revision']))
                        with self.repository.connect(write=True) as conn:
                            conn.execute('UPDATE candidate_worktrees SET workspace_json=?,updated_at=? '
                                         'WHERE candidate_id=?', (json.dumps(expected), now(), candidate_id))
                self._reconcile(task_id, candidate_id, intent, expected)
            except Exception:
                # Raw errors may contain credentials, paths or daemon addresses.
                # Intent is already committed and every retry is read-only.
                pass
        return self.candidate(candidate_id)

    def _reconcile(self, task_id, candidate_id, intent, expected=None):
        matches = []
        for row in self.creator.list():
            workspace = _workspace(row)
            if expected is not None and workspace != expected:
                continue
            try:
                root = self._validate_created(workspace, intent)
            except (ValueError, OSError):
                continue
            matches.append((workspace, root))
        if len(matches) != 1:
            # When no workspace matches and the dispatch marker (workspace_json
            # set to 'null') was written but the creator call never returned a
            # workspace (process crashed or was killed after the UPDATE), reset
            # the marker so the next provision() can retry.  The no-second-
            # dispatch guarantee (workspace_json IS NULL guard) still holds:
            # the only paths that reach here with workspace_json='null' AND
            # no matching workspace are genuinely failed dispatches.
            if len(matches) == 0 and intent['workspace_json'] == 'null':
                with self.repository.connect(write=True) as conn:
                    conn.execute("UPDATE candidate_worktrees SET workspace_json=NULL,updated_at=? "
                                 "WHERE candidate_id=? AND workspace_json='null'",
                                 (now(), candidate_id))
                    self.repository._event(conn, task_id, 'candidate.worktree.dispatch_reset', candidate_id,
                                           dict(reason='no_matching_workspace_found_after_dispatch_marker'))
            raise ValueError('No unique verified registered workspace')
        workspace, root = matches[0]
        binding = self.trees.snapshot_binding(task_id, root, paseo_workspace_id=workspace['workspace_id'])
        self._validate_created(workspace, intent)
        if (binding['base_revision'] != intent['base_revision'] or binding['branch'] != intent['branch']
                or binding['start_snapshot']['dirty']):
            raise ValueError('Starting snapshot differs from creation intent')
        binding['candidate_id'] = candidate_id
        with self.repository.connect(write=True) as conn:
            updated = conn.execute("UPDATE candidate_worktrees SET status='ready',binding_json=?,updated_at=? "
                                   "WHERE candidate_id=? AND status='unknown'",
                                   (json.dumps(binding, ensure_ascii=False), now(), candidate_id)).rowcount
            if updated:
                conn.execute("UPDATE candidates SET status='ready',updated_at=? WHERE id=? AND status='provisioning'",
                             (now(), candidate_id))
                self.repository._event(conn, task_id, 'candidate.worktree.ready', candidate_id,
                                       dict(paseo_workspace_id=workspace['workspace_id'], branch=intent['branch'],
                                            base_revision=intent['base_revision']))

    # ------------------------------------------------------------ freezing
    def _capture_clean(self, root, commit):
        capture = capture_sn_source(root, self.trees.store)
        tree = _text_git(root, 'rev-parse', commit + '^{tree}')
        if (capture.head_revision != commit or capture.head_tree != tree or capture.modified_paths
                or capture.added_paths or capture.deleted_paths or _status(root)
                or _text_git(root, 'rev-parse', 'HEAD') != commit):
            raise ValueError('Captured source does not equal the frozen commit')
        return capture.artifact_ref

    def _record_freeze(self, candidate, task_id, commit, source_ref, actor, *, committed):
        with self.repository.connect(write=True) as conn:
            updated = conn.execute("UPDATE candidates SET status='frozen',frozen_commit=?,source_ref_json=?,"
                                   "updated_at=? WHERE id=? AND status='ready'",
                                   (commit, json.dumps(source_ref), now(), candidate['id'])).rowcount
            if not updated:
                raise Conflict('Candidate changed while freezing')
            self.repository._event(conn, task_id, 'candidate.frozen', candidate['id'],
                                   dict(frozen_commit=commit, source_ref=source_ref, committed=committed, actor=actor))

    def _freeze_root(self, candidate_id):
        with self._lock(candidate_id):
            with self.repository.connect() as conn:
                candidate = self._candidate(conn, candidate_id)
                task_id = self._iteration(conn, candidate['iteration_id'])['task_id']
                binding = candidate_binding(conn, candidate_id)
            if candidate['status'] != 'ready' or candidate['origin'] != 'root':
                return
            root = _validate_worktree(self.trees.project_root, binding['worktree_path'])
            commit = binding['base_revision']
            self._record_freeze(candidate, task_id, commit, self._capture_clean(root, commit),
                                'coordinator', committed=False)

    def _message(self, candidate, iteration):
        return (f'sn-eval candidate {candidate["id"]}\n\niteration: {iteration["id"]}\n'
                f'parent: {candidate["parent_candidate_id"]}\nproposal: {candidate["proposal_id"]}\n')

    def freeze(self, candidate_id, *, actor):
        """Commit the candidate worktree as the coordinator and capture those bytes.

        The worktree must still be at its parent commit (Agents do not commit).
        A crash after the commit is resumed only when HEAD is exactly this
        coordinator commit on the parent. Private paths are never committed.
        """
        actor = _text(actor, 'actor', limit=200)
        with self._lock(candidate_id):
            with self.repository.connect() as conn:
                candidate = self._candidate(conn, candidate_id)
                iteration = self._iteration(conn, candidate['iteration_id'])
                binding = candidate_binding(conn, candidate_id)
            if candidate['status'] == 'frozen':
                raise Conflict('Candidate is already frozen; derive a child for further changes')
            if candidate['status'] != 'ready' or binding is None:
                raise Conflict('Candidate worktree is not ready')
            root = _validate_worktree(self.trees.project_root, binding['worktree_path'])
            if _branch(root) != binding['branch']:
                raise Conflict('Candidate worktree branch changed')
            base, head = binding['base_revision'], _text_git(root, 'rev-parse', 'HEAD')
            message = self._message(candidate, iteration)
            if head != base:
                parents = _text_git(root, 'rev-list', '--parents', '-n', '1', head).split()
                author = _text_git(root, 'log', '-1', '--format=%an <%ae>', head)
                body = _text_git(root, 'log', '-1', '--format=%B', head)
                if (parents[1:] != [base] or author != f'{COORDINATOR[0]} <{COORDINATOR[1]}>'
                        or body.strip() != message.strip() or _status(root)):
                    raise Conflict('Candidate worktree HEAD moved; only the coordinator commits')
                commit = head
            else:
                dirty = _status(root)
                if not dirty:
                    raise Conflict('Candidate has no changes to freeze')
                private = sorted({entry[key] for entry in dirty for key in ('path', 'original_path')
                                  if key in entry and _private(entry[key])})
                if private:
                    raise ValueError(
                        'Private paths cannot be frozen: ' + ', '.join(private) + '. '
                        'Other non-standard sensitive files must be listed in .gitignore '
                        'before calling freeze().')
                identity = ('-c', 'user.name=' + COORDINATOR[0], '-c', 'user.email=' + COORDINATOR[1],
                            '-c', 'commit.gpgsign=false', '-c', 'core.hooksPath=/dev/null')
                _text_git(root, 'add', '--all', '--', '.')
                _text_git(root, *identity, 'commit', '--no-verify', '--quiet', '-m', message)
                commit = _text_git(root, 'rev-parse', 'HEAD')
                if _text_git(root, 'rev-list', '--parents', '-n', '1', commit).split()[1:] != [base]:
                    raise ValueError('Frozen commit is not a direct child of the parent commit')
            self._record_freeze(candidate, iteration['task_id'], commit, self._capture_clean(root, commit),
                                actor, committed=True)
        return self.candidate(candidate_id)

    # ------------------------------------------------------------ running
    def association(self, candidate_id, attempt):
        """The identity the runner verifies against the source it actually loads."""
        with self.repository.connect() as conn:
            candidate = self._candidate(conn, candidate_id)
            iteration = self._iteration(conn, candidate['iteration_id'])
            binding = candidate_binding(conn, candidate_id)
            row = conn.execute('SELECT * FROM candidate_attempts WHERE candidate_id=? AND attempt=?',
                               (candidate_id, attempt)).fetchone()
        if row is None or binding is None or candidate['status'] != 'frozen':
            raise ValueError('Candidate attempt is not runnable')
        return dict(task_id=iteration['task_id'], worktree_path=binding['worktree_path'],
                    paseo_workspace_id=binding['paseo_workspace_id'], iteration_id=iteration['id'],
                    candidate_id=candidate_id, attempt=attempt, frozen_commit=candidate['frozen_commit'],
                    preparation_id=row['preparation_id'])

    def _seed_runs(self, iteration):
        """{case_id: run dir} of the root's reference measurement, or None."""
        root = self.candidate(iteration['root_candidate_id'])
        reference = root.get('reference')
        if reference is None:
            return None
        execution = self._execution(iteration['task_id'], reference['execution_id'])
        if execution is None:
            return None
        jobs = json.loads((Path(execution['run_dir']) / 'batch-plan.json').read_text())
        return {cid: job['run_dir'] for job in jobs for cid in job['case_ids']}

    def _guard_seeded_change(self, iteration, candidate):
        """Asking again on prepared notebooks hides changes to preparation code."""
        patterns = (self.execution_config or {}).get('seed_guard_patterns') or []
        if not patterns or candidate['frozen_commit'] == iteration['start_commit']:
            return
        root = self.trees.project_root
        changed = _text_git(root, 'diff', '--name-only', iteration['start_commit'], candidate['frozen_commit'])
        hits = sorted(path for path in changed.splitlines() if any(p in path for p in patterns))
        if hits:
            raise Conflict('该候选改动了资料准备相关文件，复用已导入的 notebook 不会反映这些改动: '
                           + ', '.join(hits[:10]))

    def run(self, candidate_id, *, budget, actor, request_key, adopt=False, batch_id=None, _sealed_capability=None):
        """Prepare a Job on the iteration plan with the frozen capture, then dispatch.

        A new request key is a new attempt; failures and cancellations stay.
        ``adopt`` (root only) registers the server-configured finished runs as
        this attempt instead of generating; the runner checks they match the
        plan, the frozen source and the conditions. With ``seed_from_root``
        configured, other candidates ask again on the root's prepared notebooks.
        """
        actor = _text(actor, 'actor', limit=200)
        _text(request_key, 'request key', limit=200)
        from ..artifact_store import ArtifactStore
        with self.repository.connect() as conn:
            candidate = self._candidate(conn, candidate_id)
            iteration = self._iteration(conn, candidate['iteration_id'])
        if iteration['origin'] != 'native':
            raise Conflict('Legacy iterations are read-only')
        if adopt and candidate['origin'] != 'root':
            raise Conflict('Only the root candidate adopts finished runs')
        if candidate['status'] != 'frozen':
            raise Conflict('Freeze the candidate before running it')
        if None in (self.jobs, self.executions, self.build_spec) or not isinstance(self.preparation_config, dict):
            raise ValueError('Candidate execution is not configured')
        if batch_id is not None and batch_id.startswith('sealed:'):
            from .sealed import authorize
            authorize(self.repository, iteration['id'], candidate_id, _sealed_capability)
        task_id, key = iteration['task_id'], 'candidate:' + candidate_id + ':' + request_key
        # Job receipts include actor. Bind it before preparation so changing actor
        # cannot dispatch twice against one budget reservation.
        command = dict(actor=actor, budget=budget, adopt=adopt, batch_id=batch_id)
        with self.repository.connect(write=True) as conn:
            command_scope = 'candidate.run.command:' + candidate_id
            prior_command = self.repository._prior(conn, command_scope, request_key, command)
            if prior_command is None:
                self.repository._receipt(conn, command_scope, request_key, command, dict(bound=True))
        plan_scope = iteration
        if batch_id is not None and batch_id.startswith(('cluster:', 'sealed:')):
            from .execution_scopes import get
            plan_scope = get(self.repository, iteration['id'], batch_id)
            if adopt:
                raise ValueError('Cluster runs cannot adopt full-plan executions')
        elif batch_id is not None:
            from .splits import batches
            claimed = next((b for b in batches(self.repository, iteration['id']) if b['batch_id'] == batch_id), None)
            if adopt or claimed is None:
                raise ValueError('Fresh runs require a consumed batch')
            owner = self.candidate(claimed['candidate_id'])
            if not claimed['completed_at'] and candidate_id not in (owner['id'], owner['parent_candidate_id']):
                raise Conflict('A fresh batch belongs to its claimed candidate and parent until completion')
            plan_scope = next(b for b in iteration['protocol']['fresh_batches'] if b['batch_id'] == batch_id)
        if not str(batch_id or '').startswith('sealed:'):
            from .splits import Policy
            policy = Policy(self.repository, task_id)
            for cid in self.plans.get(task_id, plan_scope['plan_id'])['content']['selection']['case_ids']:
                policy.refuse_sealed(cid)
        capability_args = dict(_sealed_capability=_sealed_capability) if str(batch_id or '').startswith('sealed:') else {}
        job = self.jobs.prepare(task_id, plan_scope['plan_id'], plan_scope['plan_revision'],
                                plan_scope['plan_content_hash'], actor=actor, request_key=key, **capability_args)
        protocol = iteration['protocol']
        store = ArtifactStore(self.preparation_config['artifact_root'])
        if store.root != self.trees.store.root:
            raise ValueError('Preparation artifact store does not match the candidate source store')
        prepared = self.jobs.capture_inputs_source(
            task_id, job['job_id'], job['state_version'], artifact_store=store,
            bundle_ref=protocol['bundle_ref'], index_ref=protocol['index_ref'],
            sn_ref=candidate['source_ref'], actor=actor, request_key=key)['preparation']
        if (prepared['source']['provenance']['head_revision'] != candidate['frozen_commit']
                or prepared['source']['artifact_ref'] != candidate['source_ref']):
            raise ValueError('Prepared source differs from the frozen candidate')
        with self.repository.connect(write=True) as conn:
            row = conn.execute('SELECT attempt FROM candidate_attempts WHERE job_id=?', (job['job_id'],)).fetchone()
            if row is None:
                attempt = conn.execute('SELECT COALESCE(MAX(attempt),0)+1 FROM candidate_attempts WHERE candidate_id=?',
                                       (candidate_id,)).fetchone()[0]
                conn.execute('INSERT INTO candidate_attempts(candidate_id,attempt,task_id,job_id,preparation_id,execution_id,created_by,created_at,batch_id) VALUES(?,?,?,?,?,?,?,?,?)',
                             (candidate_id, attempt, task_id, job['job_id'], prepared['preparation_id'],
                              None, actor, now(), batch_id))
                self.repository._event(conn, task_id, 'candidate.attempt.prepared', candidate_id,
                                       dict(attempt=attempt, job_id=job['job_id'],
                                            preparation_id=prepared['preparation_id'], actor=actor))
            else:
                attempt = row['attempt']
        config = dict(self.execution_config or {}, task_worktree=self.association(candidate_id, attempt))
        config.pop('adoptable_runs', None)
        if adopt:
            path = (self.execution_config or {}).get('adoptable_runs')
            if type(path) is not str or not path:
                raise ValueError('No adoptable runs are configured on this server')
            config['adopt_runs'] = json.loads(Path(path).read_text(encoding='utf-8'))
        elif (batch_id is not None and batch_id.startswith('cluster:')) or (
                batch_id is None and (self.execution_config or {}).get('seed_from_root') is True):
            seeds = self._seed_runs(iteration)
            if seeds is None and batch_id is not None:
                raise ValueError('Cluster runs require prepared root notebooks')
            if seeds is not None:
                self._guard_seeded_change(iteration, candidate)
                ids = plan_scope.get('case_ids')
                config['seed_runs'] = {cid: seeds[cid] for cid in ids} if ids else seeds
        if protocol.get('budget'):
            # Retries must be new ledgered attempts, not hidden runner rounds.
            config['retry_rounds'] = 0
        from .budgets import BudgetLedger
        ledger = BudgetLedger(self.repository)
        if protocol.get('budget'):
            count = len(self.plans.get(task_id, plan_scope['plan_id'])['content']['selection']['case_ids'])
            reserved = ledger.reserve_many(iteration, [dict(candidate_id=candidate_id, request_key=request_key,
                case_count=count, batch_id=batch_id)], group_key='attempt:' + hashlib.sha256(key.encode()).hexdigest())
            if reserved['status'] != 'reserved':
                raise Conflict('Iteration budget exceeded; request human confirmation')
        prior = self.executions.run_receipt(task_id, job['job_id'], job['state_version'], prepared['preparation_id'],
                                            budget, actor=actor, request_key=key)
        if prior is None:
            spec = self.build_spec(self.jobs, task_id, job['job_id'], prepared['preparation_id'], config,
                                   request_key=key, budget=budget)
            spec['budget'] = budget
            prior = self.executions.run(task_id, job['job_id'], job['state_version'], execution_spec=spec,
                                        actor=actor, request_key=key, **capability_args)
        if protocol.get('budget'):
            ledger.attach(iteration['id'], candidate_id, request_key, prior['execution_id'])
            ledger.settle_execution(prior)
        with self.repository.connect(write=True) as conn:
            conn.execute('UPDATE candidate_attempts SET execution_id=? WHERE candidate_id=? AND attempt=? '
                         'AND execution_id IS NULL', (prior['execution_id'], candidate_id, attempt))
        return self.candidate(candidate_id)

    def cluster_run(self, candidate_id, cluster_id, *, budget, actor, request_key):
        """Dispatch parent and child on an independent frozen cluster selection."""
        if self.iteration(self.candidate(candidate_id)['iteration_id'])['protocol'].get('budget'):
            return self.dispatch_run(candidate_id, 'node-cluster-run', budget=budget, actor=actor,
                                     request_key=request_key, cluster_id=cluster_id)
        from .execution_scopes import cluster_plan
        scope = cluster_plan(self, candidate_id, cluster_id)
        candidate = self.candidate(candidate_id)
        if candidate['status'] != 'frozen':
            raise Conflict('Freeze the candidate before running its cluster')
        k = self.iteration(candidate['iteration_id'])['protocol']['k_base']
        for cid in (candidate['parent_candidate_id'], candidate_id):
            existing = [a for a in self.candidate(cid)['attempts'] if a.get('batch_id') == scope['scope_id']]
            eligible = sum((a.get('admission') or {}).get('status') == 'admitted'
                           or a.get('execution_state') == 'running' for a in existing)
            for i in range(max(0, k - eligible)):
                self.run(cid, budget=budget, actor=actor, request_key=f'{request_key}:{cid}:{i}',
                         batch_id=scope['scope_id'])
        return dict(candidate_id=candidate_id, cluster_id=cluster_id, status='dispatched')

    def fresh_run(self, candidate_id, *, budget, actor, request_key):
        """Claim one fresh batch and dispatch bounded parent/candidate repetitions."""
        if self.iteration(self.candidate(candidate_id)['iteration_id'])['protocol'].get('budget'):
            return self.dispatch_run(candidate_id, 'node-fresh-run', budget=budget, actor=actor, request_key=request_key)
        from .splits import claim
        candidate = self.candidate(candidate_id)
        if not candidate['parent_candidate_id'] or candidate['status'] != 'frozen':
            raise Conflict('fresh-run requires a frozen child candidate')
        iteration = self.iteration(candidate['iteration_id'])
        batch = claim(self.repository, iteration, candidate_id, request_key)
        k = iteration['protocol']['k_base']
        for cid in (candidate['parent_candidate_id'], candidate_id):
            existing = [a for a in self.candidate(cid)['attempts'] if a.get('batch_id') == batch['batch_id']]
            for i in range(k):
                if i < len(existing) and existing[i]['execution_id']:
                    continue
                self.run(cid, budget=budget, actor=actor, request_key=f'{request_key}:{cid}:{i}', batch_id=batch['batch_id'])
        return dict(candidate_id=candidate_id, batch=batch, status='dispatched')

    def dispatch_run(self, candidate_id, verb, *, budget, actor, request_key, cluster_id=None):
        """Use the same atomic footprint for direct trusted calls and Agent actions."""
        from .action_requests import execute
        params = {k: v for k, v in budget.items() if k in ('timeout_seconds', 'limit_cny')}
        if cluster_id is not None:
            params['cluster_id'] = cluster_id
        identity = hashlib.sha256(json.dumps([candidate_id, verb, request_key]).encode()).hexdigest()
        candidate = self.candidate(candidate_id)
        tree = self.iteration(candidate['iteration_id'])
        return execute(self, dict(id=identity, verb=verb, target_id=candidate_id, params=params),
                       workspace_id=self.repository.task(tree['task_id'])['workspace_id'], actor=actor)

    def fill_validation(self, candidate_id, *, budget, actor, request_key):
        """Explicit execution path for validation measurements missing at comparison time."""
        from .splits import batches
        candidate = self.candidate(candidate_id)
        iteration = self.iteration(candidate['iteration_id'])
        for b in batches(self.repository, iteration['id'], completed=True):
            for cid in filter(None, (candidate['parent_candidate_id'], candidate_id)):
                existing = [a for a in self.candidate(cid)['attempts'] if a.get('batch_id') == b['batch_id']]
                eligible = sum((a.get('admission') or {}).get('status') == 'admitted'
                               or a.get('execution_state') == 'running' for a in existing)
                # Failed records remain evidence, but do not satisfy k_base.
                # Request-scoped keys reuse receipts if this explicit call is retried.
                for i in range(max(0, iteration['protocol']['k_base'] - eligible)):
                    self.run(cid, budget=budget, actor=actor,
                             request_key=f'{request_key}:validation:{b["batch_id"]}:{cid}:{i}', batch_id=b['batch_id'])

    # ------------------------------------------------------------ proposals, decisions
    def draft_proposal(self, parent_candidate_id, card, *, author):
        """Proposals hang under an evaluated candidate whose runs are citable."""
        from .hypotheses import HypothesisStore
        with self.repository.connect() as conn:
            parent = self._candidate(conn, parent_candidate_id)
            task_id = self._iteration(conn, parent['iteration_id'])['task_id']
            if not self._evaluated(conn, parent_candidate_id):
                raise Conflict('Proposals are drafted only under an evaluated candidate')
        return HypothesisStore(self.repository).draft(task_id, card, author=author,
                                                      parent_candidate_id=parent_candidate_id)

    def decide(self, candidate_id, decision, *, actor, note=''):
        """A research judgement; it never changes objective scores."""
        actor = _text(actor, 'actor', limit=200)
        if decision not in DECISIONS:
            raise ValueError('Decision is ' + '|'.join(DECISIONS))
        with self.repository.connect(write=True) as conn:
            candidate = self._candidate(conn, candidate_id)
            task_id = self._iteration(conn, candidate['iteration_id'])['task_id']
            previous = candidate.get('decision')
            if not self._evaluated(conn, candidate_id) and not (previous or {}).get('gate_verdict'):
                raise Conflict('Only an evaluated candidate can be adopted or rejected')
            if (previous or {}).get('gate_verdict') and not note.strip():
                raise ValueError('Overriding a gate verdict requires a note')
            body = dict(decision=decision, note=_text(note, 'note', empty=True, limit=4000), actor=actor, at=now())
            if (previous or {}).get('gate_verdict'):
                body.update(gate_verdict=previous['gate_verdict'], previous=previous)
            conn.execute('UPDATE candidates SET decision_json=?,updated_at=? WHERE id=?',
                         (json.dumps(body, ensure_ascii=False), now(), candidate_id))
            self.repository._event(conn, task_id, 'candidate.decided', candidate_id, body)
        return self.candidate(candidate_id)

    # ------------------------------------------------------------ legacy mapping
    def legacy_plan(self, task_id):
        """Map one legacy Task. Only artifact-evidenced fields are filled."""
        with self.repository.connect() as conn:
            task = self.repository._require(conn, 'tasks', task_id)
            existing = conn.execute('SELECT id FROM iterations WHERE task_id=?', (task_id,)).fetchone()
            prepared = [json.loads(r['content']) for r in conn.execute(
                "SELECT content FROM history WHERE task_id=? AND kind='job.prepared' ORDER BY seq", (task_id,))]
            plans = {(j['plan_id'], j['plan_revision'], j['plan_content_hash']) for j in prepared}
            unknown = ['start_commit', 'root_candidate']
            plan = None
            if len(plans) == 1:
                plan_id, revision, content_hash = next(iter(plans))
                snapshot = prepared[0]['plan_snapshot']
                plan = dict(plan_id=plan_id, plan_revision=revision, plan_content_hash=content_hash,
                            selection_hash=snapshot['content']['selection']['selection_hash'],
                            protocol=dict(mode=snapshot['content']['execution_intent']['mode'],
                                          request_revision=snapshot['content']['execution_intent']['request_revision'],
                                          input=snapshot['content']['input']))
            else:
                unknown.append('plan' if not plans else 'plan:multiple')
            captures = self._task_captures(task_id)
            started = {r['object_id']: json.loads(r['content']) for r in conn.execute(
                "SELECT object_id,content FROM history WHERE task_id=? AND kind='job.execution_started'", (task_id,))}
            preparations = {}
            for r in conn.execute("SELECT content FROM history WHERE task_id=? AND kind='job.inputs_source_captured'",
                                  (task_id,)):
                content = json.loads(r['content'])['preparation']
                preparations[content['preparation_id']] = content
            candidates = []
            rows = conn.execute("SELECT hypothesis_id,binding_json FROM hypothesis_worktrees WHERE task_id=? "
                                "AND status='ready' ORDER BY created_at,hypothesis_id", (task_id,)).fetchall() \
                if _has_table(conn, 'hypothesis_worktrees') else []
            notes = {}
            for row in rows:
                binding = json.loads(row['binding_json'])
                # A directory hosts several source states over time. A capture
                # equal to its clean start is the parent state, not this
                # candidate; only changed captures from it are the candidate's.
                start = ((binding.get('start_snapshot') or {}).get('source_ref') or {}).get('id')
                runs = []
                for execution_id, record in started.items():
                    prep = preparations.get(record['preparation_id'])
                    if prep is None:
                        continue
                    source_id = prep['source']['artifact_ref']['id']
                    if (binding['worktree_path'], source_id) not in captures:
                        continue
                    if source_id == start:
                        notes.setdefault(execution_id, []).append(dict(
                            kind='captured_from_clean_start_of', proposal_id=row['hypothesis_id'],
                            source_ref=source_id))
                    else:
                        runs.append(dict(execution_id=execution_id, basis=dict(
                            kind='saved_task_capture_after_start', source_ref=source_id,
                            start_source_ref=start, worktree_path=binding['worktree_path'])))
                candidates.append(dict(proposal_id=row['hypothesis_id'],
                                       observed_creation_base=binding['base_revision'],
                                       worktree_path=binding['worktree_path'], executions=runs))
            assigned = {run['execution_id'] for c in candidates for run in c['executions']}
        return dict(task_id=task_id, title=task['title'], existing_iteration=existing['id'] if existing else None,
                    plan=plan, unknown=unknown, candidates=candidates,
                    unassigned_executions=[e for e in started if e not in assigned],
                    unassigned_evidence={e: n for e, n in notes.items() if e not in assigned})

    def _task_captures(self, task_id):
        from ..identity import fingerprint
        directory = self.repository.root / 'task-captures'
        found = set()
        if not directory.is_dir() or directory.is_symlink():
            return found
        for path in directory.glob('*.json'):
            try:
                if path.is_symlink():
                    continue
                association = json.loads(path.read_text())
                if (type(association) is dict and association.get('task_id') == task_id
                        and path.stem == fingerprint(association)):
                    found.add((association['worktree_path'], association['source_ref']))
            except (OSError, ValueError, TypeError, KeyError):
                continue
        return found

    def migrate_legacy(self, *, actor, dry_run=True):
        """Report (dry run) or record legacy iterations for Tasks without one.

        Legacy candidates never receive a parent, start or frozen commit here;
        the observed creation base is kept as evidence, not as a parent edge.
        """
        actor = _text(actor, 'actor', limit=200)
        with self.repository.connect() as conn:
            task_ids = [r['id'] for r in conn.execute(
                'SELECT id FROM tasks WHERE id NOT IN (SELECT task_id FROM iterations) ORDER BY created_at,id')]
        report = [self.legacy_plan(task_id) for task_id in task_ids]
        if dry_run:
            return dict(dry_run=True, tasks=report)
        for item in report:
            with self.repository.connect(write=True) as conn:
                if conn.execute('SELECT 1 FROM iterations WHERE task_id=?', (item['task_id'],)).fetchone():
                    continue
                iteration_id, plan = uid(), item['plan'] or {}
                conn.execute('INSERT INTO iterations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                             (iteration_id, item['task_id'], 'legacy', item['title'], None, plan.get('plan_id'),
                              plan.get('plan_revision'), plan.get('plan_content_hash'), plan.get('selection_hash'),
                              json.dumps(plan['protocol']) if plan else None, None, None,
                              json.dumps(item['unknown']), actor, now()))
                for found in item['candidates']:
                    candidate_id, created = uid(), now()
                    evidence = dict(observed_creation_base=found['observed_creation_base'],
                                    worktree_path=found['worktree_path'], basis='hypothesis_worktrees ready row')
                    conn.execute('INSERT INTO candidates VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                                 (candidate_id, iteration_id, None, None, found['proposal_id'], 'legacy', 'ready',
                                  None, None, '[]', '[]', None, json.dumps(evidence, ensure_ascii=False),
                                  json.dumps(['parent', 'parent_commit', 'frozen_commit']), actor, created, created))
                    for run in found['executions']:
                        conn.execute('INSERT INTO candidate_links VALUES(?,?,?,?,?,?)',
                                     (candidate_id, run['execution_id'], item['task_id'],
                                      json.dumps(run['basis']), actor, created))
                self.repository._event(conn, item['task_id'], 'iteration.legacy_mapped', iteration_id,
                                       dict(unknown=item['unknown'], candidates=len(item['candidates']), actor=actor))
        return dict(dry_run=False, tasks=report)

    def record_legacy_candidate(self, iteration_id, *, label, basis, actor, execution_ids=(),
                                parent_candidate_id=None):
        """A person adds a legacy node; every link carries the stated basis."""
        actor = _text(actor, 'actor', limit=200)
        basis = _text(basis, 'basis', limit=4000)
        label = _text(label, 'label', limit=200)
        with self.repository.connect(write=True) as conn:
            iteration = self._iteration(conn, iteration_id)
            if iteration['origin'] != 'legacy':
                raise Conflict('Manual legacy nodes belong to legacy iterations only')
            if parent_candidate_id is not None and self._candidate(conn, parent_candidate_id)['iteration_id'] != iteration_id:
                raise ValueError('Parent belongs to another iteration')
            started = {r['object_id'] for r in conn.execute(
                "SELECT object_id FROM history WHERE task_id=? AND kind='job.execution_started'", (iteration['task_id'],))}
            if not set(execution_ids) <= started:
                raise ValueError('Executions must belong to this iteration Task')
            candidate_id, created = uid(), now()
            conn.execute('INSERT INTO candidates VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                         (candidate_id, iteration_id, parent_candidate_id, None, None, 'legacy-manual', 'ready',
                          None, None, '[]', '[]', None, json.dumps(dict(label=label, basis=basis), ensure_ascii=False),
                          json.dumps(['parent_commit', 'frozen_commit'] + ([] if parent_candidate_id else ['parent'])),
                          actor, created, created))
            for execution_id in execution_ids:
                conn.execute('INSERT INTO candidate_links VALUES(?,?,?,?,?,?)',
                             (candidate_id, execution_id, iteration['task_id'],
                              json.dumps(dict(kind='manual', basis=basis), ensure_ascii=False), actor, created))
            self.repository._event(conn, iteration['task_id'], 'candidate.legacy_recorded', candidate_id,
                                   dict(label=label, basis=basis, parent_candidate_id=parent_candidate_id,
                                        execution_ids=list(execution_ids), actor=actor))
        return self.candidate(candidate_id)

    def assign_legacy_parent(self, candidate_id, parent_candidate_id, *, basis, actor):
        actor = _text(actor, 'actor', limit=200)
        basis = _text(basis, 'basis', limit=4000)
        with self.repository.connect(write=True) as conn:
            candidate = self._candidate(conn, candidate_id)
            parent = self._candidate(conn, parent_candidate_id)
            iteration = self._iteration(conn, candidate['iteration_id'])
            if (iteration['origin'] != 'legacy' or candidate['parent_candidate_id'] is not None
                    or parent['iteration_id'] != candidate['iteration_id'] or candidate_id == parent_candidate_id):
                raise Conflict('Only an unknown legacy parent can be assigned within its iteration')
            unknown = [field for field in candidate['unknown'] if field != 'parent']
            note = json.loads(candidate['note']) if candidate['note'] and candidate['note'].startswith('{') else {}
            note['parent_basis'] = basis
            conn.execute('UPDATE candidates SET parent_candidate_id=?,unknown_json=?,note=?,updated_at=? WHERE id=?',
                         (parent_candidate_id, json.dumps(unknown), json.dumps(note, ensure_ascii=False), now(),
                          candidate_id))
            self.repository._event(conn, iteration['task_id'], 'candidate.legacy_parent', candidate_id,
                                   dict(parent_candidate_id=parent_candidate_id, basis=basis, actor=actor))
        return self.candidate(candidate_id)


def candidate_execution_ids(repository, candidate_id):
    """Execution ids owned by a candidate: native attempts, then recorded links."""
    with repository.connect() as conn:
        if not _has_table(conn, 'candidate_attempts'):
            return []
        rows = conn.execute('SELECT execution_id FROM candidate_attempts WHERE candidate_id=? AND execution_id IS NOT NULL '
                            'ORDER BY attempt', (candidate_id,)).fetchall()
        links = conn.execute('SELECT execution_id FROM candidate_links WHERE candidate_id=? ORDER BY created_at',
                             (candidate_id,)).fetchall()
    return [r['execution_id'] for r in rows] + [r['execution_id'] for r in links]
