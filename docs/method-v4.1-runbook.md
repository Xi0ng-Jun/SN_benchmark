# method-v4.1 操作与验收

2026-11-06。本手册对应 `feat/method-v4.1`，取代旧 v4 手册中有关校准、停机、保护指标和工作台启动的说明。方法依据为工作区的 `harness-opt/docs/method-v4.1.md`。离线验证不代表真实模型、服务或方法效果已通过验收。

## 入口与生命周期

工作台「新建迭代 → 自动优化」登记起点、输入、题池和 mode，创建 `split_mode=deferred` 的新迭代。已有手动迭代或旧 v4 状态不能转换；状态版本为 `v4-state-4`。

1. 填写优化时间预算、校准遍数（2–5，默认 3）、产品保护指标容忍量，以及可选 δ、模型配置与测量费用预算。主指标使用该输入的官方评分协议。
2. 预览校准并再次确认。校准固定整篇论文样本 P 和诊断样本 D，先完成 P 的独立尝试，再测 D。顾问最多等待 10 分钟，失败或无效建议回退规则值。
3. 阅读匿名校准图、预测区间、参数规则值／当前值／来源／理由／置信度。修改值后先预览影响，再保存；未保存的修改会阻止启动。保存失败时保留错误，不能假装已应用。
4. 预览划分、预算、终评授权和配置，再确认启动。服务端只接受尚未消费且未过期的确认令牌；操作者、参数、原始校准、已有配置或目标进程变化后必须重新预览。
5. 运行页分概览、轮次、校准、时间与花费、配置与账本。点击候选查看假设、实现者检查、首测／复测、机制和保护指标，再看结论。

关闭向导后迭代仍保留；重新打开 pending 迭代可继续。校准已启动时使用原输入和原操作者恢复，不能修改 k 或替换模型配置。校准完成后复用 P/D，不再次派发已完成的种子测量；冻结后的验证集其余题补测一遍。

暂停发送 SIGINT，停止本地轮询并保留远端工作以便续跑。停止发送 SIGTERM，取消在途执行、归档 Agent，记为 `stopped`，不能按普通续跑恢复。远端取消请求不等于远端已确认停止，仍需检查回执。控制台重启后按运行绑定、锁和进程身份重新发现运行；PID 的 boot ID 和 start ticks 必须匹配，发送信号通过 pidfd 固定目标。

## 配置来源

| 项目 | 来源与修改规则 |
|---|---|
| 起点、任务池、mode、官方主指标、sealed 选择 | 新迭代／已有输入协议；不让浏览器指定服务地址或源码路径 |
| `time_budget_hours`、`calibration_k`、`safeguards`、δ | 用户给出；k 不是顾问可调整参数 |
| `spend_budget`、`price_per_1k_tokens` | 用户可选；同一货币单位，未知价格或用量不补零 |
| Agent profiles | 工作台已登记配置；未选择时用服务器默认；顾问默认复用分析配置 |
| 验证题数、每轮候选数、并发、筛选策略、时限与重试 | 校准规则先计算，顾问可在白名单内建议，用户可覆盖；参数必须满足跨字段及整篇划分约束 |
| 保护噪声系数、复测等方法参数 | 方法默认值，偏离时记录变体；合并和消融默认关闭 |
| 服务地址、凭据、路径、执行器配置 | 服务器私有配置；不能从网页请求覆盖 |
| 最终运行配置 | `run-config.json` 冻结，附逐项来源和哈希；运行中只读 |

费用区间只估计后续测量（含验证集基线补测），不含此前校准及分析／实现 Agent。它不是整场运行的硬性总费用上限。实际总费用未汇总时页面显示未知。校准阶段和冻结后优化阶段各自从阶段启动计时，校准耗时没有从优化阶段预算中扣除；规划总投入时需额外计入校准与顾问时间。

## 命令行

先通过工作台或现有创建 API 建立 deferred 迭代。下列变量由操作者设置为自己的私有配置及状态目录，不把真实路径、地址或密钥提交到仓库：

```bash
# 输入 JSON 例子：不含服务地址或凭据。
# {"time_budget_hours":8,"calibration_k":3,
#  "safeguards":{"tokens_per_task_ratio":0.5,"answer_produced_rate":0.02}}
python -m rag_eval.optimizer.cli v4 calibrate \
  --config "$V41_SERVER_CONFIG" --state "$V41_WORKBENCH_STATE" \
  --iteration "$V41_ITERATION" --actor "$V41_ACTOR" --run-id "$V41_RUN_ID" \
  --state-dir "$V41_RUN_DIR" --v4-config "$V41_INPUT_JSON"

python -m rag_eval.optimizer.cli v4 proposal show --state-dir "$V41_RUN_DIR"
python -m rag_eval.optimizer.cli v4 proposal set 'max_concurrent_executions=2' --state-dir "$V41_RUN_DIR"

python -m rag_eval.optimizer.cli v4 run \
  --config "$V41_SERVER_CONFIG" --state "$V41_WORKBENCH_STATE" \
  --iteration "$V41_ITERATION" --actor "$V41_ACTOR" --run-id "$V41_RUN_ID" \
  --state-dir "$V41_RUN_DIR" --from-proposal "$V41_RUN_DIR/proposal.json" --no-test-set

python -m rag_eval.optimizer.cli v4 status --state-dir "$V41_RUN_DIR"
```

校准可选 `--no-advisor`、`--history <ledger.json>`（可重复），以及 `--spend-budget`／`--price-per-1k-tokens`。恢复校准重复原命令；恢复优化重复原 `run --from-proposal` 命令。对已查清的可重试流程失败加 `--retry-failed`，不会把已完成的独立测量替换成新的独立尝试。不能编辑冻结文件绕过身份检查。

工作台启动要求配置由真实配置文件加载（服务器保存可信 `_config_path`），同一迭代只能绑定一个自动运行。校准／运行会产生真实调用，本文只说明操作，不构成运行授权。

## 运行工件

| 文件 | 作用 |
|---|---|
| `calibration.json` | P/D 身份、阶段结果、全 k 方差、实际计时依据、未知量与假设、内容哈希 |
| `proposal.json` | 规则／顾问／用户值、范围、理由、置信度、simulation ID |
| `proposal.agent.json`、`advisor-intent.json` | 顾问产出和固定截止时间；迟到产出不覆盖回退决策 |
| `run-config.json`、`resolved-config.json`、`cli-run.json` | 不可变划分／配置及运行身份；冻结中断可以幂等恢复 |
| `baseline-remainder.json` | 冻结后 V\\P 的基线补测，用于恢复时复用 |
| `state.json`、`ledger.json` | 轮次、测量／复测、判定、人工审查标记、预测、时间账及取消回执 |
| `confirmation.json`、`launch.json`、`process.json` | 一次性确认、原始启动输入、拥有的进程身份 |
| `process.log`、`run.lock`、`control.lock` | 子进程日志、运行独占、操作串行化 |

这些目录可能包含内部诊断和案例映射，不应整体发布。公开页面投影使用计数与匿名行号；缺分保持缺失，不能变成零分。

## 已知限制与服务器验收

- P/D 当前串行执行，可选并发探测尚未实现；并发建议缺实测时标为假设。
- 基础设施重试使用现有整作用域执行接口，健康题可能物理重跑，但先前健康分数保留，重试不计为独立复测样本。
- 固定开销／每题耗时不可识别时明确采用零开销假设；无缓存回执时冷／热值未知。
- 浏览器工具和 brief 的可见范围不是操作系统级文件沙箱。
- 当前没有真实服务器模型／顾问演练证据，预测准确性与模型稳定性尚待验证。
- 校准和候选图属于验证过程观察，不证明解决了验证集过拟合。跨节点累计 Δ 是估计；正式终点评测另行授权。

首次服务器演练使用新 deferred 迭代与 `--no-test-set`。逐项核对冻结值与实际调用一致、P/D 复用、复测独立尝试、保护指标配对与噪声门槛、暂停／续跑／停止回执、预测与实际耗时。服务器 agent 只拉取、部署、运行、报告，不现场改代码。当前 98 道 sealed 题需先做访问历史审计，不能默认用作终评。

## 本地资源预算

WSL 约 7.8 GiB。所有 pytest、前端构建、类型检查、浏览器验证共用 `/tmp/v41-pytest.lock`，每次只跑一批；拿锁后 `MemAvailable` 至少 2.5 GiB。pytest 使用 `-n 1`，BLAS／OMP 线程均为 1；Node 堆上限 1536 MiB、Expo `--max-workers 1`。每批限定超时，不跑 SN 全量测试，不终止无关进程。测试证据见 `method-v4.1-progress.md` 和本地交付包。
