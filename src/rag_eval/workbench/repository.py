"""Transactional user records and append-only task history.

    SQLite is authoritative for this slice's tasks, notes and review revisions.
    Imported evaluation facts stay in verified, immutable snapshot files.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import uuid

from ..identity import fingerprint
from .ingest import read_case, read_result


class Conflict(ValueError):
    pass


class UnknownIdentity(ValueError):
    """An explicitly queried authoritative row is absent."""


def now():
    return datetime.now(timezone.utc).isoformat()


def uid():
    return uuid.uuid4().hex


def _text(value, field, *, empty=False, limit=10000):
    if not isinstance(value, str) or (not empty and not value.strip()) or len(value) > limit:
        raise ValueError('Invalid ' + field)
    return value.strip()


class WorkbenchRepository:
    def __init__(self, state_root):
        self.root = Path(state_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.database = self.root / 'workbench.sqlite'
        with self.connect() as conn:
            version = conn.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, 1, 2, 3, 4):
                raise ValueError('Unsupported workbench database version')
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS workspaces(id TEXT PRIMARY KEY, title TEXT, location TEXT UNIQUE, created_at TEXT);
                CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, workspace_id TEXT REFERENCES workspaces(id), title TEXT, goal TEXT, created_at TEXT);
                CREATE TABLE IF NOT EXISTS task_results(task_id TEXT REFERENCES tasks(id), result_id TEXT, summary TEXT, attached_at TEXT, PRIMARY KEY(task_id,result_id));
                CREATE TABLE IF NOT EXISTS history(seq INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT REFERENCES tasks(id), kind TEXT, object_id TEXT, content TEXT, registered_at TEXT);
                CREATE TABLE IF NOT EXISTS reviews(id TEXT PRIMARY KEY, task_id TEXT REFERENCES tasks(id), result_id TEXT, case_id TEXT, reviewer TEXT, parent_id TEXT, content TEXT);
                CREATE TABLE IF NOT EXISTS requests(scope TEXT, request_key TEXT, digest TEXT, receipt TEXT, PRIMARY KEY(scope,request_key));
                CREATE TABLE IF NOT EXISTS task_sessions(id TEXT PRIMARY KEY, task_id TEXT REFERENCES tasks(id), agent_id TEXT, linked_by TEXT, linked_at TEXT, UNIQUE(task_id,agent_id));
                CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), workspace_id TEXT NOT NULL REFERENCES workspaces(id), record TEXT NOT NULL, probe TEXT);
            ''')
            # J02 observations do not rewrite the closed v1 Job or its receipts.
            if 'probe' not in {row['name'] for row in conn.execute('PRAGMA table_info(jobs)')}:
                conn.execute('ALTER TABLE jobs ADD COLUMN probe TEXT')
            # Agent role and creation cwd. NULL keeps the legacy rule for sessions
            # linked before roles existed: bound Task worktree, else workspace.
            columns = {row['name'] for row in conn.execute('PRAGMA table_info(task_sessions)')}
            for column in ('role', 'cwd', 'hypothesis_id'):
                if column not in columns:
                    conn.execute(f'ALTER TABLE task_sessions ADD COLUMN {column} TEXT')
            conn.execute('PRAGMA user_version=4')
        self.database.chmod(0o600)

    @contextmanager
    def connect(self, *, write=False):
        conn = sqlite3.connect(self.database, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        try:
            if write:
                conn.execute('BEGIN IMMEDIATE')
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _require(conn, table, object_id):
        # Table names are internal constants, never request input.
        row = conn.execute(f'SELECT * FROM {table} WHERE id=?', (object_id,)).fetchone()
        if row is None:
            raise UnknownIdentity('Unknown ' + table + ' identity')
        return dict(row)

    @staticmethod
    def _prior(conn, scope, request_key, payload):
        _text(request_key, 'request key', limit=200)
        row = conn.execute('SELECT * FROM requests WHERE scope=? AND request_key=?', (scope, request_key)).fetchone()
        if row:
            if row['digest'] != fingerprint(payload):
                raise Conflict('Request key was already used with different content')
            return json.loads(row['receipt'])
        return None

    @staticmethod
    def _receipt(conn, scope, request_key, payload, result):
        conn.execute('INSERT INTO requests VALUES(?,?,?,?)',
                     (scope, request_key, fingerprint(payload), json.dumps(result)))

    @staticmethod
    def _event(conn, task_id, kind, object_id, content):
        conn.execute('INSERT INTO history(task_id,kind,object_id,content,registered_at) VALUES(?,?,?,?,?)',
                     (task_id, kind, object_id, json.dumps(content, ensure_ascii=False), now()))

    def register_workspace(self, title, location):
        title = _text(title, 'workspace title', limit=200)
        location = str(Path(location).resolve())
        with self.connect(write=True) as conn:
            existing = conn.execute('SELECT * FROM workspaces WHERE location=?', (location,)).fetchone()
            if existing:
                return dict(existing)
            result = dict(id=uid(), title=title, location=location, created_at=now())
            conn.execute('INSERT INTO workspaces VALUES(:id,:title,:location,:created_at)', result)
            return result

    def workspaces(self):
        with self.connect() as conn:
            workspaces = [dict(row) for row in conn.execute('SELECT * FROM workspaces ORDER BY created_at,id')]
            for workspace in workspaces:
                workspace['tasks'] = [dict(row) for row in conn.execute(
                    'SELECT * FROM tasks WHERE workspace_id=? ORDER BY created_at,id', (workspace['id'],))]
            return workspaces

    def create_task(self, workspace_id, title, goal, *, request_key):
        title = _text(title, 'task title', limit=200)
        goal = _text(goal, 'goal', empty=True)
        payload = dict(workspace_id=workspace_id, title=title, goal=goal)
        scope = 'task.create:' + workspace_id
        with self.connect(write=True) as conn:
            self._require(conn, 'workspaces', workspace_id)
            prior = self._prior(conn, scope, request_key, payload)
            if prior:
                return prior
            result = dict(id=uid(), **payload, created_at=now())
            conn.execute('INSERT INTO tasks VALUES(:id,:workspace_id,:title,:goal,:created_at)', result)
            self._event(conn, result['id'], 'task.created', result['id'], result)
            self._receipt(conn, scope, request_key, payload, result)
            return result

    def attach_result(self, task_id, result_id):
        summary = read_result(self.root, result_id, include_cases=False)
        with self.connect(write=True) as conn:
            self._require(conn, 'tasks', task_id)
            existing = conn.execute('SELECT 1 FROM task_results WHERE task_id=? AND result_id=?', (task_id, result_id)).fetchone()
            if not existing:
                conn.execute('INSERT INTO task_results VALUES(?,?,?,?)', (task_id, result_id, json.dumps(summary), now()))
                self._event(conn, task_id, 'result.attached', result_id, summary)
        return summary

    def task(self, task_id):
        with self.connect() as conn:
            task = self._require(conn, 'tasks', task_id)
            # cwd stays on the server; the browser sees the role only.
            task['sessions'] = [{k: v for k, v in dict(row).items() if v is not None or k not in ('role', 'hypothesis_id')}
                                for row in conn.execute(
                'SELECT id,task_id,agent_id,linked_by,linked_at,role,hypothesis_id FROM task_sessions WHERE task_id=? ORDER BY rowid',
                (task_id,))]
            task['results'] = [dict(json.loads(row['summary']), attached_at=row['attached_at']) for row in conn.execute(
                'SELECT * FROM task_results WHERE task_id=? ORDER BY attached_at,result_id', (task_id,))]
            task['history'] = [dict(seq=row['seq'], kind=row['kind'], object_id=row['object_id'],
                                    content=json.loads(row['content']), registered_at=row['registered_at'])
                               for row in conn.execute('SELECT * FROM history WHERE task_id=? ORDER BY seq', (task_id,))]
            return task

    def attach_session(self, task_id, agent_id, *, actor, request_key):
        """Save an opaque Paseo session reference, without asserting runtime state."""
        task_id = _text(task_id, 'task identity', limit=200)
        agent_id = _text(agent_id, 'agent identity', limit=200)
        actor = _text(actor, 'actor', limit=200)
        payload = dict(task_id=task_id, agent_id=agent_id, actor=actor)
        scope = 'session.attach:' + task_id + ':' + actor
        with self.connect(write=True) as conn:
            self._require(conn, 'tasks', task_id)
            prior = self._prior(conn, scope, request_key, payload)
            if prior:
                return prior
            existing = conn.execute('SELECT * FROM task_sessions WHERE task_id=? AND agent_id=?',
                                    (task_id, agent_id)).fetchone()
            if existing:
                result = dict(existing)
            else:
                result = dict(id=uid(), task_id=task_id, agent_id=agent_id, linked_by=actor, linked_at=now())
                conn.execute('INSERT INTO task_sessions(id,task_id,agent_id,linked_by,linked_at) '
                             'VALUES(:id,:task_id,:agent_id,:linked_by,:linked_at)', result)
                self._event(conn, task_id, 'session.attached', result['id'], result)
            result = {k: result[k] for k in ('id', 'task_id', 'agent_id', 'linked_by', 'linked_at')}
            self._receipt(conn, scope, request_key, payload, result)
            return result

    @staticmethod
    def session_cwd(conn, session, task):
        """The cwd an Agent session was created in; legacy sessions use the Task scope."""
        if session['cwd'] if 'cwd' in session.keys() else None:
            return session['cwd']
        bound = (conn.execute('SELECT worktree_path FROM task_worktrees WHERE task_id=?', (task['id'],)).fetchone()
                 if conn.execute("SELECT 1 FROM sqlite_master WHERE name='task_worktrees'").fetchone() else None)
        if bound:
            return bound['worktree_path']
        return conn.execute('SELECT location FROM workspaces WHERE id=?', (task['workspace_id'],)).fetchone()['location']

    def _attached(self, conn, task_id, result_id):
        self._require(conn, 'tasks', task_id)
        if not conn.execute('SELECT 1 FROM task_results WHERE task_id=? AND result_id=?', (task_id, result_id)).fetchone():
            raise ValueError('Result is not attached to this task')

    def save_review(self, *, task_id, result_id, case_id, reviewer, judgment, reason,
                    evidence, base_revision, request_key):
        reviewer = _text(reviewer, 'reviewer', limit=200)
        reason = _text(reason, 'review reason')
        if judgment not in {'confirmed', 'rejected', 'undecided'}:
            raise ValueError('Unknown judgment')
        case = read_case(self.root, result_id, case_id)
        if (not isinstance(evidence, list) or any(not isinstance(e, str) for e in evidence)
                or len(set(evidence)) != len(evidence) or not set(evidence) <= set(case['evidence_ids'])):
            raise ValueError('Unknown evidence reference')
        payload = dict(task_id=task_id, result_id=result_id, case_id=case_id, reviewer=reviewer,
                       judgment=judgment, reason=reason, evidence=evidence, base_revision=base_revision,
                       rubric='case-observation-v1', target=case['source_refs'])
        scope = 'review.save:' + task_id + ':' + reviewer
        with self.connect(write=True) as conn:
            self._attached(conn, task_id, result_id)
            prior = self._prior(conn, scope, request_key, payload)
            if prior:
                return prior
            head = conn.execute('''SELECT id FROM reviews WHERE task_id=? AND result_id=? AND case_id=? AND reviewer=?
                                   ORDER BY rowid DESC LIMIT 1''', (task_id, result_id, case_id, reviewer)).fetchone()
            if base_revision != (head['id'] if head else None):
                raise Conflict('Review revision changed; your input is preserved, reload the current revision')
            result = dict(id=uid(), parent_id=base_revision, **payload, saved_at=now())
            conn.execute('INSERT INTO reviews VALUES(?,?,?,?,?,?,?)',
                         (result['id'], task_id, result_id, case_id, reviewer, base_revision, json.dumps(result)))
            self._event(conn, task_id, 'review.saved', result['id'], result)
            self._receipt(conn, scope, request_key, payload, result)
            return result

    def reviews(self, task_id, result_id, case_id):
        with self.connect() as conn:
            self._attached(conn, task_id, result_id)
            return [json.loads(row['content']) for row in conn.execute(
                'SELECT content FROM reviews WHERE task_id=? AND result_id=? AND case_id=? ORDER BY rowid',
                (task_id, result_id, case_id))]

    def save_note(self, task_id, text, *, actor, request_key, context=None):
        text = _text(text, 'note')
        if context:
            if not isinstance(context, dict) or set(context) != {'result_id', 'case_id'}:
                raise ValueError('Invalid note context')
            read_case(self.root, context['result_id'], context['case_id'])
        payload = dict(text=text, actor=actor, context=context)
        scope = 'note.save:' + task_id + ':' + actor
        with self.connect(write=True) as conn:
            self._require(conn, 'tasks', task_id)
            if context:
                self._attached(conn, task_id, context['result_id'])
            prior = self._prior(conn, scope, request_key, payload)
            if prior:
                return prior
            result = dict(id=uid(), **payload, saved_at=now())
            self._event(conn, task_id, 'note.saved', result['id'], result)
            self._receipt(conn, scope, request_key, payload, result)
            return result
