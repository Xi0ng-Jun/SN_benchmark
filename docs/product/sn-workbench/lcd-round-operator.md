# LCD 轮次编排：服务器操作与恢复

W8 把已确认的迭代协议接成服务内后台任务。只通过既有执行、Agent 创建／消息接口派发；编排器不直接调用模型。开发验收使用合成执行与假 Agent，不代表服务器真实实验已完成。

## 入口

服务正常运行并登记该迭代后，操作者执行：

```bash
scripts/sn-workbench --state /path/to/state --url http://127.0.0.1:8091 \
  iteration round ITERATION --rounds 2 --request-key campaign-rounds-01
scripts/sn-workbench --state /path/to/state iteration rounds ITERATION
scripts/sn-workbench --state /path/to/state iteration round-show INVOCATION
scripts/sn-workbench --state /path/to/state iteration round-stop INVOCATION
```

`--state` 用于读取现有 access-token，`--url` 也可由 `SN_WORKBENCH_URL` 设置。CLI 只请求 HTTP，不在本地创建模型或执行进程。启动命令本身即人工确认基准补跑；若冷启动估算超过冻结预算，按本次明确授权覆盖并记录操作者和原因。后续候选仍受冻结预算约束。重复请求请保留 `--request-key`；省略时生成新键，若同一 iteration 已有活动 invocation 仍恢复它；恢复不会改变原有轮数、设置与操作者。

HTTP（沿用工作台 token、`X-Workbench: 1` 与同源校验）：

- `POST /api/iterations/{id}/rounds`，body 为 `{"request_key":"...","rounds":1}`。
- `GET /api/iterations/{id}/rounds`：列出该迭代的历史 invocation。
- `GET /api/rounds/{id}`：读当前阶段、候选阶段、结果、时间点与耗时。
- `POST /api/rounds/{id}/stop`，body 为 `{}`：停止后续派发，保留已启动的执行、Agent、预留与历史；需要取消在途执行时使用既有取消入口。

调用身份来自服务器配置的 actor，客户端不能提交 actor 覆盖它。HTTP 断开不终止后台任务。

## 冻结设置

创建 iteration 的 JSON 可带 `round_settings`（HTTP 与 `sn-eval iteration create --file` 共用）；创建后不回写协议。旧 iteration 无该字段时，在 invocation 起点冻结以下缺省值：

| 键 | 缺省 | 用途 |
|---|---|---|
| `analyst_profile` | `pi-flash` | 分析 Agent |
| `implementer_profile` | `pi-flash` | 各簇实现 Agent |
| `merge_profile` | `pi-flash` | 仅合并来源、解决冲突的 Agent |
| `max_clusters` | 6 | 分析任务提簇上限 |
| `b` | 2 | 每轮选择的合法未解决簇上限，不能大于 max_clusters |
| `pi` | 0.3 | 排序先验；不可测 m/v 保留 null |
| `card_approval` | `auto` | `manual` 时等待既有 proposal 批准入口 |
| `agent_timeout_seconds` | 3600 | 分析／实现／合并 Agent 阶段期限 |
| `execution_timeout_seconds` | 7200 | 基准、候选测量、新鲜确认阶段期限；整数 1–86400（既有 runner 上限） |
| `infra_retry` | 1 | 基础设施失败追加 attempt 次数；不重跑低分 |

执行调度仍由原 `execution` 设置控制，未增加本机硬件上限。`preparation_policy` 在新 iteration 中冻结显式值或服务器默认值；之后服务器改变时使用冻结值，并把两侧策略与 execution ID 写入历史。旧协议缺键继续使用服务器值。

## 预算：公司模型用题次额度

创建 iteration 时，`budget` 必须含正整数 `max_runs_per_candidate`，并至少含一个总额度：正整数 `max_case_runs` 或正数 `limit_cny`。两者可以同时提供；自动确认须满足全部已配置上限。旧 `{limit_cny, max_runs_per_candidate}` 形式继续有效。

公司模型没有公开价格时，建议使用以下创建请求片段（数值是示例，按实验规模调整，不是服务器性能限制）：

```json
{
  "budget": {"max_case_runs": 10000, "max_runs_per_candidate": 100},
  "round_settings": {"agent_timeout_seconds": 3600, "execution_timeout_seconds": 7200}
}
```

题次按每个已预留 attempt 的完整 `case_count` 累加：例如 20 题 × 2 次 = 40 题次，父补跑、簇试验、fresh、失败、取消及基础设施重试全部计入。派发前整组原子预留；重复同一 attempt 不重复计数，重试的新 attempt 另计。终态费用结算不会退还题次，预留后未完成的工作也不会被当作免费。超出任一上限的请求保留 pending，等待人工明确覆盖；round 的预算耗尽判断同时检查题次和金额，原有启动确认基准派发的语义不变。

`iteration show` 的 `budget_usage`、`iteration ledger` 的 `sampling_ledger.budget_usage` 与三笔账面板显示 `case_runs_used`／`policy.max_case_runs`；配置金额上限时同时显示 CNY 占用／额度。未知实际费用仍为 null；count-only 的内部历史 CNY 估算保留，但不参与额度判断、不冒充报价。

`limit_cny` 保留给价格表已覆盖的 DashScope 配置。创建带 CNY 额度的 iteration，或提交带 `limit_cny` 的运行请求时，服务会读取 `job_execution.model_config`，核对 `[bindings]` 实际绑定的 chat／embedding／rerank 模型是否都在 `model_budget.RATES` 中；展示用 `model_names` 不作为依据。未知价格报出模型名并指向 `max_case_runs`；配置不可读或绑定无法解析时直接拒绝，不猜测价格。修改服务器模型配置后，新的带 CNY 运行请求会重新检查。

公司模型的运行请求只传 `timeout_seconds`，不要带 `limit_cny`；W8 自身派发始终如此，不安装逐请求价格护栏。无冻结预算的旧迭代仍需既有人工确认；要使用题次自动确认，请新建含上述预算的 iteration，不能改写旧冻结协议。

## 服务器准备

- `task_worktrees.create_candidates=true`，可信 SN checkout／工件登记、执行配置、输入／sealed 登记仍按既有接入文档设置。编排不执行 sealed-run。
- 在 `agent_creation.profiles` 登记上述三个 profile 名（可指向同一个公司提供的模型配置），配置 `agent_creation.workspaceId`、`agent_bridge`，确保创建、发送和读取 Agent 状态均可用。分析者在评测 checkout；实现者只进自己的 SN worktree。
- 新建迭代时选定足够的 fresh 批次和冻结 `budget.max_case_runs`（或已定价模型的 `budget.limit_cny`）／`max_runs_per_candidate`，预算要覆盖父补跑、簇试验、候选、fresh、合并和基础设施重试。金额不是性能调度阀；不要把本机实验的金额／线程数照搬到服务器。没有冻结预算的旧迭代仍保留既有 Agent 请求人工确认边界。
- 配置检查不需要 `--execution` 就会核对 `job_execution.python` 与 `runtime_expectation` 的 executable、prefix；保留虚拟环境调用路径，不把 symlink 解析成系统 Python。`--execution` 继续额外检查文件、源码和输出隔离等条件。
- `preparation_workers` 仍受 P9 的 `2 × 最小 chat max_concurrency` 校验；准备缓存按键锁控制重复导入。每个候选独立推进，同一测量组的缺失 attempts 先一起预留再并行派发。

## 状态与恢复

invocation 持久状态为 `running`、`waiting_approval`、`completed`、`stopped`、`failed`；阶段为边界检查 → 基准 → 分析 → 选择 → 批准 → 并行候选 → 可选合并 → 账本 → 本轮完成。候选独立经过派生、实现、测量、gate、fresh；合并经过派生、实现、测量、合并比较。时间点、期限、卡片／排名快照、verdict 和账本写入 SQLite。

服务启动重新扫描活动 invocation；同一 iteration 的进程间锁防止并发推进。每个外部动作使用确定请求键，恢复复用创建／消息／执行凭据；不确定的远端创建或发送不自动重放。Agent 已发送任务后观测到运行再空闲，或发送后经过 30 秒启动宽限仍空闲而缺预期产物，会记 `stalled`；阶段超过冻结期限也记 `stalled`，保留原因，其他候选继续。存在执行中的请求时按执行期限等待，而不把 Agent 空闲误当完成。

基础设施失败和原 attempt 永久保留，允许的重试追加新键、重跑该 attempt 的完整冻结选择，并经过同一预算账。人工 stop 不回滚来源、成绩或记录。轮数耗尽、新鲜批次耗尽、连续两轮无 accept、预算耗尽时不再开新轮，并收拢已启动候选的结果。manual 卡片批准等待不消耗 Agent 期限；人批准后后台自动继续。

合并使用无逐题预测的 `component=integration` 元数据卡授权实现者，仅下发来源 commit／路径；它不冒充 LCD 合同，使用完整 validation 池的 Δ 与 η 决定保留或撤回。普通选择仍要求有簇、有合法合同并通过泄漏检查。

重试限制按原 attempt 持久计数，换 round 请求键不重置；每代重试有独立 action request 与不可变派发清单，超额人工确认仍执行同一清单。运行中或已有终态的执行不重复派发。若源码捕获／配置准备在发布 execution 前失败，记录失败并停止该路径，不把无法证明的准备异常当作可重试模型基础设施错误。新鲜批次领取若与并发既有请求冲突，保留原选择并报失败，不悄悄换用下一批。

簇试验同样参与基础设施重试，只处理实现者已经请求的本簇及父侧测量，不越权启动其他簇。若实现者已读初次结果，随后自动重试改变测量，记账人重新计算诊断并分别记录“已读原结果”和“服务刷新比较 ID”，不声称 Agent 已读新的诊断；未读过任何结果仍等待 Agent。fresh 的完成入池延后到该批的有界重试全部终态，普通树／账本查询不能提前冻结初次失败的比较。并行候选遇到刚扩大的 validation 池时，先以新的确定键补测新增批次，再针对同一份不可变比较做 gate。
