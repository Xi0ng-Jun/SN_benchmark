# 评测状态

## 2026-10-20 · 复用已准备 notebook 的补跑

ASQA 948 的 chunk 58 题、reasoning 60 题均在答案合成时失败（SN 拒绝模型返回的不完整 JSON），两模式失败题交集仅 2 题，与题目无关；资料导入已完成。新增 `--seed-prepared-run`（batch 为 `--seed-prepared-map`）：新 run 校验与原 run 同分区、同准备策略、同 export 条件族后，复制原 run 的 SN 数据库与 storage 及准备状态，只重新提问；`sources.file_path` 改写到新目录，库内其他位置若引用原 run 路径则拒绝。manifest 顶层与导出 provenance 记录种子来源，pairing 与合并条件不变。离线回归 2357 passed / 1 skipped；真实 SN `970b6793` 配离线假模型验证：删除种子目录后副本仍可读文件、检索与读取 source。

服务器转述结果（evaluator `8dee77f`）：118 题全部走种子路径，每题中位 1.03 分钟（P90 3.73），总墙钟 29.1 分钟；首题 `rewritten_source_paths=100`、未重新导入。chunk 53/58、reasoning 56/60 补跑成功，剩余仍为同型合成失败。retry-merged：chunk STR-EM 0.4917 / STR-HIT 0.2068，reasoning-no-clarification 0.4879 / 0.2089，scorer `634aed97…`，配对差均含 0。alce:837 只读核实：99 篇有 chunk，仅 Markdown 标题开头的 1 篇为 0。失败响应中位长度约 16 字符，finish_reason stop 227 / length 5 / 空 119，`answer_max_tokens=16384`，指向模型服务偶发返回近空内容而非长度截断，待反馈 SN。QA/MAUVE/引用评分待资源包到达后执行第二批 B。数值见[表 4a/4f](benchmark-result-tables.md)。

## 2026-10-16 服务器切换方式更新（用户最新决定）

此前交接文本尚未发给服务器。本次改为自包含的首次执行指令：立即停止旧调度和当前尚未完成的在途批次，不等导入完成；进程退出后在原评测目录更新，不新建 worktree。已完成结果及那 13 道合成失败保留，本次中断题统一用新目录重跑，中断原目录保留但排除最终答卷。该决定替代下文及早期交付中的“等待在途完成、另建 checkout”操作步骤。本次仅调整文档，未操作服务器或改动运行代码。

## 2026-10-16 续跑合并修正

用户指出初版“切换准备策略就不能合并”妨碍快速完成实验。已撤回此限制：同一 frozen bundle、SN 源码、模型/设置、请求和 mode 的已完成结果可与优化后剩余题一起导出；无需重跑 948 题。准备策略、评测器源码哈希与包含 checkout 路径的启动观测保留在原始 run 来源中，不再作为合并门槛；依赖声明和安装版本仍比较。每题新增原始 identity/评测器哈希及实际策略，方法配置汇总涉及的策略/评测器版本；旧工件不改写，错误/空答案及完整分母保留，重复题仍拒绝。旧答卷的比较入口同步使用相同归一化，保持可读。

本次离线回归 **252 passed（6.94s）**，包含真实 runner/reader 的不同策略分题合并、旧无策略字段与不同评测器版本续跑、错误保留、来源与顺序稳定性，以及模型/SN/模式/请求差异、重复题拒绝；覆盖 submission、Notebook v3、准备与 batch、比较/flip、feature identity、强制作答和官方评分。两项新增行为先确认在旧实现失败，再实现通过。无真实模型调用，未操作服务器。服务器按[续跑交接文本](server-asqa-continuation-prompt.md)停派、等待在途结束、只续跑剩余题；不增加快照功能或另开旧题对照批次。下方初版记录中的“异策略不可混合导出”已由本条修正。

## 2026-10-16 · 导入完成后统一刷新 notebook metadata

- **开发侧实现。** 评测准备流程增加 `--preparation-policy deferred-notebook-metadata-v1`，同步导入期间仅暂停 SN notebook metadata 刷新，全部资料完成 chunk/embedding 校验及建索引后调用一次最终刷新，再进入 Ask。默认 `per-source-metadata-v1` 保留；单次、batch、native Agent CLI 已接，工作台仍走默认。策略写入 runtime/manifest/state、batch argv 和提交身份，异策略不可混合导出。
- **失败与回执。** 导入异常/取消会恢复 suppression；部分 mapping 保留，不刷新半份语料、不标 prepared。最终刷新沿用 SN best-effort，回执只记录 invoked/call_returned/source_count/elapsed_seconds，不据返回声称模型合成成功。不支持刷新 API 的 SN 拒绝运行；已有 notebook 策略冲突拒绝复用，新 run-dir 约束不变。
- **本地实测边界。** 真实 SN repository + 离线假模型，各策略导入 100 篇合成短资料，notebook_metadata **448→8 次**，source_summary、paper_metadata、source_element_embedding、chunk_embedding 均各 **100 次**；100 篇均有 chunk，embedding 覆盖完整，摘要/论文元数据标记与最终 purpose 均落库、全部标题参与 metadata 提示。该 probe 在本地 SN `0680aa12` 及从 Git 提取的服务器同提交 `970b679304a216c280b720666f18c4802065028f` 均通过；未调用真实模型，未修改 SN 原树源码/配置/数据。
- **回归。** 准备策略初始红灯 8 项、CLI/身份接线红灯 4 项均确认；最终 Notebook、batch、submission、identity、阶段诊断、证据投影、源码加载、reasoning 协议、工作台 runner 和真实 SN probe 合计 **364 passed（30.62s）**。另一次精确服务器 SN SHA probe **1 passed（13.76s）**，与前项覆盖有重叠，不相加。修复旧源码加载测试未清理 `sys.path/app.*` 导致的顺序污染；未调整产品行为来迁就测试。`git diff --check` 通过。
- **服务器仍待验证。** 用户转述的旧样本约 51–52 分钟准备，按减少 440 × 5.25 秒推算约 13 分钟；这不是新策略服务器实测。实际分组、模型输出和质量效果需固定样本核对。本次未修改在跑任务、缓存、题间并发或私有 orchestrator，也未处理/重跑 13 道合成失败题。操作与证据边界见[准备优化说明](deferred-notebook-metadata.md)。
- **交付验证补充。** 将代码包还原到只含服务器旧基线的临时仓库时，发现 batch CLI 的只读 metrics 测试依赖 Git 外本机历史 run（70 passed / 1 failed）。该 CLI 测试改为自建合成资料与保存输出，并验证整份运行目录逐文件内容保持不变；真实历史观测验证仍留在原 stage metrics 测试中，不把本机历史数据打进交付包。交付回执单独记录固定 SHA、远端分支、包校验及还原后验收结果。

## 2026-10-15 · reasoning-no-clarification-v1 评测侧协议

- 已实现一个显式 opt-in 的强制作答变体：先调用 SN intent preview，再用固定 `cover-all-options-v1` 策略回答每个 `required != false` 的 ambiguity；非空 options 按原顺序拼接为“同时考虑以下所有可能：A；B”，无 options 时使用 `resolved_question`。每题保存原始 contract、`preview_needs_clarification`、自动确认项、答案来源和 policy 版本。
- 该协议保持 SN 生产代码和标准 reasoning 不变，使用独立 `reasoning-no-clarification-v1` identity；比较层拒绝把它与标准 reasoning 混入同一 SN reasoning comparison。生成阶段继续使用无 gold 的 `notebook-request-v3`。
- 本机只完成离线实现与测试，未运行真实模型、未运行 ASQA 生成、未修改 SN 主仓库或创建 SN worktree。下一批服务器实验固定 ordinary ASQA 全量 948 题，与 chunk 948 使用相同题单和输入；不跑标准 reasoning。ALCE 模型评分命令和环境留作后续占位，不能把本记录当作实验成绩。

## 2026-10-15 · 服务器模型接入、目录选择与失败诊断

- **费用策略按用户决定调整。** 工作台移除强制金额输入，新请求只冻结总超时，不创建预算账本、不安装固定价格表的模型拦截器；内部模型名直接由 SN 私有配置决定。保留超时、取消、固定题单与源码、未知请求原样恢复。旧 API 显式 `limit_cny`／CLI `--budget-ledger` 仍可使用旧费用保护，仍受旧价格表限制；未知价格不报告为零。操作者仍需明确授权新的模型实验，取消金额控件不等于自动授权运行。
- **多目录可选。** bootstrap 返回登记 ID、名称和路径；多条登记需要明确选择，单条预选，绑定实际选择项。换任务后不沿用未确认选择，已有绑定不因配置排序变化而替换。
- **错误链贯通。** 启动、导入、Ask、生成和评分失败保留 `error_type/error_message/failed_phase/diagnostic_path`；分区 stderr 完整脱敏保存，traceback 保留异常链与帧位置，不写源码行、局部变量或异常 notes。界面和分析 Agent 的运行摘要均显示原因与日志位置，生成失败优先于随后缺 manifest 的评分失败。启动前尚未拥有 run 目录时写独立私有 sidecar，不修改被拒绝的旧目录。已保存答案、缺分和原失败均保留，不自动重试。
- **审查修复。** 反例覆盖非对象 state 导致终态丢失、长 stderr 截断前未脱敏、启动前和评分错误未进入 Agent 摘要、私有文本以 Python／JSON 转义后漏脱敏。脱敏所需的 gold/reference 只在外层 runner／评分边界使用，未传入生成调用；生成侧只拿已公开给它的请求、资料和配置脱敏值。
- **无模型验证。** 工作台、Notebook 执行、证据映射、RunViews、费用保护与 readiness 扩展回归 **1408 passed**；最后修复后诊断／CLI／RunViews／readiness 定向 **49 passed**，生成边界／Notebook／证据定向 **117 passed**，这些集合有重叠，不合计。仅 1 条既有 Starlette 弃用警告。TypeScript、Expo Web 构建、隔离 Chromium 执行流程、skill 格式校验及 diff 检查通过。浏览器实际使用两个 Git worktree、真实捕获 API 和两次无模型 smoke 执行，覆盖第二项选择、切换任务、单项预选、超时校验、请求丢失恢复和旧金额请求保持；错误展示与取消交互使用合成响应，真实失败由 Python 子进程回归覆盖。
- **交付范围。** 更新步骤见[服务器交接](server-workbench-handoff.md)。远端 SN `970b6793`、Paseo `0.11.2`、pi `1.1.0` 仅来自用户反馈；本轮没有连接服务器、修改远端 SN 或运行真实模型，离线通过不证明该组合及公司内部服务已可生成。本机原任务库与历史 run 未用于测试，既有其他文档改动未纳入本次交付。


## 2026-10-14 · 假设卡独立 worktree 与固定三题闭环完成

- **新增能力。** 已批准的假设卡可从可信 SN 原树的 committed HEAD 新建独立 Paseo worktree，保留卡片起始快照；更改页选择目录与各卡片、会话、捕获源码的身份分别保存。外部创建结果未知时只读协调，不重复派发。已在本地工作台启用并部署；随后用户具体批准后，真实 daemon 创建、基线／候选捕获与两次有效对照均已完成，原失败也有独立回执。
- **审查修复。** 补齐派发复核中的卡片 ID、旧卡片 brief／泄漏扫描的历史绑定、旧 NULL-cwd 会话的切换前目录冻结，以及已捕获源码 A 在当前选择 B 时的真实来源标签。缺失、损坏或多目录同源码导致的来源歧义在派发前拒绝，不猜测当前目录。独立审查已复核四项修复，无未解决的 Important／Blocking；范围不含 provider、真实实验与全量历史实现。
- **验证与部署。** 两组相关 Python 检查分别 66 passed、60 passed（有重叠，不合计为独立用例数）；1 条既有 Starlette 警告。TypeScript、Expo Web 构建及隔离 Chromium fixture 通过；fixture 包含创建独立目录、切换、实现者只收到工作说明、旧分析会话回访，并保留附件竞态场景。四个既有 execution 均为终态后重启受管工作台；HTTP 200、能力入口与草稿可读，首页／脚本和验证构建逐字节一致。保留旧静态资源与运行工件。
- **真实分析与独立实现完成。** 新 task `da5e6fdf04504a1590ce3ed56282789e`；pi 分析 Agent 完成固定单篇三题观察并起草卡片。独立复核修正了“渲染前排序却声称预算结果不变”的设计，修正版只重排最终已发出的证据行，保留正文、截断、原引用键及映射。独立 pi 实现后，相关 271 项 SN 检查通过，独立 9,331 组排序性质检查通过，四个文件哈希复核一致、泄漏扫描 clean。全量 backend lane 中断且无结果，未宣称全量验收。
- **第 1–3 步已完成并停止。** 用户先批准卡片及 ¥1，原基线因向量调用约 60 秒失败并熔断而在 importing 终止，三题 missing 保留，预算未触发。用户随后要求上调且低于 ¥8；总上限设 ¥7.50，恢复基线与候选各 ¥3，使用同一三题计划与原捕获基线源码。两次有效运行均 3/3 success，官方评分完成且配对 admitted。answer_f1 逐题为 0.256410→0.250000、0.237288→0.260870（guard）、0.117647→0.800000；三条预测兑现两条，目标题失败，本轮不采纳候选。均分 0.203782→0.436957、evidence_f1 0.133333→0.612795，但前两题改写与入选证据不同，原因未决。三次 SN 账本合计 ¥0.0437579（含原超时请求预留 ¥0.0110385；非供应商账单，不含 pi Agent 费用）。工作台卡片已结论，未合并、未追加 stage replay 或更多实验。详见[实验报告](experiments/2026-10-14-r1-ordering-loop.md)；全部回执在 `var/sn-eval/r1-loop-20261014/`，执行范围见[本轮计划](superpowers/plans/2026-10-14-hypothesis-worktree-loop.md)。

核实基线：2026-10-14（北京时间）。本页是当前快照；详细历史、旧命令和交接上下文见[文档导航](README.md)、[历史归档](archive/README.md)和[源码执行与工作台历史记录](archive/2026-10-source-execution-history.md)。

本页保留已记录的代码／实验事实及阶段进展；当前目标、不变量和执行范围以 [CURRENT_STATE.md](../CURRENT_STATE.md) 为阅读入口。下方历史数字不代表本次重新验收，服务器状态需读取实际工件确认。

## 2026-10-14 Agent 角色、任务说明、读侧工具与假设卡

按用户要求实现理想形态的前两项：Agent 自己取上下文；带预测的假设卡加分析者／实现者分离。

- **角色与工作目录。** 会话记录 `role`、`cwd`、`hypothesis_id`（`task_sessions` 新增可空列；旧会话为空，沿用原规则：绑定 worktree，否则评测仓库）。从输入框菜单新建的是**分析 Agent**，工作目录为评测仓库，对 SN 只读；**实现 Agent** 只能在“更改”页从**已批准**的假设卡新建，工作目录为 Task 绑定的 SN worktree，卡片随即变为“实现中”。读会话、读时间线、发消息统一使用会话自己的 cwd。
- **任务说明（`brief` 附件）。** 每个会话第一条消息自动附上按角色生成的说明，输入框里可见、可移除；是否已送达以服务端已确认的消息记录为准，不重复附加（任务运行摘要同理）。分析说明包括任务目标、评测仓库与 worktree 路径、`scripts/sn-eval` 命令、假设卡格式示例、10 份评测文档索引（CURRENT_STATE、路线图、评测状态、失败账本、假设卡目录、benchmark 协议符合性、结果表口径、比较复核、背景、sn-eval skill）和规则（引用具体 run／case；低词面 F1 不等于语义错；基础设施失败不算 SN 质量；不运行会调用模型的评测；不改 SN）。实现说明只含卡片的标题、组件、机制、实现说明、可能副作用、改动范围、worktree 起点 dirty 列表和规则，不含 case id、题面、gold、分数。
- **读侧工具 `scripts/sn-eval`（`rag_eval.workbench.agent_tools`）。** `task / runs / cases / case / flips / hypotheses / docs / hypothesis-draft / leak-check`，实现角色只允许 `card / leak-check`。它和 `serve` 用同一套配置加载与 RunViews（含执行记录自动发现的 run）。已在真实库上对“测试4”跑通 `task / runs / cases`。
- **假设卡。** 新表 `hypotheses`：草稿 → 批准／驳回（只能由人在界面操作）→ 实现中 → 已核对预测 → 已结论。卡片字段包括组件、机制、证据、替代解释、逐题预测（improve／unchanged／not_worse／worse，可标守护题）、否定条件、预期受益与可能受害、改动范围、实现说明。核对时选基线 run 和改动后 run，要求两者通过配对准入，再逐条判定预测是否兑现；效果（支持改善／未观察到改善／退化或取舍／证据不足）与原因（得到支持／被反证／未决）分开填写。每一步写入任务历史，记录页显示中文。待批准和待结论的卡片计入侧栏待决数，并在工作页显示提示行。
- **隔离护栏。** 服务端拒绝把 `case`、`run_case`、`task_runs`、`result` 附件发给实现 Agent，也不允许把别的会话的说明发过去。泄漏扫描比对 worktree diff 新增行、实现说明，与本任务运行的题面（≥24 字符）、gold 答案／证据／参考答案的 8 词片段、case id；分析 Agent 提交草稿时若实现说明重叠即拒收，核对预测时把 diff 扫描结果写进验证记录。**限制**：读文件这一层没有沙箱，full-access 的实现 Agent 技术上仍能读到评测仓库；隔离依靠信息不下发、角色说明和事后泄漏扫描，不是强制隔离。
- **验证。** 新增 `tests/test_workbench_roles_hypotheses.py`（6 项：角色 cwd 与实现者需已批准卡片、实现者拒收 benchmark 材料、两种说明内容、卡片生命周期与预测核对、泄漏扫描、工具角色限制与草稿泄漏拒收）；fixture 浏览器流程新增“说明只发一次”“摘要与说明同时附加”“更改页批准草稿、未绑定时不能建实现 Agent”。此前全量 Python 日志为 2157 passed、1 skipped（本地官方 ROUGE 未配置），不含下述后续前端修复。
- **接手后补完附件竞态（2026-10-14）。** 运行列表晚于工作说明加载时，原先每份草稿只尝试一次的标记会阻止附加运行摘要。现在每种附件在条件就绪时各尝试一次，合并时工作说明保持在前，用户移除的附件在同一草稿内不自动补回。浏览器用请求门控推迟真实 fixture 的 run 列表，先观察旧代码在摘要附件处超时，再确认修复后的聊天、附件顺序、已送达去重和假设卡批准流程通过；不调用真实 provider。TypeScript 检查与 Expo Web 构建通过；角色／假设卡、消息、上下文和 R1/M2 接缝的 47 项 Python 检查通过（1 条既有 Starlette 弃用警告），本次未重跑全量。限定改动的独立只读审查无重要发现，移除与实现者边界由代码复核及相关后端测试覆盖，并非新增浏览器场景。已验证的前端构建更新到受管工作台，HTTP 200，实际返回的首页和脚本与测试构建逐字节一致；只更新静态文件，无服务重启、真实 Agent 消息或 SN 模型运行。
- **过程中的一次失误。** `sn-eval` 第一版直接读取配置，把相对路径 `..` 按当前目录解析，向用户任务库误写入一个 location 为 `.worktrees` 的空 workspace。发现后改用 `load_config`，确认没有任务引用后删除了该行；原有 workspace 和任务未受影响。

## 2026-10-14 工作页改为 Agent 对话，修复消息发送

**界面真实运行（n=1，用户执行，本轮回读工件核实）。** 修复启动问题后，用户在任务“测试3”中从界面运行 QASPER chunk 固定 1 题（计划 `14b84613`，execution `3d2d7894…`）：状态 finished、exit 0，独立官方文本评分 answer_f1 0.464／1、evidence_f1 0.000／1（scores `4e853b27`），阶段诊断证据召回 2/2、上下文精度 2/16，scheduled 调用 19 次（chat 5、embedding 14），问答耗时 7.9s，已加载源码 HEAD `0680aa1`、ref `3bdc4b22`。这是界面生成→官方评分链路的第一份真实工件，不是成绩、比较或 R1 全闭环。该题因 answer_f1 < 0.5 进入待读，不是执行失败。

**消息发送失败的根因。** 用户三次发送（“说明这次实验的结果”、两次“你好”）全部在服务端被 bridge 以 `not_attempted / SCOPE_MISMATCH` 拒绝，没有到达 Agent。原因：Task 绑定 SN worktree 后，Agent 创建（`agent_creations.py`）和会话读取（`sessions.py`）用的是绑定 worktree，而发送（`agent_messages.py`）仍用 workspace 原位置作为 `expectedCwd`。现在三处使用同一作用域；旧的三条拒绝记录保留不改。`tests/test_r1_m2_seams.py` 新增发送作用域断言。

**工作页改动。** 按用户反馈对齐 Paseo：主区只有任务卡（目标、run 卡、SN 改动卡、待读行）和与当前 Agent 的对话；底部一个输入框，上面有“材料”和 Agent／模型选择。去掉工作页的调查笔记、会话管理面板、interrupt/steer 选择和“新消息草稿”。笔记 API 与历史记录保留。具体行为：
- 打开任务时自动选中最近关联的 Agent 并读取对话；Agent 运行中和发送后 90 秒内每 3 秒静默刷新，不再需要手动“刷新只读消息”。
- 发送默认 interrupt（Agent 运行中时输入框上方提示会打断当前一轮）；Enter 发送、Shift+Enter 换行；送达后自动开始下一条草稿；服务端确认未发出时显示原因并保留原文。
- Agent 菜单列出本任务的 Agent，可按服务器允许的模型配置一键新建，并说明新 Agent 的工作目录（绑定 worktree 或评测仓库）；“关联已有 Agent ID”收进菜单。
- “材料”菜单先预览 Agent 将收到的确切文本，再附加；工作页只说明“只收到你发送的文字和附加的材料”。
- 记录页把事件名改成中文结果，隐藏 prepared/started 中间事件（导出仍完整）；侧栏去掉重复的“当前任务记录”，待读题改用琥珀色点；待读规则显示为中文；首次用访问链接打开时不再因请求先于登录而显示“run 视图接口不可用”；run 列表读取中显示“正在读取”；计划页去掉“选择输入 · N”“查询原 key 回执”“读取 head 并追加”等内部术语。

**新增 run 案例附件。** 消息附件新增 `run_case`（已登记 run 的单题），内容包括问题、SN 答案、逐题官方分、阶段指标、gold 标注、引用、按排名的检索块（是否被引用／是否含 gold）、资料身份与 run 工件路径，并标明“仅供分析，从不进入 SN 生成”。论文全文不内联。原结果案例附件（`case`）仍只含问答与状态。run 案例证据页新增“附加到 Agent 消息”。这让分析 Agent 第一次能从界面拿到动态 run 的完整单题证据；是否足以支撑可行动诊断仍待真实使用判断。

**验证。**
- Python：消息、会话、上下文、RunViews、M2 接缝定向 104 passed；全量 `pytest tests` 2150 passed、1 skipped（QMSum 需本地官方 ROUGE）、1 条既有 Starlette 弃用警告。
- 前端：TypeScript 检查、Expo Web 构建通过。新增 `apps/sn-workbench/tests/chat-browser-server.py` 与 `chat-browser.mjs`（合成 Agent，不调用 provider）：新建任务 → 菜单新建 Agent → Enter 发送 → 回复经轮询自动出现 → 附加任务目标再发 → 新建第二个 Agent 并切回 → 记录页显示中文结果，全部通过。
- 真实 smoke（按用户要求用 pi，`pi/deepseek/deepseek-flash`，配置 `pi-flash`）：在“测试3”（绑定 `sn-eval-exp-1`）新建 pi Agent，第一条连通性消息送达并收到“收到”，provider 中途有一次超时重试；第二条附加该 run 案例证据，Agent 给出一句差异分析。该分析是模型候选，未经人工核验。
- 受管服务在无活跃执行时经 `paseo script stop/start workbench` 重启，端口变为 44899，代理入口不变；重启前 dist 备份为 `apps/sn-workbench/dist-before-chat-20261014`。

**同日后续：用户“测试4”暴露的两个问题。** 用户在“测试4”（QASPER chunk 3 题）问 Agent“这次实验的结果怎么样”，消息没有附件，Agent 只能自行翻仓库，答的是 H1 旧记录。另外这条消息显示在 Agent 回复下方：回复前有 49 项活动，原来的 50 条读取页装不下用户那条，界面就把本地“已送达”气泡排到了底部。修复：
- 新增 `task_runs` 附件“本任务的评测运行”（每个关联 run 的状态、失败阶段、官方分与分母、阶段指标、待读题、源码 ref、run_dir）。每条新草稿自动附上它，输入框里可见、可移除；同一 Agent 已收到相同快照时不再附加（浏览器本地记录）。单题证据仍需在案例页附加 `run_case`。
- 对话读取页从 50 条提到 bridge 上限 100 条；本地气泡按发送时已有的条目定位，始终在回复之前。
- 验证：fixture 新增有 run 的任务、120 项活动挤出用户条目的场景，浏览器流程通过；全量 Python 2151 passed、1 skipped。正式服务上只读预览“测试4”摘要正确列出 3 个 run，没有向用户的 Agent 发消息。
- “测试4”的 partition 0001 失败在导入阶段：DashScope `text-embedding-v4` 返回 `ModelServiceUnavailable`（`model-events.jsonl`），是外部服务失败，不是 SN 回答错误。摘要里只显示 `ValueError / importing`，根因需读 run_dir。

**限制。** bridge 只投影工具名和固定活动标题（错误正文、工具参数不可见，界面显示“详情只在 Paseo 中可见”）；用户消息过长时 bridge 截断正文，界面从截断 JSON 中恢复输入文字与附件标题。旧浏览器脚本中依赖已删除控件的部分见 [TRIAGE](../TRIAGE.md)。对话与 run 卡不按时间交错（timeline 条目没有可用于跨源排序的时间戳投影）。假设卡仍无界面创建入口。

## 2026-10-14 用户界面运行的启动失败与修复

用户已从界面派发两次 QASPER Job（3题与1题），子进程均在 run-dir 创建之前因共享 store 与输入目录的隔离校验退出：工作台消费的是 store 内已经冻结的 bundle 对象，runner 将合法父子归属关系误判为输入／存储重叠。两次没有 manifest／答案／官方评分，预算 ledger 请求数均为 0；不是题单操作、预算不足或 SN 澄清造成。原失败 execution 与工件保留，没有自动重跑用户题目。

修复落在 runner 隔离边界：只有 pinned index 与 bundle 校验通过且路径等于 store.resolve(bundle_ref) 的不可变已安装对象可重叠，其余输入／代码／产品／run 隔离仍要求。新增反例先失败后通过；对用户本次固定题单和冻结配置在临时目录做禁止网络的启动检查，成功完成实际源码／依赖／模型注册初始化并保存 manifest／planned，在 predictions（资料导入、模型请求）之前主动停止。第一次离线检查停止边界选晚，进入资料导入但 socket 连接全部被拦截，不能作为评测或模型结果；更正后没有调用资料导入和生成。

修正计划页的说明，明确草稿意图不随 Job 改写，实际绑定／执行在更改页；工作页不再对已派发任务机械提示“仍需确认预算”；失败 run 从 batch plan 展示固定 suite／mode，同时不伪造已加载源码身份。相关 72 项 Python 回归、TypeScript 与 Expo 构建通过，1 条既有 Starlette 弃用警告；工作台在无活跃执行时重启应用修复。真实生成和官方评分仍需用户新的显式运行验证。

## 2026-10-13 R1 M4 真实 runner 的界面接线

当前本地配置已从无模型 smoke 切到可信的 captured Notebook runner。界面显式选择计划，准备时捕获输入／源码，确认费用上限与超时后按分区串行启动新进程，之后独立官方文本评分；执行历史自动发现关联 run，失败／取消／超时及原工件保留。未知响应保存原请求并允许同键恢复；明确匹配请求的领域拒绝可解除阻塞。取消与超时监督整个进程组，不只等待 leader。

本轮未新增模型实验。独立 Chromium 验证使用 smoke executor；真实环境仅做捕获准备／argv 构造／分区展开／解释器核对（1题1分区，启动0进程）。真实 wrapper 以无凭据测试输入验证子进程失败保存；已有 E5 真实单题的官方分与案例回访经严格绑定读取。最终相关回归 102 passed、1 条弃用警告；TypeScript／Expo 构建、浏览器恢复场景和独立 28 项复核通过。受管服务 healthy、代理 HTTP 200，当前用户库既有 4 任务只读核对通过。详细入口、验证与局限见 [界面执行](workbench-execution.md)。下方为此前各阶段的历史证据；新 UI 的真实生成／评分仍待实际执行，不将 M4 或 R1 全闭环记为已验收。

## 2026-10-13 R1 M4 执行层首版

M4 首版加入 opt-in 的 Job execution record：旧 Job v1 receipt 保持只读，执行层要求 verified preparation 与显式预算确认，后端构造固定 argv，在独立 run-dir 中以进程组启动并保留 `state.json`、stdout/stderr；完成、失败、超时和取消均可查询，重复 request key 幂等。初版 HTTP／CLI 与更改页已接线，当时默认配置关闭执行；后续 UI smoke 接线和证据见下一节。本轮通过 5 个执行层测试及 107 个既有 Job／准备／RunViews 回归，尚未使用真实模型做 M4 smoke，因此不把它记为 R1 闭环完成。

## 2026-10-13 R1 M4 UI 生命周期 smoke

工作台更改页现可在可信配置下完成：绑定已登记 worktree → 从最新固定计划创建 Job → 服务端捕获当前输入与 SN 源码 → 显式确认预算 → 独立 run-dir 执行 → 轮询状态与取消。新增 preparation HTTP 接口只读取服务端 `job_preparation` 绑定，不接受前端命令或路径。`configs/sn-workbench-local.json` 的执行模板是本地 deterministic smoke executor；它只写 `state.json` 与 smoke marker，不加载 SN、不调用模型、不生成官方分数。

独立临时 state 的 HTTP 彩排已通过，终态为 `finished`，run-dir、`execution-record.json`、`stdout.log`、`stderr.log`、`state.json` 和 smoke marker 均保留；相关 Python 定向回归为 121 passed、1 warning，前端 TypeScript 检查通过。该证据只证明 UI／Job 生命周期和隔离边界，真实 `run_notebook_benchmarks.py`、模型服务、官方评分与配对翻转仍待明确授权后的真实环境 smoke。

## 2026-10-11 工作台首个开发切片

工作台首个切片实现了 Expo Web 与 Python HTTP／CLI 的任务、冻结结果导入、成绩／案例阅读、人评笔记和修订历史；QMSum、MultiHop、ALCE 仅为历史工件，旧 QASPER 因身份不一致拒绝，不能据此视为当前服务器成绩。A 阶段随后接入 Paseo Task-session、候选与人工决定，保留 SDK ACK／执行完成分离和 unknown 不自动重发；工作台回归与浏览器验证通过，但没有 benchmark／judge、Job、SN 改码或正式模型成绩。原始过程和限制见[归档](archive/2026-10-source-execution-history.md)。

## 2026-10-12 工作台 B／C 首切片

B／C 首切片已在独立 canonical／state 范围闭合：持久计划草稿、固定修订回访和冻结结果只读比较可用，比较复用既有准入核心，保留失败诊断分类、左右不对称和写锁预算未测等限制；`execution=false`，没有执行 Job、模型或新增正式成绩。详细评审和限制见[归档](archive/2026-10-source-execution-history.md)。

## 2026-10-13 源码执行 E3d

E1/E2、E3a/b/c/d 在各自范围闭合，captured 启动消费者记录私有 TOML、有效 Settings、owned registry 和 scheduled Future 代际，仍保留原审查红灯、root 见证目录不符失败以及依赖／执行字节未冻结的限制；E3d 不调用真实模型，M1 仍未整体关闭，journal 配对、完整历史和逐题 Dashboard 待后续，Job 仍不可执行。证据和原限制见[归档](archive/2026-10-source-execution-history.md)。

## 2026-10-13 E4 保存 journal 审计

E4 完成保存 journal 的 run 级结构审计及 reader／aggregation／report 消费，保留配对、pending、截断、legacy 计数和拒绝歧义的原语义；结构审计的 `history_exhaustive=false`，不证明完整历史、HTTP／模型／scorer／Job 完成，也未接入逐题展示或 UI。原红灯、审查数字和 hash 证据见[归档](archive/2026-10-source-execution-history.md)。

## 2026-10-13 E5 evidence-only 完整彩排

E5 evidence-only 彩排用固定 S1 bundle/index/SN ref、公开依赖和 synthetic project 验证 owner/wrapper、network namespace guard、pidfd `waitid`、exit 0、输出排空及独立落盘，定向回归 7 项、相关源码执行／生命周期回归 125 项；状态保持 `diagnostic_complete`、`offline=true`、`evidence_only=true`、`job_executable=false`，诊断分不等于官方分。S3 readiness receipt 保留 `ready`／`blocked`／`unknown`、严格 binding 缺口和未观测 Settings／凭据／reachability，S3b 仅是显式 captured CLI 的可选启动前校验，不能提升 Job。详见[归档](archive/2026-10-source-execution-history.md)。

## 2026-10-13 E5 真实固定单题链路（S3 生成＋S4 官方评分）

实验配置下已完成一次固定单题 QASPER chunk 的真实 captured CLI 链路：零网络 preflight、ready receipt、owner helper、答案／evidence snapshot、20 次 scheduled 调用、独立 `export-sn`／`score`（answer_f1 0.6667、evidence_f1 1.0）和 `load_run` 回访均有工件；这是 n=1 的接缝事实，不是 benchmark 成绩或比较。原 SN 配置缺两个 strict chat binding，本次使用评测侧私有实验配置，原树／`.env`／数据库未改；owner 残留后代完整性 unknown，S2 对应复核未执行，Job 仍 `executable=false`，UI／preview／user state 未改。完整证据与限制见[归档](archive/2026-10-source-execution-history.md)。

## 2026-10-08 结果保存与导出改造

当前工作树已实现共享不可变 bundle/index/evaluator/SN 工件、固定 canonical 安装 index ID、操作内读取上下文、五套 compact scoring projection、private results/review 角色包与 logical/allocated inventory。runtime 仍逐 run 隔离；原始 observations、证据/排名回放、状态、分母与逐条持久化保证保持。补评、ALCE attachment、Dashboard 和 review 迁移读取已接通。原数据/旧 run 不迁移、不删除。

最终完整离线 Python **685 passed，1 skipped**（未配置可选本地 Perl ROUGE）；Node **34 passed**。独立审查 162 项相关检查通过，发现的索引身份、缓存范围和输出路径重叠问题已加反例修复。合成 1k/5k/10k 工件完整导出每次 canonical 重建一次，review 包迁移读取题数一致；详细时间、体积、内存及测量局限见[验证记录](result-storage-export-verification-2026-10-08.md)。这些不是 SN／模型实验，不代表服务器性能或正式成绩。

代码位于 `feat/benchmark-protocol-correctness`，基线为 `bcea0c5`。用户于 2026-10-08 随后授权同步这轮改造；提交／远程身份以 Git 和交接消息为准。本机未运行服务器。新操作指南见[共享存储与回传](result-storage-and-export.md)，待服务器指令见[交接 prompt](server-result-storage-export-prompt.md)。服务器现有 run 的实际版本/进度本次未重新核实。

## 2026-09-29 仓内退役清理

仅保留 **QASPER、MultiHop-RAG、ALCE、QMSum、HotpotQA**。其他旧 benchmark 专属代码、CLI、测试、配置、受跟踪样本／结果及文档已删除；共用身份、runtime、模型客户端、结果账本与报告已迁到独立模块，没有保留旧模块转发壳。Dashboard 测试使用真实构建的合成 QASPER bundle，继续执行冻结来源和计划重建校验。

本轮离线 Python 回归 **538 passed，1 skipped**（未显式配置本地 Perl ROUGE）；Node **34 passed**。清理前基线为 769 passed、2 skipped；数量减少主要来自退役专属测试退出，同时保留／迁移通用状态、分母、trace 与工件校验。实际删除逐项见 [DELETION_LOG](../DELETION_LOG.md)，未处理债务见 [TRIAGE](../TRIAGE.md)。本轮未进行真实模型实验、服务器清理、提交或推送；下方为各日期历史证据。

## 当前代码与 Git

- 本地 `benchmark-deepeval/main` 已合并 Dashboard、原生评分恢复及文档整理，HEAD 为 `e022c60`。
- 当前实现位于 `.worktrees/benchmark-protocol-correctness`，分支 `feat/benchmark-protocol-correctness`；HotpotQA、外部方法比较、文件题单及文档改动已提交并推送，尚未合并到本地 `main`。服务器执行时必须记录实际 checkout SHA。
- Dashboard 合并提交为 `e4c5333`，原生评分恢复合并提交为 `03ca577`。
- 旧远程开发分支信息见归档，本轮未重新查询远程。
- 主线原有未提交文档已复制到实现worktree继续整理，主线原文件保留。产品checkout没有新增受管理改动；原有 `.deepeval/`、`results/` 未清理。

## 当前可用入口

### Dashboard

`scripts/build_experiment_dashboard.py` 读取已有 run，生成实验地图、单题流程/原生 span 回放和结果分析。它不会重新问答、重新评分或修改原始工件。使用和比较限制见[实验 Dashboard](experiment-dashboard.md)。

MultiHop 的 SN chunk 路径另保存 native retrieval snapshot（`sn-multihop-chunk-selected-passages-v1`），可按官方检索脚本回放；reasoning 路径没有等价单一排名，检索指标仍 pending。

### Notebook 场景

HotpotQA 已加入当前 suite：本地已完成固定 distractor validation 全量 7,405 题/73,700 段落适配、句子 source units、12 项指标与原版 CLI 的合成校准及原生 answer/sp 导入；SN 最终引用到句子级 supporting-fact 的投影已实现，完整公开语料经实际 parser/chunker 校准；服务器真实运行和回放尚未完成。

当前分支：prepare默认 `notebook-data-v3`，SN/Agent CLI默认无gold的 `notebook-request-v3`。新增 `run_benchmark_reference.py`（BM25/full-context/ALCE candidate-topk）、`benchmark_protocol.py`（答卷导出/官方评分/外部答卷导入）和 `compare_benchmark_submissions.py`（严格身份、分母、逐题配对、分组bootstrap及实际成本观测）。外部答卷导入要求显式 case map，保留原始预测和源文件哈希，支持完整 scope 或明确子集；published-reference、oracle/gold-input 条件不能进入 paired comparison。执行与比较限制见[当前实验计划](notebook-benchmark-experiment-plan.md)。旧Notebook诊断、QMSum单会议baseline和Dashboard入口保留。

五套 benchmark 的标准定义、官方依据、精确评分规则、代码对应及已验证/适配/待验证边界已整理为[标准与实现符合性](notebook-benchmark-standards-and-conformance.md)。Python API 的旧默认值仍保留；新调用需显式选择 v3，不能把 CLI 默认推广为所有接口默认。

外部方法登记见[机器可读登记表](../configs/notebook-external-method-registry-v1.json)：五个固定 scope、二十九个 method 条目，包含 SN 主方法、候选和已重评分公开答卷；仅 `results` 及对应工件代表已执行结果，新增 SN 条目仍为 pending。QMSum gold-input 排除依据见[来源审计](notebook-external-method-audit-2026-09-26.md)，最新完整答卷与复现命令见[外部结果入口](notebook-external-results-2026-09-28.md)。

KG2RAG 新增受控入口 `scripts/run_kg2rag.py`，全量 7,405 题无 gold 输入已准备。保留作者检索/图流程，QA 换用训练示例，KG 提取去掉验证示例，缓存按完整 context 区分，错误独立记录。它是明确标识的 adapted 方法，真实依赖和模型运行尚未验收。QASPER LAB 公共包不含已发布预测，但已新增 `run_lab_qasper.py`，复用作者 LongChat citation；完整 416 篇/1,451 题的无 gold 数据、三训练示例、原图渲染/配置/解析均已离线验证，真实模型仍待服务器执行；详见[外部结果](notebook-external-results-2026-09-28.md)。

ALCE VANILLA 新增 `scripts/run_alce_vanilla.py`，复用固定作者的两示例、前五候选提示和 HF 生成函数，ASQA 948、ELI5 1,000、QAMPARI 1,000 题的提示/所见文档与作者 main 逐题一致。运行时保存实际引用映射，输出可进入完整官方评分；本地没有执行真实权重或 AutoAIS。加载、随机数和 BOS 预算差异明确记录，见[外部结果 §7](notebook-external-results-2026-09-28.md#7-alce完整答案与引用的-vanilla-受控方法)。

比较后的案例复核新增 `scripts/review_benchmark_comparison.py`，从已核验报告按任务/逐题指标/分差方向抽样，输出答案、参考、观测证据和独立空白人工记录；另从全范围逐题结果汇总各任务均值、分母和分差方向计数，与抽样及原 batch 估计明确分开。真实 MultiHop/ALCE 最新 `case-review-task-summary/` 工件及指标解释见[案例复核文档](notebook-comparison-case-review-2026-09-28.md)。

### 原生 Agent 与组件补评

`scripts/run_notebook_agent.py` 使用 `sn-deepeval-native-v1`，默认评检索/合成组件；`--trajectory` 才评完整 Agent 轨迹。回答和组件先保存，judge 随后评分。`scripts/score_native_components.py` 可从已保存组件建立独立补评目录。入口、失败语义和超时边界见[原生 Agent](native-agent-evaluation.md)与[评分恢复](native-scoring-recovery.md)。

## 历史验证证据

以下 2026-09 阶段计数对应当时的测试集合；退役清理和后续存储改造已经改变集合，不能与当前数量混用。最新已记录离线验证见本页 2026-10-08 条目；本次文档澄清未重跑全量测试或核实服务器进度。

2026-09-28 当前代码完整回归：**769 passed，2 skipped**。新增 SN MultiHop chunk native ranking capture、官方检索回放字段及 3 项定向测试；`sn_retrieval.py`、runner 接入、官方检索回放和既有 v3 runtime 共 47 项定向测试通过。题型自动汇总新增 2 项测试，review/comparison 合计 78 项通过；独立只读复核未发现实质问题。MultiHop/ALCE 已生成新汇总报告，原比较哈希、抽样选择及案例内容不变；MultiHop 2,556 题的四个题型统计与此前独立分析一致。两个跳过项仍需显式 native judge checkout 或本地 Perl ROUGE 环境；QMSum 的真实 Perl 重评分已另行完成。

此前案例复核初始阶段完整回归为 **764 passed，2 skipped**。新增比较案例复核入口的 4 项测试，MultiHop/ALCE 真实复核工件已生成；此前 760 项为 Hotpot 投影阶段。Hotpot SN 投影新增 7 项定向测试，涵盖 SQL 快照、跨文档句位、截短、无 gold、失败状态、完整 run/export/score 输入及伪造证据拒绝；此前 753 项为 ALCE VANILLA 阶段。ALCE VANILLA 新增 4 项定向测试；2,948 题与实际作者 main 的提示和所见文档逐题一致，实际 HF 生成分支仅以替身模型验收。真实生成、模型加载与引用模型评分未执行。此前 749 项是该入口加入前的回归。其中 KG2RAG 新增 5 项、LAB 新增 4 项定向测试；以下 734/729 等计数为此前阶段记录。HotpotQA 完整 7,405 题的合成扰动校准已实际执行原版 CLI，12 项指标最大绝对差 1.78e-14，分母均为 7,405；这些不是模型成绩。独立只读复核逐题对照 73,700 段落、原始标注和句子编号，并通过 10 项定向测试，未发现重要评分问题。

2026-09-28 后续已完成 Socratic SegEnc 的 281 条公开测试答案导入与真实 Perl ROUGE：R1=0.38955、R2=0.13960、RL=0.33942，分母均为 281，35 场会议，0 空答案/缺失。逐行映射来自作者公开预处理和保存顺序，原运行 input manifest 不可得；保留该限制，不声称完全恢复生成条件。ALCE 新增互斥的 `--alce-answer-only`，执行原始 CLI 的非引用指标，引用分保持 pending；未启动真实模型。针对两项新增功能的 31 项测试通过，独立只读审查未发现重要问题。此前 734 项全量回归为这些新增功能之前的基线。

2026-09-28 此前全量回归 `.venv/bin/python -m pytest -q`：**734 passed，2 skipped**，跳过原因仍为显式 native judge 产品 checkout 和本地 Perl ROUGE。新增原生 Multi-Meta/ALCE 导入验证，以及公开排名评分、`model_answer` 识别、ALCE 缺引用映射时阻止完整引用评分的回归。实际完成完整 MultiHop 导入/重评分、原官方 CLI 对照、八份 ALCE 样本导入，以及两份外部答卷比较报告。

本轮独立只读审查还重建并核对两份完整 Multi-Meta 与八份 ALCE 的导入行/映射、来源哈希、空答案分母及 ALCE 完整评分阻止边界，未发现本次范围内需修复的重要问题；没有重复运行全量测试或改变源工件。这不代表已验收服务器模型执行。

2026-09-28 本轮全量回归使用 `.venv/bin/python -m pytest -q`：**729 passed，2 skipped**，跳过原因与下方一致。新增文件题单回归覆盖空/重复/含空白 ID、真实 CLI 导入与 SN 导出范围、reference 不意外跑全量、完整资料保留和 missing 分母。直接调用 pytest console script 曾因 `scripts` 不在导入路径而在收集阶段失败，改用上述模块入口后通过，未修改产品逻辑绕过测试。

本工作树2026-09-26最新代码回归（包含 HotpotQA 适配、ALCE 分句资源隔离与 QASPER 最终引用证据接入）：

- Python：运行全量 pytest，**721 passed，2 skipped**；2 个跳过项分别需要 sibling project/native judge 或显式本地 Perl ROUGE，不涉及 HotpotQA。HotpotQA、外部答卷导入、条件指标逐题配对、比较准入、批量-only 指标和 cluster bootstrap 测试均包含在这次回归中。此前704/716项基线记录保留。
- Dashboard JavaScript：`node --test tests/dashboard_core.test.cjs tests/explorer_core.test.cjs tests/map_view.test.cjs`，**34 passed，0 skipped**。
- 独立审查复核了ALCE引用预处理/实际分母、QASPER证据槽、v3请求限制和依赖身份。已发现的问题均修复；未发现仍开放的可复现重要得分/比较错误。这不代表已经验收ALCE真实模型推理。

本分支已验证完整QASPER（416篇/1451题）和QMSum（35场/281题）适配，逐题gold变更不会改变公共材料/请求。QMSum真实Perl与独立pyrouge封装已校准。QASPER与QMSum各一题的SN/BM25真实生成、官方评分与比较报告成功，均只作链路验收，不作排名。QMSum SN原Python ROUGE诊断缺可选包而error，答案保留后由新Perl评分独立成功；没有把旧诊断错误隐藏为正常运行。

2026-09-24已补齐QASPER最终引用→原段落→官方Evidence F1：新v3 SN运行自动保存有界证据快照，旧运行可用 `export-sn --qasper-evidence` 只读恢复到新submission；评分回放不依赖live数据库。未知引用保留假阳性槽，缺观测/歧义为映射错误，保留答案且不报部分分母的Evidence F1。规则、职责及适配声明见[标准文档§3.5](notebook-benchmark-standards-and-conformance.md#35-2026-09-24-已实现的最终引用投影与特殊情况)。

真实保存的一题 `[k1]` 恢复为 `4:0`，与未修改原版CLI一致：Answer F1=2/3、Evidence F1=1，分母各1；151个旧文件hash未变。416篇公开论文经实际parser生成的11,065个完整块和81,316个选定截短检查全通过；23,111个公开单位均可达，gold变更不影响映射。独立审查发现的缺观测误判、分区尾部和Markdown来源边界已修复并回归。该验收没有新增模型调用或修改产品；不代表1451题正式成绩或真实reasoning所有路径已通过。

2026-09-24经用户启用的代理取得MultiHop/ALCE固定完整文件并通过身份校验。MultiHop全2556题/609文章、ALCE的5个普通候选文件均已完整适配。MultiHop官方QA/检索脚本、ALCE原始CLI文本指标和真实NLTK分句范围已对照；详细数据/隔离证据及适用范围见[真实数据验收](notebook-benchmark-real-data-validation.md)。本次未运行新SN/LLM生成；ALCE大型模型评分、正式全量和外部论文方法比较仍未完成。ALCE 的 5 个普通文件和 3 个 `reranked_oracle` 文件是候选资料/标注包，不是模型生成答卷；QMSum HMNet 公开答卷是 gold-input 校准资料，不能作为普通端到端对照，详见[来源审计](notebook-external-method-audit-2026-09-26.md)。

Multi-Meta-RAG 固定提交的 GPT-4/PaLM 完整答卷及实际排名均已取得：两方法各 2,556 题，逐题 query、标注与前六段组成的实际 prompt 全部匹配。固定官方 QA 分别为 **1,549/2,556（0.606025）**和 **1,553/2,556（0.607590）**；PaLM 30 条空答案保留在分母。共享检索的 Hits@10=0.904213，Hits@4=0.792018，MAP@10=0.338816，MRR@10=0.674762，分母为 2,255 非 null 题；原版 CLI 输出一致。已有两公开答卷的描述性配对报告，尚未与 SN 配对。作者完整索引身份未证明、生成模型/提示/预算不同，不能作算法归因；group 数为 1，CI 明确不可计算。

ALCE 固定仓库的 `human_eval` 包含真实生成答案，与先前 ordinary/oracle 候选资料不同。ASQA、ELI5 各四配置/100题已导入，原问题 ID 通过问题原文唯一映射；聚合行不当作题目，空答案不删除。ASQA 四配置的官方 str_em 为 0.353/0.382/0.3655/0.264667，配对报告已生成；ELI5 仅完成输入/预处理，模型指标 pending。原文件没有完整 shown-doc 列表和生成设置，统一限定为文本重评分，完整引用评分被显式阻止。详情及全部命令见[外部结果入口](notebook-external-results-2026-09-28.md)。

历史 146 题前缀仍保留：QA=97/146，[子集执行文档](notebook-multihop-subset-comparison.md)与 `var/external-comparison/multimeta-146-inputs-20260928.tar.gz` 供回放。当前 MultiHop 主范围已升级为完整 2,556 题，输入锁见 `configs/comparison-scopes/multimeta-e77e4638-full.json`。SN、reference、export/import 支持共享 `--case-id-file`。这段记录对应历史准备快照；当前协议已提交并推送，服务器真实实验仍待执行。

## 服务器实验边界

服务器反馈曾报告 QMSum request-v2、BM25、DeepSeek Agent 批次和原生评分超时恢复。数字来自用户转述，本机没有读取服务器原始 run、当前进程或配置；不能把“最近正在运行”当作此刻仍在运行，也不能把缺分当作零分。

用户已明确优先保证新评测正确，允许必要修改及重跑；不要求提供历史服务器目录。正式执行在服务器，本机这次仅按后续授权下载MultiHop/ALCE并做不调用生成模型的验收。新实验均使用独立runtime，不触碰生产数据、部署和timer。服务器旧运行是否仍在进行仍须以实际产物核实，不从历史转述推断。

## 当前下一步

1. 协议修正分支的审查与回归已完成；本地已准备 [campaign 机器可读模板](../configs/notebook-external-campaign-v1.json)、[执行题单](../configs/notebook-external-execution-plan-v1.jsonl)、[预检脚本](../scripts/validate_external_campaign.py) 和 [服务器手册](notebook-external-campaign-runbook.md)。预检已在当前四套本地 bundle 上通过；服务器上的 bundle、模型、scorer、依赖和运行身份仍未冻结，不能把模板当作正式实验。
2. 使用已核实的MultiHop/ALCE文件身份在服务器准备数据及ALCE固定模型环境；下载本身已不再是本机阻塞项，不能用替代模型产出官方完整分数。
3. 已有 MultiHop 两完整答卷、ALCE 八份样本及 QMSum Socratic 281 题答卷；ALCE 仅答案模型评分路径已实现，等待真实模型执行。QASPER LAB 与 HotpotQA KG2RAG 受控方法的代码及全量输入已准备，ALCE VANILLA 已新增保存真实所见文档和引用映射的受控入口，2,948 题输入已验证；接下来在服务器验收实际模型执行与完整引用评分。原 SegEnc/HGN/SAE 下载链接的访问失败和协议限制已记录，不能视为可直接重跑。
4. 新比较报告以JSON/Markdown交付，暂不涉及Dashboard。服务器阶段生成 MultiHop 完整 2,556 题与 ALCE 已冻结 100 题的 SN 答卷，两侧在同环境重评后再比较；当前没有 SN 对公开方法的正式结果。完整来源和 `var/` 工件被 Git 忽略，须独立搬运；操作入口见[最新外部结果](notebook-external-results-2026-09-28.md)。
