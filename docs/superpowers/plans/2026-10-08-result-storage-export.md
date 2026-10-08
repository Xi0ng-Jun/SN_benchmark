# Result Storage and Export Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development for the independent projection and package tasks; keep the coupled store/index/reader integration in one worktree.

**Goal:** Remove duplicated immutable inputs and source snapshots, reduce official submissions, and package only useful result artifacts while preserving benchmark correctness.

**Architecture:** A content-addressed artifact store owns immutable bytes; a versioned partition index supports bounded run startup. A shared read context validates canonical input once per operation. Compact projections and private allowlist packages are derived from unchanged authoritative observations.

**Tech Stack:** Python 3.11+, stdlib JSON/hashlib/tarfile, pytest; existing DeepEval 4.2.2 and Node tests.

**Spec:** ../specs/2026-10-08-result-storage-export-design.md (approved in conversation on 2026-10-08).

## Global Constraints

- Only QASPER, MultiHop-RAG, ALCE, QMSum and HotpotQA.
- No local SN, generation, judge or official model inference; synthetic artifact checks are allowed.
- Preserve complete-input notebook-data-v3/notebook-request-v3, official evidence replay, isolated runtime and existing per-event fsync.
- No automatic deletion or implicit resume; no credentials/runtime database in result packages.
- Existing canonical data and original run directories remain read-only.

## Task 1 — immutable store, partition index and read context

- [x] Add tests for one-copy install, concurrent install, interrupted publication, tampering, link escape and path validation.
- [x] Add capsule/request equivalence tests using QASPER and Hotpot synthetic official-shaped data; run and observe missing-module failures.
- [x] Implement `ArtifactStore.install_files/install_bytes/resolve` and private atomic publication; IDs cover kind, identity and exact file hashes.
- [x] Implement `install_bundle`, `load_partition` and centralized run reference resolution, plus operation-local `RunReadContext`.
- [x] Connect prepare and run CLI flags, snapshots, baseline, saved-run validation, derived scoring and dashboard inputs.
- [x] Check new shared runs produce the same plans/materials/statuses and reject changed inputs; verify canonical rebuild only once per operation.

## Task 2 — compact scoring projection and export

- [x] Add five-suite full/compact prepared-input and evidence replay equivalence tests; observe failure before implementation.
- [x] Implement suite-specific projection with original-record validation before field filtering.
- [x] Reuse `RunReadContext` for export and write; preserve family/scope/attempt validation.
- [x] Replace repeated list membership and set construction with operation-local indexes; copy only the compact result at its publication boundary.
- [x] Measure synthetic 1k/5k/10k cases without any model calls.

## Task 3 — allowlist package and inventory

- [x] Test results/review role selection, private archive permission, no source mutation, missing dependencies and escaping links.
- [x] Implement inventory by artifact category with logical and allocated byte counts.
- [x] Implement relocatable review packages with deduplicated shared dependencies and a result-only CLI.
- [x] Run synthetic packaging/readback tests; preserve runtime and failed runs on server.

## Task 4 — integrate, independently review and verify

- [x] Review store, projection and package task behavior and failure cases against the spec.
- [x] Update runbook/current architecture/state/triage and server instructions with actual new interfaces.
- [x] Run Python offline suite and Node tests; record skips and server-only gaps.
- [x] Run synthetic scale checks, capture times/bytes/rebuild counts and compare equivalent old/new artifacts.
- [x] Render Birdview actual scope/checks and report completed local work; server experimentation remains separate.

## Execution ledger

- 2026-10-08: User explicitly confirmed the displayed design/map revision 2. Feature worktree at bcea0c5; previous baseline 538 Python passed/1 optional ROUGE skip and 34 Node passed.
- Ruling: No commit/push is assumed for the new implementation; user previously authorized syncing the preceding cleanup specifically. Keep the new changes reviewable locally unless asked to sync.

- 2026-10-08 完成：完整 Python 685 passed / 1 optional Perl ROUGE skip；Node 34 passed；独立 final review 162 passed。数据/源码身份、分区与请求隔离、五套 prepared/证据回放、私有迁移包和合成规模已验证。目录/capsule/publication 反例先失败再修复。
- 审查补强：canonical installer 输出的 index ID 固定到 campaign，shared run 必须显式传 --artifact-index-id；capsule hash 与 bundle 对象绑定；引用角色与 code identity 精确核对；安装/运行/导出/补评/附件/打包拒绝 store/code/input 重叠。
- 存储实际边界：index 存三个 request revision，run 保留完整 product-bundle；runtime/每条 fsync 没有优化或删除。QMSum baseline turns 仍读完整 raw。规模与服务器待验收见 ../../result-storage-export-verification-2026-10-08.md。
- Birdview：revision 2 的本任务活动已闭合；revision 3 是独立的未提交工作树实际架构快照，没有重放旧活动。HTML 与源码索引生成校验通过，浏览器因缺系统依赖未完成视觉复核；约束仅审查本次存储/导出相关规则。
- 未提交/未推送；服务器真实 SN、judge、官方模型评分及磁盘性能验收未执行。

- 后续授权（2026-10-08）：用户要求服务器 agent 同步新代码，用于已有实验离线导出。准备提交与远程同步，并新增 ../../server-upgrade-running-export-prompt.md；目标 SHA 以最终交接为准。旧 legacy 输入副本不会自动迁移，先做服务器小批量成本验证，不为升级重跑生成。
