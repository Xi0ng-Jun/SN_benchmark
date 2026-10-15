# SN 评测工作台：迭代候选树的界面先例调研

日期：2026-10-15。性质：调研与设计建议，不是已批准的实现规格。

## 0. 范围、证据与主要结论

本报告针对“Workspace → Task → 工作／更改／结果／计划／记录”改为“项目 → 迭代 → 候选树”的提议。只调研、阅读代码和撰写本报告；没有安装或登录所列产品验证交互，没有启动模型实验，也没有修改实现。

证据分为三类：

- **产品事实**：实际阅读的一手官方文档、作者仓库、产品页及其截图说明。以下外链的访问日期均为 2026-10-15；历史资料另标年份。阅读文档不能证明当前发行版每个细节相同。
- **推断**：由先例或现有代码推出的判断，不归为产品能力。
- **SN 建议**：针对本项目的设计取舍，仍需通过真实工件和交互验证。

“未核实”表示本次资料不足以确认，不表示产品没有该功能。W&B 官方文档的可读索引正文与当前跳转存在差异，下文单独说明。AlphaEvolve 本次读的是作者文章，未读其完整白皮书；不把研究架构图当成交互产品界面。

**结论：新模型的方向成立，但应先区分候选身份、运行身份和工作目录。** Git 客户端提供谱系导航先例，评测产品提供比较先例，Agent harness 提供执行容器和审批先例；本次没有核实到一个现成产品完整覆盖 SN 的全部要求。

| 问题 | 结论 | 主要先例 | 对 SN 的含义 |
| --- | --- | --- | --- |
| 1. 树与分支 | 图负责显示关系，列表和详情负责阅读；比较对象必须明示 | GitKraken 三栏、Fork 折叠、Git Graph 父子跳转、Graphite stack | 借用 Git 图的读法，不把每次 Git commit 都变成评测节点 |
| 2. 实验比较 | 持久基线、临时参考和逐例配对是不同职责 | Braintrust 基线、LangSmith source、promptfoo 矩阵 | 父／根固定双基准，临时钉选另列；先核可比性，再显示改善与退化 |
| 3. 多 Agent/worktree | 工作目录隔离与并行会话组织已经成熟，客观择优闭环不能据此假定 | Paseo、Conductor、Sculptor；Crystal、Codex 历史先例 | worktree 是执行资源；候选、Agent、run 不互相充当身份 |
| 4. 搜索与优化 | 候选谱系已有直接先例；验证集用于择优不等于最终留出集 | OpenEvolve visualizer、DSPy、promptfoo optimize | 保留父子与变异来源，明确开发／验证／最终测试用途和暴露情况 |
| 5. 命令与终端 | 命令面板适合导航和结构化动作，终端适合开放式操作与诊断 | Sculptor Cmd+K、GitKraken Ctrl/Cmd+P、Graphite CLI | 共用领域动词和回执；不能用一个自由命令框替代审批证据 |
| 6. 三个待定问题 | 双基准；评测前冻结；主树加结构化并入来源 | 详见第 6 节 | 保留审计事实，首版不必展示完整 DAG |

项目依据是 [CURRENT_STATE.md](../../CURRENT_STATE.md)、[评测背景](../evaluation-context.md)、[评测状态](../evaluation-status.md) 和当前代码。产品目录中的 [README](../product/sn-workbench/README.md)、[交互草案](../product/sn-workbench/interaction.md)、[API 与事件草案](../product/sn-workbench/api-and-events.md) 提供历史意图；其中部分“尚未接真实执行”的描述早于当前状态，不能作为当前能力结论。代码阅读基点为 `dfb7c51c2ea890f105189f8e30255a2e289a492e`。

## 1. 树形、分支与堆叠 PR

### 产品事实对照

| 产品／一手资料 | 父子关系与布局 | diff 相对谁 | 节点多时的处理与核实边界 |
| --- | --- | --- | --- |
| GitKraken Desktop：[界面](https://help.gitkraken.com/gitkraken-desktop/interface/)、[Diff](https://help.gitkraken.com/gitkraken-desktop/diff/)、[Hide/Solo](https://help.gitkraken.com/gitkraken-desktop/hiding-and-soloing/) | 左侧 refs/worktrees，中央 Commit Graph，右侧选中 commit 的面板；WIP 在顶部 | 文档说明多 commit 的合并 diff、工作目录与 commit 比较；单点普通／merge commit 的默认父节点选择未核实 | Hide/Solo 按 refs 隐藏或聚焦，是显示过滤，不改变 Git；侧栏分组可收起。不能把隐藏 ref 等同于删除或归档候选 |
| Sourcetree：[历史与代码差异](https://confluence.atlassian.com/get-started-with-sourcetree/repository-history-and-code-diffs-847359147.html) | 官方历史视图展示提交历史，可按分支过滤 | 多选 commits 查看它们之间的差异 | 所读为 2018 年官方说明；当前折叠细节和单 commit 默认比较对象未核实 |
| Fork：[Collapsible Graph](https://git-fork.com/blog/posts/collapsible-graph/) | 作者用 merge tip 作为折叠入口 | 该文没有确认默认 diff 基准 | 2020 年文章明确支持点 merge tip 或左右方向键折叠／展开，以及先全部折叠再展开关注分支；以大型 Swift 仓库演示。不是任意树节点折叠规则的证明 |
| VS Code **Git Graph 扩展**：[作者 README](https://github.com/mhutchie/vscode-git-graph) | commit graph、点击详情；不是 VS Code 内置 Source Control Graph | Ctrl/Cmd 再点第二个 commit，可比较任意两个提交 | 分支多选／glob、Find、first-parent、加载更多；上下选行，Ctrl/Cmd+上下跳父子，Shift 切分岔；非 HEAD 祖先可淡化。文档的 compact folders 不是提交折叠 |
| VS Code 内置：[Source Control Graph](https://code.visualstudio.com/docs/sourcecontrol/history) | 历史浏览与 reference 比较 | 文档明确区分与 reference、与 merge base 比较，比较侧有方向 | 有 page size；可浏览历史而不 checkout。不能与上述扩展的快捷键混写 |
| Graphite：[审查 PR](https://graphite.com/docs/review-pull-requests)、[CLI](https://graphite.com/docs/cli-quick-start)、[更新 PR](https://graphite.com/docs/update-pull-requests) | Show stack／S 打开 stack，点 PR 导航；右侧 Stack/Timeline/Files 提供上下文 | PR 以其父分支为 base；split diff 显示 base 与 change | `gt log short` 浏览 stack；下层修改后 restack 上层。它组织的是依赖变更序列，不能直接等同于互相竞争的兄弟实验 |
| Graphite：[PR versions](https://graphite.com/docs/pull-request-versions) | 同一 PR 可以有多个版本 | 初次 v1，每次 `gt submit` 递增；Compare 选左右版本；可只看自上次审阅以来的变化 | 明确存在“节点身份稳定、内容版本变化”的产品先例，但这不是已评分实验可以静默覆盖的依据 |
| ghstack：[作者 README](https://github.com/ezyang/ghstack) | 每个本地 commit 映射一个 PR；以 base/head/orig 分支维护 stack | 每个 PR 的 head 对应自己的 base，突出这一层变更；支持 amend/rebase 后更新、`ghstack land` | 本次未核实独立 GUI、可折叠侧栏或候选排行榜；不要把 CLI/GitHub PR 机制描述成专门的实验树界面 |

### 推断与 SN 建议

**推断：**“父子结构”和“当前比较对象”不是同一件事。Git 图表达提交关系，但产品允许任意两点比较；stack 表达依赖关系，却允许节点被修改和 restack。两者都不能自动给出 SN 的“相对实验根累计效果”。

**建议：**中央显示的是**候选谱系**，一个候选可以包含实施过程中的多次 Git commit，最终评测锁定一个源码快照。不要逐 commit 生成候选，否则修 lint、修构建也会挤占实验结构；同时保留查看内部提交的入口。

列表行固定显示 `候选 ID／父 ID／标题／状态／父差值／根差值`，分支轨道仅辅助辨认。选中行打开详情，无需 checkout。代码 diff 标头直接写“父 C3 → 当前 C7”或“根 C0 → 当前 C7”，不要只写“更改”。Git 的 `A...B` 比较的是 merge base 到 B，不能替代固定根 C0；需按实际目标选两端快照，参见 [git-diff 官方定义](https://git-scm.com/docs/git-diff)。

节点很多时，采用以下组合，而不是只提供缩放图：

1. 稳定的拓扑顺序和分支位置；支持搜索候选 ID、假设、commit、状态。按成绩排名应是独立比较投影，不能悄悄重排谱系。
2. “仅当前祖先路径”“聚焦此分支”“隐藏已归档分支”；折叠摘要保留后代数、待审批数和已知退化数，不能把问题藏掉。
3. 键盘父／子／兄弟跳转，选中节点保持可见；过滤后保留必要祖先或明确的路径占位。
4. 长列表可以虚拟滚动和渐进加载，但不恢复五页签或让用户跨页寻找父节点。Fork 的 merge 折叠只能借鉴交互，SN 的候选树未必有 merge tip。

## 2. 固定题单上的实验比较

### 产品事实对照

| 产品／一手资料 | 基线选择 | 逐例 diff 与退化 | 矩阵／多实验与限制 |
| --- | --- | --- | --- |
| Braintrust：[Compare experiments](https://www.braintrust.dev/docs/evaluate/compare-experiments) | 可保存 experiment baseline，并有 project default；未指定时可取同 Git branch 最近实验，该自动行为可关闭 | 默认按 input 配对，可自定义 comparison key；红退化、绿改善，支持退化筛选；可切 Base→Comparison、反向或 Expected→Output；有逐例子行和文本／并排比较 | Summary table 为指标行、实验列，Grid 为逐例卡片；trial 可展开。并非只能与上一次比较，也不是天然绑定实验父节点 |
| LangSmith：[Compare experiment results](https://docs.langchain.com/langsmith/compare-experiment-results) | 从同 dataset 选多个实验；第一个选择默认作为 source，可 Set as source 更换 | 可设指标方向；列头显示改善／退化数量并筛选；Compact/Full/Diff，Diff 限两个实验 | 多实验总表；详情或 trace 并排，超过两个实验时详情一次显示两个、可切换。适合“总表定位，再看双列证据” |
| W&B：[Run Comparer](https://docs.wandb.ai/models/app/features/panels/run-comparer) | 所读官方索引正文以当前可见 runs 为比较集合；没有核实固定根／持久钉选基线 | Diff only 隐藏相同配置／指标；不能据此声称有逐题质量退化规则 | 正文说明比较前 10 个可见 runs，run 为列、配置和指标为行，排序／筛选影响集合；明确不自动区分 `job_type`。直接打开当前链接跳转至 docs.coreweave.com 未成功，当前跳转版本未复核 |
| W&B Weave：[DSPy 优化教程](https://docs.wandb.ai/weave/cookbooks/dspy_prompt_optimization) | 教程对 baseline 和 optimized 使用同一 validation dataset | 查看输出、分数和 trace 的对照 | 这是工作流先例，不证明存在特定的 baseline 钉选控件；该控件及具体红绿规则未核实 |
| MLflow：[Prompt evaluation](https://www.mlflow.org/docs/latest/genai/eval-monitor/running-evaluation/prompts/)、[Quickstart](https://mlflow.org/docs/latest/genai/eval-monitor/quickstart/)、[产品页](https://mlflow.org/genai/evaluations/) | 评测详情顶部下拉选择另一个 run 并排 | 示例呈现简洁性改善但覆盖下降，逐行变化与 trace/rationale 可追查 | 支持逐问题比较和 prompt 版本关联；固定全局基线、默认上一个 run、多层候选树未核实 |
| promptfoo：[Web UI](https://www.promptfoo.dev/docs/usage/web-ui/)、[配置指南](https://www.promptfoo.dev/docs/configuration/guide/)、[输出](https://www.promptfoo.dev/docs/configuration/outputs/) | 配置组合形成测试列；Eval Actions 可 Compare 不同评测。持久基线默认规则未核实 | 支持 failures/passes/errors/different 等筛选；散点图比较两列成绩；Compare 的绿新增／红删除是**文本差异**，不能等同质量改善／退化 | prompts × providers × tests 形成矩阵；HTML/浏览器并排；详情含输出、prompt、vars、grading。UI 也允许人工改 Pass/Fail/Score，SN 不宜照搬为覆盖官方分 |

补充事实：Braintrust 文档允许事后评分追加到实验记录，见 [Interpret results](https://www.braintrust.dev/docs/evaluate/interpret-results)。因此不能概括成“实验追踪工具都强制结果完全不可变”。

### 推断与 SN 建议

**推断：**现成产品多提供“选择一个 source/baseline”，不自动承担“单步归因 + 累计守护”两个研究问题。自动选最近实验在分支并行时尤其容易选错：最近完成的可能是兄弟节点，且完成时间并不代表来源关系。

**建议：固定父／根，允许另加临时参考。**

- 相对父节点：回答“这一步在现有条件下改变了什么”。相对根节点：回答“迄今累计变成什么”。用户钉选 C2 时显示第三种明确标注的“参考 C2”，不替换父／根含义。
- 基准不仅锁 candidate ID，还锁所用 run/attempt、scoring batch 与比较协议。补评不能让旧比较悄悄改数；新比较保留来源与时间。
- 同一候选可以有多个 attempt；节点总览必须声明当前展示哪次或怎样汇总。不要默认挑最高分，也不要用最后完成者覆盖先前证据。
- 比较前核对 bundle、scope、case IDs、scorer、指标方向与实际分母；不满足条件显示“不可配对／部分可配对”和原因。复用后端 admission 与逐例 flips，不在前端直接相减伪造配对。
- 同时展示总体指标和 `改善 / 退化 / 相同 / 未配对` 数量。颜色加文字、箭头和状态，避免仅靠红绿；文本删增、质量变化、运行失败使用不同视觉语义。
- 根分数缺失、父 run 失败、指标不适用都显示真实状态。`pending/error/missing/no_answer/clarification` 不合并成 0；“没有观察到退化”不等于已证明不退化。

矩阵应有两个尺度：**指标 × 候选**查看权衡，**案例 × 候选**定位分歧。默认只比较用户选中的少量候选，固定 C0 和父参考在视野内；宽表可横向滚动／选择列，逐例证据一次展开两个候选。先按“相对根退化”筛选，再读输出、检索证据、trace 和人工判断。各套件、各指标分别呈现，不将 Braintrust 等产品的汇总做法照搬为 SN 跨套总分。

## 3. 多 Agent 并行 worktree 的 harness

### 产品事实对照

| 产品／一手资料 | 多候选与侧栏／容器 | 比较、采用与审批 | 可借鉴范围／未核实项 |
| --- | --- | --- | --- |
| Paseo：[Workspaces](https://paseo.sh/docs/workspaces.md)、[Parallel development](https://paseo.sh/docs/parallel-development)、[CLI](https://paseo.sh/docs/cli) | project 下有 workspaces；workspace 承载 Agent、terminal、browser、diff 等 sessions；可从同一 ref 建多个 workspace 做竞争实现。多个 workspace 也可共享同一 worktree | 文档建议 review/test 后通过 Git／Agent commit、PR 采用；archive 清理；CLI 有 pending permission 的 ls/allow/deny | 稳定 workspace 容器与临时执行目录值得借鉴；最后一个关联 workspace archive 后才处理共享 worktree。没有核实固定题单自动排行／择优树；Command Center 的具体 Ctrl+K 键位未核实 |
| Conductor：[Parallel agents](https://www.conductor.build/docs/concepts/parallel-agents)、[Worktrees](https://www.conductor.build/docs/concepts/git-worktrees)、[First workspace](https://www.conductor.build/docs/first-workspace) | 独立任务分 workspace，同分支可在同 workspace 放多个 agents；workspace 汇集分支、文件、聊天、终端、diff/PR。首次使用文档截图说明包含 project sidebar 与 workspace chat | [Diff viewer](https://www.conductor.build/docs/reference/diff-viewer) 有 changed files、unified diff、commit 过滤和行评论反馈 Agent；[Plan Mode](https://www.conductor.build/docs/concepts/agent-modes) 先计划，approve/feedback 后实施 | 计划批准有先例，但 provider 能力有差异；共享 workspace 的 agents 共享文件，worktree 隔离也不等于安全沙箱；固定题单择优未核实 |
| Crystal：[作者仓库](https://github.com/stravu/crystal)、[Changelog](https://github.com/stravu/crystal/blob/main/CHANGELOG.md) | 历史版本有同一 prompt 启动 Claude/Codex、多 agent／terminal panel、folders | changelog 记载 merge and archive 等工作流 | 当前 README 标明 2026-02 停用、由 Nimbalyst 替代；只能作为历史先例。当前精确侧栏树、审批弹窗和“每轮自动 commit”未核实，不以旧介绍外推 |
| Sculptor：[作者仓库](https://github.com/imbue-ai/sculptor)、[Workspaces](https://github.com/imbue-ai/sculptor/blob/main/docs/help/workspaces.md) | research preview；workspace 顶部 tabs，topbar 为 repo›branch→target；默认 git worktree，clone/inplace 为实验模式；支持不同 workspace 并行或同 workspace 多 agent | [Changes](https://github.com/imbue-ai/sculptor/blob/main/docs/help/changes.md)：Files 中 Browse/Changes/Commits；Changes 是上次 commit 以来的未提交改动；显式 Commit 才让 Agent 生成信息并提交，不 push；仓库介绍 review 后 merge | 不应把 Changes 直接映射成实验候选变化；也不应沿用旧宣传声称当前默认容器沙箱。客观评测择优／留出集 UI 未核实 |
| Codex：[2025 DevDay 官方案例](https://developers.openai.com/blog/codex-at-devday)、[当前 worktrees 文档入口](https://developers.openai.com/codex/app/worktrees)（转到 [Git worktrees](https://learn.chatgpt.com/docs/environments/git-worktrees)） | 历史 best-of-N 案例展示两个 attempts，借预览截图比较方案；当前文档区分 chat 管理的 worktree 与作为侧栏独立 project 的永久 worktree，后者可多 chats | Handoff 在 local/worktree 间移动工作上下文；测试、建分支、commit/push/PR 有显式工作流 | best-of-N 证明多方案供人选择，不证明自动质量评分、候选谱系或冻结题单。不能把历史案例与当前所有控件视为同一版本能力 |

### 推断与 SN 建议

**推断：**harness 主要解决“在哪工作、谁在工作、怎样审代码”。SN 还必须回答“这次成绩属于哪份源码、哪份题单、哪个父节点”。一个 workspace 一条聊天或一棵 worktree 的 UI，不能替代评测身份。

**建议：**侧栏只保留项目与迭代，并聚合待审批／运行中数量；候选在中央树中，Agent 会话属于当前候选或分析上下文，终端属于执行资源。详情可显示 worktree 路径的脱敏简称、可用状态和打开动作，但路径不成为用户识别候选的主要名称。

把三个决定分清：**批准假设及实施范围、授权执行评测、采用候选／合并代码**。它们可以在用户明确授权的一个流程中串联，不要求每步重复弹窗；也不能把“允许 Agent 写文件”解释成“用户已接受实验结果”。批准前展示具体提议版本、父候选 commit、题单和实施约束；采用前展示父／根对照及尚未解决的退化。沿用项目现有决定，默认不增加金额闸门。

归档 worktree 只释放执行资源，不能删掉候选、run、源码引用或审查依据。若未来重新打开冻结候选，恢复的是它的明确快照；继续修改应派生新候选，不能接着改旧身份。

## 4. 搜索、演化优化与留出集

### 产品／研究事实对照

| 一手来源 | 已核实的候选组织 | 防过拟合／留出集证据 | 没有核实的 UI |
| --- | --- | --- | --- |
| AlphaEvolve：[Google DeepMind 作者文章](https://deepmind.google/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/) | 架构图包含 prompt sampler、LLM 生成程序、evaluators、program database 的循环；展示多次 mutation 的程序演进案例 | 本次阅读不足以确认统一的留出集界面或权限机制 | 可操作的谱系浏览器、折叠导航、审批、holdout 控件均未核实；文章图示不是产品截图证明 |
| OpenEvolve：[作者仓库 README](https://github.com/algorithmicsuperintelligence/openevolve) | 作者明确介绍实时 interactive visualizer：parent-child evolution tree、按 generation 的表现、mutation 代码 diff、MAP-Elites grid、多指标 | README 的这些功能说明不足以证明独立盲测或防泄漏机制 | 本次依据 README 及其截图链接说明，未操作应用／核对截图像素；默认 diff 基准、折叠方式和留出集权限 UI 未核实 |
| DSPy/MIPROv2：[API](https://dspy.ai/3.0.0/api/optimizers/MIPROv2/) | 搜索 instructions 与 few-shot 组合；Bayesian optimization，minibatch validation，并周期性 full validation，按 full validation 结果选择 | [RAG 教程](https://dspy.ai/3.3.0/tutorials/rag/) 分 train/dev/test，dev 用于迭代，test 用于最终 held-out 检查，并讨论过拟合 | 这是 API／教程证据，不是候选树 GUI、锁定 test 按钮或可见性权限先例 |
| promptfoo：[Prompt optimization](https://www.promptfoo.dev/docs/usage/prompt-optimization/) | CLI optimize 从 baseline 开始，生成候选、评估、报告最佳方案 | 文档提示默认全量 eval 优化可能过拟合；`--validation-split 0.2` 使用独立部分参与候选 adoption | 这是 CLI 配置，不证明浏览器有 holdout 锁；被反复用于候选择优的 validation 不能据此称为最终未见测试集 |

### 推断与 SN 建议

**推断：**OpenEvolve 是本次最直接的“候选谱系 + 变异 diff + 表现”先例，但有图不等于比较协议可靠。AlphaEvolve 和 DSPy 说明搜索系统需要候选档案和选择依据，却不能证明某一种 UI 已解决自适应过拟合。

**建议：**迭代的固定题单应明确用途。被分析 Agent 反复阅读、用于提出假设和选择候选的题单是开发证据；其中的“守护题”用于发现开发范围内的退化，不等同于未见留出集。

界面在题单摘要中显示 `用途 / 样本数 / 是否已查看逐题结果 / 是否已用于择优 / 最近运行状态`。独立最终测试未运行时显示“未验证”，不能与开发集绿色成绩合成“已通过”。最终测试一旦反馈到下一轮选择，就应记录其暴露并调整结论，不能只靠“holdout”标签继续宣称盲测。DSPy 或 promptfoo 的示例比例不应直接固化为 SN 的样本配比。

实施 Agent 继续只接收经批准且不含考题／gold 的实施说明；分析 Agent 可查看被允许的开发证据。当前项目没有文件系统强制隔离，界面应准确表达“未下发”与“不可访问”的区别，不能靠隐藏面板声称已经建立安全边界。

## 5. 命令面板、CLI 与终端

### 产品事实对照

| 一手来源 | 面板／CLI 的角色 | 终端或其他交互的角色 | 对 SN 的启发 |
| --- | --- | --- | --- |
| Sculptor：[Command palette](https://github.com/imbue-ai/sculptor/blob/main/docs/help/command_palette.md) | Cmd+K 搜索／切换 workspace、agent，创建对象，打开 panel/file/settings；箭头、Enter、Esc 操作 | 开放式开发仍有 workspace 内工具 | 一个入口连接导航和结构化动作，但文档确证的是 Cmd+K，不据此断言全部平台按键 |
| GitKraken：[Command palette](https://help.gitkraken.com/gitkraken-desktop/command-palette/) | **Cmd/Ctrl+P** 搜索命令、文件、refs、worktrees，动作受仓库状态影响 | 可 Open in terminal | 键位不统一；GitKraken 的 Ctrl/Cmd+K 是左侧栏切换，不是这里的面板入口 |
| Paseo：[Parallel development](https://paseo.sh/docs/parallel-development)、[CLI](https://paseo.sh/docs/cli) | Command Center 打开 Changes/Files/terminal/browser；CLI 管 workspace、terminal、scripts 和权限请求 | terminal 是 workspace 中一种 session，可分屏 | 共用对象模型的多入口有先例；具体 Command Center 快捷键本次未核实 |
| Graphite：[CLI quick start](https://graphite.com/docs/cli-quick-start)、[Web review](https://graphite.com/docs/review-pull-requests) | `gt create/modify/submit/log/pr/sync` 驱动 stack 生命周期，Web 负责 stack 导航与审查 | Git/CLI 与网页分工 | 是“领域动词驱动流程、UI 展示并审查”的明确先例，不证明 UI 内部调用 CLI |
| promptfoo：[Outputs](https://www.promptfoo.dev/docs/configuration/outputs/)、[Web UI](https://www.promptfoo.dev/docs/usage/web-ui/) | CLI `eval` 产生评测，`view` 打开浏览器表格；UI 提供比较和过滤 | CLI 负责可脚本化运行 | 运行与可视分析共享工件的先例，不必让用户在终端重新拼比较命令 |

**推断：**可以确认上述产品采用这些工作流；本次没有用户研究数据证明“命令面板优先”对所有人更高效。对 SN，Ctrl+K 是合理入口，但不应成为唯一可发现入口。

**建议：**候选详情保留“派生提议”“比较”“批准并实施”“运行”“采用”等可见动作，Ctrl+K 调用同一组带对象和状态约束的动作。导航立即执行；改变执行状态的动作先展现必要参数和具体影响，复用已有授权，不增加例行确认。

以下仅为**建议的领域动词，尚未实现**：

| 动词意图 | 命令面板的表现 | 所需明确上下文 |
| --- | --- | --- |
| `iteration open` / `candidate open` | 搜索并跳转 | 项目、迭代、候选 ID |
| `candidate propose --parent C3` | 打开提议草稿 | 父候选、假设版本、预期及证据 |
| `proposal approve` | 展示并批准具体提议 | 父 commit、范围、实施 Agent、授权覆盖哪些后续动作 |
| `candidate evaluate` | 展示冻结输入、执行／查看回执 | candidate、plan revision、源码引用、超时、attempt |
| `candidate compare --to parent/root/C2` | 进入比较投影 | 两端 candidate/run/scoring refs、配对状态 |
| `candidate integrate --from C2 --onto C3` | 创建并入提议 | 主父、来源、合并方式；结果是待重新评测的新候选 |

UI 和 CLI 应调用同一领域服务、输出同一 request key／回执，而非让 UI 拼 shell 字符串来改变状态。终端用于诊断、人工 Git 操作和开放式探索；终端里改变了文件并不自动更新冻结候选，需经显式捕获与身份核验。选择节点、切 Agent、切终端 cwd 也不应暗中改变下一次评测对象。

## 6. 三个待定问题的建议

| 待定问题 | 先例与取舍 | 推荐决定 | 仍需保留的边界 |
| --- | --- | --- | --- |
| (a) 只比较父，还是也比较根？ | stack 的局部 diff 利于审查每一步；Braintrust 可保存 baseline，LangSmith 可选择 source；但这些产品不自动保证 SN 的双重研究问题 | **父和根都显示**，临时钉选另列。直接子节点的父=根时合并显示并说明；根节点无父差值 | Δ父回答单步变化，Δ根回答累计变化；任一比较缺证据则显示不可用。即使两者都改善，也不能单凭均值声称全量不退化或统计显著 |
| (b) 跑完冻结，还是允许修订？ | Graphite PR versions 允许同 PR 继续修订；Braintrust 可追加评分。代码审查与实验记录的生命周期不同 | **提交评测前冻结源码与协议，结束后封存这次执行结果**。修改代码派生子候选；同源码重复试验用新 attempt；补评用新 scoring batch | 实施中允许多次编辑／内部 commit；不能在同 candidate ID 下静默换源码。失败、取消、未知回执不删除，禁止因分低重跑挑优 |
| (c) 兄弟合并用 DAG 还是来源元数据？ | [Git merge](https://git-scm.com/docs/git-merge) 的真实非快进 merge 有多父语义；OpenEvolve 明确有父子树，但其多来源 UI 未核实 | **首版展示单主父树，保存结构化 `integrated_from`，可展开来源边**。真实 Git parents 另原样保存；不只写自由文本备注 | 数据应容纳来源 DAG，主界面不必同时铺开全部边。squash/cherry-pick/手工重写与真实 merge 分开记录；有多父事实时不能为了树形美观丢失它 |

具体例子：C0 派生 A、B；将 B 的改动并入 A，生成 M。M 的 `primary_parent=A`，`integrated_from=[B]`，同时保存 B 的 commit／patch、方式及冲突处理说明。默认显示 M−A、M−C0；需要时临时比较 M−B。M 必须重新跑同题单，不能把 A、B 的分差相加当作 M 的预测成绩。仅借用了 B 的分析思路则记 `inspired_by`，不冒充代码继承。

若未来出现大量真正对等的多来源组合、用户需要按所有来源追溯贡献，或单主父持续造成误解，再将 DAG 提升为主视图。现在先保留足够的结构化事实，比提前展示复杂图或事后补猜来源更稳妥。

## 7. 对新模型的必要修正

### 7.1 对象边界

以下均为 SN 建议，不是当前已有模型。

| 对象 | 唯一负责的事实 | 不应承担的职责 |
| --- | --- | --- |
| 项目 | 代码仓库与可用执行配置的入口 | 不以某个活跃 worktree 代表全部实验 |
| 迭代 | 起点快照、冻结题单、比较协议、根候选 C0、研究目标 | 不把随后变化的模型／scorer 默认为与起点相同 |
| 提议 | 假设、证据、预测、父候选、内容修订与审批 | 未批准时没有伪造的 commit、run 或分数；虚线节点是投影 |
| 代码候选 | 主父、起因提议、冻结源码、并入来源、生命周期 | 不等同于聊天摘录、Git 分支名、Agent 会话或目录 |
| 执行资源 | worktree、Agent session、终端及其占用状态 | 不决定永久候选身份；归档不抹历史 |
| attempt/run | 一次实际执行及其输入、输出、receipt、终态 | 不覆盖另一轮执行；“已创建”不等于“已运行” |
| 评分批次／比较记录 | scorer、分母、配对依据、两端引用与判断 | 不追随“最新 run”自动改写历史结论 |

冻结题单是必要条件，仍需固定或明确记录 bundle、分区／资料范围、mode、scorer、请求协议和可核验配置。题单或评分口径变化应另开迭代或明确的比较分组，不能在原位置静默替换根基线。代码或方法就是待研究变量时，允许变化但要披露；其他条件变化后不能继续把分差仅归因于该代码改动。模型服务等无法彻底冻结的外部条件标出实际记录与未知项，不能以一个 hash 宣称端到端完全可复现。

### 7.2 生命周期与失败语义

1. 创建迭代：固定协议与 C0 源码，建立基线执行意图。基线失败时 C0 仍存在，状态为待补证据；不显示零分，不悄悄换起点。恢复执行保留原失败工件与新 attempt。
2. 分析生成提议：虚线节点挂在**明确的父候选**下。用户批准具体修订后，保存批准内容及父 commit；不能在实际创建时改用“当前 HEAD”。
3. 创建资源并实施：按冻结的父引用创建 worktree。创建请求已发出但回执未知时保持 unknown 并对账；重试不能盲目再建一个分支／Agent。
4. 提交评测：协调／执行边界验证并生成候选 commit，同时保存可核验源码快照，锁定协议和源码，再派发。commit 用于后续派生，捕获引用用于核对实际运行；Agent 说“完成”不是源码冻结证据，commit 本身也不能替代实际加载 receipt。
5. 运行与评分：答案先保存，评分可单独失败；保留取消、超时和迟到结果的既有边界。不同候选并行执行时，选中节点变化不影响已派发任务。
6. 完成与决策：封存该次执行，生成明确引用的父／根比较。采用、拒绝或未决是研究判断，不修改客观分数；后续改代码另生子节点。

“冻结”应针对源码、协议与既有执行证据；标题、注释、人工解释可以追加有来源的修订，不需要因为修正一句说明而制造新代码节点。一次假设也可产生多个实现候选，候选不应被限制为“每张假设卡唯一一份代码”。

## 8. 推荐界面草图

下图是结构草案，节点和数量均为构造示例，不是本项目实验结果。为易读采用从根到后代的顺序，视觉借用 Git 分支轨道；实际默认顺序应通过导航测试决定。

```text
┌ 项目 / SN ─ 迭代 I12：检索排序 ──────────────────── [Ctrl+K 搜索 / 操作] ┐
│ 根源码 abc123 · 题单 D7(冻结) · 开发用途 · scorer S2 · 最终测试：未运行     │
├────────────────┬─────────────────────────────────┬─────────────────────┤
│ 项目           │ 候选谱系                 [比较所选]│ C3：合并 A 与 B      │
│ ▾ SN           │ [全部] [当前路径] [搜索]          │ 已冻结 · commit def │
│   ▾ 迭代       │       状态      Δ父       Δ根     │ 父 C1 / 并入 C2     │
│     I12  ●运行 │ ● C0  基线      —         —       │ [派生提议] [采用]   │
│     I11  已归档│ ├─● C1 完成     查看      查看    │                     │
│                │ │ ├─● C3 完成   查看      查看    │ 比较依据            │
│ 待处理         │ │ └┄○ P4 待批准 —         —       │ 父 C1 → C3          │
│   1 项提议     │ └─● C2 完成     查看      查看    │ 根 C0 → C3          │
│   1 项未知回执 │                                 │ 配对 N / 未配对 M   │
│                │ [折叠分支：8 后代 / 1 项退化]    │ 改善 / 退化 / 相同  │
│                │                                 │                     │
│                │                                 │ ▾ 结果与逐例证据    │
│                │                                 │ ▾ 代码差异 [父/根]  │
│                │                                 │ ▾ 假设与审批        │
│                │                                 │ ▾ 运行 / 补评记录   │
├────────────────┴─────────────────────────────────┴─────────────────────┤
│ 按需抽屉：分析 Agent / 实施 Agent / 终端（明确候选与 cwd）/ 执行日志       │
└───────────────────────────────────────────────────────────────────────┘
```

中央工作面没有“工作／更改／结果／计划／记录”五个页签。候选详情是连续的可折叠小节，可用锚点定位；题单摘要固定在迭代头部。逐例材料或 Agent 对话可展开侧面板，窄屏顺序展开，不必同时塞入四列。

比较所选在同一工作面展开矩阵，保留回到原选中节点和筛选状态的能力：

```text
比较：C1、C2、C3       固定根 C0     临时参考：无
指标：answer_f1 ↑     配对条件：查看     筛选：相对根退化

案例        根 C0       C1        C2        C3       差异说明
case-a      有效分数    ↓         ↑         ↓        输出 / 证据 / 评分
case-b      有效分数    ↑         相同      ↑        输出 / 证据 / 评分
case-c      有效分数    error     pending   有效分数  状态差异，不能补零

选中 case-a： [C0 输出与证据]  ↔  [C3 输出与证据]
              [官方评分依据]      [人工判断：单独记录]
```

每个箭头对应明确指标、方向和参考。另一种“指标 × 候选”摘要展示精度、覆盖、耗时等权衡；没有经确认的综合目标时，不用一个“冠军分”掩盖退化。虚线提议只展示假设与审批，不放进已有成绩的排序。

## 9. 现有代码的迁移冲突与可复用边界

以下是**当前代码事实**及其迁移含义；没有实施这些变化。链接指向仓库文件，行号为本次阅读定位，可随后续改动变化。

| 位置与现状事实 | 与新模型的冲突 | 建议的迁移边界 |
| --- | --- | --- |
| [shell.tsx](../../apps/sn-workbench/src/shell.tsx) 的 `WorkspaceSidebar`（约 91 行）按 Workspace.tasks 展示任务并聚合状态；[contracts.ts](../../apps/sn-workbench/src/contracts.ts) 的 Task 持有 results/sessions/history，Workspace 持有 tasks | 把 Task 改名“候选”会让目标、会话、结果和迭代容器混在一起；改名“迭代”也不能自动得到节点身份 | 先明确哪些旧 Task 是一次迭代、哪些只是历史工作容器；项目／迭代负责导航，候选负责谱系。不要将所有历史 Task 自动强制映射为一棵有根树 |
| [TaskViewNav.tsx](../../apps/sn-workbench/src/workspace/TaskViewNav.tsx)（12 行附近）固定五视图；[App.tsx](../../apps/sn-workbench/src/App.tsx) 约 404、470 行依赖 task/view 渲染及 ChangesPage | 不是删五个按钮即可：路由、材料选择和状态作用域仍依赖 Task | 将选中迭代／候选／比较对象成为显式导航状态；老链接保留可解释的历史入口，不由当前选中 worktree 猜目标 |
| [ChangesPage.tsx](../../apps/sn-workbench/src/runs/ChangesPage.tsx) 聚合 Task worktree、diff、ExecutionPanel、假设和 runs；[task_worktree.py](../../src/rag_eval/workbench/task_worktree.py) 约 48 行 `task_worktrees.task_id PRIMARY KEY` | 一个 Task 当前 binding 与多张假设的独立 worktree 并存，是“当前改动”含混的来源 | 将当前浏览选择与执行身份分离；候选绑定自己的源码／资源，不再由 Task 的唯一当前 binding 决定所有动作 |
| [hypothesis_worktrees.py](../../src/rag_eval/workbench/hypothesis_worktrees.py) `create()` 约 255 行：从可信 `project_root` 读取 **HEAD** 作为 base；表以 `(task_id,hypothesis_id)` 唯一 | **目前不是从父候选冻结 commit 创建。** 展示树形边会暗示一个实际并不存在的来源契约；一假设一 worktree 也难以支持同假设多个实现 | 创建请求保存 parent candidate 与确切 commit；创建／协调验证同一 intent。新候选不复用假设唯一键承载代码身份 |
| 同文件 `activate()` 约 314 行先固定旧 session cwd、保存 historical binding，再替换 Task binding | “设为当前改动”同时影响领域状态；树中点击节点若照搬 activate，可能改变后续执行目标 | 浏览选中只影响视图；执行显式携带 candidate ID。保留旧 cwd 固定和历史绑定证据，不丢旧会话 |
| [run_views.py](../../src/rag_eval/workbench/run_views.py) `_runs_for_binding()` 约 712 行，通过可信 task 注册或 manifest 中 task/path/source_ref 关联，并接纳历史 bindings | 能证明 run 属于 Task，不足以证明属于某个候选或父子边；相同路径还可能随时间改变 | 在生产运行的边界捕获 candidate/iteration/attempt 身份，向 ledger、manifest、执行回执传播。旧 run 只作有证据的映射，无法确认者标未知归属 |
| [notebook_runner.py](../../src/rag_eval/notebook_runner.py) 约 235 行校验 `task_worktree` 字段集为 task_id、worktree_path、paseo_workspace_id、source_ref | 不能只在前端添加 candidate_id：运行身份验证与传播格式也要演进 | 在身份生产与校验边界建立新契约，并保留旧工件可读性；不以当前 HEAD／分支名反推过去运行 |
| [HypothesesPanel.tsx](../../apps/sn-workbench/src/runs/HypothesesPanel.tsx) 保存 evidence/predictions/verification，提供“设为当前改动”和手选 baseline/candidate run；[hypotheses.py](../../src/rag_eval/workbench/hypotheses.py) 的 `record_check()` 约 145 行更新 verification JSON | 当前一对 run 的核验不足以表达父／根两个长期基准；最新 verification 会替换当前值，事件摘要不等于完整比较版本 | 复用证据、预测、效果与原因区分；比较成为可追溯记录，保存父／根两组明确 refs。提议状态、实施状态、结果判断分开 |
| [contracts.ts](../../apps/sn-workbench/src/contracts.ts) 的 PlanDraftRecord（约 64 行）已有 revision/parent_revision/selection_hash；source_intent 是 `task-workspace-at-freeze`，scorer/model refs 为空 | 已有题单修订能力可用，但“题单冻结”尚不等于协议、源码或实际模型已绑定 | 复用不可变修订；将迭代协议与候选源码分开绑定，补齐比较必要的引用，不把 draft 描述成已经执行的事实 |
| [ExecutionPanel.tsx](../../apps/sn-workbench/src/runs/ExecutionPanel.tsx) 的 plans/jobs 查询和 pending localStorage key 以 taskId 为作用域，准备／运行／取消围绕该 Task | 树上多个候选并行时，单一 Task pending 状态可能混淆准备、取消和回执 | 状态按 candidate/job/execution/request key 关联；切换选择不改变正在运行的对象，unknown 回执继续对账 |
| [job_preparation.py](../../src/rag_eval/workbench/job_preparation.py) 的绑定包括 job/task/workspace/plan/revision/hash；捕获源码不等于已实际加载，存在 captured_unbound 语义 | 候选节点显示“已冻结／已运行”不能只依据 Job 创建或源码 hash | 保留源码捕获、实际加载和执行 receipt 的区别；在这些既有边界加候选身份，不能被新图形状态机压成一个“完成” |
| [briefs.py](../../src/rag_eval/workbench/briefs.py) 的 `implementer_brief()`（约 89 行）明确实现 Agent 不提交 Git、不启动评测、不接收考题 | 新模型“实现完成后节点拥有 commit”没有现成的 Agent 提交契约 | 由协调／执行服务承担验证和生成冻结 commit 的责任，或另行明确调整授权；不能默认 Agent 会自行提交并评测 |
| [agents/useCandidates.ts](../../apps/sn-workbench/src/agents/useCandidates.ts) 的 Candidate 是 finding/direction/next_step 等聊天摘录，附 source locator 和人工判断 | **Candidate 名称已被另一领域对象占用**；复用会混淆分析候选与代码候选 | 新代码候选使用独立实体／类型；既有对象在 UI 可称“分析记录／发现候选”，保留来源定位和判断历史 |
| [run_views.py](../../src/rag_eval/workbench/run_views.py) 的 `flips()`（约 600 行）调用既有配对比较并保留 admission/unpaired；[agent_tools.py](../../src/rag_eval/workbench/agent_tools.py) 为 `scripts/sn-eval` 提供 task/runs/cases/case/flips/hypotheses 等 JSON 动词 | 已有读证据与比较边界，不应在新 UI 内再造简化评分或另一套身份推断 | 复用比较服务与结构化工具，新增候选上下文；CLI 与 UI 对同一事实返回一致回执。已有只读工具不应因命令面板接入自动获得执行权限 |

特别注意：旧数据缺失父候选或迭代协议时，不能用 Git ancestry、时间最近、同名题单、同一个 Task 等线索直接补成“已证实谱系”。可以记录人工关联及依据，保留“历史未分配／不可确定”区域。代码迁移顺序宜是身份和来源先行，再接比较，最后改变导航布局；本报告没有执行该迁移。

## 10. 风险与最小验证建议

下列验证均为后续建议，本次没有执行。

| 风险／触发信号 | 影响 | 最小验证与应调整的条件 |
| --- | --- | --- |
| 提议显示父 C2，实际从 project HEAD 建树 | 图在表达错误因果关系 | 用不同于 project HEAD 的父 commit 建一个受控候选，核创建 intent、起点快照及运行源码；任何不一致先修创建边界，再做界面 |
| 选中节点／activate 改变执行目录 | 并行候选跑错源码、取消错任务 | 两兄弟各有 pending job 时反复切换选择，检查 request key、job、cwd、source ref；若选择影响已建 job，必须拆状态所有权 |
| “同题”但 scorer、资料范围、模型或分母不同 | 漂亮的差值掩盖不可比 | 使用 case IDs 相同而 scorer／scope 不同的工件，确认显示明确限制；若前端仍直接出绿箭头，停用该比较路径 |
| 基线失败被当零、缺分被补零 | 改善和退化计数失真 | 覆盖 missing/pending/error/no_answer/clarification/不适用与部分配对，检查后端分母及 UI 文案；不以只成功样本恢复可比 |
| 节点评测期间仍可改源码 | 分数与节点身份脱钩 | 提交后修改原 worktree，核实际加载的捕获引用与节点冻结引用；若不能证明一致，不宣称已完成有效比较 |
| 重试／补评覆盖旧结果，或自动挑最佳 attempt | 选择偏差与不可审计 | 用失败后恢复、成功后计划性重复、补评三种轨迹核身份和显示规则；每次保留旧证据，不允许按成绩触发择优重跑 |
| 兄弟成绩好，合并后未经验证就采用 | 交互效应导致退化 | 用 A、B、M 三个不同快照演练来源与比较；M 必须有自身运行，不允许由 A/B 差值计算“合并分” |
| 把开发守护题称为盲测 | 自适应过拟合被隐藏 | 标记案例是否已被分析／用于选择，演练最终测试反馈进入下一轮；若不能追踪暴露，只能报告用途声明，不能声称严格盲测 |
| 实施 Agent 可读本地 gold，却标“已隔离” | 用户误判防泄漏能力 | 核资料下发与实际文件访问边界；沿用当前无强沙箱的限制说明，界面隐藏不作为隔离证据 |
| unknown 创建／超时后重复派发，迟到结果改写终态 | 重复资源、错归属、历史不稳定 | 演练请求成功但回执丢失、取消与迟到返回；同 intent 只协调，结果按既有终态规则保存，不自动新建第二份 |
| 节点过多或全用颜色，用户看不懂来源 | 树变成另一种难懂的“更改页” | 用 20／200 节点草图测试寻找父、根、兄弟、未批准提议与退化节点；若必须反复展开全图，改默认过滤和摘要，而非只缩小字体 |
| 导入旧 Task/run 时强制补齐谱系 | 历史证据被重写 | 用现有固定三题闭环工件做纸面映射，分别列已证实字段与未知字段；无法确定的来源保持未知，不凭路径／时间补猜 |
| 删除 worktree 后无法阅读候选 | 把临时资源当长期档案 | 核已归档候选仍可读源码引用、diff、run、审批与比较；若依赖活目录，应先补工件保存规则 |
| Ctrl+K 成为唯一入口，或终端绕过身份捕获 | 可发现性差、执行来源不一致 | 完成一次不使用终端的标准提议—审批—评测—比较流程，再检查终端人工修改如何被捕获；所有入口使用同一身份和回执 |

最先值得做的证伪工作是：以现有真实三题闭环工件手工画出 C0、A 及其 run／源码关系，明确哪些关系已有证据；再加入构造的 B、M 检查并入语义。若连这张小图都必须靠“当前 worktree”猜父节点，问题在数据契约，不应先投入大规模界面实现。现有三题结果只证明局部闭环，不能作为新模型的统计效果或正式 benchmark 成绩。

## 11. 尚未核实与结论边界

- 未核实 GitKraken／Sourcetree／Fork 对各类单 commit 的默认父 diff 细节；不得将所有产品概括为统一默认。Git Graph 扩展与 VS Code 内置图已分开列出。
- W&B Run Comparer 采用所读官方索引正文；直接访问的当前重定向版本未复核。Weave 的持久基线控件、红绿规则未核实。
- Crystal 是停用产品的历史先例，Sculptor 是 research preview；本次没有对这些工具作稳定性或生产成熟度评级。
- 未核实 Paseo Command Center 的具体键位、MLflow/promptfoo 的默认持久基线、ghstack 的独立图形导航；不根据相似产品补写细节。
- 未核实任何所列 harness 同时具备 SN 所需的固定题单、父／根双基准、候选冻结和防泄漏审批闭环。Codex best-of-N 的历史演示不等于这些能力已存在。
- 未核实 AlphaEvolve 的交互式候选档案和留出集 UI；OpenEvolve 的谱系能力来自作者 README，不是实际操作测试。DSPy/promptfoo 的数据划分先例不证明 GUI 强制隔离。
- 本报告没有给出自动采用阈值、统一综合分、正式样本量或强制费用审批。它们不由界面先例决定；本轮目标是让来源、比较与决策可读、可核验。
