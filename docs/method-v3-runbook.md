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
| 其余 `ProtocolConfig` 字段 | 否 | `z_screen`, `z_confirm`, `b_cards`, `k_screen`（必须等于迭代的 `k_base`，否则启动前拒绝）, `safeguard_bounds`, `max_confirmations`, `predicate_coverage_floor`, `analyst_failure_threshold`, `analyst_max_failures`, `analyst_n_controls`, `analyst_cards`, `max_consecutive_run_failures`, `attribution_n_min`; `k_smoke` 只能是 1；不接受 `n_smoke_sample`（用 `smoke_sample_size`） |
| 适配器设置 | 否 | `analyst_profile`, `implementer_profile`（默认 `pi-flash`）, `agent_poll_interval`(30), `agent_timeout`(5400), `worktree_timeout`(600), `analyst_view_budget`(12000, ≤15000；上限，实际预算再压到 v2 消息限制内：正文 ≤8 KiB UTF-8，正文+分析 Agent 工作说明 ≤16384 UTF-16 单位), `eval_poll_interval`(30), `eval_timeout`(7200), `infra_retry`(1), `execution_timeout_seconds`(7200) |

## 第 1 级：只测 analyse()

```bash
RUN=v3-analyse-$(date +%Y%m%d%H%M%S)          # 每次新 run id：同一 id 重放回执，不会产出新卡
$V3 analyse-only $COMMON --run-id $RUN --state-dir var/method-v3/$RUN --n-cards 4 \
  2>&1 | tee var/method-v3/$RUN.log
```

做什么：评测根候选在搜索集上（purpose=diagnose，k=k_base；v2 measure 同时覆盖搜索集 ∪ 验证池，已有足量尝试则只读回执）→ 固定规则选案例 → 派发一次分析 Agent（round_id=0）→ 读回卡片。不派生、不实现、不冻结，不碰新鲜批次和封存集。可加 `--protocol FILE` 用预注册的分析者设置。

检查：
- `last_analysis:` 行：`selected`、`with_view`（有视图的案例数，应接近 selected）、`shown`/`omitted`（字符预算内展示了几个）、`evidence_chars`、`budget`（实际字符预算，≤ analyst_view_budget，按 v2 消息限制压缩）、`message_bytes`（≤ 8192）。中文 SN 行下通常只能展示几个案例。
- `var/method-v3/$RUN/analyse-only-evidence.txt`：发给分析者的全文。确认没有 gold/参考答案、分数、验证集或新鲜批次题目；案例用 c1、c2… 不透明编号。
- `cards: N` 及每张卡：标题、`predicate: yes/none`、谓词读取的字段名（应是行为字段或 `trace.*`）。

汇报：完整终端输出；evidence 文件前 3000 字符和总长度；耗时；Agent 失败时的错误。

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

检查：`halt reason`（分类停机原因）；`ledger.json` 三部分；`status` 中确认节点、指针、验证池大小、m、已用新鲜批次；`sealed-curve.json` 的 `status: finished`、每个节点的 `state/style`、`delta_vs_seed`、校正后区间。

汇报：`status` 输出；`ledger.json`；`rounds.jsonl`；`sealed-curve.json`；总耗时与每轮耗时；所有退出码。

## 工作台

`run` 会把运行绑定到迭代，绑定不可解除。之后这个迭代在工作台上是只读的自动运行页，手动操作和 Agent 的多数命令都会被拒绝，所以第二级试跑要用测试迭代，正式运行用新迭代。`analyse-only` 不绑定。

`run` 运行期间持有运行目录下的 `run.lock`，页面据此判断进程是否还在。进程退出后页面显示“已中断”，用同一条命令续跑。服务端和运行进程必须能访问同一个运行目录（`--state-dir`），页面才读得到状态。
