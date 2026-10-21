# 一次性 Node 协议：读取、无 prompt 创建与单次发送

基于 npm 发布的 `@getpaseo/client@0.11.2` 包根 API。构建后运行：

```bash
npm --prefix apps/sn-agent-bridge run build
node apps/sn-agent-bridge/dist/cli.js < server-owned-request.json
```

stdin 是一份 UTF-8 JSON；调用者写入后须关闭 stdin。stdout 仅一份 JSON 加换行，
SDK logger 关闭，没有配置、凭据或原异常。结构化成功／缺失／失败均退出 0；
stdout 管道错误退出 1。Python 调用者须检查 envelope，不能仅凭 exit code 判断成功。
调用者负责子进程 timeout；超时／终止且未收到完整 envelope 时，资源释放状态未知。

## 输入

```json
{
  "config": {"url": "ws://127.0.0.1:6767/ws", "connectTimeoutMs": 10000},
  "command": {"op": "readAgent", "agentId": "agent-example"}
}
```

`config` 只允许 `url,password?,authHeader?,connectTimeoutMs?`。配置由服务端登记，
不能由浏览器透传。CLI 不读环境变量或 token 文件，不回显配置。
url 必须 ws/wss、路径以 `/ws` 结束且不含 URL 用户信息。
connectTimeoutMs 是 1..120000 的整数，默认 10000。

每个命令严格拒绝表中以外的字段，不接受 subscribe、requestId 或额外控制操作：

| op | 其余允许字段 | 成功 result |
| --- | --- | --- |
| listProviders | cwd? | status, entries, generatedAt |
| listModels | provider, cwd? | status, entries, provider, fetchedAt |
| listWorkspaces | cursor?, limit? | status, entries, pageInfo |
| readAgent | agentId | status, agent |
| readTimeline | agentId, direction?, cursor?, limit?, projection?, expectedCwd? | status 及下述历史页 |
| createAgent | workspaceId, expectedCwd, agentId, idempotencyKey, config, title? | not_attempted / created / unknown，见 A06a |
| sendAgent | agentId, expectedCwd, text, messageId, activeTurnBehavior（全部必填） | not_attempted / acknowledged / unknown，见 A07a |

direction 为 `tail|before|after`，省略时遵循 SDK 默认；projection 为
`projected|canonical`，本模块默认 projected。cursor 为 `{epoch:string,seq:number}`，
seq 是非负安全整数。limit 为 1..100 整数，默认 50；SDK 的 limit=0 表示无限制，
因此本边界拒绝 0。ID／epoch 最多 512 UTF-16 units，其余输入字符串最多 8192；
ID 不接受空白首尾。stdin 总量最多 64 KiB，非法 UTF-8／JSON、多个 JSON 文档或
超限均返回固定 INVALID_INPUT，且不构造 SDK。

## 结果与释放

始终返回 `{result,release}`。连接成功后只执行一个命令，finally 调用 close。
result 成功不能证明释放成功：

```json
{"result":{"status":"ok","entries":[],"generatedAt":"2026-10-11T00:00:00Z"},"release":{"status":"unavailable","code":"CLOSE_FAILED"}}
```

正常释放为 `release:{status:"ok"}`；输入被拒绝或构造前失败为
`release:{status:"not_needed"}`。SDK close 拒绝则保留未确认释放状态，不假报成功。

```json
{"result":{"status":"missing","code":"AGENT_NOT_FOUND"},"release":{"status":"ok"}}
```

```json
{"result":{"status":"unavailable","code":"INVALID_INPUT"},"release":{"status":"not_needed"}}
```

只有 refresh 返回 null 才声明 missing；RPC 错误／历史读取过程中 Agent 消失均为
READ_FAILED，不从原异常文字猜不存在。请求必须使用完整 ID；SDK 别名解析到其他 ID
会返回 IDENTITY_MISMATCH。其余固定码见 README 的表。

stdout（包括换行）最多 4 MiB。读取超限整份 result 变为 RESPONSE_TOO_LARGE，保留
实际 release；不丢条目制造假分页。已开始创建／发送的编码失败则为 unknown，见 A06a／A07a。该界限约束输出字节量，不限制 SDK 接收或
JSON 序列化前的内存占用。没有新增逐 RPC timeout，后续 Python 管理进程预算。

## Agent 快照

`readAgent` 的成功 result 为 `{status:"ok",agent:{...}}`。字段是
id/cwd/status/title/provider/model，及 SDK 实际存在的 workspaceId/archivedAt/activeTurn。
activeTurn 只含 turnId/startedAt，或 SDK 真实的 null。缺少的可选字段保持省略，
不能将未观测值改成“已确认不存在”。cwd 保留 SDK 原始路径，供服务器匹配本地
Task workspace；浏览器投影与目标授权由 Python 层负责。

capabilities 有十个白名单 supports… 字段（见样例）；SDK 已知 boolean 原样保留，
未知为 null。没有 config/env/runtime、权限内容、labels、持久化信息、私有 metadata
或原异常。可直接调用模块：

```ts
const result = await bridge.readAgent("agent-example");
const history = await bridge.readTimeline("agent-example", {
  direction: "before", cursor: { epoch: "epoch-example", seq: 14 },
  limit: 2, projection: "projected",
});
```

## A02e 服务端目录条件

`readTimeline` 可传 `expectedCwd?: string`。读取命令中仅此命令允许该字段；
A06a 创建也必填该条件。它来自 Python
注册的 Task workspace，浏览器不能自由提供。模块与 CLI 都校验此条件，且不会将
它传给 SDK refetch。省略时保持既有无条件读取语义，不做本地路径解析。

后端逐页调用的合成 command（config 仍由服务端提供）：

```json
{"op":"readTimeline","agentId":"agent-example","expectedCwd":"/example/repo","projection":"projected","limit":50,"direction":"after","cursor":{"epoch":"epoch-example","seq":13}}
```

在 refresh 返回完整 ID 的 Agent 后、refetch 前，解析 expectedCwd 和实际 agent.cwd
为本 Node 主机文件系统的 realpath，并确认两者都是目录，再比较 canonical 路径。
符号链接、`.`、`..` 与尾斜杠可指向同一目录；比较采用该主机的实际路径语义。
这要求 daemon cwd 与 Task workspace 在 bridge 主机的文件系统中可解析。
每次调用重新检查，不缓存，也不向输出增加条件路径或 canonical 路径。

| 情况 | result | 是否发 refetch |
| --- | --- | --- |
| canonical 目录相同 | 既有历史页 DTO | 是（生命周期仍有效时） |
| canonical 目录不同 | unavailable / SCOPE_MISMATCH | 否 |
| expectedCwd 非绝对路径、空值、NUL、类型无效或无法解析为存在目录 | unavailable / INVALID_INPUT | 否 |
| 实际 cwd 非绝对路径、缺失、解析／stat 异常或非目录 | unavailable / READ_FAILED | 否 |
| refresh 确认 Agent 不存在 | missing / AGENT_NOT_FOUND | 否 |
| 目录检查期间 close | unavailable / CLOSED | 否 |

文件系统原异常和路径不会进入失败响应。CLI 仍独立报告实际 release，例如：

```json
{"result":{"status":"unavailable","code":"SCOPE_MISMATCH"},"release":{"status":"ok"}}
```

该条件是读取生产者尽早检查 workspace 身份。它不是 Agent 沙箱或原子锁：
refresh、路径检查、refetch 仍非原子，检查之后 Agent cwd 或符号链接仍可能变化。
服务器跨页逐次提供 expectedCwd、固定 projected/limit50，并使用真实 cursor。

## 历史页

成功 result 保留实际 agentId/direction/projection/epoch/reset/staleCursor/gap/window
（minSeq/maxSeq/nextSeq）、startCursor/endCursor、hasOlder/hasNewer，以及 mergeWindow
（SDK 未提供时为 null）。响应 projection 来自 SDK 实际结果，可能不同于请求。
不修正／推算任何序号、游标、恢复标记，也不把一次 refresh＋refetch 声称为原子快照。

每个 entry 保留 provider/timestamp/seqStart/seqEnd/sourceSeqRanges/collapsed/turnId；
缺省 turnId 为 null。sourceSeqRanges 是实际来源区间，可能不连续；不能由数组 offset
或 seqStart..seqEnd 包络猜来源身份。后续服务器按 epoch 与实际 entry 校验来源。
SDK 返回超过 100 条时整页失败，不截断条目。

item 始终含 type/text/title/status。投影目标是让人在工作台看到与 Paseo 相同的
Agent 轨迹（早先的“最小暴露”投影已由用户决定取代；隔离约束针对发给 Agent 的内容，
不针对人看到的内容）：

- user_message／assistant_message：text 为消息正文，title/status 为 null。
- tool_call：title 为有界 name，status 为 running/completed/failed/canceled；另含
  `detail` 与 `error`。detail 是展平的 ToolCallDetail：
  `{"kind":"shell","fields":{"command":<文本>,"cwd":<文本>,"output":<文本>,"exitCode":0}}`。
  kind 取 SDK detail.type；fields 只含该类型已有字段，值为文本片段、有限数字、布尔或
  null。各类型字段：shell command/cwd/output/exitCode；read filePath/content/offset/limit；
  edit filePath/oldString/newString/unifiedDiff；write filePath/content；search
  query/toolName/content/mode/truncated/numFiles/numMatches/filePaths/webResults/annotations
  （列表按行拼接）；fetch url/prompt/result/code/codeText；worktree_setup
  worktreePath/branchName/log；sub_agent subAgentType/description/log/actions（每行
  `toolName: summary`）；plain_text label/text；plan text；unknown input/output（JSON 文本）。
  未来未知 detail 类型映射 kind unknown，整个 detail 作为 input JSON。缺少 detail 时为 null。
  error 在失败时为错误文本（非字符串按 JSON 文本），否则 null。callId/metadata 不输出。
- reasoning：text 为思考正文；error：text 为 message；notification：text 为 message，
  status 为 level；todo：`items:[{text,completed,status}]` 最多 100 项，其余计入
  `itemsOmitted`；compaction：status；plugin：仅固定标题，不输出 payload。
- 未知类型若 SDK 已交付给投影层，则映射 activity，固定标题 Activity、原类型放在
  有界 sourceType，text/status 为 null；不输出 unknown payload。
  发布 SDK 的解析层是否接受未来未知类型尚未真实验证，替身仅证明投影边界行为。

活动文本（reasoning、tool detail/error、todo 项、error/notification）每个仍按下述
8192 UTF-16 units 片段裁剪，另共享每页 2.5 MiB（JSON UTF-8 字节）预算，从最新
条目向前分配；预算耗尽后旧条目只保留极短前缀并标 truncated。整页输出因此远低于
CLI 4 MiB 上限，不会因长工具输出整页失败。消息正文不计入该预算。

文本／标题／sourceType 使用：

```json
{"value":"Example","truncated":false,"originalLength":7,"lengthUnit":"utf16"}
```

最多保留 8192 UTF-16 units，避免切断代理对。originalLength 是原字符串的 UTF-16
长度，truncated 明示是否裁剪；不得将截断内容显示为完整消息。文本完整性只相对于
SDK 返回的该字符串，不表示整轮回复完成。空历史成功返回 entries:[]，其 window、
游标和标记仍来自 SDK；故障不伪装成空历史。

## 合成样例

全部内容为构造示例，无真实凭据、会话或私有消息：

- [read-agent.request.json](examples/read-agent.request.json)／[read-agent.response.json](examples/read-agent.response.json)
- [read-timeline.request.json](examples/read-timeline.request.json)／[read-timeline.response.json](examples/read-timeline.response.json)

response 文件就是完整 stdout envelope 的形状（实际 CLI 输出单行 JSON）。目录读取
DTO 继续遵循 A02a README。写能力仅下述无 prompt 创建和单次发送；没有显式订阅、
Job、MCP、HTTP 网关或 Python 接线。

## A06a：一次无 prompt 创建

模块接口 `bridge.createAgent(options): Promise<CreateAgentResult>` 与 CLI command
除 `op` 外同形。全部输入来自服务器冻结的 creation intent；Task、workspace、provider、
model、mode、effort 的允许范围及持久化由服务器负责。桥接白名单不等于授权或沙箱。
CLI 合成 stdin（所有 ID／路径／配置均为示例）：

```json
{"config":{"url":"ws://127.0.0.1:6767/ws","connectTimeoutMs":10000},"command":{"op":"createAgent","workspaceId":"workspace-example","expectedCwd":"/example/repo","agentId":"b7abb649-7b0d-4ead-9368-b2a4c7c58209","idempotencyKey":"create-example-1","config":{"provider":"codex/model-example","modeId":"mode-example","thinkingOptionId":"effort-example"},"title":"Example"}}
```

`config` 仅允许 `provider,modeId?,thinkingOptionId?`；provider 必须含非空的
`provider/model`，按公共 SDK 的第一个 `/` 分隔。opaque 字符串不接受空值、首尾
空白或 NUL；workspaceId、幂等键及配置字段最多512 UTF-16 units，title最多200。
agentId 必须完整 RFC UUID（version 1..8、标准 variant，保留原大小写），不接受
短 ID／别名。expectedCwd 必须非空绝对路径、最多8192、不含NUL，保留尾空格等
真实路径字符，不 trim。其余字段一律 INVALID_INPUT，包括 prompt、env、parent、
git/worktree、images/attachments、MCP、features、toolPolicy、systemPrompt、native options。
不从文件或环境读取配置／凭据。

创建前：公共 `workspaces.ref(workspaceId).refresh()` 必须返回完全相同的 workspaceId、
已知活动状态、`archivingAt:null` 和可用 directory。状态集合是
needs_input/failed/running/attention/done，它们是聚合活动桶，不是创建就绪标签；
不要求整个 workspace 空闲。expectedCwd 与 workspaceDirectory 经本机 realpath/stat
确认是同一现存目录。再用完整预定 UUID 公共 refresh 检查是否已存在；存在则
AGENT_ALREADY_EXISTS，不能把旧资源当本次创建。检查后再次核对 closed、releaseFailed、
实例归属、是否正在释放与 connected，再恰一次调用 workspace handle `agents.create`。
只传 agentId、idempotencyKey、闭合 config 和可选 title，省略 prompt 等字段。

调用返回后核对 handle 的完整 ID，再公共 `handle.refresh()` 核对 id、canonical cwd、
workspaceId、provider 与 model。核心身份缺失／不符、refresh null／异常都不能声明
成功。archivedAt、实际 status 与 activeTurn 决定当前观测的 readiness：

| result.status | 含义与字段 |
| --- | --- |
| not_attempted | 此次桥接未调用 SDK create；固定 code，合法完整输入保留 agentId |
| unknown | SDK create 调用已开始，不能确认完整结果；code=CREATE_OUTCOME_UNKNOWN、固定 cause、预定 agentId，及可选 observed |
| created | 返回 handle 与读回核心身份全部匹配；agentId、最小 agent DTO、ready、readiness |

created 的 readiness 为 idle / not_idle / archived / incomplete_snapshot。
只有 status=idle、archivedAt=null、activeTurn=null 且已观测这些字段时 ready=true。
缺少可选归档／activeTurn 或 status 字段返回 created、ready=false、incomplete_snapshot；
不等待 turn 结束。此快照不验证 mode／effort 生效，不保证之后仍然空闲。
agent 与 readAgent 使用相同白名单，原 cwd 供服务器核对，不含 config/env/runtime。

not_attempted 的附加固定码为 WORKSPACE_NOT_FOUND、WORKSPACE_UNAVAILABLE、
AGENT_ALREADY_EXISTS；另沿用 INVALID_INPUT、SCOPE_MISMATCH、IDENTITY_MISMATCH、
READ_FAILED 与连接／生命周期码。创建调用开始后任何异常（含同步 throw、超时拒绝）、
close、断连／实例替换、handle不符或无法完整核对读回都为 unknown；cause 是
CREATE_FAILED 或已有固定生命周期／读取／身份码，不从异常文字判断副作用。
observed 仅含调用结束前观测到的有界完整 handleId，以及已通过身份核对时的最小
agent 快照；它不表示 intent 所有设置已生效，也不表示远端不存在。

合成 stdout（正常 idle agent 字段略以完整 DTO 字段形状展示）：

```json
{"result":{"status":"created","agentId":"b7abb649-7b0d-4ead-9368-b2a4c7c58209","ready":true,"readiness":"idle","agent":{"id":"b7abb649-7b0d-4ead-9368-b2a4c7c58209","cwd":"/example/repo","workspaceId":"workspace-example","archivedAt":null,"status":"idle","title":"Example","provider":"codex","model":"model-example","activeTurn":null,"capabilities":{"supportsStreaming":true,"supportsSessionPersistence":true,"supportsSessionListing":null,"supportsDynamicModes":false,"supportsMcpServers":false,"supportsReasoningStream":true,"supportsToolInvocations":true,"supportsRewindConversation":null,"supportsRewindFiles":null,"supportsRewindBoth":null}}},"release":{"status":"unavailable","code":"CLOSE_FAILED"}}
```

```json
{"result":{"status":"unknown","code":"CREATE_OUTCOME_UNKNOWN","cause":"READ_FAILED","agentId":"b7abb649-7b0d-4ead-9368-b2a4c7c58209","observed":{"handleId":"b7abb649-7b0d-4ead-9368-b2a4c7c58209"}},"release":{"status":"ok"}}
```

```json
{"result":{"status":"not_attempted","code":"AGENT_ALREADY_EXISTS","agentId":"b7abb649-7b0d-4ead-9368-b2a4c7c58209"},"release":{"status":"ok"}}
```

输入拒绝时 recognizable createAgent 返回 not_attempted/INVALID_INPUT、无 agentId；
无法解析 JSON／非法UTF-8等无可识别 command 时沿用 unavailable/INVALID_INPUT，
这些输入未进入 SDK。connect／构造失败也为 not_attempted，正常 close 独立报告。
已开始创建的输出超限／编码异常降为 unknown，保留预定 ID 与已有安全 handle 事实、
真实 release，cause=RESPONSE_TOO_LARGE／INTERNAL_ERROR；不会伪报未调用。

边界：公共 SDK 的现代创建内部观察 creation lifecycle（wire subscribe=true），
没有新增显式订阅操作。旧 host 无 caller-ID 支持时公共 create 会拒绝；桥接既已调用
它便保守返回 unknown，不从错误文字改判 not_attempted。SDK创建没有本模块新增的
deadline；Python 后续管理子进程 timeout，并在发起前持久化 intent／预定 ID／unknown。
进程被终止或没有完整 stdout 时按可能已创建处理；close成功只确认本地 SDK 释放，
不能确认未创建，close不删除／归档远端资源。

0.11.2 的公共 SDK 会在回执前断连后保留底层 creation promise 等恢复，即使
reconnect=false。桥接仅在这次 SDK create await 期间拥有一个100ms本地timer，
调用公共getConnectionState()检查仍connected；已知断连终结本地等待，返回
unknown/CREATE_OUTCOME_UNKNOWN、cause=CONNECTION_UNAVAILABLE，并保留预定ID与
已观察安全事实。CLOSED／CLOSE_FAILED继续优先，CLI随后进入finally并独立报告release。
这是本地状态检查，无RPC、无创建重发、无新增deadline。timer在create兑现／拒绝、
断连或close的等待结算后清理；底层promise的迟到拒绝保持处理，不触发unhandled。
预检与readback不新增timer。SDK尚显示connected但无回执时仍可持续pending，
Python进程budget不变；timer也不保证事件循环阻塞时的精确100ms检测延迟。

不自动重发、删除或归档。reconnect=false，不主动恢复创建。readAgent可按预先持久
完整 ID 再核对实例与Task范围，但当前读取 DTO 无幂等键／receipt／intent证明，
不能单凭存在推断本次创建或 mode／effort已生效。预检、目录检查、create、readback
不是原子锁，别的调用者仍可能竞争同一个 ID 或改变状态／目录；服务器必须持有
intent／unknown与本地关联，bridge不存Task数据。创建无prompt仍可能初始化provider
会话或触发daemon插件，不能宣称无其他副作用。本轮不验收真实daemon创建。


## A07a：单次显式发送

`bridge.sendAgent(options): Promise<SendAgentResult>`与CLI command除op外同形，
只接受以下字段（合成示例，不是真实会话）：

```json
{"config":{"url":"ws://offline.test/ws","connectTimeoutMs":10000},"command":{"op":"sendAgent","agentId":"b7abb649-7b0d-4ead-9368-b2a4c7c58209","expectedCwd":"/example/repo","text":"  Example request\n","messageId":"2ac576eb-a0ad-4f85-8bf7-53d12a67c080","activeTurnBehavior":"steer"}}
```

agentId/messageId必须完整RFC UUID（version 1..8、标准variant，保留大小写），不接受
别名／短ID。expectedCwd为server注册Task workspace：非空绝对路径，最多8192 UTF-16
units、无NUL；本机realpath/stat核对现存目录，保留原值及尾空格，不trim。
text必须非全空白字符串、无NUL，同时最多16384 UTF-16 units及32768 UTF-8 bytes；
首尾空白／换行和全部正文原样保留，不静默截断。stdin整体64KiB规则仍生效，JSON转义
可能先触及它。activeTurnBehavior必填interrupt或steer，没有默认。禁止workspaceId、
options、attachments/images、parent、env、config、tools、prompt、MCP及其他字段。
首个await前复制参数；调用者突变不能改变本次target、scope、text、messageId或行为。

Task-session显式关联只存Agent ID，Task本地workspace.location由server注册。
允许已有Agent引用，不要求A06创建配置／binding；remote workspaceId可缺省，没有统一
权威映射，所以不要求或猜测它。服务器负责授权、冻结持久消息意图、配置和产品选择；
白名单不是授权或Agent沙箱，浏览器不能自由指定expectedCwd／daemon配置。

precheck公共完整ID handle.refresh返回agent.id完全匹配；归档字段严格null。
status、archivedAt、pendingPermissions、activeTurn缺失或activeTurn形状无效为
INCOMPLETE_SNAPSHOT。pendingPermissions必须数组且为空；非空或attentionReason为
permission则PERMISSION_REQUIRED。只允许idle/running/error（error是上一轮因provider／连接错误结束，Paseo在下一条消息时续接同一会话）且providerUnavailable不为true；
initializing/closed/未知状态为AGENT_UNAVAILABLE。activeTurn只确认已知null或
{turnId:非空string,startedAt:null|string}，不将idle或turn观察视为完成。
expectedCwd与快照原cwd经canonical现存目录匹配，不符SCOPE_MISMATCH，预期路径异常
INVALID_INPUT，实际路径／读取异常READ_FAILED，不回显路径或异常。
这些门槛避免对已观察归档／权限资源触发隐式unarchive／权限清理；refresh、路径IO与
send非原子，不能保证远端之后不变化，也不提供原子reject-busy。

所有异步预检后及最后调用边界核对closed、releaseFailed、实例归属、正在释放和
connected，随后无await恰一次handle.send(text,{messageId,activeTurnBehavior})。
不调run/waitForFinish、不新增显式订阅／其他写操作，reconnect=false、零重发。

| result.status | 含义 |
| --- | --- |
| not_attempted | 本桥接没有调用SDK send；固定code，合法输入保留预定agentId/messageId |
| acknowledged | SDK Promise<void> resolve；只含预定agentId/messageId，无execution/turnCompleted/receipt |
| unknown | 调用已开始但本地等待失败；code=SEND_OUTCOME_UNKNOWN、固定cause、预定agentId/messageId，可有observed:{sdkResolved:true} |

预检附加固定码AGENT_NOT_FOUND、AGENT_ARCHIVED、INCOMPLETE_SNAPSHOT、
PERMISSION_REQUIRED、AGENT_UNAVAILABLE，另沿用身份／scope／读取／生命周期码。
调用后的同步throw、reject（含accepted:false或RPC超时）、close、断连／实例失效均
unknown；cause为SEND_FAILED或已有生命周期码，不按异常文本推断拒绝／副作用。
SDKresolve与close交错时仍unknown，但保留已观察sdkResolved；迟到resolve不修改已返回
结果，底层晚reject有处理器。unknown绝不能触发自动重发或生成新messageId重试。

SDK ACK仅按requestId匹配、不核真实ACK agentId且丢弃payload；输出agentId/messageId
是调用前冻结意图，不能称为ACK携带或已验证的因果关系。acknowledged不是模型执行、
完成或receipt查询。SDK默认RPC等待60秒；connected无ACK可pending至超时，断连立即
reject（与create保留pending不同）。没有新增send timer；Python进程budget不变，无完整
envelope的终止由server持久unknown。close不证明远端取消或未接收。

interrupt可能取消运行turn；steer也可能在provider不支持时取消重启。UI后续须显式
选择，不能承诺不打断。公共接口无receipt query／原子busy拒绝；当前历史投影仍不含
messageId，关联证据另切片处理，不能用相同正文或offset猜身份。

合成stdout：

```json
{"result":{"status":"acknowledged","agentId":"b7abb649-7b0d-4ead-9368-b2a4c7c58209","messageId":"2ac576eb-a0ad-4f85-8bf7-53d12a67c080"},"release":{"status":"unavailable","code":"CLOSE_FAILED"}}
```

```json
{"result":{"status":"unknown","code":"SEND_OUTCOME_UNKNOWN","cause":"CONNECTION_UNAVAILABLE","agentId":"b7abb649-7b0d-4ead-9368-b2a4c7c58209","messageId":"2ac576eb-a0ad-4f85-8bf7-53d12a67c080"},"release":{"status":"ok"}}
```

```json
{"result":{"status":"not_attempted","code":"PERMISSION_REQUIRED","agentId":"b7abb649-7b0d-4ead-9368-b2a4c7c58209","messageId":"2ac576eb-a0ad-4f85-8bf7-53d12a67c080"},"release":{"status":"ok"}}
```

可识别sendAgent输入在解析／白名单失败时not_attempted/INVALID_INPUT，零SDK；非法
JSON／UTF8或整体超限无法识别command，沿用unavailable/INVALID_INPUT。result与release
独立。编码超限／失败将acknowledged或send unknown改为SEND_OUTCOME_UNKNOWN，保留
预定agentId/messageId、已知sdkResolved及实际release；not_attempted保留未调用事实和
合法身份。输出无凭据、text、私有快照、原异常。
