"""SN adapter for method-v4 (binding plan W2): v3's SNAdapter plus subsets, safeguard observation, reviewer.

Everything v3 does (versions = v2 candidates, analyst / implementer / repair
Agents, smoke, search-set diagnosis, rebase) is inherited unchanged; request
keys start with ``v4/`` instead of ``v3/`` and the analyst sees the v4 history
vocabulary. New:

- ``evaluate_subset``: v2 ``RoundOperations.subset`` on the run's fixed
  validation subsets ``subset:mb:<hash>`` / ``subset:rest:<hash>`` with an
  explicit k (frozen with the key), paired with the parent or, for the seed's
  calibration, alone.
- ``observe_safeguards``: the iteration protocol's safeguards observed exactly
  as v3 does (``observe_safeguards`` + the v3 infrastructure rule), returned
  as point values; the verdict is v4's (``decide.safeguard_verdict``).
- ``review``: the accountant writes the blinded pairs into an evidence file;
  a v2 Agent with role ``reviewer`` (brief ``method-v4``, bound to the frozen
  candidate, no commands) reads it and writes one verdict file.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Optional

from ..adapter import Card, Diff, EvalResult, Version
from ..sn_adapter import SNAdapter

REVIEW_FORMAT = "sn-v4-review-evidence-v1"
V4_REVIEWER_BRIEF = "method-v4"
MAX_REVIEW_DIFF_CHARS = 12000


class SNAdapterV4(SNAdapter):
    _KEY_PREFIX = "v4"
    _HISTORY_VOCABULARY = "v4"
    _BRIEF = V4_REVIEWER_BRIEF       # every Agent of a v4 run: the v4 analyst / implementer / reviewer brief

    def __init__(self, config: dict, *, reviewer_profile: Optional[str] = None, reviewer_view_budget: int = 30000,
                 **kwargs) -> None:
        super().__init__(config, **kwargs)
        self._reviewer_profile = reviewer_profile or self._analyst_profile
        self._reviewer_view_budget = reviewer_view_budget
        self.last_review: dict = {}

    # ------------------------------------------------------------------
    # subsets (method-v4 §3.2 stages 1-2, §2.3 calibration)
    # ------------------------------------------------------------------

    def evaluate_subset(self, version: Version, reference: Optional[Version], subset: str, task_ids: list[str],
                        k: int) -> tuple[EvalResult, Optional[EvalResult]]:
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
                                "p" if rcid is not None else "u", *self._generation())
        kwargs = self._v2_run_kwargs()
        label = f"{subset} {cid} k={k}"
        self._await({label: (key, lambda: ops.subset(cid, subset, ids, k=k, paired=rcid is not None, key=key,
                                                     **kwargs))},
                    {label: (cid, rcid)})
        scopes = {t: scope_id for t in ids}
        return self._read(cid, scopes), (self._read(rcid, scopes) if rcid is not None else None)

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

    def observe_safeguards(self, candidate: EvalResult, reference: EvalResult) -> dict:
        from rag_eval.workbench.comparisons import ComparisonService, observe_safeguards
        specs = list(self._protocol().get("safeguards") or [])
        ops, views = self._require_round_ops("observe_safeguards"), self._views()
        comparisons = ComparisonService(ops.repository, ops.service, views, initialize=False)
        trajectories, infra = {}, {}
        for side, result in (("subject", candidate), ("reference", reference)):
            cid = (result.context or {}).get("candidate_id")
            if not cid:
                raise ValueError("safeguards need SN results (context.candidate_id)")
            cases: dict[str, list] = {}
            for case_id, stubs in result.trajectories.items():
                for stub in stubs or []:
                    try:
                        cases.setdefault(case_id, []).append(views.observation(stub["run_id"], case_id))
                    except (KeyError, ValueError):
                        continue
            trajectories[side] = cases
            node = ops.service.candidate(cid)
            scopes = sorted(set((result.context.get("scopes") or {}).values()), key=lambda x: (x is not None, x or ""))
            rows = [row for scope in scopes for row in self._infrastructure(comparisons, node, scope)]
            infra[side] = dict(total=sum(r["total"] for r in rows), failed=sum(r["failed"] for r in rows),
                               unobserved=sum(r["unobserved"] for r in rows))
        observed, _ = observe_safeguards(trajectories)
        if all(infra[s]["total"] and not infra[s]["unobserved"] for s in infra):
            observed["infrastructure_failure_rate"] = {s: infra[s]["failed"] / infra[s]["total"] for s in infra}
        out = {}
        for spec in specs:
            value = observed.get(spec["key"]) or {}
            out[spec["key"]] = dict(direction=spec["direction"], margin=spec.get("margin"),
                                    subject=value.get("subject"), reference=value.get("reference"))
        return out

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
        from ..sn_trajectory import render_views
        if self.evidence_dir is None:
            raise RuntimeError("review() needs an evidence directory (set_evidence_dir)")
        iteration_id, _ = self._require_ids()
        cid = self.candidate_id(candidate)
        items = []
        for p in pairs:
            for side in ("A", "B"):
                # The task id only locates the row; the rendered view (whitelist) never contains it.
                views = self.diagnostic_views({p["task"]: list(p[side])})
                if p["task"] in views:
                    items.append((f"{p['pair']}-{side}", f"pair {p['pair']} side {side}", views[p["task"]]))
        diff_text = ((diff.content if diff is not None else "") or "")
        cut = len(diff_text) > MAX_REVIEW_DIFF_CHARS
        head = (f"# method-v4 reviewer evidence\nformat: {REVIEW_FORMAT}\ncandidate: {cid}\n"
                f"pairs: {', '.join(p['pair'] for p in pairs)}\n\n## Card\nfailure mode: "
                f"{' '.join(card.failure_mode_desc.split())}\nchange hypothesis: {card.change_hypothesis}\n\n"
                f"## Diff{' (truncated)' if cut else ''}\n```diff\n{diff_text[:MAX_REVIEW_DIFF_CHARS]}\n```\n\n"
                "## Pairs (in each pair the sides A and B are in random order; some pairs may not differ)\n")
        views_text, shown, omitted = render_views(items, max(1000, self._reviewer_view_budget - len(head)))
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
