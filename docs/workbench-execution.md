# 从界面执行 QASPER 评测

2026-10-15：按服务器部署反馈，界面取消强制费用上限，支持选择已登记 SN worktree，失败时展示脱敏原因和诊断文件位置。保存计划和准备不会启动模型，点击“运行评测”才派发。已有 2026-10-14 用户 QASPER chunk 单题真实运行（n=1）和固定三题闭环记录，不代表本次又运行模型或服务器已验收。

## 操作

1. 打开工作台，使用原访问码。新建或选择任务，在“更改”页按登记名称和目录选择 SN worktree 后绑定。多条登记必须明确选择；只有一条时预选。已绑定任务不会随登记顺序更换目录。
2. 在“计划”页选择 QASPER 输入、明确题单和 chunk／reasoning 模式，保存计划。建议先使用少量固定题目验证流程，不按答案是否成功筛选正式成绩。
3. 回到“更改”页，点击要执行的计划，再点击“捕获源码并准备运行”。核对固定计划版本、题数、分区数、模式、模型和源码 ref。
4. 设置总超时（1–86400 秒），点击“运行评测”。费用由操作者运行前自行估算；默认不创建费用账本、不安装固定模型价格表拦截器。SN 使用服务器私有配置中的实际模型。
5. 页面显示生成／官方文本评分阶段和执行终态。下方关联 run 可打开案例、待读题和官方评分；现有翻转视图可在满足同题与身份条件时配对。评分失败保持缺失，答案和错误工件保留。

每个 partition 使用独立进程和新 run-dir；实验 runtime、日志和评分目录位于配置的 execution output_root。生成不读取 gold；官方评分在生成后独立执行。clarification、error、missing、pending 原样保存，不把进程 exit 0 解释为答案全部成功。

失败状态包含 `error_type`、`error_message`、`failed_phase`、`diagnostic_path`。诊断文件保留异常链和堆栈位置，不保存局部变量或源码行；消息脱敏后落盘。分区完整脱敏 stderr 的位置在 batch report 的 `stderr_path`，工作台优先展示生成失败，评分失败另存 `scoring-status.json`。启动前校验失败尚无 run 目录时，诊断写到独立临时 sidecar，其路径写入 stderr；诊断文件写入失败也会标记，不能据空日志声称没有异常。

旧 API 明确传入 `limit_cny` 时仍需 `confirmed=true`，旧 CLI 的 `--budget-ledger` 保留为可选保护；它们仍限定旧价格表支持的模型。旧浏览器待恢复请求按原内容恢复，不静默去掉已选择的上限。

取消／超时只终止该执行的进程组；宽限期后清理仍在运行的子进程，保留工件。未确认响应会按 Task 在当前浏览器会话保存原请求；刷新先查询执行记录，“核对／恢复原运行请求”使用原 key 与固定内容。页面不自动重发 POST。已执行 Job 不复用，重新捕获准备创建新的 Job。

## 与 Agent 对话（工作页）

一圈的分工：**分析 Agent** 读错题、提假设卡草稿 → **你**在“更改”页批准 → 从卡片新建 **实现 Agent** 改 SN → 你在“更改”页用同一计划重跑 → 在卡片上“核对预测” → 你写效果和原因两个结论。

1. 打开任务，工作页上方是任务卡（目标、run 卡、SN 改动、待读题、待处理的假设卡），下方是与当前 Agent 的对话。没有 Agent 时点“新建分析 Agent”，或点输入框上的 Agent 按钮按模型新建。分析 Agent 的工作目录是评测仓库，对 SN 只读；它用 `scripts/sn-eval` 自己查运行、案例、翻转和文档，用 `hypothesis-draft` 提交假设卡草稿。
2. 草稿出现在“更改”页的“假设卡”里：读组件、机制、证据、逐题预测和交给实现者的说明，批准或驳回。批准后在卡片上按模型新建实现 Agent（需要 Task 已绑定 SN worktree），界面自动切到与它的对话；它只拿到卡片的实现说明，看不到考题和 gold。
3. 改完后在“更改”页运行同一计划，得到新 run；在卡片上选基线 run 和改动后 run，点“核对预测”，再选效果与原因结论保存。
4. 输入后按 Enter 发送（Shift+Enter 换行）。Agent 运行时页面每 3 秒刷新对话，回复自动出现；Agent 运行中再发送会打断它当前这一轮。
5. **Agent 只收到你发送的文字和输入框里列出的附件，以及它自己用工具读到的内容**。每个 Agent 的第一条消息自动附上它的工作说明；分析 Agent 在任务有 run 时还会附上“本任务的评测运行”摘要。已送达过的相同内容不再附加，附件都可以移除。实现 Agent 只能收到说明和仓库文件，服务端拒绝给它发案例、运行摘要和成绩。附加材料：点“材料”，选任务目标、成绩摘要、当前案例或仓库内文件，先读预览（就是 Agent 收到的原文）再附加；或在 run 案例证据页点“附加到 Agent 消息”。
6. run 案例附件包含问题、SN 答案、逐题官方分、阶段指标、gold、引用、按排名的检索块（标注是否引用、是否含 gold）和 run 工件路径，标为“仅供分析”。论文全文不内联，Agent 可按给出的路径自行读取。gold 只交给分析 Agent，SN 生成进程从不读取。
7. Agent 的回答是候选。需要留存时在回答下点“记为候选”，到“记录”页做人工判断；候选不改官方分。

测试时请用 pi 配置（`pi-flash`，DeepSeek Flash），不要用 Claude；优先用合成 fixture：`WORKBENCH_STATIC_DIRECTORY=<dist> PYTHONPATH=src .venv/bin/python apps/sn-workbench/tests/chat-browser-server.py`，再运行 `node apps/sn-workbench/tests/chat-browser.mjs`。

## 服务与配置

`configs/sn-workbench-local.json` 使用 `mode=notebook`，绑定已存在的独立 SN worktree、artifact store、私有实验模型配置、解释器／依赖要求与官方 scorer。前端不接收任意命令、模型配置或路径。准备读取并捕获源码，运行时复制并核对 runtime 文件 hash；不修改 SN 原树、配置或数据库。

已准备 notebook 通过内容寻址缓存复用（`job_execution.preparation_cache`，缺省为 `<output_root>/preparation-cache`，`false` 关闭）：键为分区语料、准备策略与文档容量、SN 准备路径源码哈希（显式列表见 `src/rag_eval/preparation_cache.py`）、准备相关 Settings、模型服务配置与评测侧准备代码。命中时逐字节核验后复制到本运行的私有数据库与 storage，manifest 记 `prepared_from`；未命中则正常导入并在提问前发布只读条目。原 `seed_from_root`／`seed_guard_patterns` 已退役（配置里保留也不再生效），显式 `seed_runs` 与 `--seed-prepared-run` 保留。

调度相关的可选键（`job_execution` 或 iteration 创建时的 `execution`，后者覆盖前者，只影响调度、不进比较身份）：`parallel`（分区并行数）、`preparation_workers`（分区内同时处理的导入文档数，上传仍逐篇顺序；默认 1，内容不随它变化，不进准备缓存键）、`question_workers`（分区内同时提问数，默认 1 即原顺序路径；Agent 评测与 MultiHop 排名捕获仍顺序执行）、`case_deadline_seconds`／`partition_deadline_seconds`（超时题记 `error` 并带 `failed_phase`，分区照常结束、评分；进程超出分区期限 120 s 后被杀）、`model_concurrency`（本服务器所有分区进程共享的模型请求槽位数：`<output_root>/model-gate` 下登记的一个 System V 信号量集（空闲名额 + 容量），一个目录只有一个全服务器上限；等待者在内核里阻塞、按到达顺序获得槽位，不轮询；某个进程要求的槽位数与当前容量不同时，只在闸门空闲（无人持有或排队）时把容量原子地调到新值，忙时沿用当前容量（分区的 `speed-diagnostics.json` 同时记 `gate_slots` 与 `gate_requested_slots`），两种槽位数永远不会相加；取还都带 `SEM_UNDO`，进程无论怎样退出槽位都由内核归还；`scripts/model_gate_status.py <output_root>/model-gate` 查看当前在途数与排队数）。每个分区结束即官方评分；子进程在监督超时前预留余量停止启动新分区，已完成分区照常评分。`retry_rounds` 只重问失败（`error`／缺失）的题，原运行与各轮重试都保留，`retry-rounds.json` 与 `batch-plan.json` 的 `retries` 写明每题用哪一份；比较时只对这些题取重试运行的官方分。迭代协议冻结补问规则 `case_retry = {max_retries_per_case: N}`（新迭代缺省 1，`0` 关闭；建迭代时定、之后不改），它覆盖服务器的 `retry_rounds`，C0、候选、簇试验、新鲜确认的每次 attempt 都用它；协议里没有这个键的旧迭代不变（有预算为 0，无预算沿用服务器设置）。补问在同一 attempt、同一已准备 notebook 上进行；只有在冻结规则下运行的执行（`batch-plan.json` 的补问记录带 `replaces_failed`），被补问答上的题，原运行的失败答案才不再阻止准入、也不再触发 infra 重试，比较取补问运行的官方分；补问后仍 `error`／缺失才走编排的 infra 重试。没有冻结规则的旧迭代和普通 job 即使服务器设了 `retry_rounds`，准入与 infra 判断也和以前一样只看分区运行本身。预算账本把补问按题次计入 `max_case_runs`：预约时按最坏情况（题数 ×(1+N)）占用；只要执行写出了 `retry-rounds.json`（含中途异常的补问轮），就按其中的实际补问数结算题次（`retry_case_count` 列），与成本是否观测到无关（只按题次计的预算从来观测不到成本）；没有这份记录（被杀、超时、写出前失败）则保留最坏情况。该列在服务启动和每次账本写入时自动迁移，读取不依赖它。`retry-rounds.json` 的 `case_retry` 汇总首答失败数、补问次数、仍失败数和首次失败原因分类（`synthesis_failed`／`case_timeout`／`partition_deadline`／`model_infrastructure`／`capture_failed`／`missing`／`other`），各分区 `cases` 逐题记录补问次数、首次失败和最终评分来源；比较结果的 `retry_summary` 与门判定结果的 `case_retry` 把候选和父节点（含新鲜批次）的补问率并排列出，只作披露，不进入门判定。门看的是首次失败：比较的 `infrastructure` 统计每个 attempt 分区运行里的 `error`／缺失，被补问救回的首次失败照样计入，`infrastructure_failure_increase` 仍可拦下让合成变脆弱的候选——与 S3 之前一致；补问率只报告。题目带 `error` 的分区不再使整个执行失败；只有分区没有走到自身终态（崩溃、被杀、未启动）或评分失败才算执行失败。

速度诊断（只记时间，不改请求、评分和比较身份）：每个分区进程（含重试运行）在运行目录写 `speed-diagnostics.json`——各阶段墙钟（`setup`／`preparation`（含准备缓存锁等待、复制或导入）／`answer`／`scoring`／`reporting`）、准备缓存锁等待，以及每个模型请求的阶段、端点（只留 URL 路径末两段）、三个时刻（开始、拿到闸门名额、在归还名额前结束）和结果（HTTP 状态或异常类名）。执行的 `official-summary.json` 在 `speed` 下汇总：各阶段墙钟的最大值／总和及最慢分区、闸门获取次数与等待总和／最大值／占比、按阶段与端点的延迟分布（p50／p90／p99／max），以及由合并时间线重建的峰值在途数、峰值等待数、闸门满载秒数。同一服务器上同时运行的多个执行共用闸门，用 `scripts/speed_report.py <执行目录>...` 合并查看。

```bash
paseo script start workbench --cwd /home/wabiwabi/silicon-notebook/benchmark-deepeval/.worktrees/benchmark-protocol-correctness
```

也可使用 README 的本地 `serve` 命令。服务沿用 `var/sn-workbench` 的任务和访问码；浏览器测试必须使用独立 state 与无模型 smoke executor。

## 实际验证与边界

2026-10-15 反馈修复的验证见[评测状态](evaluation-status.md)。下面是先前版本的验收记录，预算控件等历史行为已被上方操作流程替代。

- 独立 state 的 Chromium 验证：计划选择、源码准备、零预算禁用、匹配请求的 422 拒绝可恢复、未知请求刷新后同键恢复、成功回执丢失恢复、单次执行与完成状态刷新恢复。测试用 smoke executor，不调用模型。
- 真实 Notebook wrapper 用无凭据的测试输入执行子进程失败路径，保留 batch plan/report、原错误与空预算请求账本；没有通过替身伪造真实生成成功。
- 当前可信环境对已成功的固定 QASPER 单题完成捕获准备、执行规格构造、分区展开和解释器身份核对，启动进程数为 0。
- 已保存 E5 真实单题的答案、实际 evidence 与官方分经现有 RunViews 读取和严格绑定检查，非新增模型实验。

最终相关回归：`PYTHONPATH=src .venv/bin/python -m pytest` 对计划 CLI、Job API／CLI、执行层、Notebook wrapper、RunViews 和配置等 102 项检查全部通过，1 条 Starlette 弃用警告；TypeScript 检查、Expo Web 构建和 `git diff --check` 通过。独立只读审查复核 28 项通过，当前范围 0 Blocking／0 Important。较广回归曾暴露 CLI 局部导入遮蔽问题，以及 CLI 子进程未传 `PYTHONPATH` 的测试命令问题；前者已加反例修复，后者用正式启动方式重跑上述集合，不以失败计数宣称全量通过。

受管 `workbench` 服务已启动并通过健康检查，代理 HTTP 200；Chromium 只读核对当前真实执行配置、QASPER 入口、绑定控件与既有 4 个任务，页面无错误。沿用原任务库和访问码，没有在用户任务库写入测试题单或运行。

界面真实生成到评分已有一次用户运行（n=1）；之后的对话改造没有新增 SN 评测运行，只用 pi 做了两条 Agent 消息 smoke。R1 全闭环、自动改 SN、自动 baseline 翻转和生产多用户部署不在本次完成声明内。执行监督句柄属于当前服务进程，服务崩溃后不按保存的 PID 杀进程或自动重派；运行中请保持该工作台服务。
