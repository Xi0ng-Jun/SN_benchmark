# method-v3 服务器运行手册（三级验证）

分支 `feat/method-v3-on-v2`。入口：`python -m rag_eval.optimizer.cli`（源码 `src/rag_eval/optimizer/cli.py`）。
分工：本地改代码并推送；服务器 Agent 只拉代码、运行、汇报，不改代码。出错时把完整输出带回来。

## 0. 前提

- 在评测仓库根目录执行，环境与启动工作台服务器时相同（同一 venv、同一批 `SYSTEM_*` 凭据、同一 agent bridge 配置）。本进程按服务器的 `load_config` + `create_app` 构造服务，自己启动并监管它请求的评测进程；工作台服务器保持运行（它负责请求派发与孤儿执行回收）。
- 长任务用 `tmux` 或 `nohup` 跑，输出 `tee` 到日志。
- 公共变量（按服务器实际值替换）：

```bash
cd <评测仓库根目录>
mkdir -p var/method-v3
export PYTHONPATH="$PWD/src"
PY=.venv/bin/python
CFG=var/sn-workbench-server/config.json      # 与 serve_workbench.sh 相同的默认值
STATE=var/sn-workbench-server/state
IT=<iteration_id>
ACTOR=<操作者名，整个 run 不能变>
V3="$PY -m rag_eval.optimizer.cli"
COMMON="--config $CFG --state $STATE --iteration $IT --actor $ACTOR"
```

- 先确认迭代协议有 `splits.search`、`splits.validation`、`fresh_batches`（非空且尚未使用）、`k_base`、`k_sealed`：

```bash
$PY -m rag_eval.workbench.cli --state $STATE iteration-show --config $CFG --iteration $IT \
  | $PY -c 'import json,sys; p=json.load(sys.stdin)["protocol"]; print({k: p.get(k) for k in ("k_base","k_sealed")}, {k: len(p["splits"][k]) for k in ("search","validation","sealed")}, [len(b["case_ids"]) for b in p.get("fresh_batches",[])])'
```

退出码：0 成功；2 参数/协议/目录校验失败（什么都没跑或在任何派发前拒绝）；3 运行停在 `process_failure` 或 `budget_exhausted`；1 意外错误（附 traceback）。

## 协议文件（预注册，运行中只读）

JSON 对象，未知键直接拒绝。首次 `run` 把文件 sha256、原文副本（`protocol.json`）和解析后的全部取值（`cli-run.json`）写入 run 目录；续跑时文件有任何字节变化都拒绝。

| 键 | 必填 | 说明 |
|---|---|---|
| `mde`, `sigma_d`, `ladder_c`, `K_max`, `T`, `n_fresh` | 是 | `ProtocolConfig` 必填字段 |
| `max_rounds`, `smoke_sample_size` | 是 | 轮数上限；冒烟样本量（规则：搜索集 id 按 sha256(case_id) 排序取前 N，存 `smoke-sample.json`） |
| 其余 `ProtocolConfig` 字段 | 否 | `z_screen`, `z_confirm`, `b_cards`, `k_screen`（必须等于迭代的 `k_base`，否则启动前拒绝）, `max_confirmations`, `predicate_coverage_floor`, `analyst_failure_threshold`, `analyst_max_failures`, `analyst_n_controls`, `analyst_cards`, `max_consecutive_run_failures`, `max_rounds_without_screen_pass`（默认 2：连续多少轮无候选过筛即停机 `no_candidates`）, `max_consecutive_rejections`（默认 2：连续多少次确认被拒即停机 `no_improvement`）, `attribution_n_min`, `smoke_repair_attempts`（默认 1，≥0，0 关闭：行为冒烟未过（谓词没按预测方向变）时给实现 Agent 的修复次数；通用冒烟失败和冒烟运行故障不修复）, `analyst_max_failures_step`（默认 0，≥0：本父节点下每多一个连续无采纳轮，失败案例上限加这么多，不超过搜索集大小）; `k_smoke` 只能是 1；不接受 `n_smoke_sample`（用 `smoke_sample_size`）；不接受非空 `safeguard_bounds`：保护指标只来自迭代协议里预注册的 v2 `safeguards`（迭代没有就拒绝 `run`） |
| 适配器设置 | 否 | `analyst_profile`, `implementer_profile`（默认 `pi-flash`）, `agent_poll_interval`(30), `agent_timeout`(5400), `worktree_timeout`(600), `analyst_view_budget`(12000, ≤60000；证据文件的字符预算，不受 v2 消息上限约束；冒烟修复的反馈文件用同一预算), `analyst_view_budget_step`(0, ≥0；本父节点下每多一个连续无采纳轮，证据预算加这么多字符，总数不超过 60000), `eval_poll_interval`(30), `eval_timeout`(7200), `infra_retry`(1), `execution_timeout_seconds`(7200) |

## 第 1 级：只测 analyse()

```bash
RUN=v3-analyse-$(date +%Y%m%d%H%M%S)          # 每次新 run id：同一 id 重放回执，不会产出新卡
$V3 analyse-only $COMMON --run-id $RUN --state-dir var/method-v3/$RUN --n-cards 4 \
  2>&1 | tee var/method-v3/$RUN.log
```

做什么：评测根候选在搜索集上（purpose=diagnose，k=k_base；v2 measure 同时覆盖搜索集 ∪ 验证池，已有足量尝试则只读回执）→ 固定规则选案例 → 派发一次分析 Agent（round_id=0）→ 读回卡片。不派生、不实现、不冻结，不碰新鲜批次和封存集。可加 `--protocol FILE` 用预注册的分析者设置。

检查：
- `last_analysis:` 行：`selected`、`with_view`（有视图的案例数，应接近 selected）、`shown`/`omitted`（预算内展示了几个）、`evidence_chars`、`budget`（= analyst_view_budget）、`message_bytes`（短消息，远小于 8192）。
- `analyst evidence:` 行：证据文件 `var/method-v3/$RUN/analyst-evidence/round-0-<父候选前12位>.md` 及其 sha256。消息只写这个绝对路径和 sha256，分析者读文件。文件只写一次（原子写）；同一 run id 重放时保留已有文件。确认没有 gold/参考答案、分数、验证集或新鲜批次题目；案例用 c1、c2… 不透明编号。
- `var/method-v3/$RUN/analyse-only-message.txt`：发出的消息正文。v3 创建的 Agent 附带的是简短的 v3 工作说明（分析：只用 `docs`、`leak-check`、`proposal draft`；实现：只用 `card`、`leak-check`），不是工作台的说明。
- `cards: N` 及每张卡：标题、`predicate: yes/none`、谓词读取的字段名（应是行为字段或 `trace.*`）。

汇报：完整终端输出；证据文件前 3000 字符、总长度和 sha256；耗时；Agent 失败时的错误。

## 第 2 级：单轮（b=2，T 很高，不触发里程碑）

协议文件示例（`k_screen` 改成迭代的 `k_base`；其余是验证用值，不是正式预注册）：

```json
{"mde": 0.05, "sigma_d": 0.15, "ladder_c": 1.0, "K_max": 20, "T": 1000000.0, "n_fresh": 30,
 "b_cards": 2, "k_screen": 2, "analyst_cards": 3, "max_rounds": 1, "smoke_sample_size": 5}
```

```bash
RUN=v3-stage2-$(date +%Y%m%d%H%M%S)
$V3 run $COMMON --run-id $RUN --state-dir var/method-v3/$RUN --protocol stage2.json \
  2>&1 | tee var/method-v3/$RUN.log
$V3 status --state-dir var/method-v3/$RUN
```

检查：
- 开头 `new run:` / `feasibility:` 行（划分大小、新鲜批次数和大小、可确认次数）。
- 结束 `halt reason: max_rounds`，`rounds: 1`，`certified nodes: 1`（只有种子）；`fresh batches used: 0/…`（里程碑没触发）。
- `var/method-v3/$RUN/rounds.jsonl` 每张卡的 `verdict`、`timings`（`implement_s`、`smoke_s`、`total_s`）：两张卡的实现时间应重叠（并行生效），整轮耗时约等于最慢的一张，而不是相加。
- 中途杀掉进程再用同一条命令续跑：应不重新派发分析，已决定的卡跳过，未完成的卡回放（v2 回执，不重复评测）。
- 停在 `process_failure`（退出码 3）：修好原因后同一命令加 `--retry-failed`（key_generation +1：measure/smoke 换新键，且每个失败槽位多给 `infra_retry` 次 v2 基础设施重试；v2 在任何键下都把失败 attempt 算作已占用槽位，只能沿重试链重跑）。新鲜批次 segment 不换键，已认领批次上的失败 segment 无法靠重试恢复。

- 冒烟修复（`smoke_repair_attempts` > 0）：`rounds.jsonl` 里同一张卡有两条记录，第一条 `attempt: 1, verdict: smoke_fail, superseded: true`，第二条 `attempt: 2, repaired: true, repair_of: <第一次的候选>`。迭代树上修复是同一父节点下的兄弟候选（`integrated_from` 指向失败的那个，卡片由 v2 重新登记为一份已批准副本），由新的实现 Agent 完成；它读的反馈文件是 `analyst-evidence/repair-<失败候选前12位>-a2.md`（消息里写路径和 sha256）。反馈文件里只有谓词取值和诊断视图，没有分数、gold 和题目 id。
- 每轮 `analysis`：`no_adoption_rounds`（本父节点下连续无采纳轮数，父指针一动就清零）、`max_failures`、`failures_new`（轮换选题新看到的失败案例数）、`adapter.budget`（本轮证据预算）。

汇报：`status` 输出；`rounds.jsonl`；每张卡的 timings；总耗时；任何异常 traceback。

注意：第 2 级在迭代树上留下真实候选，并在验证池上测过它们。正式运行最好换一个新迭代；若沿用同一迭代，要在预注册里写明。

## 第 3 级：完整运行 + 封存集

用预注册的协议文件（参数在预注册时定，不在此处定）：

```bash
RUN=v3-<名字>
$V3 run $COMMON --run-id $RUN --state-dir var/method-v3/$RUN --protocol preregistered.json \
  2>&1 | tee -a var/method-v3/$RUN.log
# 中断后：同一条命令续跑；process_failure 后：加 --retry-failed
$V3 status --state-dir var/method-v3/$RUN
```

停机后（非 `process_failure`；`process_failure` 是可续跑状态，要放弃续跑才加 `--allow-process-failure-halt`）封存集只评一次：

```bash
echo '{"timeout_seconds": 7200}' > sealed-budget.json   # 可选 "limit_cny": X 需同时 "confirmed": true
$V3 sealed $COMMON --state-dir var/method-v3/$RUN --budget sealed-budget.json     # 只打印节点集，退出码 2，不认领
$V3 sealed $COMMON --state-dir var/method-v3/$RUN --budget sealed-budget.json --confirm
```

不加 `--confirm` 时打印将评测的节点数、node_set_hash 和每个节点；人确认后再加 `--confirm`。`--k` 默认用迭代的 `k_sealed`。评测中断后同一条命令只续跑/轮询，不重复认领。

检查：`halt reason`（分类停机原因）；`ledger.json` 三部分（每次筛选的 `safeguards`、每次确认的 `safeguards_end`：迭代协议的保护指标按 v2 规则判定，越界或未观测到都判不可测。基础设施失败率是 v3 规则，与 v2 不同：某侧在该范围内所有已结束的 attempt 中，未发布逐题结果就死掉的 attempt 按其作业计划的题数全部计为失败（记录里 `counted_from_plan: true`），所以父节点的死 attempt 会放宽边界、候选自己的死 attempt 会抬高它的失败率；只有连计划都读不到时才按 v2 记为未观测）；`status` 中确认节点、指针、验证池大小、m、已用新鲜批次；`sealed-curve.json` 的 `status: finished`、每个节点的 `state/style`、`delta_vs_seed`、校正后区间。

汇报：`status` 输出；`ledger.json`；`rounds.jsonl`；`sealed-curve.json`；总耗时与每轮耗时；所有退出码。

## 工作台

`run` 会把运行绑定到迭代，绑定不可解除。之后这个迭代在工作台上是只读的自动运行页，手动操作和 Agent 的多数命令都会被拒绝，所以第二级试跑要用测试迭代，正式运行用新迭代。`analyse-only` 不绑定。

`run` 运行期间持有运行目录下的 `run.lock`，页面据此判断进程是否还在。进程退出后页面显示“已中断”，用同一条命令续跑。服务端和运行进程必须能访问同一个运行目录（`--state-dir`），页面才读得到状态。

## round × 封存集曲线图

### 工作台（自动运行页）

`cli sealed --confirm` 完成后刷新工作台，自动运行页的封存集区块会显示轮次 × 得分曲线：

- x 轴为轮次（种子 = 第 0 轮），y 轴为该轮封存集得分；
- 主路径（种子 → … → 最终指针）连实线；已确认节点为实心点，暂定为空心，已回滚为虚线轮廓并以虚线段连到父节点；
- 竖向误差棒为相对种子的配对差的多重校正区间（Bonferroni），误差棒与种子参照线的相对位置直接体现校正后是否成立；
- 同一轮多个节点横向微偏；悬停任一节点显示分数、Δ、p_holm、成立与否；点击节点在右栏显示封存集数值和候选详情；
- 曲线下方的"逐节点数值"折叠区始终包含全部节点的数字，可在不悬停的情况下核查。

投影逻辑在 `src/rag_eval/optimizer/curve_view.py`：节点的"轮"取 `rounds.jsonl`（或 `state.json`）中首个非 superseded 的 `screen_pass` 记录的 `round_id`；主路径沿 `parent_hash` 从最终指针追溯（跳过已回滚节点）；如果找不到 `screen_pass` 记录则取前序节点的最大轮次（标注 `round_inferred`）。

### 论文图（`cli curve`）

```bash
python -m rag_eval.optimizer.cli curve \
  --state-dir <运行目录> \
  --out fig.svg          # 也可以 .pdf 或 .png
```

可选参数：

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--lang zh\|en` | `zh` | 坐标轴标注语言；`zh` 需要 CJK 字体 |
| `--font <字体文件>` | 自动检测 | 指定字体文件路径（ttf/ttc） |
| `--width <英寸>` | 3.4 | 单栏宽度约 3.4 英寸 |
| `--height <英寸>` | 2.5 | |

图形编码与工作台一致（§6）：实心已确认、空心暂定、虚线已回滚，误差棒为多重校正区间，种子参照横线，图例说明三种标记和区间。

样本输出（已生成）：`harness-opt/docs/paper/figures/v3-curve-sample.svg`
