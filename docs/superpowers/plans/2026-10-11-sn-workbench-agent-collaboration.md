# A 阶段：任务与 Agent 协作

2026-10-11。用户已授权按小任务开发、独立评审和持续集成；本记录跟随实际结果更新，不冻结长期接口。

目标：持续任务 → 关联／创建 Agent → 显式引用目标、代码、文档与结果 → 真实会话 → 保存有出处的发现／候选方向／下一步 → 人工判断 → 重开或换 Agent 继续。B～E 的 Job、比较、实际修改验证与批量调查不在本阶段实现。

当前权威：SQLite 保存任务、领域记录、关联与历史；Paseo 保存 Agent 会话、消息与运行状态。关联不复制生命周期，关闭 Agent 不删除任务记录，不产生 Job 取消。原结果快照与人评修订保持既有语义。模型内容只能保存为候选，不能以 local-reviewer 人评或 official score 入库。

当前结论：A01～A07 切片及委员会必要修复均已通过对应独立评审，Astra high＋Opus5.5 high 最终对应委员会均通过；A 在已披露的单操作人本地开发范围闭合。工作台 Python 377 项、消息 hook／浏览器 168 项、独立 31 场景及最终临时 App 流程通过；真实创建、取源候选和单次消息证据另存。最终浏览器修复只做 synthetic／HTTP／SQLite 回归，没有再次真实发送。2026-10-12 另获授权更新预览，恢复事实另记，不把代码验收当作已部署。

## 小任务与归属

| 切片 | Owner／模型 effort | 文件归属 | 验收与进展 |
| --- | --- | --- | --- |
| A1 持久 Task-session 关联 | 集成人 Codex6.1 high | repository.py、api.py、定向 Python 测试 | 16项及独立评审通过，类型Minor复核闭合；无模型调用 |
| A02a SDK 连接与目录读取 | Codex6.1 high，`d97133c6-e948-4e70-b6d9-e7f1eee42c23` | apps/sn-agent-bridge/** | 释放/发布竞态修复复核通过；SDK0.11.2，15替身＋1拒握手smoke；本地connect/workspaces/close另已最小验证，provider/models未真实验收 |
| A02b 只读会话／窄 IPC | 原 bridge Codex6.1 high | apps/sn-agent-bridge/** | fresh Codex `8cf732bc-e85e-4bd6-86e8-ef386c0130ed` 独立通过，无分级缺陷；重跑36＋1并9补充合成，typecheck/build0；A02a Minor由118闭合。实际SDK wire拒future_event整页，不能保证未知活动进入投影。真实只读验收开始，无创建/发送/订阅 |
| 简单前端组件范围确认 | Codex6.1 high，`65a0458d-743b-4a3a-9559-e70491191fb9` | 只读实际前端 | TaskComposer与TaskSessionPanel两个独立brief已完成，未改源码 |
| A03 简单前端组件实现 | Pi DeepSeek V4.1 Flash max，`8f23a41f-2800-40b7-9420-b2767bee2a32` | 仅新增 src/agents/TaskComposer.tsx | 组件修复及复核通过；App笔记迟到回执由3f50修复、原678复核两Important闭合，typecheck0、作者build/browser通过；发送仍未接入 |
| A03b 会话只读展示 | Pi DeepSeek V4.1 Flash max，`58e987c2-6bdc-40b8-bcef-45166f25bda6` | 仅新增 src/agents/TaskSessionPanel.tsx | 分页handler独立修复后678整体复核通过，无剩余Important/Minor，typecheck0；未接线/真实browser |
| 领域候选与最终接线 | 集成人 Codex6.1 high | workbench 领域、App.tsx、contracts.ts | 前置切片验收后逐项推进 |
| A04a 显式上下文快照 | 集成人 Codex6.1 high | context.py、api.py、tests/test_workbench_context.py、contracts.ts 类型 | 5项及原reviewer678复核通过；尚未前端上下文选择/发送 |
| A04b 显式上下文选择展示 | Pi DeepSeek V4.1 Flash max，`66d45196-3c1d-45c9-ba19-9413c4bda41d` | 仅新增 src/agents/ContextPicker.tsx | 空白门禁/hash缺testID/空blocked原因修复后独立Codex5de复核通过，typecheck0；完整空文本允许，无发送/持久化；未接线/browser |
| A05a 候选与人工决定领域 | 集成人 Codex6.1 high | candidates.py、tests/test_workbench_candidates.py | 4项与独立reviewer5de通过；尚未HTTP/真实会话取源；服务器实际取源是接线前置保证 |
| A05b 精确候选来源范围 | 集成人 Codex6.1 high | candidates.py、tests/test_workbench_candidates.py 局部 | 真实SDK不连续sourceSeqRanges/turn可选保存，原文hash保留；6项且独立5de通过，无HTTP取源 |
| A05c 候选保存真实取源 | 集成人 Codex6.1 high | sourced_candidates.py、tests/test_workbench_sourced_candidates.py | locator经scoped SDK重读精确assistant/原文hash/ranges/epoch/turn，browser不供权威原文；已存receipt先核对返回，远端丢失不改旧ACK；4项且独立5de评审通过，无HTTP |
| A05d 候选起草受控表单 | Pi Flash max，`58eddb89-1f64-4b13-8a58-4a57355d0c53` | 仅新增 CandidateDraftPanel.tsx | 独立5de评审通过、typecheck0；来源/草稿/save与unknown状态均受控，未接线 |
| A05e 本地候选列表与人工决定HTTP | 集成人 Codex6.1 high | api.py局部与collaboration_api单一nodeid | 先红404后定向9 passed，独立118通过；真实Task B跨task拒绝无写入覆盖Minor补充1项通过并原reviewer闭合；不依赖远程页/不自动人评 |
| A02c Python 只读进程适配 | 集成人 Codex6.1 high | bridge_adapter.py、tests/test_workbench_bridge_adapter.py | 有界读/深JSON/timeout前置校验复核闭合，11项通过；尚未真实CLI/HTTP接线 |
| A02d Task-session 服务器读取边界 | 集成人 Codex6.1 high | sessions.py、api.py、tests/test_workbench_sessions.py/test_workbench_session_api.py | 最小读取scope及边界13项通过；窄HTTP与原API合4项，独立d155通过无分级问题；真实CLI/SDK只读前置已验证，尚未前端接线 |
| A02e 历史读取生产者scope检查 | 原bridge Codex6.1 high d971 | apps/sn-agent-bridge/** 局部types/input/bridge及检查/协议 | 45项/typecheck/build0；独立Codex8cf评审及尾空格路径Minor复核闭合，reads+CLI28项。expectedCwd在refetch前存在目录canonical核对及IO后生命周期检查；仍非原子隔离 |
| A02f 历史页最小投影与稳定来源 | 集成人 Codex6.1 high | timeline.py、tests/test_workbench_timeline.py | 服务器Task scope→fixed expectedCwd/limit50/projected；真实页保留、ID绑定epoch/ranges/turn/原文hash，gap/stale/cropped/release失败不可候选；原元数据/identity两Important及turn/安全序号Minor先红后修，独立d155复核通过，timeline+sourced11项；HTTP另切片验收 |


| A02g／A05g 历史页与取源候选HTTP | 集成人 Codex6.1 high | api.py、tests/test_workbench_collaboration_api.py、timeline.py缺配置分支 | 独立d155窄评审通过，21定向项；仅server scope/actor/expectedCwd，browser只能cursor/locator与意图 |
| A03c 显式会话与历史页父接线 | 集成人 Codex6.1 high | App.tsx/contracts.ts、useTaskSessions.ts、tests/sessions-browser.mjs | typecheck/build及独立state受控HTTP browser通过，原678独立评审通过无Important/Minor；尚未真实SDK browser/发送 |
| A05f 已存候选与人工判断受控面板 | Pi Flash max 58ed | 仅CandidateRecordsPanel.tsx | 首评receipt不绑定当前decision Important和合法judgment Minor局部修复，原5de对应复核闭合/typecheck0；未接线 |
| A04c 显式上下文父接线 | 集成人 Codex6.1 high | App.tsx、useMessageContext.ts、context-browser.mjs | typecheck/build与独立state browser通过，原678独立复核通过；完整快照per消息草稿、来源/hash去重与多版本，发送blocked |
| A05h 已提交回执只读核对 | 集成人 Codex6.1 high | candidate_receipts.py、api.py局部、定向receipt测试 | 原请求key空格误trim Important已红绿修复，原d155复核12项通过；missing仍unknown，不重发/不写库 |
| A05i 候选与人工判断父接线 | 集成人 Codex6.1 high | App.tsx、useCandidates.ts、candidates-browser.mjs、collaboration-server.py、candidate-boundaries-browser.mjs | 原5de对应复核闭合两Minor，无Blocking/Important/Minor；14定向browser＋原完整browser/typecheck/build通过；真实已有SDK browser关联/历史/精确取源候选通过，无创建/发送/人判断 |
| A06a 无prompt单次创建bridge | 原bridge Codex6.1 high d971 | apps/sn-agent-bridge/** | 原9a16对应复核通过，Blocking/Important/Minor均0；独立typecheck/build0及53定向项，timer只属单次create；真实创建尚未验收 |
| A06b Python创建进程适配 | Codex6.1 high 118851f7 | 仅creation_bridge.py、test_workbench_creation_bridge.py | 9a16独立95/95及额外畸形stdout/进程回收验证通过，无Blocking/Important/Minor；可能派发后失去完整结果保留unknown，result/release独立 |
| A06c 固定创建意图与Task关联 | 集成人 Codex6.1 high | 仅agent_creations.py、test_workbench_agent_creations.py | d155独立11项通过，无Blocking/Important/Minor；真实进程崩溃/重开不重发与去掉unknown claim红绿已验证；created与关联同事务，HTTP/App另切片 |
| A06d 创建意图HTTP | Codex6.1 high 118851f7 | creation_api.py/test_workbench_creation_api.py、api最小接入、Store只读list | 原d155对应复核50项通过，无Blocking/Important/Minor；本地request_key恢复Important闭合，SDKkey不公开，GET零RPC/零写 |
| A06e 创建空白Agent受控面板 | Pi Flash max 58ed | 仅AgentCreationPanel.tsx | 原678复核通过，无Blocking/Important/Minor；ready独立是/否显示Important闭合，typecheck0，未HTTP/App接线 |

| A06f 创建父状态与App接线 | Codex6.1 high 3f50 | App/contracts最小增量、useAgentCreations、creation browser/harness | 原5de最终复核0/0/0，九项Important闭合；71项harness及独立HTTP/browser/typecheck通过，App归属释放；未知本地key只读恢复，不自动选session |
| A07a 单次显式行为发送bridge | 原bridge Codex6.1 high d971 | apps/sn-agent-bridge/** | 9a16独立通过0/0/0、92+7/typecheckbuild0；显式行为与单次调用，ack仅SDKresolved |
| A07b 持久消息意图store | 集成人Codex6.1 high | agent_messages.py、test_workbench_agent_messages.py | d155局部复核通过0/0/0、24store+89adapter通过；workspace冻结与旧schema授权、history SDKID泄露Important已闭合 |
| A07c 发送行为与ACK受控组件 | Pi Flash max58ed | 仅TaskComposer.tsx | 678独立通过，无分级缺陷；真实React/RNW22checks/typecheck0；父接线另A07e |
| A07b Python消息适配 | Codex6.1 high 118 | message_bridge.py/test_workbench_message_bridge.py | 9a16独立0/0/0；89消息+13读+26独立项、395输入与Node一致；A07d纯preflight增量另复核 |
| A07d 消息意图HTTP | 118原作者／root局部修复，原生Codex6.1 high复核 | message_api.py/test、api最小接入、adapter pure preflight、store前置callback | /root/a07d_review对应复核0/0/0，54HTTP/组合169/整体workbench377通过；零写预算及来源拒绝类型码、callback脱敏闭合 |
| A07e 消息父状态与App接线 | 原生Codex6.1 high /root/a07e_parent | 新useAgentMessages/useMessageContext最小guard、App/contracts与消息harness | 最终168hooks/typecheck/临时actual-App HTTPbrowser通过；原reviewer168+31对应复核0/0/0、App两新state连续通过，身份冻结、同源排他与拒绝刷新闭合；真实一次send及匹配turn_completed另存 |

每个小任务使用实际工作树的前后内容评审。A1 基线位于本机 `/tmp/sn-workbench-a-baseline`，包含原有未提交实现；不能只与 HEAD 比较。没有 commit/stash/reset/clean，不写预览 state `var/sn-workbench`，验证使用临时 state。模型联调限于明确的小接入动作，不启动 SN benchmark 或 judge。

调度统一使用 Paseo，`notifyOnFinish=true`；依赖完成通知衔接，不轮询、不催促、不重复分派。每次选择 profile 前刷新 list_profiles（本次为 0），无 profile 使用实际发现的模型。确认／复杂开发／独立只读评审均为 `codex/gpt-6.1-sol`、full-access、high；简单且已确认的任务为 `pi/deepseek/deepseek-flash`、max，无 mode。大阶段完整可运行且小任务评审通过后才使用 `paseo-committee`，委员为 `codex/gpt-6-astra` high 与 `claude/claude-opus-5-5` high，启动前重新核对可用设置；委员只读，统一 prompt 末尾加入用户指定的 analysis-only 禁止改文件语句。父会话 `27afbbe2-d92a-4648-855e-db1b9895e849` 接收阶段反馈；不得用 committee 代替小补丁评审。

当前 A1 独立评审：`d155f3dc-d492-425b-9a24-c5d9c4b87a62`（Codex6.1 high），已派，仅读上述前后快照与必要临时 state 检查。A02a 接口计划已接受：`createAgentBridge(serverConfig)` → connect/close/listProviders/listModels/listWorkspaces，ok 与固定脱敏 unavailable code，workspace 分页，空列表为成功。A03 brief 由确认者完成并转交；Pi 已按指定精确 provider 与 max 启动，无 mode，保留完成通知。当前组件未接线，也未宣称发送能力。

A03 独立评审：`678b2437-0f26-44b5-8a37-a269199f1960`（Codex6.1 high），只审新增 TaskComposer 与所用 ui 合同，不改文件。A04a 独立文件按任务解析 goal/file/result/case；文件为 workspace 相对 UTF-8 有界读取，拒绝越界／symlink／隐藏与运行目录／binary；结果与案例必须已关联，摘要不含运行配置／绝对源路径，案例不含 gold/reference。返回选中内容文本＋sha256供预览及后续显式发送，仅读不写领域记录。大小 64KiB 是当前单次选择上限，后续真实需求可改，不是永久产品契约。

A02a 独立评审：`118851f7-46b2-4d20-8f20-085b7c355299`（Codex6.1 high），已派，只审全新增 bridge 及已安装公共 SDK 声明。worker 已收到下一阶段的集成方向，但未获指令扩大当前代码。A04a HTTP `POST /api/tasks/{id}/context` 已加，当前 4 项通过；前端仅新增对应类型，typecheck 0，尚不代表 UI／发送验收。

集成人追加 A02a 最小真实只读验证：Node 通过已构建公共入口连接本地 daemon，`listWorkspaces({limit:50})` 返回 ok、count=3、hasMore=false、当前 `wks_33bfcd650d3260f6` 匹配；close=ok。只输出数量／匹配及固定状态，不打印目录、地址或凭据。没有创建或发送 Agent，也未写工作台 state。这证明当前本地 SDK/daemon 连接与 workspace 查询可用，不证明其他部署的认证策略或消息控制。

A02a 首评 2 Important：connect 失败且释放失败后丢失未释放状态，随后 close 可虚报 ok；握手兑现与 close 微任务交错缺最终发布检查。已回原作者补最小反例与修复，修复后原独立 reviewer 仅复核对应问题再进入 A02b。已有 11＋1 项通过未覆盖这些组合，不能代替问题复现；只读 reviewer 提供的是静态时序推导，作者须实际验证。

A03 首评两处可局部修正：提交文案为“发送”而 brief 要求“发送消息”；多个引用的打开／移除按钮缺操作对象的无障碍名称。已交 Pi 只改组件；集成人在 ui.Button 增加 optional accessibilityLabel，默认仍用 label，以免重复长引用破坏视觉密度。修复后原 reviewer 复核。该 reviewer 当前同时接到新 A04a 独立只读小评审，不改代码，A03 修复完成后再交明确复核请求。

Pi 修复两处已交付，原 reviewer 已收到复核请求（顺序处理 A04a 后附 A03 结论）。集成人开始 A03 接线：App 用受控 TaskComposer 替换原笔记 dock；mode 按 task、笔记保留 notes[taskId]，消息按 taskId＋agentId 分开；消息 blockedReason 固定“发送尚未接入”，无 onSend。笔记的 submitting 独立于结果导入 busy，有同步 in-flight 集合防重复，回执仅清原 key 草稿，保留后续变更；状态按 task 保存。typecheck 0，浏览器验证与接线独立评审未完成，尚不宣称可发送。

A03 接线浏览器：独立临时 state＋合成官方工件，现有完整阅读／人评／笔记／丢回执幂等／历史／布局流程通过；新增笔记“甲”与引用N、消息“乙”往返不混合，未接入发送禁用。第一次960px断言失败，实际 resize 返回瞬间 work-surface=180、settled=400，属于 RN 异步 resize 的 harness 等待缺失；加 waitForFunction 保留原400px断言后通过。Chromium最初环境路径相对cwd错误，改为已存在解包库的绝对路径后启动，无系统安装。浏览器未写预览state。

A02a 原作者修复已交付，原reviewer已收到只读复核请求：4 个明确 queueMicrotask 反例，最终15替身＋1SDK smoke通过，typecheck/build0。保留未确认释放current/releaseFailed，不替换实例；connect最终publication以及read发布检查终止／实例归属。详见 bridge/IMPLEMENTATION，不在本记录重复完整时序。

A05a 保存 immutable candidate（finding/direction/next_step）＋关联session及epoch/seq范围/必要消息文本hash，origin=agent、selected_by操作人，不能自动产生人评。人工决定另写candidate.decided，按actor保留修订与幂等，原候选不改。当前内部save的source必须由后续服务器从SDK实际读取，不能直接接受浏览器自由源DTO。3项验证来源／任务隔离／失败不写／幂等／重启／决定冲突通过；还未API或会话接线。

A05a 独立评审已派 `5deaf091-0da7-4603-95cf-f0fad862a3e0`（Codex6.1 high），只审新候选模块及复用事务辅助。A1原评审、A04a/A03复核、A02a复核仍按通知衔接，不轮询。待这些边界收敛后下一小任务为 A02b 只读会话历史＋一次性 Node JSON stdin/stdout，仍不创建／发送；其后才接 Python adapter及消息意图／发送。每一调用职责只覆盖当前使用，不做任意SDK透传或完整平台。

A02a复核已通过：原两Important和相关read成功发布边界闭合，reviewer重跑typecheck、15＋1通过。Minor为挂起read异常分支在重连释放失败后错误码未优先CLOSE_FAILED，不发布成功／不遗忘资源；已交原作者与A02b先补局部反例修正。A02b接口围绕实际SDK页读取，文本裁剪需明确truncated，不伪装完整；无订阅则不声称实时。CLI服务端config只在stdin、不读取现有凭据文件、不打印私有历史整文；本次仅已有会话只读。

A05a额外来源反例：原辅助_text会trim实际消息。新增保留原消息空白测试先红，改为只校验、保持精确原文后hash；现4项通过，已告知独立reviewer。A02c内部Python adapter已按A02b确认的{result,release} envelope实现，服务器config仅stdin，stderr不传播，timeout杀死并wait本次owned Node进程且返回release unknown，不改变远端Agent。3项用临时替身子进程检查固定失败/超时/非法JSON/凭据不输出/释放失败与读取成功分开；尚未接真实CLI，不能把替身通过当SDK整条路径验收。

A04a首评2 Important为遗漏literal runtime目录、non-NUL控制字节被接受；已补确定反例先后红，再局部修复。超限与非文本错误分开，UTF8 65536字节边界、result/case字段最小集已锁定，现5项通过，原reviewer复核中。A03组件两处复核通过，原reviewer接着评App接线/浏览器变更，尚未宣称接线评审通过。A02c独立评审交给空闲的bridge reviewer118851f7，只审两个新增Python文件，不审仍开发的A02b。

A05a 独立评审通过（4项重跑），无当前实现Important；生产者保证必须由服务端SDK按agent/session/epoch/seq/assistant角色/原文核对，浏览器不提交自由source DTO，actor由服务端提供。Minor精确原文hash断言已补入原测试。前端只读确认者65a0458d收到下一小范围确认：为独立受控会话关联/只读timeline组件给足够小brief，不自行派Pi、不改文件；待brief明确再执行，不能一次丢全阶段。

A1类型Minor原reviewer局部复核通过，A1已完整闭合。A02c首评2Important为stdout在完整缓冲后才检查大小、深层JSON RecursionError逃逸；新增3MB后sleep反例旧实现错误等到TIMEOUT、明确错误码断言也先红。改为selectors双管道有界读＋deadline，超限立即kill/wait owned process，finally关管道；深层JSON固定失败。现4项通过，原reviewer复核中。owned CLI仍需固定投影与输出预算，Python限量不能替代浏览器授权/字段投影。

## A1 当前接口与最小证伪

A03c 实际浏览器通过：独立测试state，真实本地关联＋受控远程HTTP fixture，显式选择且关联不代选；实际startCursor翻页、A→B→A迟到响应丢弃、消息草稿隔离和dark390验证。旧UI先红缺session panel；首次新harness未等新Task hash切换而操作上一任务，沿用既有createTask等待后通过。typecheck/build0。App/contracts基线实副本为/tmp/sn-workbench-session-ui-baseline-eni56h6e；已交678只读评审，审中统筹不改这些文件。

A05f 原5de对应复核闭合，typecheck0；原Pi完成对应修复：receipt必须原值匹配当前candidate、judgment、reason及actorLabel，并要求current/revision；三值judgment门禁覆盖保存与正向回执。已交原5de复核；未接线，不用类型检查代替回执生命周期验收。

A02f原d155复核11项通过，全部Important/Minor闭合。A02g/A05g窄HTTP原d155独立21项通过无分级问题；真实HTTP→Python adapter→CLI→SDK使用独立TemporaryDirectory关联已有d971并读取完整assistant，候选保存原文/hash一致、receipt重放、列表1且human decisions0，历史恰task.created/session.attached/candidate.saved。临时库清理，只打印状态/数量，不输出私有正文/hash/凭据；这不是browser真实SDK或发送验收。

A05c真实来源最小验收：独立TemporaryDirectory注册当前cwd并关联已有d971，scoped SDK历史ok/releaseok，5条当前完整assistant可引用；选一条经SourcedCandidates第二次真实SDK读取后精确原文/hash/ranges保存为integration-check候选，相同请求回原ACK，改hash新意图Conflict，历史恰task.created/session.attached/candidate.saved，无人工判断。临时库退出清理，未输出私有正文/hash/目录/凭据，未写预览state/真实消息；这不是HTTP/UI验收。A05d独立5de通过，A05e覆盖Minor原118闭合；3f50交付后已交678原问题复核，App继续冻结。

A03原678复核已闭合note两Important，App归属释放给统筹；A03b分页修正及单文件整体也通过，无剩余Important/Minor。reviewer重新typecheck0，build/browser采用3f50实际报告，未重复运行；前端后续新增scope生命周期仍须独立验证，不据旧浏览器声称新接线通过。

A05c独立5de评审通过、4项重跑，无Important/Minor；HTTP生产者仍由服务器持有actor/bridge，browser仅locator和候选意图。接续新增collaboration HTTP验收文件5项，缺路由404导致全部先红；尚未改api，等待A02f评审后接线。已将候选起草表单单文件只读范围确认交65，不创建重复执行者、不占App；人工决定列表另切片。

接续A05e只先接已审CandidateStore的GET列表与显式decisions，9定向检查通过，118独立审；api前快照/tmp/sn-workbench-collaboration-http-baseline-c45t07pq，审中暂停api修改。另5项历史/真实source HTTP仍404未交付，待A02f后接线，不能报告此文件整体通过。A05d Pi按完整受控brief交付唯一文件，已交5de独立审。bridge作者d971另收到下一A06a创建空闲Agent只读范围确认，不实现/不真实创建发送；creation intent与unknown留SQLite统筹。

A02e尾空格Minor已原8cf复核闭合，reads+CLI28项/typecheck/build0。随后独立tmp state实际scope历史：当前registered cwd匹配的已有d971读取ok、epoch及每条稳定ID/locator存在、releaseok；另一个真实存在目录下Task虽保存同Agent关联，历史读取返回SCOPE_MISMATCH/releaseok，关联保留。仅输出数量/固定状态，不打印私有正文/路径/凭据，未写预览state。A02f/05c仍独立评审中，尚未HTTP历史/候选接线。

A02e尾空格Minor作者局部修复交付，仅input/reads tests/cli tests/IMPLEMENTATION4文件；真实独立owned与owned[空格]、symlink对照及不同sibling反例旧0/2红、修后reads+cli28通过/typecheck0。已交原8cf只读复核，不重复分派；不偷偷trim路径，不改opaque ID规则/SDK/资源策略。

A02e独立8cf通过45项及9补充合成，无Blocking/Important，可进Python；Minor为合法尾空格目录被opaqueID字符串规则误拒，已回d971仅路径输入局部补真实反例修复。目录本身必须原样处理，不以trim改真实路径；原子/主机可解析限制仍成立。A02f服务模块当前3合成/性质检查通过，HTTP未接，待d155评审。

A02d实际HTTP整条验收已通过：独立TemporaryDirectory状态＋显式测试访问码，经HTTP本地关联→scope→Python→CLI→SDK读取已有d971，read/release均ok，浏览器字段集合恰id/title/status/provider/model，读取未写history。未修改现有预览state；此路径尚未前端接线/消息发送。

A02d新增窄HTTP接线：server-owned config.agent_bridge或内部注入，GET Task-local session agent仅最小投影；缺配置返回固定unavailable/not_needed并保留scope检查，bootstrap agent_read不等于healthy/发送。2新HTTP检查先缺参数失败后通过，与原API合4 passed，交d155独立审（前API基线a1-delivered含context）。A04b首评1Important空/全空白目的地/id/hash错误启用，已回Pi66局部修，hash缺testID与空blocked原因同文件处理，不抢App。

A02b已通过后实际整条只读：Python adapter→Node CLI→本地SDK readAgent(d971完整ID) ok且identity匹配，timeline projected limit2 ok、2条、epoch存在、hasOlder true/hasNewer false；两次release ok。仅输出状态/数量/标志，无私有正文/路径/凭据。A02d原reviewer8项通过无Important，2Minor为缺字段投影ok和异常cwd canonical抛出；补5确定反例先红后最小字段明确（title/model真实null可留）＋路径异常固定失败，13项通过，原reviewer对应复核中。

A02c字段顺序Minor原reviewer118复核1 passed/12 deselected闭合。首次实际Python→Node CLI验证：默认python/venv缺src导入路径导致两次ModuleNotFoundError；加PYTHONPATH=src后，错误假设坏URL在CLI parse拒绝，实际由已构造bridge.connect返回INVALID_CONFIG/close ok，故首次assert失败。随后分别核对额外command字段→INVALID_INPUT/not_needed、坏URL→INVALID_CONFIG/ok，两条真实envelope均通过且无连接创建。此验证仅输入/配置拒绝路径，不是daemon成功读取验收。

A02b实际CLI新增release.not_needed（输入/构造前拒绝），Python已据实际协议新增适配：只result unavailable时接受，ok/missing不能配not_needed；反例先红后13项通过，且补release先出现＋result null避免字段顺序导致异常逃逸。原reviewer118对应复核中。CLI4MiB输出、Python2MiB读取预算当前各自保留：超过Python预算即停止 owned进程并返回invalid response/release unknown，不宣称消费CLI最大页全部内容。

本轮接续组合检查：pytest六个workbench定向文件（Task/API/context/candidates/bridge_adapter/sessions）45 passed、既有Starlette/AnyIO warning 1。独立tmp state，无模型/预览写入；此检查不代替仍在开发的A02b、UI接线或真实来源验收。

A03b独立首评Important：分页误依赖onRefresh存在。已回Pi58局部修为各自handler独立＋共同选择/pending门槛，typecheck0，原reviewer678复核并给整体结论中。A02c missing适配原reviewer118重跑12项通过。SDK后续发送先验：已安装0.11.2包根send只返回void/失败异常，协议activeTurnBehavior实际仅interrupt/steer，无reject选项；不能捏造“空闲原子发送”或回执查询API，后续须在实际SDK边界明确当前turn行为和unknown持久化，不读private transport实现复制。

A02c timeout新Minor已由118复核11 passed闭合。对A02b已声明missing结果补Python适配：只result允许missing＋AGENT_NOT_FOUND，release仍只ok/unavailable，1行为反例先红后12项通过，原reviewer局部复核中。A02d现8项通过并交d155只读评审；前置缺module失败不记为行为红，尚未实际CLI/HTTP。确认者65另已接A04b单文件受控上下文选择/预览/加入草稿范围确认，不创建Pi、不改文件。

接续记录：A03b Pi `58e987c2-6bdc-40b8-bcef-45166f25bda6` 已派，不重复分派；等待通知。A04a 原 reviewer 678 复核5项通过，runtime/controls/精确字段边界闭合。A03 App 首评两Important（切Task迟到提示、改草稿key后旧status）交 fresh Codex6.1 high `3f50f7bb-7ae3-47ce-b414-c493d1e470c8`，仅 App.tsx/browser.mjs，先延迟HTTP反例后局部修复；其拥有App期间集成人不改App。A02c原两Important和错误码Minor已由118复核4项通过；新Minor invalid timeout在Popen后逃出清理，已补7类非法参数先红后在constructor校验有限正数，11项通过，待原reviewer局部复核。A1已含类型Minor复核闭合，A05a已通过，A02b仍等待实际交付，类型声明不能作为完成证据。

Task 的 `sessions` 列表只保存本地关联 id、Paseo `agent_id`、关联操作人和时间。`POST /api/tasks/{id}/sessions` 接收 `agent_id`、`request_key`，操作人来自服务端。关联不证明远程 Agent 仍存在或可运行；远程查询在 SDK 接线边界处理。

先验证：旧 v1 state 可原位增加关联表且旧任务／人评不丢失；关联幂等不会重复写历史；相同 request_key 异内容拒绝；未知任务不能留下关联；两个任务分别关联同一会话，互不修改。若现有事务边界无法保证这些行为，先收敛该边界再扩展会话功能。

- [x] A1 实现与定向验证：新增 3 项先红（缺 method/key/route），随后两个 workbench 测试文件 **16 passed**，既有 Starlette/AnyIO deprecation warning 1 项。临时 pytest state，无预览写入。
- [x] A1 独立 Codex6.1 high 只读评审：无 Blocking/Important；v1 实迁移／8 并发同 agent 最终1行1历史、未知 task／冲突无残留，16项通过。Minor内部task_id类型已用先红反例局部修复，原reviewer复核闭合。
- [x] 后续小切片逐项补充实际范围、验证与未决
- [x] A 可运行阶段通过后，Astra high＋Opus5.5 high committee、长期文档更新及一致性检查

本阶段检查项已闭合；历史首切片 85 项通过不作为本阶段验证。以下早期接续段落保留当时状态，当前结果以小任务表和末尾证据为准。

## 早期接口核对依据（当时尚未实施）

已只读核对 Paseo `packages/client/README.md`、包根 `src/index.ts`、`public-docs/sdk/events.md` 及实际 agent-panel/composer 使用关系。公共 SDK 的 `send` 默认会中断在途 turn，后续实现必须显式选择实际支持的 activeTurnBehavior；不能仅凭 UI idle 判断完成。历史条目是 epoch＋seqStart/seqEnd，投影条目可能覆盖多段序号，不能把简单数组 offset 当消息身份。timeline.subscribe 不自动补历史，若采用订阅必须 await ready、恢复后 refetch并分页、replacement 失效旧 epoch；若首个切片采用显式 refetch，则保留分页和过期响应边界，不声称实时流。

候选的来源拟绑定本地 session 关联及实际读到的 epoch／序号范围，保存选中消息的必要文本快照；候选内容与人工判断分开追加，不能复用 case review 写成模型确认。字段应等实际可安装 SDK 的返回核对后再落实。消息上下文必须显式选取，笔记案例引用不会自动发送，诊断 gold 与整包结果不默认注入。

A06a 交付后已派fresh Codex6.1 high/full-access reviewer `9a16c1cd-3b2d-414c-9987-f7f66f6b8ac9`。基线是bridge目录内node_modules/.cache/a06a-baseline-20261011（25副本＋manifest），sha7523f418…；11既有＋2新增测试。重点核最后创建调用生命周期、闭合immutable intent、读回identity/readiness和CLI编码fallback保留unknown与release；无真实创建，评审通过前不Python接线。调度profiles0，inspect实际Codex设置后创建，notifyOnFinish=true。

A04c 上下文父接线：App进入副本/tmp/sn-workbench-context-ui-baseline-rd9wm5ww（sha c8a66657…）；新增useMessageContext及context-browser。旧UI缺picker行为先红，实施后typecheck/build0，独立18092临时state browser通过。真实API合法空goal/emptySHA；合成大中文正文完整原值与服务端hash、path和A→B→A迟到scope失效、附件去重/新hash并存/存快照打开移除、笔记/消息隔离、dark390。已交原678独立审，App冻结；发送blocked，未声称服务端附件持久化。

A06a 9a16独立评审build/64+1通过但发现Important：publicSDK回执前DAEMON_CONNECTION_LOST保留creation Promise等待恢复，reconnect=false时150ms实际仍pending，untilClosed仅显式close取消，因此无法返回声明unknown。已回原d971在owned目录补真实publicSDK反例局部修，不用Python超时冒称契约闭合；其余边界评审无分级问题。评审67文件hash不变，仍无真实创建。

A05h 已提交回执只读核对：新增candidate_receipts.py及2项HTTP测试（原404先红），api局部两个request_key-only查询入口；task/server actor/真实target检查、读取不改history/requests、不远端读取、missing仍unknown。初次10项通过但d155评审抓request_key误trim Important；实读repo._prior/_receipt保持原key，已补两operation真实空格key与plainkey反例均红后仅移除赋回trim值，12项通过，交原d155复核。api基线/tmp/sn-workbench-candidate-receipt-baseline-p5oqw9nq sha0f7f4592…；未接UI核对动作。

A06a SDK公共入口缺口已实读：包根无connection status subscription，internal入口unsupported，保持禁private import。统筹撤销自己先前过强的“本地SDK等待禁止timer检查”额外限制，明确允许仅singlecreate in-flight内可清理timer读取public getConnectionState（零RPC），已知断连终结为unknown、零重发。Agent完成通知仍不轮询；Pythonbudget不变、不新增请求deadline。该局部修复已交原作者，待真实publicSDK反例和9a16复核。

A05h 原d155对应复核12项通过，request_key原值Important闭合，无新增分级问题。candidate/decision两条read-only receipt路径已可用于显式unknown核对；返回missing只保留unknown，不重发。

A05i 候选与人工判断父状态准备中：新增useCandidates.ts和candidates-browser.mjs（旧UI缺records-panel先红）；来源绑定task/session/SDK条目/页面版本，写意图key冻结，迟到回执仅原草稿；当前actor head从decisions筛选，不用global-last。synthetic bridge独立浏览器server在tests/collaboration-server.py，真实SQLite/HTTP/来源重读/人判断修订，不连接daemon。App仍在A04c评审冻结，候选hook未接、尚无browser绿或独立评审结论。已补unknown显式只读回执核对逻辑，未调用新save重发。

A05i实际接线及browser通过，App进入副本/tmp/sn-workbench-candidates-ui-baseline-24zskzd3 sha48c8d996…；typecheck/build0。新增harness在独立18093 state执行真实HTTP/SQLite/来源重读/人工修订，桥接本身是syntheticfixture且无daemon。覆盖来源refresh失效、旧A候选ACK换B不串、全局bobhead不替代alicebase、原文保留、两次人判断及candidate/decision实际写后丢响应→unknown→只读receipt恢复；missing保持unknown，核对A途中选B不污染草稿，不多写记录。已交5de独立审，App/useCandidates/tests冻结。A06a断连修复等待作者完成通知，不轮询。

A06a 断连Important局部修复交付：仅5既有文件、100ms singlecreate本地公共getter检查，finally清timer；publicSDK旧pending对照保留，bridge断连和CLI两release子例旧红、修后约100ms unknown且1create，close/releaseFailed优先与late reject/恰一次清timer均验证。typecheck/build0、53定向项。已交原9a16对应复核；进入基线a06a-review9a16-baseline manifest66a921e5…，local-timer基线manifest89666952…，无Python接线/真实创建。

A06a 原9a16对应复核通过：Blocking/Important/Minor均0，临时副本build/typecheck与53项通过；额外实证unknown时close调用0，CLI随后finally独立release。开始A06b/A06c，仍无HTTP/App创建入口或真实创建。

A06b 已交118独立窄开发，仅新增creation_bridge.py及对应测试。消费A06a闭合server-owned command，复用既有有界进程，提供call及纯command/envelope校验；无完整stdout或可能派发后的失败一律unknown，禁止自动重发。A06c由集成人新增agent_creations.py和对应测试，不改repository/API：独立表保存Task/actor/request_key、冻结profile/UUID/SDK key/expectedCwd；prepare先事务持久化，attempt在远端调用前事务claim为unknown，崩溃后同意图不再派发。created读回事实与Task-session引用在同事务保存，即使release失败也保留；unknown的observed不建立关联，不把readAgent missing当可重发证明。

A06c验证计划：真实SQLite重开和并发claim；独立Python进程在fake外部副作用marker后os._exit造成丢回执，重开仍unknown且不二次调用；参数/UUID/key冻结与返回值复制、同key异意图冲突；created/release失败、非idle、已有关联幂等；foreign Task/actor与授权变更先拒绝；坏DTO/unknown不得关联。server bindings只由constructor注入，本地目录只从注册Task workspace取；browser后续只能profile选择/title/request_key。创建事实不证明key所有权、所有配置生效或未来idle。开发与验证均临时state，不访问真实daemon或用户var。

A06c当前新增两文件初始不存在，未修改repository/API/bridge/App。首批9项因模块缺失先失败（不冒称行为红）；消费A06b当前共享validator后11项通过。随后局部反证将SQL claim从unknown临时改为prepared，真实os._exit(23)外部marker后重开反例实际1 failed（prepared != unknown）；finally恢复逐字节源码，再运行11项全部通过。额外覆盖SQLite outcome提交失败关联/event同事务回滚且unknown保持、bridge调用方篡改参数不能污染已冻结intent、历史不包含cwd/SDK key。已交d155独立只读窄评审，只看这两个新文件；A06b依赖自身仍需另一独立评审。无真实创建、模型或预览访问。

A06b作者118完成通知：仅两个新增文件，既有read adapter hash不变；实际34项首红及重复JSONkey/JS字符串契约反例后82创建测试＋13只读回归通过。sha creation_bridge f19d152194d84e18739672c92ac4ef62e48ca78f9ada1b3178f5ce4ff4112c10、test c6a4b82062e47f271ed424e091ba9cb1a4268b3a92ee36b1bf1171db8c7ac89f。已交原bridge reviewer9a16审这个作者不同的窄Python切片；A06c由d155独立审。HTTP/App创建入口继续停评审门槛前。

A06c d155独立只读评审通过，无Blocking/Important/Minor，11项实际重跑通过，两个owned文件hash前后不变；包含真实进程退出、并发claim和本地outcome事务回滚。结论只限持久意图/关联，A06b继续等9a16通知；尚无HTTP/App或真实创建验收。

A06b原9a16独立通过，无Blocking/Important/Minor，95/95＋嵌套重复键/非法UTF8/多份JSON/NaN/非零完整stdout的独立反例均unknown；timeout owned进程已回收/管道关闭，11必要文件hash不变。A06a/b/c前置已齐，HTTP/App创建尚未实施。

A05i原5de首评无Blocking/Important，有两项边界Minor：同pageVersion替换entry仍旧来源可保存；明确4xx非JSON错误响应被判unknown。已实读生产者pageVersion每次读取递增，当前后端错误JSON完整，因此不是既有正常路径故障；局部增强仍只useCandidates.ts，不改App/生产者。进入副本/tmp/sn-workbench-candidate-minor-baseline-1tgs8ugt，hook初始sha48bee6768f3349d771934a09e354802bcf3465b58669f0a608213781a0bc77dc。新增candidate-boundaries-browser.mjs以真实React/ReactDOM/Chromium挂载实际hook，仅synthetic fetch和session输入，内存TypeScript编译，不引入依赖或需要build。旧hook12项行为断言红（9种同ID替换、400/403/422空体或HTML拒绝），500/200坏JSON仍unknown两个对照通过；修后14项通过/typecheck0。当前sourceCurrent同时核assistant/未截断、完整locator/hash/原文；write先按明确无写status分类，错误bodyJSON仅取说明，不改变unknown的保守策略。原候选browser回归及对应复核接续中。

A05i修后实际build0（仅既有NO_COLOR提示），原18093独立state candidates-browser完整流程通过；同locator原文等合法来源未误拒，保存和只读receipt均真实SQLite/HTTP、syntheticbridge。已交原5de对应复核，只owned hook/新增边界harness，App/生产者/contracts不变。

A06d窄HTTP开发已交118：server-only creation bridge injection及闭合agent_creation workspace/profile配置，三个Task/actor scoped POST/GET/list路由，bootstrap只公开profile选择与配置能力；仅creation_api.py/对应test/api最小接入/Store只读list。根目录/UUID/SDK key/config/actor不从浏览器接收，所有副作用继续prepare/claim/finalize已审边界。先HTTP反例再实现，待独立审后才UI创建；无真实创建。

A06e只读范围确认65已给singlefilebrief，已交原Pi58ed沿用Flash/max/无mode，不另建执行者；只新增AgentCreationPanel.tsx、typecheck、不build/接线。profile无默认，标题原值可空，父映射空title为省略；created receipt绑定intent/profile/提交标题/current及Agent/session，不能误比较远端自动标题。unknown/saving/checking锁定意图，显式本地GET核对独立于create，关闭不撤销已提交创建。父状态/请求key/UUID/代次仍留后续接线。

真实已有Agent browser验收准备：新增agent-browser-server.py/agent-browser.mjs，server仅允许显式Agent的readAgent/readTimeline，独立TemporaryDirectory与测试访问码，不读取凭据，不支持create/send；18094由本次owned进程提供。SDK本地public无凭据connect/readAgent(d971完整ID)/close最小预检实际均ok，输出只有状态/identityMatched布尔。待A05i对应复核通过再运行browser取源候选流程；当前尚不报告其通过。测试失败也不输出私有正文/hash。

A06e Pi完成单文件交付/typecheck0，已交678独立只读评审。未build/接线/浏览器/真实daemon创建；当前App发送仍blocked。A06d作者仍在其owned范围开发，root不抢改API或Store。

A05i原5de对应复核闭合两Minor，无Blocking/Important/Minor，独立typecheck/build/node语法检查0；其环境未设置既有browser库路径而无法启动Chromium，不冒称独立重复执行harness。root之前实际14项及原browser成功使用明确LD_LIBRARY_PATH/FONTCONFIG_FILE，无系统依赖安装。真实已有Agent browser流程已开始，仍独立18094临时state，无创建/发送/模型或人工判断。

真实已有Agent browser实测通过：18094独立TemporaryDirectory＋当前真实CLI/SDK，只允许d971完整ID的readAgent/readTimeline。browser显式创建临时Task并关联/选择该Agent，Agent和timeline HTTP envelope均ok/releaseok；点击完整assistant起草并保存，服务端第二次scopedSDK重读后候选来源原文/hash/Agent精确匹配。临时库恰1session/1origin=agent候选/0human decisions，history恰task.created/session.attached/candidate.saved。只输出数量/布尔与固定状态，无私有正文/hash/凭据。服务器关闭时计数readAgent=1/readTimeline=2，无create/send；owned进程正常结束，临时state无残留。该路径不证明真实创建/发送/完整会话执行或正式人评已验收。

A06d独立d155评审49项通过但新增Important实际复现：同profile/title两个本地key“ key ”/“key”在SQLite保存正确，HTTP投影却省略key，无响应POST后不能只读精确认领。已交原118仅creation_api/test局部补原始本地request_key投影及反例，不泄露SDK key，不新增route或重发。A06e原678对应复核通过，新增ready独立行可区分所有readiness，title/intent/profile/current绑定及门禁无退步。

A06f准备独立18095 TemporaryDirectory＋synthetic creation adapter浏览器fixture，仅HTTP/SQLite真实，不daemon/model。新增creation-browser-server.py/creation-browser.mjs，先旧UI缺创建入口行为红，待A06d复核后才父接线。App/contracts入场副本/tmp/sn-workbench-creation-ui-baseline-8ah3d3qy，SHA App950323addb1b860e409700ceac94700da31c5ee8cb01aa5f8fbc1bd45a5bd5f7、contracts e41e0771cea5d08de93bb83aa5e27526477fd2f158239e95d4296498bcb87fbe。原d971另只读确认公共SDK send硬限制与离线证伪，不扩bridge实现或真实发送。

A06d原d155对应复核50项通过且两修复文件hash不变；root另实际50HTTP/store与93creation/store适配定向通过，warning既有。A06f已交原3f50复杂窄实现，owned仅App/contracts最小增量、新useAgentCreations及定向harness/creation测试fixture；不得改组件/backend/bridge。旧18095 root进程正常结束，临时state清理，作者自行启动当前fixture。未知无intentId需父层按原keyGETlist恢复，不能造server ID；任一prepared/missing/在途不证明未创建，禁止新key绕过。

已开始A06真实HTTP→Python→CLI→公共SDK无prompt单次创建，独立owned临时state /tmp/sn-workbench-live-creation-2sqnv8f8，Task de6332541eaa4070a6da4ed9b0418597。server允许profile codex/gpt-6.1-sol/full-access/high来源为本轮刷新list_profiles=0后inspect_provider实际设置；远端workspace为当前wks_33bfcd650d3260f6。不读凭据、不写预览，不发送prompt，当前等待单次结果；若unknown保留state和预定ID，不自动重试。

A06真实无prompt创建实测通过：独立Task de6332541eaa4070a6da4ed9b0418597，以服务器允许profile单次POST，经真实Python适配/ownedCLI/SDK在当前workspace创建空白Agent 8fd05bb1-5a60-4dcd-81cd-d2ed82ba59e4。结果created/ready=true/readiness=idle/releaseok，完整Agent ID及本地session引用一致；GET detail回同receipt，临时Task恰1session。history恰task.created/agent.creation.prepared/agent.creation.started/session.attached/agent.creation.observed，内部command闭合且无prompt。没有send或模型内容调用、重发、自动选择/归档/删除；state /tmp/sn-workbench-live-creation-2sqnv8f8/acceptance.json保留本轮Task/intent/Agent身份供后续只读及消息验收。仅状态/own ID/布尔输出，未打印私有路径正文或凭据。这个验收只证明单次实际创建及Python/HTTP关联，不证明UI创建或mode/effort有效应用、未来空闲或创建key因果；A06f仍开发中。

A06真实创建后只读补验：保留的独立state重开且移除creation配置，GET本地receipt仍created；真实agent/timeline scope读取均ok/releaseok，Agent idle，user/assistant/activity条目计数均0。证明当前空白会话观察与本地回执不依赖创建配置，不是未来状态承诺；零send、零模型内容调用。

A07公共SDK只读事实d971已完成：安装0.11.2包根＋global WebSocket替身17/17、类型检查通过，bridge41非依赖文件hash不变；断连send立即reject（不同于createpending），connected无ACK默认RPC60s，close不回滚；reconnect true亦不重放已发send。ACK仅requestId/agentId/accepted/error，send丢弃payload且不核agentId，无messageId/turn完成证据。公开历史可有messageId/clientMessageId/turnId，但拒绝输入也入历史，steer可共用turn，所以匹配仅证明观察到记录，不能证明ACK/intent完成，missing亦不能证明未发送。run只按agent等待，不能补意图专属完成。成熟server有receipt和provider reload/retry，但公共无durable receipt查询，不能宣称端到端exactly-once。

A07a已交原d971窄实现sendAgent及CLI，server闭合command预定Agent/messageUUID、原文、expectedCwd、必填interrupt/steer，UI后续显式选择；steer provider fallback可能取消重启，不能承诺不打断。既有Agent按Task注册cwd核scope，不要求creation绑定配置；precheck不得借send解除归档/处理待决权限。调用前not_attempted，开始后失败unknown，SDKresolve仅acknowledged与release独立，零重发/privateimport/真实send。9a16另只读确认A07b持久消息/显式上下文store范围，不实现或重调查SDK。

A07b只读scope确认9a16完成：建议AgentMessageStore独立表、显式text/session/reference+sha/request_key/必填active_turn_behavior，server重读source/hash后冻结messageUUID/body/hash/格式版本；先unknown claim提交再单次bridge，重复先返回旧冻结体不重新解析已变来源。ACK只SDKresolved而非执行/完成，本地receipt/list投影原key；未知不能靠历史或missing提升。建议当前可调预算text8KiB/最多4引用/上下文32KiB/最终body48KiB UTF8，并另核stdin真实序列化预算；尚未实现或实验。

A07c只读确认65给完整brief已交原Pi58ed，仅TaskComposer.tsx。Pi交付当前sha7b95ebc57eee95170f8ce63f93fac735cf5aee52b60fed44df752d90c91ee060，入场/tmp/A07c-TaskComposer-entry.tsx sha c54d44092a5785502875a299a9650fbbe7e489809bdebb8c4902b449f84738e3。behavior可选nullable+handler兼容旧父但禁发送，显式interrupt/steer，无默认；sending/unknown锁text/移除/行为，ACK独立于当前blocked，accepted禁重发。已交678独立只读审；Pi实际typecheck exit2仅并发useAgentCreations.ts两个错误，归3f50处理，组件无报错。不宣称全程序类型通过或运行通过。

root新增外置composer-behaviors-browser.mjs，以内存CJS bundle挂真实React/RNW/实际ui与组件，不buildApp、不mockReact/控件；初两次为测试loader故障（TS相对import解析、目录被当文件），非行为红，已修加载器准备基线行为对照。App创建接线仍3f50owned，发送bridge仍d971owned，不抢改。

A07c外置运行证据已齐：root加载器修复后，指定WORKBENCH_COMPOSER_SOURCE=/tmp/A07c-TaskComposer-entry.tsx基线实际行为红（省略behavior依旧enabled发送，true≠false）；当前组件同harness22checks全部通过。真实React/RNW与实际ui/组件、无controls mocks，内存构建不覆盖App。覆盖非法/省略行为和handler、空目标/正文/空blocked、literalcallback不send、sending/unknown锁及引用打开/mode可读、accepted+各种blocked保持ACK且禁重复、idle/error真实sendcallback、note不变和dark390不溢出。已交678对应运行证据；类型全程序通过仍等A06f作者最终检查，不用22局部检查替代父层/SDK实际发送。

A07c 原678独立评审通过，无Blocking/Important/Minor，22真实React/RNW checks及当前整套typecheck0；Pi历史并发hook错误现未复现，不将其判成组件缺陷。组件hash仍7b95ebc5…，App消息尚未接线。

A06f 原3f50交付新增useAgentCreations与App/contracts最小接线，两套browser/typecheck/build均通过，owned18095正常关闭。已交5de独立只读评审，重点原key恢复、跨Task/actor与A→B→A、未知锁与明确选择、runtimeDTO和非ready/release事实；review前不扩App接线。

A07a 原d971交付92/92/typecheckbuild0，零真实send；实际基线/tmp/a07a-baseline-c83gda8x/sha256.json SHA6b3ab2b49edcaa37a688d8d4f7548d657a3789ee4c7a2426e8a1755b12872aa2，当前manifest ced628aba6977b0bd82cc6117d87a8f024e5fad7c3d31cc768c1d24e749c617b。已交9a16 fresh独立只读评审，先通过再Python adapter接线。send body实际限制32KiB UTF8与16384 UTF16，比brief的48KiB暂定限制更小，消费者须遵守生产者契约；stdin64KiB仍另核实际序列化。

root A07b当前仅新增agent_messages.py与定向test，两文件初始不存在。13初始缺module失败不记行为红；准备/冻结10项通过，3派发/进程crash/outcome事务测试等待不存在的message_bridge验证器。内部冻结原text/原key/必选行为、有序reference+hash、服务端原快照及source_refs、最终sendbody/hash/格式版本/完整messageUUID；不默认goal。BEGIN IMMEDIATE提交unknown后单次adapter，重复/并发/崩溃不重发；未HTTP/App/真实发送。加入实际A07a字节与UTF16总body两个反例，旧store均DID NOT RAISE，局部改总body为32KiB/16384units，未改变单用户8KiB/4引用暂定选择策略。最终与stdin结合的验证待adapter实际交付，尚未独立评审。

A07a原9a16 fresh评审通过0/0/0，独立92包测试+7补例/typecheckbuild0，16最后微任务interleavings零额外send，45文件hash不变。A07b新增message_bridge.py/test由118交付后9a16独立通过0/0/0：89消息适配+13读适配+26独立反例，395输入与Nodevalidator一致，27文件hash不变，无真实发送。

root store与adapter组合123通过后交d155独立审；21store测试通过但Important2：未冻结workspaceID、history暴露SDK messageID。已冻结注册workspace_id并核派发前身份，移除三个history事件SDKmessageID；新增两反例修复通过。首个workspace反例构造两次UNIQUE location冲突属harness缺陷，不记行为红；之后修成swap path且允许两个workspace。复核仍抓新Important：旧表迁移用Task当前workspace回填原身份不可靠。补实际旧结构反例先DID NOT RAISE，删除回填，NULL旧prepared保持可读但拒派发；store+adapter113 passed，原d155第三局部复核中，不先消息HTTP。

A06f原5de审出5Important：GET先重绑profile/title；runtime非法回执组合可成功；合法incomplete created被抹；checking跨代次锁与newIntent；created未刷新Tasksession。回原3f50局部修复，当前58checks/HTTPbrowser/typecheck/build通过，旧副本58项38失败；已交原5de对应复核，App仍冻结。记录缺陷来自逐字段和生命周期独立反例，既有combinednegative不足，不把原harness通过当验收。

A07b原d155第三对应复核通过0/0/0，113store+adapter通过，独立旧schema中新旧intent路径正确。root随后临时移除workspace身份比较，两个registeredworkspace/legacy prepared反例均DID NOT RAISE；finally恢复逐字节原源码。该反证确认测试能捕获身份保证，而此前UNIQUE冲突仅测试构造缺陷。

A07d已交原118窄HTTPslice：message_api.py/test/api最小注入与bootstrap，并可在message_bridge加pure preflight真实config+转义stdin预算（claim前检查）；rootstore不改。原request_key GET/list/detail恢复，冻结text/orderedrefs/hash/behavior/ID事实projection、actor/Task/session/workspace隔离、不漏cwd/modelbody/rawsnapshots或异常。无configured能力不prepared；重复不重解来源或重发，GET零adapter/写入。尚未交付或真实发送。

A06f原5de第二复核五原Important闭合但新增4Important：新POST收到已知旧key合法receipt会重绑、真实422纠正后handler静默零POST、created合法BRIDGE_RELEASE_UNKNOWN误拒、bootstrap配置移除后local记录UI全不可达。已回3f50先重判断通用applyRecord多职责，POST/check必须显式expected冻结意图、历史选择独立；明确未写拒绝可显式新意图但unknown/prepared不能绕过；恢复区域不依赖创建能力。App消息接线继续停在门槛前。

root保留store最终内容副本 /tmp/sn-a07-message-reviewed-7mie1bda，store SHA7317b119ea5c17ba1923bf29a7ee56e99498d2b664a808d16a888a4ed28ad4d2、test ff3914e10f00cb7da97e6a1c581b167f82fbd6bc23506fabc57f2ca2fa782a56；24store测试通过。该目录另两adapter文件是在A07d作者正在新增preflight期间读取的当前快照，其hash已变化，不能把它们冒称A07b已审版本；A07b已审adapter SHA207788c5…/test2e8d7f53…，A07d增量须重新独立审。

A06f 最终原5de对应复核通过0/0/0：九项Important均闭合，71项真实React边界与独立HTTP/browser/typecheck通过。POST/check捕获expected冻结绑定、合法不完整/释放未知保留created、checking跨代次锁、Task会话刷新与配置移除历史恢复均独立核实。App可进入消息接线。最后修复副本/tmp/a06f-binding-repair-e5_hgbwk，after manifest841b536c…f2062。

A07d作者交付46HTTP/217必要回归后，d155发现prepared已写却返回generic422。原118修复时provider余额403，d155与替代9a16也遇相同403；不重复重试失败provider。用户继续授权下切换本环境原生Codex6.1 high独立评审 /root/a07d_review，保留小切片门槛。

A07d root局部修复：store.prepare纯preflight回调在最终command冻结后、INSERT/history前执行；HTTP不再prepare后检查。预算HTTP反例旧实现实际留下prepared先红，当前零intent/history/调用，原key纠正文可首次提交；25store与HTTP/adapter合计161通过。原生独立review验证零写及GET只SELECT、原key/旧receipt bypass，但新Important发现callback异常私有文本回显。新增ValueError/TypeError/RecursionError三反例修前均真实失败，HTTP仅callback包装固定公开错误，外围domain Conflict不吞；完整组合165 passed/既有warning1，交原reviewer对应只读复核。mask修复入场副本/tmp/a07d-preflight-mask-8u9x5rz4。

A07父接线前实际browser红已取得：新增独立TemporaryDirectory消息fixture与messages-browser.mjs，首个标题定位失败是harness错误；改实际状态定位后，点击behavior依旧send disabled超时，证明当前App尚未接发送。owned18096已关闭，未访问daemon/model/预览state。

Ruling: 原生子Agent替代余额耗尽的Paseo Codex开发/评审 — 用户继续且原工具链无法运行；模型仍使用已指定Codex6.1 high，完成报告自动返回，不重复派工。原有“不commit/清理、root唯一阶段记录、当前内容baseline”优先于技能默认commit/ledger流程。

A07d原生原reviewer对应复核通过0/0/0：mask仅callback、三类固定422无私有文本，SQL零domain写/零send；来源变化409及任务错误分类保留，旧ack/prepared不preflight不重派。独立50HTTP通过、五文件hash前后不变，证据/tmp/a07d-mask-independent-jhs4fqlp。A07d最终组合165通过。

A07e父状态切片交原生 /root/a07e_parent（Codex6.1 high）：owned新useAgentMessages、useMessageContext最小同步快照/写guard/version、App/contracts与消息定向harness。严格actor/Task/session/agent/draft绑定、首次await前冻结及持久原key、跨刷新unknown锁/同agent跨Task防绕、GET-only恢复、ACK后显式空新草稿。禁止改已审组件/后端/创建/笔记，禁止build覆盖现有预览；真实服务仍待独立审后。

A07d复核后必要工作台整体回归：PYTHONDONTWRITEBYTECODE=1 pytest -p no:cacheprovider tests/test_workbench*.py -q，共373 passed/既有warning1，临时pytest状态，无模型或预览写入。

真实消息验收准备（尚未发送）：沿用空白Agent8fd05bb1-5a60-4dcd-81cd-d2ed82ba59e4，当前真实只读readAgent观察idle、cwd匹配、releaseok。最初误给readAgent额外expectedCwd被CLI INVALID_INPUT/not_needed拒绝，修闭合command后才成功，不混作服务不可用。已准备/tmp/a07-live-acceptance-pdyv1fem/server.py与observe.mjs，语法通过但未启动：固定现有Task/Agent、固定无工具短回复正文、显式interrupt、独立临时state、单次不可重入send gate；公共timeline.subscribe先ready、独立记录turn事件，ACK与完成区分。等待A07e独立审后才执行，不创建新Agent、不读取凭据/用户var。

A07e作者阶段报告：当前48真实React/hooks checks/typecheck0，临时actual-App Expo export /tmp/a07e-app-static-HSOL4E，不覆盖预览dist；root不以中间绿代替交付。root实读首POST前hash await可跨actor/Task/session/代次，回作者补受控crypto延迟反例与最后生命周期核对，网络开始后仍保留unknown而非宣称撤销。root另单句移除materials.tsx“Agent尚未接入”旧描述，改为已有显式关联/创建/读取/候选功能；入场副本/tmp/a-stage-doc-consistency-jgwt0f2v，无App/hook抢改。

委员会启动前设置只读确认：list_profiles仍0，inspect_provider显示codex/gpt-6-astra与claude/claude-opus-5-5可选且provider enabled；这只证明配置存在，不证明余额/实际调用可用。没有启动委员会，仍等待A07e交付/独立评审与真实验收门槛。

A07e扩散前root核旧ACK生命周期：newDraft移除intent，而archived仅内存，reload后旧ACK区域不可见；作者实证认可缺口。将保留已核ACK必要原key/ID/binding/hash/behavior/有序refs归档metadata，读取本地GET后逐字段恢复到旧ACK区域，不复制私有正文、不绑新draft、不自动选session。补实际App newDraft→reload→旧ACK可见/新正文空反例；仍待作者交付及fresh独立审。

A07e作者最终交付已停源码修改：入场/tmp/a07e-baseline-zrc5h0_9/sha256.json，最终完整副本/SHA/owned.patch /tmp/a07e-final-4qdqaygf，root逐个确认7owned当前hash匹配。typecheck0/90React-hooks/临时actual-App HTTPbrowser0，3synthetic派发含丢POST后reload GET恢复零重派与3oldACK reload可读、新正文空、note/releasefailure/dark390。foreignsession与预POST hash竞态及oldACKreload红绿闭合；owned18096正常退出。已交fresh原生Codex6.1 high /root/a07e_review，只读逐字段/生命周期反例，不先真实发送。最新actual-App静态 /tmp/a07e-app-static-HSOL4E，indexSHA cba8e06d5d2bc050f60a2674318b3f089a57af5f82bb278ae52e726229c3ff0b。

A07e fresh独立评审确认1 Important，无其他可复现分级问题：首次合法unknown后，GET同intent/key/正文/行为/引用而换合法message_id或input/body hash，仍可升级accepted；reload和accepted后覆盖也复现。/tmp/a07e-independent-review.mjs五个实际React/Chromium反例。root核对源码认定消息身份冻结是必要消费者边界，已交原/root/a07e_parent修复；要求首次观测完整tuple持久化、后续严格匹配、未知锁和原ACK事实保留，以及保守旧metadata恢复。未真实发送。

root同步文档一致性：CURRENT_STATE明确最小本地Agent接入验收授权不扩大benchmark/judge范围；ARCHITECTURE、工作台README、评测状态和bridge README改为当前接线职责与原key/ACK/unknown语义，materials单句纠正旧未接入说明。前内容副本/tmp/a-stage-doc-consistency-jgwt0f2v；不覆盖预览dist或用户state，最终真实验收/委员会证据尚待追加。

A07e Important局部修复交付：仅useAgentMessages及消息boundary harness；入场/tmp/a07e-identity-repair-baseline-vk8rfvvz，最终/tmp/a07e-identity-repair-final-dsjjcr23，hookSHA b44589fd…6834c。首次完整权威tuple固定intentId/messageId/input/body hash并persist，后续/迟到/archive一致校验；已有intentId但缺tuple旧metadata不猜原身份。作者实际116 hooks/typecheck/临时actual-App HTTPSQLitebrowser通过，owned18096正常退出。root逐个确认7源码hash及临时indexSHA匹配，并已交原reviewer复核；最终静态/tmp/a07e-identity-static-nINQ6y，不覆盖预览。

A07e原reviewer对应复核0/0/0，31个实际React/Chromium场景通过：合法tuple替代、prepared/unknown、reload、accepted原回执/archive保留、legacy、hash并发双顺序、迟到POST；脚本/tmp/a07e-identity-recheck.mjs，7源码hash匹配且只hook+harness改变。root最终typecheck exit0。

真实单次消息验收通过：/tmp/a07-live-acceptance-pdyv1fem，实际最终App→HTTP→SQLiteclaim→Pythonadapter→NodeCLI→公共SDK；沿用A06已有测试Agent，先await timeline订阅ready且观察idle，然后页面显式interrupt一次发送固定无工具短回复，contexts=[]。browser实际ACK显示、send禁用、零pageerrors，transportCalls=1且本地记录1，acknowledged/releaseok；message_id 03655f9d-0afd-45c9-bf81-3a13bf4e8b0f，intent dc4c88c1b75640419eb98a5c6993eefc。独立事件user_message的messageId/clientMessageId与本地冻结ID精确匹配，关联codex-turn-0；同turn观察到指定A_STAGE_ACK_TEST回复和turn_completed、零tool事件，订阅release/clientclose均true。correlated-facts.json自动断言通过。ACK与完成是两份独立证据，不把ACK本身叫完成。owned18097/session25472正常Ctrl-C exit0；观察56425/browser65789均exit0。未重发/新建或归档Agent、未写用户预览、未运行benchmark/judge。这仅验收这一条真实输入及当前本地daemon/provider，不推断其他部署、并发或长期可用性。

A阶段只读委员会按用户模型启动：list_profiles0，实际模型目录确认Opus5.5支持high；/tmp/a-stage-committee-brief.md同任务和精确analysis-only末尾。Astra gpt-6-astra/high走原生/root/a_stage_astra_committee，避开先前余额失败的Codex通道；Opus claude-opus-5-5/high通过Paseo cc907e9e-5e8e-470f-8427-c6d47881df50，notifyOnFinish=true/bypassPermissions，只读prompt约束、无委派/发送。当前等待完成通知，不轮询不催促；启动状态不当作完成或共识。

A阶段委员会独立初审：Astra0Blocking/2Important/0Minor，Opus0Blocking/3Important/4Minor。共同发现多页localStorage覆盖及来源变化409未知锁；Opus另证已确认prewrite422刷新丢纠正资格，root接受。两位对应方案收敛：真单同源消息写所有权排他（不以merge/listener兜竞态），首次lease后完整重建metadata、释放后异步零写、无支持failclosed；生产者按抛出点返回仅前写零派发固定code/原key、前端绑定持久拒绝proof并reload GET核对。网络fetch错误不能证明零写，Opus明确撤回此推断；不引入settle/tombstone/abandoned，不改变missing/prepared/导航取消unknown。idle无会话误呈unknown Minor交父层修，其他M1/M3/M4作为明确取舍记录。方案不是通过，仍待实现/独立反例复核。

root生产者局部修复：当前内容基线/tmp/a-stage-context-refusal-baseline-wrer0sai，最终/tmp/a-stage-prewrite-refusal-final-_jky24m4。仅agent_messages/message_api及HTTPtest三文件：新意图来源hash mismatch专用Conflict，resolver ValueError/OSError专用unavailable，纯预算/preflight专用InputRejected，仅newprepare前INSERT分类；旧key/attempt/500不带类型码。固定{error,code,request_key}分别MESSAGE_CONTEXT_CHANGED_PREWRITE(409)/MESSAGE_CONTEXT_UNAVAILABLE_PREWRITE(422)/MESSAGE_BRIDGE_UNCONFIGURED_PREWRITE(422)/MESSAGE_INPUT_REJECTED_PREWRITE(422)；requestvalidation只回显合法原key，其他输入不回显。来源hash断言先红，删除/config/preflight5项先红后绿；组合169 passed/既有warning1，另afterprepare异常包括专用类也不被标前写。已交原/root/a07d_review窄独立审。

候选完整性只读核对：真实短回复的stream共有3个assistant delta；随后公共bridge projected页实际2entries/1assistant，seqStart2/seqEnd4，text.truncated=false且完整指定回复，releaseok；安全facts /tmp/a07-live-acceptance-pdyv1fem/projected-facts.json。证明当前这个已完成turn的delta合并为一个完整快照，不推断进行中全部provider行为；候选保证的是未截断条目及精确来源重读，不声明turn已完成。没有新模型发送或用户state写入。

root前写类型码生产者原reviewer窄复核0/0/0：四类code SQL零INSERT/UPDATE/DELETE、intent/history/send不变、精确key回显与固定脱敏；旧回执绕配置/来源/preflight、同key冲突无code；HTTP初始list后并发新增由prepare事务prior识别；prepared之后普通Conflict/三种专用异常/RuntimeError都不标前写。三个文件SHA与finalmanifest匹配不变。前端lease/proof对应复核仍待交付，委员会尚未最终通过。

明确本阶段取舍：消息prepare到attempt之间崩溃的prepared不恢复派发（与空白创建首次prepared claim不同），missing/普通错误/导航中止无手动解除，换Agent继续；不增加settle/tombstone。Web Locks排他只覆盖同浏览器profile同origin，不是跨浏览器/设备/actor/其他Paseo客户端互斥；缺API/非安全context/争锁失败禁发。恢复metadata无自动容量管理，损坏/配额failclosed、不忽略坏行或自动丢未决/ACK。候选“完整”仅指未截断条目快照，精确来源不等于turn完成。这些限制已同步工作台README，不把typed前写拒绝与未知混为一谈。

A07e委员会修复作者最终停写：基线/tmp/a07e-lease-baseline-1qk3zrk3，交付/tmp/a07e-lease-final-ow3fxy7q，7文件manifest/current copies及owned-repair.patch，5文件实际增量。真实Web Locks排他覆盖所有persist与迟到hash/POST/GET；新owner完整重读metadata，无API/失败failclosed、StrictMode正常释放重取。四类精确status/code/key拒绝proof保存在原intent，reload只读GET后missing可纠正，同key任意记录撤proof锁unknown，后续正确权威ACK仍走既有身份门禁恢复。idle/error锁定由App独立只读分支说明真实原因，不编造unknown。作者168实际hook/browser checks、typecheck、最终App HTTP/SQLite通过，临时bundle /tmp/a07e-lease-static-final2，不覆盖预览；owned45963正常关闭。root核7/7SHA匹配且TaskComposer已审SHA不变，已交原/root/a07e_review对应只读复核。

root最终验证已完成：工作台Python全量377 passed/既有warning1（42.61s），typecheck exit0；实际React/Chromium168检查通过，包括同源双页零覆盖、owner关闭后重载恢复unknown、四类拒绝proof刷新、普通错误保守、损坏metadata与存储失败零POST。6份长期文档及阶段记录共120个本地链接无缺失。实际最终App独立临时18098 HTTP/SQLite复验及委员会最终复核仍在进行，不把作者通过或上述定向结果代替阶段最终结论。

root独立最终App流程通过：仅18098独立TemporaryDirectory、当前既有fixture和/tmp/a07e-lease-static-final2，四类真实HTTP写前拒绝刷新恢复、新hash重新选择后synthetic发送、普通samekey409 unknown、丢POST只读ACK恢复、同源第二页只读/笔记仍可写、dark390全部通过。首次fixture缺PYTHONPATH与访问链接参数拼错属root启动错误，纠正后完整脚本exit0，无产品修改；服务正常关闭exit0，端口已释放。没有新真实发送。

A07e原reviewer最终对应复核0/0/0：独立当前168与上轮31身份/lifecycle反例通过，最终App HTTP/SQLite在两个全新state连续通过。首轮来源变化拒绝后reload等待task-session-panel超时，后两轮未复现且无pageerror；原因尚未定位，保留为验证局限而不认定已修复。7owned SHA评审前后匹配不变、TaskComposer已审SHA不变，ownedfixture正常关闭。

已向原两位委员会发送最终对应证据brief /tmp/a-stage-final-corresponding-review.md；Astra原生/root/a_stage_astra_committee与Opus5.5 Paseo cc907e9e-5e8e-470f-8427-c6d47881df50均只读，后者notifyOnFinish=true。本轮不重发真实消息、不催促/轮询，不把启动成功称为最终通过。

Astra high最终对应只读复核通过：原三项Important及M2闭合，无新Blocking/Important/需修复Minor；M1/M3/M4取舍披露足够，可按本地开发范围验收。直接核源码/定向测试/168与App日志及10文件manifest，未重跑或真实服务访问；377Python/独立31/两新state通过为统筹记录，证据层次明确。首轮刷新超时仍未解释，最终修复未再真实发送，跨profile/设备互斥和预览部署不在保证内。README补明确候选“完整”只指条目快照。

Opus5.5 high最终对应只读复核通过：I1/I2/I3与M2闭合，M1/M3/M4披露充分，无新Blocking/Important，A可认定闭合。三个非阻断Minor保留：ifAvailable刷新释放竞态是未证实推断，安全性failclosed；新旧bundle混用时旧页不持锁，因此部署须关闭或刷新所有旧页；schema类型拒绝证明本次未写而非key不存在，立即纠正依赖浏览器不自动重发/旧key不复用，刷新仍GET核对。首轮reload面板超时未经定位，后两轮未复现，保留验证局限。两位委员未重跑测试或访问daemon/model。此结论不代表最新预览已部署、最终bundle再次实发或benchmark/judge成绩；A不重开。

2026-10-12另获授权预览更新：旧state/config/dist已一致备份并在副本验证additive migration，原6表内容逐hash不变；bridge/frontend build0，访问码/私密runtime不入bundle，CLI不再打印认证URL。可靠本地Paseo端点只读握手及workspace匹配后配置A能力，通过现有受管workbench启动。真实代理浏览器仅GET与内存cookie，原3任务/A能力/executionfalse/无会话禁发原因/零pageerrors/零domainwrites通过，预览旧表仍不变。当前入口 http://workbench--feat-benchmark-protocol-correctness--bench--16ca78a8.localhost:6767 （不含访问码），部署须关闭或刷新所有旧页。没有再次实发或新建Agent。备份及恢复细节见[后续B/C记录](2026-10-12-sn-workbench-plan-comparison.md)，本段只补部署事实，不重开A。
