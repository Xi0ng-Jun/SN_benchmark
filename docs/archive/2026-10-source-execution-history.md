# 2026-10 源码执行与工作台历史记录

本文件承接状态文档中按日期追加的过程记录。内容是历史证据和当时的限制，不自动代表当前可执行能力；当前入口请读 [CURRENT_STATE](../../CURRENT_STATE.md)、[评测状态](../evaluation-status.md) 和 [目标与规划](../goal-and-roadmap.md)。

## 从 CURRENT_STATE.md 迁移的活跃约束段落

以下内容原样迁自 `CURRENT_STATE.md`「活跃约束」，原位置为原文件第 25—44 行（2026-10-13 工作树版本）。迁移原因：这些段落记录 E1—E5、工作台和 Job 的过程时点，当前页只保留仍有效的约束。

- **2026-10-13 E5 S3/S4 已跑通一次真实链路（Fable 5.1 接手）。** 用户要求提速并允许省略小步评审，Fable 接手 shared/docs（接手时全部旧 agent idle，无重复派工）。零网络 preflight 用真实 `execute` 路径在 ingestion 前停止，观测 strict=true、实验配置下 37/37 binding、3 服务凭据变量名、解释器/依赖 `startup_verified`，据此生成首个基于真实观测的 `ready` receipt；attempt-1 经交付版 R1 owner helper 启动显式 captured CLI 并消费该 receipt，pidfd 确认 exit 0、完整排空、墙钟 17.5 秒，run `finished`。固定 case `qasper:397a1e85…` 答案 `success` 已保存，QASPER evidence snapshot complete，20 次 scheduled 调用（embedding 15／chat 5／rerank 0）全部 completed 且 journal 配对 20/20。诊断分为 adapted/diagnostic；独立 `export-sn`→`score` 官方文本评分 answer_f1 0.6667、evidence_f1 1.0，planned 1／success 1、无 pending；`load_run` 离线回访通过。**边界**：原 SN `model-services.toml` 缺 `gap_consult_query`／`external_evidence_answer` 两个 chat binding（strict 下部署配置无法启动，这是应反馈 SN 侧的真实产品问题），本次使用评测侧私有实验配置（原字节＋两条 binding 指向同一 chat 服务），原 SN 文件／`.env`／数据库未改；n=1、仅 chunk，只证明链路可用，不是成绩或比较；owner 残留后代完整性仍 unknown；S2 R1 原5153对应复核未执行，“复核前阻断”是按用户本日指令解除，不是评审闭合；Job 仍 `executable=false`，UI／preview／user state 未改。证据与限制见 `apps/sn-workbench/test-results/source-execution-20261012/e5-s3-real-run-20261013/README.md`。
- 用户接受UI01当前布局，授权继续真实评测→修改→验证闭环。E1实际工作树源码捕获、E2显式captured分支材料化／加载核验、E3a固定Job完整canonical/source准备事实与离线回访、E3b私有dotenv及Settings类／实例默认绑定已在各自范围闭合，8项当前hash匹配闭合账本。E3c已接入fresh CLI，原独立审的保存声明／关键模块遗漏和跨run比较失配两项Important均经原reviewer对应复核关闭，在启动观测范围闭合。统筹实际consumer10项、reviewer新／改pytest10项与独立7组场景通过；12文件准确hash匹配。精确启动观测保留，比较只归一明确运行坐标与观测时间，依赖字节／venv／外来路径差异仍拆分。依赖环境未冻结，origin_file_sha256不证明执行字节。E3d已在启动registry及scheduled Future代际观测范围闭合，原5153一次独立审0 Blocking／0 Important／1 Minor；捕获SN的合成正常consumer／排空见证通过，网络请求0，不代表真实凭据或模型可用。E3d原M1及审结论保留；E4已在run级保存journal结构审计范围闭合，逐题展示未接入，不能据启动比较推导完整调用代际相同或runner就绪，可靠真实owner仍待接线，官方评分是独立后续动作，Job仍不可执行。原Codex6.1 high统筹唯一接管shared/docs，scope与reviewer不重复，见[源码执行记录](../superpowers/plans/2026-10-12-sn-workbench-source-execution.md)。当前不改SN源码或启动模型，UI01预览保持；单题smoke在绑定与独立runtime落实后使用下述已有局部授权。
- E5 当前仅进入 S1/S2 准备：QASPER 固定单题与完整论文已离线核验，安全投影保留原 model-services 声明、合法 credential variable 名称和 endpoint hash；`credential_variable_configured` 只表示声明形状合法，不表示有效环境变量非空、认证成功或 endpoint 可达，非空状态若后续可安全观测须另列事实。effective Settings、strict registry、可达性及实际调用预算仍未知，缺 catalog binding 不放宽。真实 CLI 前必须通过窄 owner gate：父进程持有唯一子句柄并负责 wait/exit 归属，取消／超时／句柄丢失为 unknown、不重派，答案保存与诊断／官方评分分开。S2 原交付经唯一5153审为 1 Blocking／3 Important：除真实 ECHILD 状态误判外，还存在有界排空时输出前缀丢失／取消无回执，以及 proc stat／进程组与完整后代清理混淆；原735已在新 evidence 路径按同一 owned 范围修复，修复后再由原5153对应复核。Linux pidfd 原语另有 exit7／ECHILD／SIGTERM 可行性证据，供原作者选型，不是修复或 readiness 证明。真实 CLI 在对应复核闭合前继续阻断。沿用现有 runner 时 `score_outputs` 的确定性分只能标 adapted/diagnostic，官方 QASPER 文本 scorer 另行 pending；`no_answer`、`error`、`missing` 仍按正式 scorer／分母协议分别处理，不能统称为 `unscored` 或擅自排除；不新增完全不评分模式。E5 不改 SN 原树、原配置、Job/UI/preview/user state，Job 仍 `executable=false`。
- E5 S2 修复版已交付到独立 `e5-owner-fix/`，最终 witness exit 0，原红灯、receipt 与 protected hash 保留；原5153对应复核进行中，因此仍不称 S2 通过。复核期间只收敛 launch 边界：root 持有唯一外层 wrapper 的 live handle/pidfd，namespace init 执行 CLI，wrapper/init/CLI 退出事实、CLI receipt、输出排空和后代观测必须属于同一 run；缺失或冲突一律 unknown。owner evidence 与 CLI `--run-dir` 使用两个新建且不嵌套的目录，彩排 project-root 使用 evidence-owned synthetic project，S1 captured refs 独立传入。详见 `apps/sn-workbench/test-results/source-execution-20261012/e5-preparation/s2-launch-boundary-brief.md`。不启动 CLI/SN/model/network，不提升 Job。
- 在上述 gate 仍等待对应复核期间，已完成一条独立 evidence-only captured-CLI rehearsal：`src/rag_eval/captured_cli_rehearsal.py` 读取已材料化 S1 refs，在合成 project-root 中运行零网络 synthetic child，实际观察到 `unshare-user-net`、Linux pidfd `waitid`、exit 0 和完整排空；7 项定向测试通过。它写入独立 owner receipt、`state.json`、`outputs.jsonl`、diagnostic score 与 `official-score-pending.json`，并明确保持 `offline=true`、`evidence_only=true`、`job_executable=false`。证据在 `apps/sn-workbench/test-results/source-execution-20261012/e5-offline-rehearsal-20261013/`。该彩排没有 import SN、读取原 project 配置、调用网络／模型或运行官方评分，不替代 R1 对应复核，也不把 S2 或 S3 标为通过。
- E5 S3 readiness gate 已实现为纯离线输入适配：`s3_readiness.py` 复用 E3d loaded model identity 校验，输出带 fingerprint 的 readiness receipt；当前已知缺失 `external_evidence_answer`、`gap_consult_query` 会返回 `blocked`，Settings/凭据/可达性未观测会保留 `unknown`，不会因为配置文件存在或诊断分数而返回 `ready`。44 项 owned 与 runtime model/config 相关回归共 61 项通过；未读环境变量值、未联网、未启动进程或模型，Job 仍 `executable=false`。范围见 `apps/sn-workbench/test-results/source-execution-20261012/e5-s3-readiness-scope.md`。
- S3b 的 `--readiness-receipt` 为可选参数；省略时 captured CLI 仍走原路径，因此本切片不是所有 captured 执行的强制门禁。
- E5 S3b 已把 readiness receipt 接到显式 captured CLI 的启动前边界：CLI 根据不可变 bundle/index、单题 scope 与 SN source manifest 派生期望身份，显式提供的 receipt 文件缺失／无效／非 ready／身份不匹配时在调用 execute 前拒绝；旧非 captured runner 不读取 receipt。相关 readiness、CLI、Job API/CLI 与 notebook runtime 回归 **113 passed**，但当前配置缺少严格 registry binding 且 Settings／凭据／可达性仍未观测，因此不能生成 ready receipt、不能启动真实 SN／模型，也不能提升 Job `executable`。
- 后续 Linux readiness witness 还显示 `unshare --user --map-root-user --pid --fork --mount-proc --net --kill-child=KILL` 可在本机提供 namespace PID1 和后代 pidfd 退出观察；该证据只用于未来在后代完整性仍为 unknown 时划定最小启动边界，不改变当前735修复或5153对应复核，不宣称 containment、重启对账或真实CLI就绪。
- 本机做开发、文档和不调用模型的离线检查／测试；实际 SN、生成模型、judge 和官方模型评分在服务器执行。当前整理任务不启动实验。
- 2026-10-12新增局部授权：用户允许针对本机 `/home/wabiwabi/silicon-notebook/project` 只读核对实际源码／加载关系及独立临时runtime的小scope SN smoke。用户原话为“简单的测试”，当前增量目标是一题／一次Ask，完整资料所需导入、embedding和metadata可能产生多次模型调用，不把计划里的“单请求”当作用户要求HTTP上界1；实际配置可知后记录范围与估算，重要预算／模型取舍仍未知才由父收敛。本授权不扩大为五套full／judge／批量实验，不修改SN源码、原数据库／生产数据，不读取输出.env／密钥，不去掉dirty检查。UI01预览保持，暂未启动SN smoke；后续真实绑定仍需据实验证。
- 工作台 A 阶段的最小 Paseo 接入已在披露的本地范围闭合：独立临时 state 会话读取、无 prompt 创建及一次短消息，属于传输与生命周期验收。最后浏览器修复没有再次真实发送；证据及限制见[阶段记录](../superpowers/plans/2026-10-11-sn-workbench-agent-collaboration.md)。
- 工作台 B／C 首切片已在披露的本地范围闭合并更新预览：完整计划草稿、追加固定修订／回访，以及已关联冻结结果的只读比较。独立 state 实际页面验收与 Astra／Opus最终收敛通过；原三任务只读验收、state 内容不变。保存不执行 Job，源码／模型／scorer 尚未冻结；完整 canonical 输入独立登记，不要求先有成绩。正式比较复用既有准入与比较核心，原三份历史结果不强行配对。保留 canonical 失败分类／左右不对称和未测写锁预算三项 Minor，见[B／C 记录](../superpowers/plans/2026-10-12-sn-workbench-plan-comparison.md)。
- J01固定revision到独立Job的登记／回执／回访／派发前取消及[J02独立临时state固定替身验证](../superpowers/plans/2026-10-12-sn-workbench-job-lifecycle-probe.md)已在各自披露范围闭合，已随UI01受管更新加载当前后端，但没有Job页面派发入口。真实Job仍仅 `not_dispatched`／`cancelled`、`executable=false`；jobs.probe单独保存替身观测，不产生评测完成。失去OS status或owner句柄保持unknown，不重派；取消走原helper私有管道，owner不按PID发信号。自然PID复用为同owner另一child时的退出status代际归属尚未制造／排除，真实受管执行前必须另外核验；本地重开SQLite／owner崩溃不是实际服务重启验收。旧默认runner仍从mutable project加载；E1/E2及E3a/b已补显式capture/输入/配置分支，不删除旧dirty gate、不改SN。完整dependency/model/scorer及真实owner仍未绑定，无真实runner派发；预览为UI01工作面版本，不推进J03。[J01证据](../superpowers/plans/2026-10-12-sn-workbench-job-registration.md)与J02证据不能扩大为真实执行可用。
- UI01界面切片已通过fresh独立审与Astra／Opus体验回看，并更新受管预览：工作／结果／计划／记录分流，设置独立，页面内草稿及unknown恢复语义保留。更新前一致备份及schema2→4副本预检通过；现有4任务全部保留，原3份结果只读可访问，10个既有表与7个非SQLite状态文件内容不变，jobs为空。证据、截图及后续体验项见[UI01记录](../superpowers/plans/2026-10-12-sn-workbench-ui-focus.md)。
- 当前服务器范围为 **SN-only**：五套 chunk，另加 MultiHop reasoning；先 smoke 后 full。外部方法／reference 暂缓，专门 Agent judge 评测需单独安排，不由普通 reasoning 运行自动产生。
- 新正式 Notebook 运行使用 `notebook-data-v3`／`notebook-request-v3`；原生 Agent 使用 `sn-deepeval-native-v1` 和 DeepEval 4.2.2。Python API 部分旧默认仍存在，新调用显式选版本。
- 一个 partition × mode × attempt 使用新进程、新 run-dir；当前入口不支持原目录隐式续跑。实际源码、模型、scorer 和依赖身份在服务器冻结，结构预检不能代替环境或模型对齐验收。
- **共享输入／源码已实现，服务器迁移待验收。** `prepare --install-bundle ... --artifact-root ...` 安装已有 bundle，并将受信任输出 index ID 冻结到 campaign 配置；新 run 必须同时传 `--artifact-root`／`--artifact-index-id` 复用不可变 bundle/index/evaluator/SN 对象，不能每次从可变 pointer 重取预期 ID。runtime 仍独立；单 run 自动报告只校验所用 capsule，正式 export 完整校验 canonical bundle。
- **完整审计与回传答卷分开。** outputs/Agent 工件保留完整事实；SN submission 使用五套 compact scoring projection，保留证据回放字段、状态与来源 hash。`package_benchmark_results.py --mode results|review` 按角色生成私有包；无自动删除、TTL、GC，不改原 run/runtime。具体边界见[结果存储与导出](../result-storage-and-export.md)。

## 从 docs/evaluation-status.md 迁移的日期节

以下内容原样迁自 `docs/evaluation-status.md` 原第 7—44 行，包含 2026-10-11、2026-10-12 和 2026-10-13 的工作台、源码执行、E4、E5 过程记录。当前页只保留压缩后的状态段，并链接回本节。

## 2026-10-11 工作台首个开发切片

已实现 Expo Web 工作面及 Python HTTP／CLI：workspace → 持久任务 → 显式接入真实结果 → 成绩／案例／源资料与保存观测 → 人评／调查笔记 → 固定修订历史及导出。实际范围和操作见[使用说明](../apps/sn-workbench/README.md)。任务记录在 SQLite，结果消费内容冻结为 hash 绑定的独立快照，原工件只读；复用既有官方校验与比较准入，不另建差分器。

本机接入 QMSum SN 单题（1/281）、MultiHop PaLM 公开答卷（2556，检索分母2255）和 ALCE ASQA Vanilla 100题文本成绩；它们都是历史工件，非当前服务器成绩。旧 QASPER 因当前 prepared／evidence projector 身份校验不一致被拒绝，未改旧身份绕过。来源与适用范围在页面和配置明确标注，官方汇总、条件不适用、batch-only、零分、缺失及 pending 分开。按用户后续反馈，左右侧栏进一步对照 Paseo 实际壳层调整为约320px、薄标题／标签栏、层级单行导航和底部图标，支持独立收起、拖动及浏览器宽度记忆。

定向 `tests/test_workbench.py`、`tests/test_workbench_api.py`、`tests/test_benchmark_comparison.py` 共 **85 passed**（已安装 Starlette/AnyIO deprecation warning 1项）；前端类型检查与 Expo Web 构建通过。实际 Chromium 流程覆盖任务创建、结果接入、案例阅读、切材料及隐藏右栏后的草稿保留、人评／笔记持久保存、丢回执幂等重试、刷新恢复、历史、两栏拖动／宽度恢复、960px中间空间、390px 窄屏与深色。侧栏后续调整只改前端，未重跑 Python 测试；相关类型检查、构建和浏览器流程重新通过。Paseo 服务代理另验登录及 Origin、三个真实任务、有效零分案例与 ALCE pending；预览库未写测试判断。浏览器写入测试使用独立 state，截图存本机忽略目录。

首个切片交付时尚未接入 Agent。随后 A 阶段增加 Paseo Task-session 关联／读取、显式上下文、有出处的候选及人工决定、无 prompt 创建和显式消息发送；逐切片独立评审、回执恢复契约、临时 state 验收及当前门槛见[阶段记录](../superpowers/plans/2026-10-11-sn-workbench-agent-collaboration.md)。Python 工作台回归 377 项通过；SDK ACK 与执行完成分开，unknown 不自动重发。2026-10-12 A 已经最终委员会闭合，预览更新并恢复受管服务，原三任务只读核对与A能力声明通过；未向用户state写入测试记录或重复真实发送。服务仍是本地单操作人开发接入，没有评测 Job、SN 改码或修改验证；旧 Dashboard 保留。本阶段不运行 benchmark／judge，也不据最小 Agent 接入验收报告正式模型成绩。

## 2026-10-12 工作台 B／C 首切片

工作台 B／C 首切片在披露的本地范围闭合并更新预览：持久计划草稿、固定修订回访和已有冻结结果只读比较。Astra／Opus最终无Blocking／Important，保留失败诊断分类／左右不对称及写锁预算未测三项Minor。实际 App 验证使用独立 canonical／state；预览只读核对原三任务、A/B/C能力、execution=false，全部状态内容不变。没有执行 Job／模型或新增正式成绩，详见[B／C 记录](../superpowers/plans/2026-10-12-sn-workbench-plan-comparison.md)。

## 2026-10-13 源码执行 E3d

E1/E2及E3a/b/c/d在各自披露范围闭合。E3d接入真实captured启动消费者，分别记录私有TOML、有效Settings、正常owned registry和实际scheduled Future服务代际，仍未调用真实模型。原5153一次独立审0 Blocking／0 Important／1 Minor；reviewer实际9 owned＋5 shared／legacy pytest通过，并以捕获原生消费者、合成私有配置和禁止网络验证strict完整catalog、invalid reload旧registry、A/B路由及回调排空。root原见证目录不符失败未改写。M1保留：新代际journal尚无配对／完整性审计或逐题Dashboard暴露，启动比较不证明后续每次调用代际相同。下一步先只读journal审计范围；Job仍不可执行，preview／用户state不变。准确hash、原失败及限制见[唯一源码执行记录](../superpowers/plans/2026-10-12-sn-workbench-source-execution.md)。

## 2026-10-13 E4 保存journal审计

E4由root在run级结构审计及共享消费范围闭合：Astra/high scope、Pi owned实现与指定Sol审0B／0I／0M（38passed及probe／fixture／编译）；root红8fail／1pass→绿9新＋4直接受影响＝13pass。原5153仅共享四文件delta审0B／0I／0M，reviewer实际9pytest与10组独立完整消费者（含九次预期结构拒绝）分开记数，4 reviewed＋3 protected准确hash匹配。reader／aggregation／report可见保存journal的配对、pending和截断，legacy计数、case事件、启动family／pairing／分母及保存工件保持原义。原红灯、审查与完成通知保留，无旧矩阵重跑。E3d原M1结论不改，逐题展示仍未接入；history_exhaustive恒false，不证明完整历史或HTTP／模型／scorer／Job完成，不改变Job `executable=false`、UI／preview／user state。准确记录见[源码执行计划](../superpowers/plans/2026-10-12-sn-workbench-source-execution.md)。

## 2026-10-13 E5 evidence-only 完整彩排

已完成一条不调用 SN 或模型的最小端到端彩排：通过 `scripts/run_captured_cli_rehearsal.py` 固定 S1 bundle/index/SN ref、公开依赖与解释器期望值，经 synthetic project 和 R1 owner/wrapper 观察后，分别落盘 owner receipt、运行状态、输出、诊断分数及官方评分 pending。实际本机观察到 network namespace guard、Linux pidfd `waitid`、正常 exit 0 和完整排空；定向回归为 **7 passed**，相关源码执行／生命周期回归合计 **125 passed**。证据保存在 `source-execution-20261012/e5-offline-rehearsal-20261013/`。

这条流程的状态是 `diagnostic_complete`，`offline=true`、`evidence_only=true`、`job_executable=false`；诊断分数不等于官方 QASPER 分数，官方 export／score 仍 pending。S2 R1 对应复核仍独立等待中，真实 SN CLI、模型调用和官方评分没有启动。

S3 readiness gate 已完成：纯离线 `s3_readiness.py` 复用 E3d 的 loaded identity 校验，输出带 fingerprint 的 `ready`／`blocked`／`unknown` receipt。当前实际缺少 `external_evidence_answer`、`gap_consult_query` 时返回 `blocked`，effective Settings、凭据存在性或 reachability 未知时不会误报 ready。44 项 owned 测试及 runtime model/config 相关回归共 **61 passed**；没有读取秘密、联网、启动模型或提升 Job。
S3b 的 `--readiness-receipt` 为可选参数；省略时 captured CLI 保持原路径，不构成统一强制启动门禁。

S3b 已把 receipt 接到显式 captured CLI：在真实子进程启动前按不可变 bundle/index、单题 scope 和 SN source manifest 校验显式提供的 receipt；指定文件缺失、无效、非 `ready` 或身份不匹配均以稳定类别拒绝，非 captured 旧 runner 不读取该参数。readiness、CLI、Job API/CLI 与 notebook runtime 定向回归共 **113 passed**；仍未读取秘密、联网、启动 SN／模型或提升 Job，当前配置缺口仍阻止生成 ready receipt。

## 2026-10-13 E5 真实固定单题链路（S3 生成＋S4 官方评分）

Fable 5.1 接手后在实验配置下跑通一次完整真实链路：零网络 preflight（真实 `execute` 路径、ingestion 前停止，exit 0）→ 基于真实观测的 `ready` readiness receipt → R1 owner helper 启动显式 captured CLI（pidfd 确认 exit 0、完整排空、17.5 秒，run `finished`）→ 答案与 evidence snapshot 保存（1 题 `success`，20 次 scheduled 调用全部 completed、配对 20/20）→ 独立 `export-sn`／`score` 官方 QASPER 文本评分 **answer_f1 0.6667、evidence_f1 1.0**（subset，planned 1／success 1，pending 空）→ `load_run` 离线回访。诊断 token F1 0.667 与官方值相同但为独立计算，仍分开标注。

这是本地核实的链路事实，不是 benchmark 成绩：n=1、仅 chunk；原 SN `model-services.toml` 缺两个 chat binding（strict 下部署配置无法启动，已作为产品问题记录），本次使用评测侧私有实验配置补齐（原 SN 文件、`.env`、数据库、HEAD 均未改）；owner 残留后代完整性仍 unknown；S2 R1 对应复核按用户本日“省略小步评审”指令未执行；Job 仍 `executable=false`，UI／preview／user state 未改。证据、命令退出、hash 与完整限制见 `apps/sn-workbench/test-results/source-execution-20261012/e5-s3-real-run-20261013/README.md`。同日形成的目标、差距与初版规划见[目标与规划](../goal-and-roadmap.md)。


## 从 ARCHITECTURE_CURRENT.md 迁移的日期增补

以下内容原样迁自 `ARCHITECTURE_CURRENT.md` §1，原位置为原文件第 13—43 行（2026-10-13 工作树版本）。迁移原因：架构正文改为当前／待审／历史状态小节，日期过程证据集中保存。

2026-10-11 增补：`apps/sn-workbench` 是 Expo Web 工作面，`src/rag_eval/workbench` 提供 Python 接入、SQLite 用户记录、FastAPI 和 CLI。显式导入复用公共 `validate_scored_submission`，阅读允许不完整生成，正式比较仍要求完整；imports 快照负责结果事实，SQLite 负责持久任务、追加判断／笔记与历史，前端不重算官方总分。A 阶段增加 Task-session 关联、Paseo 会话读取、显式上下文、有出处的候选和人工决定，以及无 prompt 创建和显式行为发送。SQLite 保存本地意图与回执，Paseo 保存实际会话与执行状态；SDK ACK 不代表执行完成，unknown 只能核对原请求，不能自动重发。Job、SN 改码、修改验证和旧 Dashboard 迁移仍未接入。逐切片评审和实际验收以[阶段记录](../superpowers/plans/2026-10-11-sn-workbench-agent-collaboration.md)为准，操作见[使用说明](../../apps/sn-workbench/README.md)。

2026-10-12 B／C 增补：计划草稿复用 SQLite history／requests 保存完整不可变修订与事务回执，全局 head CAS 不按 actor 分叉；登记完整 canonical 输入独立于结果接入。比较读取 Task 关联的冻结对象并绑定可信完整 canonical，路径入口与对象入口共用既有比较核心，官方汇总和附加配对估计分开。两者均 `execution=false`，无新队列表；后续执行仍须捕获真实加载的源码、模型和 scorer 身份。实际评审、保留限制与部署证据集中于[B／C 记录](../superpowers/plans/2026-10-12-sn-workbench-plan-comparison.md)。

2026-10-12 J01 登记边界已独立闭合：独立 Job 登记以明确的计划 revision 和 content hash 为输入，SQLite jobs 保存当前状态，requests 保存原命令回执，history 仅作审计；取消 Job 不改变计划草稿。首步只允许 `not_dispatched`／`cancelled`，`executable=false`，执行输入字节、实际源码加载、model/runtime、scorer 仍未捕获。源码 archive 与实际加载位置尚未绑定，现 runner 从传入 checkout 的 backend 加载；保留 dirty gate，不据归档宣称运行身份已冻结。UI01预览更新已加载当前后端，未接Job UI；[J01 证据](../superpowers/plans/2026-10-12-sn-workbench-job-registration.md)不代表执行闭环，单次隔离替身另按[J02 计划](../superpowers/plans/2026-10-12-sn-workbench-job-lifecycle-probe.md)验收。

2026-10-12 J02仅在独立临时state／固定替身验证范围闭合：schema4追加nullable jobs.probe，jobs.record仍负责真实计划意图，probe是唯一当前替身观测，history审计、requests原命令事实。启动授权事务先提交再创建固定短helper，外部进程I/O不持SQLite写锁；取消意图与Job取消同事务，owner私有管道控制原helper。只采用实际取得的OS终态status，ECHILD／owner丢失unknown、不自动重派；exit0不产生真实评测完成。waitpid数字PID没有独立代际防护，自然复用为同owner另一child的status归属未制造／排除，真实runner前须另行验证；私有管道无PID信号不等于OS状态归属已覆盖所有代际变化。无attempt表／队列／probe派发HTTP/CLI/UI，UI01更新时已对受管state一致备份并升级schema，jobs仍为空，没有替身派发，见[J02最终范围与证据](../superpowers/plans/2026-10-12-sn-workbench-job-lifecycle-probe.md)。

UI01 将现有能力分配到工作／结果／计划／记录四个中心视图，设置独立消费 bootstrap 的登记配置，Inspector 仅解释具体结果、案例或固定历史。App 保持语义 hooks 和各领域 controller 的生命周期；导航状态不充当请求取消，计划首次打开后在当前 Task 中保持激活，材料父层保留按对象分桶的人评输入。纯展示组件只回调用户意图，没有第二套任务状态或配置写入接口。具体预览版本和验证范围见[UI01 记录](../superpowers/plans/2026-10-12-sn-workbench-ui-focus.md)，交互演进见[产品说明](../product/sn-workbench/interaction.md)。

2026-10-13源码执行增补：上述J01源码缺项为登记时事实。现在显式captured CLI分支材料化当前工作树SN对象，私有配置绑定Settings，核对所选解释器／声明及已加载依赖，正常构造原生owned provider后才进入repository；旧默认runner仍保留tracked dirty拒绝和mutable checkout加载语义。E3d在启动registry及scheduled Future代际观测范围闭合：declared TOML、有效Settings、loaded registry与实际提交runtime分别记录，实例级observer复用 `model-events.jsonl`，关闭provider排空回调先于observer／journal退出。offline reader校验保存的启动schema/hash和captured native来源，不加载当前产品或配置。源／依赖文件hash不是任意执行字节或完整环境冻结，Future终态不是HTTP／模型／scorer／Job完成。

E3d原M1报告与结论保留：交付时新scheduled-generation事件只有raw journal、没有离线审计或逐题Dashboard暴露。E4随后在保存journal的run级结构审计范围闭合；纯模块只配对已解析rows，reader唯一提供presence／truncated_tail事实并挂 `scheduled_generation_audit`，aggregation的run cell和report／summary消费该投影。结构歧义原样拒绝，pending／截断仍可读；legacy unfinished计数只属于显式adapter，includeFalse不读取新journal，case observations不接新pairs。没有新增账本或执行状态写入。

E4的 `history_exhaustive=false`恒成立，结构complete不是完整历史或HTTP／模型／scorer／Job完成：整对缺失和未记录调用不可证，增长journal的跨consumer读取不是原子快照。比较families仍使用启动事实，调用generation不同可能同family，不能据其证明每次代际相同。M1未整体关闭，逐题Dashboard／UI暴露仍待后续；可靠真实owner及生成／独立官方评分未接线，Job仍 `executable=false`。本轮不改UI／preview／user state，不调用真实模型，准确审查与原失败证据见[源码执行记录](../superpowers/plans/2026-10-12-sn-workbench-source-execution.md)。

E5 当前先做受控 QASPER 单题 CLI smoke 的 S1/S2：S1 固定完整 canonical bundle、单 case、完整论文、captured SN ref、解释器／公开 requirements 和脱敏 model-services 预算投影；投影中的 `credential_variable_configured` 仅代表合法变量名声明，不代表有效环境变量非空、认证成功或 endpoint 可达，后续非空观测必须另列。S2 在无网络彩排中证明父进程持有唯一直接 CLI 子句柄、`wait`/exit 归属、取消／超时／句柄丢失保留 `unknown` 且不重派，答案保存与评分事实分离。该窄 gate 不是 Job claim/CAS/restart 或第二账本；J02 裸 PID 语义不直接复用。现有 runner 的同进程 `score_outputs` 仍会产生确定性 SN adapted/diagnostic 分，官方 QASPER 文本 scorer 是独立 pending 动作；`no_answer`、`error`、`missing` 继续按正式 scorer／分母协议处理，不能统称为 `unscored`，不新增完全不评分模式。S1/S2 通过前不调用真实模型；S3 即使单题成功也只证明一次本机局部链路，Job 仍 `executable=false`，真实 owner／服务代际、完整预算与后续 Job 接线另行处理。

S2 修复版的对应复核尚未结束；复核后的最小 launch 验证边界见 `apps/sn-workbench/test-results/source-execution-20261012/e5-preparation/s2-launch-boundary-brief.md`。root 只持有一个外层 wrapper 的 live handle/pidfd，namespace init 执行 CLI，并分别核对 wrapper/init/CLI receipt、输出排空及后代观测；owner evidence 与 CLI run-dir 新建且不嵌套，synthetic project 与 S1 captured refs 分开。任何身份、receipt 或残留事实不一致均为 unknown，不把 pid namespace witness 当作 containment/readiness。
已另行完成一条 `captured_cli_rehearsal` evidence-only 彩排：合成 child 通过 owner/wrapper 边界后落盘独立 state、输出、诊断分数和官方评分 pending；这条路径实际验证了本机的 network namespace 与 pidfd 观察能力，但不导入 SN、不调用模型，也不提升 Job 可执行状态。
E5 S3 新增的 `s3_readiness` 只消费已观察投影，复用 `runtime_model_identity` 的完整 Settings/registry schema，生成带 fingerprint 的 ready/blocked/unknown receipt。它把严格 registry 缺口、凭据存在性和服务可达性分别保留，不能把声明变量名、离线彩排或诊断分数解释成真实模型就绪。
S3b 的 `--readiness-receipt` 为可选参数；省略时 captured CLI 保持原路径，不构成统一强制启动门禁。

S3b 将该 receipt 接入显式 captured CLI 的启动前边界：CLI 由不可变 bundle/index、单题 scope 和 SN source manifest 派生期望身份，若提供 receipt，则先校验再进入现有 runner；指定文件缺失、无效、非 ready 或身份不匹配均在调用 execute 前拒绝。非 captured 旧 runner 不读取 receipt。该边界已通过 113 项 readiness/CLI/Job API/CLI/notebook runtime 回归，但仍是离线身份门槛，不代表真实 Settings、凭据、服务或模型已就绪。


S4 的只读 export／score argv 与 fixed-case、完整 bundle object、official answer-first/evidence-pending 边界保存在 `e5-parent-score-handoff/official-score-handoff.json`，尚未执行；`no_answer`、`error`、`missing` 继续由正式 scorer／分母协议处理，不筛成功题。

2026-10-13 E5 S3/S4 实际执行：上述 preflight／receipt／owner／CLI／export／score 消费者已在本机以真实 SN 捕获源码、私有 runtime 与实验 model-services 配置连成一次链路（`e5-s3-real-run-20261013/`）。真实 `execute` 在 ingestion 前可零网络停止并产出完整 runtime identity，这也是生成真实 `ready` receipt 的来源；owner helper 以 pidfd 确认退出并排空输出；运行后 `export-sn`→`score` 独立产出官方 QASPER 文本分，`load_run` 可离线回访。该链路证明的是接缝可用（n=1、chunk、实验配置补齐两条 chat binding），不是成绩、不是 Job 派发、不是 owner 后代完整性或重启对账证明。对现有架构的差距评估（身份分档、有效特性集合、embedding 复用、readiness 合并进 runner、owner 简化、诊断分去重、Job 最小定义）与初版规划见[目标与规划](../goal-and-roadmap.md)。
