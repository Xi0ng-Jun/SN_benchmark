# Paseo 服务端连接器：读取、无 prompt 创建与单次发送

供后续 FastAPI 接入使用的 TS 模块，负责公共 SDK 的连接、释放和目录读取。
增加现有 Agent 最小快照、历史页读取，以及供 Python 调用的一次性 Node JSON CLI。
配置由服务端持有；不要在 Expo／浏览器中导入此包，也不要将配置序列化到响应。
另提供一次无 prompt 创建和一次显式行为的发送，业务结果与释放结果独立。
没有常驻进程、HTTP 路由、其他 Agent 控制、MCP、Job 或显式事件订阅接口。

## 安装、构建与调用

在仓库根执行：

```bash
npm --prefix apps/sn-agent-bridge ci --ignore-scripts --no-audit --no-fund
npm --prefix apps/sn-agent-bridge run build
```

本 package 要求 Node >=22.18（包括定向测试使用的 TS stripping）。
本目录内可按包名导入。其他 Node 服务可在构建后使用
`npm install /absolute/path/to/apps/sn-agent-bridge`，再按包名导入；锁文件固定了
SDK 依赖。本包为私有本地 package，不需要发布。构建输出在被忽略的 `dist/`。

```ts
import { createAgentBridge } from "@silicon-notebook/sn-agent-bridge";

// serverConfig 来自服务端登记配置／秘密管理，不能来自浏览器请求。
const bridge = createAgentBridge({
  url: serverConfig.paseoWebSocketUrl, // ws(s)://.../ws
  password: serverConfig.paseoPassword,
  connectTimeoutMs: 10_000,
});
try {
  const connection = await bridge.connect();
  if (connection.status !== "ok") return connection;

  const providers = await bridge.listProviders({ cwd: serverConfig.repository });
  const models = await bridge.listModels("codex", { cwd: serverConfig.repository });
  const workspaces = await bridge.listWorkspaces({ limit: 50 });
  // 有 nextCursor 时再次读取；这里返回一页，不隐式扫描整个 daemon。
  return { providers, models, workspaces };
} finally {
  const release = await bridge.close();
  // 调用者需处理 CLOSE_FAILED；结果里没有 SDK 的原始异常。
  if (release.status === "unavailable") handleReleaseFailure(release.code);
}
```

FastAPI 同源入口可启动 `node apps/sn-agent-bridge/dist/cli.js`，将服务端配置和
一个白名单命令写入 stdin 并关闭输入，读取 stdout 的一份 `{result,release}`。
确切命令、DTO、失败语义及合成样例见 [PROTOCOL.md](PROTOCOL.md)。没有改 Python
或浏览器；调用者负责子进程 timeout、目标授权和浏览器字段投影。

## 接口与失败语义

包根导出 `createAgentBridge(config): AgentBridge` 和对应 DTO／结果类型。

| 方法 | 成功内容 | 公共 SDK 方法 |
| --- | --- | --- |
| `connect()` | `{ status: "ok" }` | `connect()` |
| `close()` | `{ status: "ok" }` | `close()` |
| `listProviders({ cwd? }?)` | `entries, generatedAt` | `providers.snapshot()` |
| `listModels(provider, { cwd? }?)` | `entries, provider, fetchedAt` | `providers.listModels()` |
| `listWorkspaces({ cursor?, limit? }?)` | `entries, pageInfo` | `workspaces.list()`，不带 subscribe |
| `readAgent(agentId)` | `agent: AgentSnapshot` | `agents.ref(id).refresh()` |
| `readTimeline(agentId, options?)` | 历史页字段及投影后的 `entries` | `refresh()` 校验身份后 `timeline.refetch()` |
| `createAgent(options)` | `not_attempted / created / unknown`，见协议 | workspace `refresh()`、既有 ID `refresh()`、workspace `agents.create()`、返回 handle `refresh()` |
| `sendAgent(options)` | `not_attempted / acknowledged / unknown`，见 A07a 协议 | 完整 ID `refresh()`、本地目录核对、恰一次 handle `send()` |

成功空目录返回 `{ status: "ok", entries: [], ... }`。
provider 的 `status` 保留 `ready/loading/unavailable/error`，不等待发现完成，
不把 loading 解释为空模型表。模型响应的 `error` 或缺少 `models` 是
`CATALOG_UNAVAILABLE`；确实返回 `models: []` 才是成功空目录。
workspace 默认每页 50，保留 `nextCursor/prevCursor/hasMore`；目录选择与隐含过滤
遵循已安装 SDK／daemon 的默认行为，不声称枚举所有隐藏／归档 workspace。

确定不存在返回 `{ status: "missing", code: "AGENT_NOT_FOUND" }`；其余失败为
`{ status: "unavailable", code }`，不能把读取失败包装为空历史：

| code | 含义 |
| --- | --- |
| `INVALID_CONFIG` | URL 非 ws/wss、非 /ws 路径、含 URL 用户信息，或连接超时值无效 |
| `NOT_CONNECTED` | 尚未连接或上次连接失败后的实例已释放 |
| `CONNECTION_UNAVAILABLE` | 已建连接失效，或读取属于被替换的连接 |
| `CONNECT_FAILED` | SDK 构建／握手失败；临时实例已尝试释放 |
| `READ_FAILED` | 目录读取／投影失败；不传播原始异常 |
| `CATALOG_UNAVAILABLE` | 模型 RPC 返回错误或没有模型目录 |
| `CLOSED` | 本连接器已终止；在途 connect/read 会收到此结果 |
| `CLOSE_FAILED` | SDK 资源释放失败；不能宣称清理成功 |
| `INVALID_INPUT` | 窄命令或读取参数无效 |
| `IDENTITY_MISMATCH` | SDK 返回的完整 Agent ID 与请求不一致 |
| `RESPONSE_TOO_LARGE` | 历史页超过 100 条或 CLI 序列化输出超过 4 MiB |
| `INTERNAL_ERROR` | CLI 构造／执行边界发生未分类异常 |

`connect()` 合并同时发起的握手；握手失败且旧实例确认释放后才允许显式重试，
使用新 SDK 实例。SDK `close()` 拒绝则保留未确认释放的实例与失败状态，
后续 connect/read 返回 `CLOSE_FAILED`，close 也持续报告该失败；不创建替换实例，
不隐式再次调用失败的 SDK close。调用者仍需处理旧资源的未确认释放，构建新
连接器不会消除这一事实。
不自动重连。`close()` 幂等且终止本连接器，在途本地等待被取消；读取迟到结果
丢弃，已开始创建／发送返回 unknown，不能推断远端未创建／未接收。
重新使用需构建新连接器。普通读取失败不会擅自改变业务状态或启动重试。
关闭取消的是本地等待，实际 transport 的释放依赖公共 SDK `close()`。
握手等待结束后，open 与最终共享 connect promise 均在成功发布前核对终止状态、
SDK 实例归属及实际连接状态；读取也在发布前核对终止状态。

SDK 日志在连接边界关闭；返回值不包含 URL、凭据、连接 reason、provider
错误文本、任意 metadata、脚本和 daemon config。目录名称与 workspace 的
`directory` 是真实服务端资料，后续 HTTP 层需按实际访问范围决定投影与展示。
此模块本身没有认证或 workspace 授权过滤，不能直接当浏览器网关使用。

## 实际依赖与证据

2026-10-11 本机核对：Node 24.20.0、npm 11.19.0、TypeScript 5.9.3。
安装来源为 npm registry 的 `@getpaseo/client@0.11.2`，不是 file 链接或复制
Paseo 私有 transport；`@getpaseo/protocol/relay` 均为 0.11.2。
`package-lock.json` 记录 tarball URL 与 integrity。

已检查安装包的 `package.json`：包根 `.` 映射到
`dist/index.d.ts`／`dist/index.js`。运行时包根导入后，factory、connect、close、
providers.snapshot、providers.listModels、workspaces.list 均为 function。
`agents.ref(id).refresh` 和 `timeline.refetch` 也经发布包声明及运行时核对。
离线公共 SDK WebSocket 替身验证 workspace `agents.create` 传递预定 UUID、幂等键、
workspace placement 与闭合配置，省略 prompt；旧能力分支不发 caller-ID 创建 RPC。
类型检查使用安装包公开声明，替身也只实现这些声明中使用到的接口。
没有导入 `@getpaseo/client/internal/*`。

源码参考为 `/home/wabiwabi/paseo/packages/client/README.md`、`src/index.ts`、
`public-docs/sdk/events.md` 与 providers/workspaces 文档。本地 checkout 与同版本
发布包不保证内容完全相同；实现按实际 npm 导出核实，不依赖本地 checkout
较新文档中的 background workspace 等额外能力。

## 定向验证与边界

```bash
npm --prefix apps/sn-agent-bridge run typecheck
npm --prefix apps/sn-agent-bridge test
npm --prefix apps/sn-agent-bridge run build
npm --prefix apps/sn-agent-bridge run test:sdk
```

最终验证结果记录在 [IMPLEMENTATION.md](IMPLEMENTATION.md)。
覆盖空目录／未连接区分、分页、目录字段投影、模型错误与缺字段、异常脱敏、
失败后的释放及重试、关闭在途握手／读取、旧连接迟到结果、清理失败、配置校验。
独立评审后补了 4 个先红后绿的反例：握手／释放双失败后不能遗忘资源，
两个明确微任务窗口不能发布连接成功，保留待释放实例时读取不能在关闭后成功。
smoke 从本包公共入口加载真实 SDK，用独立临时 TCP 端点拒绝 WebSocket 握手，
验证连接失败与关闭，测试进程正常退出；没有连接真实 daemon 或读取真实凭据。

用户转述：集成人已通过本地公共入口 connect/listWorkspaces/close，目录 count=3，
当前 workspace 匹配。本实现者未重复该验证；它不代表 providers/models 真实目录
已验收。自身离线替身与 smoke 不证明真实 daemon 的认证和成功握手。
读取超时使用 SDK 的默认预算（发布包模型发现为 90 秒），没有新增逐请求
timeout/cancel API；调用者可 `close()` 结束整个连接器。没有持续目录订阅或缓存。
本段记录最初只读连接器交付。后续集成人已完成真实 Agent／历史读取及无 prompt 创建的独立临时 state 验收，实际范围见[工作台阶段记录](../../docs/superpowers/plans/2026-10-11-sn-workbench-agent-collaboration.md)，不据此推断其他部署可用。
预览服务和 `var/sn-workbench` 未触碰。独立只读评审由统筹安排。

初始事实及执行边界见 [IMPLEMENTATION.md](IMPLEMENTATION.md)。

## A07a 单次发送

`sendAgent({agentId,expectedCwd,text,messageId,activeTurnBehavior})`仅用公共根handle
send，所有字段必填，无任意options。ID为完整标准UUID，行为明确interrupt/steer，
text保留原值，最多16384 UTF-16 units且32768 UTF-8 bytes，拒绝全空白／NUL。
路径保留尾空格，realpath/stat核对实际现存目录；已有Task-session不要求A06创建绑定，
所以不要求remote workspaceId。服务器持有授权、配置、冻结消息意图及本地cwd范围。

precheck严格archivedAt=null、已知空pendingPermissions、已知activeTurn形状，保守
允许idle/running，拒绝权限attention、providerUnavailable=true、其他状态及缺字段。
预检与send非原子，不能避免检查后远端状态变化，也不提供原子reject-busy或Agent沙箱。
steer可能取消重启；工作台UI要求显式选择行为，不能承诺不打断。

acknowledged仅表示SDK Promise<void> resolve。返回ID是冻结意图身份；SDK仅按requestId
匹配ACK、不核返回agentId且丢弃payload，不提供receipt查询或执行完成证明。调用后
失败统一unknown，不解析异常文字、不重发、不调run/waitForFinish。SDK默认60秒，
断连立即reject，无新增send timer。result与release独立，SDK resolve与close交错／编码
失败保留最小sdkResolved观察，释放成功不消除unknown。

离线public SDK WebSocket替身与实际CLI子进程覆盖wire、badACK、断连、60秒虚拟超时、
严格字段、范围／权限／归档、微任务close交错、意图防突变和释放失败；见IMPLEMENTATION。
以上是 bridge 切片交付时的离线证据。后续 Python store／adapter／HTTP 和工作台父状态分别交付与独立评审；本包不持有浏览器请求 key 或本地意图恢复锁。完整接线及真实发送验收结果以[工作台阶段记录](../../docs/superpowers/plans/2026-10-11-sn-workbench-agent-collaboration.md)为准。
