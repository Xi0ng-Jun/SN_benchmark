# 方法 v2 在工作台上的实施计划

方法规格（唯一真相来源，只读）：`/home/wabiwabi/silicon-notebook/harness-opt/docs/method-v2.md`
分支：`feat/method-v2`，基线 `perf/agent-speed` @ 825348c（R2d 实际运行的代码）
目标：下周在 SN + ALCE-ASQA 上跑出"轮次 × 性能"曲线。

## 为什么要改：R2d 暴露的闭环断点

1. `gate.py` 用逐题翻转兑现率 ρ ≥ τ 作采纳必要条件。ASQA 的 str_em 按比例给分，k=2 时单题噪声带 0.613，逐题几乎不可能兑现。R2d 四个候选零采纳，候选 A 验证集 Δ +0.118、机制已证实，却因 ρ=0.125 被判 audit。
2. `loop.py` 在 gate 判 accept 后才跑 fresh，但 fresh 的结果不参与判定；超时还会标 `accepted_unconfirmed` 并照样当父节点。新鲜批次不决定任何事。
3. 没有候选被采纳 → 父节点不前进 → 两轮都从 C0 出发 → 没有多轮累积 → 画不出曲线。
4. `splits.py:198` 朴素子串脱敏把可见 case id 改坏成 `[withheld]5`，候选 D 因此 unmeasurable。合同入口不校验 id。
5. `sealed.py` 每个 iteration 只允许一次封存集评测，而曲线需要对谱系每个节点评一次。

## 总体改法

采纳权从"逐题合同兑现"移到"独立样本上的配对净效用"；合同降级为只产标签的机制假设；新鲜批次成为唯一采纳依据；父节点随确认前进；封存集在全部版本冻结后对整条谱系评一次出曲线。

## 工作包

### M1 统计与判据（阻塞项）

`src/rag_eval/workbench/noise.py`
- 新增逐任务配对差统计量：`paired_delta(reference_attempts, subject_attempts, *, families)` → `dict(delta, se, n, k, icc, n_eff, lower, upper)`。
- 用**原始分数**，不按 0.5 二值化。每任务取 k 次均值 s(i)，d_i = s_subject(i) − s_reference(i)，Δ = mean(d_i)，SE = sd(d_i)/√n；任务分族时用族聚类稳健 SE（按族求均值再算族间方差）。
- 单侧界：`lower = Δ − z*SE`，`upper = Δ + z*SE`，z 由调用方传入。
- 保留现有 `estimate` / `difference_band` 不动（旧 iteration 的记录要能继续读）。

`src/rag_eval/workbench/gate.py`
- 新增规则 `lcd-v2`：`dict(id='lcd-v2', z_screen=0.84, z_confirm=1.65, statistic='paired_raw', safeguards='required')`。`DEFAULT_RULE` 保持 `lcd-v1` 不变，由 iteration 协议选用 v2。
- 新增 `validate_mechanism(value)`，机制假设三件套：
  - `slice_predicate`: `dict(kind='field_match'|'regex', field=..., ...)`，在**父版本轨迹**的固定 schema 上求值。禁止引用 case_id、score、gold。
  - `manipulation`: `dict(feature=..., direction='decrease'|'increase', threshold=float)`，可机械测量。
  - `slice_effect`: `dict(direction='positive', magnitude=[lo, hi] | None)`。
  - 严格解析，未知键或类型错即 `ValueError('contract invalid')`，候选在入口就判无效，不进入实现。
- 新增 `screen(comparison, rule)` → `dict(verdict='pass'|'fail'|'unmeasurable', delta, se, lower, ...)`：验证池上 `lower(z_screen) > 0` 则 pass；保护指标越界则 unmeasurable。
- 新增 `confirm(comparison, rule, *, mechanism=None)` → 三态 `holds` / `does_not_hold` / `insufficient_evidence`，外加 `unmeasurable`。判据：`lower(z_confirm) > 0` → holds；`upper(z_confirm) ≤ 0` → does_not_hold；其余 insufficient_evidence。保护指标越界优先判 unmeasurable。
- 新增 `attribute(comparison, mechanism)` → `dict(label='supported'|'refuted'|'insufficient', manipulation_rate_parent, manipulation_rate_candidate, delta_slice, delta_outside, slice_size)`。**标签不参与任何判定**，只写入记录。
- 保护指标槽位：`safeguards` 列表，每项 `dict(key, direction, margin)`。必填三项：反刷分指标（本实验用"答案语言与问题语言一致率"）、基础设施失败率、每题成本。任一越过 margin → unmeasurable。
- 保留完整算术轨迹（现有 `judge` 的风格），所有中间量进 `evidence`。

`src/rag_eval/workbench/hypotheses.py`
- 卡片 `contract` 字段按协议 `gate_rule.id` 分派：`lcd-v1` 走 `validate_contract`，`lcd-v2` 走 `validate_mechanism`。
- 合同里出现的任何 case id 必须是该候选可见的真实 id，否则拒绝（修 R2d 的 `[withheld]5`）。

测试：`tests/test_workbench_gate.py` 补 v2 用例；新增 `tests/test_workbench_paired_noise.py`。必须覆盖：按比例给分时 v1 判 reject 而 v2 判 holds 的回归用例（用 R2d 候选 A 的形状构造）。

### M2 闭环（依赖 M1）

`src/rag_eval/workbench/loop.py`
- 候选阶段改为 `derive → implement → measure(validation) → screen → confirm(fresh) → done`。
- `screen` 用验证池比较，pass 才进 confirm，fail/unmeasurable 直接 done。
- `confirm` 消费一个新鲜批次，**结果决定采纳**：`holds` → `accepted`；`does_not_hold` → `rejected`；`insufficient_evidence` → 预注册允许时再取一个批次合并分析，否则按 rejected；`unmeasurable` → `unmeasurable`。
- 删掉 `accepted_unconfirmed`：fresh 超时或批次耗尽一律不采纳，记 `unconfirmed_not_adopted` 并触发停机检查。
- 父节点推进：只有 `accepted` 才写 `state['baseline_candidate_id']`。删除 `merge` 阶段与多候选合并。
- 队列与 rebase：父节点变化后，同轮剩余候选标 `needs_rebase`，重新从父节点派生 diff；冲突则丢弃并记录。
- 新鲜批次用后并入验证池：`splits.py` 增加 `merge_used_batch_into_validation`，并让后续 screen 的验证池包含它。
- 停机原因分三类写入 ledger：`no_gain`（连续 R 个诊断周期无候选通过筛选或全部不成立）、`resources_exhausted`（fresh/预算/轮数）、`process_failure`（Agent 超时、基础设施超阈、合同无效连超）。删掉现在的 `no_accept_streak >= 2` 作为唯一停机。

`src/rag_eval/workbench/splits.py`
- 修 `:198` 的子串脱敏：改为按 id 边界精确替换（正则加词边界或按 token 列表整体匹配），禁止把 `case_5` 改成 `[withheld]5`。
- 泄漏扫描不再把任意数字子串当 case id；按注册的 id 集合精确匹配。

测试：`tests/test_workbench_loop.py`、`test_workbench_loop_integration.py` 更新；新增"fresh 不成立 → 父节点不前进"和"fresh 成立 → 父节点前进且下一候选 rebase"两个用例。

M2 已做（2026-10-31）：
- 轮次只接受 `lcd-v2` 协议（`describe` 拒绝 v1；v1 记录仍可读）。协议创建新增 `gate_rule='lcd-v2'` 与必填 `safeguards`。
- 候选阶段：`derive → [rebase] → implementation → measure → screen → queued → confirm → done`。同轮全部候选筛选完毕后，按 Δ_val 降序逐个确认；确认成立即推进父节点并把其余 `queued` 候选标 `needs_rebase`、在新父节点下派生（卡片复制到新父节点，源 diff 机械 `git apply`，冲突记 `rebase_conflict`）。
- 候选终态 `status`：`accepted` / `rejected` / `screen_failed` / `unmeasurable` / `unconfirmed_not_adopted` / `stalled` / `budget_exhausted` / `needs_rebase` / `rebase_conflict`。不再产生 `accepted_unconfirmed` 与 merge。
- 证据不足时按轮次设置 `confirm_extra_batches`（默认 1）再取一个批次合并分析，仍不足按不成立。
- 停机：invocation 记录 `stop = {category, reason, detail, no_gain_streak, process_failure_streak}`，同时写入最后一轮的 `stop` 与 `ledger.stop`；`category` ∈ `no_gain` / `resources_exhausted` / `process_failure`（人工停止为 `operator`），`stop_reason` 保留细分原因（如 `fresh_exhausted`、`rounds_exhausted`）。每轮记 `outcome` ∈ `gain` / `no_gain` / `process_failure` / `resources`；连续 2 轮 `no_gain` 或 `process_failure` 停机。
- 谱系：invocation 的 `lineage[]`（`candidate_id, parent_candidate_id, round, screen_delta, confirm_delta, attribution`）。
- 候选 decision：screen 写 `gate_verdict`（stage=screen，`fail` 记 rejected，其余 undecided）；confirm 写 `decision.confirm`（holds→adopted，does_not_hold/最终证据不足→rejected，unmeasurable→undecided）。
- 观测保护指标在比较记录上：`families`、`safeguards`（`answer_language_match`、`cost_per_task` 按父节点归一为 1）、`safeguard_observations`（原始 token 均值与计数）。用过的新鲜批次记入 `iteration_fresh_merges`。

### M3 封存集曲线（可与 M1 并行）

`src/rag_eval/workbench/sealed.py`
- 现在一个 iteration 只允许一次 claim。改为：claim 覆盖**一组**已冻结的谱系节点（种子 + 全部已确认节点），一次性原子消费。`study_key` 仍保证同一封存集只被同一研究用一次。
- 新增 `run_lineage(iteration_id, *, budget, actor, confirmed=True)`：要求 iteration 已停机、节点全部 frozen；对每个节点在封存集上跑 k_sealed 次，同任务配对。
- 输出 `curve`: 每个节点 `dict(node_index, candidate_id, score, se, delta_vs_seed, lower, upper)`，按已确认改动序号排序。
- 多重比较：按节点数做 Holm 校正，区间同时报原始与校正后。

`scripts/`：新增 `lcd-curve` 生成曲线图与 CSV（沿用 `cluster-report` 的自包含 HTML 风格，不依赖服务重启）。

测试：新增 `tests/test_workbench_sealed_lineage.py`，覆盖"同一封存集不能被二次 claim"与"曲线节点数等于谱系长度"。

### M4 UI 与记账

- 节点详情按 v2 流程重排：假设 → diff → 筛选(验证池) → 确认(新鲜批次) → 归因标签 → 保护指标。去掉 ρ/τ 展示，改为 Δ 与置信下界。
- ledger 增加三类证据分区：效用、选择（Δ_val 与 Δ_fresh 并列及缩水量）、机制（三件套 + 标签）。停机原因分类展示。

M4 已做（2026-10-31）：
- 触发入口 `iteration-sealed-lineage-run`（目标为 iteration 的种子候选，参数 `timeout_seconds`、`limit_cny`、`k`），只能由人工确认后派发到 `SealedService.run_lineage(confirmed=True)`；预算策略永不自动确认。Agent CLI：`sn-eval --task <task> iteration sealed-lineage-run <seed> [--k N] --reason ...`。
- `sealed_score` 在谱系结果存在时带 `kind='lineage'`、`k`、`nodes`、`alpha`、`m`、`correction` 与 `curve` 数组（每点 `node_index, candidate_id, status, score, se, delta_vs_seed, lower, upper, lower_adj, upper_adj, p_holm, holds_holm`）；曲线未定稿时 `curve` 为 null。

待整合阶段（与 M2 停机分类共同落地，M4 未动以免与 M2 抢同一块代码）：
- ledger 新增 `evidence` 键，三类分别报告、不相加（方法 §2.4）。暂定结构：
  - `utility`：`confirmed[]`（每笔 `candidate_id, delta_fresh, se, k_fresh, n_fresh, safeguards[], cost_change`）+ `curve` 引用 `sealed_score.curve`。
  - `selection`：`confirmations[]`（每个进入确认的候选 `candidate_id, delta_val, delta_fresh, shrinkage = delta_val − delta_fresh`）+ `screened_total` + `screen_counts` / `confirm_counts`（按判定计数，含被拒）。
  - `mechanism`：`candidates[]`（三件套、探针、`r_p, r_c`、`delta_S, delta_not_S`、`label`）+ `supported_share`（已确认改动中标签为 supported 的比例）。
  - `context`：划分大小、任务族与 ICC、基础设施失败计数、耗时与成本、预注册参数、`stop_reason`（`no_gain` / `resources_exhausted` / `process_failure`，由 M2 写入）。
- 旧四个顶层键保留不改名；v1 iteration 的 `evidence` 可为 null。

## 硬约束

- 本机 WSL 内存约 7.8 GB：**只跑定向测试**，禁止 `pytest` 全量跑测试套件（曾导致 WSL 崩溃）。
- 不改执行模型、不动数据库迁移以外的既有表结构；新表用 `CREATE TABLE IF NOT EXISTS`。
- 旧 iteration 的记录必须仍可读：v1 的 gate 规则、contract、verdict 全部保留，不做破坏性改名。
- 代码由本地开发与测试后推送；服务器只拉取部署。
