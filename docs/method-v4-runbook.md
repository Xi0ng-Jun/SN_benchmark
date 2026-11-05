# method-v4 运行手册与实现记录

分支 `feat/method-v4`（从 `method-v3-clean` 拉出）。入口：`python -m rag_eval.optimizer.cli v4 <run|status|test|figure>`，源码在 `src/rag_eval/optimizer/v4/`。
方法文档：`harness-opt/docs/method-v4.md`（草稿 v1）；绑定方案：`harness-opt/docs/method-v4-sn-binding-plan.md`。
分工不变：本地改代码、测试、推送；服务器 Agent 只拉代码、部署、运行、汇报。

---

## 1. 一次运行的流程

1. **校准**（§2.3）：种子在搜索集、种子小批、种子其余部分上各跑 k = `calibration_k`（= 迭代的 `k_base`，必须为 2）。由验证集上的 A/A 得出：重跑方差、任务方差、恒定题比例、`k_val`（重跑方差 > 任务方差取 2，否则 1）、SE 下限。打印可行性卡片：各阶段耗时；按准备缓存命中/未命中分出冷、热两列；预算内候选数和轮数；测试集 95% 半宽。卡片只提示，不阻断。
2. **每一轮**（父版本固定）：
   - 搜索集诊断（父版本切换后在后台预取）→ 分析 Agent 出 2b 张卡；
   - b 个实现并行，空槽立即补位；
   - **阶段 0 冒烟**：搜索集 3 题，k=1，父子配对。先查能否跑通，再查行为预期，未过给一次修复；
   - **阶段 1 小批**：这个父版本自己的小批，k=1，与父版本缓存配对。Δ_mb < −1·SE_mb 或保护指标越界即淘汰；
   - **阶段 2 全验证集**：只补测其余部分，k=k_val，与小批结果合并算 Δ_val；
   - **阶段 3 机制检查**（只在 Δ_val > 0 时做）：机械行为检查加盲审；
   - **轮末决定**：合格候选里取 Δ_val 最大的（差距小于 SE/2 时取 diff 小的），前两名尝试合并（rebase）；合并候选更好就采纳合并候选。
3. **停机**（§3.5）：
   - 时间用完，或剩余时间不够平均一轮；
   - 连续 R 轮无采纳；
   - 达到 `max_rounds`；
   - 连续冒烟运行故障（`process_failure`），或预算用尽（`budget_exhausted`）。后两种可以续跑。
4. **消融剪枝**（§4.2，停机后、终点前，在验证集上做）：逐个去掉谱系节点，后面的改动重新 rebase 叠上。去掉后损失小于 `ablation_kill`·SE 的改动剪除。剪除后的版本再测一次，不变差才成为终点的最终版本。时间不够自动跳过，原因写入报告。
5. **终点**（§4.1，人工确认）：`v4 test` 预览节点集；`v4 test --confirm --budget F` 在测试集（迭代登记的 sealed 选择）上评测一次，得出 Δ_test、按论文 bootstrap 的 95% 区间、缩水量和部署结论；`v4 figure` 画曲线。

## 2. 实现与方法文档的差异（也写在 `ledger.json` 的 `deviations` 里）

- 划分沿用 v2：搜索/验证按 60/40 切，比例硬编码；测试集就是迭代登记的 sealed 选择，不是方法文档默认的 40/30/30。
- 机械检查的 A/A 差距、审查者的对照对，都用**种子**的校准 A/A，不用当前父版本的 A/A（被采纳的父版本在小批上只跑过一次）。
- 消融按谱系节点逐个去掉；合并节点（两张卡）作为一个整体去掉。
- 回传给分析 Agent 的只有机制档位，没有审查者的文字理由。

方法里写了、已经实现的部分：
- 小批按父版本重新抽取（抽样种子由父版本哈希决定），按论文分层；
- SE 按论文聚类（CR1），并取 A/A 下限；
- 终点区间用按论文的 bootstrap；
- 消融剪枝；
- 父版本切换后后台预取搜索集；
- 可行性卡片分冷、热缓存两列。

## 3. 配置文件（预注册，运行中只读）

一个 JSON。未知键一律拒绝。首次 `run` 把原文副本（`v4-config.json`）、sha256 和解析后的全部取值写入 run 目录的 `cli-run.json`，之后字节有任何变化都拒绝续跑。方法常数改了默认值的，报告里列为"方法变体"。

| 键 | 默认 | 说明 |
|---|---|---|
| `time_budget_hours` | **必填** | 优化部分的墙钟预算，不含终点 |
| `min_meaningful_improvement` | null | 只用于可行性卡片对照 |
| `b` / `cards_per_analysis` | 4 / 2b | 并行实现数 / 每次分析的卡片数 |
| `smoke_size` / `smoke_repair_attempts` / `smoke_behaviour_check` | 3 / 1 / true | |
| `minibatch_fraction` / `minibatch_min` / `minibatch_kill` | 0.25 / 10 / 1 | |
| `c` / `c_mech` | 1 / 0.5 | 机制未确认 / 已确认时的采纳门槛系数 |
| `review` / `review_pairs` / `review_controls` / `review_min_effect` / `review_max_control_effect` | true / 6 / 2 / 4 / 1 | |
| `no_adoption_rounds` / `max_rounds` / `round_timebox_hours` | 3 / 50 / null | |
| `merge` / `tie_fraction` | true / 0.5 | |
| `ablation` / `ablation_kill` | true / 1 | |
| `calibration_k` / `k_val` | 2 / null（由校准决定） | |
| `safeguard_tolerances` | 基础设施失败率 0.05，每题成本 0.5 | 其他保护指标用迭代协议里登记的 margin |
| `unobserved_safeguard_blocks` | true | 保护指标未观测到时不采纳（不会因此在小批阶段淘汰） |
| `endpoint_nodes` | `seed_final` | 或 `lineage`：测谱系所有节点 |
| `analyst_failure_threshold` / `analyst_max_failures` / `analyst_n_controls` | 0.5 / 12 / 3 | |
| `agent_estimate_minutes` | 45 | 可行性卡片里的 Agent 时间 |
| `adapter.*` | 见 `v4/config.py` 的 `ADAPTER_DEFAULTS` | 分析/实现/审查 Agent 的 profile（审查者默认用分析 Agent 的 profile）、轮询间隔、超时、证据预算、`infra_retry`、`execution_timeout_seconds` |

## 4. 对迭代的要求（`v4 run` 启动前检查）

- `k_base` = `calibration_k` = 2；
- 迭代协议里登记了保护指标（lcd-v2）；
- 验证集至少 2 题（方法要求至少 40 题，不足时可行性卡片会警告）；
- sealed 选择存在，并且从未被声明过：本迭代和其他任何迭代都没有对同一组 (bundle, sealed 题目) 声明过。不满足就拒绝启动；演练用 `--no-test-set`；
- `fresh_batch_count` 建议为 0，v4 不使用新鲜批次。

## 5. 命令

```bash
V4="$PY -m rag_eval.optimizer.cli v4"
COMMON="--config $CFG --state $STATE --iteration $IT --actor $ACTOR"
$V4 run    $COMMON --run-id $RUN --state-dir var/method-v4/$RUN --v4-config run.json   # 启动或续跑；--retry-failed；--no-test-set
$V4 status --state-dir var/method-v4/$RUN
$V4 test   $COMMON --state-dir var/method-v4/$RUN                                     # 只预览，不声明测试集
$V4 test   $COMMON --state-dir var/method-v4/$RUN --confirm --budget budget.json      # 需用户同意；只能做一次
$V4 figure --state-dir var/method-v4/$RUN --out curve.svg
```

退出码：0 成功；2 参数或校验失败，或 `test` 未加 `--confirm`；3 停在 `process_failure` / `budget_exhausted`；1 意外错误。

## 6. run 目录

| 文件 | 内容 |
|---|---|
| `cli-run.json` | 运行身份，`method: "v4"`，配置哈希和解析后取值 |
| `state.json` | 完整状态：划分、论文映射、校准、谱系、轮次、每个候选的记录、历史标签、消融、时间。续跑从这里恢复 |
| `ledger.json` | 账本：配置、方法变体、偏离、校准与可行性卡片、所有轮次、谱系、消融、时间账（含按冷/热缓存拆分的阶段耗时）、停机原因 |
| `endpoint.json` | 终点报告（只在 `test --confirm` 之后才有） |
| `evidence/` | 分析 Agent 的证据文件；审查者的证据文件（`review-*.md`）和判定文件（`*.verdict.json`）；冒烟修复的反馈文件 |
| `run.lock` | 运行锁：被锁住就说明进程还活着 |

## 7. 工作台

绑定了 v4 运行的迭代，在工作台上打开的是 v4 页面（`apps/sn-workbench/src/auto/V4RunPage.tsx`），数据来自 `GET /api/iterations/{id}/auto-run`，由 `optimizer/v4/run_view.py` 投影。页面内容：

- 状态和下一步；
- 进度：时间、轮数、采纳数、划分大小、流程故障计数；
- 终点；
- 校准与可行性卡片；
- 谱系（标出被剪除的节点）；
- 每一轮：每个候选到达的阶段、Δ、机制档位、标签，轮末决定，合并结果；
- 消融。

右栏点开一个候选，可以看到：级联各阶段的数字、SE 的来源、采纳门槛、机械检查、审查者每对的判定（标出对照对）、保护指标、耗时与缓存命中、相关 Agent，以及 diff。

页面只读，不提供任何写操作，手动操作仍被 `auto_runs` 锁住。审查者 Agent 在会话列表里显示为"审查"。

## 8. 测试

```bash
PYTHONPATH=src:tests:. ../../.venv/bin/python -m pytest tests/optimizer/ -n 4
cd apps/sn-workbench && npx tsc --noEmit
# 浏览器：先启动 fixture 服务器，再跑浏览器测试（需要能运行的 Chromium）
PYTHONPATH=src:. python apps/sn-workbench/tests/auto_run_fixture.py --port 8097 --root /tmp/auto-fixture
WORKBENCH_TEST_URL=http://127.0.0.1:8097 node tests/auto-run-browser.mjs
```

- `test_v4_loop.py`：在确定性 mock 上测循环，覆盖级联、合并、两档门槛、对照失败、刷分否决、修复、保护指标、kill 后续跑、停机、按父版本抽小批、聚类 SE、消融。
- `test_v4_rehearsal.py`：在真实 v2 服务上跑完整流程，用合成分数，覆盖采纳、盲审、测试集只评一次、刷分否决、阶段 2 中 kill 后续跑、工作台接口。
- `test_v4_units.py`：测配置、判定文件解析、终点报告、节点集和图。
