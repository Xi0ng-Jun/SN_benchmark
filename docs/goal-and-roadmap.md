# Silicon Notebook 评测系统：目标、理想形态、现状与初版规划

2026-10-13。由用户与接手人 Claude Fable 5.1 在一次真实链路跑通之后共同收敛。本文是方向性文档：总体目标怎样精确表述、理想形态是什么、现在到了哪、差距在哪、下一个版本做什么。决策不变量与有效约束仍在 [CURRENT_STATE](../CURRENT_STATE.md)，代码结构在 [ARCHITECTURE_CURRENT](../ARCHITECTURE_CURRENT.md)，实验事实在 [evaluation-status](evaluation-status.md)。本文随每个里程碑收口更新，不按日期追加流水账。

## 1. 总体目标

**一句话：让 SN 的每一次改动都能快速、可信地回答“答得更好了吗，好在哪、差在哪、为什么”。**

展开为可检验的表述：

- **服务对象**：SN 算法与产品开发者（日常迭代）、研究产出（论文实验与外部方法比较）、协作复核者（人工判读）。
- **要回答的问题**：对 SN 的一次改动 Δ，在固定题单 S、固定条件 C（源码、配置、依赖、模型服务代际、有效特性集合）下，答案质量、证据质量、拒答行为与成本如何变化；哪些题翻转；每个翻转归因到哪个组件（解析／分块／embedding／查询改写／检索／重排／证据筛选／生成／引用）。
- **衡量方式**：五套 benchmark（QASPER、MultiHop-RAG、ALCE、QMSum、HotpotQA）的官方指标是终点分；阶段指标（gold 证据召回率、上下文精度、引用精度、拒答率、调用数与延迟）是归因依据；人工判断独立记录，不覆盖分数。
- **分数的职责只有两件**：发现有问题、确认改完没退化。改什么由失败归因的证据链决定，不由均分决定。
- **可信要求**（既有不变量，继续有效）：生成不见 gold；状态真实（success／no_answer／clarification／error／missing／pending 各自保留，不补零不筛题）；证据来自实际输出；失败不抹掉已完成工作；比较条件可核验并披露差异；实验与生产隔离。
- **非目标**：跨套件总分、自动发布门禁、恢复旧 benchmark、把评测链当 HTTP 压测、无证据的显著性或收入结论。

## 2. 理想形态

### 2.1 工具层：agent 可直接使用的评测工具

- 全部能力以 CLI／Python 加文件工件暴露，输出机器可读、错误有稳定类别。这已是现状的优点，继续坚持。
- 不变量在工具内部强制，不靠提示词：runner 不接触 gold、拒绝已有 run 目录、答案先落盘；诊断轨道（如 oracle context）是显式参数且产物自带 `diagnostic` 标签，不可能混入正式分。
- 读侧工具与写侧工具同等重要：列 run、列案例并按状态／分差筛选、取单题完整证据链、算阶段指标、对比两个 run 的翻转表。agent 分析时调用它们，不手写脚本翻 JSON。
- 读工具随时可用；真实运行需要明确的题单、源码、模型服务和超时。2026-10-15 用户取消界面强制金额与预算卡：开发环境自行估算费用，服务器使用公司资源。
- 以 skill 形式交付（类似 Paseo 的 SKILL.md）：有哪些命令、何时用、产物在哪、哪些事不能做；任何 provider 的 agent 接手都拿到同一套能力。

### 2.2 账本层：给 agent 工作用的事实源

- **能力状态表**：每项能力一行，状态、证据链接、下一步。
- **run 账本**：每次运行一行 JSON：输入 bundle／题单、源码 ref、配置 hash、模式、有效特性集合、状态、官方分、阶段指标、路径。
- **失败账本**：每个失败案例的类别、归因组件、证据路径、人工确认状态。
- **假设卡**：假设、涉及组件、预期改善的题类、可能伤害的题类、对应改动、配对验证结果。
- 历史证据与过程记录另存归档；长期文档不再按日期追加“当前”段落。

### 2.3 迭代循环：分数怎样变成改进

1. 基线：固定题单（每套 50～100 题，分层抽样，固定 seed），chunk 与 reasoning 各跑一次，得到官方分、阶段指标、全部案例。
2. 切片与读案例：按题型、失败类别、分差排序；agent 先读初判，人复核，写入失败账本。读最差的 20 题，不读均分。
3. 提假设：写清组件、机制、预期受益题类、可能受害题类。
4. 最小改动：优先配置与 prompt，再代码，最后换模型；一次一个变量；改动与假设互相引用。
5. 配对验证：同题单重跑，报告翻转计数（变好／变差／不变及其案例），不以均分差作结论；group 足够时再做 bootstrap。
6. 留出集：一部分题永不参与读案例与调参；真实用户问题集是最终目标，benchmark 是能力代理。
7. 沉淀：有效改动合入；修好的题进入回归集；每次改动过回归集。

### 2.4 诊断轨道

- **oracle context**：把 gold 证据段落直接喂给生成阶段。oracle 分减真实分＝检索损失；满分减 oracle 分＝生成上限问题。明确标注诊断，不进正式成绩，不违反“生成不见 gold”（它是独立轨道，产物隔离）。
- **阶段指标**：从已保存的 retrieval_context、anchors、evidence snapshot、模型事件直接计算，不需要新观测。

### 2.5 人机分工

- agent：跑工具、读案例、初判类别、生成假设、起草改动、产出配对报告；每个判断引用具体 run 与案例文件。
- 人：批准付费运行、确认失败类别（前几轮亲自做，形成标准后交给 agent）、决定改不改 SN、判断假设价值、最终判读。
- 角色隔离而非信息隔离：分析 agent 可读 gold；SN 运行时永远不见 gold；两者是不同进程与上下文。

### 2.6 界面：Paseo 的三栏语义装评测内容

- 左栏：workspace → 实验／任务，每行带实时状态点与待决数；运行中的 run 显示进度。
- 中心时间线：agent 对话与领域区块交错。一次运行是一张工具卡（进度、完成后内嵌成绩摘要与“变差的 N 题”）；SN 改动是 diff 卡；需要人决定的是一行就地按钮。输入框只跟 agent 说话，附件可以是题单、结果、案例。
- 右栏：案例列表（按状态、分差筛选）、案例详情（题、答案、gold、论文原文证据高亮）、前后对比、折叠的 run 身份与 provenance。
- 解读规则放一处（信息图标或帮助页），不在每个数字下重复免责声明。

## 3. 现状（2026-10-13）

| 能力 | 状态 | 证据／位置 |
| --- | --- | --- |
| 五套数据冻结、无 gold 请求、不可变 bundle/index/store | 可用 | `notebook_bundle`、`artifact_store`、`bundle_index`；当前约束与操作见 [CURRENT_STATE](../CURRENT_STATE.md)、RUNBOOK |
| 实际 SN 源码捕获与 fresh 进程加载绑定 | 可用（显式 captured 分支） | E1/E2，`sn_source_capture`、`runtime_environment`；文件 hash 不等于完整执行环境 |
| 私有 dotenv／TOML／依赖／模型服务代际身份 | 部分可用（启动观测已记录） | E3b/c/d，`runtime_*_identity`；依赖、远端模型和凭据有效性仍未完全冻结 |
| scheduled-generation journal 结构审计 | 可用（run 级；不证完整历史） | E4，`scheduled_generation_audit`；`history_exhaustive=false` |
| 单题真实链路：生成→保存→官方评分→离线回访 | **可用（一次固定单题，n=1）** | `e5-s3-real-run-20261013/README.md`；接缝事实，不是基准成绩 |
| 一分区一进程的 CLI | 可用（captured 分支已实跑） | `run_notebook_benchmarks.py`；真实链路证据见 `e5-s3-real-run-20261013/` |
| 多分区批量展开与进度 | 原基线及批准补跑均终态（40＋5run；保留原结果） | 固定seed20261013、20篇72题，每模式20分区；chunk 17 finished／3 failed、62 success／10 missing，reasoning 17 finished／1 finished_with_errors／2 failed、39 success／30 clarification／3 missing；原执行exit1。官方Answer F1分别0.280731／0.193771（分母各72），Evidence F1 pending；`var/sn-eval/baselines/qasper-20-20261013/official-summary.json`；原共享¥10预算占用约¥6.514，仅missing13次获批串行补跑，12success／1clarification，生成与独立评分exit0；新subset分母10／3，工件另存 `retry-attempt-2/`，累计预算占用约¥6.628，不覆盖原72题基线 |
| 阶段指标（召回／精度／引用／拒答／成本） | 原40＋补跑5run已回读（diagnostic；未观测／不适用不补零） | `stage_metrics` 已接单run报告与summary；missing/pending残留记录拒计，not_applicable终态保留；[里程碑修复](../apps/sn-workbench/test-results/r1-launch-20261013/milestone-review-resolution.md)，220 passed／exit0，45真实run cases/summary与保存值相同。Hotpot文本包含诊断可能命中标题／重复文本，不等于source-unit召回；时间窗口调用归因仍有限 |
| oracle context 诊断轨道 | 离线chunk入口可用；正式答卷读写拒收明确诊断标记（真实oracle未执行） | `execute_oracle` → SN `_answer_chunks`；run reader/export/report/explorer 与通用 `build_submission`／validate边界隔离；已有带标记答卷的prepare／score／compare反例拒绝，[里程碑修复](../apps/sn-workbench/test-results/r1-launch-20261013/milestone-review-resolution.md)。仅证明明确provenance保护；reasoning专用入口未实现 |
| 配对比较与翻转表 | 真实20分区回读一致（15准入、5拒绝；SN答卷身份绑定已补齐） | `flip_report` 校验实际SN mode／manifest identity，与export共用局部分区scope规范化；借用不同runtime／features答卷反例拒绝。原 `flip-index.json`／`flips/` admission/summary/comparison保持一致；[真实回读](../apps/sn-workbench/test-results/r1-launch-20261013/milestone-real-readback.json)。合法身份差异仍逐run披露，不作模块归因 |
| run 账本／失败账本／假设卡（含 2026-10-14 工作台假设卡：逐题预测、人批准、配对核对） | 原40＋补跑5run已登记；20题用户按类确认；H1实施中 | [失败账本](failure-ledger.md) human_confirmed＝用户按类授权确认（2026-10-13），原初判/分数/状态保留；[H1假设卡](hypotheses/h1-reasoning-clarification.md) 先写后改；原72题固定分母，留出17题仅自动评分；H1尚未生成新结果 |
| 有效特性集合作为身份 | 已接 runner／reader／比较报告；新真实 run 保存投影 | `feature_identity` 从 effective settings 派生并在 reader 校验；比较按必须相等／披露／未知分档；M1-B 新 manifest 含 feature_set，旧 E5 显示未投影；配置差异反例通过 |
| 评测工具 skill（agent 可直接使用） | 已交付（命令／预算边界离线核对；新会话复述未独立验收） | [sn-eval skill](../skills/sn-eval/SKILL.md)；M0-B owned 4 passed；链路回读与账本查询 exit 0 |
| 工作台：任务、Agent 对话、候选、人评、计划草稿、冻结结果比较 | 可用；工作页 2026-10-14 改为 Paseo 式对话 | 自动选会话与轮询回复、输入框内选 Agent／模型、材料预览后附加、run 案例（含 gold）可附加；发送作用域 Bug 已修，真实 pi smoke 送达并回复。见 [评测状态](evaluation-status.md) 2026-10-14 |
| 工作台：run 卡、案例视图、翻转表、provenance 抽屉、侧栏状态 | 原M3已集成；H1阈值/真实diff followup中（preview未更新） | 原类型检查/export/浏览器证据保留；用户四项默认决定：QASPER status非success或answer_f1<0.5可配置、判断先失败账本、未绑定只给关联入口、真实H1diff截图。shared阈值/Task假设关联33passed/exit0，前端followup待交付 |
| Job 真实派发 | 界面真实运行一次（QASPER chunk 1 题，n=1，用户执行） | 更改页显式选固定计划、捕获源码、确认费用上限／超时、运行／取消与刷新恢复；按分区执行并进行独立官方文本评分，执行历史自动回访为 run；旧 Job v1 receipt 保持兼容。浏览器以无模型 smoke 验证，真实失败路径和已保存结果另验；[操作与验证边界](workbench-execution.md) |
| Task 绑定 SN worktree、更改页（diff／假设／验证） | HTTP／CLI／Agent scope 已接线（只读 diff；验证只产 argv） | `task_worktree`、API 注册 ID 绑定、CLI bind/read/run-argv；Task scope 使用绑定 workspace；新捕获保存 task_worktree 关联，避免同 HEAD 误挂；临时 git 非空 diff 及真实实验 worktree 空 diff 已验证；不提供 git 写操作 |
| 文档 | 已收敛为状态式，过程记录归档 | [CURRENT_STATE](../CURRENT_STATE.md)、[评测状态](evaluation-status.md)、[历史归档](archive/2026-10-source-execution-history.md) |


## 4. 与理想形态的差距

### 4.1 架构

1. **身份是最大化哈希，缺等价策略。** 远端模型权重无法由哈希证明；`.env` 中无关键的变化会打断配对。应分三档：必须相等（bundle、case、scorer）、必须披露（源码修订、配置差异）、无法得知（远端模型），比较器按档处理并披露。
2. **评测的是关闭了多个模块的 SN，却没有把“有效特性集合”作为身份暴露。** 结论会被误读为“SN 的成绩”。
3. **一分区一进程重新导入与 embedding，拒绝任何跨 run 复用。** 冻结文档＋固定 embedding 服务代际的向量可内容寻址复用，不损害可复现性；生成缓存继续关闭。
4. **readiness receipt 是冗余层。** 真实 runner 启动时观测的一切覆盖了它；今日生成真实 `ready` 的方式就是“把 runner 跑到 ingestion 之前”。应合并为 runner 在 ingestion 前写一张启动回执和执行参数。
5. **owner 生命周期在解决自己制造的问题。** 以 run 目录为真相源、子进程自写心跳与终态回执，owner 退化为“启动然后读文件”。
6. **诊断分与官方分重复。** run 内只保留必须依赖运行内部才能算的诊断（引用对象存在、上下文覆盖），镜像官方指标的删除。
7. **Job 自顶向下设计，至今没有执行过。** 最小定义应是“在该 run 目录跑这条 argv，状态即 state.json”，从真实失败里再长出 claim 与恢复。

### 4.2 流程与知识

8. 证据目录成了事实数据库，人读不了、机器不消费。
9. 状态文档是日志不是状态。
10. 评审按步骤触发而非按风险触发；reviewer 核 hash 多于构造反例；七百余测试但真实链路到今日才跑一次。最便宜且最诚实的回归是每日一次几分钱的真实 smoke。

### 4.3 界面

11. 中心区展示会话与回执等管线对象，没有 run、案例与 diff；方法论不变量泄漏成每个区块下的免责声明；计划页要求手敲哈希 case ID；记录页混放候选、人评与原始事件名；设置页是只读诊断 dump；侧栏缺实时状态；两种输入模式共用一个输入框；信息密度远低于 Paseo。原因是界面在 run 存在之前就做了，只能展示管线。
    - 2026-10-14 进展：工作页已是任务卡＋Agent 对话、单一输入框（笔记模式移出），记录页事件改为中文结果；Agent 有角色说明并能用 `scripts/sn-eval` 自取上下文；假设卡由分析 Agent 起草、人在更改页批准、从卡片新建实现 Agent、按逐题预测核对。仍未做：对话与 run／diff 卡按时间交错、设置页可编辑、批准假设卡时自动新开 worktree、阶段重放。

### 4.4 方法论

12. 从分数到改动缺三层：失败分类、阶段指标、切分实验（oracle）。这三层是 agent 分析与人决策的共同依据。

### 4.5 今日发现的产品问题

13. 部署配置 `model-services.toml` 缺 `gap_consult_query`／`external_evidence_answer` 两个 chat binding，strict 模式下 SN 无法启动。本次评测以评测侧实验配置补齐，应反馈 SN 侧修正部署配置。

## 5. 初版规划 R1

目标：在约四到六周内，让“跑一组题 → 读错题 → 提假设 → 改 SN → 配对验证”的循环真实转一圈，并把界面与文档改到围绕 run 与案例。每个里程碑以真实 smoke 验收，不以测试数量验收。评审按风险触发：只在里程碑边界做一次独立回看，不按切片开评审。

### M0 收口与减法（约 1 周）

- 文档重构：能力状态表（本文第 3 节为起点）、`docs/archive/` 收纳按日期的过程记录，CURRENT_STATE 瘦身为不变量与有效约束。
- 评测工具 skill：`skills/sn-eval/SKILL.md`，覆盖 prepare／run／export／score／读侧查询／账本，含禁止事项与付费边界。
- run 账本：`var/sn-eval/runs.jsonl` 的写入与查询工具；今日 attempt-1 作为第一行。
- 产品问题反馈：把 4.5 写成给 SN 侧的一条明确记录。
- 验收：新 agent 只读 skill＋能力表＋账本，10 步内能复现今日单题链路。

### M1 基线与阶段指标（约 1～2 周）

- 批量展开：按分区顺序或有限并行启动现有 CLI，进度与失败写入账本，不新增队列系统。
- 题单：QASPER 20 篇论文（分层随机、固定 seed）全部题目，chunk 与 reasoning 各一次；留出 5 篇不参与读案例。
- 阶段指标：从 outputs 计算 gold 证据召回率、上下文精度、引用精度、拒答率、调用数与延迟；写入 run 账本与单 run 报告。
- 失败分类 v0：agent 初判、人确认，形成失败账本首批条目。
- 验收：两份基线 run 入账本；阶段指标表可读；至少 20 道错题完成人工确认分类。

### M2 诊断与比较（约 1～2 周）

- oracle context 诊断轨道：显式参数、产物标 `diagnostic`、与正式 run 隔离。
- 翻转表：对比同题单两个 run，列出变好／变差／不变与案例链接，复用既有配对与准入核心。
- 有效特性集合进 manifest 与比较身份；比较身份分三档并披露。
- 回归集：由修好的题与高价值错题组成，固定文件。
- 验收：oracle 与真实 run 的差值可读；翻转表能从账本两行直接生成。

### M2-C Task 绑定 SN worktree（约 1 周，可与 M2 并行）

- 像 Paseo 一样，每个 Task 可以创建或绑定一个 SN worktree：把 `/home/wabiwabi/silicon-notebook/project` 登记为 Paseo project，Task 通过 Paseo 的 worktree workspace 从当前 HEAD 分支出去，记录 workspace id、worktree 路径、分支与 base 提交；Task 下的 Agent 会话在该 worktree 中改代码。
- 评测复用 E1 捕获：运行前对 worktree 做 `capture_sn_source`（含 dirty 与新增文件），`--sn-source-ref` 指向该捕获；`--project-root` 仍指原 project 且只读取 `.env`（worktree 不含未跟踪的 `.env` 与 `model-services.toml`）；原树不动。
- 绑定时保存一次“起点快照”（HEAD、dirty 清单、捕获 ref），之后的改动据此与既有 dirty 区分。
- 验收：在一个临时 worktree 中改一行 SN 代码，Task 页能显示该改动，捕获 ref 与 baseline 不同，运行走捕获字节而非原树。

### M3 工作台围绕 run 与案例重做，含初步更改页（约 2 周，可与 M1/M2 并行）

- **初步更改页**：每个 Task 一页，显示 base（绑定起点）、当前 worktree diff（文件列表、增删行、按文件展开，起点既有 dirty 与之后改动分开标记）、关联假设卡、在该 worktree 上跑过的 run（按 run 账本的 source ref／head_revision 查询）、“验证”动作（R1 先生成可复制的批量运行命令并说明预算；M4 Job 可执行后变为按钮，完成后直接给出与 baseline 的翻转表）。只读 `git diff`，不做提交、合并、回滚。

- 以今日真实 run 为唯一 fixture。
- 中心时间线新增 run 卡（进度、成绩摘要、变差题入口）；右栏新增案例列表、案例详情（证据在原文高亮）、翻转表、provenance 抽屉；侧栏加状态点。
- 免责声明收敛到一处；计划页改为选题器（分区分组、筛选、随机抽样带 seed）。
- 暂不动设置页与 Job 页。
- 验收：浏览器中打开真实 run，三步内到达任一错题的证据高亮；与 Paseo 并排对照由用户判断。

### M4 Job 最小可执行与第一轮真实改进（M1～M3 之后）

- Job 最小定义：argv＋run 目录＋运行确认与超时；状态读 state.json；取消为明确动作。
- 第一轮改进：从失败账本选一条假设（候选：chunk 模式 top-k 与重排，今日观察到 16 个检索块仅 1 个相关且 rerank 未调用），在 Task 绑定的 SN worktree 中改动，通过更改页的“验证”跑配对、看翻转表、写假设卡。
- 验收：一条假设走完整循环并有翻转表结论，无论结论是否为正。

### 减法清单（R1 期间执行）

- 合并 S3/S3b readiness 为 runner 启动回执；不再新增独立 gate。（2026-10-25 已删除 readiness receipt，runner 启动时的实际观测即回执，见 DELETION_LOG。）
- 删除 run 内镜像官方指标的诊断分。
- owner helper 保留为启动器，取消后代扫描等观测性分支的扩展。
- 不新增队列、账本之外的状态存储、Job claim/CAS。
- 文档不再按日期追加“当前”段落。

## 6. 原则

- 正确性不变量不因提速放松；提速的办法是减少层数，不是跳过检查。
- 每个里程碑用一次真实 smoke 验收，费用以分计，远低于任何一轮评审。
- agent 的每个判断引用具体工件；人只在需要决策的地方出现。
- 先让 20 篇论文的循环转起来，再谈 400 篇与五套 full。
