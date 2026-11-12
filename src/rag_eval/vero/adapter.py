"""method-v4.1 SystemAdapter for VeRO-Bench (paper-v1) target agents.

Versions are commits of the target repo (``target.TargetRepo``). Evaluation runs VeRO's own
task code in the agent venv (``evaluation.Evaluator``). Splits: VeRO train -> search,
validation -> validation, test -> the endpoint (``cli test``). The analyst and the implementer
are CLI coding agents run in throwaway worktrees (``agent_dispatch``); the files the access
rules exclude (test tasks, grading rules, mock databases) are hidden from them and edits
outside the writable paths are reverted before a candidate is committed.

Safeguards: ``answer_produced_rate`` (no inference error and a non-empty output) and
``tokens_per_task_ratio``, *estimated* from trace length (chars / 4), not provider-reported
usage. No reviewer, no smoke repair (the loop then rests on the mechanical check).
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import shlex
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from rag_eval.optimizer.adapter import (AgentRequestFailed, Card, Diff, EvalResult, EvaluationUnavailable, Predicate,
                                        SystemAdapter, Version)
from rag_eval.optimizer.v4.decide import paired_safeguard_means

from .access import WEB_SEARCH_AGENT_RULES, AccessRules
from .agent_dispatch import AgentCommandFailed, AgentRunner, last_json, parse_json_strings, shorten
from .evaluation import Evaluator, parse_task_id
from .target import TargetRepo

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Preset:
    name: str
    agent_subdir: str          # inside the VeRO checkout
    module: str                # registers the VeroTask
    task: str                  # VeroTask name
    import_root: str           # put first on sys.path, relative to the checkout
    dataset: str               # vero-benchmarking dataset builder name
    description: str
    gold_keys: tuple = ()      # input fields that hold the reference answer
    hidden_keys: tuple = ()    # input fields never shown to the analyst
    builtin_rules: Optional[str] = None   # used when the agent has no .veroaccess


PRESETS = {
    "simple_qa": Preset(
        name="simple_qa", agent_subdir="vero-agents/agents/web_search_agent", module="web_search_agent.vero_tasks",
        task="simple_qa", import_root="src", dataset="simple_qa_verified_wiki_unanswered",
        description=("web_search_agent: an OpenAI-Agents-SDK agent with Wikipedia search and page tools that "
                     "answers short factual questions (SimpleQA-Verified, questions strong models fail without "
                     "tools). An LLM grader compares the final answer with a gold answer: CORRECT scores 1, "
                     "INCORRECT or NOT_ATTEMPTED score 0."),
        gold_keys=("answer",), hidden_keys=("urls",), builtin_rules=WEB_SEARCH_AGENT_RULES),
    "retail": Preset(
        name="retail", agent_subdir="vero-agents/agents/tau-bench", module="tau_bench.vero_tasks", task="retail",
        import_root=".", dataset="tau_bench_retail",
        description=("tau-bench retail: a tool-calling customer-service agent for an online retailer talks to an "
                     "LLM-simulated user and calls retail tools (orders, returns, exchanges) under a written "
                     "policy. A run scores 1 when the final database state and required outputs match the "
                     "task's goal, else 0."),
        hidden_keys=("__index_level_0__",)),
}

SAFEGUARDS = ("tokens_per_task_ratio", "answer_produced_rate")

ANALYST_PROMPT = """You are the analyst in an automated optimisation loop for an AI agent.

Target: {description}

The agent's code is in the current directory (a scratch checkout: read it, do not edit it).
The model the agent calls is fixed and must not change; everything in the writable files may.

Access rules (.veroaccess; the last matching rule wins, unmatched paths are read-only):
{rules}

Evidence: executions of the current version on training questions, one file each, in
{case_dir}
{case_list}
"failure" = the run failed or answered wrongly; "control" = the run succeeded (a change must not break these).

Changes already tried on this version (do not propose them again; labels say how they ended):
{history}
{escalation}
Task: read the code and the cases, identify recurring failure modes that a change to the agent's code,
prompts or tools could fix, and propose up to {n_cards} independent changes, most promising first.
Each change must generalise: never special-case particular questions, never embed answers, never touch
evaluation or grading.

Answer with a JSON array as the LAST fenced ```json block of your reply:
[{{"title": "<short failure-mode title, at most 80 characters>",
   "failure_mode": "<what goes wrong, citing case files>",
   "change": "<the concrete change: which file / function, what to do>",
   "predicate": {{"pattern": "<Python regex searched in the JSON execution trace of one run>",
                  "direction": "increase" or "decrease"}} or null}}]
The predicate is optional: a regex whose match rate over runs should move in `direction` once the
change takes effect (for example a tool name that should be called more often).
"""

IMPLEMENTER_PROMPT = """You are the implementer in an automated optimisation loop for an AI agent.

Target: {description}

The current directory is a git checkout of the agent. Your edits are collected from the working tree:
do not commit, do not switch branches. Edits outside the [write] paths below are reverted automatically.

Access rules (.veroaccess; the last matching rule wins, unmatched paths are read-only):
{rules}

Implement this change:
Title: {title}
{hypothesis}

Rules:
- Keep the change focused on this hypothesis. Do not change which model the agent calls.
- Generalise: never special-case particular questions, never embed answers or task data.
- Do not edit evaluation or grading code. You cannot run the benchmark; you may check syntax with
  `python -m py_compile <file>`.
- Finish with a 2-3 sentence summary of what you changed.
"""


def agent_command(command: str, case_dir: Path) -> str:
    """``{case_dir}`` in an agent command is the directory of the analysis cases, the only path outside
    the worktree the analyst reads (for agent CLIs that confine file access, e.g. ``--add-dir {case_dir}``)."""
    return command.replace("{case_dir}", shlex.quote(str(case_dir)))


def setting(settings: dict, key: str, default: Any) -> Any:
    value = settings.get(key)
    return default if value is None else value


class VeROAdapter(SystemAdapter):
    def __init__(self, settings: dict, state_root: str | Path, run_dir: str | Path):
        self.settings = settings
        self.preset = PRESETS[settings["preset"]]
        self.state_root, self.run_dir = Path(state_root).resolve(), Path(run_dir).resolve()
        self.repo = TargetRepo(self.state_root / "target")
        self.seed_sha = self.repo.seed()
        rules = self.repo.read_file(self.seed_sha, ".veroaccess") or self.preset.builtin_rules
        if not rules:
            raise ValueError(f"no access rules for preset {self.preset.name}")
        self.rules = AccessRules(rules)
        self.evidence = self.run_dir / "agents"
        self.agents = AgentRunner(retries=int(setting(settings, "agent_retries", 2)),
                                  retry_wait=float(setting(settings, "agent_retry_wait", 30.0)))
        self.agent_timeout = float(setting(settings, "agent_timeout", 1800))
        self.evaluator = Evaluator(
            python=settings["agent_python"], dataset_path=settings["dataset_path"], module=self.preset.module,
            task=self.preset.task, import_root=self.preset.import_root, cache_dir=self.run_dir / "evals",
            scratch_dir=self.run_dir / "scratch", eval_tree=self.repo.eval_tree,
            task_params=settings.get("task_params") or {},
            sample_concurrency=int(setting(settings, "sample_concurrency", 8)),
            sample_timeout=int(setting(settings, "sample_timeout", 300)),
            call_timeout=float(setting(settings, "call_timeout", 3600)),
            infra_retry=int(setting(settings, "infra_retry", 1)), blame=self._blame,
            extra_sys_path=tuple(settings.get("extra_sys_path") or ()))
        self._regex: dict[str, Optional[re.Pattern]] = {}
        self._lock = threading.Lock()
        self.last_analysis: Optional[dict] = None

    # -- versions ----------------------------------------------------------

    def version_for(self, cid: str) -> Version:
        meta = dict(candidate_id=cid)
        if cid == self.seed_sha:
            meta["kind"] = "seed"
        return Version(cid, metadata=meta)

    def seed(self) -> Version:
        return self.version_for(self.seed_sha)

    def _blame(self, sha: str, traceback: str) -> bool:
        """A batch crash is the candidate's fault when its traceback runs through a writable file of it."""
        if sha == self.seed_sha:
            return False
        tree = str(self.repo.worktrees / f"eval-{sha[:12]}") + "/"
        for path in re.findall(r'File "([^"]+)"', traceback):
            if path.startswith(tree) and self.rules.writable(path[len(tree):]):
                return True
        return False

    # -- evaluation --------------------------------------------------------

    def _result(self, sha: str, ids: list[str], k: int) -> EvalResult:
        scores, trajectories, costs, errors = {}, {}, {}, {}
        attempts: list[dict] = [{} for _ in range(k)]
        for t in ids:
            stubs = []
            for a in range(k):
                rec = self.evaluator.load(sha, t, a)
                if rec is None:
                    continue
                attempts[a][t] = float(rec["score"])
                stubs.append(dict(version=sha, task=t, attempt=a, path=str(self.evaluator.path(sha, t, a)),
                                  answered=bool(rec.get("answered")), est_tokens=int(rec.get("est_tokens") or 0),
                                  error=(str(rec["error"])[:300] if rec.get("error") else None)))
            if not stubs:
                continue
            values = [attempts[s["attempt"]][t] for s in stubs]
            scores[t] = sum(values) / len(values)
            trajectories[t] = stubs
            costs[t] = float(sum(s["est_tokens"] for s in stubs))
            errors[t] = next((s["error"] for s in stubs if s["error"]), None)
        return EvalResult(per_task_scores=scores, trajectories=trajectories, costs=costs, error_classes=errors,
                          attempt_scores=attempts, context=dict(version=sha))

    def _measure(self, version: Version, ids: list[str], k: int) -> EvalResult:
        self.evaluator.ensure(version.content_hash, list(ids), k)
        result = self._result(version.content_hash, list(ids), k)
        # A few unscored samples are dropped by the loop's pairing rules. An attempt with most samples
        # unscored is a service outage (grader or model down), not a measurement of this version.
        for attempt, scores in enumerate(result.attempt_scores[:k]):
            if ids and 2 * len(scores) < len(ids):
                raise EvaluationUnavailable(
                    f"VeRO evaluation of {version.content_hash[:12]}: {len(ids) - len(scores)} of {len(ids)} "
                    f"sample(s) of attempt {attempt} have no score (model or grader service failure)")
        return result

    def _both(self, version: Version, reference: Version, ids: list[str], k: int) -> tuple[EvalResult, EvalResult]:
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="vero-pair") as pool:
            a = pool.submit(self._measure, version, ids, k)
            b = pool.submit(self._measure, reference, ids, k)
            return a.result(), b.result()

    def evaluate(self, version, task_ids, k, *, purpose="screen"):
        return self._measure(version, task_ids, k)

    def evaluate_pair(self, version, reference, task_ids, k, *, purpose):
        return self._both(version, reference, task_ids, k)

    def evaluate_subset(self, version, reference, subset, task_ids, k):
        if reference is None:
            return self._measure(version, task_ids, k), None
        return self._both(version, reference, task_ids, k)

    def evaluate_search(self, version, task_ids, k):
        return self._measure(version, task_ids, k)

    # -- safeguards --------------------------------------------------------

    def observable_safeguards(self):
        return SAFEGUARDS

    def observe_safeguards(self, candidate: EvalResult, reference: EvalResult) -> dict:
        tokens: dict = {}
        produced: dict = {}
        for side, result in (("subject", candidate), ("reference", reference)):
            for t, stubs in result.trajectories.items():
                for s in stubs or []:
                    tokens.setdefault(t, dict(subject=[], reference=[]))[side].append(float(s["est_tokens"]))
                    produced.setdefault(t, dict(subject=[], reference=[]))[side].append(1.0 if s["answered"] else 0.0)
        tokens, ts, tr = paired_safeguard_means(tokens)
        produced, ps, pr = paired_safeguard_means(produced)
        return dict(
            tokens_per_task_ratio=dict(kind="ratio", direction="lower_is_better",
                                       subject=ts / tr if ts and tr else None, reference=1.0 if tr else None,
                                       tasks=tokens, estimated=True),
            answer_produced_rate=dict(kind="proportion", direction="higher_is_better", subject=ps, reference=pr,
                                      tasks=produced))

    # -- predicates --------------------------------------------------------

    def _pattern(self, spec: Any) -> Optional[re.Pattern]:
        if not isinstance(spec, dict) or spec.get("kind") != "trace_regex":
            return None
        pattern = spec.get("pattern")
        with self._lock:
            if pattern not in self._regex:
                try:
                    self._regex[pattern] = re.compile(pattern)
                except (re.error, TypeError):
                    self._regex[pattern] = None
            return self._regex[pattern]

    def features(self, trajectories, predicate):
        if predicate is None:
            return {}
        rx = self._pattern(predicate.boolean_expression)
        out: dict[str, Optional[bool]] = {}
        for t, stubs in trajectories.items():
            if rx is None or not stubs or not Path(stubs[-1]["path"]).exists():
                out[t] = None
                continue
            rec = json.loads(Path(stubs[-1]["path"]).read_text())
            text = json.dumps(rec.get("execution_trace"), ensure_ascii=False, default=str)[:2_000_000]
            out[t] = rx.search(text) is not None
        return out

    # -- analyst -----------------------------------------------------------

    def _case_text(self, i: int, role: str, stub: dict) -> str:
        rec = json.loads(Path(stub["path"]).read_text())
        row = dict(rec.get("input") or {})
        hidden = set(self.preset.hidden_keys) | set(self.preset.gold_keys)
        lines = [f"# Case {i:02d} ({role})", "", "## Input", "```json",
                 json.dumps({k: v for k, v in row.items() if k not in hidden}, ensure_ascii=False, indent=1), "```"]
        gold = {k: row[k] for k in self.preset.gold_keys if k in row}
        if gold and setting(self.settings, "show_gold", True):
            lines += ["", "## Reference answer (training data)", json.dumps(gold, ensure_ascii=False)]
        if rec.get("feedback"):
            lines += ["", "## Grader verdict", str(rec["feedback"])[:2000]]
        lines += ["", "## Final output", json.dumps(shorten(rec.get("output"), 3000), ensure_ascii=False)[:6000]]
        if rec.get("error"):
            lines += ["", "## Error", str(rec["error"])[:2000]]
        limit = int(setting(self.settings, "analyst_string_limit", 1500))
        trace = shorten(parse_json_strings(rec.get("execution_trace")), limit)
        text = json.dumps(trace, ensure_ascii=False, indent=1)
        budget = int(setting(self.settings, "analyst_case_chars", 30000))
        if len(text) > budget:
            text = text[:budget // 2] + f"\n…[{len(text) - budget} chars cut]…\n" + text[-budget // 2:]
        lines += ["", "## Execution trace", "```json", text, "```", ""]
        return "\n".join(lines)

    def analyse(self, search_set_trajectories, n_cards, *, case_roles=None, parent=None, round_id=None,
                history=None, no_adoption_rounds=0, **kwargs):
        parent = parent or self.seed()
        tag = f"r{round_id or 0:03d}-{parent.content_hash[:12]}"
        folder = self.evidence / f"analysis-{tag}"
        cards_file = folder / "cards.json"
        if cards_file.exists():          # restart-stable: a finished analysis of this (round, parent) is reused
            raw = json.loads(cards_file.read_text())
        else:
            case_dir = folder / "cases"
            case_dir.mkdir(parents=True, exist_ok=True)
            listing = []
            roles = case_roles or {}
            for i, t in enumerate(sorted(search_set_trajectories, key=lambda t: (roles.get(t) != "failure", t)), 1):
                stubs = search_set_trajectories[t]
                if not stubs:
                    continue
                role = roles.get(t, "case")
                name = f"case-{i:02d}-{role}.md"
                (case_dir / name).write_text(self._case_text(i, role, stubs[-1]))
                listing.append(f"- {name}")
            hist = "\n".join(f"- {h.get('title')}: {h.get('label')}" + (f" (attribution: {h['attribution']})"
                                                                         if h.get("attribution") else "")
                             for h in history or []) or "- (none)"
            escalation = ("" if not no_adoption_rounds else
                          f"\nThe last {no_adoption_rounds} round(s) adopted nothing: look for different failure "
                          "modes or different components than the ones tried above.\n")
            prompt = ANALYST_PROMPT.format(description=self.preset.description, rules=self.rules.describe(),
                                           case_dir=case_dir, case_list="\n".join(listing), history=hist,
                                           escalation=escalation, n_cards=n_cards)
            view = self.repo.new_workspace(parent.content_hash, f"analyst-{tag}-{uuid.uuid4().hex[:6]}", self.rules)
            try:
                answer = self.agents.run(agent_command(self.settings["analyst_cmd"], case_dir), prompt, cwd=view,
                                         timeout=self.agent_timeout, log_path=folder / "analyst.log")
            except AgentCommandFailed as exc:
                # The agent service is down, not the analysis: the loop halts (resumable) instead of crashing.
                raise AgentRequestFailed(f"analyst: {exc}") from exc
            finally:
                self.repo.remove_worktree(view)
            raw = last_json(answer)
            if isinstance(raw, dict):
                raw = raw.get("cards") or [raw]
            folder.mkdir(parents=True, exist_ok=True)
            cards_file.write_text(json.dumps(raw, ensure_ascii=False, indent=1))
        cards = []
        for i, item in enumerate(raw if isinstance(raw, list) else [], 1):
            if not isinstance(item, dict) or not item.get("title"):
                continue
            hypothesis = (f"Failure mode: {item.get('failure_mode', '').strip()}\n\n"
                          f"Change: {item.get('change', '').strip()}")
            pred = None
            spec = item.get("predicate")
            if isinstance(spec, dict) and spec.get("direction") in ("increase", "decrease"):
                expr = dict(kind="trace_regex", pattern=str(spec.get("pattern") or ""))
                if expr["pattern"] and self._pattern(expr) is not None:
                    pred = Predicate(boolean_expression=expr, predicted_direction=spec["direction"])
            ref = f"{tag}-c{i:02d}"
            cards.append(Card(failure_mode_desc=str(item["title"])[:200], change_hypothesis=hypothesis,
                              predicate=pred, ref=ref))
            if len(cards) >= n_cards:
                break
        self.last_analysis = dict(cases=len(search_set_trajectories), cards=len(cards), evidence_path=str(folder))
        return cards

    # -- implementer -------------------------------------------------------

    def workspace(self, version, card=None):
        ref = re.sub(r"[^A-Za-z0-9_.-]", "_", (card.ref if card else "card") or "card")
        name = f"impl-{ref}-{uuid.uuid4().hex[:6]}"
        path = self.repo.new_workspace(version.content_hash, name, self.rules)
        return dict(path=str(path), parent=version.content_hash, card=card.ref if card else None, name=name)

    def implement(self, card, workspace):
        path = Path(workspace["path"])
        prompt = IMPLEMENTER_PROMPT.format(description=self.preset.description, rules=self.rules.describe(),
                                           title=card.failure_mode_desc, hypothesis=card.change_hypothesis)
        log_path = self.evidence / "implement" / f"{workspace['name']}.log"
        try:
            # One command serves both roles: {case_dir} is the card's analysis cases (training cases only).
            cases = self.evidence / f"analysis-{(card.ref or '').rpartition('-c')[0]}" / "cases"
            answer = self.agents.run(agent_command(self.settings["implementer_cmd"], cases), prompt, cwd=path,
                                     timeout=self.agent_timeout, log_path=log_path)
            diff, reverted = self.repo.stage_changes(path, workspace["parent"], self.rules)
        except AgentCommandFailed as exc:
            # Not an implementation failure: the card stays unmeasured (run_failed) and is not consumed.
            self.repo.remove_worktree(path)
            raise EvaluationUnavailable(f"implementer agent unavailable: {exc}") from exc
        except BaseException:
            self.repo.remove_worktree(path)
            raise
        if not diff.strip():
            self.repo.remove_worktree(path)
        return Diff(content=diff, metadata=dict(workspace=str(path), parent=workspace["parent"], ref=card.ref,
                                                title=card.failure_mode_desc, reverted=reverted,
                                                summary=answer.strip()[-1500:], log=str(log_path)))

    def materialize(self, diff, parent):
        path = Path(diff.metadata["workspace"])
        try:
            sha = self.repo.commit_workspace(path, f"{diff.metadata.get('title') or 'change'}\n\ncard {diff.metadata.get('ref')}")
        finally:
            self.repo.remove_worktree(path)
        return Version(sha, metadata=dict(candidate_id=sha, parent=parent.content_hash, card=diff.metadata.get("ref")))

    def rebase(self, candidate, new_parent):
        sha = self.repo.cherry_pick(candidate.content_hash, new_parent.content_hash,
                                    f"rebase-{candidate.content_hash[:8]}-{uuid.uuid4().hex[:6]}")
        if sha is None:
            return None
        return Version(sha, metadata=dict(candidate_id=sha, parent=new_parent.content_hash,
                                          rebased_from=candidate.content_hash))

    # -- runtime -----------------------------------------------------------

    def configure_runtime(self, *, remaining: Callable[[], float], max_concurrent_executions: int) -> None:
        self.evaluator.set_parallel(max_concurrent_executions)

    def check_runtime(self) -> None:
        self.evaluator.check()

    def cancel_pending(self) -> None:
        self.evaluator.cancel()
        self.agents.cancel()

    def stop_polling(self) -> None:
        self.cancel_pending()

    def task_families(self, task_ids):
        return {}


def split_ids(sizes: dict[str, int]) -> dict[str, list[str]]:
    from .evaluation import task_id
    return {split: [task_id(split, i) for i in range(n)] for split, n in sizes.items()}


def check_ids(ids: list[str]) -> None:
    for t in ids:
        parse_task_id(t)


def digest_settings(settings: dict) -> str:
    return hashlib.sha256(json.dumps(settings, sort_keys=True).encode()).hexdigest()
