# 记账式失败簇下降（LCD）接入迭代树工作台

> 给执行开发的 agent：先读 `AGENTS.md`、`CURRENT_STATE.md`、`docs/evaluation-context.md`、`docs/evaluation-status.md`，再读 `docs/product/sn-workbench/iteration-tree-design.md` 与 `docs/experiments/2026-10-19-iteration-chain-and-comparison-smoke.md`。方法原文在 `/home/wabiwabi/silicon-notebook/harness-opt/docs/paper/draft-v0.md` §4，本文只写它落到这个工作台上要改什么。本文按工作项顺序执行，每项一个提交，提交前跑完 `RUNBOOK.md` 的 pytest 命令；不调用任何模型，不启动 SN，不碰 `var/` 里的真实运行。

**Goal:** 让迭代树上的每一个候选都带着三样东西被判定：重跑噪声下的逐题翻转、它的编辑合同兑现了多少、在从未参与选择的新鲜题上是否成立；并让失败归因从人手写的 `docs/failure-ledger.md` 变成可机械重扫的簇，簇附带上限。人仍然做最终 adopt，但门的判定作为预注册规则的事实记录在节点上。

**Architecture:** 全部改动在评测仓库（本 worktree）内，SN 源码不动。新增模块落在 `src/rag_eval/workbench/`：`noise.py`（重跑噪声与 η）、`gate.py`（合同与判定）、`splits.py`（search / validation / fresh / sealed 协议）、`clusters.py`（签名 DSL、重扫、q/m/v）、`ledger.py`（三笔账）。现有对象只加字段不改语义：假设卡加可选 `contract`，iteration 协议加 `k_base`、`splits`、`gate_rule`，比较记录加 `attempts_used` 与 `noise`。SQLite `user_version` 升到 5，迁移只加表与列。

**Tech Stack:** 现有 Python 3.13 + SQLite + pytest；UI 改动限于显示新字段（`apps/sn-workbench`），不新增交互。`scripts/sn-eval` 加 verb。

**Spec:** 下文"工作项"。

## 边界与关键假设

- **门不是自动发布。**`CURRENT_STATE.md` 写明不制造自动发布门槛。本文的 `verdict ∈ {accept, reject, audit, unmeasurable}` 是"按 iteration 创建时冻结的规则，对已有事实的判定"，与 Δ父记录同级存放，`decide` 仍由人调用。UI 把 verdict 和它引用的规则 id、阈值、数字一起显示。若用户不接受这一定位，W2 退化为只记录合同兑现率，不出 verdict。
- **信息边界不放松。**实现者仍看不到题目、gold、分数、case_id。簇交给实现者时只给描述、机械签名、成员数、q/m/v 与分析者写的机制说明；不给成员轨迹。这比 LCD 原文保守，原因是这里的泄漏扫描以 case_id 与题目文本为单位。
- **噪声来自查询改写与检索。**10-19 的噪声基线显示同源重跑逐题漂移与候选效应同量级。所以任何翻转判定都要在 k_base ≥ 2 次重跑上做，η 由重跑估计，不由单次分数。
- **只有 QASPER 本地可用。**封存集用现有的 5 篇 17 题留出（`selection.json` 的 holdout），不引入新 benchmark。新鲜批次按 paper 划分（partition = paper，整篇导入），paper 之间不重叠。
- **成本不是约束，墙钟是。**每题每次 ¥0.005 到 ¥0.045；导入与 embedding 的等待和 429 才是瓶颈。seeded run 复用根节点已导入的 notebook，新鲜批次需要新导入，一批 8 篇按已有记录估 10 到 30 分钟。
- **成功的定义。**QASPER 主指标 answer_f1 连续，簇统计里的"成功"取 `run_views.py` 已有的阅读阈值：answer_f1 ≥ 0.5。写进 iteration 协议 `success_threshold`，可按 suite 改。
- **采样账在这里是平凡的。**RAG 没有免费的终局验证器，S(B) 以 r = f 计算退化为种子本身。账本照记（k = 1，verifier none），不做 best-of-k。若某笔编辑装了验证器（例如引用存在性门控），它的上限按 harness-opt 的 gate_ceiling 算，这是 W6 的可选项。

## 对象与字段

**iteration.protocol 新增**（创建时冻结，不可改）：

```json
{"k_base": 2, "success_threshold": 0.5,
 "splits": {"search": ["qasper:…"], "validation": ["qasper:…"], "sealed_ref": "selection:20261013:holdout"},
 "fresh_batches": [{"batch_id": "f1", "plan_id": "…", "case_ids": ["…"], "status": "unused"}],
 "gate_rule": {"id": "lcd-v1", "tau": 0.5, "eta": "rerun_se", "band": "jeffreys90"}}
```

search 与 validation 来自同一个 plan selection，每个 case 带 `role`；fresh 批次各自是独立 plan（独立 selection_hash），paper 与 search / validation / sealed 都不重叠。

**hypothesis.card 新增可选字段 `contract`**：

```json
{"cluster_id": "c02", "predicted_flips": ["qasper:…", "…"], "guards": ["qasper:…"], "tau": 0.5}
```

旧 `predictions` 继续有效；有 `contract` 时门按合同判，没有时只按 `predictions` 做符号检验并标注"无噪声带"。

**clusters 表**：`iteration_id, cluster_id, description, signature_json, missing_signature_json, violation_signature_json, frozen_from_execution, proposer, frozen_at, scan_json`。`scan_json` 由记账人写：`members, n_cases, n_attempts, n_failed, q, m, v`。冻结后只能新增，不能改。

**candidate_comparisons 新增列**：`attempts_used_json`（两侧各用了哪些 attempt）、`noise_json`（η、逐题噪声带、来源 attempt 对）、`verdict_json`（规则 id、τ、合同兑现率 ρ、全局 Δ、判定与理由）。

**iteration 的 ledger**：`var/<state>/ledgers/<iteration_id>.json`，键固定：`sampling_ledger`、`overfit_ledger`、`structure_ledger`、`sealed_score`（只在人显式触发时填）。

## 工作项

- [ ] 1. **重跑噪声与 η**（`noise.py`，改 `comparisons.py`）。
  候选允许多个已接纳 attempt；比较记录对每个 case 取各侧已接纳 attempt 的主指标均值作 `left/right`，`attempts_used` 记下用了哪些。新增 `noise.estimate(candidate)`：同一候选 attempt 之间逐题差的分布，输出每题噪声带（Jeffreys 平滑的 90% 半宽，按 attempt 数）与整体 η = sqrt(mean(p(1−p)) / n_eff)，p 为逐题成功率（按 `success_threshold`），n_eff 按 paper 族内相关折算（算法同 harness-opt `ledger/overfit.py` 的 `icc_from_families` / `n_eff`，复制实现，不跨仓库 import）。attempt < k_base 时 η 照算但标 `k_below_base: true`。
  `node run` 的动作请求接受 `attempts`（1 到 4），执行为连续编号的 attempt。
  测试：两份合成执行（同候选）给出的逐题带与 η 可手算；attempt 数不足时的标记；比较记录在新增 attempt 后生成新记录而不覆盖旧记录（现有 UNIQUE 约束含 execution id，确认即可）。

- [ ] 2. **编辑合同与门**（`gate.py`，改 `hypotheses.py`、`iterations_api.py`、UI）。
  `gate.judge(comparison, noise, contract, rule)`：
  - ρ = 合同中 predicted_flips 里实际翻转（父侧失败、候选成功，按阈值与噪声带）的比例；guards 里任一题下降超噪声带记 `guard_broken`。
  - 全局 Δ = 候选均值 − 父均值（validation 角色的题；W3 之前为全部题）。
  - 判定：Δ ≥ −η 且 ρ ≥ τ 且无 guard_broken → `accept`；Δ > η 且 ρ < τ → `audit`（收益来自合同之外）；候选基础设施失败率（status ∈ {error, missing}）比父高出噪声带 → `unmeasurable`；否则 `reject`。
  - 输出含每一步的数字与规则 id。
  `check_predictions` 保留；`record_check` 在有 `contract` 时同时写 verdict。verdict 存进比较记录 `verdict_json`，`node show` 与 UI 节点卡显示。`decide` 不变。
  测试：四种判定各一例，含"均分涨但合同未兑现 → audit"与"实现者改了准备代码导致 missing 增多 → unmeasurable"；无 contract 的旧卡走旧路径且输出标注。

- [ ] 3. **任务划分协议**（`splits.py`，改 `iterations.py`、`evaluation_plans.py`、`agent_tools.py`、`run_views.py`）。
  - 创建 iteration 时 selection 的每个 case 带 `role ∈ {search, validation}`；默认按 paper 用固定种子分 60/40。`sealed_ref` 指向现有留出 selection，任何 verb 都不接受 sealed 的 case_id。
  - `fresh_batches`：创建时可指定 N 批、每批 M 篇，从 bundle 里不与 search / validation / sealed 重叠的 paper 中按种子不放回抽取，各自生成 plan 并冻结。状态 `unused → used(by candidate, at)`。
  - 分析 Agent 的 `cases / case / execution-cases / flips / compare` 只返回 role = search 的题；validation 的题对分析者只给汇总（n、均值、η），不给逐题。实现者不变。
  - 新动作 `node fresh-run CANDIDATE`：取下一个 unused 批次，对候选与其父各跑 k_base 次（父若已在该批次有 attempt 则复用），生成该批次上的比较记录（kind = `fresh`），标记批次 used，并把该批次的 case 以 role = validation 并入 iteration（后续候选的 validation 池扩大；父节点在新池上缺的 attempt 由下一次比较时补跑，记录在 `attempts_used`）。只能由分析者请求、人确认。
  测试：划分不重叠且可复现；sealed 拒绝；分析者看不到 validation 逐题；fresh-run 用后批次不可再用；并入后 validation 池变大。

- [ ] 4. **失败簇与记账人**（`clusters.py`，加 verb）。
  签名 DSL（JSON）：`{"kind":"regex","field":"<path>","pattern":…}`、`{"kind":"range","field":"<path>","min":…,"max":…}`、`all_of / any_of / not`。字段路径限定在 case view 的白名单：`status`、`prediction`、`response.answer`、`response.grounded`、`response.evidence_level`、`intent_preview.needs_clarification`、`deterministic.evidence_hit`、`deterministic.citation_valid_count`、`deterministic.citation_invalid_count`、`stage_metrics.gold_evidence_recall.value`、`stage_metrics.context_precision.value`、`stage_metrics.citation_precision.value`、`metrics.<primary>`、`usage.total_tokens`、`retrieval_context.*.text`（只允许正则）。禁止 `question`、gold、case_id 字段进入签名（签名描述失败形态，不得点名题目）。
  `clusters.scan(iteration, cluster, executions)`：成员 = 至少一次 attempt 匹配签名的 search 题；q = 成员全部 attempt 的成功率；m = 成员失败 attempt 中匹配 missing_signature 的比例（默认 missing_signature = `gold_evidence_recall.value == 0`，即证据未进上下文）；v = 失败 attempt 中匹配 violation_signature 的比例（默认 = `deterministic.citation_invalid_count > 0` 或 `response.answer` 匹配 `(?i)unanswerable|not (stated|mentioned)` 且 `evidence_hit == true`）。上限：retrieval 类编辑 (1 − q)·m，验证/拒答类编辑 (1 − q)·v。
  verb：`cluster propose --file`（分析者；卡片内容过泄漏扫描；写入即冻结）、`cluster list`、`cluster show ID`（分析者得成员 case_id；实现者得描述、签名、计数、q/m/v，不得成员）。`proposal draft` 的卡可引用 `cluster_id`，合同的 predicted_flips 必须是该簇成员。
  测试：合成 case view 上的重扫结果可手算；字段白名单外的签名被拒；实现者视图不含 case_id；冻结后 propose 同 id 被拒。

- [ ] 5. **簇级内环**（`node cluster-run CANDIDATE CLUSTER`）。
  只在簇的 search 成员上跑候选 k_base 次（seeded），与父在同一成员上的 attempt 比较，输出簇级 Δ、ρ 与带。实现者可请求；是否自动确认见"待用户决定"。
  测试：请求只接受该实现者自己的候选；执行只跑成员题；输出结构。

- [ ] 6. **三笔账**（`ledger.py`，verb `iteration ledger`，UI 一个面板）。
  sampling：k = 1、verifier none、种子 search 均值、S(B) = 种子。overfit：n、n_eff、K（在 validation 上比较过的不同 frozen_commit 数）、期望偏差 = η·sqrt(2 ln K)、每个被采纳候选的 validation Δ 与 fresh Δ、配对观测偏差。structure：各簇状态（resolved / open）、每笔合同的 ρ 与 verdict、audit 数、愿望清单（无 accept 候选的簇，附 q/m/v）。`sealed_score` 只在人显式调用 `iteration sealed-run` 时填，且 UI 提示"仅一次"。
  测试：从合成的比较记录与簇算出的账本键齐全、数字可手算；sealed 未触发时为 null。

- [ ] 7. **第一次实验的预注册与运行**（不交给开发 agent；由研究者与用户执行）。
  QASPER chunk，qwen-flash。search 15 篇、validation 10 篇、fresh 3 批 × 8 篇、sealed = 既有 5 篇留出。C0 跑 k_base = 2；诊断一次取前 3 个簇；每簇一个实现者；门判定；两轮。预算估计 ¥40 内。预注册写 `docs/experiments/2026-10-xx-lcd-r1.md`，运行前提交。

## 待用户决定（开发可先按默认做）

1. verdict 的定位：默认"规则事实 + 人 adopt"；替代是"只记 ρ 不出 verdict"。
2. 实现者能否看簇成员的脱敏轨迹：默认不能。
3. `cluster-run` 是否在人预批的每迭代预算内自动确认：默认仍需人确认，但支持一次确认多条请求。

## 执行记录

（开发 agent 按工作项追加：日期、提交、测试结果、偏离计划之处与原因。）
