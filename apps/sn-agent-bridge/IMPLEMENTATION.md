# A02a／A02b 事实与执行记录

初始核对（2026-10-11）：HEAD bdc22e253041ac1d40defeaaa3b7357dcd2cf921，
`apps/sn-agent-bridge/` 不存在。因此本目录交付文件全部新增；整仓已有 dirty
文件不是本轮修改。仅拥有本目录，不提交、不改 Paseo、不改预览 state。

切片：Node >=22.18 服务端公共 SDK 连接器。配置由服务端传入；没有浏览器入口、
HTTP 路由或进程管理。FastAPI 不能直接导入 TS；后续接入需要 Node 执行边界，
但本轮只交模块，不预定常驻进程或 IPC。

执行顺序：

1. 核对本地公共源码与 npm 0.11.2 实际导出，验证包根可加载。
2. 先写资源释放、空目录、失败脱敏与关闭竞态的定向测试并确认失败。
3. 实现 connect/close 与三种只读查询；生命周期只归连接器所有，
   关闭终止本实例，连接失败且旧资源确认释放后可用新 SDK 实例重试，不自动重连。
4. 类型检查、定向测试、构建及包根导入验证；回报统筹并请求安排独立评审。

接口：`createAgentBridge(config)` 返回 `connect/close/listProviders/listModels/listWorkspaces`。
结果为 `status: ok` 或 `status: unavailable`＋固定错误码；成功的空数组不表示故障。
workspace 返回一页及 nextCursor，provider 保留 loading/unavailable/error 状态，
模型 RPC 的 error 或缺少 models 不能包装为空成功。目录是当次读取，不维护第二份缓存。

边界：仅使用包根 API；不读 daemon config、不暴露 SDK 对象、不记录原始异常。
对外字段显式投影。无 Agent 会话、发送、控制、MCP、Job 或订阅。

核实结果：npm 的 `@getpaseo/client@0.11.2` 可安装，包根及所需方法可加载。
发布包分页对象要求 limit，本模块默认 50；替身使用公开读取签名而非订阅重载。
SDK 的 close 会清空待连接回调，因此连接器自有取消信号，且每次读取结束后
移除取消监听，避免长连接累積等待回调。没有修改 SDK。

测试先建立，初次运行因连接器模块尚未存在失败（模块加载阶段，非行为断言）。
实现后 11 个行为断言测试通过；公开包入口使用真实 SDK 的独立 TCP 握手失败
smoke 1 项通过。类型检查、构建退出 0。命令、接口示例和适用范围见 README。
未连接真实 daemon，未读 access-token 或真实密码，未创建业务 Agent／发业务消息。

## 独立评审后的生命周期修复

评审 118851f7 提出两项 Important。本轮仅修改 bridge.ts、bridge.test.ts、
README.md 与本记录；未扩 A02b。修复前 bridge.ts SHA256：
`fc131ad2cec812e3334077c5b767acb43e6812a756c3387fc3944a6ac99be0e4`；
原测试 SHA256：`2a6db2adeca84e0c0293b9beb6cd80dcf09d047431b858235b1c9565ecb18775`。
这些是本轮进入时的文件事实，不以整仓 HEAD／dirty 作为修复基线。

先建立反例，运行：

```bash
cd apps/sn-agent-bridge
node --experimental-strip-types --test --test-name-pattern='unconfirmed release|close microtask' tests/bridge.test.ts
```

旧实现 0/3 passed，均在行为断言失败：

- 握手拒绝且 SDK close 拒绝后，retry 的 factory 调用累计为 2（应为 1），
  bridge.close 返回 ok（应为 CLOSE_FAILED）。原因是失败清理也清空 current。
- 两步 queueMicrotask 明确将关闭插在 untilClosed 兑现与 open 恢复之间，
  观测顺序为 bridge.close → sdk.connected → sdk.close，connect 返回 ok。
- 三步 queueMicrotask 将关闭插在 open 成功与外层 connect 恢复之间，
  观测顺序为 sdk.connected → bridge.close → sdk.close，connect 仍返回 ok。

先修资源事实：拒绝释放时保留 current 并锁定 releaseFailed，阻止替换与读取，
close 持续返回 CLOSE_FAILED。此时 1/3 passed，两个微任务反例仍失败。
再修发布边界：open 成功携带内部 SDK 实例身份；最终共享 promise 在发布前
检查 closed／releaseFailed／同一实例及 connected，清理 pending 与发布同步执行，
不在其后追加 return-await/finally 间隙。公共返回仍只有 status/code，不泄露 SDK。

保留待释放 current 也改变了读取的状态窗口，因此新增读取反例；三步微任务
让关闭位于 untilClosed 与 read 发布之间，修复前得到 ok＋迟到空目录。
read 发布前补 closed/releaseFailed 检查后得到 CLOSED。这是同一生命周期边界。

最终定向 4/4 passed；完整本 package 替身 15/15 passed；typecheck/build 退出 0；
独立本地 TCP 握手失败 SDK smoke 1/1 passed。无 sleep／真实 daemon 请求。
用户转述集成人的公共入口连接／workspace 读取／关闭成功（count=3，当前 workspace
匹配）仅作为独立集成证据记录，本实现者未重复。请原 reviewer 118851f7 只读复核。

## A02b 进入事实与范围

用户转述原 reviewer 已复核通过 A02a 两项 Important 和读取竞态；另有 Minor：
read catch 缺少 releaseFailed 优先级。本轮先补同边界反例修正，再实施已接受的
只读 readAgent/readTimeline 与一次性 Node CLI；不修改 Python/App 或其他目录，
不自行安排评审 Agent，不扩展创建、发送、订阅、Job、MCP 或 HTTP 网关。

A02b 进入时现有文件 SHA256（本目录仍是未提交文件，不取整仓 HEAD 当基线）：

| 文件 | 初始 SHA256 |
| --- | --- |
| src/bridge.ts | 1bd258d2fa068041743f765d53a994d7cf40203a62c31b57ac91556b85d56746 |
| src/index.ts | 8f8efc477a4e75ed55fe9d9027e253861147caaef0bb59591be744a3fe9d53bb |
| src/types.ts | 66963c5fbec35119f885f6e4d587bad0c8d2739a3e3cc141cb6c20a5b39184fe |
| tests/bridge.test.ts | 9e449d869253c378701986b60004e2e56daf82aff05c31fc0790b79b5c8f2b71 |
| package.json | 89d02eb714c54ff69e8b4c07a83e6be704788c53f713bc28bcf4f31d6fc13e8e |
| README.md | a43c8c2354f8d1145a9b0b1c2bd43e0c4f3aded1f4f7d9b061d28332b90cd293 |
| IMPLEMENTATION.md | 5e90cc299f786b89c9e26da9d7c8bae161389bc99a88576a08ddf822a9060a23 |

本轮新增：src/input.ts、src/read-projections.ts、src/cli-protocol.ts、src/cli.ts，
tests/read-fixture.ts、tests/reads.test.ts、tests/cli.test.ts、tests/cli-process.test.ts，
PROTOCOL.md 和 examples/ 四份合成 request/response。其他既有文件和锁文件未修改。
构建 dist、已安装 node_modules 是忽略目录内的本地工件。

## A02b 源码依据与契约

实际 npm `@getpaseo/client@0.11.2` 包根声明：agents.ref(id).refresh() 返回
`{agent,project}` 或 null；timeline.refetch(options) 返回完整 FetchAgentTimelinePayload。
仅使用包根 API，未复制或导入 private transport。发布包运行时也核对 refresh/refetch
均为 function，bridge 包根七个方法均可加载，没有连接真实 daemon。

SDK read limit=0 为不限制，本模块 limit 默认50且限制1..100。请求包含 direction、
cursor 的 epoch/seq、projection；输出保留实际分页及恢复元数据，不从 offset 构造
身份、不改变响应 projection。源码观察到较新 daemon 可返回 projected，即使请求
canonical；此接口按实际结果报告。SDK 可将短 ID 解析到完整 ID，因此 refresh 与
timeline 的返回 ID 均严格核对请求，防止读到另一会话。

Agent 白名单保留真实 cwd、最小 activeTurn 和公开 capability flags；未知可选字段
保持省略，未知 capability 保持 null。timeline 只保留 user/assistant 文本、其他
活动类型及必要标题。长字符串有明确 truncated/originalLength/utf16 标记，未知
item 映射 activity；不发完整 tool args/output 或 private metadata。

CLI config 仅服务端提供，严格白名单 op/参数；输入64 KiB、输出4 MiB，超限整份
result 失败并保留 release。connect 成功执行一个读取，finally close；成功读取与
资源释放分开报告。无新增运行时依赖，无进程池或网关，子进程预算由 Python 后续负责。
确定协议及合成样例见 PROTOCOL.md；package 的 files 包含 dist/cli.js、协议及样例。

## A02b 反例与定向验证

先红后绿事实：

- Minor：在途 read 拒绝，重连释放旧 SDK 也拒绝；旧 catch 返回
  CONNECTION_UNAVAILABLE，反例要求 CLOSE_FAILED。加入 releaseFailed 优先后通过，
  未改变既有失败资源策略或隐式重试行为。
- 初始读取替身7项在行为断言失败，实现投影／缺失／分页／有界文本后通过；初始
  CLI行为4项失败、1项通过，实现单命令与 finally close 后通过。
- 明确微任务反例：refresh 的迟到兑现在 close 后仍启动 refetch；在两次读取之间
  补终止、释放失败及实例归属检查后，不再启动后续 RPC。
- SDK 没有提供 workspaceId/archivedAt/activeTurn 时，原投影人为填 null，反例失败；
  改为保持缺省后通过。
- 未知 future_event 原投影导致 READ_FAILED；反例要求 activity 和真实不连续来源范围，
  加有界 sourceType 后通过，私有 payload 未输出。

最终实际运行（2026-10-11，仅本 package）：

```bash
npm --prefix apps/sn-agent-bridge run typecheck
npm --prefix apps/sn-agent-bridge test
npm --prefix apps/sn-agent-bridge run test:sdk
```

typecheck（含 build）退出0；test（含 build）36/36 passed：生命周期16、读取11、
CLI协议6、CLI子进程3；SDK smoke 1/1 passed。CLI子进程使用真实 dist/cli.js 验证
坏JSON、多个文档、非法UTF-8、溢出、非法操作／配置和独立TCP握手拒绝；stdout
只有一份固定JSON，stderr为空，异常／凭据 sentinel 不回显。
分页替身保留 disjoint ranges、reset/staleCursor/gap、实际 projection 与游标；
空历史不制造 offset，过长文本避免切断代理对，超大页／输出不假装完整。

包根运行时核对实际 SDK version=0.11.2、根 exports 的 dist/index.js/index.d.ts、
refresh/refetch 及 bridge 七个方法；只构造未连接对象并关闭。npm pack --dry-run
退出0，包含21项，其中有 dist/cli.js、声明、PROTOCOL.md 和四份样例，没有打包凭据。

未验证：本轮没有真实 daemon Agent/历史成功读取；未来未知 item 是否能通过该版本
SDK解析层尚不确定，替身只证明进入投影层后的兼容。输入输出界限不限制 SDK接收及
序列化前内存，刷新与历史页也非原子快照。读取沿用SDK默认timeout，未加独立cancel
或进程终止后释放保证。Python目标授权、Task关联、浏览器投影和端到端验收由统筹
在独立只读评审后接线。未读任何已有凭据，未写预览记录，未发送业务Agent消息。

## A02e：owned 历史读取的目录条件

进入依据（用户转述）：A02b freshreview8cf 通过，A02a catch Minor118 闭合；
Python→CLI 对完整 Agent ID 的 readAgent 和 timeline limit2 均 ok/releaseok。
本轮不重复真实 daemon 验证，仅在本目录增加一个服务端目录读取条件。

进入时 SHA256（仍不以整仓 HEAD／dirty 作为切片基线）：

| 文件 | 初始 SHA256 |
| --- | --- |
| src/types.ts | 37cd9228417beb6380309030972adb1d020797673cd926708ace861d2d555b9b |
| src/input.ts | 7972c67ce9b75dffd68b41437e332c396c5dd29e031f2f7022828c69e3bdd054 |
| src/bridge.ts | ac0de86a07d985b1ed92857966dc665f4a76aaf09022a095f291846d38ce42f7 |
| tests/reads.test.ts | 4157ea2eb7195275294fe516396c44204be2a1e7a6892ab3b926a2c2997e3604 |
| tests/cli.test.ts | 2a0f53168cdfedb728c6e7e8d6f6f6760db4c4a8430cc7c0dda87eb425b47fd9 |
| PROTOCOL.md | fc901bc3ffc17d7b1c8a7684ac3d0e22256f96f78e9b53c26667ec28a7b1d56b |
| IMPLEMENTATION.md | b7eb003c4ea3d71002bcc65d8f0fdf6f873af3b62a1965adf0b79d08c6f0afe4 |

只修改表中七个文件，无新增依赖、op、私有 SDK 导入、HTTP 或资源恢复策略。
`readTimeline(id, {expectedCwd?, ...options})` 和 CLI readTimeline 唯一新增
expectedCwd 字段。它由 Python 注册 Task workspace 生成，不能让浏览器自由透传。
无条件调用保持已有语义，不进行目录 IO；readAgent 的原始 cwd 投影未改变。

输入先校验 expectedCwd 是有界的绝对路径且不含 NUL。refresh 返回 Agent 完整 ID
并核对生命周期后，以 Node 公共 fs.realpath/fs.stat 确认 expectedCwd 与 agent.cwd
是本机现存目录且 canonical 路径相同，再发 refetch。expectedCwd 从 SDK 参数剥离。
不同目录固定 SCOPE_MISMATCH；条件路径无效／缺失／非目录／解析失败固定 INVALID_INPUT；
实际 cwd 无法解析为绝对目录固定 READ_FAILED。refresh 确认不存在仍为 AGENT_NOT_FOUND，
所有错误均不回显路径或原异常。路径 IO 后重新检查 closed/releaseFailed/实例归属和
connected，close 后的迟到目录检查不能启动 timeline RPC。

该条件不是沙箱或原子锁。refresh／目录检查／refetch 非原子，检查后 cwd 或符号链接
仍可能变化；需要 bridge 主机能解析这些目录。跨页每次调用由服务器重新提供
expectedCwd、固定 projected/limit50 和实际 cursor；本模块不缓存目录身份。
成功历史 DTO 和 CLI 的独立 result/release 不变。合成 command 与失败响应见 PROTOCOL。

反例和证据：

1. 先添加8项定向测试（目录6、CLI2）；旧实现6项在行为断言失败、2项已通过。
   旧实现拒绝新增字段，不能返回预期的 ok/missing/SCOPE_MISMATCH；验证不是仅类型错误。
2. 使用本目录内独立临时目录、目录符号链接与文件验证 canonical 同目录、不同目录、
   相对／空／null／NUL、缺失路径、非目录和缺失 Agent；临时目录测试后清理。
   用窄 realpath/stat 替身产生权限／stat 异常，返回固定码且没有原异常或路径。
3. 关闭反例用显式 promise 暂停 realpath；close 后放行，stat 使用已知目录结果使
   迟到工作完全处于受控微任务队列。临时删除路径 IO 后生命周期检查时，该反例
   实际捕获1次关闭后的 refetch（expected []）；恢复检查后不再请求，pending 为 CLOSED。
4. CLI 增补 mismatch 行为测试，确保即使 releaseok 也不会返回授权范围外历史。
   matching case 确认 expectedCwd 不出现在 SDK options；其他四个 op 不接受该字段。

最终定向验证命令：

```bash
npm --prefix apps/sn-agent-bridge run typecheck
npm --prefix apps/sn-agent-bridge test
```

typecheck（含build）退出0；完整包内45/45 passed，其中新增目录6、CLI3。
包内原有子进程握手拒绝测试仍使用独立TCP端点；未连接真实 daemon、读取凭据、
创建／发送 Agent，未写预览、改 Python/App/Paseo/SN 或提交。本切片完成通知统筹，
由统筹安排独立 Codex 只读评审，本实现者不自行委派或扩下一项。

## A02e 原评审 Minor：尾空格目录

用户转述 reviewer 8cf 无 Important，另实测 expectedCwd 指向现存 `owned ` 目录时
被 string() 的首尾空白规则误拒，符号链接路径却可成功。只修 expectedCwd 的输入
边界：保留路径原值，校验 string／8192长度／非空／绝对／NUL；现有 realpath/stat、
scope 与生命周期逻辑不改。opaque ID 校验不改，没有创建／发送扩展。

进入时 SHA256：

| 文件 | 初始 SHA256 |
| --- | --- |
| src/input.ts | 5252f2d50b7b7caf39777c9004bc6ef63a990f402a9bf06e978a5aee354ff3bc |
| tests/reads.test.ts | 989557f7766bcc28b01698ee6658e32b44973f437192516efca99cb71623e597 |
| tests/cli.test.ts | c5bc9c91d1b5df0d94ce87cf48387fa366a100d065cb6a14de65bf0692f0a706 |
| IMPLEMENTATION.md | ac4d667bae9dc2a228dd8b3ad42bd7b75192528a6f3f5934237e4c0740f20db9 |

在本目录的独立临时状态建立真实、不同的 `owned` 与 `owned ` 目录，不 mock路径 IO。
模块反例先验证到尾空格目录的 symlink 可用，再验证原路径可用；CLI反例让请求经
JSON stringify/parse 后执行。两个反例都要求尾空格路径成功且不得传入 SDK options，
并将实际 cwd 改为 trimmed sibling 要求 SCOPE_MISMATCH、不发 refetch，防止偷偷 trim
后检查错目录。SDK 使用只读替身，不连接真实 daemon；CLI另校验实际 releaseok。

先红：仅运行 trailing-space 两项，旧实现0/2 passed，均在状态断言失败
（unavailable，而非预期 ok）；模块symlink对照已成功。仅局部改 input.ts 后验证：

```bash
npm --prefix apps/sn-agent-bridge run typecheck
cd apps/sn-agent-bridge
node --experimental-strip-types --test tests/reads.test.ts tests/cli.test.ts
```

typecheck/build退出0；受影响读取／CLI定向套件28/28 passed。临时目录已清理。
本轮仅 input.ts、两个测试文件及本记录改变；未重跑真实daemon、读取凭据、写预览、
修改Python/App/Paseo/SN或提交。完整通知统筹，请原 reviewer 8cf 只读复核，本切片停边界。

## A06a：一次无 prompt 创建（待独立只读评审）

进入时按当前内容保存25份 owned 文件副本与 SHA256 manifest，位于本目录忽略的
`node_modules/.cache/a06a-baseline-20261011/`，未按整仓 HEAD／既有 dirty 归因。
manifest SHA256 为 `7523f4182554114c4111719cdf4bf8b117fecf9ada930a69b88e41630f87ea4c`。
本轮既有文件初始 SHA256：

| 文件 | 初始 SHA256 |
| --- | --- |
| src/bridge.ts | e3abc05b855ca67a811ed9a3c7cff199e19c83855145ed1b1e2b4759b7b41d78 |
| src/types.ts | a6a19552180090ca1e8651df976782a2144ff8e6f94e4b10406069778f199868 |
| src/input.ts | 7b92f82a24ae12105aa1e6eb8ec721585d2be8291deffc616e59868f58c74cea |
| src/index.ts | 783fa531bfc2a1ad7a68af475d90c7acfe242f513347a658d6ec2260d07312a9 |
| src/cli-protocol.ts | e594138742863d782b6fe71a85c0ce8fdf7f71b1ce4c4badf3301f62fba5f707 |
| tests/read-fixture.ts | c6e7c25bf44bcc1217f441dd2e2e86d4f3ea854bcc6ae3b762d194be22264bb2 |
| tests/bridge.test.ts | f0409872b0b3f5cbdcfc099dc941e32e6f0ff5ad3532942dd8da1a9c77c9f1b3 |
| tests/cli-process.test.ts | df049408ed3f4ec73e6e0eef1654ad4424802ac442c4bc79e86481576d790558 |
| README.md | 7cfb372fad85aad9020a24e6da9441805bfa2efbcc2d61c6b910c38dbd20d6bb |
| PROTOCOL.md | c2b8262af160e1e98bd026a4f33c3acb8a7940e33725811feefb175f26808590 |
| IMPLEMENTATION.md | 149a4f862bc8712dccd65d2a1b186ab52f7e8e3b5af1ea75813c53dd0840e1bc |

新增仅 tests/create.test.ts、tests/public-create.test.ts；既有测试 fixture 增加公共
workspace ref 类型槽。package.json／锁文件／依赖不变，业务模块只新增闭合
createAgent与同形CLI op。输入、结果、stdout合成样例及服务器责任见 PROTOCOL A06a。

发布包依据：实际安装 `@getpaseo/client@0.11.2` 的根 exports 指向
dist/index.d.ts／dist/index.js。公共 PaseoWorkspaceHandle.agents.create 接收
PaseoWorkspaceAgentCreateOptions（去除 cwd 的公共 create options）；预定 UUID 与
幂等键存在于公共声明，workspace handle负责附加cwd与workspaceId。无private导入。
离线测试通过根 createPaseoClient、替换测试环境 global WebSocket，在现代
creationLifecycle 分支捕获一次真实 SDK wire request：完整 agentId、key、闭合config、
workspaceId/cwd/title准确，无prompt/images/attachments/env/parent；返回handle ID匹配。
旧host分支拒绝callerID，且零legacy／modern create RPC。最初测试尝试的
webSocketFactory虽然运行时可用，但不在该包根配置声明中；已改成测试环境WebSocket
替身，最终typecheck验证根声明，无生产transport注入或新增依赖。

行为：先refresh完整workspace、核对未归档／已知活动桶／canonical现存目录，再查
完整预定Agent ID。预检后检查终止／释放失败／实例归属／正在释放／connected，恰一次
workspace agents.create。调用开始前 not_attempted，开始后不完整结果统一unknown；
成功handle与refresh核对id/cwd/workspaceId/provider/model，保留实际归档／status／turn，
已观察但未idle或缺就绪字段返回created、ready=false。不声称验证mode/effort。
release独立；close失败、输出编码失败均不能抹掉创建事实。无重发／归档／删除策略。

真实红绿及反例：

- 初始10项创建／CLI测试0/10通过：8项缺createAgent方法，CLI创建及编码fallback
  在行为断言上返回旧unavailable而非created／unknown。实现后这10项均通过。
- 增补显式微任务、实例替换／释放失败、预检目录IO和intent防调用者突变反例。
  最后预检race→untilClosed→create续体之间close，SDK release仍只是排队，零create。
  临时局部去掉最后两处生命周期检查，该微任务测试0/1通过，返回unknown而非
  not_attempted，说明确实跨过创建边界；立即恢复源码并重建，最终测试通过。
- mismatch／missing／archiving／无效workspace桶／现存ID／别名／读取异常零创建；
  真尾空格目录及symlink匹配；无效／不存在／非目录path与close目录IO拒绝。
- SDK调用reject／同步throw／close、handle不符、readback失效／缺身份／目录或
  provider/model不符／断连均unknown、一次create，不按异常文字判副作用。
- nonidle／activeTurn／归档／缺就绪字段保留created；close读回／目录检查保留handle
  事实但不得ready。created后release失败保留结果，超限／循环编码fallback保留
  unknown+plannedID+observed handle+实际release。CLI子进程拒绝12类额外字段，stdout
  一份JSON、stderr为空、sentinel不回显；无效URL为not_attempted，实际release独立。

最终实际命令（2026-10-11）：

```bash
npm --prefix apps/sn-agent-bridge run typecheck
npm --prefix apps/sn-agent-bridge test
npm --prefix apps/sn-agent-bridge run test:sdk
```

全部退出0：typecheck含build；包测试64/64 passed（原47项＋创建14、公共SDK2、
CLI子进程1）；SDK smoke1/1 passed。包根离线加载另核对SDKversion0.11.2、根声明／
运行时exports、workspace.refresh／agents.create及bridge.createAgent均为function后close。
定向命令为 `node --experimental-strip-types --test tests/create.test.ts tests/public-create.test.ts tests/cli-process.test.ts`。

局限：没有真实daemon创建或请求、模型调用、凭据／预览访问；仅替身与独立拒绝握手
端点，不能代替真实创建验收。预检／create／readback非原子，ready只是当前快照。
公共现代SDK内部观察creation lifecycle，未新增订阅操作；reconnect=false。
创建无新增deadline，无完整stdout的子进程超时须由服务器持久unknown，不能重发。
callerID旧host拒绝仍保守unknown；close不证明远端取消。readDTO无receipt／幂等键，
不能证明intent全配置生效。server允许列表、intent持久化、unknown核对、Task关联及
Python/HTTP接线由统筹在独立Codex6.1 high只读评审后安排；本实现者不自行委派。
本轮停在此切片，没有修改其他目录或提交。

## A06a reviewer 9a16：断连等待反例已复现，公共入口缺口待确认

以下为暂停时记录；续作修复与验证见下节。当时尚未修复 Important，不能进入 Python
接线或宣称 green。按当前内容保存27份
owned文件副本及manifest至 `node_modules/.cache/a06a-review9a16-baseline/`；manifest
SHA256=`66a921e5a5476f58678727acfa841555fed3593a0b45d26d97d5a20966e2329a`。
进入hash：src/bridge.ts=`34fe69eb3810feacddd41ceb1afb764d8d02c6b452dfcffe2a387b49ce61a7a3`；
tests/public-create.test.ts=`7ce5fd790901b788babd32e5fe6f655daac3bbffce0f30455f3ec9f6f01249af`；
IMPLEMENTATION.md=`ae2f1711d1e7d9a83d6af42b41cbf7c6395b1f92bafc40997cb70215df26567d`。

仅修改既有 public-create.test.ts 与本记录，生产代码／接口／协议未改，无新增文件。
基于真实已安装0.11.2包根 createPaseoClient 与既有global WebSocket替身，补两个测试：

- SDK独立证据：连接现代creationLifecycle，公共agents.create无prompt且预定UUID/key，
  实际wire只发一次create；回执前触发WebSocket close，public getConnectionState确认
  disconnected。150ms与SDK promise竞赛仍pending，显式public close后才reject。
- bridge目标反例：公共SDK完成真实workspace refresh与UUID缺失预检后实际发一次create；
  回执前断连，150ms后仍pending，而规格要求无需close即unknown/CONNECTION_UNAVAILABLE，
  保留预定ID。测试明确为红，并在finally公共close清理SDK。替身声明
  workspaceMultiplicity/ownedSubscriptions，避免旧host workspace读取被转成Agent目录；
  创建之前另有500ms测试guard，防止fixture错误被误判为创建等待缺陷。

当前验证：`npm --prefix apps/sn-agent-bridge run typecheck` 退出0；
`node --experimental-strip-types --test tests/create.test.ts tests/public-create.test.ts`
17/18 passed，唯一失败是上述目标反例的still_pending；原创建14项全通过，公共SDK
4项中3通过、目标反例1失败。150ms仅为测试观测预算，不是实现轮询或新RPC timeout。

源码与声明限制：安装包根 dist/index.d.ts 的PaseoClient（约400行）只给
connect/close/ensureConnected/getConnectionState，没有subscribeConnectionStatus；
PaseoClientConfig也没有连接生命周期回调。运行时根对象检查subscriber为undefined。
subscribeConnectionStatus在dist/daemon-client.d.ts:634，而package.json只把该文件
暴露为 `./internal/daemon-client`；安装README明确internal是unsupported implementation
details。公共observeEvents/OwnedSubscription不是断连通知：owned.disconnected只清除
id/snapshot并推进generation，既不调用observer.error也不结束ready；不能当连接watch。
不依赖私有logger消息形状来伪造公开连接订阅。

已向用户提出约束确认：保持包根限制并由统筹解决SDK公共生命周期缺口，或明确允许
internal连接生命周期入口例外。没有授权该例外前不实施私有导入／轮询／SDK改动，
也不以Python timeout代替桥接规格。本次只形成真实反例与缺口证据；Important仍开放，
待用户明确入口后继续局部修复，再交原9a16只读复核。没有真实daemon请求／创建、
模型、凭据或预览访问，没有委派或提交。

### 9a16 局部修复：用户明确允许单次create本地状态timer

后续用户确认包根无连接通知，保持禁止internal导入／SDK/Paseo编辑，并明确撤销
之前额外的“SDK本地等待不准轮询”限制；只允许单次create in-flight期间可清理、
非RPC本地timer读取公共getConnectionState。本阶段按该授权继续，前节“缺口待确认”
是此前暂停时的事实，不是当前仍未解决的入口问题。

续作当前内容另保存27文件基线至忽略目录
`node_modules/.cache/a06a-review9a16-local-timer-baseline/`，manifest SHA256
`89666952071a127392075ea170c6e107d9b8e4bf52a3cd474bd1ff0bd4d61584`。

| 文件 | 本地timer阶段初始SHA256 |
| --- | --- |
| src/bridge.ts | 34fe69eb3810feacddd41ceb1afb764d8d02c6b452dfcffe2a387b49ce61a7a3 |
| tests/create.test.ts | 6fc71ad2653c8279c35f94c19e434d284e5caf05b563c3673881c1dfacd7e34e |
| tests/public-create.test.ts | 10648be786f5cac119f9ca1c11c76c94a402b03362e378f39931290df105d2ef |
| PROTOCOL.md | 4a6c623e92bb6fd848e934ccdc821a3be364d7562e767b43e9ae977da9e61771 |
| IMPLEMENTATION.md | b9f88629f61e903fd6a9a357c56ea25be4f833dfef3a96b35ab7c20bb3c35f31 |

仅以上五份既有文件改动，无新增／删除文件／依赖。生产仅bridge.ts新增
untilCreateSettled(client,operation)：100ms interval使用公共getter与既有
lifecycleCode检查归属／正在释放／connected，race仍沿用untilClosed与终止信号。
finally清理这次timer，Promise.race处理底层晚reject。只包装SDKcreate的等待，
不扩大到预检、路径IO、readback、目录或catalog查询；不修改reconnect=false、
单次调用、readiness、intent复制、observed安全事实或CLI编码fallback。
没有请求deadline／自动close／重发／新的释放策略。断连unknown先返回，CLI原finally
才close；release与result保持独立。协议补准确局限：仍connected却无回执可pending，
检测周期也受事件循环调度影响；Python进程budget不变。

反例：

- 保留前节真实SDK pending对照与bridge先红证据。新增timer归属和优先级两项在旧
  实现0/2通过（实际无timer／无tick）；实现后成功／reject／close／disconnect全部
  一个timer且恰一次clear，tick本身不发RPC，迟到reject经过下一事件循环仍无unhandled。
- 关闭与断连tick交错保持CLOSED，失败释放与tick交错保持CLOSE_FAILED；一create。
- 原公共SDK反例由still_pending转成unknown/CONNECTION_UNAVAILABLE，约一个100ms
  周期返回，显式close只用于后续独立清理，不是唤醒条件。
- 补公共SDK＋executeRequest两种release结果：wire create一次后回执前断连，CLI
  自行进入finally，实际SDKdisposed；unknown不变，release分别ok／CLOSE_FAILED，
  sentinel不回显，零重发。测试结束后的额外close仅失败清理，不参与成功断言。

另作局部反证：临时把调用点换回untilClosed（不接本地状态timer），重建后只运行
公共SDK的bridge断连与CLI finally两项，包含两个release子例共0/4 passed，全部捕获
still_pending（CLI500ms内无法进入finally）。立即恢复调用点并重建。SDK独立pending
对照保留为通过项，区别底层SDK限制与桥接修复，未修改SDK或按异常文本猜副作用。

最终实际验证：

```bash
npm --prefix apps/sn-agent-bridge run typecheck
cd apps/sn-agent-bridge
node --experimental-strip-types --test tests/create.test.ts tests/public-create.test.ts tests/cli.test.ts tests/cli-process.test.ts tests/bridge.test.ts
```

typecheck/build退出0，定向53/53 passed（含release两个子例）。公共SDK bridge断连
目标约106ms返回unknown，CLI两种release子例约105/103ms结束。timer替身连续20次
connected tick不形成deadline、不发额外refresh；成功进入readback时timer已经清理。
退出后无unhandled rejection、未清理的timer或临时测试目录。未因断连隐式重发。
本轮只比较上述当前内容基线，五份既有文件修改、无新增／删除，依赖／锁文件／
公开DTO／CLI生产代码不变。没有private导入／全局生产WebSocket包裹／目录轮询／
transport策略，没有真实daemon、模型、凭据、预览、Python/App/SDK/Paseo改动或提交。
完成通知统筹（notifyOnFinish=true），请原reviewer9a16对应只读复核；停此局部修复边界。


## A07a：公共SDK单次发送（完成实现，停fresh独立评审门槛）

用户已接受A07只读事实后授权此窄实现，后补真实空闲创建快照的字段事实；本实现者
仍仅离线验证，未访问该Agent／真实daemon、模型、凭据或预览。修改只在本owned目录，
没有Python/App/Paseo/SDK/SN/阶段文档改动，无依赖、提交、重置、清理或委派。
没有自行发送评审通知；由统筹安排fresh Codex6.1 high只读评审及后续真实验收。

进入时从当前内容复制41份文件（含忽略的dist，排除node_modules）到
`/tmp/a07a-baseline-c83gda8x/content/`，完整初始SHAmanifest为同目录`sha256.json`，
SHA256=`6b3ab2b49edcaa37a688d8d4f7548d657a3789ee4c7a2426e8a1755b12872aa2`。
基于这些当前内容比较，不把整仓HEAD／旧dirty算成本轮变更。完整最终manifest与
相对基线diff在同目录`current-sha256.json`、`changes.json`、`owned.diff`；它们含本记录
最终hash，避免本记录自身hash循环。核心源文件初始／交付SHA256如下：

| 文件 | 初始SHA256 | 当前SHA256 |
| --- | --- | --- |
| src/bridge.ts | 406fbcb580b9cc34aa3c173c4ce7b2f1ef8166857414a7521689a57a7eec7568 | 920c2238073dfbb7138d5d397c6a1e579107f3972c56f9e5717f603137abbd51 |
| src/types.ts | a166ba11bead46a54290d1d31a9ad374db558d8e2a8f793886038e4a8f45aaf0 | 8c73f9d4c80d39bde6b883cf784cb34fb7b3e16c920004f90a09d191a6bed0e3 |
| src/input.ts | 04c7be4de3addd9e7c8e639a9da3fc41e696a9a75d328696e643d7d8c9eb1ed2 | 9411df3ae4e34f6737721f142b639e3e59de8c02251fada6b8c16bcd2078f186 |
| src/cli-protocol.ts | 7389d9178c6d338b3dcc834358d7a4dbb7d346284a39b9b20fb646596cf835e1 | 14aaf492cc2deb4623a6969b6160d7de87370331ee73d5ca89047ac228a55d0b |
| src/index.ts | f45782d6ed92b0df44acb3697681c86615b72493c84a8ae186cd7a9c3fa04739 | bad4bf6a8e64d7028915dda486f77b38acf21217c53be56833bbe29b354e1e10 |

修改五个生产源文件、既有read-fixture、README/PROTOCOL/本记录；新增仅
`tests/send.test.ts`、`tests/public-send.test.ts`、`tests/send-cli-process.test.ts`、
`tests/send-cli-socket.mjs`。构建更新已有dist产物；无删除文件，package／lock不变，
历史投影不变。准确接口／command／stdout及全部固定码见PROTOCOL的A07a节。

范围依据：只读repository.attach_session与sessions.TaskSessionReader.scope显示，
Task-session允许任意已有完整Agent引用，本地Task workspace.location是server权威。
remote workspaceId无适用于全部引用的可靠映射，creation binding不是发送必要条件。
因此闭合命令只有op/agentId/expectedCwd/text/messageId/activeTurnBehavior。
完整标准UUID、原text（16384 UTF16且32768 UTF8）、非全空白／NUL、明确interrupt/steer
在SDK构造／读取前校验，拒绝扩选项。expectedCwd原值允许尾空格／symlink canonical
同目录。服务器负责授权、配置、持久冻结消息意图与产品选择，桥接不持有Task数据。

precheck完整Agent ID／canonical现存cwd，严格archivedAt=null、已知空pendingPermissions
及activeTurn形状；只允许idle/running/error（2026-10-20：error可续接），权限attention／providerUnavailable拒绝。
缺字段、归档、权限、不可用状态固定not_attempted，减少已观察资源的隐式unarchive／
权限清理风险；不宣称非原子预检之后状态不变。所有async预检后及最后调用边界核
生命周期／实例／releaseFailed／connected，恰一次公共handle.send。首个await前复制
原始意图；没有prompt拼装、上下文预算、receipt query、run/waitForFinish或其他写操作。

acknowledged只表示SDKvoid Promise已resolve，ID是预定冻结意图身份，不能伪称真实
ACK携带／身份因果验证。SDK按requestId匹配、不核返回agentId、丢弃payload，离线badACK
证明这一限制。调用开始后异常／close／断连／超时均unknown，不分类异常文字、无重发。
resolve与close交错及编码fallback保留安全sdkResolved事实；晚reject处理、迟到resolve
不修改已返回结果。result与release独立，release失败不抹ack，release成功不消除unknown。
SDK默认60秒等待、断连立即reject，不复制create的100ms timer；reconnect=false。

真实红绿／证伪：

- 先新建public-send测试：安装0.11.2包根+global WebSocket替身的3个对照通过，准确
  wire／显式interrupt-steer／省略行为、错误requestId忽略、错误ACKagentId仍resolve、
  无完成查询；ACK前断连立即DAEMON_CONNECTION_LOST；虚拟时钟59999ms仍pending、
  60000ms拒绝DAEMON_REQUEST_TIMEOUT。accepted:false拒绝，零自动重放。
- 加bridge/CLI接口反例后17项只有3个SDK对照通过，14项红：缺sendAgent方法、CLI拒绝
  旧unavailable、编码fallback丢意图身份。见`red.txt`；没有将fixture错误当生产缺陷。
- 初步实现16/17通过，唯一失败为测试对从未交给SDK的deferred gate人为late reject，
  形成测试自身unhandled；局部改为只在确有在途gate的close/disconnect分支晚reject，
  未放宽生产约束。最终晚reject经过setImmediate无unhandled。
- 最后目录stat已fulfilled，canonicalDirectory/race/untilClosed/send续体之间用明确
  三层微任务close；SDKrelease仍只是排队。临时局部去掉最后生命周期检查并重建，
  该反例0/1：close后跨调用边界返回unknown+sdkResolved。立即恢复／重建后1/1，
  not_attempted/CLOSED、零send。见`final-boundary-red.txt`。
- 覆盖意图突变、missing／alias／scope／实际尾空格与symlink／路径异常、归档／权限／
  缺字段／其他状态、refresh和IO关闭／断连、旧实例迟到／失败释放、同步throw／reject、
  resolve-close交错、安全observed、失败释放优先级、编码超限／循环保留身份与release。
- CLI实际子进程闭合字段拒绝，stdout一份JSON且stderr空、原异常／sentinel不回显。
  用仅测试的--import WebSocket替身运行真实dist/cli.js，成功ACK／badACK、reject／
  disconnect均一次send、finallyrelease；不打开网络。公共SDK+executeRequest另覆盖
  close失败时acknowledged或unknown与CLOSE_FAILED独立。

实际验证命令（全部exit0）：

```bash
npm --prefix apps/sn-agent-bridge run typecheck
npm --prefix apps/sn-agent-bridge test
```

typecheck含build及测试源类型检查；完整包92/92 passed，0 fail/cancel/skip，结果保存
`package-tests.txt`。之后仅补本实现记录，不重复扩大测试。定向复核命令：

```bash
cd apps/sn-agent-bridge
node --experimental-strip-types --test tests/send.test.ts tests/public-send.test.ts tests/send-cli-process.test.ts
```

真实包根自引用导出只实例化bridge不connect，确认sendAgent为function并close ok。
实际CLI的合成离线stdout保存`cli-ack.stdout.json`和`cli-disconnect.stdout.json`，分别为
acknowledged/releaseok和unknown(SEND_OUTCOME_UNKNOWN,CONNECTION_UNAVAILABLE)/releaseok，
预定UUID/messageId保留，无执行／完成／真实ACK身份／receipt宣称。

局限：没有真实daemon/provider发送验收；idle及empty permissions只是一份快照，
refresh／IO／send非原子，不提供原子reject-busy、授权或沙箱。steer可取消重启。
公共SDK丢ACK字段、无receipt查询，现有历史投影没有messageId，本轮不扩展它。
子进程budget、unknown持久化／核对、消息上下文预算、HTTP/UI显式行为选择由统筹后续
实施；无完整envelope不能推断未投递，不自动重发。App blocked未改。本轮停fresh
独立Codex6.1 high评审门槛，不进入A07b或自行通知／委派。
