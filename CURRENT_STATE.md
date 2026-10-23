# 当前状态

更新：2026-10-15（UI01可用；E1/E2及E3a/b/c/d、E4在各自记录范围闭合；E4限保存journal的run级结构审计，不证明完整执行历史；E5 已于 2026-10-13 在实验配置下完成一次真实固定单题 captured CLI 生成、答案保存与独立官方 QASPER 文本评分，M4 已接真实 runner，已有真实界面生成与固定三题“假设→独立实现→同计划对照→结论”闭环证据（不代表正式成绩）；总体目标、差距与初版规划见 [目标与规划](docs/goal-and-roadmap.md)）。适用范围：Silicon Notebook 评测仓库。本文记录当前决策依据；范围改变时更新对应条目，历史证据另存，不在这里追加交接流水账。

## 目标

正确使用 benchmark 评测[agentic支持以及展示支持] SN，正确地与已有方法比较，得到可追溯、可解释的结果，支撑算法迭代与论文实验。

2026-10-13 起，总体目标的精确表述、理想形态、现状能力表、与理想的差距和初版规划统一维护在 [目标与规划](docs/goal-and-roadmap.md)；本文只保留决策依据与不变量。一句话目标：**让 SN 的每一次改动都能快速、可信地回答“答得更好了吗，好在哪、差在哪、为什么”。** 分数只负责发现问题和确认未退化，改什么由“失败归因到组件”的证据链决定。

**只保留 QASPER、MultiHop-RAG、ALCE、QMSum、HotpotQA 五套 benchmark。** 其他旧 benchmark 专属内容已从当前工作树删除，共用职责已分离，见 [删除记录](DELETION_LOG.md)；服务器与 Git 外私有工件未清理。协议、官方评分与比较入口已实现并有离线验证；**代码就绪不等于服务器全量实验通过**。当前先验收 SN 自身，服务器进度以实际工件为准，本次未复核。

## 核心不变量

- **生成不见 gold。** 输入资料、选题、请求和 scorer 版本可追溯；不得向生成注入答案／标注，不偷偷删题或缩小任务资料范围。
- **结果状态真实。** success、no_answer、clarification、error、missing、pending／不适用按各自语义保留；缺分不伪装成有效零分，不按低分重跑或只选成功题，分母遵守明确评分协议。
- **证据来自实际输出。** 引用、检索排名和 Agent 轨迹必须有真实观测／显式预测及可核验映射；上下文覆盖诊断、组件分不能冒充官方证据分或完整 Agent 分。
- **失败不抹掉已完成工作。** 回答及组件先于 judge 评分保存，指标完成即落盘；补评写新批次，超时后的迟到结果不覆盖既定终态，保留原工件。
- **比较条件可核验。** 本项目答卷的同题比较核对 bundle、scope、case IDs、scorer 和实际分母；各类比较披露模型／提示／预算差异。作者复现、适配方法和公开答卷分别标识，不把端到端分差直接归因于某模块。
- **强制作答协议必须独立。** `reasoning-no-clarification-v1` 使用评测侧 `cover-all-options-v1` 自动回答 SN intent contract 的必填歧义，保留原始 contract、`preview_needs_clarification`、逐项选择和 policy 版本；它不是标准 reasoning，不能与标准 reasoning 共用 protocol/comparison identity。默认仍为标准 reasoning，生成阶段不接触 gold。
- **比较证据匹配研究问题。** 论文或作者汇总值可直接标为发表参考，保留原文条件与未知项；不要求逐题答卷、统一重评分、BM25 或外部方法重跑。同题配对遵守工件检查，模块归因另需控制其余因素；没有独立榜单不等于没有可引用成绩。
- **实验与生产隔离。** 数据库、存储、日志和运行配置使用专用目录；不改生产数据、泄露密钥或将私有运行目录整体公开。

## 活跃约束

- LCD gate 是优化器内部的候选接受规则，自动记录采纳／拒绝／未决及证据，不是发布门；人工覆盖须附理由并保留原判定。

- **资料准备优化待服务器验收（2026-10-16）。** 新增显式 `deferred-notebook-metadata-v1`，整份资料导入后统一刷新 notebook metadata；默认逐篇刷新不变，策略保留在原始运行身份和答卷来源记录中；支持旧结果与优化后剩余题合并导出，无需全量重跑。真实 SN `970b6793` 配离线假模型的 100 篇 probe 验证 metadata 448→8 次，单篇摘要/论文元数据及两类 embedding 各 100 次保留；服务器约 51→13 分钟仍是推算。开发侧未操作服务器；用户最新决定：服务器立即停止当前在途批次，在原代码目录更新，保留已完成结果（含错误），统一重跑中断题并完成其余题；中断旧 run 保留但排除最终提交。暂不改变并发、缓存或答案合成失败处理，见[优化与验收说明](docs/deferred-notebook-metadata.md)。

- **工作台默认不设金额闸门（2026-10-15 用户决定）。** 开发环境由操作者在实际运行前自行估算，服务器使用公司模型服务；运行保留明确题单、源码、超时和取消。默认不安装旧固定价格表的模型请求拦截器，内部模型名由 SN 配置决定。旧显式费用保护仍保留；未知价格不计作零。服务器登记选择和脱敏失败诊断的本次验证范围见[评测状态](docs/evaluation-status.md)。

- **服务器工作台交付已整理，远端验收另计。** 当前工作台及其源码捕获、Agent bridge、执行与对照依赖一起交付；服务器以交接 SHA 为准，按[服务器接入](docs/server-workbench-handoff.md)新建 checkout、venv、私有配置和 state。初始化核验完整 bundle 并生成本机 ref，缺执行配置时不会启用模型。旧本机配置、历史 state、凭据和构建缓存不入 Git；现有服务器 bundle/scorer 先核验复用。本次范围为本机交付准备与无模型验证，没有查询/改动远端 SN 或运行新付费实验。

- **真实链路已局部可用，不能外推为基准结果。** 2026-10-13 的固定单题 QASPER chunk run 已保存答案、实际证据、scheduled journal、独立官方文本评分和离线回访；它是 n=1 的接缝验证，不是成绩、比较或全量完成证明。原始过程、失败证据和限制见[源码执行与工作台历史记录](docs/archive/2026-10-source-execution-history.md)。
- **源码与运行身份必须来自实际加载。** 显式 captured CLI 可绑定捕获的 SN 源码、私有配置、解释器和已加载依赖；文件 hash、声明配置和启动 receipt 不能证明完整执行字节、远端模型、凭据有效或服务可达。
- **状态与证据按真实生命周期保存。** 答案先保存再评分；`success`、`no_answer`、`clarification`、`error`、`missing`、`pending` 和不适用各自保留，不能补零、筛题或把诊断分当官方分。journal 结构审计只能报告已保存的配对、pending 和截断事实，`history_exhaustive=false`。
- **当前执行范围仍是 SN-only。** 五套 benchmark 先做 chunk，并另有 MultiHop reasoning；外部方法、专门 Agent judge、批量编排和服务器 full 运行仍待实际工件确认。旧 Job v1 保留只读意图；新 execution 层已支持操作者确认后的真实 Notebook runner 派发，当前可信本地输入为 QASPER。用户最初两个 Job 在模型调用前的目录隔离校验失败（工件保留）；修复后用户在任务“测试3”从界面完成一次 QASPER chunk 1 题真实运行，生成与独立官方文本评分均落盘（answer_f1 0.464／1，n=1），这是链路证据而非成绩。
- **协议和隔离边界保持不变。** 新正式 Notebook 运行使用 `notebook-data-v3`／`notebook-request-v3`，原生 Agent 使用 `sn-deepeval-native-v1` 与 DeepEval 4.2.2；一个 partition × mode × attempt 使用新进程和新 run-dir。实验数据库、存储、日志和配置使用专用目录，不触碰生产状态。
- **共享输入与输出投影已有实现，成本仍未实测。** 新 run 可复用不可变 bundle/index/evaluator/SN 对象，完整审计工件与 compact scoring projection 分开，runtime 仍逐 run 隔离。服务器上的 I/O、RSS、证据体积、阶段耗时和全量迁移收益未核实，不能据离线样例宣称性能改善。
- **工作台当前可读写记录、与 Agent 对话并通过界面执行 QASPER 评测。** 工作页是任务卡加 Agent 对话（Paseo 式单一输入框，含材料附件与 Agent／模型选择）；Agent 分两种角色：分析 Agent 在评测仓库工作、用 `scripts/sn-eval` 自取运行与案例证据并起草带逐题预测的假设卡；实现 Agent 只能从人批准的假设卡新建，在 SN worktree 改码，服务端不向它下发考题、案例或成绩，泄漏扫描检查该卡片的 diff。已部署从批准卡片创建独立 worktree 的入口，保存旧会话目录与历史绑定，未知创建只读协调；用户批准的首轮固定三题前后对照已完成并按要求停在第 3 步：真实独立目录创建、pi 分析／实现、源码捕获、官方评分和逐题核对均有回执。原基线导入失败保留，用户将预算提高至低于 ¥8 后使用原捕获源码新建恢复运行；SN 账本合计约 ¥0.043758（含超时预留）。读文件层面没有沙箱，隔离不是强制的。结果／计划／记录视图、冻结结果导入、案例阅读、人评和固定修订历史可用；更改页可绑定可信 worktree、创建固定 Job、捕获输入与源码、设置超时、确认运行并取消。前端不重算官方分；默认本地配置已接 captured Notebook runner、可选费用上限、总超时和独立官方文本评分，执行历史自动投影为关联 run。界面测试使用无模型 smoke 与合成 Agent fixture；真实 Agent smoke 用 pi（DeepSeek Flash），不用 Claude。用户启动失败的原工件保留；已有一次真实界面运行及三题“改 SN → 同计划重跑 → 翻转”闭环：两题升一题降，守护题未退化，但目标题预测失败，本轮不采纳候选；查询改写／证据集合有额外变化，原因保持未决。只证明这一小范围闭环，不能宣称 R1 全面验收或正式成绩完成，详见[实验报告](docs/experiments/2026-10-14-r1-ordering-loop.md)。操作与边界见 [界面执行](docs/workbench-execution.md)。
- **不扩大本轮授权。** 不改 SN 原树、原数据库、原配置或生产 state，本轮可启动评测工作台供用户操作，不启动 SN 生产服务、不输出密钥；真实模型、judge、官方模型评分和服务器部署只在明确授权且有实际工件时记录。所有未验证项保持 pending／unknown，不因测试通过而补造完成状态。

## 已废弃假设

- “必须维持旧成绩、不能大改”已不成立：正确性优先，允许必要系统修改和服务器重跑；五套与共用历史可保留，退役 benchmark 专属内容已删除，不恢复其旧测试／reader。
- “曾授权本机下载／校准，所以可以继续本机实验”已不成立：历史例外不扩大当前执行范围。
- “同 benchmark、同名指标或同名模型就能直接比较”不成立：还需验证输入、评分和方法条件；当前没有自动完成模型对齐的 gate。
- “运行结束／离线测试通过／已有公开答卷就代表正式结果完成”不成立：还需范围对账、官方评分和服务器证据；普通 reasoning 也不代表专门 Agent 评测已完成。

## 已知债务与暂缓原因

- **scheduled审计不证明完整历史，逐题展示仍未接入。** E3d原M1报告与0 Blocking／0 Important／1 Minor结论保留，不将M1整体关闭。E4经Astra scope、Pi owned实现／指定Sol审（38passed、0B／0I／0M）、root共享接线及原5153仅共享delta审闭合；root红8fail／1pass→绿13pass，reviewer9pytest与10组完整消费者分别通过，4 reviewed＋3 protected hash匹配。保存journal的run级pair／unfinished／truncated投影已接完整reader、aggregation和report；legacy unfinished计数只覆盖显式adapter，includeFalse不读／不审新journal。history_exhaustive恒false，未记录／整对缺失不可推断，startup比较相同不证明后续每次generation相同，跨consumer增长journal不保证原子快照。新pairs不归case、不改UI／preview，不能据审计证明HTTP／模型／scorer／Job完成。真实owner、凭据／服务可达性、官方评分和依赖冻结仍未就绪；M4 执行层已实现为显式 `job_execution` 配置下的 opt-in，默认本地配置已接 captured Notebook runner 与官方文本评分；新 UI 已有实际模型运行和固定三题闭环回执，仍不以这些局部证据证明 E3/E4 未验收部分。原失败／审查／通知证据保留。
- **五套内部版本默认与输入规则仍分散。** 旧 benchmark 专属路径已删除，共用职责已迁到独立模块；Python API 旧默认、五套历史 reader、预检与运行器的题单解析差异仍待收敛，见 [TRIAGE](TRIAGE.md)。
- **重复不可变输入／源码已收敛，可变成本仍需实测。** 共享模式和 compact 导出已实现；旧物理副本仍逐份验证，导入／数据库／建索引仍按 run 隔离，campaign 靠执行者展开。服务器实际 runtime、证据体积、I/O、RSS 与阶段耗时尚未核实，不能据离线样例宣称全量性能收益。
- **环境交付与报告分散。** SN／scorer／权重分别准备，Dashboard、官方比较和组件补评使用不同工件。暂不做统一平台，先明确身份及转换契约，避免重复计数或误合分数。

以上是当前工程取舍，**不构成暂缓修复正确性缺陷的理由**。具体风险和验证入口见[当前架构](ARCHITECTURE_CURRENT.md)。

## 明确不做

- 不再维护五套之外的旧 benchmark；本阶段不新增 benchmark，不启动外部方法实验，不以整理为由重写整套框架。
- 不触碰生产部署／timer，不追加 KG、长期记忆、交互可靠性、资料更新、PDF/OCR 或完整模块消融 campaign。
- 不制造跨套件总分、自动发布门槛、无证据的显著性／收入结论；不将未验证项标为完成。

操作见 [RUNBOOK](RUNBOOK.md)；实验事实见[验证与实验记录](docs/evaluation-status.md)；评分契约见[标准与实现符合性](docs/notebook-benchmark-standards-and-conformance.md)。新决定改变上述状态时，同步受影响文档；删除事实记录于 DELETION_LOG，其余保留必要历史证据。
