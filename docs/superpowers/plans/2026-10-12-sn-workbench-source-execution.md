# 源码绑定到真实评测闭环

2026-10-12。用户接受UI01当前界面，授权继续小步实现、独立审及后续真实评测闭环。本记录保存当前范围和证据，不预定永久调度策略、尝试次数或模型预算。

## 当前接续与模型安排

2026-10-13当前（Fable 5.1 接手，最新）：用户要求尽快跑通完整流程、允许省略小步评审并指名 Fable 接手；接手时 `paseo ls` 显示旧统筹 6176、原作者 7358、各 scope/reviewer 均 idle，仅本 agent running，未重复派工。E5 S3/S4 已在 `e5-s3-real-run-20261013/` 完成一次真实链路：(1) 零网络 preflight 用真实 `notebook_runner.execute` 走 captured 启动路径并在 `predictions` 前抛出停止，exit 0／6.3 秒，观测 strict=true、实验配置下 37/37 binding、3 服务凭据变量名、`startup_verified`、dotenv snapshot captured；(2) 由该观测加凭据存在布尔与 TCP 可达布尔生成首个真实 `ready` receipt；(3) attempt-1 通过交付版 R1 owner helper（hash `68b107b8…`，与 final-hashes 一致）启动显式 captured CLI，显式 `--readiness-receipt`、network guard 关闭、去代理变量，pidfd `CLD_EXITED/0`、drain complete、墙钟 17.5 秒，run `finished`；答案 `success` 保存，evidence snapshot complete，20 次 scheduled 调用全部 completed、配对 20/20（embedding 15／chat 5／rerank 0）；诊断 token F1 0.667／context F1 0.077／citation 1.0 标 adapted/diagnostic；(4) 按 `e5-parent-score-handoff` 模板独立 `export-sn`→`score`：answer_f1 0.6667、evidence_f1 1.0、subset、planned 1／success 1、pending 空；(5) `load_run` 离线回访通过。配置来源：原 SN TOML（`62e0ca10…`）缺 `gap_consult_query`／`external_evidence_answer`，本次用评测侧私有实验配置（`e5a59629…`，原字节＋两条 binding→`general`），原 SN 文件／`.env`／数据库／HEAD 未改；捕获 ref `81a682e4…` 等于当前 SN HEAD。限制：n=1 仅 chunk，链路证明而非成绩；owner 残留完整性仍 unknown；S2 R1 原5153对应复核未执行，阻断按用户本日指令解除；`runtime/logs/llm.jsonl` 未生成，usage 来自 provider 事件；Job 仍 `executable=false`，UI／preview／user state 未改，无第二次尝试。同日用户与接手人收敛了总体目标、差距与初版规划，见 `docs/goal-and-roadmap.md`；E6/E7 的具体顺序以该规划为准。以下各段为此前时点历史。

2026-10-13当前：E4由root在**保存scheduled-generation journal的run级结构审计与共享消费**范围闭合。Astra/high scope→Pi DeepSeek max纯模块／测试→指定Sol owned独立审已完成，owned审0B／0I／0M、38passed，probe／fixture／编译通过；root共享reader／aggregation／report红灯 **8failed／1passed→9新＋4直接影响旧检查＝13passed（0.28秒）**。原5153只审准确四文件共享delta，0B／0I／0M，实际 **9 integration pytest passed（0.09秒）** 与 **10组独立完整消费者（exit0）** 分开记录，包含九次预期结构拒绝的同一异常对象传播。root实读报告并重新核对4 reviewed＋3 protected准确hash；无重复owned38／E3d14／E3c或旧矩阵。原红灯、通知和hash证据保留，闭合事实见 `coordinator-e4-closure.json`、`e4-integration-review/REVIEW.md`。

2026-10-13当前：委员会已将下一大阶段收敛为 **E5 受控 QASPER 单题 CLI smoke**，先做 S1 安全配置／预算投影，再做 S2 断网彩排与窄直接 CLI owner gate；S3 才允许一次真实单题生成，S4 独立官方文本评分，S5 冷读收口与后续 Job 范围。S1 复用已核 QASPER 完整 bundle 与固定 case，完整论文资料保留；本次可信 bundle index 与 SN capture ref 已在 `e5-preparation/s1-materialized-refs.json` 独立材料化，ref 仍是 `captured_unbound`。model-services 只做脱敏白名单投影：原声明 3 个服务、合法 credential variable 名称和 endpoint hash 已记录；`credential_variable_configured` 只代表声明变量名形状合法，不代表有效环境变量非空、认证成功或 endpoint 可达。effective Settings、strict registry、凭据解析、可达性与调用上界仍未知；catalog 缺失 binding 必须继续拒绝，chunk 不豁免。S2 只证明一次新进程／新 run-dir 的父句柄、wait/exit 归属、取消／超时或句柄丢失时 `unknown`、不重派和答案／评分事实分离；断网彩排的 `project-root` 必须是 evidence-owned synthetic project，不能读原 SN `.env`，同时仍消费本次真实 captured ref、requirements/runtime expectation 和 artifact index；不建设完整 Job claim/CAS/restart，也不沿用 J02 裸 PID 语义。

当前 runner 会在生成后无条件运行同进程确定性 `score_outputs`；E5 若沿用该 CLI，产物中的分数只能标为 SN adapted/diagnostic，官方 QASPER scorer／judge 仍是独立 pending 动作，不引入完全不评分模式。`no_answer`、`error`、`missing` 不统称为 `unscored`，继续由正式 scorer／分母协议决定适用性，不筛成功题。官方 S4 已有只读 handoff `e5-parent-score-handoff/official-score-handoff.json`：export／score 直接消费本次完整 bundle object 与固定 case，官方 answer 文本先行，evidence 缺口按既有 pending 处理；当前未执行 export/scoring。S1/S2 未通过不得调用真实模型；任何真实失败保留原 attempt、答案／错误和 audit，不换题、缩资料、自动重试或将诊断分写成官方成绩。Job 仍 `executable=false`，UI／preview／user state 不改；证据目录为 `apps/sn-workbench/test-results/source-execution-20261012/e5-preparation/`，当前 owner gate brief 已交原 Sol/high 作者，root 继续独占 shared/docs 和后续实际运行。

S2 的下一窄验证边界已单独记录在
`e5-preparation/s2-launch-boundary-brief.md`：root 持有唯一外层 wrapper 的 live
handle/pidfd，namespace init `exec` CLI，并把 wrapper/init/CLI 的退出事实分开；
CLI receipt、pidfd 状态、输出排空和后代观测必须属于同一 run，否则为
`unknown`。owner evidence 与 CLI `--run-dir` 必须是新建且互不嵌套的目录。彩排
继续使用 evidence-owned synthetic project 与零网络 guard，同时独立传入 S1
captured source/ref、requirements、runtime expectation 和 artifact index。该边界
只用于 review 后的 synthetic rehearsal，不把 pid-namespace witness 当作真实
containment 或 readiness 证明。

2026-10-13 已完成一次独立的 evidence-only captured-CLI rehearsal，入口为
`scripts/run_captured_cli_rehearsal.py`（领域实现为
`src/rag_eval/captured_cli_rehearsal.py`）。它实际消费本次 S1 的 bundle/index/SN
ref、公开 requirements 和 runtime expectation，在合成 project-root 中创建互不
嵌套的 owner evidence 与 CLI run 目录，使用 R1 owner helper 观察一个零网络
synthetic child，并分别写入 `state.json`、`outputs.jsonl`、诊断分数和
`official-score-pending.json`。本机实际观察到 `unshare-user-net`、Linux pidfd
`waitid`、正常 exit 0 和完整排空，结果为 `diagnostic_complete`；同时保持
`offline=true`、`evidence_only=true`、`job_executable=false`，官方评分仍为
`pending`。定向测试 7 项通过，完整回执保存在
`apps/sn-workbench/test-results/source-execution-20261012/e5-offline-rehearsal-20261013/`。
这次彩排不导入 SN、不读取原 project 的 `.env` 或 model-services、不访问模型，
也不替代原5153对 R1 修复的对应复核；它证明的是隔离启动和回执分离，不是 S3
真实生成就绪。

同日补入 S3 readiness gate：Astra/high scope 后由 Pi DeepSeek max 只新增
`src/rag_eval/s3_readiness.py` 与 `tests/test_s3_readiness.py`。该纯离线函数复用
E3d `validate_loaded_model_identity`，将声明配置、effective Settings、strict
registry binding gaps、凭据变量名／存在性和服务 reachability 汇总成带 fingerprint
的 `sn-e5-s3-readiness-receipt-v1`。缺 `external_evidence_answer` 或
`gap_consult_query`、strict binding gap、凭据 absent 或服务 unreachable 会
`blocked`；Settings、凭据或可达性未知会保留 `unknown`；只有全部事实满足才
允许 `ready`。44 项 owned 测试与 runtime model/config 相关回归共 61 项通过。
当前真实配置仍应返回 blocked/unknown；此 gate 不读 env 值、不联网、不启进程，
不写 Job/UI/state，也不把诊断分数或离线 rehearsal 转成 ready。

S3b 的 `--readiness-receipt` 为可选参数；省略时 captured CLI 保持原路径，不构成统一强制启动门禁。

S3b 已接入显式 captured CLI：`scripts/run_notebook_benchmarks.py` 在真实子进程
启动前，根据本次不可变 bundle/index、单题 scope 与 SN source manifest 派生期望
身份，再离线校验显式提供的 `sn-e5-s3-readiness-receipt-v1`。指定文件缺失、格式／指纹错误、
状态不是 `ready`、scope／source／index 不匹配都会以稳定类别拒绝；错误输出不含
私有路径、凭据或分数。非 captured 旧 runner 不读取该参数，旧执行语义保持不变。
定向 readiness、CLI、Job API/CLI 与 notebook runtime 回归共 **113 passed**（仅一项
既有 Starlette 弃用警告）；`py_compile` 与 `git diff --check` 通过。该接线仍只
消费离线 receipt，不观测环境、不联网、不启动 SN 或模型；当前已知 registry 缺口
仍使真实 receipt 为 `blocked`／`unknown`，不能据此提升 Job `executable`。

S2 原735已交 evidence-owned helper／witness／synthetic project，三份 owned hash 核对匹配，已只派一次原5153 Sol/high独立审。父最小接受性反例在准确 `owner_driver.py` hash `51058b2a…` 证实：普通 exit7 可归属，但独立 probe 的 SIGCHLD=SIG_IGN 内核回收后，Popen 代填0被误记为 confirmed；预期 unknown 断言失败。该反例已并入原审，真实 CLI 保持阻断；不靠调整宿主handler或只拒该handler代替 external reap 保证。父另有本机 pidfd 原语可行性证据（normal exit7、external reap→ECHILD/unknown、SIGTERM→CLD_KILLED/15，exit0）供后续作者选型，未修在审helper、不证明PID复用／descendant风险消失，不新派scope／review。证据在 `e5-parent-owner-witness/`，原审收到的两份补充在 `e5-owner-review/`；等原审结论再按原归属修复。

原5153一次独立审已完成，结论 **1 Blocking／3 Important／0 Minor**，未宣称S2通过。除B1真实丢失OS status被Popen代填0外，I1为timeout有界排空丢失已观察输出前缀却标记preserved，I2为cancel排空超时逃逸且无result/output回执，I3为proc stat空白切分及仅进程组扫描将活跃同组／新session后代报成clean。原红灯、5/5失败probe、receipt、pidfd可行性与完整限制保留；真实CLI继续阻断。原735已收到同一owned范围修复brief，必须分开维护退出状态、输出前缀/排空完整性、进程组／完整后代观测三类唯一事实，修复证据写新目录；之后仅交原5153对应复核，不建设Job队列或生产owner。

后续 readiness 候选原语仅作边界输入：父 fresh witness `pid_namespace_feasibility.py`（exit0）验证本机 `unshare --user --map-root-user --pid --fork --mount-proc --net --kill-child=KILL` 可提供 namespace PID1、后代新session、提前持有后代 pidfd，以及 init 退出7后后代 pidfd 可确认退出；零网络、无SN/model/config读取。它不改变当前735修复scope，不是owner修复、完整containment、PID复用／重启对账或真实CLI证明。若对应复核后仍只能诚实报告后代完整性unknown，再据此另定最小Linux启动边界，不扩队列或架构。

E4可用入口是完整 `load_run(include_model_events=True)` 的 `scheduled_generation_audit`、aggregation的run cell及report／summary；presence／truncated_tail来自真实parser，includeFalse不读／不审journal，legacy `unfinished_model_calls`保持旧义。pending／截断可读且有说明，结构歧义拒绝但不改outputs／scores／state；新pairs不放case observations、不推测case。`history_exhaustive=false`恒成立：仅审已记录行的配对，整对缺失／未记录调用不可验证；不证明完整历史、HTTP／模型／scorer／Job完成。startup comparison仍原义，后续generation不同仍可能同family，增长journal跨consumer不是原子快照。**E3d原M1报告与结论不改，也不把M1整体关闭**；本切片补其run级审计消费，逐题Dashboard／UI暴露仍未接入。Job保持 `executable=false`，可靠真实owner／服务／生成及独立官方评分仍待后续；本轮不改源码、UI、preview或user state，不调用真实模型。以下“当前／待审／下一E4”段落是各交付时点的历史。

2026-10-13当前：E3d由root在启动registry及scheduled Future代际观测范围闭合。原5153一次独立审 **0 Blocking／0 Important／1 Minor**；root实读报告并重新核对8 reviewed＋4 protected准确hash。reviewer实际 **14 pytest通过**（9 owned＋5 shared／legacy，与作者9／root5分开），独立修正shipped-helper捕获原生consumer、held callback drain及10个完整reader语义拒绝等通过，网络请求0。root原见证失败仍是失败；评审harness失败及输出重用限制保留，不重标历史。M1仅为新scheduled-generation journal缺离线pair／unfinished／completeness审计与逐题Dashboard展示；启动families相同不证明后续调用代际相同。Job仍不可执行，不代表依赖冻结、真实凭据／服务可达性或官方评分完成。准确收口证据 `coordinator-e3d-closure.json`、`e3d-review/REVIEW.md`。

用户本轮分工优先：下一E4小范围由Astra/high只读收敛scheduled-generation audit（pair／unfinished／completeness），继而Pi DeepSeek max实现、独立Codex6.1-sol/high审。不改Job/UI/preview，不做真实模型，root独占shared/docs；不重审E3d／重跑旧矩阵。此处E4是先补journal消费者的局部切片，原路线中的可靠真实runner owner仍待后续，不把只读audit当作进程生命周期完成。以下开发／待审段落按日期保留为历史。

E4只读scope已一次启动：Astra/high `a0758876-d396-4ccf-a02a-9f1a67d188b8`，CLI实际launch为running，模型目录已核 `gpt-6-astra` 支持high；证据／prompt在 `e4-audit-scope/`。仅写独立scope证据，给Pi新纯模块／tests边界及root唯一reader／report接缝，不修改E3d/Job/UI/preview或比较身份，不猜case、不把成对结构当完整执行认证。保留完成通知、一次finish event等待；未提前启动Pi或第二reviewer。root收口时再次核12 hash一致，762原证据字节不变，唯一变化是此前挂起的一次CLI wait完成后填入idle通知文件，详见closure账本；不是历史测试证据重写。

E4 scope与Pi owned交付现已完成，指定Sol独立审报告 `e4-review/REVIEW.md` 为0 Blocking／0 Important／0 Minor，定向38passed及独立probe／scope fixture／编译通过；两个owned与scope brief准确hash经root核对3/3匹配。root已独占接reader／aggregation／report：JSON解析器直接返回rows／presence／truncated_tail，完整load_run新增run级审计；include_model_events=False不读新journal，legacy unfinished计数原义保留。aggregation新projection放run cell、case仅保留旧明确case事件，report增加scheduled配对／pending／tail及非全历史声明；比较身份不变、原outputs/scores不写。新完整consumer **8failed／1passed→9新＋4直接影响旧检查＝13passed（0.28秒）**；保护owned未改，不重跑38／E3d14或旧矩阵。准确4文件共享delta及保护hash在 `e4-integration/final-hashes.json`，后续只审共享消费delta，不重复owned审；共享阶段未闭合，Job／UI／preview／真实模型不推进。

2026-10-13 原统筹 `6176b569-cad6-493a-8938-724fddccac9c` 已通过恢复端点 `127.0.0.1:6767` 实时核对配置为 `gpt-6.1-sol / high` 并接管唯一shared/docs职责；父已确认Astra统筹不再派工，无需唤醒。`original-lead-resumed-20261013.json` 记录8/8 E3ab closure哈希匹配，未重跑无变化历史检查。父已派现有只读scope `/root/e3c_scope_sol61`，不重复scope；E3c从所选解释器及依赖实际加载身份开始，核验位置必须先于repository/ingest。以下网络不可达与Astra统筹段落为历史事实，以本段为当前归属。

2026-10-13 用户要求恢复最初 Agent 与模型组合：简单任务由 Codex 6.1 high 明确范围，再交 Pi DeepSeek V4.1 Flash max；复杂任务由 Codex 6.1 high 开发，另派独立 Codex 6.1 high 评审；较大阶段由 Codex 6 Astra high 与 Claude Opus 5.5 high 讨论现状及长期文档。此前 Astra 开发／评审的交付与证据保留，不因模型切换重做。

原统筹 `6176b569-cad6-493a-8938-724fddccac9c` 已收到接续指令。沙箱期间默认CLI报DAEMON_NOT_RUNNING、显式端点EPERM为历史连接观察，不能用于推断Agent停止；现在原端点已恢复，父完成模型切回和单一共享归属交接。未重开daemon或重复派工。

E3c父派只读scope已交接，原E1作者 `7358345f-3801-4db9-984e-52a4fc5b54c9`（实时核Codex6.1 high、派前idle）复用角色，仅owned新 `runtime_dependency_identity.py`／对应测试。root唯一接configure返回边界、notebook_runner、fresh CLI及offline reader：显式captured CLI同时接受source ref、完整公开依赖声明和所选解释器executable/prefix期待；Settings真实加载后的同进程观测在repository/ingest前完成，进入runtime-identity及manifest引用。声明单独保存run/dependency-requirements.txt并随既有结果包保留，不从E1 ref猜requirements冻结、不改原Job回执。离线核格式/哈希不import当前依赖。身份称startup_verified／observed，origin_file_sha256与实际执行字节分开；当前处于开发待fresh审，不提升Job executable。

本次核对 `coordinator-e3ab-closure.json` 中 8 个源码／测试 hash 均与当前文件一致；这是交付一致性核对，没有重新运行旧测试。接续说明及未发送状态保存在既有证据目录 `resumption-20261013.json` 和 `resume-original-lead-20261013.md`。下一切片仍从解释器／依赖实际绑定开始，接着核实际服务代际、scorer/evaluator 与 owner；Job 保持不可执行。以下 Astra 统筹身份和派工描述为该阶段历史，不表示恢复指令已经送达。

E1 作者交付及 fresh Astra 独立评审均已完成，详见下方证据；捕获 API 仍返回 `captured_unbound`、依赖／配置未捕获。E2 I1/M1 修复已由原 reviewer 窄复核闭合；E3a 输入/源码准备事实和 E3b 私有dotenv默认绑定均已实现、经独立审查修复并由各原 reviewer 窄复核闭合。原Codex6.1 high统筹现在唯一维护共享实现与本记录；Astra统筹交付及下方“待审/待修”段落为历史，完整E3/真实派发仍未就绪。

本次接手统筹为 Codex `de7229a2-6dc4-4a5a-bcb1-0d4e9076f469`（GPT-6 Astra high），父为 `27afbbe2-d92a-4648-855e-db1b9895e849`。E1 fresh 独立评审为 `80affe67-d71b-48b3-aa01-6ba7942dfca0`（GPT-6 Astra high），只读 E1 owned 文件、必要 ArtifactStore/result-package 契约和独立临时反例；评审证据落在 `e1-review-astra/`，完成通知保留。父已明确不重复派审；E1 评审期间只推进 E2 的只读 scope/shared 接缝与独立实现准备。

E1 fresh 评审已于 2026-10-12 完成，结论为无 Blocking/Important/Minor finding；E1 仍为 `captured_unbound`，依赖和配置为 `not_captured`。评审针对回归为 `.venv/bin/python -m pytest -q tests/test_sn_source_capture.py tests/test_artifact_store.py`，**46 passed，exit 0**；独立临时 Git witness、tar/hash/ref、dirty/new/deleted、cache 源包、普通 `token.py`/`credentials.py`/`key.py`、资源、私密排除、链接/FIFO/路径越界/变更屏障均通过。证据在 `apps/sn-workbench/test-results/source-execution-20261012/e1-review-astra/`，包括 `review-report.md`、`hash-ledger.json`、`targeted-regression.txt`、`independent_checks.py`、`independent-checks.json`、`review-failure-record.txt`。E1 owned 当前 hash 与 handoff 一致：`sn_source_capture.py` `9502c5ad64e42f1b8d216719b17ed043a8d700e7b938bcc96fa091998558ebb3`，`test_sn_source_capture.py` `f7258d519441266df3dc17bd05db2ec5fb8c0ac2bacf3c313bce65ee8bd32136`；评审完成通知保留。

已检查本机 Paseo CLI：MCP 不可用时 `paseo run --background` 启动后台任务，`paseo send --no-wait` 发送后立即返回；默认保留 `notifyOnFinish`，因此本阶段不关闭完成通知、不轮询、不催促、不重复派工。接手时 E1 review 在 `paseo ls` 中为 running（已按后续报告闭合），旧统筹 `6176b569-cad6-493a-8938-724fddccac9c` 与旧 E1 作者 `7358345f-3801-4db9-984e-52a4fc5b54c9` 均 idle。

E2 最小接缝已收敛为：从既有 `sn` ArtifactStore 引用安全材料化 `product-source.tar` 到本次 run 私有 source 根；显式传入该根时，fresh loader/import 将其置于 `app` 的 import 优先级，并记录实际 module origin 与字节 hash。原目录变化应不影响捕获字节；仅源码绑定被验证，依赖、配置、模型和完整 runner 隔离继续分别标为未捕获/待后续切片。默认 runner 行为保持不变，E2 接线涉及共享 `runtime_environment`/`notebook_runner`/`run_reader`、新增共享 capture 身份校验 `source_identity` 及窄测试。

E2 fresh Astra high 评审 `856a11df-263b-42ac-ac9c-0fd40aa76c20` 已完成，发现 I1（reader 允许 capture 缺实际清单）和 M1（材料化目标祖先 symlink 可重定向写入），原报告在 `e2-review-astra/review-report.md`。统筹已局部修复：新增共享 `source_identity.captured_source_manifest` 给 reader/materializer 使用；record/object 均须非空清单、fingerprint 一致且清单相等；材料化在 mkdir 前拒绝目标全部祖先 symlink。E1 两文件未改。`e2-fix/red.txt` 为修复前 5 failed/1 passed，`green.txt` 为相关 25 passed/exit 0。原 reviewer 窄复核现已闭合 I1/M1，无剩余 finding：独立 20 项检查和 7 项对应回归通过，修复 hash 与 E1 hash 核对一致，见 `e2-review-astra/recheck/`。此闭合仅限 E2 既定范围与原问题，不代表完整 runner、依赖、配置、模型或 Job 冻结。

## 本轮目标与最小反证

2026-10-12 接续 E3a scope（E2 修复复核期间只读收敛）：固定 Job 的 `plan_snapshot` 是本次意图唯一输入，不读取当前 plan head。先绑定既有 ArtifactStore 的 canonical bundle/index 与已捕获 sn ref；完整 canonical 重建及选中 partition capsule 校验不能缩成题目列表。新准备事实暂存既有 history/requests 的不可变记录，不改 J01 的原始登记/取消回执，不新增工件库或调度表。材料化成功仅证明确有这些字节，不声称实际 runner 加载。

首反证是：固定 Job 后更换 canonical bundle/index 或取消 Job，准备不得变成可执行或越过版本检查。昂贵校验在写事务外，提交事务内重新读取同一 Job/version；过期拒绝且不写事件。runtime、dependencies、model、scorer、evaluator 实际加载以及真实 owner 仍缺绑定，首切片 `executable=false`。SN 的 model-services watcher 会热更新，Settings 相对路径依赖源码根；现有 CLI 尚未传入 `sn_source_ref`，因此不能从 E2 单模块验证推导完整执行就绪。E3a owned brief 位于本机证据目录的 `e3-implementation-brief.md`，待 E2 原 reviewer 窄复核闭合后交 Astra high 开发。`e3-scope-probe.json` 记录隔离临时 state 的实际验证：单题选择保留所在分区全部两题/一份材料，追加新 plan head 不改变旧 Job 固定修订；命令 exit 0，不涉及用户 state 或 SN/model。

先做E1/E2：捕获所选SN工作树中与运行有关的实际源码，包括tracked dirty和新增文件；fresh进程从捕获内容加载，以实际module origin及字节hash核对身份。原目录随后变化不能影响这次加载。源码捕获不等于完整依赖、配置或模型环境冻结。

root已实读SN `/home/wabiwabi/silicon-notebook/project/AGENTS.md`、当前 `runtime_environment.py`、`notebook_runner.execute` 和 `ArtifactStore`：现有snapshot拒绝tracked dirty并归档HEAD；runner随后把原project/backend加入sys.path，Settings及配置仍来自原project。因此归档存在不能证明加载绑定。复用既有共享artifact store及SN/evaluator对象，不另建工件库或状态账本；不以删除dirty拒绝而继续加载mutable源来凑可运行。

优先反证是：临时源含已改tracked文件和新增可加载模块，捕获后改变原目录，fresh进程仍加载捕获字节；错误来源、缓存或路径遮蔽不能伪装为匹配。捕获过程变化、symlink及未纳入依赖如何拒绝/披露，由scope依据实际接缝收敛。验证使用隔离临时样例与必要只读真实源码，不启动模型或读取私密配置正文。

## 路线及本轮归属

| 切片 | 可检查的交付 |
| --- | --- |
| E1 | 所选工作树实际源码捕获、内容身份与可读来源；保留原目录及已有改动 |
| E2 | fresh进程加载捕获内容；实际加载身份与记录绑定，evaluator/配置/依赖分别说明 |
| E3 | 固定Job revision绑定完整input/code/model/runtime/scorer；不足明确未准备 |
| E4 | 保存scheduled-generation journal的run级结构审计与reader／aggregation／report消费；不证明完整历史 |
| E5 | 受控QASPER单题CLI smoke：S1安全投影、S2窄直接CLI owner gate、S3生成并保存答案；诊断评分与官方评分分开 |
| E6 | 独立官方文本评分、不可变import回流同一Task，Web与CLI共用操作 |
| E7 | 可靠真实owner／Job claim-CAS-取消恢复及服务重启对账，再与SN基线／修改diff／固定题单关联比较效果与原因 |
| E8 | 按真实需求增量调查及报告 |

父已启动只读Codex6.1 high scope：E01 `25620b9c-6f66-4942-9801-b970e1653ed4`（源码捕获/加载），E04 `fbdff822-6754-4a5f-b1b5-e816ed8bb0ec`（真实进程/恢复）。两者不写代码、不测试、不委派，完成直接交brief；root不重复scope，不poll或催促。

两份brief已收到。首交付E1仅新增 `sn_source_capture.py` 与 `test_sn_source_capture.py`，由Codex6.1 high `7358345f-3801-4db9-984e-52a4fc5b54c9` 实施，随后fresh独立审；新API捕获实际字节及修改/新增/删除事实，复用sn对象的product-source.tar形态，返回captured_unbound。排除规则不得把token.py等普通源码名称当秘密而漏包；失败不发布有效ref。实现brief及启动通知存本机证据目录。E1审闭合后再交E2 materialize/fresh-load模块，不把捕获完成冒称加载绑定。

root只读实际源码补充：SN `backend/app/core/cache/{__init__,policy,backend,sqlite_backend}.py` 是合法包，不能按任意路径段cache/runtime/storage名称排除源码；已交实现者作为反例。`app/__init__.py`及`app/core/__init__.py`为空、`app/core/text_whitespace.py`仅声明常量，可作为后续安全实际加载的候选；当前只完成静态阅读，未import，不能据此宣称SN加载验证通过。

最多3线。root唯一修改共享runtime_environment/notebook_runner/核心、迁移、API/CLI/contracts/App和当前/长期文档；worker仅写约定owned新模块，不共改shared。大阶段实际可用才做Astra/Opus体验及架构回看，不逐模块开committee。

E04只读brief已收敛责任：Job store持有意图/claim/cancel及回执，owner持有实际句柄与owner_instance/generation，run工件持有冻结身份/outputs/scoring事实。后续CAS拒绝迟到旧owner；cancel intent、进程exit、generation output commit、scoring及terminal receipt分开。无可靠句柄或代际/终态证据则unknown/handle_lost，不凭裸PID控制、不重派。暂不预选pidfd或常驻supervisor，不新增runner adapter；先交E1/E2。

## 执行与证据边界

UI01布局和受管预览保持，不重开A/B/C/J02审查。J02数字PID代际局限仍保留；E4不能继承替身通过就宣称真实进程可靠。

本机SN简单smoke授权有效：先完成源码加载与独立数据/runtime隔离，再以已有可用配置单题/单请求起步；预算或模型配置有实质未知时从来源安全判断后交父压缩询问。当前不启动SN/model/judge或五套full。选一套样例不限制通用Task及五套适配。后续SN修改另成切片，本轮不改SN源码、原数据库或生产数据，不输出.env/密钥。

保留dirty/untracked，不commit/push/stash/reset/clean，不重启主Paseo。证据存本机忽略目录 `apps/sn-workbench/test-results/source-execution-20261012/`，保存命令退出、hash manifest及完成通知；源码快照在证据目录使用非.ts/.tsx扩展或归档，避免被前端tsc误纳入。/tmp仅作工作区，不作唯一证据位置。仅运行与变更有关、能推翻前提的检查，不重跑已通过且未变的历史矩阵。

## E3a 准备事实与 E3b 配置绑定

Astra high owned worker `2ce8c97f-c2e9-4616-b73b-2c25c0fb81cb` 实施 `workbench/job_preparation.py` 与 `test_workbench_job_preparation.py`，统筹唯一集成 Job 接缝与文档。使用现有 workspace、`paseo run --background`，完成通知保留。原 E2 reviewer 以一次 `paseo wait` 取得已 idle 的完成回执并实读 recheck 报告，未轮询/催促/重复派审。E3a 只绑定输入/源码准备事实，尚未形成可执行 Job。

统筹 shared 接缝为 `EvaluationJobStore.capture_inputs_source/preparation_receipt/preparations`，新增 owned 集成测试 `test_workbench_job_preparation_records.py`；既有 history 保存 Job snapshot + preparation + actor/time，requests 保存原命令回执，Job record/J02 不修改。首次集成红灯为缺方法的 AttributeError，保存在 `e3-integration/red.txt`；修改所属 Job 模块既有回归 `test_workbench_evaluation_jobs.py` **223 passed/exit 0**，44.30 秒，不再重复该无变化集合。builder 完成后的联调与独立审仍待执行。

接续联调已使用实际 builder：取消竞争、离线历史回执、损坏 payload 三项首先通过。统筹新增“另一份有效 source preparation 替换原回执”的独立反例，replay/receipt 两分支修前均失败，保留 `e3-integration/receipt-binding-red.txt`；现由同一回执校验边界重建命令并与 requests 原 digest 对照，相关集成 **5 passed/exit 0**，`e3-integration/green.txt`。该摘要不代表 owned 作者交付或 fresh 独立审已完成；评审 brief 已准备，待交付后派一次 fresh Astra。

E3b 最小反证（只读 scope，不改 E3a 在审接口）：实际 SN 注册器 `_effective_environ` 从 `settings.model_config['env_file']` 再读 dotenv，当前 configure 只向实例构造传 `_env_file=project/.env`。使用已安装 Pydantic、合成 Settings/临时 marker 文件及从真实源码 AST 提取的原 helper，证实实例读到显式 marker 而注册器读不到；`e3b-config-scope-probe.py/.json` exit 0，保存原 helper 所属源码 hash。未 import SN/读取真实配置。下一小切片需让 Settings 与注册器指向同一私有配置快照，再检查实际生效身份；复制 service TOML/hash 本身不能证明模型服务或凭据解析绑定。依赖版本目前只读观察，不声称环境冻结。

E3a 作者已交付，owned 当前 hash 与 `e3-dev/hash-ledger.json` 一致；作者最终 42 owned + 5 root 集成 = **47 passed/exit 0**，不是统筹重跑。作者沙箱无法发 CLI 消息，完成回执已持久保存在 `e3-dev/completion-notice.md`，统筹已实读。fresh Astra high reviewer `bdcc2785-0eb1-47d6-aa8b-46326886ba3e` 已通过 `run --background` 启动，scope 为两个新 owned 文件及 root E3a shared 接缝/测试，完成通知保留，证据目标 `e3-review-astra/`。旧 E2 reviewer 不重复派。

E3a 审查期间统筹仅推进独立 E3b config 接缝：captured-source 分支在 import Settings 前将可用 dotenv 原字节保存为本 run 私有 0600 文件，将 `SILICON_NOTEBOOK_ENV_FILE` 与显式 `_env_file` 指向同一快照。缺失文件记录 absent；原 SN .env 不写入 ArtifactStore/公共回执。验证使用合成无秘密配置、fresh 子进程及原目录随后变更反例。公开身份只保存 hash/状态，环境变量、请求实际服务、依赖/scorer 继续未绑定；不会提升 E3a readiness。root owned `runtime_environment.py` 与新增窄测试，不修改 E3a 在审文件。此为实施范围，不预先声称完成。

E3b 实现及本轮验证已落盘：`test_runtime_config_snapshot.py` 两项新检查 + E2 相关 source-loading 检查 **11 passed/exit 0**；修前两项缺 snapshot 失败及先前测试脚本引号错误分别保留在 `e3b-dev/`。fresh Astra reviewer `00858ab6-fcbb-4a51-81e6-56a8f350c886` 独立只审 E3b delta，完成通知保留，不重审 E3a/E2。经静态副作用检查后，`e3b-dev/real-config-witness.py/.json` 使用 E1 捕获真实 SN 的 5 个 Settings/registry/依赖叶模块，在 fresh 子进程实际核 origin/hash；合成配置原文件后改仍不改变默认 Settings 与实际 `_effective_environ` 所见，exit 0。没有构造模型服务 registry/客户端、调用模型或读取实际私密配置。依赖 scope 仅通过 metadata 检查当前评测 venv 对 SN 声明 32 项 requirements 的版本均满足，`e3c-dependency-scope.json`；不等于依赖字节/环境冻结。

E3a fresh review 已完成并实读 `e3-review-astra/review.md`：无 Blocking/Important，保留 M1（JSON 数组工件使底层 reader 的 AttributeError 逃逸准备失败边界），47 scoped passed，独立 5 passed/2 failed（两失败均 M1），无错误历史/回执写入。已交原 Astra 作者仅修 owned 外部 reader 边界及窄测试，证据 `e3-fix/`，之后原 reviewer 复核；尚未把 E3a 标闭合。reviewer CLI 消息因沙箱未送达，完成 notice 已保留，统筹通过一次 wait 得到 idle 回执并读取证据，不重复派审。

后续实际 model 绑定的只读约束：SN `repository_runtime` 持有 `models`，provider 的服务 runtime 按 `(service.id, service.fingerprint)` 保存代际，registry 提供 `service_for/workloads_for/thinking_mode_for`；已有 provider observation sink 主要覆盖失败/恢复，不是每个成功请求的完整身份事件。评测 `usage_capture` 当前也只摘录 kind/model/status/latency/usage/support_id。因此不能从模型名日志或启动时 registry 快照推导每次请求绑定；下一小切片需在实际提交/完成边界核对所用服务代际，复用现有 runner/log 生命周期，不另建 adapter/队列。这是源码观察与待验证范围，未实现、未调用真实模型。

E3a M1 作者已交付局部 reader 异常归一，未扩大 builder 总异常捕获：仅两处外部 canonical reader 中 JSON 非对象缺 `.get` 的 AttributeError 转领域失败，无关 AttributeError/SQL/材料化异常原样保留。`e3-fix/` 保留 6 failed/6 passed → **13 passed/exit 0** 与 owned 前后 hash；已核当前 hash 并交原 reviewer `bdcc2785…` 窄复核。

E3b fresh review 已完成：0 Blocking/1 Important/0 Minor，详见 `e3b-review-astra/report.md`。独立反例证明 captured Settings 可忽略 import-time dotenv override、仍接受实例 `_env_file`，导致实例与registry读不同文件而返回成功。root已在显式source入口检查类与构造后实例的默认dotenv配置，只接受唯一私有snapshot路径，absent/disabled要求没有活动默认文件；不匹配在写成功identity前拒绝。`e3b-fix/` 保存修前 **2 failed/2 passed** → **4 passed/exit 0**、前后hash/快照；原 reviewer `00858ab6…` 正在对应窄复核。两切片当前均不能提前称评审闭合。

E3a 原 reviewer M1 窄复核现已闭合，报告实读 `e3-review-astra/recheck/review.md`：原定向 **13 passed**，新增独立边界 **6 passed**，两原失败反例转绿、无关异常仍保持原对象，owned/shared/原反例 hash 均按账本核对。无新 finding，不重跑47/223，不包含 E3b。E3a 闭合仅意味着固定 Job 的 input/source 准备事实可验证与持久回访，依然不可执行。E3b 修后真实五模块配置见证再次 exit 0，`e3b-fix/real-config-witness.json`，原 reviewer 的配置绑定窄复核仍待结论。

E3b 首次窄复核 `recheck/report.md` 保留同项 Important：静态错误默认已拒绝，但构造期间可保留实例配置同时改变类默认，导致下一次默认构造读另一文件。此处重新确认返回边界须同时保证类和实例两个消费者；root保留构造前校验，并在构造后用同一helper再次校验类、再校验实例，不引入新模式。`e3b-fix2/` 原独立类漂移present/absent **2 failed/4 passed** → **6 passed/exit 0**，拒绝必须为精确binding错误且不生成成功identity；已交原 reviewer `00858ab6…` 仅复核该剩余边界到 `recheck2/`。不把此次修复当作任意未来类/环境突变防护。

E3b 最终窄复核现已闭合，报告已实读 `e3b-review-astra/recheck2/report.md`：**6 项产品检查、10 个独立fresh子进程场景通过，exit 0**，原Important及同步class-drift边界均关闭，无剩余finding。兼容present/absent/disabled保持正确，类/实例同步漂移拒绝且不写成功identity；原审和首次复核失败证据字节未变。统筹再次核当前runtime/test hash与fix2及review账本一致，E1原两hash仍一致。各完成notice保留，无同scope重复fresh审。

本次交付可用范围：固定 Job revision 的完整canonical输入/source准备事实及可离线回访的回执；显式captured-source运行分支的私有dotenv文件与Settings类/实例默认绑定。准备记录仍 `executable=false`，没有真实Job派发、provider调用或用户state/preview更新。下一切片从已定位的依赖解释器/实际模型服务代际与scorer身份入口继续，真实owner代际另核；当前不做大阶段委员会或预览发布。


## E3c 当前消费接线（2026-10-13，待 fresh 独立审）

原 E1 Codex6.1 high 作者 `7358345f…` 只写新 `runtime_dependency_identity.py` 和对应测试；统筹唯一接入 configure/runner/reader/CLI 与结果包 allowlist。公开 requirements 是独立输入时间点，保存到 run 根 `dependency-requirements.txt`，不冒称 E1 同次捕获依赖。真正 configure 在 Settings 已加载、repository/ingest 尚未开始时核所选 executable/prefix、全部有效直接声明和已加载关键模块的distribution文件归属；观测进入既有runtime identity，run manifest引用观测和声明hash。新CLI同时要求 `--sn-source-ref`、`--dependency-requirements`、`--runtime-expectation` 和固定共享index；旧默认入口语义不自动改变。

统筹实际consumer检查 `e3c-integration/consumer-check.log` 为 **8 passed/exit 0**：fresh CLI的合成captured repository哨兵只在成功观测已写入/引用后到达；错误venv在repository前失败；原声明/source后来变化不影响保存记录；offline reader不调用observer且声明tamper拒绝；results/review包保留声明而排除private runtime。此前缺接缝 **3 failed/1 passed** 的红灯保留。worker仍待正式完成通知，准确hash及fresh审后才可闭合；这不是模型或生产SN执行。

`origin_file_sha256` 是观测时磁盘文件hash，与实际执行字节／旧pyc分开。观测称 startup_verified/observed，extras/transitive/native动态链接、后续lazy import/并发变化、模型/provider/scorer/evaluator/owner仍未验证；Job仍不可执行。raw observation含时间和本地路径，当前精确引用不能直接当跨run环境等价凭据；后续比较身份需依据实际事实收敛，不能删除字段来假称可比。UI01预览不改。父并行原E04仅核本机smoke现成材料到独立证据目录，本线不重复、不等旁路结果。

2026-10-13父补充当前执行分解：生成与官方评分是独立动作。真实Job可执行提升须该所选动作所需input/source/runtime/model/可靠owner成立；scorer尚未准备不应成为单题生成的统一前置，答案先保存、官方评分明确pending，评分动作另核scorer/judge身份。上述E3路线是增量方向，不是每步一次完成所有冻结与功能。等原E04本机smoke材料brief后据现有资源收敛下一切片，E3c不等待旁路scope、不启动真实SN。

原E04本机材料brief已收到：推荐复用既定QASPER历史selection `qasper:397a1e851aab41c455c2b284f5e4947500d797f0`，canonical `qasper-v3` 完整load_bundle核验成功；该partition有4题，显式只选一题仍保留38,279 UTF-8字节的完整论文。所选本次可信index/SN ref尚待准备，不从可变pointer临时取期待pin。官方QASPER文本scorer脚本hash匹配已声明资源，评分独立后置、不发judge。本次材料核对不是真实生成或评分。

预算仍有实质未知：完整论文导入会有条件性summary/metadata和embedding；query/rerank/Ask及重试也可能增加调用，一次Ask不等于一条HTTP。model-services仅stat，未读正文／证明工作负载或可达性。下一小切片先依据安全的实际有效工作负载观测核请求代际与调用边界；用户原授权是本机“简单测试”，阶段文字“单题／单请求”不能反推用户要求只有一条HTTP；实际配置可知后记录一次Ask与完整论文必要导入／embedding／metadata的范围与估算，不宣称调用上界1。复用现成canonical和store，不新增数据／安装或把该样例限定为通用Task架构。

父进一步确认下一切片：E3c独立审闭合后，优先真实所选model workload／服务代际／有效配置安全观测，并结合最小生成路径收敛owner。可读取配置做明确白名单投影（workload、模型名、timeout/retry/batch参数、凭据变量名的配置存在性、endpoint仅hash），不输出服务地址／凭据正文，不读打印.env／密钥；不先调用模型探可用性。官方文本评分后置，现成资源不重复scope，不扩reasoning/judge/full或自动重试实验。剩余重要预算／模型取舍无法从公开事实判断时，才给父精简具体问题；不重复整体授权。

E3c原作者正式交付已实读，两个owned hash与 `e3c-dev/final-hashes.json` **2/2一致**，作者完整owned验证 **42 passed/exit 0**。统筹在准确交付版上再核consumer+直接改变的旧默认runner一项 **9 passed/exit 0**（4.76秒），未重复作者42或旧历史矩阵；三次root消费者日志按实际改动/交付时点分别保留。fresh Codex6.1 high reviewer `5153d070-74dd-42a6-a003-4c19f24d06bd` 已启动，只读8文件E3c scope，证据 `e3c-review/`，完成通知保留并以单次wait等待event，不poll/催/重复派审。此处仍待审，不宣称E3c闭合。

E3c fresh 5153审已实读，**0 Blocking/2 Important/0 Minor**；当前不闭合。I1是保存declaration原字节仍有声明时，观测删一项direct check或native模块且全部hash重封仍被完整load_run接受；I2是原始观测含时间/run坐标使同条件cross-mode/family失配。原51green、独立5pass/3fail及完整reader失败不改。root完整reader新反例修前 **1 failed/1 passed**；I1消费边界已补host-neutral parse entry/hash/size对应与默认5模块集合，随后相同完整反例通过遗漏检查、到I2真实aggregation反例仍 **1 failed/1 passed**，两日志分别保留。

原735只新增离线比较投影owned模块/测试，root维护reader/runner/aggregation/paired report接线。环境投影保留解释器/ABI/flags/venv、完整声明、marker事实、实际module/缓存/RECORD字节与归属和ordered sys.path；只归一明确captured-backend与cwd两种run坐标、移除观测时间/原fingerprint。manifest新增版本化startup binding存raw observation与declaration精确hash、稳定environment digest及原run坐标；reader离线重新验证并导出comparison_identity/comparison_pairing_id，raw manifest identity/protocol/pairing保持精确审计原义。既有aggregation/dashboard/paired report消费同一派生比较身份；不关闭比较、不删除整份来源。保留源/input/service/scorer身份的原精确检查。原run坐标使搬移后的离线对账不依赖当前host；不是环境冻结/第二账本，未绑定lazy imports/native链接或模型owner。尚待准确owned交付与原5153对应复核，不新派同scope审，不重跑原51矩阵。

E3c修复交付已按准确文件核对：原735新增projection模块／测试2/2匹配，原identity模块／测试hash不变。root实际运行新的完整consumer及两项直接关联的旧默认runner／dashboard检查 **10 passed/exit 0，6.65秒**；I1两种完整load_run重封遗漏拒绝，两个真实fresh合成CLI运行经aggregate／paired report／实际dashboard可比，不同模块字节事实仍分组。没有真实SN／模型调用。`e3c-fix-integration/final-hashes.json` 包含12个源码／测试，保存修前快照／diff／命令退出；原红灯与原51green不重写、不重复。原5153已收到窄对应复核brief，一次event wait已挂起，完成通知保留；在其结论前不宣称E3c闭合。

E3d准备期间父补充真实consumer约束：registry在解析services／bindings后对全部catalog做binding_gap，disabled feature无豁免；chunk不自动免除安全投影所见两缺项。strict=true只是公开Settings默认，尚未观测私有配置／环境后的有效值；其他解析／凭据错误可能更早，不能认定缺binding是第一个实际运行错误。正常provider先加载registry再启动watcher；传显式registry会改变ownership并禁用watcher，不能用该测试代表normal startup/reload。invalid reload保留旧registry，file hash／metadata不代表实际提交所选代际；已有service.id+fingerprint持有旧call runtime。下一切片区分declared file／effective Settings／loaded registry／actual call generation，复用已有private model-services副本与model-events工件，不新增store。先用无网络合成配置验证正常消费者；若需实验专用配置，记录独立来源、不改原SN文件、不借缺项停止全部开发或豁免strict。E3c仍待原5153窄对应结论。

E3c原5153对应复核已实读并由父接受：I1／I2关闭，修复范围无剩余Blocking／Important／Minor。reviewer实际新／改pytest **10 passed/exit 0** 与独立 **7组场景通过/exit 0** 分开记数；完整load_run重封遗漏、实际aggregation／report／dashboard可比和venv／byte／外来路径拆分、原坐标搬移及保存marker真值验证通过。原42／51与旧矩阵未重跑。统筹重新核当前 **12/12准确hash匹配** 并记录 `coordinator-e3c-closure.json`，在startup-observation范围闭合；原失败／1419证据文件保留。不是依赖冻结、执行字节证明、完整模型／owner就绪或Job executable提升。

E3d首证伪已实际运行，`e3d-config-preparation/normal-consumer.json`：捕获真实SN源码，fresh子进程、仅合成private dotenv／TOML、正常provider构造（未注入registry）与内存raw transports。有效strict=true拒缺bindings；normal watcher线程确实启动；invalid forced reload保留旧registry，valid reload使新调用转新代、已提交旧调用仍用旧代，**exit 0，网络请求0**。所有实际导入app模块origin/hash核对capture；未读真实.env／凭据／调用模型／数据库。只验证正常构造和原forced reload consumer，自动watcher tick时序未测。此证据是下一实施输入，不代表真实本机服务可用。后续实现复用private配置／provider和既有model-events，独立source/config/actual runtime事实；生成与官方评分仍分阶段。

E3d正式进入实施：原735 Codex6.1 high已收到bounded owned `runtime_model_identity.py`／`test_runtime_model_identity.py`，一次完成event wait挂起；无重复scope/委员会。root唯一接 `initialize_captured_model_services`、runner/reader及窄consumer测试。实际正常provider不注入registry，在repository前加载并记录declared digest／effective Settings／actual loaded registry／captured native模块origin/hash；通过既有model_provider seam复用该对象。实例级提交观测写既有model-events，完成回调持原runtime身份；Future terminal不代表HTTP上界、scoring或Job完成。关闭provider排空Futures先于observer和journal关闭，默认历史runner保持原义。root bootstrap缺入口 **2 failed/exit1** 和fresh consumer缺接线 **1 failed/1 passed/exit1** 保存在 `e3d-integration/`，owned模块未正式交付前不反复跑缺模块红灯。正式准确revision随后只派一次独立Sol/high审，不提升Job可执行／调用真实服务或更新UI。

E3d原735正式交付已实读：两个owned准确hash与handoff／final manifest一致，作者 **9 passed/exit0**；root共享及fresh CLI消费窄验证 **5 passed/exit0（6.55秒）**，包括bootstrap完整／缺binding、真实fresh CLI／错误venv和旧默认clarification／pairing检查，不把5项全部归为单个bootstrap文件。合成priority fixture由对象改为原生字符串形式，不修改生产行为。root准确8文件manifest及保护文件hash在 `e3d-integration/final-hashes.json`，已有红灯保留。更新的shipped-helper原生见证 **exit1**：harness材料化位置不在该helper要求的run/captured-source，source containment拒绝；原stderr／capture／命令保留，不当作通过或真实SN证据。原5153 Sol/high已一次接收此准确版本进行独立只读E3d审，目标 `e3d-review/`；复用原角色、完成通知保留、一次event wait，不重复scope／旧矩阵。评审前E3d仍未闭合，Job仍不可执行，原树／真实服务／UI01／用户state不变。
