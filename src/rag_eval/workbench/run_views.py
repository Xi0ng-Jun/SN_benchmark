"""Read-only run, case, flip and worktree views for the workbench.

Every local path comes from a trusted, server-side registration (config ID ->
run_dir / scores / worktree). Requests select only registered IDs; no request
field is ever used as a host path. Nothing here writes a run directory, the
ledger, workbench state or a git repository: run facts are read from saved
artifacts, stage diagnostics are recomputed in memory, and git is invoked with
read-only plumbing under GIT_OPTIONAL_LOCKS=0.

Gold appears only in the analysis projections (case detail, highlights). It is
read from the frozen bundle that the run consumed; SN generation never sees
these responses. Missing facts stay null/unknown with an explicit reason.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import subprocess
from typing import Any
from ..execution_diagnostics import sanitize_diagnostic

FORMAT = 'sn-workbench-run-views-v1'
TERMINAL = {'finished', 'finished_with_errors', 'failed', 'interrupted', 'cancelled', 'not_applicable'}
# Official per-case metric used to list cases needing a read. Unlisted suites
# fall back to the first per-case metric the scores file declares.
PRIMARY_METRIC = {'qasper': 'answer_f1', 'hotpotqa': 'answer_f1', 'multihop_rag': 'accuracy',
                  'qmsum': 'rougeL', 'alce': 'str_em'}
# Above this many partitions an execution is summarized instead of listed run by run.
COLLAPSE_PARTITIONS = 20
NEEDS_READ_THRESHOLDS = {suite: (0.5 if suite == 'qasper' else 1.0) for suite in PRIMARY_METRIC}
_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$')
_SHA = re.compile(r'^[0-9a-f]{40}$')


class NotRegistered(LookupError):
    """The requested ID is not a trusted registration."""


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding='utf-8'))


def _rows(path: Path) -> list[dict] | None:
    if not path.is_file():
        return None
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def _signature(*paths: Path) -> tuple:
    result = []
    for path in paths:
        try:
            info = path.stat()
            result.append((str(path), info.st_mtime_ns, info.st_size))
        except FileNotFoundError:
            result.append((str(path), None, None))
    return tuple(result)


def _fold(text: str) -> tuple[str, list[int]]:
    """Whitespace-folded text and a map from folded index to original index."""
    folded, index, space = [], [], False
    for i, char in enumerate(text):
        if char.isspace():
            if folded and not space:
                folded.append(' '); index.append(i)
            space = True
        else:
            folded.append(char); index.append(i); space = False
    return ''.join(folded), index


class _Locator:
    def __init__(self, text: str):
        self.text = text
        self.folded, self.index = _fold(text)

    def find(self, needle: str) -> tuple[int, int] | None:
        target = ' '.join(needle.split())
        if not target:
            return None
        at = self.folded.find(target)
        if at < 0:
            return None
        return self.index[at], self.index[at + len(target) - 1] + 1

    def spans(self, needle: str, *, minimum: int = 24) -> list[tuple[int, int]]:
        """Exact folded match; else exact matches of each line (heading-prefixed chunks)."""
        whole = self.find(needle)
        if whole:
            return [whole]
        found = []
        for line in needle.split('\n'):
            if len(line.strip()) >= minimum and (span := self.find(line)):
                found.append(span)
        return found


@dataclass(frozen=True)
class RunEntry:
    id: str
    run_dir: Path
    scores: Path | None = None
    title: str | None = None
    task_id: str | None = None
    case_ids: tuple = ()
    execution_state: str | None = None
    suite: str | None = None
    mode: str | None = None
    failure: dict | None = None


@dataclass(frozen=True)
class WorktreeEntry:
    id: str
    path: Path
    branch: str
    base: str
    task_id: str | None = None
    paseo_workspace_id: str | None = None
    # Optional binding-time snapshot from M2-C: {"head": sha, "dirty": {path: blob_sha_or_null}}.
    start_snapshot: dict | None = None


@dataclass
class BatchTemplate:
    """Values for the copyable verification command; never executed here."""
    values: dict[str, str] = field(default_factory=dict)


def _registered_id(value: Any, kind: str) -> str:
    if not isinstance(value, str) or not _ID.match(value):
        raise ValueError(f'Invalid {kind} ID')
    return value


class RunViews:
    def __init__(self, runs: list[RunEntry], worktrees: list[WorktreeEntry] = (), *,
                 ledger: Path | None = None, batch: BatchTemplate | None = None, worktree_service=None,
                 needs_read_thresholds=None, hypotheses=(), execution_enabled=False,
                 execution_config=None, execution_records=None):
        self._needs_read_thresholds = dict(NEEDS_READ_THRESHOLDS)
        if needs_read_thresholds is not None:
            if not isinstance(needs_read_thresholds, dict):
                raise ValueError('Attention thresholds must be a suite mapping')
            for suite, value in needs_read_thresholds.items():
                if (suite not in PRIMARY_METRIC or type(value) not in (float, int)
                        or not math.isfinite(value) or not 0 <= value <= 1):
                    raise ValueError('Invalid attention threshold')
                self._needs_read_thresholds[suite] = value
        self._hypotheses = []
        for card in hypotheses:
            if not isinstance(card, dict) or set(card) != {'id', 'task_id', 'title', 'path', 'status'}:
                raise ValueError('Explicit Task hypothesis registration required')
            if any(not isinstance(value, str) or not value.strip() for value in card.values()):
                raise ValueError('Invalid hypothesis registration')
            _registered_id(card['id'], 'hypothesis')
            self._hypotheses.append(dict(card))
        self._runs = {}
        for entry in runs:
            _registered_id(entry.id, 'run')
            if entry.id in self._runs:
                raise ValueError('Duplicate run ID')
            self._runs[entry.id] = RunEntry(entry.id, Path(entry.run_dir).resolve(),
                Path(entry.scores).resolve() if entry.scores else None, entry.title, entry.task_id,
                entry.case_ids, entry.execution_state, entry.suite, entry.mode, entry.failure)
        self._worktrees = {}
        for entry in worktrees:
            _registered_id(entry.id, 'worktree')
            if entry.id in self._worktrees or not _SHA.match(entry.base):
                raise ValueError('Duplicate worktree ID or invalid base revision')
            self._worktrees[entry.id] = WorktreeEntry(entry.id, Path(entry.path).resolve(), entry.branch, entry.base,
                                                      entry.task_id, entry.paseo_workspace_id, entry.start_snapshot)
        self._ledger = Path(ledger).resolve() if ledger else None
        self._batch = batch or BatchTemplate()
        self._worktree_service = worktree_service  # e.g. TaskWorktreeService; binding()/read() only
        self._execution_enabled = execution_enabled
        self._execution_config = execution_config if isinstance(execution_config, dict) else None
        self._cache: dict[str, tuple[tuple, dict]] = {}
        self._execution_records = execution_records
        # Store objects are content-addressed and immutable: once verified, the
        # same bundle serves every partition view instead of being re-hashed
        # and reloaded per run (hundreds of partitions for one execution).
        from ..run_reader import RunReadContext
        self._read_context = RunReadContext()
        self._partition_context = RunReadContext(partition_only=True)

    def _discover_executions(self):
        if self._execution_records is None:
            return
        records = self._execution_records()
        # Hundreds of partitions are looked up one by one; rescan only when a record or plan changed.
        def stamp(record):
            plan = Path(record['run_dir']) / 'batch-plan.json'
            try:
                stat = plan.stat()
            except OSError:
                return (record.get('execution_id'), record.get('state'), None)
            return (record.get('execution_id'), record.get('state'), stat.st_mtime_ns, stat.st_size)
        signature = tuple(stamp(record) for record in records)
        if signature == getattr(self, '_discovered', None):
            return
        self._discovered = signature
        for record in records:
            root = Path(record['run_dir'])
            plan = root / 'batch-plan.json'
            if root.is_symlink() or plan.is_symlink():
                continue
            try:
                jobs = _json(plan)
                run_state = record.get('run_state') or {}
                failures = [*run_state.get('generation_failures', []),
                            *run_state.get('scoring_failures', [])]
                adopted = None
                if (root / 'adopted-runs.json').is_file() and not (root / 'adopted-runs.json').is_symlink():
                    adopted = set(_json(root / 'adopted-runs.json'))
                for ordinal, job in enumerate(jobs, 1):
                    path = Path(job['run_dir'])
                    if job.get('adopted') is True:
                        # A finished run registered by this execution's own adoption step.
                        if adopted is None or str(path) not in adopted or path.is_symlink() or not path.is_absolute():
                            continue
                    else:
                        relative = path.relative_to(root)
                        if not relative.parts or any((root.joinpath(*relative.parts[:i])).is_symlink()
                                                      for i in range(1, len(relative.parts) + 1)):
                            continue
                        if '..' in relative.parts or not path.resolve().is_relative_to(root.resolve()):
                            continue
                    identity = _registered_id(record['execution_id'] + '-' + str(ordinal), 'run')
                    name = job.get('official_name', path.name)
                    if type(name) is not str or not _ID.match(name):
                        continue
                    scores = root / 'official' / name / 'scores' / 'scores.json'
                    self._runs[identity] = RunEntry(identity, path.resolve(), scores,
                        task_id=record['task_id'], case_ids=tuple(job['case_ids']),
                        execution_state=record.get('state') if record.get('terminal') else None,
                        suite=job.get('suite'), mode=job.get('mode'),
                        failure=next((row for row in failures if row.get('run_dir') == str(path)), None))
            except (OSError, ValueError, KeyError, TypeError):
                continue

    @classmethod
    def from_config(cls, section: dict | None, base: Path, *, worktree_service=None,
                    execution_enabled=False, execution_config=None, execution_records=None) -> 'RunViews':
        """Build from a config section; relative paths resolve against ``base``."""
        section = section or {}
        resolve = lambda value: (base / value).resolve() if value else None
        runs = [RunEntry(r['id'], resolve(r['run_dir']), resolve(r.get('scores')), r.get('title'), r.get('task_id'))
                for r in section.get('runs', [])]
        worktrees = [WorktreeEntry(w['id'], resolve(w['path']), w['branch'], w['base'], w.get('task_id'),
                                   w.get('paseo_workspace_id'), w.get('start_snapshot'))
                     for w in section.get('worktrees', [])]
        return cls(runs, worktrees, ledger=resolve(section.get('ledger')),
                   batch=BatchTemplate({k: str(v) for k, v in (section.get('batch') or {}).items()}),
                   worktree_service=worktree_service,
                   needs_read_thresholds=section.get('needs_read_thresholds'), hypotheses=section.get('hypotheses', []),
                   execution_enabled=execution_enabled, execution_config=execution_config,
                   execution_records=execution_records)

    # ------------------------------------------------------------------ runs
    def _entry(self, run_id: str) -> RunEntry:
        self._discover_executions()
        try:
            return self._runs[_registered_id(run_id, 'run')]
        except KeyError:
            raise NotRegistered('Run is not registered') from None

    def _ledger_rows(self) -> list[dict]:
        if self._ledger is None or not self._ledger.is_file():
            return []
        from ..run_ledger import query_runs
        return query_runs(self._ledger)

    def _load(self, run_id: str) -> dict:
        entry = self._entry(run_id)
        run = entry.run_dir
        files = [run / n for n in ('state.json', 'manifest.json', 'planned.jsonl', 'outputs.jsonl',
                                   'scores.jsonl', 'model-events.jsonl', 'runtime-identity.json', 'product-bundle.json')]
        if entry.scores:
            files.append(entry.scores.parent / 'submission.json')
        signature = _signature(*files, *( [entry.scores] if entry.scores else []))
        signature += (entry.execution_state, entry.case_ids, entry.suite, entry.mode)
        signature += (json.dumps(entry.failure, sort_keys=True),)
        cached = self._cache.get(run_id)
        if cached and cached[0] == signature:
            return cached[1]
        view = self._build(entry)
        self._cache[run_id] = (signature, view)
        return view

    def _build(self, entry: RunEntry) -> dict:
        run = entry.run_dir
        state = _json(run / 'state.json') if (run / 'state.json').is_file() else {'phase': 'unknown'}
        phase = state.get('phase', 'unknown')
        runner_phase = phase
        if phase not in TERMINAL and entry.execution_state:
            phase = {'timeout': 'interrupted', 'finished': 'interrupted'}.get(
                entry.execution_state, entry.execution_state)
        manifest = _json(run / 'manifest.json') if (run / 'manifest.json').is_file() else None
        planned = _rows(run / 'planned.jsonl') or []
        if not planned and entry.case_ids:
            planned = [dict(case_id=cid) for cid in entry.case_ids]
        outputs = {row['case_id']: row for row in _rows(run / 'outputs.jsonl') or []}
        runtime_scores = _rows(run / 'scores.jsonl') or []
        scope = list(dict.fromkeys(p['case_id'] for p in planned))
        warnings: list[str] = []
        bundle = stage = None
        if manifest is not None:
            try:
                from ..run_reader import RunReadContext
                from ..stage_metrics import compute_stage_metrics
                context = self._partition_context
                bundle = context.bundle_for_run(run)
                stage = compute_stage_metrics(run, context=context)
            except (OSError, ValueError, KeyError, TypeError) as error:
                warnings.append(f'stage diagnostics unavailable: {type(error).__name__}')
        official = self._official(entry, manifest, scope, warnings)
        by_case = {c['case_id']: c for c in (bundle or {}).get('cases', [])}
        stage_cases = {c['case_id']: c for c in (stage or {}).get('cases', [])}
        primary = official.get('primary_metric')
        threshold = self._needs_read_thresholds.get((manifest or {}).get('suite'), 1.0)
        missing_status = 'missing' if phase in TERMINAL else 'pending'
        cases = []
        for cid in scope:
            output = outputs.get(cid)
            record = (output or {}).get('product_record', {})
            per = (official.get('per_case') or {}).get(cid)
            row = dict(case_id=cid, status=output['status'] if output else missing_status,
                       question=record.get('original_question') or (by_case.get(cid) or {}).get('question'),
                       group_id=(per or {}).get('group_id'),
                       official=dict(state='scored' if per else 'not_in_official_scores' if official['status'] == 'available' else official['status'],
                                     metrics=(per or {}).get('metrics', {})),
                       stage={k: (stage_cases.get(cid) or {}).get(k, {}).get('value')
                              for k in ('gold_evidence_recall', 'context_precision', 'citation_precision')})
            value = row['official']['metrics'].get(primary) if primary else None
            row['needs_read'] = row['status'] != 'success' or (value is not None and value < threshold)
            cases.append(row)
        code = (manifest or {}).get('identity', {}).get('code', {})
        refs = _json(run / 'artifact-refs.json') if (run / 'artifact-refs.json').is_file() else {}
        ledger_row = next((r for r in self._ledger_rows() if Path(r.get('run_dir') or '').resolve() == run), None)
        summary = (stage or {}).get('summary') or {}
        usage = summary.get('model_usage', {})
        attention = [c['case_id'] for c in cases if c['needs_read']]
        return dict(
            format=FORMAT, id=entry.id, title=entry.title or run.name, run_dir=str(run), task_id=entry.task_id,
            suite=(manifest or {}).get('suite') or entry.suite, mode=(manifest or {}).get('mode') or entry.mode,
            partition_id=(manifest or {}).get('identity', {}).get('notebook_context', {}).get('partition_id'),
            state=dict(phase=phase, terminal=phase in TERMINAL, updated_at=state.get('updated_at'),
                       **(dict(runner_phase=runner_phase) if phase != runner_phase else {})),
            failure={key: sanitize_diagnostic(value) for key, value in
                     {**(entry.failure or {}), **state}.items() if key in
                     ('error_type', 'error_message', 'failed_phase', 'diagnostic_path') and value} or None,
            progress=dict(planned=len(scope), outputs=len(outputs), runtime_scores=len(runtime_scores),
                          status_counts=_count(c['status'] for c in cases)),
            official={k: v for k, v in official.items() if k != 'per_case'},
            stage=None if stage is None else dict(
                metric_role='diagnostic',
                gold_evidence_recall=_pick(summary.get('gold_evidence_recall')),
                context_precision=_pick(summary.get('context_precision')),
                citation_precision=_pick(summary.get('citation_precision')),
                refusal_rate=_pick(summary.get('refusal_rate')),
                calls=dict(scheduled=(usage.get('scheduled_call_count') or {}).get('sum_over_observed'),
                           provider_logged=(usage.get('provider_logged_call_count') or {}).get('sum_over_observed'),
                           total_tokens=(usage.get('total_tokens') or {}).get('sum_over_observed'),
                           ask_latency_seconds=(usage.get('ask_latency_seconds') or {}).get('sum_over_observed'),
                           history_exhaustive=False),
                scheduled=_scheduled(stage.get('model_events'))),
            attention=dict(rule=f'status != success or official {primary} < {threshold:g}' if primary else 'status != success',
                           count=len(attention), case_ids=attention),
            cases=cases,
            provenance=self._provenance(run, manifest, code, refs, official),
            ledger=dict(present=ledger_row is not None, row=ledger_row),
            warnings=warnings,
            _bundle=bundle, _outputs=outputs, _per_case=official.get('per_case') or {},
        )

    def _official(self, entry: RunEntry, manifest: dict | None, scope: list[str], warnings: list[str]) -> dict:
        if entry.scores is None:
            return dict(status='not_registered', reason='no official scores.json registered for this run')
        if not entry.scores.is_file():
            return dict(status='missing', reason='registered official scores.json is not readable')
        scores = _json(entry.scores)
        try:
            from ..flip_report import _read_side
            from ..run_reader import RunReadContext
            from ..benchmark_comparison import validate_scored_submission
            side, artifacts = _read_side(entry.run_dir, entry.scores, None)
            if artifacts is None or side.get('reason'):
                raise ValueError('Official answer sheet is not bound to this run')
            bundle = self._read_context.bundle_for_run(entry.run_dir)
            validate_scored_submission(bundle, artifacts['submission'], artifacts['scores'])
        except (OSError, ValueError, KeyError, TypeError):
            return dict(status='mismatch', reason='official artifacts failed run binding or scorer validation')
        if manifest is not None and scores.get('suite') != manifest.get('suite'):
            return dict(status='mismatch', reason='official scores suite differs from the run')
        ids = scores.get('case_ids') or []
        if not set(ids) <= set(scope):
            return dict(status='mismatch', reason='official scores include cases outside the run plan')
        per_case = {row['case_id']: row for row in scores.get('per_case', [])}
        metrics = scores.get('metrics') or {}
        primary = PRIMARY_METRIC.get(scores.get('suite'))
        if primary not in metrics:
            primary = next(iter(scores.get('metric_denominators') or metrics), None)
        if set(ids) != set(scope):
            warnings.append('official scores cover a subset of the run plan')
        return dict(status='available', suite=scores.get('suite'), scope=scores.get('scope'), metrics=metrics,
                    metric_denominators=scores.get('metric_denominators') or {},
                    pending_metrics=scores.get('pending_metrics') or [],
                    batch_only_metrics=scores.get('batch_only_metrics') or [],
                    coverage=scores.get('coverage') or {}, primary_metric=primary,
                    scores_id=scores.get('scores_id'), scorer_id=scores.get('scorer_id'),
                    bundle_id=scores.get('bundle_id'), submission_id=scores.get('submission_id'),
                    comparison_category=((scores.get('method') or {}).get('configuration') or {}).get('comparison_category'),
                    per_case=per_case)

    @staticmethod
    def _provenance(run: Path, manifest: dict | None, code: dict, refs: dict, official: dict) -> dict:
        identity = (manifest or {}).get('identity', {})
        runtime = _json(run / 'runtime-identity.json') if (run / 'runtime-identity.json').is_file() else {}
        loaded = ((runtime.get('loaded_model_services') or {}).get('observation') or {})
        return dict(
            # Iteration candidate attempt as the runner verified it; None for Task runs.
            candidate=identity.get('candidate'),
            source=dict(sn_source_ref=((refs.get('sources') or {}).get('sn') or {}).get('id'),
                        source_format=code.get('source_format'), head_revision=code.get('head_revision'),
                        head_tree=code.get('head_tree'), selected_roots=code.get('selected_roots'),
                        product_archive_sha256=code.get('product_archive_sha256'),
                        modified_paths=code.get('modified_paths'), added_paths=code.get('added_paths'),
                        deleted_paths=code.get('deleted_paths')),
            configuration=dict(service_config_sha256=identity.get('product_services'),
                               runtime_settings_sha256=identity.get('runtime_settings'),
                               comparable_settings_sha256=runtime.get('comparable_settings_sha256'),
                               protocol_id=(manifest or {}).get('protocol_id'),
                               pairing_id=(manifest or {}).get('pairing_id'),
                               request_revision=identity.get('notebook_context', {}).get('request_revision'),
                               bundle_id=official.get('bundle_id'), scorer_id=official.get('scorer_id')),
            # The runner disables several SN modules but does not project them as
            # identity yet (roadmap M2). Report that fact instead of guessing.
            features=(dict(status='projected', values=identity['feature_set']['feature_set'],
                           fingerprint=identity['feature_set']['fingerprint']) if identity.get('feature_set')
                      else dict(status='not_projected', values=None,
                                reason='effective feature set is not recorded in the run manifest')),
            model_services=dict(
                status=loaded.get('status', 'unavailable'), fingerprint=loaded.get('fingerprint'),
                strict_bindings=(loaded.get('effective_settings') or {}).get('model_bindings_strict'),
                services=[{k: s.get(k) for k in ('service_id', 'kind', 'protocol', 'model', 'generation')}
                          for s in loaded.get('services', [])],
                limits=loaded.get('limits') or {}),
            runtime_dependencies={k: (identity.get('runtime_dependencies') or {}).get(k)
                                  for k in ('format', 'observation_sha256', 'requirements_sha256', 'environment_sha256')},
        )

    @staticmethod
    def _public(view: dict) -> dict:
        return {k: v for k, v in view.items() if not k.startswith('_')}

    def list_runs(self) -> dict:
        self._discover_executions()
        runs = []
        for run_id in list(self._runs):
            try:
                view = self._public(self._load(run_id))
                view.pop('cases')
                runs.append(view)
            except (OSError, ValueError, KeyError, TypeError) as error:
                runs.append(dict(id=run_id, title=self._runs[run_id].title, status='unreadable',
                                 reason=type(error).__name__))
        registered = {str(e.run_dir) for e in self._runs.values()}
        unregistered = [dict(run_dir=r.get('run_dir'), suite=r.get('suite'), mode=r.get('mode'), state=r.get('state'),
                             official_score=r.get('official_score'))
                        for r in self._ledger_rows() if str(Path(r.get('run_dir') or '').resolve()) not in registered]
        return dict(format=FORMAT, runs=runs, ledger_only=unregistered)

    def run(self, run_id: str) -> dict:
        return self._public(self._load(run_id))

    def cases(self, run_id: str, *, status: str | None = None, needs_read: bool | None = None,
              against: str | None = None, delta: str | None = None) -> dict:
        view = self._load(run_id)
        rows = [dict(r) for r in view['cases']]
        metric = view['official'].get('primary_metric')
        if against is not None:
            paired = self.flips(against, run_id)
            deltas = {r['case_id']: r['delta'] for r in paired['rows']} if paired['admission'] == 'admitted' else {}
            for row in rows:
                row['delta'] = deltas.get(row['case_id'])
        if status:
            rows = [r for r in rows if r['status'] == status]
        if needs_read is not None:
            rows = [r for r in rows if r['needs_read'] is needs_read]
        if delta in {'worse', 'better', 'same'}:
            sign = {'worse': lambda d: d < 0, 'better': lambda d: d > 0, 'same': lambda d: d == 0}[delta]
            rows = [r for r in rows if r.get('delta') is not None and sign(r['delta'])]
        return dict(run_id=run_id, metric=metric, against=against, total=len(view['cases']), cases=rows)

    # ------------------------------------------------------------------ case
    def case(self, run_id: str, case_id: str) -> dict:
        view = self._load(run_id)
        row = next((r for r in view['cases'] if r['case_id'] == case_id), None)
        if row is None:
            raise NotRegistered('Case is not in this run plan')
        bundle = view['_bundle'] or {}
        case = next((c for c in bundle.get('cases', []) if c['case_id'] == case_id), None)
        output = view['_outputs'].get(case_id)
        record = (output or {}).get('product_record', {})
        documents = {d['id']: d for d in bundle.get('documents', [])}
        material = [documents[d] for d in (case or {}).get('material_document_ids', []) if d in documents]
        response = record.get('response') or {}
        anchors = response.get('anchors') or []
        cited_keys = {a.get('key') for a in anchors}
        anchor_docs = record.get('anchor_documents') or {}
        from ..stage_metrics import _blocks, _gold, _hits
        atoms = _gold(case, bundle.get('documents', [])) if case else None
        blocks = _blocks(record) if output else None
        papers = []
        for document in material:
            locator = _Locator(document['text'])
            marks: list[tuple[int, int, str, str]] = []
            unmatched = []
            for i, (_, text, _) in enumerate(atoms or []):
                if document['id'] and text:
                    found = locator.spans(text)
                    marks += [(a, b, 'gold', f'gold-{i + 1}') for a, b in found]
                    if not found:
                        unmatched.append(dict(kind='gold', label=f'gold-{i + 1}', excerpt=text[:240]))
            for rank, block in enumerate(blocks or [], 1):
                if block.get('document_id') not in (None, document['id']):
                    continue
                key = block.get('key')
                body = re.sub(r'^k\d+: ', '', block['text'])
                kind = 'cited' if key in cited_keys else 'retrieved'
                found = locator.spans(body)
                marks += [(a, b, kind, key or f'block-{rank}') for a, b in found]
                if not found:
                    unmatched.append(dict(kind=kind, label=key or f'block-{rank}', excerpt=body[:240]))
            papers.append(dict(document_id=document['id'], title=document.get('title'),
                               characters=len(document['text']), text_sha256=document.get('text_sha256'),
                               paragraphs=_paragraphs(document['text'], marks), unmatched=unmatched,
                               first_gold=min((a for a, _, k, _ in marks if k == 'gold'), default=None)))
        retrieved = []
        for rank, block in enumerate(blocks or [], 1):
            retrieved.append(dict(rank=rank, key=block.get('key'), document_id=block.get('document_id'),
                                  cited=block.get('key') in cited_keys,
                                  contains_gold=any(_hits(a, block) for a in atoms or []),
                                  excerpt=re.sub(r'^k\d+: ', '', block['text'])[:280]))
        citations = [dict(key=a.get('key'), object_id=a.get('object_id'), object_type=a.get('object_type'),
                          name=a.get('name'), document_id=anchor_docs.get(a.get('key')),
                          rank=next((r['rank'] for r in retrieved if r['key'] == a.get('key')), None),
                          contains_gold=next((r['contains_gold'] for r in retrieved if r['key'] == a.get('key')), None),
                          snippet=(a.get('snippet') or '')[:280]) for a in anchors]
        gold = (case or {}).get('gold') or {}
        usage = record.get('usage') or {}
        return dict(
            format=FORMAT, run_id=run_id, case_id=case_id, status=row['status'], needs_read=row['needs_read'],
            question=row['question'], answer=(output or {}).get('prediction'),
            answer_reason=(output or {}).get('reason'),
            official=dict(row['official'], primary_metric=view['official'].get('primary_metric')),
            stage=row['stage'],
            references=(case or {}).get('references'),
            gold=dict(annotations=[dict(answer=a.get('answer'), type=a.get('type'), evidence=a.get('evidence', []))
                                   for a in gold.get('annotations', [])]) if gold else None,
            gold_role='analysis-only; not part of any generation request',
            papers=papers, citations=citations, retrieved=retrieved,
            usage=dict(call_count=usage.get('call_count'), total_tokens=usage.get('total_tokens'),
                       latency_seconds=record.get('latency_seconds'), coverage=usage.get('coverage')),
            evidence_snapshot=(record.get('qasper_evidence') or {}).get('status'),
        )

    def task_runs_context(self, task_id: str) -> tuple[str, str, dict]:
        """This Task's runs as an Agent attachment: status, scores, cases to read, paths."""
        overview = self.task_overview(task_id)
        ids = overview['runs'] if overview['status'] != 'unbound' else [
            run_id for run_id, entry in self._runs.items() if entry.task_id == task_id]
        if not ids:
            self._discover_executions()
            ids = [run_id for run_id, entry in self._runs.items() if entry.task_id == task_id]
        runs, collapsed = [], set()
        groups = {}
        for run_id in ids:
            prefix, _, ordinal = run_id.rpartition('-')
            if prefix and ordinal.isdigit():
                groups.setdefault(prefix, []).append(run_id)
        for run_id in ids:
            prefix = run_id.rpartition('-')[0]
            if len(groups.get(prefix, ())) > COLLAPSE_PARTITIONS:
                # A whole-benchmark execution: one summary line, cases via execution-cases.
                if prefix not in collapsed:
                    collapsed.add(prefix)
                    runs.append(self._execution_summary(prefix, len(groups[prefix])))
                continue
            view = self.run(run_id)
            stage = view.get('stage') or {}
            runs.append(dict(
                id=run_id, title=view.get('title'), suite=view.get('suite'), mode=view.get('mode'),
                phase=view['state'].get('phase'), failure=view.get('failure'),
                progress=view.get('progress'),
                official={k: view['official'].get(k) for k in ('status', 'scope', 'metrics', 'metric_denominators', 'pending_metrics', 'reason')},
                stage={k: {f: (stage.get(k) or {}).get(f) for f in ('numerator', 'denominator', 'status')}
                       for k in ('gold_evidence_recall', 'context_precision', 'citation_precision', 'refusal_rate')},
                attention=view.get('attention'), sn_source_ref=view['provenance']['source'].get('sn_source_ref'),
                head_revision=view['provenance']['source'].get('head_revision'), run_dir=view.get('run_dir'),
                warnings=view.get('warnings')))
        content = dict(
            role='summary of this Task\'s evaluation runs; per-case evidence is attached separately as a run case, '
                 'or read outputs.jsonl / planned.jsonl under run_dir',
            task_id=task_id, worktree=overview.get('worktree'), runs=runs)
        return (f'本任务的评测运行 · {len(runs)} 个', json.dumps(content, ensure_ascii=False, indent=1), {'task_id': task_id})

    def task_run_ids(self, task_id: str) -> list[str]:
        """Every run of a Task, with summarized executions expanded to their partition runs."""
        from .agent_tools import task_run_ids
        return task_run_ids(self, task_id)

    def _execution_record(self, execution_id: str) -> dict | None:
        if self._execution_records is None:
            return None
        return next((r for r in self._execution_records() if r.get('execution_id') == execution_id), None)

    def _execution_summary(self, execution_id: str, partitions: int) -> dict:
        record = self._execution_record(execution_id) or {}
        summary = None
        path = Path(record['run_dir']) / 'official-summary.json' if record.get('run_dir') else None
        if path is not None and path.is_file() and not path.is_symlink():
            summary = _json(path)
        return dict(id=execution_id, kind='execution', partitions=partitions, phase=record.get('state'),
                    summary=summary, run_dir=record.get('run_dir'),
                    note=f'{partitions} 个分区（每个分区一个 run，ID 为 {execution_id}-<序号>）；'
                         f'逐题分数用 `execution-cases {execution_id}`，单题证据用 `case <run_id> <case_id>`')

    def execution_cases(self, execution_id: str) -> dict:
        """Per-case primary scores of one execution, lowest first, read from its official score files.

        This is an analysis listing; formal admission and comparison are separate.
        """
        runs = self.execution_runs(execution_id)
        rows, metric = [], None
        for entry in runs:
            scores = _json(entry.scores) if entry.scores is not None and entry.scores.is_file() else None
            if scores is None:
                rows.extend(dict(case_id=cid, run_id=entry.id, status='unscored', value=None) for cid in entry.case_ids)
                continue
            metric = metric or PRIMARY_METRIC.get(scores.get('suite'))
            for row in scores.get('per_case', []):
                rows.append(dict(case_id=row['case_id'], run_id=entry.id, status=row.get('status'),
                                 value=(row.get('metrics') or {}).get(metric), metrics=row.get('metrics')))
        rows.sort(key=lambda r: (r['value'] is not None, r['value'] if r['value'] is not None else 0, r['case_id']))
        values = [r['value'] for r in rows if r['value'] is not None]
        return dict(execution_id=execution_id, metric=metric, partitions=len(runs), cases=len(rows),
                    mean=sum(values) / len(values) if values else None, rows=rows)

    def case_context(self, run_id: str, case_id: str) -> tuple[str, str, dict]:
        """One run case as analysis material for an Agent message.

        Includes gold, which never reaches the SN generation process; paper text
        is not inlined, only its identity and artifact paths for the Agent to read.
        """
        detail = self.case(run_id, case_id)
        view = self.run(run_id)
        content = dict(
            role='analysis-only material: includes gold; never part of an SN generation request',
            run=dict(id=run_id, title=view.get('title'), suite=view.get('suite'), mode=view.get('mode'),
                     run_dir=view.get('run_dir'), sn_source_ref=view['provenance']['source'].get('sn_source_ref'),
                     head_revision=view['provenance']['source'].get('head_revision'),
                     attention_rule=view.get('attention', {}).get('rule')),
            case_id=case_id, status=detail['status'], needs_read=detail['needs_read'], question=detail['question'],
            sn_answer=detail['answer'], answer_reason=detail['answer_reason'], official=detail['official'],
            stage=detail['stage'], references=detail['references'], gold=detail['gold'],
            citations=detail['citations'],
            retrieved=[{k: block[k] for k in ('rank', 'key', 'cited', 'contains_gold', 'excerpt')} for block in detail['retrieved']],
            papers=[dict(document_id=paper['document_id'], title=paper['title'], characters=paper['characters'],
                         gold_not_found_in_text=[u['excerpt'] for u in paper['unmatched'] if u['kind'] == 'gold'])
                    for paper in detail['papers']],
            usage=detail['usage'], evidence_snapshot=detail['evidence_snapshot'],
            artifacts=dict(outputs=str(Path(view['run_dir']) / 'outputs.jsonl'), planned=str(Path(view['run_dir']) / 'planned.jsonl')),
        )
        text = json.dumps(content, ensure_ascii=False, indent=1)
        return (f'run 案例 · {case_id.split(":")[-1][:8]} · {view.get("title") or run_id}', text,
                {'run_id': run_id, 'case_id': case_id})

    # ----------------------------------------------------------------- flips
    def shared_bundle(self, run_id: str):
        """Verify and load a run's frozen bundle once, for many partitions of one comparison."""
        from ..notebook_bundle import load_bundle
        from ..run_reader import RunReadContext
        try:
            return load_bundle(RunReadContext().input_directory(self._entry(run_id).run_dir))
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def flips(self, left_id: str, right_id: str, *, bundle=None) -> dict:
        from ..flip_report import compare_runs_flips
        left, right = self._entry(left_id), self._entry(right_id)
        metric = (self._official_scores(left) or {}).get('primary_metric') if bundle is not None else \
            self._load(left_id)['official'].get('primary_metric')
        report = compare_runs_flips(left.run_dir, right.run_dir, before_scores=left.scores,
                                   after_scores=right.scores, bundle=bundle)
        rows, counts = [], dict(better=0, worse=0, same=0, unpaired=0)
        for row in report['cases']:
            value = row['metrics'].get(metric, {})
            direction = {'unchanged': 'same', 'unavailable': 'unpaired'}.get(value.get('direction'), value.get('direction', 'unpaired'))
            counts[direction] += 1
            rows.append(dict(case_id=row['case_id'], left=value.get('before'), right=value.get('after'),
                             delta=value.get('difference'), flip=direction))
        return dict(format=FORMAT, kind='paired' if report['admission']['status'] == 'admitted' else 'not_admitted',
                    admission=report['admission']['status'], same_run=left_id == right_id,
                    left_run=left_id, right_run=right_id, metric=metric, counts=counts, rows=rows,
                    note=report['admission']['reason'], admission_detail=report['admission'])

    def execution_runs(self, execution_id: str) -> list[RunEntry]:
        """Partition runs of one workbench execution, in batch-plan order."""
        self._discover_executions()
        prefix = execution_id + '-'
        found = [e for i, e in self._runs.items() if i.startswith(prefix) and i[len(prefix):].isdigit()]
        return sorted(found, key=lambda e: int(e.id[len(prefix):]))

    @staticmethod
    def _official_scores(entry: RunEntry) -> dict | None:
        """The registered scores' primary metric without building the whole run view."""
        if entry.scores is None or not entry.scores.is_file():
            return None
        scores = _json(entry.scores)
        metrics = scores.get('metrics') or {}
        primary = PRIMARY_METRIC.get(scores.get('suite'))
        if primary not in metrics:
            primary = next(iter(scores.get('metric_denominators') or metrics), None)
        return dict(primary_metric=primary)

    def admission(self, run_id: str, *, bundle=None) -> dict:
        """Formal admission of one run on its own: the flip report's self comparison."""
        from ..flip_report import compare_runs_flips
        entry = self._entry(run_id)
        report = compare_runs_flips(entry.run_dir, entry.run_dir, before_scores=entry.scores,
                                    after_scores=entry.scores, bundle=bundle)
        status = report['admission']['status']
        return dict(status=status, reason=None if status == 'admitted' else
                    report['before'].get('reason') or report['admission']['reason'])

    # -------------------------------------------------------------- worktree
    # The Changes page consumes TaskWorktreeService.read()'s shape. When the
    # coordinator injects that service, it is the only diff source. Until then
    # a config-registered worktree yields the same shape with unknown start
    # flags (no start snapshot is recorded here).
    def _worktree(self, worktree_id: str) -> WorktreeEntry:
        try:
            return self._worktrees[_registered_id(worktree_id, 'worktree')]
        except KeyError:
            raise NotRegistered('Worktree is not registered') from None

    def worktree_for_task(self, task_id: str) -> WorktreeEntry | None:
        return next((w for w in self._worktrees.values() if w.task_id == task_id), None)

    @staticmethod
    def _git(path: Path, *args: str, allowed=(0,)) -> bytes:
        env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        env.update(GIT_OPTIONAL_LOCKS='0', GIT_TERMINAL_PROMPT='0', LC_ALL='C')
        command = ['git', '--no-pager', '--literal-pathspecs', '-C', str(path), '-c', 'core.fsmonitor=false',
                   '-c', 'core.quotePath=true', '-c', 'diff.external=', *args]
        try:
            result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    env=env, timeout=15, check=False)
        except (OSError, subprocess.TimeoutExpired):
            raise ValueError('Worktree Git inspection unavailable') from None
        if result.returncode not in allowed:
            raise ValueError('Worktree Git inspection failed')
        return result.stdout

    def fallback_read(self, tree: WorktreeEntry, *, max_diff_bytes=131072, max_file_diff_bytes=32768,
                      max_files=200) -> dict:
        """Read-only base..working-tree diff in TaskWorktreeService.read() shape."""
        from .task_worktree import _private
        root = tree.path
        if not root.is_dir():
            raise ValueError('Registered worktree path is not readable')
        text = lambda *a: self._git(root, *a).decode('utf-8', 'replace').rstrip('\n')
        head = text('rev-parse', 'HEAD')
        branch = text('rev-parse', '--abbrev-ref', 'HEAD')
        options = ('diff', '--no-ext-diff', '--no-textconv', '--no-renames')
        changed = [p for p in self._git(root, *options, '--name-only', '-z', tree.base, '--').decode().split('\0') if p]
        untracked = [p for p in self._git(root, 'ls-files', '--others', '--exclude-standard', '-z').decode().split('\0') if p]
        candidates = sorted(set(changed) | set(untracked))
        files, budget = [], max_diff_bytes
        for path in candidates[:max_files]:
            private = _private(path)
            target = root / path
            kind = 'private' if private else 'file' if target.is_file() and not target.is_symlink() else \
                'missing' if not target.exists() and not target.is_symlink() else 'nonregular'
            entry = dict(path=path, preexisting_dirty=None, changed_since_start=None, private=private,
                         binary=False, kind=kind, diff=None, truncated=False, untracked=path in untracked)
            if not private and kind in {'file', 'missing'}:
                if path in changed:
                    raw = self._git(root, *options, '--patch', tree.base, '--', path)
                else:
                    raw = self._git(root, 'diff', '--no-ext-diff', '--no-textconv', '--no-index', '--',
                                    '/dev/null', str(target), allowed=(0, 1))
                if b'Binary files ' in raw:
                    entry['binary'] = True
                else:
                    cap = max(0, min(budget, max_file_diff_bytes))
                    entry['truncated'] = len(raw) > cap
                    entry['diff'] = raw[:cap].decode('utf-8', errors='ignore')
                    budget -= len(entry['diff'].encode())
            files.append(entry)
        return dict(binding=dict(task_id=tree.task_id, paseo_workspace_id=tree.paseo_workspace_id,
                                 worktree_path=str(root), branch=tree.branch, base_revision=tree.base,
                                 start_snapshot=tree.start_snapshot, created_at=None),
                    current_head=head, current_branch=None if branch == 'HEAD' else branch,
                    files=files, file_count=len(candidates), files_truncated=len(candidates) > max_files,
                    start_flags='unknown' if tree.start_snapshot is None else 'recorded')

    def _binding_read(self, task_id: str) -> tuple[dict, str] | None:
        service = self._worktree_service
        if service is not None and service.binding(task_id) is not None:
            return service.read(task_id), 'task_worktree'
        tree = self.worktree_for_task(task_id)
        if tree is None:
            return None
        return self.fallback_read(tree), 'run_views_fallback'

    def task_changes(self, task_id: str) -> dict:
        cards = [{k: v for k, v in card.items() if k != 'task_id'} for card in self._hypotheses if card['task_id'] == task_id]
        hypotheses = dict(status='available', items=cards, reason='Explicit Task hypothesis registrations') if cards else _HYPOTHESES
        found = self._binding_read(task_id)
        if found is None:
            return dict(format=FORMAT, task_id=task_id, status='unbound', read=None, runs=[],
                        hypotheses=hypotheses, verification=None)
        read, source = found
        binding = read['binding']
        runs = self._runs_for_binding(binding)
        return dict(format=FORMAT, task_id=task_id, status='available', source=source, read=read, runs=runs,
                    hypotheses=hypotheses, verification=self._verification(binding, runs))

    def _runs_for_binding(self, binding: dict) -> list[dict]:
        """Only explicit trusted registrations or saved Task provenance associate runs."""
        self._discover_executions()
        rows = []
        # A Task can select a newer hypothesis worktree without losing runs
        # captured from its earlier immutable bindings.
        paths = {binding.get('worktree_path')}
        repository = getattr(self._worktree_service, 'repository', None)
        if repository is not None:
            from .hypothesis_worktrees import historical_bindings
            with repository.connect() as conn:
                paths.update(b['worktree_path'] for b in historical_bindings(conn, binding.get('task_id')))
        for run_id, entry in list(self._runs.items()):
            try:
                view = self._load(run_id)
                manifest_path = entry.run_dir / 'manifest.json'
                manifest = (_json(manifest_path) or {}) if manifest_path.is_file() else {}
            except (OSError, ValueError, KeyError, TypeError):
                continue
            association = manifest.get('identity', {}).get('task_worktree') or {}
            source = view['provenance']['source']
            if entry.task_id == binding.get('task_id'):
                match = 'explicit_registration'
            elif (association.get('task_id') == binding.get('task_id')
                  and association.get('worktree_path') in paths
                  and association.get('source_ref') == source.get('sn_source_ref')):
                match = 'saved_task_capture'
            else:
                continue
            rows.append(dict(id=run_id, title=view['title'], suite=view['suite'], mode=view['mode'],
                             state=view['state'], official=view['official'].get('metrics'),
                             attention=view['attention']['count'], match=match,
                             sn_source_ref=source.get('sn_source_ref'), head_revision=source.get('head_revision')))
        return rows

    def _verification(self, binding: dict, runs: list[dict]) -> dict:
        placeholders = dict(bundle='$STORE_BUNDLE', case_id_file='$CASE_ID_FILE', output_root='$OUTPUT_ROOT',
                            project_root='$SN_PROJECT_ROOT', artifact_root='$ARTIFACT_ROOT',
                            artifact_index_id='$ARTIFACT_INDEX_ID', mode='chunk', model_config='$MODEL_CONFIG',
                            sn_source_ref='$SN_SOURCE_REF', dependency_requirements='$DEPENDENCY_REQUIREMENTS',
                            runtime_expectation='$RUNTIME_EXPECTATION', ledger='$EVAL_ROOT/var/sn-eval/runs.jsonl')
        placeholders.update({k: v for k, v in self._batch.values.items() if k in placeholders})
        quote = lambda v: v if v.startswith('$') else shlex.quote(v)
        argv = ['"$EVAL_PYTHON" scripts/run_notebook_batch.py run']
        for flag in ('bundle', 'case_id_file', 'output_root', 'project_root', 'artifact_root', 'artifact_index_id',
                     'mode', 'model_config', 'sn_source_ref', 'dependency_requirements', 'runtime_expectation', 'ledger'):
            argv.append('--' + flag.replace('_', '-') + ' ' + quote(placeholders[flag]))
        smoke = self._execution_config is not None and self._execution_config.get('mode') == 'smoke'
        commands = (dict(dry_run='本地 smoke executor（不加载 SN、不调用模型、不生成官方分数）',
                         run='本地 smoke executor（确认运行后写入独立 run-dir）') if smoke else
                    dict(dry_run=' \\\n  '.join(argv + ['--dry-run']), run=' \\\n  '.join(argv)))
        budget_note = ('当前配置是本地 smoke executor；不会加载 SN、调用模型或生成官方评分。'
                       '确认本次运行的隔离目录与超时。' if smoke else
                       '会调用已配置的模型服务；先核对题单、源码与超时，费用由操作者在运行前自行估算')
        return dict(stage='R1', executable=self._execution_enabled,
                    capture_note=('运行前对该 worktree 重新捕获源码（TaskWorktreeService.run_argv），'
                                  '得到 --sn-source-ref；--project-root 仍指原 project，只读其 .env'),
                    worktree_path=binding.get('worktree_path'),
                    commands=commands,
                    budget=dict(note=budget_note,
                                estimate='not_computed',
                                reference_runs=[dict(id=r['id'], suite=r['suite'], mode=r['mode']) for r in runs],
                                fields=['suite', 'partition 数', '题数', 'mode', '预计 embedding/chat/rerank 调用',
                                        '模型服务代际', '超时']))

    # --------------------------------------------------------------- sidebar
    def task_overview(self, task_id: str) -> dict:
        service = self._worktree_service
        binding = service.binding(task_id) if service is not None else None
        if binding is None and (tree := self.worktree_for_task(task_id)) is not None:
            binding = dict(task_id=task_id, worktree_path=str(tree.path), branch=tree.branch,
                           base_revision=tree.base, paseo_workspace_id=tree.paseo_workspace_id, start_snapshot=None)
        pending = self._pending_decisions(task_id)
        cards = self._pending_cards(task_id)
        if binding is None:
            return dict(task_id=task_id, status='unbound', pending=pending + cards, pending_hypotheses=cards, worktree=None, runs=[])
        runs = self._runs_for_binding(binding)
        views = [self._load(r['id']) for r in runs]
        status = ('running' if any(not v['state']['terminal'] for v in views)
                  else 'failed' if any(v['state']['phase'] in {'failed', 'interrupted'} for v in views)
                  else 'attention' if any(v['attention']['count'] for v in views)
                  else 'done' if views else 'idle')
        return dict(task_id=task_id, status=status, pending=pending + cards, pending_hypotheses=cards,
                    worktree={k: binding.get(k) for k in ('worktree_path', 'branch', 'base_revision', 'paseo_workspace_id')},
                    runs=[r['id'] for r in runs],
                    pending_rule='undecided saved candidates and latest human reviews')

    def _pending_cards(self, task_id):
        """Hypothesis cards waiting for a person: drafts to approve, checked cards to conclude."""
        repository = getattr(self._worktree_service, 'repository', None)
        if repository is None:
            return 0
        with repository.connect() as conn:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='hypotheses'").fetchone():
                return 0
            return conn.execute("SELECT count(*) FROM hypotheses WHERE task_id=? AND status IN ('draft','verified')",
                                (task_id,)).fetchone()[0]

    def _pending_decisions(self, task_id):
        repository = getattr(self._worktree_service, 'repository', None)
        if repository is None:
            return 0
        from .candidates import CandidateStore
        pending = sum(c['decision'] is None or c['decision']['judgment'] == 'undecided'
                      for c in CandidateStore(repository).list(task_id))
        latest = {}
        for event in repository.task(task_id)['history']:
            if event['kind'] == 'review.saved':
                r = event['content']
                latest[(r['result_id'], r['case_id'], r['reviewer'])] = r
        return pending + sum(r['judgment'] == 'undecided' for r in latest.values())

    def tasks_overview(self, task_ids: list[str]) -> dict:
        return dict(format=FORMAT, tasks={task_id: self.task_overview(task_id) for task_id in task_ids})

    # ---------------------------------------------------------------- router
    def router(self):
        from fastapi import APIRouter
        from fastapi.responses import JSONResponse
        router = APIRouter()

        def guard(call):
            try:
                return call()
            except NotRegistered as error:
                return JSONResponse({'error': str(error), 'code': 'RUN_VIEW_NOT_REGISTERED'}, status_code=404)
            except (OSError, ValueError) as error:
                return JSONResponse({'error': '只读工件不可用或格式无效', 'code': 'RUN_VIEW_UNREADABLE',
                                     'detail': type(error).__name__}, status_code=422)

        @router.get('/api/run-views/runs')
        def runs():
            return guard(self.list_runs)

        @router.get('/api/run-views/executions/{execution_id}/cases')
        def execution_cases(execution_id: str):
            return guard(lambda: self.execution_cases(_registered_id(execution_id, 'execution')))

        @router.get('/api/run-views/flips')
        def flips(left: str, right: str):
            return guard(lambda: self.flips(left, right))

        @router.get('/api/run-views/runs/{run_id}')
        def run(run_id: str):
            return guard(lambda: self.run(run_id))

        @router.get('/api/run-views/runs/{run_id}/cases')
        def cases(run_id: str, status: str | None = None, needs_read: bool | None = None,
                  against: str | None = None, delta: str | None = None):
            return guard(lambda: self.cases(run_id, status=status, needs_read=needs_read, against=against, delta=delta))

        @router.get('/api/run-views/runs/{run_id}/case')
        def case(run_id: str, case_id: str):
            return guard(lambda: self.case(run_id, case_id))

        @router.get('/api/run-views/tasks')
        def tasks(ids: str = ''):
            return guard(lambda: self.tasks_overview([i for i in ids.split(',') if i][:200]))

        @router.get('/api/run-views/tasks/{task_id}')
        def task(task_id: str):
            return guard(lambda: self.task_overview(task_id))

        @router.get('/api/run-views/tasks/{task_id}/changes')
        def changes(task_id: str):
            return guard(lambda: self.task_changes(task_id))

        return router


_HYPOTHESES = dict(status='not_available', items=[], reason='hypothesis cards are not stored yet (roadmap M1/M2)')


def _count(values) -> dict:
    result: dict[str, int] = {}
    for value in values:
        result[value] = result.get(value, 0) + 1
    return result


def _pick(metric: dict | None) -> dict | None:
    if not metric:
        return None
    return {k: metric.get(k) for k in ('value', 'numerator', 'denominator', 'status', 'reason')}


def _scheduled(events: dict | None) -> dict | None:
    """Run-level scheduled journal facts; unattributed calls stay separate."""
    if not isinstance(events, dict):
        return None
    return {k: events.get(k) for k in ('status', 'submission_count', 'terminal_count', 'unattributed_call_count',
                                       'service_kind_counts', 'journal_pair_state', 'history_exhaustive')}


def _paragraphs(text: str, marks: list[tuple[int, int, str, str]]) -> list[dict]:
    """Split on blank lines; each paragraph lists text segments with mark kinds."""
    bounds, start = [], 0
    for match in re.finditer(r'\n\s*\n', text):
        bounds.append((start, match.start())); start = match.end()
    bounds.append((start, len(text)))
    result = []
    for a, b in bounds:
        if a >= b:
            continue
        cuts = {a, b}
        inside = [m for m in marks if m[0] < b and m[1] > a]
        for m in inside:
            cuts.update((max(a, m[0]), min(b, m[1])))
        points = sorted(cuts)
        segments = []
        for x, y in zip(points, points[1:]):
            kinds = sorted({m[2] for m in inside if m[0] <= x and m[1] >= y})
            labels = sorted({m[3] for m in inside if m[0] <= x and m[1] >= y})
            if segments and segments[-1]['marks'] == kinds and segments[-1]['labels'] == labels:
                segments[-1]['text'] += text[x:y]
            else:
                segments.append(dict(text=text[x:y], marks=kinds, labels=labels))
        result.append(dict(start=a, end=b, marked=bool(inside), segments=segments))
    return result


def stable_run_id(run_dir: str | Path) -> str:
    """Suggested registration ID for a run directory (config authors may choose any)."""
    return 'run-' + hashlib.sha256(str(Path(run_dir).resolve()).encode()).hexdigest()[:12]
