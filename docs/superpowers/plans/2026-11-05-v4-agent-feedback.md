# V4 Agent diagnosis and feedback

User approved the preceding design: bounded product-source inspection, cheap hypothesis checks before implementation, explicit evidenced early exit, actionable feedback, precise search-case binding, and durable adopted-change context. Implement locally in the existing method-v4 worktree; preserve all runtime changes. No commits/push/deployment/real-model runs. This plan extends the approved runtime work, not its statistical acceptance rules.

## Contracts

- Analyst source inspection reads the frozen parent revision, only product source/tests, via a bounded read tool; no workspace writes, evaluator source, benchmark data, validation/test observations or credentials. This is a narrow tool boundary, not a filesystem sandbox claim.
- Cards may name opaque search references (`c1`, etc.) via optional `search_refs`; these references are checked against the exact immutable round evidence. Implementers receive only the cited rendered cases. Historical cards without refs are explicitly marked as legacy context, not silently assigned invented refs.
- Implementer writes a bounded JSON report to its coordinator-named evidence path. Shape: `decision` implement|skip; `reason` supported|refuted|already_satisfied|inconclusive; `summary`; `checks` array of {kind: source|test|probe, target, observation}. Both branches need 1–32 actual reported checks, never just an unsupported claim. An inconclusive check may justify a bounded implementation experiment, or a skip. It means the implementer reported a conclusion, not independently proved a hypothesis false.
- The report is consumed after the Agent is idle and before materialization. Skip raises `ImplementationDeclined` carrying the validated report; coordinator records `preflight_skipped` and performs no freeze/smoke/evaluation/repair for that card. Bad/missing reports are explicit failures, never fabricated skips. Reports and diff metadata survive frozen replay.
- Only pre-measurement source/local-check feedback can be copied as text into analyst history. Validation conclusions use fixed categorical summaries; never reviewer prose, raw errors, validation cases or scores.
- Adopted changes are a separate untrimmed summary of the current lineage; old failed attempts can be compressed. Speculation receives known outcomes and only genuinely unfinished cards marked in_progress; same-parent reuse is recorded as potentially missing later feedback.

## Work and acceptance

1. Source inspection and briefs: add a task/node-bound frozen-source command with bounded output and path/type restrictions; expose only the new V4 command permission. Update analyst/implementer instructions consistently (including messages), emphasizing cheap discriminating checks and allowed early exit. Test frozen-versus-dirty source, traversal/symlink/data rejection, task ownership and command gates.
2. Adapter report and search evidence: add the optional opaque refs card field, validated round mapping and per-card evidence; add implementer report parser/hooks/exception and replay. Tests cover malformed/missing/cross-candidate reports, skips before freeze, implement reports, cited-only evidence, unknown refs and immutable replay.
3. Coordinator feedback: integrate skip state/history/UI, preserve check provenance, separate adopted inventory from bounded history, add categorical mechanism outcomes and correct speculative in-progress history. Tests observe no materialization/measurement on skip, useful next-round feedback, old adopted items retained under history pressure, no numeric validation leakage, persistence/resume.
4. Integrate with synthetic workbench rehearsal and affected v3/v4 tests, frontend typecheck/build, independent review; document actual evidence and empirical limits.

## Ownership / interface review

Source task owns new workbench source module, agent_tools/auto_runs and brief text plus its tests. Adapter task owns SNAdapter hooks, SNAdapterV4 report/evidence, hypothesis optional field and adapter exception plus its tests. Coordinator task owns loop/stages/history renderer/UI and core tests. Brief-to-report interface is the JSON shape above; source-to-brief interface is `source --path <product path> [--query <literal>] [--start N --lines N]` bound by analyst --node. Source task will communicate V4 permission detection before integration. Shared sn_adapter.py: adapter task owns methods; coordinator owns only _history_line/_render_history functions after coordination.

## Progress

- Context read: current state and method-v4 runtime plan; referenced evaluation-context/status docs are absent in this worktree.
- Baseline: prior runtime/affected workbench run 711 passed, brief suite 14 passed; frontend typecheck/build passed. Browser unavailable (libnspr4.so).
- Existing filesystem access is unsandboxed; source tool restriction is not an enforcement claim about arbitrary shell access.

## Implemented and reviewed

- Source task: bounded frozen Git reader and task/node binding; V4-only source command via explicit immutable method registration. Real Git tests cover dirty trees, replacement refs, symlinks, traversal and bounded output. Old bindings default to V3 and cannot silently acquire V4 capability.
- Adapter task: candidate/card/parent-bound report, required concrete checks, immutable accepted snapshot on replay, valid skip before materialization, correct repair sibling identity. Explicit opaque search refs bind only cases actually rendered to the analyst. A staged text+mapping snapshot precedes Markdown publication so interrupted writes replay the original cases even if current inputs differ.
- Coordinator task: terminal preflight_skipped, implementation failure history, source/local report feedback distinguished from categorical validation outcomes, protected current-parent adopted inventory, actual completed statuses in speculative history. Below-threshold candidates still receive a cheap local mechanical check from already measured trajectories without invoking the reviewer.
- UI: shows the implementer-reported decision/checks, skipped label and correct candidate provenance including skipped repairs.
- Dispatch/brief consistency: source/probe can be the cheapest check; test commands only where useful. Inconclusive and infrastructure failures do not permanently ban a direction. Existing V3 prompts remain unchanged via default hooks.

Independent review identified and verified fixes for the evidence publication crash window and skipped-repair UI identity. Final review found no remaining concrete scoped issue. Local behavior is tested with synthetic Agents; actual model adherence, net time saved and quality gains remain unmeasured. Source CLI is a bounded tool, not an OS-level sandbox; it cannot identify secrets embedded in ordinary source code. Source browsing requires known file paths and uses a conservative product-directory allowlist. Protected inventory preserves adopted intended changes, not proof of every hypothesized mechanism.

Final verification: **500 passed, 4 dependency deprecation warnings, 73.22 seconds**. Log: `/tmp/v4-agent-feedback-regression.log`. The initial run had 499 passes and one old assertion that implementation errors were absent from history; its replacement requires the new categorical label and absence of raw error text. Frontend `npm run typecheck && npm run build` both passed. `git diff --check` passed. No real model/server/endpoint action taken.

Reproduction command (worktree root):

```sh
PYTHONPATH=src:. ../../.venv/bin/python -m pytest tests/optimizer/ tests/test_workbench_frozen_source.py tests/test_workbench_auto_runs.py tests/test_workbench_lcd_briefs.py tests/test_workbench_agent_tools_speed.py tests/test_workbench_roles_hypotheses.py tests/test_workbench_iteration_agents.py tests/test_workbench_loop_api.py tests/test_workbench_loop_services.py tests/test_workbench_loop_integration.py tests/test_workbench_round_protocol.py tests/test_workbench_round_reliability.py -n 4 -q --tb=short
```
