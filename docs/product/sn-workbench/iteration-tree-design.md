# 迭代与候选树：工作台交互与数据设计（草案）

2026-10-15。状态：**第一阶段（身份契约）与第二阶段（父／根比较记录，第 10 节第 2 步）已实现**，见 [2026-10-17 smoke 记录](../../experiments/2026-10-17-iteration-identity-smoke.md)与[2026-10-19 链路与比较 smoke](../../experiments/2026-10-19-iteration-chain-and-comparison-smoke.md)；第三阶段（CLI 动词与待确认请求）与第四阶段（界面，2026-10-20；五页签已删除，终端未做）已实现。由用户与协调者讨论收敛，依据[界面先例调研](../../research/2026-10-15-iteration-tree-ui-prior-art.md)和当前代码。第 2 节列出已由用户确认的方向；其余是建议，实现中按真实使用调整。本文不改变 [CURRENT_STATE](../../../CURRENT_STATE.md) 的不变量。

## 1. 要解决的问题

当前工作台以“Workspace → Task → 工作／更改／结果／计划／记录”组织。问题有三层：

- **Task 没有确定含义。** Task 只有标题和目标文本；题单、基线、worktree、假设、run 都是事后挂上去的，侧栏一项可能是一轮实验，也可能是一个话题。
- **“当前被评测的是哪份代码”说不清。** 一个 Task 只绑定一个 worktree（`task_worktrees.task_id` 主键），而每张批准的假设卡又各开一个 worktree；更改页同时展示绑定、diff、“起点 dirty”标签、假设、run 和执行面板。
- **页签按管线产物划分，不按迭代步骤划分。** 用户要自己在脑中拼出“哪个题单、哪个起点、哪个改动、哪次运行、什么结论”。

## 2. 已确认的方向（用户，2026-10-15）

1. **迭代**由起点和评测数据范围确定。目标不显式设置：建迭代时确定候选 0（C0），在 C0 上跑一次得到基线，之后自然以超过起点为目标。
2. 迭代内部是**像 git 分支一样的候选树**：从某节点出发，分析 Agent 提建议，批准 N 个就新开 N 个 worktree 并运行，成为挂在该节点下的 N 个子节点；子节点再派生时，其比较与 diff 对象是它的父节点。
3. **中间区不分页**；更改页取消，其内容并入节点详情。
4. 结构的展示与导航借鉴 Paseo 和同类产品（调研见上）。

## 3. 概念模型

| 对象 | 负责的事实 | 不承担的职责 |
| --- | --- | --- |
| 项目 | 被测 SN 仓库及可用执行配置 | 不以某个活跃 worktree 代表全部实验 |
| **迭代** | 起点 commit、冻结题单（plan revision + selection_hash）、比较协议（mode、request revision、scorer、bundle/index）、根候选 C0 | 不默认后来变化的模型服务／scorer 仍与起点相同 |
| **提议** | 假设、证据、逐题预测、父候选、内容修订与审批记录 | 未批准时没有 commit、run 或分数；界面上是虚线节点 |
| **候选** | 主父候选、起因提议、冻结源码（commit + 捕获引用）、并入来源、结论 | 不等同于 Git 分支名、Agent 会话、聊天摘录或目录 |
| 执行资源 | worktree、Agent 会话、终端及其占用状态 | 不决定候选身份；归档资源不抹掉候选历史 |
| attempt／run | 一次实际执行的输入、输出、回执、终态 | 不覆盖另一次执行 |
| 评分批次／比较记录 | scorer、分母、配对依据、两端引用 | 不随“最新 run”自动改写历史结论 |

**命名冲突。** 代码里的 `Candidate`（`apps/sn-workbench/src/agents/useCandidates.ts`）已指“从对话摘出的分析发现”。建议代码候选沿用用户的“候选”一词，原对象在界面与类型上改称“发现”（Finding），迁移时保留其来源定位和判断历史。

**一张假设卡可产生多个候选**（例如两种实现）；候选不以 `(task_id, hypothesis_id)` 唯一键承载代码身份。

## 4. 树的规则

### 4.1 比较对象

每个候选固定显示两组差值，另允许临时钉选一个参考：

| 比较 | 回答的问题 | 用途 |
| --- | --- | --- |
| Δ父 | 这一步改动带来了什么 | 主视图；翻转表、diff、假设逐题预测核对都相对父 |
| Δ根 | 从起点累计改善了多少 | 第二列；根上答对而此处答错的题单独标红作为守护 |
| 临时参考 | 用户想看的任意节点 | 单独标识，不写入候选的长期结论 |

父即根时合并为一列并说明。树上所有节点共用一个冻结题单，因此任意两节点**原则上可比**，但仍必须通过现有配对准入（bundle、scope、case IDs、scorer、实际分母、SN mode 与 manifest 身份）；缺证据就显示“不可用”，不显示绿箭头。均值变好不等于全量不退化或显著。

### 4.2 冻结与修订

- 实施期间可在 worktree 内多次编辑。
- **提交评测时由协调服务冻结**：在候选 worktree 的分支上生成 commit，同时生成捕获源码引用，锁定协议后派发。实现 Agent 按现有 brief 不提交 Git、不启动评测，冻结责任不交给 Agent。
- 冻结后再改代码 → 派生子候选。同一源码重复执行 → 新 attempt；补评 → 新评分批次。失败、取消、未知回执保留；禁止按低分重跑挑优。
- 标题、说明、人工判断可追加有来源的修订，不制造新节点。

### 4.3 合并兄弟改动

首版只画**单主父树**；数据保存结构化 `integrated_from`（来源候选、commit／patch、方式：真实 merge／cherry-pick／手工重写、冲突说明），详情里可展开来源边。例：C0 派生 A、B，把 B 并入 A 得 M：`primary_parent=A`，`integrated_from=[B]`，默认显示 M−A、M−C0。**M 必须自己重跑**，不能用 A、B 分差相加估计。只借用思路记 `inspired_by`。

## 5. 生命周期

```text
提议:   drafted → approved ─────────────→ (派生候选)
                 └→ rejected
候选:   provisioning → implementing → frozen → running → evaluated → adopted | rejected | undecided
          │ unknown（创建回执未知：只对账，不重建）
          └ failed（保留工件，可新 attempt）
run:    沿用现有 Job/execution 状态与终态规则
```

1. **建迭代**：选起点 commit 和冻结题单，C0 冻结即起点；自动建立基线执行意图（已有同条件 run 可经准入后复用）。基线失败时 C0 仍存在，显示“待补证据”，不显示零分、不换起点；恢复用新 attempt。
2. **提议**：分析 Agent 起草的假设挂在**明确的、已评测的父候选**下（与现有假设卡须引用 run 证据一致），界面为虚线节点。
3. **批准**：保存批准的具体修订和父候选的冻结 commit；按该 commit 创建 worktree（不是项目 HEAD）和实现 Agent。
4. **实施 → 冻结 → 运行**：见 4.2。运行中切换选中节点不影响已派发任务。
5. **完成与决策**：生成明确引用父、根两端的比较记录；采纳／放弃／未决是研究判断，不改客观分数。采纳的候选可作为后续派生的父；本版**只在工作台标记**，不自动推送 SN 分支或开 PR（用户 2026-10-15）。

## 6. 身份契约（先于界面实现）

| 字段 | 产生者 | 传播到 |
| --- | --- | --- |
| `iteration_id`、协议引用、冻结题单引用 | 建迭代 | 候选、job preparation、run manifest、比较记录 |
| `candidate_id`、`parent_candidate_id`、`parent_commit` | 批准提议 | worktree 创建 intent 与回执、候选记录 |
| `frozen_commit`、捕获源码 `source_ref` | 提交评测（协调服务） | job preparation、run manifest、ledger、比较记录 |
| `attempt` | 每次派发 | run 目录、执行回执 |
| `integrated_from` | 合并派生 | 候选记录 |

约束：

- 实现说明（2026-10-17）：`candidate_id`／`iteration_id`／`attempt` 没有改写 Job／preparation／execution 的 v1 封闭格式，而是由 `candidate_attempts` 把候选与 attempt 绑定到 job_id 与 preparation_id（preparation 的 source head 必须等于 `frozen_commit`），再随执行关联传给 runner，由 runner 在实际加载源码后核对并写入 manifest `identity.candidate` 与 ledger。
- 身份在**生产边界**写入（worktree 创建、job preparation、runner 启动），并由 runner 校验（当前 `notebook_runner.py` 对 `task_worktree` 字段集做校验，需扩展契约而不是只在前端加字段）。
- `frozen_commit` 用于派生，`source_ref` 用于核对实际加载；二者都不替代 runner 的实际加载回执。
- **浏览选择只改视图。** 现有 `activate()`（设为当前改动）会切换 Task 执行绑定；新界面中执行请求显式携带 `candidate_id`。
- 旧 Task／run 不强制补成谱系：不能用 Git ancestry、时间、同名题单或同一 Task 推断父子；无法确认者放入“历史未分配”，人工关联须记录依据。

## 7. 界面

```text
┌ SN ▸ 迭代 I3：QASPER 3题 · 起点 0680aa1 · 题单 5cb19a(冻结) · chunk · scorer S ── [Ctrl+K] ┐
├──────────────┬──────────────────────────────────────────┬───────────────────────────┤
│ 项目          │ 候选谱系        [全部][当前路径][搜索]       │ A：证据按阅读顺序排列        │
│ ▾ SN          │        状态        answer_f1  Δ父    Δ根   │ 已评测 · 未决               │
│   ▾ 迭代      │ ● C0  基线          0.204      —      —     │ 父 C0 · source aa7a40…      │
│     I3 ●未决  │ ├─● A  已评测·未决  0.437   +0.233  +0.233  │ [派生提议][采纳][放弃]       │
│     I2  归档  │ │  └┄○ A1 提议·待批                         │ ▾ 假设与逐题预测（2/3 兑现）  │
│              │ └┄○ B  提议·待批                            │ ▾ 对父的翻转 ↑2 ↓1           │
│ 待处理        │                                            │ ▾ 代码差异（2 文件）[父/根]   │
│  2 项提议     │ [折叠：已放弃 0 · 运行中 0]                  │ ▾ 运行与评分记录             │
├──────────────┴──────────────────────────────────────────┴───────────────────────────┤
│ 抽屉：分析 Agent（可见：题面·gold·成绩） │ 实现 Agent·A（可见：假设卡·代码） │ 终端·A worktree │ 日志 │
└──────────────────────────────────────────────────────────────────────────────────────┘
（数值取自 2026-10-14 固定三题实验，仅作示意；该实验 n=3，不是成绩。）
```

- **左栏**：项目 → 迭代 → 待处理。迭代行带状态点（运行中、待你决定、无待处理）。worktree 不在侧栏出现，它藏在候选背后。
- **中间：候选谱系**。竖排列表加左侧分支轨道（借鉴 `git log --graph`、GitKraken、Fork 的折叠），不画二维连线图。默认按**树结构**排列：父在子前，兄弟按派生时间；不按分数重排树，以免破坏谱系阅读、助长挑优。另提供平铺的“按 Δ根 排行”切换视图。显示主指标、Δ父、Δ根、翻转计数；已放弃分支可折叠。提议是虚线节点，只展示假设与审批，不参与成绩排序。
- **右栏：节点详情**。连续可折叠小节（假设与预测 → 翻转 → diff → 运行与评分 → 审批与结论），锚点定位，不分页。窄屏顺序展开。
- **比较所选**：在中间区展开逐题矩阵（行＝冻结题，列＝所选候选与根），状态差异（error、pending、clarification、missing）按原样显示，不补零；点题进入单题对照（两端输出与证据、官方评分依据、单独记录的人工判断）。
- **底部抽屉**：Agent 对话与终端。分析 Agent 按节点绑定（用户 2026-10-20）：在已评测节点 X 上分析即续接或新建绑定 X 的会话，上下文是 X 的运行、Δ父／Δ根、起因提议与预测核对、从根到 X 的路径记录，提议默认挂在 X 下；跨轮延续靠这些结构化记录而不是一条持续增长的对话。实现 Agent 仍一个候选一个。每个 Agent 头部标出**可见范围**；给实现 Agent 附材料时，界面直接禁用题面／gold／成绩类材料并说明原因（服务端拒绝仍保留；现有隔离不是文件系统沙箱，界面不得暗示更强保证）。终端明确所属候选与 cwd。
- **Ctrl+K 命令面板**：主要导航与结构化动作入口——跳到迭代／候选、比较、派生提议、批准、运行、采纳、打开终端。终端用于开放式操作与诊断，不驱动界面跳转。

## 8. 动词一致

命令面板、`scripts/sn-eval` 与 Agent 工具使用同一组领域动词和回执：

`iteration show|create` · `node show|list` · `proposal draft|approve|reject` · `node derive|freeze|run` · `compare <a> <b>` · `node adopt|reject`

只读动词对分析 Agent 开放；写动词（批准、冻结、运行、采纳）需人确认，接入命令面板不自动给 Agent 执行权限。现有 `sn-eval` 的 task/runs/case/flips/hypotheses 读动词在候选上下文中复用。

**写动词的确认方式（用户，2026-10-19）**：Agent 发起、人在工作台确认。`sn-eval` 的派生、冻结、运行、采纳／放弃、新建迭代只登记一条待确认请求（发起角色、理由、确切参数），不执行；人确认后由运行中的服务器以请求 id 为幂等键、按登记参数执行（运行必须由服务器监督，CLI 进程不能自己派发），回执写回请求；人也可附说明驳回。批准提议不经请求——草稿本身就在等人批准。实现 Agent 只能为自己的候选申请冻结与运行。第四阶段界面之前，人经 HTTP 或 `scripts/sn-workbench` 确认。本机没有沙箱：请求账本让“谁申请、谁确认”可查，不构成强制隔离。

## 9. 用现有工件做的验证小图

用 2026-10-14 固定三题实验（[报告](../../experiments/2026-10-14-r1-ordering-loop.md)，工件 `var/sn-eval/r1-loop-20261014/`）手工映射到新模型，检查哪些关系已有证据：

```text
迭代：plan b0e4aca3 rev 09550c08 · selection_hash 5cb19a8a… · QASPER 3 题 · chunk
● C0  SN commit 0680aa12 · source 81a682e4…
│     attempt 1：execution 原基线，importing 阶段失败，3/3 missing（保留）
│     attempt 2：execution 271ecd31（恢复），3/3 success
└─● A  提议 d616c085（已批准）· worktree sn-hyp-d616… · source aa7a4097…
       run 50f00516-1，3/3 success · 结论 regression_or_tradeoff / cause undecided
```

| 关系 | 证据 | 判断 |
| --- | --- | --- |
| 两次运行属于同一迭代 | 两份 preparation 的 plan id、revision、content_hash、selection_hash、bundle/index 相同 | **有证据** |
| C0 的源码 | baseline-recovery-preparation：source 81a682e4…，head 0680aa12 | **有证据** |
| A 的父是 C0 | worktree-receipt 的 base_revision 0680aa12 = C0 的 head | **只是巧合成立**：worktree 从项目 HEAD 创建，恰好等于 C0；若起点不是 HEAD，这条边就是错的 |
| A 的冻结源码 | candidate-preparation：source aa7a4097…；但 head_revision 仍是 0680aa12 | **只有捕获引用，没有 commit**：改动未提交，无法从 A 派生子候选 |
| run 属于 A | hypothesis-conclusion 的 verification.candidate_run；run 本身只记录 task_id | **靠假设卡事后关联**，run 身份里没有候选 |
| 模型／scorer／配置身份 | preparation 中 runtime/model/scorer/config 均为 `not_captured`；报告另述冻结文件哈希相同 | **部分**：比较可行，但不在 preparation 契约里 |
| C0 的失败 attempt 与恢复 | baseline-failure-diagnosis 与两次 execution 分开保存 | **有证据**，符合“失败保留、新 attempt” |

**结论**：现有数据能支撑“一个迭代、一个父、一个子”的画面，但父子边和 run 归属都靠巧合或事后关联。这验证了调研的判断——**先修身份契约（第 6 节），再做界面**：批准时按父 commit 建 worktree，冻结时生成 commit，并把 `candidate_id` 写进 preparation 和 run manifest。

## 10. 迁移与实现顺序

1. **身份**：迭代与候选表；提议批准按父 commit 建 worktree（改 `hypothesis_worktrees.create()` 读取 HEAD 的做法）；协调服务冻结生成 commit；`candidate_id`／`iteration_id`／`attempt` 写入 job preparation、run manifest、ledger，runner 校验扩展契约。旧 Task **映射为迭代**（用户 2026-10-15）：起点、题单、父子边和 run 归属只填有工件证据的字段，其余标“未知”；人工补的关联记录依据，不凭 Git ancestry、时间或路径补猜。旧 run 保持可读。
2. **比较**：复用 `run_views.flips()` 与现有配对准入，新增“父／根双基准”比较记录；`hypotheses.record_check()` 的单一 verification 改为引用比较记录。
   第二阶段细则（用户 2026-10-19 批准）：
   - **已评测**：候选有一次 attempt 的运行通过单次准入（生成完整、无 error 题、评分齐全）；**参考 attempt** 取第一次通过的，与分数无关、事先确定。有 error 题的运行可看，不画 Δ、不作提议依据（回答第 12 节原待定项）。
   - **比较记录**：每个已评测候选生成“相对父”“相对根”两条，记两端候选／attempt／execution／run、准入结论与原因、主指标、逐题行、好／差／平计数、内容哈希；同一两端只生成一次，不随最新 run 改写。多分区按分区配对，分区集合不一致记“不可用”。
   - **守护**：只记事实——相对根逐题下降的题，界面标红；不做“质量是否退化”的自动判断。
   - **临时钉选**：即算即显，不写入候选结论。
   - **假设核对**：树上候选的 `record_check` 引用“相对父”比较记录；旧 Task 假设流程暂不变，界面阶段收口。
   - **接口**：HTTP 只读三个（候选的比较列表、单条比较、临时比较）；CLI 动词归第 3 步。
   - **验收**：离线测试（准入边界、参考 attempt 确定性、幂等、父即根）；真实工件两份——10-14 遗留迁移的 C0→A 复现“2 好 1 差”，本轮链路 smoke 的 C0→A。
3. **CLI 动词**：`sn-eval` 增加第 8 节动词，先供 Agent 与测试使用。
4. **界面**：侧栏、谱系列表、节点详情、比较矩阵、抽屉、Ctrl+K；取消五页签与更改页（旧链接保留历史入口）。`Candidate` 改名“发现”。
5. 每步以真实 smoke 验收（Agent 用 pi，按现有约定）；先用第 9 节的三题工件构造一个 C0→A 的迁移样例，再在新数据上从非 HEAD 父节点派生一个受控候选，核对创建 intent、起点快照和运行源码一致。

现有代码冲突点的完整清单见调研报告第 9 节。

## 11. 已定的取舍（用户，2026-10-15）

| 问题 | 决定 |
| --- | --- |
| 采纳之后 | 只在工作台标记，不自动推送 SN 分支／开 PR |
| 留出集 | 本版不做；过拟合风险保留在第 4 节说明中 |
| 旧 Task | 映射为迭代，只填有证据的字段，其余标未知（见第 10 节） |
| 默认排序 | 树结构顺序（父在前、兄弟按派生时间），另设“按 Δ根 排行”切换（协调者推荐，用户委托） |
| 提议的父节点 | 维持现状：只对已评测、可引用 run 证据的节点提议 |
| 迭代内换 mode | 另开迭代；mode／reasoning protocol 属于比较身份，可沿用同一起点与题单（用户，2026-10-17） |
| 迭代之间的关联 | 新迭代记录 `seeded_from = (迭代, 候选)`，以该候选冻结 commit 为起点；只用于导航，分数不跨迭代继承（用户，2026-10-17） |

## 12. 仍待定

- （已定，2026-10-19）部分题出错的运行不算“已评测”、不作提议依据，见第 10 节第 2 步细则。
