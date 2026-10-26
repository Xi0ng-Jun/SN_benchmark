# 记账式失败簇下降（LCD）接入迭代树工作台

> 给执行开发的 agent：先读 `AGENTS.md`、`CURRENT_STATE.md`、`docs/evaluation-context.md`、`docs/evaluation-status.md`，再读 `docs/product/sn-workbench/iteration-tree-design.md` 与 `docs/experiments/2026-10-19-iteration-chain-and-comparison-smoke.md`。方法原文在 `/home/wabiwabi/silicon-notebook/harness-opt/docs/paper/draft-v0.md` §4，本文只写它落到这个工作台上要改什么。本文按工作项顺序执行，每项一个提交，提交前跑完 `RUNBOOK.md` 的 pytest 命令；不调用任何模型，不启动 SN，不碰 `var/` 里的真实运行。

**Goal:** 让迭代树上的每一个候选都带着三样东西被判定：重跑噪声下的逐题翻转、它的编辑合同兑现了多少、在从未参与选择的新鲜题上是否成立；并让失败归因从人手写的 `docs/failure-ledger.md` 变成可机械重扫的簇，簇附带上限。**门的判定直接决定候选在树上的去留**（accept 成为后续派生的基准，reject / audit / unmeasurable 记录在案），人只在创建迭代时批预算、事后读审计清单与愿望清单；是否把采纳的改动合入 SN 主线是循环之外的另一件事。

> 2026-10-24 用户决定：以把目标做好为准，不以兼容现有实现为准。原稿"门只记事实、人 adopt"的保守定位据此改为上面这段；"待用户决定"三项全部按自动化一侧定，见该节。

**Architecture:** 全部改动在评测仓库（本 worktree）内，SN 源码不动。新增模块落在 `src/rag_eval/workbench/`：`noise.py`（重跑噪声与 η）、`gate.py`（合同与判定）、`splits.py`（search / validation / fresh / sealed 协议）、`clusters.py`（签名 DSL、重扫、q/m/v）、`ledger.py`（三笔账）。现有对象只加字段不改语义：假设卡加可选 `contract`，iteration 协议加 `k_base`、`splits`、`gate_rule`，比较记录加 `attempts_used` 与 `noise`。SQLite `user_version` 升到 5，迁移只加表与列。

**Tech Stack:** 现有 Python 3.13 + SQLite + pytest；UI 改动限于显示新字段（`apps/sn-workbench`），不新增交互。`scripts/sn-eval` 加 verb。

**Spec:** 下文"工作项"。

## 边界与关键假设

- **门是循环内部的接受机制，不是发布门。**`CURRENT_STATE.md` 的"不制造自动发布门槛"指对外成绩与发布；LCD 的 `verdict ∈ {accept, reject, audit, unmeasurable}` 按 iteration 创建时冻结的规则判定，并**自动写入候选的 decision**（actor = `gate`，附规则 id、阈值与全部数字）。人可以覆盖，覆盖必须带理由并保留原判定。`CURRENT_STATE.md` 相应补一句说明。
- **实现者看得到簇证据，看不到 gold。**实现者拿到：簇描述、签名、q/m/v、分析者写的机制说明，以及该簇在 search 上的成员证据（题目、SN 答案、上下文块、阶段指标、官方分），**不含 gold 答案与 gold 证据文本**。防捷径靠三层：diff 的泄漏扫描（题目文本、gold 8-gram、case_id）、合同的预测翻转必须兑现、新鲜批次确认。看不到 gold 就写不出答案硬编码；看得到题目与输出才调得了 harness。`agent_messages.py` 的上下文种类屏障按此调整：对实现者开放 `cluster_case`（脱敏视图），仍拒绝 `case / run_case / task_runs / iteration_tree / result`。
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

- [x] 1. **重跑噪声与 η**（`noise.py`，改 `comparisons.py`）。
  候选允许多个已接纳 attempt；比较记录对每个 case 取各侧已接纳 attempt 的主指标均值作 `left/right`，`attempts_used` 记下用了哪些。新增 `noise.estimate(candidate)`：同一候选 attempt 之间逐题差的分布，输出每题噪声带（Jeffreys 平滑的 90% 半宽，按 attempt 数）与整体 η = sqrt(mean(p(1−p)) / n_eff)，p 为逐题成功率（按 `success_threshold`），n_eff 按 paper 族内相关折算（算法同 harness-opt `ledger/overfit.py` 的 `icc_from_families` / `n_eff`，复制实现，不跨仓库 import）。attempt < k_base 时 η 照算但标 `k_below_base: true`。
  `node run` 的动作请求接受 `attempts`（1 到 4），执行为连续编号的 attempt。
  测试：两份合成执行（同候选）给出的逐题带与 η 可手算；attempt 数不足时的标记；比较记录在新增 attempt 后生成新记录而不覆盖旧记录（现有 UNIQUE 约束含 execution id，确认即可）。

- [x] 2. **编辑合同与门**（`gate.py`，改 `hypotheses.py`、`iterations_api.py`、UI）。
  `gate.judge(comparison, noise, contract, rule)`：
  - ρ = 合同中 predicted_flips 里实际翻转（父侧失败、候选成功，按阈值与噪声带）的比例；guards 里任一题下降超噪声带记 `guard_broken`。
  - 全局 Δ = 候选均值 − 父均值（validation 角色的题；W3 之前为全部题）。
  - 判定：Δ ≥ −η 且 ρ ≥ τ 且无 guard_broken → `accept`；Δ > η 且 ρ < τ → `audit`（收益来自合同之外）；候选基础设施失败率（status ∈ {error, missing}）比父高出噪声带 → `unmeasurable`；否则 `reject`。
  - 输出含每一步的数字与规则 id。
  `check_predictions` 保留；`record_check` 在有 `contract` 时同时写 verdict。verdict 存进比较记录 `verdict_json`，`node show` 与 UI 节点卡显示。门自动写入候选 decision（actor 为 gate）：accept → adopted，reject → rejected，audit / unmeasurable → undecided；人可用 decide 覆盖，必须带理由并保留原 verdict。
  测试：四种判定各一例，含"均分涨但合同未兑现 → audit"与"实现者改了准备代码导致 missing 增多 → unmeasurable"；无 contract 的旧卡走旧路径且输出标注。

- [x] 3. **任务划分协议**（`splits.py`，改 `iterations.py`、`evaluation_plans.py`、`agent_tools.py`、`run_views.py`）。
  - 创建 iteration 时 selection 的每个 case 带 `role ∈ {search, validation}`；默认按 paper 用固定种子分 60/40。`sealed_ref` 指向现有留出 selection，任何 verb 都不接受 sealed 的 case_id。
  - `fresh_batches`：创建时可指定 N 批、每批 M 篇，从 bundle 里不与 search / validation / sealed 重叠的 paper 中按种子不放回抽取，各自生成 plan 并冻结。状态 `unused → used(by candidate, at)`。
  - 分析 Agent 的 `cases / case / execution-cases / flips / compare` 只返回 role = search 的题；validation 的题对分析者只给汇总（n、均值、η），不给逐题。实现者不变。
  - 新动作 `node fresh-run CANDIDATE`：取下一个 unused 批次，对候选与其父各跑 k_base 次（父若已在该批次有 attempt 则复用），生成该批次上的比较记录（kind = `fresh`），标记批次 used，并把该批次的 case 以 role = validation 并入 iteration（后续候选的 validation 池扩大；父节点在新池上缺的 attempt 由下一次比较时补跑，记录在 `attempts_used`）。只能由分析者请求、人确认。
  测试：划分不重叠且可复现；sealed 拒绝；分析者看不到 validation 逐题；fresh-run 用后批次不可再用；并入后 validation 池变大。

- [x] 4. **失败簇与记账人**（`clusters.py`，加 verb）。
  签名 DSL（JSON）：`{"kind":"regex","field":"<path>","pattern":…}`、`{"kind":"range","field":"<path>","min":…,"max":…}`、`all_of / any_of / not`。字段路径限定在 case view 的白名单：`status`、`prediction`、`response.answer`、`response.grounded`、`response.evidence_level`、`intent_preview.needs_clarification`、`deterministic.evidence_hit`、`deterministic.citation_valid_count`、`deterministic.citation_invalid_count`、`stage_metrics.gold_evidence_recall.value`、`stage_metrics.context_precision.value`、`stage_metrics.citation_precision.value`、`metrics.<primary>`、`usage.total_tokens`、`retrieval_context.*.text`（只允许正则）。禁止 `question`、gold、case_id 字段进入签名（签名描述失败形态，不得点名题目）。
  `clusters.scan(iteration, cluster, executions)`：成员 = 至少一次 attempt 匹配签名的 search 题；q = 成员全部 attempt 的成功率；m = 成员失败 attempt 中匹配 missing_signature 的比例（默认 missing_signature = `gold_evidence_recall.value == 0`，即证据未进上下文）；v = 失败 attempt 中匹配 violation_signature 的比例（默认 = `deterministic.citation_invalid_count > 0` 或 `response.answer` 匹配 `(?i)unanswerable|not (stated|mentioned)` 且 `evidence_hit == true`）。上限：retrieval 类编辑 (1 − q)·m，验证/拒答类编辑 (1 − q)·v。
  verb：`cluster propose --file`（分析者；卡片内容过泄漏扫描；写入即冻结）、`cluster list`、`cluster show ID`（分析者得成员 case_id；实现者得自己簇的描述、签名、计数、q/m/v 及 search 成员脱敏证据（题目、SN 答案、上下文块、阶段指标、官方主分；用 m01 等序号，不给 case_id、gold 答案或 gold 证据文本））。`proposal draft` 的卡可引用 `cluster_id`，合同的 predicted_flips 必须是该簇成员。
  测试：合成 case view 上的重扫结果可手算；字段白名单外的签名被拒；实现者视图不含 case_id；冻结后 propose 同 id 被拒。

- [x] 5. **簇级内环与预算内自动确认**（`node cluster-run CANDIDATE CLUSTER`，改 `action_requests.py`）。
  簇运行是独立的执行范围：为该簇的 search 成员生成独立 plan selection（同 bundle、同 mode，selection_hash 独立），候选在其上跑 k_base 次（seeded），父若在这些成员上缺 attempt 则一并补足；配对准入按这个子集的 plan 做，不从整批运行里过滤行。输出簇级 Δ、ρ 与逐题带，结果回给实现者（脱敏：逐题只给成员序号 m01… 与翻转方向，不给题目与分数）。
  预算：iteration 协议新增 `budget: {limit_cny, max_runs_per_candidate}`。每次派发前按历史均价预留，派发的全部 attempt（含父补跑、fresh-run、失败与重试）都计入同一账本；额度内的请求自动 confirmed 并执行，额度外挂起等人；预留在终态结算。实现者只能请求自己候选的簇运行。
  测试：请求只接受该实现者自己的候选；执行只跑成员题并走独立 plan；额度内自动执行、额度外挂起；预留与结算累计正确；父补跑计入账本。

- [x] 6. **三笔账**（`ledger.py`，verb `iteration ledger`，UI 一个面板）。
  sampling：k = 1、verifier none、种子 search 均值、S(B) = 种子。overfit：n、n_eff、K（在 validation 池上比较过的不同 frozen_commit 数）、期望偏差 = η·sqrt(2 ln K)、每个 accept 候选的 validation Δ 与 fresh Δ 按 batch 对齐（validation 池随并入批次增长，比较时注明池的组成）、配对观测偏差。structure：各簇状态由后续证据派生（有 accept 候选且该候选在簇成员上 ρ ≥ τ → resolved，否则 open；不改冻结成员）、每笔合同的 ρ 与 verdict、audit 数、愿望清单（open 簇，附 q/m/v）。`sealed_score` 只在人显式调用 `iteration sealed-run` 时填：需要独立的可信授权（`job_preparation.sealed_selections` 登记加人工确认），领取持久化且一次性，用后任何再次调用都拒绝；UI 在调用前提示"整个研究只此一次"。
  测试：从合成的比较记录与簇算出的账本键齐全、数字可手算；sealed 未触发时为 null，触发一次后第二次被拒。

- [x] 8. **闭环编排**（`iteration round`，`loop.py`）。2026-10-26 按本机 R1-mini 的经验重写（见 `docs/experiments/2026-10-26-lcd-r1-mini.md` 的运行记录）。实验改在评测服务器上跑，模型服务由公司提供，**花费不是约束，墙钟是**：凡能并行的都并行，凡能自动的都自动，人只在创建迭代（批预算、批本轮设置）和事后读审计时出现。

  **8a 前置：准备策略冻结进协议。**`preparation_policy` 现在是服务器级 `job_execution` 设置，迭代中途改了不会被发现。把它加进 `EXECUTION_POLICY_KEYS`，创建 iteration 时冻结（缺省取服务器值并写入协议），执行时以协议为准；与服务器值不同时以协议为准并在执行记录里注明。准备缓存键已含该策略，不变。测试：冻结、覆盖、旧 iteration 无该键时的兼容。

  **8b 前置：配置检查核对解释器。**本机第一次 smoke 在初始化阶段失败（`Selected interpreter executable does not match invocation`），而 `configure_workbench_server.py check` 显示通过。让 check（不需要 `--execution`）比较 `job_execution.python` 与 `runtime_expectation` 的 interpreter executable / prefix，不一致时报错并给出两个路径。测试：一致通过、不一致报错。

  **8c 编排本体。**`iteration round ITERATION [--rounds N]` 跑一轮或多轮；每轮的步骤与要求：
  1. **基准测量**：当前基准（第 1 轮是 C0，之后是上一轮 accept 的候选或其合并）在 search ∪ validation 上补足 k_base 次准入 attempt，所有缺的 attempt 一次并行派发（准备缓存按键加锁，每题只导入一次）。启动 round 的人即视为确认了这次派发（冷启动预留超出冻结预算时走 override，记录 actor 与原因），不再单独挂起等人。
  2. **分析与提簇**：创建分析 Agent（profile 取迭代设置，缺省 `pi-flash`）。只有一个已评测候选时绑定基准候选，两个及以上时 `scope = iteration`。发给它的任务由服务器生成：读 search 题、提 ≤ `max_clusters`（缺省 6）个机械簇、为排名前 b 的簇各起草一张带 `contract` 的卡。**分析 brief 补全**提簇所需的一切（`cluster propose` 用法、签名算子与字段白名单、`missing_signature` / `violation_signature`、写入即冻结、合同格式、predicted_flips ⊆ 成员且与 guards 不重叠、排序公式），不再依赖人在消息里补。
  3. **选择**：记账人重扫全部簇，按 |C|·(1−q)·max(m, v, π) 排序（π 缺省 0.3；m、v 不可测时为 null，不当作 0，排序记录里注明退化为 |C|·(1−q)·π），取前 b 个（缺省 2）未解决且有合法卡片草稿的簇。卡片批准方式是迭代设置 `card_approval: auto | manual`，缺省 auto（泄漏扫描、合同校验照旧）；manual 时本步停在待批状态，人批准后 round 自动继续。
  4. **实现（各候选并行）**：每张卡派生一个候选、创建实现 Agent。**实现 brief 写明 LCD 顺序**：实现 → 只跑与改动相关的 SN 测试 → leak-check → `node freeze` → `node cluster-run` → 读 `cluster result` → `node run --attempts k_base`；这些请求在预算内自动执行。各候选之间不互相等待。
  5. **门**：候选在 validation 上有 k_base 次准入 attempt 后自动做合同核对，写入 verdict（W2 已有）。
  6. **新鲜确认**：accept 的候选自动 fresh-run，结果写入账本。
  7. **合并**：两个及以上 accept 时按 `integrated_from` 派生合并候选，创建一个实现 Agent 只做合并（解决同一文件 / 同一提示片段的冲突，不加新改动），跑 k_base 次；合并候选在 validation 上低于基准减 η 则撤回，否则成为下一轮基准。只有一个 accept 时它就是下一轮基准；没有 accept 时基准不变。
  8. **记账**：更新三笔账与愿望清单，写轮次记录（选了哪些簇、每个候选的各阶段时间点与结果、verdict、各步耗时）。
  停机：`--rounds` 用尽、新鲜批次用尽、连续两轮无 accept、预算耗尽。

  **8d 预算补充（2026-10-26）**：公司模型不在 `model_budget.RATES` 时，iteration 预算支持 `{max_case_runs, max_runs_per_candidate}`；`limit_cny` 可选，保留旧金额形式并支持混合额度，自动确认须满足全部上限。题次为全部预留 attempt 的 case_count 之和，父补跑、fresh、失败和 infra retry 均计入；结算不退题次。summary／账本／预算视图显示已用题次与额度，round 停机包含题次耗尽。创建 CNY iteration 或提交 CNY run 前读取实际模型配置，核对全部绑定 chat／embedding／rerank 价格；未知价格或配置不可读直接拒绝，错误指向题次形式。W8 仍只传 timeout_seconds，金额护栏留给已定价 DashScope 配置。服务器示例见 [轮次操作](../../product/sn-workbench/lcd-round-operator.md)。

  **可靠性要求**（来自本机 R1：WSL 崩溃时所有进程丢失，代理中途停住而没有任何人发现）：
  - round 的状态机持久化在 SQLite，每一步有确定的 request_key；进程或服务器重启后再次调用 `iteration round` 从上一个完成的步骤继续，已派发的执行与已创建的 Agent 不重复。
  - 编排在工作台服务内以后台任务运行，客户端断开不影响；有 HTTP 接口启动 / 查看 / 停止，CLI 是它的薄封装；进度可读（当前步骤、每个候选的状态、各步耗时）。
  - 每个 Agent 步骤有超时（可配置）。Agent 空闲却没有产出预期结果（没有冻结的簇、没有卡片、没有冻结请求、运行请求失败）时，记为该候选的 `stalled` 并写明原因，其余候选照常推进；不无限等待。
  - 题级基础设施失败（P9 的 `PreparationInfrastructureError`、执行 error / missing）按现有口径单列；迭代设置 `infra_retry`（缺省 1）允许对基础设施失败的 attempt 追加一次新的、记账的 attempt，不覆盖原记录。
  - 编排本身不调用模型；它只通过已有的服务接口派发执行与创建 Agent。

  测试：用假 Creator / 假 Agent 与合成执行走完一轮与两轮的状态机；card_approval 两种模式；单候选与多候选并行；合并与撤回；在每一步之后模拟重启并恢复（不重复派发、不重复建 Agent）；Agent 超时 / 无产出被记为 stalled 而其余候选继续；infra_retry；四个停机条件；预算耗尽时已评测的记录完整。

- [ ] 7. **第一次实验的预注册与运行**（不交给开发 agent；由研究者与用户执行）。
  QASPER chunk，qwen-flash。search 15 篇、validation 10 篇、fresh 3 批 × 8 篇、sealed = 既有 5 篇留出。C0 跑 k_base = 2；诊断一次取前 3 个簇；每簇一个实现者；门判定；两轮。预算估计 ¥40 内。预注册写 `docs/experiments/2026-10-xx-lcd-r1.md`，运行前提交。

## 已定决策

1. gate 是优化器内部接受规则，自动写入候选 decision；人工覆盖须带理由且保留原判定。
2. 实现者可看自己簇的 search 脱敏证据；cluster_case 可发送，其他 benchmark 上下文种类仍拒绝。
3. cluster-run 在预批预算内自动确认；预算策略与轮次编排属于 W5 / W8，不在本次 W1–W4 范围。

## 执行记录

（开发 agent 按工作项追加：日期、提交、测试结果、偏离计划之处与原因。）

### W1 · 2026-10-24

- 提交：本条所在 `feat(workbench): compare admitted reruns with recorded uncertainty`。
- 验证：RUNBOOK 完整离线 pytest **2430 passed / 7 skipped**（可选本地工件与 Perl ROUGE 未配置），Node **34 passed**；未调用模型、启动服务或修改虚拟环境。
- 实现：全部准入 attempt 均值、冻结 attempt 集合身份、逐题 Jeffreys 平滑 90% 正态半宽、ICC / n_eff / η、SQLite v5 加列、node run 的 1–4 次请求。
- 解释与偏离：Jeffreys 带使用 harness-opt bookkeeper 的正态近似而非 Beta 分位数；多次运行连续派发编号，不等待前次完成。干净 checkout 缺私有 E5 工件，原先无条件依赖它的测试改为合成保存运行；schema 断言随 v5 更新，CLI 子进程显式指向本 checkout 的 src，不修改共享 .venv。复用已有 Node 依赖并构建 bridge，仅补离线测试前提。
- 文档同步：按用户最新指令恢复 e36d63f 边界并修正 W2/W4 与“已定决策”的残留旧文案；同步前工作副本差异保存于 /tmp/lcd-plan-preexisting.patch。W5/W8 未实现。

### W2 · 2026-10-24

- 提交：本条所在 `feat(workbench): apply frozen LCD contracts to candidate decisions`（W1：`9a0fab5`）。
- 验证：完整离线 pytest **2437 passed / 7 skipped**；Node **34 passed**；`npm --prefix apps/sn-workbench run typecheck` 通过。
- 实现：冻结 lcd-v1 / k_base / 成功阈值；合同与四种判定；verdict 保存全部计算输入；核对时原子写入比较判定、候选 decision（actor=gate）和历史。人工覆盖必须带理由且保留原判定；重复核对同一比较不会覆盖人工意见。节点 UI 展示规则、数字与依据；CURRENT_STATE 说明内部接受规则。
- 解释与偏离：质量准入会排除基础设施失败，故另计全部终态 attempt 的 error/missing；无准入质量对也保存不可测诊断，未观测到分母时不猜测失败率。不足 k_base、缺合同测量或缺全局比较均不可测。原计划的“decide 不变”按用户新决策改为保留 gate 的带理由覆盖；旧卡仍走标注“无噪声带”的符号检验。

### W3 · 2026-10-24

- 提交：本条所在 `feat(workbench): freeze paper splits and consume fresh validation batches`（W2：`5ff5b5e`）。
- 验证：完整离线 pytest **2444 passed / 7 skipped**；Node **34 passed**；本项未改 UI。定向覆盖纸级可复现不重叠划分、role hash 到 preparation、sealed 拒绝、CLI/context 的 validation 隔离、fresh 请求恢复／耗尽、完成入池、显式补跑与旧记录保留。
- 实现：默认 paper 60/40，独立 fresh plans，候选 attempt 的 batch_id，独立领取／完成表；sealed 拒绝；分析者 search 逐题与 validation 汇总；fresh-run 动作及 HTTP 入口。gate 的全局 Δ 和 η 改取 validation。
- 解释与偏离：`case_ids` 保持字符串数组，角色用同一 selection hash 覆盖的并行 `roles` 映射；只有一篇时 search-only，gate 不可测。真实 holdout 不能读取，故要求 `job_preparation.sealed_selections` 可信登记（bundle_id 与 case_ids），未知 ref 关闭式拒绝；测试登记空合成留出集，未声称接通真实 17 题。冻结协议不回写使用状态，实际状态由附加表与 validation_pool 派生。
- 异步边界：fresh-run 先领取再派发；后续比较收拢终态、记录 fresh 比较并入池，失败也不释放批次。读取 compare 不隐式调用模型；缺扩展池测量先记录 missing_batches，经确认的 node run 显式补齐候选与父。这替代原稿“下一次比较补跑”，以保持现有只读工具与执行确认边界，执行函数仍接受非人工 actor。
- 旧测试调整：迭代 fixture 补可信合成封存登记；gate 持久化 fixture 明确 a=search、b=validation，使其在新协议下仍测试可测判定。旧卡、旧 plan 和旧比较记录继续读取。配置与行为已同步到 iteration-tree-design。

### W4 · 2026-10-24

- 提交：本条所在 `feat(workbench): freeze failure clusters and share scoped member evidence`（W3：`153f4e4`）。
- 验证：RUNBOOK 完整离线 pytest **2462 passed / 7 skipped**（可选本地工件与 Perl ROUGE 未配置；1 条既有 Starlette 弃用警告），Node **34 passed**；`npm --prefix apps/sn-workbench run typecheck` 通过。未调用模型、启动服务、读取真实运行或修改 .venv。
- 实现：签名白名单、search 并集成员、q/m/v 与编辑上限；冻结来源、成员与观测；cluster propose/list/show；卡片 cluster_id 与合同成员校验；实现者自己的簇证据投影和 cluster_case 消息归属校验。原泄漏扫描保持不变。
- 解释与偏离：组合语法明确采用 `{"all_of": [...]}`、`{"any_of": [...]}`、`{"not": {...}}`；观测随提议冻结，纯函数重扫不改旧记录。未知官方分与空分母为 null；整次执行没有产出时只派生 missing 状态，不向原记录补零。实现者视图按来源排除 gold 标注，保留实际交付上下文（即使与标注原文重合），并把文本中已知 case_id 脱敏为占位符。
- 集成修正：分析者 counts/mean/η 从可见 search 行重新计算；brief 先投影完整比较再汇总，原 brief 测试断言无需修改。发现显式 validation 补跑误将失败 attempt 计入 k_base，新增失败回归后修复为只计已准入或仍运行的 attempt；新的明确请求可补足，同一请求复用凭据，失败记录永久保留，读取仍不派发执行。
- 范围：W5–W8 未实现。后续 W5 须为簇子集建立独立 plan 与配对准入，预算计入全部派发（含父补跑、fresh 和失败）并先预留；W6 的 sealed-run 须单独可信授权及持久化一次性领取，簇 resolved/open 由后续证据派生，不改冻结成员。账本按 batch 对齐 validation/fresh，K 按不同 frozen_commit 计数。


### W5 · 2026-10-24

- 提交：本条所在 `feat(workbench): run scoped clusters within a durable iteration budget`（基线 `ea0ad59`）。
- 验证：RUNBOOK 完整离线 pytest **2486 passed / 7 skipped**，Node **34 passed**；1 条既有 Starlette 弃用警告。未改 UI，未调用模型、启动服务、读取真实运行或修改 .venv；旧测试断言未改。
- 实现：独立簇 plan／selection 与配对准入，根 notebook 的成员 seed map，父与候选补足 k_base；`node cluster-run`、异步 `cluster result`。冻结预算协议，整个请求的测量在派发前原子预留，统一涵盖父补跑、fresh、失败和新键重试；额度内 budget actor 自动执行，超额 pending，既有人确认可明确覆盖该请求。终态／取消回调与历史重放收拢费用。
- 解释与偏离：历史均价只用完整且币种明确的 CNY 费用；无历史时 ¥1／题为保守规划先验，不是报价。默认运行没有可信金额时保留预留、actual=null；取消和不确定派发亦不猜测免费。预留后的准备失败仍保留凭据和占用，执行原键可恢复；本项没有新增退款流程。迭代额度是估算账，未强制启用原 ModelBudget 的固定模型价格护栏；后者仍可显式配置。预算迭代关闭隐藏 retry_rounds，使重试必须以新 attempt 记账。
- 实现者边界：按用户最新“成员序号＋翻转方向”要求，cluster result 仅投影这两项；Δ／ρ／逐题带保存在分析者完整比较中，题目和分数仍只从既有 cluster_case 获得。这是对原稿“结果回给实现者”的收窄解释。
- 审查修复：在准备 Job 前持久绑定 request_key 的 actor 与参数，防止换 actor 绕过预算去重；fresh 指定批次领取与整组预留同事务，竞争失败保持 pending，不消耗下一批次或残留新预留。独立复审无未解决高／中风险项。表与字段均附加，SQLite 仍为 v5；W6、W8、W7 未开始。

### W6 · 2026-10-24

- 提交：本条所在 `feat(workbench): report LCD ledgers and protect once-only sealed evaluation`（基线 W5 `73dbc49`）。
- 验证：RUNBOOK 完整离线 pytest **2512 passed / 7 skipped**（581.60 秒），Node **34 passed**，UI `npm --prefix apps/sn-workbench run typecheck` 通过；1 条既有 Starlette 弃用警告。全部为合成执行／保存分数／case view；未调用模型、启动 SN 或工作台服务、读取真实 var/ 或修改 .venv。保留旧测试期望，仅增加 fresh 池组成与批次来源断言。
- 实现：`aggregate_ledger`、`LedgerStore.write`、`LedgerService.read`；`iteration ledger` 与 workspace 范围内的 ledger HTTP 接口、谱系下“三笔账”面板。比较附加冻结源码、每批 selection 身份／n／Δ／η／attempt 来源，旧比较不重写。结构账使用后续获准比较重算整个冻结簇的 ρ，包含合并候选证据；冻结成员、最初 q/m/v 和合同原文不改。
- sealed：`SealedService.run/score` 与 `iteration sealed-run` 待人工确认动作；UI 先提示“整个研究只此一次”。可信登记必须与冻结协议一致；先永久领取，再准备独立 plan 并通过同一执行预算账派发；失败／取消／崩溃不释放。普通 plan/Job/候选执行拒绝 sealed；sealed 不作候选 reference，普通任务／分区汇总与内存、磁盘准入缓存均核验访问边界。只有正式准入后的主指标、n、均值及领取元数据进入账本。SQLite 仍为 v5，只加一张领取表。
- 解释与偏离：sampling 的 k=1 取根候选首个获准 base attempt 的 search 均值。K 按确切 validation 池分别统计比较两侧不同 frozen_commit，包含基准；旧记录缺池身份时保持未知。fresh 偏差要求同批／同 selection／同冻结源码对且独立测量；同一 fresh 测量并入 validation 后标 `same_measurements`、偏差 null，不减不同组成的均值，也不伪造零偏差。簇 resolved 允许任何覆盖全部成员的后续比较，不限自己的 cluster-run；ρ 针对该记录的配对 reference，账本记录 reference 身份供审计。
- 一次性范围的保守解释：除了每 iteration 一次，同一状态库中相同 bundle＋sealed selection 也只能领取一次，改登记别名或新建迭代不能重用。人工确认继续用现有工作台请求确认路径；CLI 只登记，不提供字符串 actor 冒充人工或自动确认。确认回执重读不重新派发；新的 sealed-run 调用仍拒绝。
- 审查与回归：修复未派生合同的 UI 空身份、CLI 误套普通 validation 投影、仅部分合同题目兑现误判全簇、sealed 汇总与缓存绕过；恢复既有请求键冲突优先语义，并复制每批 attempt 来源避免合并池时共享引用污染。最后独立复审无未解决高／中风险项。
- W8 的后续需求（未实现）：持久轮次状态机与恢复／等待，基准选择、b 与 π 的定义，提议／实现者创建和门判定／fresh 的显式串接，真正的源码合并与冲突处理（integrated_from 目前只是来源记录），以及三种停机条件在仍有运行／未知费用预留时的收尾语义。W7 未开始。

### W8a · 2026-10-26

- 提交：本条所在 `fix(workbench): freeze iteration preparation policy`（起点 `99dcc67`）。
- 验证：按 RUNBOOK 的离线环境、现有解释器执行全套 pytest，依用户要求将 `-n auto` 限为 `-n 6`，**2607 passed / 7 skipped**（含并行开发中 W8c 的合成测试）；Node **34 passed**。未改 UI；未调用模型／启动服务／访问真实 var/／修改 .venv。
- 实现：创建迭代冻结显式准备策略或服务器缺省值；执行继续以冻结值覆盖服务器，首次附加 attempt 时原子记录 execution ID、两侧策略与覆盖来源。创建凭据保留原请求参数，重试不受后来服务器设置影响；旧协议缺键仍走旧行为。
- 解释与偏离：覆盖说明放在任务执行历史而非改写原协议；事件与 execution 关联且重试不重复。现有调度协议测试增加准备策略缺省值断言，因为新协议现在必须显式冻结该键。无功能范围偏离；W8b/W8c 另提交。

### W8b · 2026-10-26

- 提交：本条所在 `fix(workbench): check configured interpreter without execution mode`（W8a：`018cf5f`）。
- 验证：RUNBOOK 离线 pytest，现有解释器与 DEEPEVAL 环境、`-n 6`，**2663 passed / 7 skipped**（含未提交 W8c 合成测试）；Node **34 passed**。无 UI 改动，未操作模型、服务、真实 var/ 或 .venv。
- 实现：存在 job_execution 时，普通 check 即启动所选 Python 的纯 sys/json 身份探针，按运行器同样口径比较 executable 与 prefix；不一致列出预期／实际路径，保留虚拟环境调用 symlink。完整 `--execution` 的输出隔离等检查优先级保留。
- 解释与偏离：复审发现缺 output_root 的部分配置未按配置目录解析相对路径，因此在 load_config 的统一路径边界修复，而不在 check 重复兜底。新增相对路径／缺省／一致／两种不一致回归；现有测试无需改断言。第一次全套揭示检查顺序回归，修复后重新全套通过。


### W8c · 2026-10-26

- 提交：本条所在 `feat(workbench): orchestrate resumable LCD rounds`（W8a `018cf5f`、W8b `138eaac`）。
- 验证：最终 RUNBOOK 全套离线 pytest **2669 passed / 7 skipped**（190.20 秒；既有 Starlette 弃用警告），Node **34 passed**。依用户约束用现有 .venv、完整 DEEPEVAL 环境与 `-n 6`；未改 UI，不需要 UI typecheck。真实服务接缝使用临时 Git、合成执行／case views、假 Creator／Agent，覆盖一轮／两轮和每步重建服务；状态机另覆盖每次 SQLite 保存后的崩溃、人工／自动批卡、并行候选、合并／撤回、超时、停机与预算保留。未调用模型、创建真实 Agent、启动服务、访问真实 var/、修改私有配置或 .venv，未运行 SN 全套。
- 实现：RoundService 持久 invocation／阶段／期限／候选结果；服务 lifespan 启动后台 manager 恢复活动轮次，客户端断开不影响，按 iteration 文件锁防重。RoundOperations 复用既有 Agent 创建／消息凭据、批准／派生／gate／账本；RoundMeasurements 冻结整个派发范围、先一起预留再并行派发，普通／簇／fresh／父补跑及重试同账，重试按原 attempt 持久限次，每代独立 action request，人工确认不改变原 actor／范围。SQLite 继续 v6，只附加表，不重写旧比较或冻结簇成员。
- 接口与协议：人工 CLI 为 `scripts/sn-workbench iteration round ITERATION --rounds N`，另有 rounds／round-show／round-stop；HTTP 为 iteration rounds 的 POST／GET、round 的 GET／POST stop。创建协议新增 round_settings：三个 profile 均 pi-flash、max_clusters=6、b=2、pi=0.3、card_approval=auto、agent_timeout_seconds=3600、execution_timeout_seconds=7200、infra_retry=1；旧迭代缺设置时在 invocation 起点冻结缺省。操作说明见 [lcd-round-operator.md](../../product/sn-workbench/lcd-round-operator.md)。
- 解释与偏离：人工启动放在现有操作者 CLI，sn-eval 保持 Agent 角色边界；启动本身确认基准冷预算覆盖。m/v 部分不可测时仅对可测维度和 π 取最大值，两者未知才完全退化，null 不变零。合并使用 component=integration、无逐题预测／合同／簇的元数据卡，避免伪造 benchmark 预测；正常选择仍必须有合法簇合同。实现者已读原簇结果、自动重试后才产生新诊断时，由服务器刷新并单列原读凭据与刷新比较 ID，不伪造 Agent 已读新结果。重试重跑整份冻结 selection；发布 execution 前的源码捕获／配置异常只记失败，不猜测可安全重试。新鲜批次领取竞争失败保留原选择并报错，不自动换下一批。
- 可靠性修复：合并派生复制请求参数后再补来源 metadata，避免重放冲突；持久 actor 避免服务器身份改变后重复建 Agent；iteration 分析作用域不再同时绑定 candidate。并发 fresh 扩大验证池时，gate 使用新确定键补测新增 scope，再核对同一不可变比较。fresh 入池须等有界重试全部终态，读取旧树快照也会在就绪屏障后重读两侧 attempt。原簇请求尚在准备剩余 attempt 时不冻结重试观察清单；超预算／人工确认／崩溃不覆盖前代 action 与结果。未知远端创建／发送保守 stalled，不重放；stop 停后续派发，已启动工作与预留保留。
- 复审：三项中风险发现及修复引入的竞态已补回归、逐项复审关闭；最终无未解决高／中风险项。服务器真实模型吞吐与 Agent 行为尚未验收，不能由离线测试推断实验成功。W7 未开始。

### W8d · 2026-10-26

- [x] 公司模型无价格时的题次预算与 CNY 请求前置拒绝。
- 提交：本条所在 `feat(workbench): support case-run budgets for unpriced models`（基线 W8c `bd9fb21`）。
- 验证：最终按 RUNBOOK 使用现有 .venv、完整 DEEPEVAL 离线环境与 `-m pytest -q -rs -n 6`，**2702 passed / 7 skipped**（192.77 秒；6 条既有 Starlette 弃用警告）；Node **34 passed**；`npm --prefix apps/sn-workbench run typecheck` 通过。新增 33 项合成测试覆盖 count-only／混合额度、并发原子预留、自动确认／超额 pending、失败与重试不退题次、fresh 父子共同计数、round 停机、创建／请求／HTTP／CLI 的价格检查、不可读配置及已定价配置。未调用模型、创建真实 Agent、启动服务、访问真实 var/、改私有配置或 .venv，也未运行 SN 全套。
- 实现：预算必须含正整数 max_runs_per_candidate，以及 max_case_runs（正整数）／limit_cny（正数）至少一种；混合形式逐项满足，旧金额形式有效。case_runs_used 从已有 reservation.case_count 累加，整组预留、失败保留、重试追加、同 attempt 去重与人工覆盖沿用原账本；金额结算不改变题次。轮次检查与预留共用额度判定，达到题次总额即预算耗尽。W8 派发仍只传 timeout_seconds。
- 价格检查：model_budget.check_priced_models 只读取 TOML 的 services／bindings，按全部实际绑定的 chat／embedding／rerank 模型查 RATES，不使用展示用 model_names、不导入 SN、不读取凭据环境。通过 budgets.validate_model_budget 接到 iteration 创建、action request 登记／执行、候选运行、fresh／cluster／sealed 以及 Job spec；未知模型报名称并指向 max_case_runs，配置不可读／绑定不可解析明确拒绝，HTTP 与 Job CLI 返回可操作的 MODEL_PRICING_REJECTED。仅登记请求的 CLI 保留模型配置用于检查，但不因此开放执行。
- 展示与取舍：iteration show 附加 budget_usage，四个顶层账本键不变，在 sampling_ledger 内附加 budget_usage，三笔账面板显示题次用量／上限及已配置 CNY 用量／上限。预算视图保持只读，避免 brief 的写事务内再开写事务；首次预留前直接读取冻结协议，旧记录无需迁移。count-only 继续保存原 CNY 估算字段以保留历史语义，但它不参与额度判断；actual 未知仍为 null，不补零。无功能范围偏离。
- 既有测试调整：Notebook settings fixture 原来把 model.toml 写成不可解析的 `{}`；现在换成绑定已定价 chat／embedding／rerank 的最小 TOML，因为 CNY 前置校验必须读取真实绑定，不能再以占位文件通过。既有断言未放宽。操作文档已加入服务器 `{max_case_runs: 10000, max_runs_per_candidate: 100}` 示例及 DashScope 金额额度说明。
