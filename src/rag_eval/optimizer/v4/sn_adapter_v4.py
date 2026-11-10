"""SN adapter for method-v4 (binding plan W2): v3's SNAdapter plus subsets, safeguard observation, reviewer.

V3 supplies candidate lifecycle, Agent dispatch, smoke and rebase mechanics.
Request keys start with ``v4/`` and V4 adds bounded source diagnosis, immutable
per-card search evidence and implementer-reported preflight feedback before
materialization. V3's default hooks preserve its existing behavior. Also:

- ``evaluate_subset``: v2 ``RoundOperations.subset`` on the run's fixed
  validation subsets ``subset:mb:<hash>`` / ``subset:rest:<hash>`` with an
  explicit k (frozen with the key), paired with the parent or, for the seed's
  calibration, alone.
- ``observe_safeguards`` (method-v4.1 §4.5): ``tokens_per_task_ratio`` (ratio of
  mean tokens per admitted attempt) and ``answer_produced_rate`` (share of admitted
  attempts whose output status is ``success``), each with per-task, per-attempt
  detail; infrastructure counts (v3 rule) are returned as information only. The
  verdict is v4.1's (``decide.safeguard_verdict``); safeguards come from the v4
  config, not from the iteration protocol.
- ``remeasure_subset``: one more attempt of a version on a subset under its own
  request key (tag ``remeasure``), read back with every admitted attempt.
- Per-card hard time limit: ``implement`` / ``repair`` end with a TimeoutError
  after ``agent_timeout`` seconds, including the worktree wait.
- ``review``: the accountant writes the blinded pairs into an evidence file;
  a v2 Agent with role ``reviewer`` (brief ``method-v4``, bound to the frozen
  candidate, no commands) reads it and writes one verdict file. Each pair shows
  the question once and then only the fields on which A and B differ.
- ``evaluate_search``: diagnosis on a per-parent search sample
  (``subset:diag:<hash>``) at its own k instead of the whole search split.
- A global cap on concurrent subset / diagnosis measurements
  (``max_concurrent_measurements``): concurrent cold executions compete for the
  same preparation CPUs and model-gate slots. Smoke samples are not capped.
- Preparation cost: ``touches_preparation`` reads a diff against
  ``preparation_cache.PREPARATION_SOURCES``; the analyst's evidence and the
  implementer's message name that list (a file in the evidence directory).
  The implementer also gets the analyst evidence file its card came from.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from pathlib import Path
from typing import Any, Optional

from ..adapter import Card, Diff, EvalResult, Version
from ..sn_adapter import SNAdapter

REVIEW_FORMAT = "sn-v4-review-evidence-v2"
V4_REVIEWER_BRIEF = "method-v4"
MAX_REVIEW_DIFF_CHARS = 12000


class SNAdapterV4(SNAdapter):
    _KEY_PREFIX = "v4"
    _HISTORY_VOCABULARY = "v4"
    _BRIEF = V4_REVIEWER_BRIEF       # every Agent of a v4 run: the v4 analyst / implementer / reviewer brief

    def __init__(self, config: dict, *, reviewer_profile: Optional[str] = None, reviewer_view_budget: int = 30000,
                 calibrator_profile: Optional[str] = None,
                 max_concurrent_measurements: int = 3, **kwargs) -> None:
        super().__init__(config, **kwargs)
        self._reviewer_profile = reviewer_profile or self._analyst_profile
        self._calibrator_profile = calibrator_profile or self._analyst_profile
        if type(reviewer_view_budget) is not int or reviewer_view_budget < 1:
            raise ValueError("reviewer_view_budget must be a positive int")
        self._reviewer_view_budget = reviewer_view_budget
        if type(max_concurrent_measurements) is not int or max_concurrent_measurements < 1:
            raise ValueError("max_concurrent_measurements must be a positive int")
        self._measure_slots = threading.BoundedSemaphore(max_concurrent_measurements)
        self._card_evidence_lock = threading.Lock()
        self._safeguard_cache: dict[tuple, dict] = {}
        self._safeguard_cache_lock = threading.Lock()
        self._card_limit = threading.local()
        self.last_review: dict = {}
        self._v41_measurements = None
        self._v41_measurements_lock = threading.Lock()

    def _gated(self, call):
        """Run one keyed measurement while holding a global measurement slot."""
        with self._runtime_slot(self._measure_slots):
            return call()

    # ------------------------------------------------------------------
    # subsets (method-v4 §3.2 stages 1-2, §2.3 calibration)
    # ------------------------------------------------------------------

    def evaluate_subset(self, version: Version, reference: Optional[Version], subset: str, task_ids: list[str],
                        k: int) -> tuple[EvalResult, Optional[EvalResult]]:
        return self._subset(version, reference, subset, task_ids, k)

    def remeasure_subset(self, version: Version, subset: str, task_ids: list[str]) -> EvalResult:
        """Re-measurement (method-v4.1 §4.4): v2 tops the version up to 2 attempts on the subset.

        A distinct request key (tag ``remeasure``); the result holds every admitted
        attempt of the version on that scope, i.e. the first measurement and this one.
        """
        return self._subset(version, None, subset, task_ids, 2, tag="remeasure")[0]

    def _subset(self, version, reference, subset, task_ids, k, tag=None):
        from rag_eval.workbench.execution_scopes import subset_scope_id
        ops = self._require_round_ops("evaluate_subset")
        cid = self.candidate_id(version)
        rcid = self.candidate_id(reference) if reference is not None else None
        if rcid is not None and ops.service.candidate(cid).get("parent_candidate_id") != rcid:
            raise ValueError(f"a paired subset compares a candidate with its parent, not {rcid!r}")
        ids = sorted(task_ids)
        scope_id = subset_scope_id(self._protocol(), subset, ids)    # refuses non-validation ids
        # The scope is in the key: each parent has its own minibatch (method §3.2), and a
        # paired and an unpaired request never share a key.
        key = self._request_key("subset", subset, scope_id.rsplit(":", 1)[-1], cid, k,
                                "p" if rcid is not None else "u", *((tag,) if tag else ()), *self._generation())
        kwargs = self._v2_run_kwargs()
        label = f"{subset} {cid} k={k}" + (f" {tag}" if tag else "")
        from .measurements import V41Measurements
        with self._v41_measurements_lock:
            if self._v41_measurements is None:
                self._v41_measurements = V41Measurements(ops.api, execution_timeout_override=
                    getattr(self, '_recovery_execution_timeout_seconds', None))
        measurements = self._v41_measurements
        self._gated(lambda: self._await({label: (key, lambda: measurements.subset(
            cid, subset, ids, k=k, paired=rcid is not None, key=key, **kwargs))}, {label: (cid, rcid)}))
        return (measurements.read_result(cid, scope_id, k, key=key),
                measurements.read_result(rcid, scope_id, k, key=key) if rcid is not None else None)

    def evaluate_search(self, version: Version, task_ids: list[str], k: int) -> EvalResult:
        """Diagnosis: *version* alone on a search sample (``subset:diag``), k attempts."""
        return self.evaluate_subset(version, None, "diag", task_ids, k)[0]

    # ------------------------------------------------------------------
    # preparation cost (method-v4 §6 cost estimate, Agent guidance)
    # ------------------------------------------------------------------

    @staticmethod
    def preparation_paths() -> list[str]:
        from rag_eval.preparation_cache import PREPARATION_SOURCES
        return sorted(set(PREPARATION_SOURCES))

    def touches_preparation(self, diff: Optional[Diff]) -> Optional[bool]:
        """Conservative hint: whole-file hits are certain; unresolved symbol-file hits are unknown.

        A unified diff without the complete old/new Python source cannot prove
        which enclosing symbol an arbitrary hunk belongs to. Never classify an
        answer-prompt edit as a preparation edit just because it shares a file.
        """
        text = (diff.content if diff is not None else "") or ""
        if not text.strip():
            return None
        changed = set(re.findall(r"^diff --git a/(\S+) b/", text, re.M)) | set(re.findall(r"^\+\+\+ b/(\S+)", text, re.M))
        if not changed:
            return None
        unknown = False
        for entry in self.preparation_paths():
            path, _, symbol = entry.partition("::")
            if not any(c == path or (path.endswith("/") and c.startswith(path)) for c in changed):
                continue
            if not symbol:
                return True
            # Only explicit changed definitions/assignments prove a symbol hit.
            # Other edits in the file remain unknown, including hunk headers.
            chunks = re.split(r"(?=^diff --git )", text, flags=re.M)
            for chunk in chunks:
                if not re.search(r"^(?:diff --git a/" + re.escape(path) + r" b/|\+\+\+ b/" + re.escape(path) + r"$)", chunk, re.M):
                    continue
                if re.search(r"^[+-](?:async\s+def\s+|def\s+|class\s+)?" + re.escape(symbol)
                             + r"(?:\s*\(|\s*(?::[^=\n]+)?=)", chunk, re.M):
                    return True
            unknown = True
        return None if unknown else False

    def _preparation_file(self) -> tuple[Path, str]:
        lines = ["# Preparation cache sources", "",
                 "These exact sources contribute to the preparation cache key. Directories end with '/'.",
                 "A ::symbol entry covers only that Python definition/assignment, not the whole file.",
                 "Changing a covered source may require a new preparation snapshot. A matching snapshot",
                 "can be reused across evaluations; actual cache hits depend on settings, inputs and availability.",
                 "Answer-path prompts outside the listed symbols do not by themselves invalidate preparation.", ""]
        path, digest, _ = self._write_evidence("preparation-sources-v2.md", "\n".join(lines + self.preparation_paths()) + "\n")
        return path, digest

    def _evidence_extra(self) -> str:
        path, digest = self._preparation_file()
        advice = getattr(self._card_limit, 'calibration_advice', None)
        prefix = ('## Calibration advisor hypotheses (D only; verify independently)\n'
                  + json.dumps(advice, ensure_ascii=False) + '\n\n') if advice else ''
        return self._task_note() + prefix + (f"## Cost of a change\nPreparation cache sources are listed in {path} (sha256 {digest}). "
                "The ::symbol entries cover only those definitions, not the whole file. Changing covered sources "
                "may require a new preparation snapshot; subsequent evaluations can reuse it. Prefer answer-path "
                "changes when suitable, and draft the most promising card first.\n\n")

    def _task_note(self) -> str:
        from .task_notes import task_note
        try:
            iteration_id, _ = self._require_ids()
            protocol = self._round_ops.service.iteration(iteration_id).get('protocol') or {}
        except Exception:  # noqa: BLE001  (an adapter without the workbench has no note)
            return ''
        return task_note(protocol.get('input'))

    def analyse(self, *args, calibration_advice=None, **kwargs):
        self._card_limit.calibration_advice = calibration_advice
        try:
            return super().analyse(*args, **kwargs)
        finally:
            self._card_limit.calibration_advice = None

    def _analysis_history(self, history, budget, parent_id):
        from .agent_context import analysis_history
        return analysis_history(self, history, budget, parent_id)

    def _analyst_input_text(self):
        return ("This is your only case-evidence input; do not request other cases or read other files under var/, "
                "except the adopted-change inventory and preparation-source list explicitly linked in it. "
                "Read those linked files too. You may inspect the frozen parent's product source/tests through "
                "the bounded `source --path <product path> [--query <literal>] [--start N --lines N]` command. ")

    def _analyst_implementation_guidance(self):
        return ("Each card's implementer_brief must name the relevant product source locations, the proposed "
                "pattern or logic to change if the hypothesis holds, and the cheapest discriminating source, "
                "test or probe check. Include relevant test commands when useful; a source inspection may "
                "already settle the premise. A brief that only restates the failure mode is not acceptable. ")

    def _analyst_stuck_text(self, no_adoption_rounds):
        if not no_adoption_rounds:
            return ""
        text = (f"No hypothesis has been adopted under this parent for {no_adoption_rounds} consecutive "
                "round(s). Avoid identical adopted or in-progress proposals; revisit a refuted claim only with new "
                "evidence that addresses the reported refutation. ")
        if no_adoption_rounds >= 2:
            text += ("Change direction: target failure modes, components or product stages different from every "
                     "direction in the history (for example retrieval/evidence selection instead of answer wording, "
                     "or the reverse), and re-read the task-and-metric section before choosing. ")
        return text

    def _analyst_extra_text(self) -> str:
        return ("Separate observations from causal hypotheses in mechanism, and put the cheapest discriminating "
                "source/test/probe check in falsification. Cite only actually displayed opaque case references in "
                "search_refs (for example [\"c1\", \"c3\"]); use [] for a source-only hypothesis. "
                "Draft the most promising card first: cards are implemented in draft order. Prefer changes outside "
                "the preparation path when suitable. "
                f"The implementer has at most {int(self._agent_timeout // 60)} minutes per card, including its checks: "
                "keep each card a small, single-mechanism change; split a multi-step retrieval change (extra model "
                "calls, retries, re-ranking) into one bounded step per card. ")

    def _write_analysis_evidence(self, name, content, items, ordered, shown):
        """Publish a durable text+mapping snapshot first, so an interrupted publication is recoverable."""
        from ..sn_trajectory import _render_one
        from .. import ledger_io
        path = self.evidence_dir / name
        manifest = path.with_suffix('.cases.json')
        if path.exists():
            # Pre-upgrade evidence without a manifest remains legacy; don't
            # assign today's refs to an old file that an analyst already read.
            return self._write_evidence(name, content)
        if manifest.exists():
            saved = json.loads(manifest.read_text(encoding='utf-8'))
            content = saved.get('evidence_text')
            if (not isinstance(content, str) or hashlib.sha256(content.encode('utf-8')).hexdigest()
                    != saved.get('evidence_sha256')):
                raise ValueError('search_refs staged evidence provenance is invalid')
        else:
            rows = {ref: dict(case_id=case_id, rendered=_render_one(ref, role, view))
                    for (ref, role, view), case_id in zip(items[:shown], ordered[:shown])}
            saved = dict(evidence_sha256=hashlib.sha256(content.encode('utf-8')).hexdigest(),
                         evidence_text=content, cases=rows)
            ledger_io.atomic_text(manifest, json.dumps(saved, ensure_ascii=False))
        return self._write_evidence(name, content)

    def _published_evidence(self, name):
        """The file, or the staged text a recovery would publish under *name*."""
        published = super()._published_evidence(name)
        manifest = (self.evidence_dir / name).with_suffix('.cases.json')
        if published is None and manifest.exists():
            text = json.loads(manifest.read_text(encoding='utf-8')).get('evidence_text')
            return text.encode('utf-8') if isinstance(text, str) else b''
        return published

    def _analysis_evidence_ready(self, path, digest, items, ordered, shown, reused):
        manifest = Path(path).with_suffix('.cases.json')
        if manifest.exists():
            saved = json.loads(manifest.read_text(encoding='utf-8'))
            if saved.get('evidence_sha256') != digest:
                raise ValueError('search_refs evidence provenance changed')

    def _cards_analysed(self, cards, path, digest) -> None:
        """Bind each card to its cited rendered cases; existing bindings never change on replay."""
        from .. import ledger_io
        index = self.evidence_dir / 'card-evidence.json'
        manifest = Path(path).with_suffix('.cases.json')
        saved = json.loads(manifest.read_text(encoding='utf-8')) if manifest.exists() else None
        if hashlib.sha256(Path(path).read_bytes()).hexdigest() != digest:
            raise ValueError('search_refs evidence file changed')
        if saved is not None and saved.get('evidence_sha256') != digest:
            raise ValueError('search_refs evidence provenance changed')
        with self._card_evidence_lock:
            known = json.loads(index.read_text(encoding='utf-8')) if index.exists() else {}
            for card in cards:
                if not card.ref:
                    continue
                previous = known.get(card.ref)
                if previous:
                    if previous.get('round_sha256', previous.get('sha256')) != digest:
                        raise ValueError('search_refs card already bound to another round')
                    continue
                raw = (self._hypotheses.get(self._require_ids()[1], card.ref)['card']
                       if self._hypotheses is not None else {})
                refs = raw.get('search_refs')
                if refs is None:
                    known[card.ref] = dict(path=str(path), sha256=digest, legacy=True,
                                          round_path=str(path), round_sha256=digest)
                    continue
                cases = saved.get('cases', {}) if saved else {}
                if not isinstance(refs, list) or any(ref not in cases for ref in refs):
                    raise ValueError('search_refs names unknown or omitted case evidence')
                content = ('# Card search evidence\nOnly this card\'s cited search cases, exactly as shown to the analyst.\n\n'
                           + ''.join(cases[ref]['rendered'] for ref in refs))
                token = hashlib.sha256(card.ref.encode()).hexdigest()[:24]
                selected, selected_digest, _ = self._write_evidence(f'card-{token}-search.md', content)
                known[card.ref] = dict(path=str(selected), sha256=selected_digest, search_refs=list(refs),
                                      round_path=str(path), round_sha256=digest, legacy=False)
            ledger_io.atomic_text(index, json.dumps(known, ensure_ascii=False, indent=1))

    # ------------------------------------------------------------------
    # per-card hard time limit (method-v4.1 §4.1) and card direction (§4.1)
    # ------------------------------------------------------------------

    def _within_card_limit(self, call):
        # The limit includes the worktree wait of ``workspace`` that preceded ``implement``.
        start = getattr(self._card_limit, "started", None) or time.monotonic()
        self._card_limit.started = None
        self._card_limit.deadline = start + self._agent_timeout
        try:
            return call()
        finally:
            self._card_limit.deadline = None

    def workspace(self, version, card=None):
        start = time.monotonic()
        self._card_limit.deadline = start + self._agent_timeout
        try:
            out = super().workspace(version, card)
        except BaseException:
            self._card_limit.started = None
            raise
        finally:
            self._card_limit.deadline = None
        self._card_limit.started = start
        return out

    def implement(self, card: Card, workspace: Any) -> Diff:
        return self._within_card_limit(lambda: super(SNAdapterV4, self).implement(card, workspace))

    def repair(self, *args, **kwargs):
        return self._within_card_limit(lambda: super(SNAdapterV4, self).repair(*args, **kwargs))

    def _runtime_wait(self, seconds: float) -> None:
        deadline = getattr(self._card_limit, "deadline", None)
        if deadline is not None:
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError(f"implementer agent exceeded the per-card time limit of {self._agent_timeout}s")
            seconds = min(seconds, left)
        return super()._runtime_wait(seconds)

    def card_direction(self, card: Card) -> Optional[str]:
        """The analyst's ``direction`` tag of a drafted card (None when absent: inferred by the loop)."""
        if self._hypotheses is None or not card.ref:
            return None
        raw = (self._hypotheses.get(self._require_ids()[1], card.ref) or {}).get("card") or {}
        value = raw.get("direction")
        return value if isinstance(value, str) and value.strip() else None

    def _preflight_path(self, workspace):
        if self.evidence_dir is None:
            raise RuntimeError('preflight report needs an evidence directory')
        cid = workspace.get('candidate_id')
        if not isinstance(cid, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', cid):
            raise ValueError('preflight candidate identity is invalid')
        return self.evidence_dir / f'preflight-{cid}.json'

    def _repair_input_text(self):
        return 'read it in full, along with the preparation and card-evidence files named below. '

    def _implementation_order(self, *, repair=False):
        return ('cheap source/test/probe falsification check -> decide implement or skip -> '
                'if implementing, edit and run only relevant SN tests -> leak-check -> write preflight report. ')

    def _implementation_metadata(self, card, workspace, *, frozen=False):
        from ..adapter import ImplementationDeclined
        from .. import ledger_io
        path = self._preflight_path(workspace)
        accepted = path.with_suffix('.accepted.json')
        source = accepted if accepted.exists() else path
        if frozen and not accepted.exists():
            raise RuntimeError('preflight accepted report missing for frozen replay')
        try:
            with source.open('rb') as handle:
                data = handle.read(16385)
            if len(data) > 16384:
                raise ValueError('report exceeds 16384 bytes')
            report = json.loads(data)
        except (OSError, ValueError) as exc:
            raise ValueError(f'preflight report missing or malformed: {exc}') from exc
        fields = {'candidate_id', 'hypothesis_id', 'parent_candidate_id', 'decision', 'reason', 'summary', 'checks'}
        if not isinstance(report, dict) or set(report) != fields:
            raise ValueError('preflight report fields do not match the required shape')
        candidate = self._require_round_ops('preflight').service.candidate(workspace['candidate_id'])
        expected = dict(candidate_id=workspace['candidate_id'],
                        hypothesis_id=workspace.get('hypothesis_id') or card.ref,
                        parent_candidate_id=workspace.get('parent_candidate_id'))
        if (any(not expected[key] or report[key] != expected[key] for key in expected)
                or candidate.get('proposal_id') != expected['hypothesis_id']
                or candidate.get('parent_candidate_id') != expected['parent_candidate_id']):
            raise ValueError('preflight report belongs to another candidate, card or parent')
        allowed = {'implement': ('supported', 'inconclusive'),
                   'skip': ('refuted', 'already_satisfied', 'inconclusive')}
        if (not isinstance(report['decision'], str) or report['decision'] not in allowed
                or report['reason'] not in allowed[report['decision']]):
            raise ValueError('preflight decision and reason are incompatible')
        def concrete(value, limit):
            return isinstance(value, str) and bool(value.strip()) and len(value) <= limit and '\0' not in value
        if not concrete(report['summary'], 2000):
            raise ValueError('preflight summary must be bounded nonempty text')
        checks = report['checks']
        if not isinstance(checks, list) or not checks or len(checks) > 32:
            raise ValueError('preflight requires concrete checks (1 to 32)')
        for check in checks:
            if (not isinstance(check, dict) or set(check) != {'kind', 'target', 'observation'}
                    or check['kind'] not in ('source', 'test', 'probe')
                    or not concrete(check['target'], 500) or not concrete(check['observation'], 2000)):
                raise ValueError('preflight checks require kind, concrete target and observation')
        if not accepted.exists():
            ledger_io.atomic_text(accepted, json.dumps(report, ensure_ascii=False, sort_keys=True))
        if report['decision'] == 'skip':
            raise ImplementationDeclined(report)
        return dict(preflight=report)

    def _implementer_extra_text(self, card: Card, workspace: dict) -> str:
        index = self.evidence_dir / 'card-evidence.json' if self.evidence_dir is not None else None
        known = json.loads(index.read_text(encoding='utf-8')) if index is not None and index.exists() else {}
        entry = known.get(card.ref or workspace.get('hypothesis_id') or '')
        prep, prep_digest = self._preparation_file()
        text = (f' Preparation cache sources are listed in {prep} (sha256 {prep_digest}). '
                'The ::symbol entries cover only those definitions, not the entire file. Covered changes may '
                'require a new snapshot, which subsequent evaluations can reuse. Prefer answer-path changes when suitable.')
        if entry:
            if hashlib.sha256(Path(entry['path']).read_bytes()).hexdigest() != entry['sha256']:
                raise ValueError('search_refs card evidence changed')
            text += (f" The approved evidence for this card (search cases only) is {entry['path']} "
                     f"(sha256 {entry['sha256']}); read it before checking the hypothesis.")
            if entry.get('legacy', 'search_refs' not in entry):
                text += ' Legacy fallback: this card has no search_refs; the original round context is provided without invented case attribution.'
        else:
            ref = card.ref or workspace.get('hypothesis_id')
            raw = (self._hypotheses.get(self._require_ids()[1], ref)['card']
                   if self._hypotheses is not None and ref else {})
            if 'search_refs' in raw:
                raise ValueError('search_refs evidence binding is missing for this card')
            text += ' Legacy fallback: this card has no recorded search_refs evidence binding.'
        if workspace.get('candidate_id'):
            path = self._preflight_path(workspace)
            identity = {key: workspace.get(key) or (card.ref if key == 'hypothesis_id' else None)
                        for key in ('candidate_id', 'hypothesis_id', 'parent_candidate_id')}
            text += (f' Write the preflight report to {path}. JSON fields: {json.dumps(identity, sort_keys=True)} '
                     'plus decision (implement|skip), reason (supported|refuted|already_satisfied|inconclusive), '
                     'summary, checks [{kind: source|test|probe, target, observation}]. Limit 16384 UTF-8 bytes; '
                     'summary <=2000 chars; 1 to 32 checks, target <=500 chars, observation <=2000 chars. '
                     'implement requires supported or inconclusive; skip requires refuted, already_satisfied or '
                     'inconclusive. Both decisions require nonempty concrete checks. Report actual checks and their limits; these are '
                     'implementer-reported findings, not independently proved conclusions. If skipping, leave no '
                     'product edits. The coordinator reads this report after you are idle, before freezing or measurement.')
        return text

    # ------------------------------------------------------------------
    # task families (papers) and the cache / timing profile of a measurement
    # ------------------------------------------------------------------

    def task_families(self, task_ids: list[str]) -> dict[str, str]:
        """task -> paper (partition id of the frozen input); {} when the catalogue is unavailable."""
        ops = self._require_round_ops("task_families")
        try:
            input_id = self._protocol()["input"]["input_id"]
            partitions = ops.service.plans.inputs.describe(input_id)["partitions"]
        except (AttributeError, KeyError, LookupError, TypeError, ValueError):
            return {}
        wanted = set(task_ids)
        return {c: str(p["partition_id"]) for p in partitions for c in p.get("case_ids", ()) if c in wanted}

    def timing_profile(self, result: EvalResult) -> list[dict]:
        """Per execution behind *result*: wall seconds, cases, and preparation-cache hits / misses / lock waits.

        Read from the execution record (started_at / finished_at) and each partition
        run's ``preparation-cache.json``. Executions not yet terminal are skipped.
        """
        from datetime import datetime
        ops = self._require_round_ops("timing_profile")
        _, task_id = self._require_ids()
        by_execution: dict[str, dict] = {}
        for task, stubs in (result.trajectories or {}).items():
            for stub in stubs or []:
                if isinstance(stub, dict) and stub.get("execution_id"):
                    row = by_execution.setdefault(stub["execution_id"], dict(cases=set(), runs=set()))
                    row["cases"].add(task)
                    if stub.get("run_dir"):
                        row["runs"].add(stub["run_dir"])
        out = []
        for eid, row in sorted(by_execution.items()):
            record = ops.service._execution(task_id, eid) or {}
            try:
                wall = (datetime.fromisoformat(record["finished_at"]) - datetime.fromisoformat(record["started_at"])
                        ).total_seconds()
            except (KeyError, TypeError, ValueError):
                continue
            hits = misses = 0
            waited = 0.0
            for run_dir in row["runs"]:
                try:
                    receipt = json.loads((Path(run_dir) / "preparation-cache.json").read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if receipt.get("status") == "hit":
                    hits += 1
                else:
                    misses += 1
                waited = max(waited, float(receipt.get("lock_waited_seconds") or 0.0))
            out.append(dict(execution_id=eid, wall_s=wall, cases=len(row["cases"]), partitions=len(row["runs"]),
                            cache_hits=hits, cache_misses=misses, lock_waited_s=waited))
        return out

    # ------------------------------------------------------------------
    # safeguards: observation only (the verdict is method-v4's point rule)
    # ------------------------------------------------------------------

    def _safeguard_observation(self, views, stub: dict, case_id: str) -> dict:
        """Cache only terminal output-derived status/token evidence, never infrastructure.

        The key identifies the actual registered run and artifact revisions. The
        access policy is checked even on hits. Synthetic/alternate views without
        this provenance API keep their normal observation semantics.
        """
        run_id = stub["run_id"]
        entry_fn = getattr(views, "_entry", None)
        check = getattr(views, "_assert_unsealed", None)
        if not callable(entry_fn) or not callable(check):
            return views.observation(run_id, case_id)
        check(run_id, case_id)
        entry = entry_fn(run_id)
        run_dir = Path(entry.run_dir).resolve()
        from ..sn_trajectory import read_rows, run_is_final
        if not run_is_final(run_dir):
            return views.observation(run_id, case_id)
        def provenance():
            out = []
            for name in ("state.json", "outputs.jsonl"):
                info = (run_dir / name).stat()
                out.append((name, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns))
            return tuple(out)
        try:
            signature = provenance()
        except OSError:
            return views.observation(run_id, case_id)
        key = ("safeguard-output-v2", run_id, stub.get("execution_id"), str(run_dir), case_id, signature)
        with self._safeguard_cache_lock:
            hit = self._safeguard_cache.get(key)
            if hit is not None:
                return dict(question=hit["question"], prediction=hit["prediction"], status=hit["status"],
                            response=dict(hit["response"]), usage=dict(hit["usage"]))
        observed = views.observation(run_id, case_id)
        load = getattr(views, "_load", None)
        try:
            # RunViews already batches the output stream; reuse that projection
            # instead of rescanning the same run once for each case.
            row = ((load(run_id).get("_outputs") or {}).get(case_id) if callable(load)
                   else read_rows(run_dir, [case_id]).get(case_id)) or {}
        except OSError:
            return observed
        record = row.get("product_record") or {}
        question = record.get("original_question") or record.get("question")
        projected = dict(question=observed.get("question"), prediction=observed.get("prediction"),
                         status=observed.get("status"),
                         response=dict(answer=(observed.get("response") or {}).get("answer")),
                         usage=dict(total_tokens=(observed.get("usage") or {}).get("total_tokens")))
        # Some views derive question/tokens from other artifacts or live service
        # state. Cache only when all safeguard inputs demonstrably match outputs.
        proven = (question is not None and projected["question"] == question
                  and projected["prediction"] == row.get("prediction")
                  and projected["status"] is not None and projected["status"] == row.get("status")
                  and projected["response"]["answer"] == (record.get("response") or {}).get("answer")
                  and projected["usage"]["total_tokens"] == (record.get("usage") or {}).get("total_tokens"))
        try:
            stable = signature == provenance()
        except OSError:
            stable = False
        if proven and stable:
            with self._safeguard_cache_lock:
                if len(self._safeguard_cache) >= self._TRAJ_CACHE_SIZE:
                    self._safeguard_cache.pop(next(iter(self._safeguard_cache)))
                self._safeguard_cache[key] = projected
        return observed

    SAFEGUARDS = ("tokens_per_task_ratio", "answer_produced_rate")

    def observable_safeguards(self) -> tuple[str, ...]:
        return self.SAFEGUARDS

    def observe_safeguards(self, candidate: EvalResult, reference: EvalResult) -> dict:
        """Point values and per-task, per-attempt detail of the v4.1 SN safeguards (verdict: decide).

        tokens_per_task_ratio: mean total tokens per admitted attempt, subject / reference
        (``kind='ratio'``; per task the raw token counts). answer_produced_rate: share of
        admitted attempts whose output status is ``success`` (notebook_runner writes
        success / no_answer / error; ``kind='proportion'``). Infrastructure failures are
        never a safeguard: ``infrastructure`` carries v3-rule counts for the record only.
        """
        from rag_eval.workbench.comparisons import ComparisonService
        ops, views = self._require_round_ops("observe_safeguards"), self._views()
        comparisons = ComparisonService(ops.repository, ops.service, views, initialize=False)
        tokens: dict[str, dict] = {}
        produced: dict[str, dict] = {}
        infra = {}
        for side, result in (("subject", candidate), ("reference", reference)):
            cid = (result.context or {}).get("candidate_id")
            if not cid:
                raise ValueError("safeguards need SN results (context.candidate_id)")
            for case_id, stubs in result.trajectories.items():
                for stub in stubs or []:
                    try:
                        o = self._safeguard_observation(views, stub, case_id)
                    except (KeyError, ValueError):
                        continue
                    t = (o.get("usage") or {}).get("total_tokens")
                    if type(t) in (int, float):
                        tokens.setdefault(case_id, dict(subject=[], reference=[]))[side].append(float(t))
                    if o.get("status") is not None:
                        produced.setdefault(case_id, dict(subject=[], reference=[]))[side].append(
                            1.0 if o.get("status") == "success" else 0.0)
            node = ops.service.candidate(cid)
            scopes = sorted(set((result.context.get("scopes") or {}).values()), key=lambda x: (x is not None, x or ""))
            rows = [row for scope in scopes for row in self._infrastructure(comparisons, node, scope)]
            infra[side] = dict(total=sum(r["total"] for r in rows), failed=sum(r["failed"] for r in rows),
                               unobserved=sum(r["unobserved"] for r in rows))

        from .decide import paired_safeguard_means
        tokens, ts, tr = paired_safeguard_means(tokens)
        produced, ps, pr = paired_safeguard_means(produced)
        ratio = ts / tr if ts is not None and tr else None
        return dict(
            tokens_per_task_ratio=dict(kind="ratio", direction="lower_is_better", subject=ratio,
                                       reference=1.0 if ratio is not None else None, tasks=tokens,
                                       means=dict(subject=ts, reference=tr)),
            answer_produced_rate=dict(kind="proportion", direction="higher_is_better",
                                      subject=ps, reference=pr,
                                      tasks=produced),
            infrastructure=infra)

    # ------------------------------------------------------------------
    # reviewer (method-v4 §3.2 stage 3)
    # ------------------------------------------------------------------

    def review(self, card: Card, diff: Optional[Diff], pairs: list[dict], *, candidate: Version,
               key: str) -> Optional[list[dict]]:
        """Write the evidence file, dispatch one reviewer Agent, read its verdict file.

        The evidence holds the card, the diff (capped) and per pair the two
        diagnostic views (whitelisted projection: no gold, scores, task ids)
        labelled A / B as the accountant shuffled them. The Agent writes JSON to
        the verdict file named in the message; a rerun replays the same Agent
        (key ``v4/{run}/review/{candidate}``) and re-reads the file.
        """
        from rag_eval.workbench.agent_messages import MAX_MESSAGE_BYTES
        if self.evidence_dir is None:
            raise RuntimeError("review() needs an evidence directory (set_evidence_dir)")
        iteration_id, _ = self._require_ids()
        cid = self.candidate_id(candidate)
        blocks, shown = [], 0
        for p in pairs:
            # The task id only locates the rows; the rendered views (whitelist) never contain it.
            a = self.diagnostic_views({p["task"]: list(p["A"])}).get(p["task"])
            b = self.diagnostic_views({p["task"]: list(p["B"])}).get(p["task"])
            blocks.append(render_pair(p["pair"], a, b))
            shown += a is not None and b is not None
        diff_text = ((diff.content if diff is not None else "") or "")
        fixed = (f"# method-v4 reviewer evidence\nformat: {REVIEW_FORMAT}\ncandidate: {cid}\n"
                 f"pairs: {', '.join(p['pair'] for p in pairs)}\n\n")
        instructions = ("## Pairs\nSides A/B are shuffled. Values are canonical JSON; list order matters. "
                        "Identical fields are named once. Truncated values are incomplete evidence.\n\n")
        fields = [("failure mode: ", _json(card.failure_mode_desc)),
                  ("change hypothesis: ", _json(card.change_hypothesis)),
                  ("diff: ", _json(_clip(diff_text, MAX_REVIEW_DIFF_CHARS)))]
        minimum = len(fit_blocks(blocks, 0, minimum_only=True))
        head_prefix, head_suffix = fixed + "## Card and diff\n", "\n" + instructions
        head_minimum = len(head_prefix + head_suffix) + sum(
            len(label) + len(_clip(value, len(TRUNCATED))) + 1 for label, value in fields)
        # Reserve most of the budget for paired observations; a long card/diff
        # must not reduce every side to a truncation marker.
        pair_reserve = max(minimum, self._reviewer_view_budget * 2 // 3)
        head_budget = max(head_minimum, self._reviewer_view_budget - pair_reserve)
        if head_budget + minimum > self._reviewer_view_budget:
            raise ValueError("review evidence budget cannot preserve every pair and both side labels")
        head = _fit_values(head_prefix, fields, head_suffix, head_budget)
        views_text = fit_blocks(blocks, self._reviewer_view_budget - len(head))
        omitted = len(pairs) - shown
        tag = hashlib.sha256(key.encode()).hexdigest()[:12]
        path, digest, reused = self._write_evidence(f"review-{cid[:12]}-{tag}.md", head + views_text)
        verdict = self.evidence_dir / f"review-{cid[:12]}-{tag}.verdict.json"
        text = (f"Review candidate {cid}. Your evidence is the file {path} (sha256 {digest}); read it in full, it "
                f"is your only input. For each pair ({', '.join(p['pair'] for p in pairs)}) decide whether the "
                f"change the card describes took effect and on which side (A or B), whether there is a side "
                f"effect, and whether there is score gaming. Write exactly one JSON object "
                f'{{"pairs": [{{"pair", "effect", "changed_side", "side_effect", "gaming", "note"}}, ...]}} '
                f"to the file {verdict} and nothing else. Run no command other than reading the evidence file "
                f"and writing the verdict file.")
        self.last_review = dict(candidate_id=cid, evidence_path=str(path), evidence_sha256=digest,
                                evidence_reused=reused, verdict_path=str(verdict), views_shown=shown,
                                views_omitted=omitted, message_bytes=len(text.encode("utf-8")))
        if self.last_review["message_bytes"] > MAX_MESSAGE_BYTES:
            raise RuntimeError(f"reviewer message is over v2's {MAX_MESSAGE_BYTES} bytes")
        call = dict(iteration_id=iteration_id, key=self._request_key("review", cid, tag),
                    profile=self._reviewer_profile, text=text, brief=V4_REVIEWER_BRIEF)
        self._poll_agent("reviewer", cid, call)
        return parse_verdict(verdict, [p["pair"] for p in pairs])


TRUNCATED = "[truncated]"


def _json(value: Any) -> str:
    """Canonical mapping order, retained list order, escaped multiline content."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:max(0, limit - len(TRUNCATED))] + TRUNCATED


def _view_lines(view: dict) -> dict[str, str]:
    """Compare structured diagnostic fields, never reverse-parse rendered text."""
    fields = ("question", "status", "behavior", "completeness", "grounded", "evidence_level",
              "citations", "intent", "steps", "answer")
    return {key: _json(view[key]) for key in fields if key in view}


def render_pair(name: str, a: Optional[dict], b: Optional[dict]) -> str:
    """Render complete paired fields before applying a shared display budget."""
    out = [f"### {name}"]
    if a is None or b is None:
        out.extend(f"{side} | trajectory: {'unavailable' if value is None else 'available'}"
                   for side, value in (("A", a), ("B", b)))
        return "\n".join(out) + "\n"
    la, lb = _view_lines(a), _view_lines(b)
    fields = list(dict.fromkeys([*la, *lb]))
    same = [k for k in fields if la.get(k) == lb.get(k)]
    differ = [k for k in fields if la.get(k) != lb.get(k)]
    if "question" in same:
        out.append("question: " + la["question"])
    if same:
        out.append("identical on both sides: " + ", ".join(same))
    if not differ:
        out.extend(["A | comparison: identical", "B | comparison: identical",
                    "A and B are identical in every shown field."])
    for key in differ:
        out.append(f"A | {key}: {la.get(key, '(absent)')}")
        out.append(f"B | {key}: {lb.get(key, '(absent)')}")
    return "\n".join(out) + "\n"


def _fit_values(prefix: str, fields: list[tuple[str, str]], suffix: str, budget: int) -> str:
    """Fit value text fairly while preserving all structural labels."""
    def build(cap):
        return prefix + "".join(label + _clip(value, cap) + "\n" for label, value in fields) + suffix
    if len(build(len(TRUNCATED))) > budget:
        raise ValueError("review evidence budget cannot preserve every pair and both side labels")
    lo, hi = len(TRUNCATED), max([len(TRUNCATED), *(len(value) for _, value in fields)])
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if len(build(mid)) <= budget:
            lo = mid
        else:
            hi = mid - 1
    return build(lo)


def fit_blocks(blocks: list[str], budget: int, *, minimum_only: bool = False) -> str:
    """Strict character budget with every pair header and both sides retained.

    Every value shares the same cap. Clipping a whole block could silently
    remove the B side or a control pair, so only individual value text is cut.
    Impossible budgets fail explicitly rather than yielding biased evidence.
    """
    fields = []
    for block in blocks:
        for line in block.rstrip("\n").split("\n"):
            if ": " in line and not line.startswith("### "):
                label, value = line.split(": ", 1)
                fields.append((label + ": ", value))
            else:
                fields.append((line, ""))
    if minimum_only:
        return "".join(label + _clip(value, len(TRUNCATED)) + "\n" for label, value in fields)
    return _fit_values("", fields, "", budget)


def parse_verdict(path: Path, expected: list[str]) -> list[dict]:
    """The reviewer's verdicts, one per known pair; malformed entries are dropped (never raise on content)."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"reviewer verdict file {path} missing or not JSON: {exc}") from None
    rows = data.get("pairs") if isinstance(data, dict) else None
    out = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or row.get("pair") not in expected:
            continue
        side = row.get("changed_side")
        out.append(dict(pair=row["pair"], effect=row.get("effect") is True,
                        changed_side=side if side in ("A", "B") else None,
                        side_effect=row.get("side_effect") is True, gaming=row.get("gaming") is True,
                        note=str(row.get("note") or "")[:500]))
    return out
