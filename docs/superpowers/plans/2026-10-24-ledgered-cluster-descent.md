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

- [ ] 5. **簇级内环与预算内自动确认**（`node cluster-run CANDIDATE CLUSTER`，改 `action_requests.py`）。
  只在簇的 search 成员上跑候选 k_base 次（seeded），与父在同一成员上的 attempt 比较，输出簇级 Δ、ρ 与带，结果回给实现者（脱敏：逐题只给 case 序号与翻转方向，不给题目）。实现者可请求；预算策略：iteration 协议新增 `budget: {limit_cny, max_runs_per_candidate}`，请求在额度内自动 confirmed 并执行，额度外挂起。
  测试：请求只接受该实现者自己的候选；执行只跑成员题；额度内自动执行、额度外挂起；预算账本累计。

- [ ] 6. **三笔账**（`ledger.py`，verb `iteration ledger`，UI 一个面板）。
  sampling：k = 1、verifier none、种子 search 均值、S(B) = 种子。overfit：n、n_eff、K（在 validation 上比较过的不同 frozen_commit 数）、期望偏差 = η·sqrt(2 ln K)、每个被采纳候选的 validation Δ 与 fresh Δ、配对观测偏差。structure：各簇状态（resolved / open）、每笔合同的 ρ 与 verdict、audit 数、愿望清单（无 accept 候选的簇，附 q/m/v）。`sealed_score` 只在人显式调用 `iteration sealed-run` 时填，且 UI 提示"仅一次"。
  测试：从合成的比较记录与簇算出的账本键齐全、数字可手算；sealed 未触发时为 null。

- [ ] 8. **闭环编排**（`iteration round`，`loop.py`）。
  一条命令跑一轮：确保当前基准有 k_base 次 attempt → 记账人重扫全部簇、按 |C|·(1−q)·max(m, v, π) 排序取前 b 个未解决簇 → 每簇派生一个候选并创建实现者 Agent（带簇证据）→ 实现者的 freeze / cluster-run / run 在预算内自动执行 → 门判定 → 通过 Ladder 的候选做 fresh-run 确认 → accept 的候选按 `integrated_from` 合并派生一个新候选并重测（干扰检查）→ 更新账本、愿望清单，写轮次记录。停机：新鲜批次用尽、连续两轮无 accept、预算耗尽。人只在创建迭代时批预算，之后读审计与愿望清单。
  测试：用假 Creator 与合成执行走完一轮的状态机；停机三条件；预算耗尽时已评测的记录完整。

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
