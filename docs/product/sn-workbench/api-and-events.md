# 接口与事件草案 v0.4

2026-10-11 修订。这是拟定接口，尚不可调用；路由、DTO、字段命名和传输方式可在实现中调整，不作为预先冻结的契约。浏览器、Agent 工具和 CLI 共享 Python 领域应用服务；Node 网关负责认证、Agent 适配和转发。正文中的 `/api/v1` 是同源网关路径，内部 Python 路径去掉 `/api`。不开放任意文件路径、shell 或 Paseo RPC 透传。

具体接口在负责该职责的模块实现时核对生产者与消费者即可，不需要先逐条完成审批。保持执行记录、评分来源、取消回执与历史解释一致；这些行为可以由不同机制实现。正文的独立网关、SSE、outbox 和委托令牌是当前方案示例，是否采用及怎样组合随实际接入确定。

## 数据归属

| 内容 | 生产者与权威 | 消费者可依赖的保证 |
| --- | --- | --- |
| Task、关联、草稿、操作意图 | Python／SQLite | 稳定 ID、revision、明确来源；会话不存在不丢失目标 |
| Job 状态、阶段计数 | worker 对进程及工件对账，SQLite 保存投影 | 更新时间、observation 状态，计数与来源范围一致；不是仅心跳判断 |
| 计划、结果、评分、人评 | 发布的版本化工件及清单 | hash／范围／分母可核验，不被数据库缓存回写 |
| 会话与 turn | Paseo／provider | capability、epoch／seq 与真实 turn 事件；idle 不证明任务完成 |
| 卡片 | Python 读取上述对象生成 | 稳定 card ID、目标 ref、live revision；没有独立评分或状态副本 |
| 临时输入、布局、材料选择 | 前端 | 与具体目标版本绑定；失败不清空输入，不能宣称服务端保存 |

所有 ID 是 opaque string，时间使用 UTC ISO-8601 或 null＋unknown reason。缺值不由客户端转零。分数采用有限 JSON 数字，单位与分母显式；来源引用包含角色、逻辑 ID、字节 hash 和受控 locator ID。路径只在服务端登记，EvidenceRef 不接受 Agent 自由路径。

## DTO 与修订

TypeScript 设计形状如下，实际 Zod schema 与 Python校验须同义。只列首个切片公开字段，秘密配置始终以 profile ID 引用。

```ts
type Ref = { kind: string; id: string; revision: string };
type JobStatus = 'queued' | 'starting' | 'running' | 'cancel_requested'
  | 'reconciling' | 'completed' | 'partial' | 'failed' | 'cancelled' | 'interrupted';
type Coverage = {
  planned: number; attempted: number; successful: number;
  noAnswer: number; clarification: number; error: number; pending: number; unstarted: number;
};
type JobSnapshot = {
  id: string; revision: number; taskId: string; plan: Ref; status: JobStatus;
  observedAt: string | null; observation: 'current' | 'reconciling';
  generation: Coverage; scoring: Record<string, MetricCoverage | BatchMetricState>;
  resultRefs: Ref[]; cancellation: { requestedAt: string | null; acknowledgedAt: string | null };
};
type MetricCoverage = {
  kind: 'per-case'; eligible: number; evaluated: number; validZero: number;
  error: number; pending: number; notApplicable: number;
};
type BatchMetricState = {
  kind: 'batch-only'; status: 'pending' | 'completed' | 'error' | 'not-applicable';
  eligibleScope: Ref; scoresRef: Ref | null; reason: string | null;
};
type Card = {
  id: string; taskId: string; kind: 'evaluation-plan' | 'job-progress'
    | 'result-investigation' | 'change-validation';
  revision: number; target: Ref; sourceRefs: Ref[];
  actions: { command: string; available: boolean; reason: string | null }[];
};
type EvaluationPlanView = {
  ref: Ref; taskId: string; selectionRef: Ref; sourceBindingRef: Ref;
  benchmarkTask: string; caseCount: number; partitionRefs: Ref[];
  mode: 'chunk' | 'reasoning'; profiles: Record<string, Ref>;
  budget: { maxCalls: number | null; maxSeconds: number | null;
    estimatedCost: number | null; unknownReasons: string[] };
};
type TaskView = {
  id: string; revision: number; workspaceId: string; title: string; goal: string;
  state: 'open' | 'paused' | 'completed' | 'archived';
  sessionRefs: Ref[]; activeJobIds: string[]; cards: Card[];
  sourceBinding: Ref | null; updatedAt: string; viewCursor: string;
};
```

Coverage 为展示轴，不能拿它们替换官方逐指标分母。generation 的 successful／noAnswer／clarification／error／pending／unstarted 互斥，合计 planned；successful只统计原success状态，clarification不归入successful。attempted 按实际输出状态计算，不是 provider 调用次数。clarification 保留独立计数及案例原状态；pending 指已开始但未生成终态，与unstarted分开。scoring 每个指标保存 eligible 与 evaluated，pending／error 不补零；不适用通常位于 eligible 之外，具体集合引用原 scorer 协议。一个指标只有批量值时不造逐题 Coverage。

content revision 是不可变工件 hash；可变 Task／Job revision 是单调整数。Card 的 sourceRefs 只指明确版本。live 读取返回当前 accepted revision，历史 ref 读原 revision，不以最新分数替代。

## HTTP 命令与查询

| 方法与路径 | 请求 | 返回与作用 |
| --- | --- | --- |
| GET `/workspaces` | cursor、limit | workspace 摘要和 Task 数，目录状态与运行绑定分别返回 |
| POST `/workspaces` | 已登记 repository/placement ID、SN profile | 登记 workspace，未开始 worktree 创建或实验 |
| GET／POST `/workspaces/{id}/tasks` | 查询分页；创建 title／goal／requestKey | Task 列表或新 Task |
| GET `/tasks/{id}` | 可选 revision | TaskView、卡片及实际关联，不返回全量证据正文 |
| PATCH `/tasks/{id}` | expectedRevision、title／goal／state | 新 revision；冲突 409，paused 不自动暂停 Job |
| GET `/catalog/benchmarks` | 无 | 五套 task／scope／来源／scorer／可用状态 |
| GET `/catalog/cases` | bundleId、benchmarkTask、cursor、query | 分页案例，不发送 gold 给选题工具；详情权限另分配 |
| POST `/selections` | bundleId、benchmarkTask、显式 IDs或筛选＋seed | 冻结具体 ID集合与 hash／资料分区／题数；0题或未知题拒绝 |
| GET `/selections/{id}` | 可选分页cursor | 固定case IDs、hash、选择规则／seed及完整资料分区 |
| GET `/plans/{id}` | 必选revision或发布hash | EvaluationPlanView，旧计划按原配置固定展示 |
| POST `/tasks/{id}/evaluation-plans` | selectionRef、sourceBindingRef、SN/model/scorer profile refs、预算、requestKey | 发布不可变 plan；生成前所需配置版本完整，未调用模型 |
| POST `/plans/{id}/submit` | requestKey、expectedPlanHash、delegationRef或手动确认摘要 | 202 receipt 与 Job ID；幂等记录先于派发 |
| GET `/requests/{requestKey}` | 无 | 原 receipt、发布中或明确未找到；用于丢失响应后的对账 |
| GET `/jobs/{id}` | 无 | JobSnapshot，可能有 provisional 输出；不隐式执行恢复 |
| POST `/jobs/{id}/cancel` | requestKey、expectedRevision | 登记取消意图；响应不是执行端停止保证 |
| POST `/jobs/{id}/retry` | requestKey、明确目标阶段及范围 | 新 Job／attempt，父引用及原因；不在原 run 续跑 |
| GET `/results/{id}` | 必选 scores revision | 原正式汇总、逐指标分母、覆盖、能力缺口及来源 |
| GET `/results/{id}/cases/{caseId}` | answer revision、scores revision | 案例和受控证据引用；片段按需单独读取 |
| GET `/evidence/{locatorId}` | 原 artifact hash、record／范围 | 核验后片段与完整性，不能传任意 URL／路径 |
| POST `/reviews` | targetRef、reviewer、rubricRef、baseRevision或null、判断、理由、证据、requestKey | 工件发布后返回新 ReviewRevision，索引延迟可对账 |
| POST `/results/{id}/rescore` | 答卷引用、新 scorer profile、预算、requestKey | 新评分计划及 Job，不增加 SN 生成次数 |
| POST `/tasks/{id}/sessions` | create或attach、provider profile或已存在 session ID、scopeRef、requestKey | 关联 session；create 与 attach分别校验，其他导入方式待实际能力核对 |
| GET `/sessions/{id}` | 无 | 实际session／turn、provider能力、连接、pending permission；与Task状态分开 |
| GET `/workspaces/{id}/files` | 已登记parent locator ID或根 | 只读文件目录，秘密目录受策略限制；不接受任意根路径 |
| GET `/files/{locatorId}` | expected content hash或实时读取声明 | 有来源及内容hash的只读文本；源码位置属于哪个checkout显式 |
| GET `/workspaces/{id}/changes` | 可选固定source revisions | dirty或固定快照diff，capture时间及外部改动可能性，不把diff当执行树 |
| POST `/sessions/{id}/messages` | text、context refs、clientMessageId | 持久消息意图及真实 turn/session 回执；scope由服务端关联，不来自 text |
| POST `/sessions/{id}/interrupt` | requestKey、完整 session ID与期望 activeTurn | 单会话停止请求，能力与 CLI 适配见技术决策；不取消 Job |
| POST `/sessions/{id}/permissions` | requestId、response、scope revision | 只回应真实 provider 待决项；过期请求拒绝 |
| GET `/tasks/{id}/history` | cursor、kind、limit | 固定事件版本，originalAt／registeredAt 分开，未知原时间不替换 |

路径以 `/api/v1` 为前缀。所有写操作都检查操作者、workspace/Task/session 关联。Python内部调用只接受网关签名的短期actor/scope断言或等价受认证客户端身份，不信任裸actor请求头；共享秘密仅网关和Python服务持有，内部端口绑定loopback。普通 profile、repository 和路径登记由受控配置管理，不让 Agent 随意增加可执行目录。generation 工具不读取 `results/.../cases` 的诊断 gold；诊断工具与生成工具的能力范围分开。

CLI停止操作核对expected activeTurn后发起，但当前公开CLI没有daemon原子turn条件；这是单会话当前活动工作的中断，不能保证恰好停止某个旧turn。若快照变化，明确记录实际观察及未决回执，不据旧请求把新turn标取消。

scan／analysis／change capture／validation／comparison 可复用同一领域命令思路，名称和接入顺序随实现调整。当前均为拟定路由；原型仅演示预期交互。

成功 receipt 包含 `requestId, requestKey, objectRef, jobId|null, savedAt`。服务端请求幂等键按 actor＋command 作用域保存；相同键相同规范化内容返回原 receipt，不同内容 409。模型副作用不是 HTTP 恰好一次保证：owner/process 对账按总体技术方案，未知时停派发。requestKey 未找到时也须查稳定 plan/job目标，不能以单次404证明无远端动作。

## 错误契约

```json
{
  "error": {
    "code": "SOURCE_NOT_FROZEN",
    "message": "当前源码有未提交变更，尚未形成可执行快照。",
    "retryableRead": false,
    "requestId": "req_demo_1",
    "details": { "workspaceId": "ws_demo", "remedy": "capture-source-or-select-clean-snapshot" }
  }
}
```

| 错误码 | HTTP | 用户行为 |
| --- | --- | --- |
| `INVALID_SELECTION`／`CONFIG_INCOMPATIBLE` | 422 | 保留输入、调整题单／配置 |
| `REVISION_CONFLICT`／`IDEMPOTENCY_CONFLICT` | 409 | 对照真实版本，不自动覆盖或换键重试 |
| `SOURCE_NOT_FROZEN`／`RUNTIME_UNVERIFIED` | 409 | 阅读与调查继续，明确版本后再提交 |
| `CHECKOUT_WRITE_CONFLICT` | 409 | 核对写入者，不能超时抢占 |
| `CAPABILITY_UNAVAILABLE` | 422 | 说明 provider／SDK 限制，保留手动能力 |
| `ARTIFACT_CONFLICT` | 409 | 拒绝合并损坏或同 ID 异内容 |
| `ARTIFACT_MISSING` | 404 | 保留其他可用结果，说明依赖缺口 |
| `UNAUTHORIZED`／`SCOPE_FORBIDDEN` | 401／403 | 重新登录或取得相应范围，不把 text当授权 |
| `DEPENDENCY_UNAVAILABLE` | 503 | 显示哪条连接失效，读查询可有界退避 |

错误公开正文脱敏；完整异常留服务器。mutation 不自动重试，读操作可以有界退避。超时未拿到提交回执先对账；明确新 attempt 不属于 HTTP 重试。

## 领域更新：持久变更流＋查询

GET `/api/v1/tasks/{id}/events?after={cursor}` 返回 SSE；同一任务的字段更新在 SQLite 状态事务中产生 outbox 行。序号是该 Task 内严格递增，不声明所有进程时间戳能证明跨任务因果。

```json
{
  "schema": "sn-workbench-event-v1",
  "taskId": "task_demo",
  "seq": 42,
  "eventId": "event_demo_42",
  "kind": "job.updated",
  "subject": { "kind": "job", "id": "job_demo", "revision": "7" },
  "occurredAt": "2026-10-10T08:00:00Z",
  "registeredAt": "2026-10-10T08:00:00Z",
  "origin": "worker",
  "requestId": "req_demo_1"
}
```

事件仅包含引用与使查询失效的事实，不塞完整成绩／证据／敏感正文。`task.updated, plan.published, job.updated, artifact.registered, review.published, session.linked` 是首个切片事件。阶段的频繁进度通知可以合并发送最新 seq，但缺 seq 仍触发补查；历史业务事件与性能进度通知分开。

打开任务：建立受授权 SSE 观察，获取 TaskView 和 cursor，再补读之后的事件；客户端按 seq 去重并对 subject revision失效查询。另设 GET `/tasks/{id}/event-page?after=...&limit=...` 按 cursor 拉页，`hasMore` 真时继续。查询视图带 `viewCursor`，旧查询响应不能替换已经接受的更新版本。

断连时保留最后快照和更新时间，不改 Job。恢复后按 cursor补读；如果日志窗口不再覆盖游标，服务返回 `reset_required`，重新获取完整 TaskView，再续接新 cursor。未知 kind 触发 Task 查询而不是猜字段。SSE 断开只释放观察，不停止 Job／Agent。

如采用 outbox，初期可以保留记录并按实际规模决定保留策略；压缩或过期游标仍需明确 reset 语义。history 的固定事件仍持久保留，不由短期通知窗口决定。

## Agent 流：采用 Paseo 既有 epoch／seq

Node 适配建立 SDK timeline.subscribe，await ready 后请求 timeline.refetch；把数据与 live消息按 epoch／seq 合并，不只拼助手文本。sdk `subscription_restored` 后显式补读，`replacement` 使旧 epoch 缓存失效，`hasNewer` 表示继续分页。turn完成从 turn_completed／failed／canceled读取。

网关提供 GET `/sessions/{id}/timeline?cursor=...` 与 GET `/sessions/{id}/events`；前者转发窄 refetch契约，后者转发受限流。网关不另造一个权威聊天库。Task卡片恢复来自 Python；provider无法恢复的会话显示能力限制，Task证据仍在。

两条流并列显示，不混用 cursor，任务来源事件与 Agent消息不能靠时间戳强制排序为因果。工作卡用任务事件序号及对象 revision，引用到具体 session／turn只是关系；Agent 自由文本仅作为消息。

## 工具、附件与执行权限

MCP工具提供 `inspect_task, propose_evaluation, submit_evaluation, get_job, cancel_job, read_result` 的窄目录，对应上述应用命令。服务端工具配置携带 task／session关联及 DelegationScope令牌；用户消息或正文不能扩大它。提议计划不调用模型评测，submit需要范围匹配、预算及稳定来源。读 gold的diagnostic工具不得被SN生成流程调用。

上下文附件保存 `Ref＋purpose＋excerptPolicy`。前端添加附件时查对象是否存在及操作者权限；网关在每次发消息前重新核对，提取必要有界片段并显示发送目的地。整个结果包、凭据或路径不默认发送；附件标题来自服务端投影，不能使用 Agent 提供的 HTML。

同时写 checkout、直接 shell绕开 MCP、provider重试等行为不是此 API 的硬隔离保证。Agent创建前核对 provider配置，低能力 provider降级；限制的实际可执行性在首个改码切片核验，不能以工具权限证明任意 shell已受限。

## 数据发布顺序

计划：保存请求／固定ID → 发布 plan清单 → 更新索引及 outbox → 入队 → 返回回执。结果：runner先保存答案及观测 → scorer独立发布 → worker校验并登记引用／outbox。人评：核对base revision → 发布修订工件 → 更新索引与 outbox → 返回已保存。

因此实时卡片可能尚有部分数据；最终报告须绑定完整来源。数据库与文件不假定跨介质原子事务，恢复按现有发布清单对账；无法确认的发布不触发重跑。任何旧revision或owner token的迟到写入必须被拒绝。

2026-10-11：明确接口、通信与数据表示仍是草案，具体机制在开发边界收敛；本轮未实现或运行验证。
