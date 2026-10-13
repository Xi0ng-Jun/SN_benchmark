# SN 工作台：本地开发切片

2026-10-11：已有真实可运行工作面。进入 workspace，创建持久任务，显式接入结果，读完整成绩与案例，保存人评／调查笔记，再回访修订历史。A 阶段新增 Agent 关联／创建、会话读取、上下文选择、来源候选和显式消息发送。实际评审与验收见[阶段记录](../../docs/superpowers/plans/2026-10-11-sn-workbench-agent-collaboration.md)。这里使用真实历史工件，设计原型仍是另一份构造数据页面。

## 查看与启动

workspace 的 Paseo `workbench` 服务由 Scripts 管理，当前是否运行以 Scripts 状态为准。2026-10-12 已更新 UI01 工作面预览并启动受管服务，既有四任务及原三份结果内容保留。首次访问需输入 `var/sn-workbench/access-token` 中的访问码；启动日志不输出该值。若使用访问链接，访问码放 URL fragment，经同源请求换为 HttpOnly cookie，随后从地址栏移除；不要把完整访问链接纳入版本控制或公开记录。

本机代理入口（需运行 Paseo）：
`http://workbench--feat-benchmark-protocol-correctness--bench--16ca78a8.localhost:6767`

从仓库根目录安装和构建：

```bash
.venv/bin/python -m pip install -e '.[workbench]'
npm --prefix apps/sn-workbench ci
npm --prefix apps/sn-workbench run build
PYTHONPATH=src .venv/bin/python -m rag_eval.workbench.cli --state var/sn-workbench serve --config configs/sn-workbench-local.json
```

直接启动默认在 `http://127.0.0.1:8091`；Paseo `paseo.json` 使用受管理端口。后端同时提供静态前端和 API，构建后重开页面即可；Python 改动需重启该服务。启动日志不再输出访问码或完整认证 URL。更新消息功能版本时须关闭或刷新所有旧工作台页面，旧 bundle 不具备本版的消息写入锁。`npm run dev` 是前端开发服务器，当前没有单独配置跨端口 API 转发，完整工作路径使用上述同源服务。

## 走通一次工作路径

1. 打开任务，默认进入“工作”：上方任务卡，下方与当前 Agent 的对话。最近关联的 Agent 自动选中并读取对话；输入框上的 Agent 按钮可切换、按允许配置新建或关联已有 Agent。Enter 发送，回复自动刷新。“材料”先预览再附加；run 案例证据页也可“附加到 Agent 消息”。
2. “结果”集中已关联结果卡、已登记来源的接入及只读比较。点击某份结果的完整成绩、案例或来源，右栏只解释当前对象。
3. 成绩保留原汇总、题目范围、生成覆盖、逐指标分母和 pending；案例可搜索、翻页、筛有效零分。选中案例后读取答案、参考、材料全文和观测，保存独立人评。gold 只供诊断。
4. “计划”明确选择完整输入和题单，保存草稿、查看固定修订或追加已读取的 head。保存仍不执行 Job。
5. “记录”查看来源候选及人工决定、笔记、人评和任务事件。点击记录打开当时保存的固定内容，也可导出任务 JSON。

底部“工作区设置”（窄屏或左栏收起时在顶部）独立展示服务器已登记的工作区、操作者、Agent 配置与评测输入；外观中可切换浅色／深色。服务器配置当前只读，入口声明不代表远端健康或执行可用。设置不再打开结果资源目录。

纯工作面切换、设置开关与右栏关闭保留当前页面的输入、选择和消息／计划恢复状态；需要发送消息的引用通过“引用材料”显式选择，已添加引用可以打开保存快照。UI 隐藏不等于取消请求。UI01 的实际检查与预览更新状态见[短记录](../../docs/superpowers/plans/2026-10-12-sn-workbench-ui-focus.md)。

切换材料／案例保留当前页面中的未保存判断；失败保留输入，回执丢失重试不会重复保存。刷新恢复已保存记录和阅读位置，**未保存草稿尚不跨刷新持久化**。同一评审者的旧基线会返回冲突，旧修订及其他评审者记录不会被覆盖。当前 HTTP 服务操作人固定为启动参数 `--actor`（默认 `local-reviewer`）。

左右侧栏按 Paseo 的实际壳层调整：默认各 320px，顶部约 36px，左侧单行层级导航与底部图标，右侧材料标签与同色背景。拖动侧栏内沿可调宽度，浏览器记住宽度和左栏开关；窗口变窄时自动给中间工作区保留空间。顶部图标分别开关两栏，Ctrl/Cmd+B 切换左栏、Ctrl/Cmd+E 切换右栏；收起再展开右栏保留当前材料与页面内草稿。右栏开关恢复上次材料；尚未明确选择结果时，当前会显示任务第一份结果，并在标题标明对象。窄屏使用可关闭的导航抽屉和全屏材料。

## Task 与 Agent 协作

在任务中显式关联完整 Agent ID，或选择服务器允许的 profile 创建无 prompt Agent；创建成功后刷新本地会话列表，仍需明确选择会话。关联是持久引用，不证明远端存在、空闲或可用。bootstrap 声明是否配置了连接／创建／发送能力，不是服务健康证明。配置移除后，本地创建记录仍可只读核对。

会话页显式刷新及分页，保留来源 epoch、真实序号范围和条目标识。选中完整 assistant 条目才可起草候选；服务端重新读取并核对来源。这里的“完整”指未截断且来源精确的条目快照，不证明对应 turn 已结束。候选不是人工判断，最终确认／拒绝／待定另存修订。上下文只从显式目标、允许的相对文件、已关联结果／案例选择；发送前服务器重读来源、核对所选 hash，变化则拒绝。案例投影不含 gold；普通文件路径限制不保证文件正文无 gold，需要操作者和服务器来源配置保持范围。

工作页发送默认使用 `interrupt`（Agent 运行中时输入框上方会提示将打断当前一轮）；hook 仍支持 `steer`，界面暂不提供切换。发送前状态预检不构成原子 reject-busy。当前正文限 8 KiB、最多 4 个引用、上下文合计 32 KiB；最终组合正文还须满足 bridge 的 32 KiB UTF-8／16384 UTF-16 units 和序列化 stdin 64 KiB 限额。这些是当前局部预算，不是永久产品契约或 token 预算。

消息恢复元数据由同一浏览器 profile、同一 origin 的一个页面独占写入。其他页面仍可阅读任务、会话及保存调查笔记；消息写入禁用，关闭原页面后重开其他页面才能接管。通过 Web Locks 保持实际排他，接管后重读最新元数据；API 不支持、锁失败或非安全上下文则禁发。因此通过非 localhost 的普通 HTTP 地址访问时，可能无法使用消息功能。该保证不覆盖其他浏览器／设备、换 actor 的服务重启或 Paseo 的其他客户端；SQLite 另保证同一个请求 key 只 claim 一次。

父状态在首次异步操作前锁定正文、引用和行为，持久保存原请求 key 与必要恢复身份，不在浏览器恢复记录中保存完整私有正文。发送中和 unknown 不能编辑、换 key 或开启新意图；切模式和打开引用仍可阅读。关闭页面／停止等待不撤销远端副作用。重开按原 key 只读 GET 本地记录；missing、prepared、坏回执及读取失败不能证明未发送，也不允许重发。发送前因导航而中止的意图也保守保留 unknown，当前没有人工解除入口。

来源变化、删除／不可读、未配置发送能力及输入预算拒绝，仅当服务端明确返回固定前写类型码和匹配的原 key 时，才允许显式纠正。已确认拒绝也保存恢复身份；刷新后核对本地记录，不能被存在的同 key 意图覆盖为纠正许可。普通 409／422、网络错误或没有匹配类型码的错误继续保持 unknown。浏览器不会自动重发 POST；已放弃的前置拒绝 key 不再用于发送。消息 prepare 与 attempt 之间进程崩溃留下的 prepared 只读返回、不会再次派发，与无 prompt 创建的首次 prepared claim 策略不同；本阶段有意保持消息恢复的保守策略。

恢复元数据尚无自动归档清理或容量管理。格式损坏、读取／保存失败或浏览器配额不足会明确禁发，不自动删除未知意图或旧 ACK。手工清理浏览器存储会丢失这些本地锁和定位元数据，不能据此宣称 SQLite 意图已解除。

“SDK已返回发送ACK，不代表执行完成”仅陈述已绑定的 SDK resolved 事实。连接释放失败、当前目标不可用不能抹掉已有 ACK；历史输入匹配或空闲状态不能证明完成。核实 ACK 或明确未调用后，可显式开启空的新消息草稿，行为重新选择；旧 ACK 按原意图保留。真实 turn 完成需要独立、正确关联的终态事件，本阶段未增加通用完成追踪界面。

## 当前真实材料

B／C 首切片已完成独立 state 页面验收和 Astra／Opus最终收敛，并更新受管预览。计划输入由独立 `inputs` 登记提供完整 canonical bundle，不要求 Task 先有关联成绩；结果比较消费 Task 已关联的冻结快照，并由可信配置选择完整 canonical 输入，不从快照的来源路径自动授信。

计划保存的是不可变草稿修订，`execution=false`。选题显式指定，按 canonical 顺序保存并派生分区归属；源码绑定尚未捕获，模型服务和官方 scorer 尚未选择。保存不检查 workspace 是否 dirty，也不证明将来可执行。执行前仍须绑定实际加载的源码内容、输入与运行／评分配置。`notebook-request-v3` 和 Notebook runner 是本适配器的执行意图，不是所有 Task 或原生 Agent 的统一协议。

Job 保存固定 plan revision；更改页可显式选计划、捕获输入与源码、确认费用上限和总超时后启动真实 captured Notebook runner，按分区生成后独立官方文本评分。执行历史自动发现关联 run，可回访答案、待读题和官方分；取消与超时保留已有工件，unknown 响应按原请求恢复。

当前 `configs/sn-workbench-local.json` 支持 QASPER，使用已存在的独立 SN worktree 与私有实验模型配置。保存计划和准备不会调用模型，只有点击“确认预算并运行”才执行。完整步骤与实际验证边界见 [从界面执行评测](../../docs/workbench-execution.md)。本轮浏览器验证用无模型 smoke，未新增真实模型实验；旧 Job receipt 继续只读兼容。

J02仅在独立临时state验证固定短替身的实际启动、取消意图、OS退出观测及owner崩溃后保守对账；当前后端含该模块，但没有页面／HTTP／CLI替身派发入口，该历史替身入口与新 M4 Notebook execution 层分开；新 UI 的模型运行未执行。退出状态丢失保持unknown，不自动重派；owner取消走原helper私有管道，不按PID发信号。自然PID复用为同owner另一个child时的退出status代际归属尚未制造或排除，真实runner前还需验证；测试owner重开不等于实际服务重启验收。具体闭合范围、I1红绿与保留限制见[J02记录](../../docs/superpowers/plans/2026-10-12-sn-workbench-job-lifecycle-probe.md)。

同题比较复用既有准入与比较器，保留正式汇总、完整声明范围及实际分母；附加逐题／分组估计独立显示，不重评分、不缩到成功题交集、不合成总分。不可配对或来源不可用时仍保留各侧阅读。当前三份历史结果属于不同任务／套件，不能据此宣称已有合格配对；正向验收使用独立完整 canonical 与明确身份的合成工件。

历史面板的“执行前需冻结输入”指最终执行前重新核验原输入，并绑定实际加载的源码内容、模型服务和 scorer。草稿保存时已核过的输入身份与选题，不等于完成可执行冻结。`source_revision` 是不加盐的内容哈希，避免下发原文，但不为容易猜到的值提供保密保证。

“评测草稿”先明确选择输入、填写显式题单，再保存。历史行打开固定 revision；“读取 head 并追加”明确读取最新基础版本。回访旧版本仍以旧 base 保存，冲突时保留正文，不偷偷换到新 head。保存未知时保留页面中的原命令与 key，只能显式查询回执或同 key／同正文重试；缺回执不是未提交证明。未保存正文和未确认命令尚不跨页面刷新持久化，unknown 时先在原页面核对；重开后从服务端列表／历史回访已提交版本，不因列表暂缺就自动另换 key。

来源显式登记于 [本地配置](../../configs/sn-workbench-local.json)，路径相对配置文件解析；不自动选最新或最高分。原材料在本机被忽略的 `var/` 中，其他 checkout 需要独立取得材料或改配置。

本切片保留三项诊断／性能限制：

- 登记 canonical 目录被换成另一个合法 bundle，或在两次读取间发生这种替换时，可能返回 `not_comparable`，详情为 “different frozen bundle”。先核对 canonical 登记，不能据此认定原两份结果本身不可配对；不可读或校验失败的 canonical 才按不可用诊断。
- HTTP 可信 canonical 按左侧结果的登记选择。左侧未登记时提示需要可信完整基准数据；左右互换后可能变成不可配对。两种状态都阻止比较，当前不保证失败分类对称。
- 每次保存新修订（同 key 重放除外）会在 SQLite 写锁内完整重建 canonical，尚未测量耗时／争用预算。其他写操作等待该锁，最多 10 秒后可能失败；消息意图仍按 A 的保守规则处理 unknown，不自动重发。这不是承诺的运行预算。

结果读取／比较仍返回原 `manifest.source`（含 revision／source_url），与既有结果阅读一致；B 的 opaque revision 不替代结果来源投影。未登记一侧仅保证快照结构与自洽内容身份，尚未绑定 canonical。覆盖计数通过“打开成绩”阅读；`recomputed-subset` 与 `controlled-rerun` 差异在方法条件中披露，缺失／非法类别和发表参考拒绝进入主配对。服务换 `--actor` 后原回执作用域不同。两次未解释的 reload／网络瞬态失败仍是验证局限，最终成功不证明根因已修复或多次稳定运行。

| 材料 | 范围与限制 |
| --- | --- |
| QMSum SN chunk | 历史单题链路验收，1/281；Perl ROUGE 分母 1，不能解释为全量成绩 |
| MultiHop-RAG PaLM | 公开答卷重评分 2556 题；QA 分母 2556、检索分母 2255；不是 SN 成绩 |
| ALCE ASQA Vanilla | 作者 human_eval 100/948 题，文本重评分；所见文档映射缺失，引用指标 pending |

旧 QASPER 工件在当前严格重建下因 prepared／evidence projector 身份不一致被拒绝，尚未接入；没有改写旧身份或放宽准入。导入不会生成答案或重新运行 scorer。页面保留原 batch 汇总，逐题值用于调查，不能替代完整成绩。

## CLI、数据与备份

计划 CLI 的已知领域拒绝输出固定 JSON 诊断到 stderr，非零退出；保存拒绝回显原 `request_key`，不会输出 traceback、来源路径或宣称此前未提交。查询回执仍按原 actor／key 作用域进行。未识别的程序异常继续透传。

```bash
PYTHONPATH=src .venv/bin/python -m rag_eval.workbench.cli --state var/sn-workbench import --bundle /path/to/bundle --submission /path/to/submission.json --scores /path/to/scores.json --task TASK_ID
PYTHONPATH=src .venv/bin/python -m rag_eval.workbench.cli --state var/sn-workbench export --task TASK_ID > /path/to/task.json
PYTHONPATH=src .venv/bin/python -m rag_eval.workbench.cli --state var/sn-workbench review --input /path/to/review.json --actor reviewer-name
PYTHONPATH=src .venv/bin/python -m rag_eval.workbench.cli --state /path/to/state plan-save --config /path/to/config.json --task TASK_ID --actor reviewer-name --input /path/to/plan-command.json
PYTHONPATH=src .venv/bin/python -m rag_eval.workbench.cli --state /path/to/state plan-list --task TASK_ID
PYTHONPATH=src .venv/bin/python -m rag_eval.workbench.cli --state /path/to/state plan-show --task TASK_ID --plan PLAN_ID --revision REVISION_ID
PYTHONPATH=src .venv/bin/python -m rag_eval.workbench.cli --state /path/to/state plan-receipt --task TASK_ID --actor reviewer-name --request-key ORIGINAL_KEY
```

`review.json` 字段：`task_id`、`result_id`、`case_id`、`judgment`（confirmed/rejected/undecided）、`reason`、`evidence`（案例提供的 evidence_ids）、`base_revision`（首次 null）、`request_key`（本次命令固定唯一键）。同键异内容拒绝。

`plan-command.json` 必填 `title`、`input_id`、`expected_bundle_id`、`case_ids`、`mode`、`request_key`；修订另带 `plan_id`／`base_revision`，新计划两者为空或省略。操作人与 Task 从 CLI 参数提供，workspace 从 Task 推导，不能用正文覆写。CLI、HTTP 共用同一事务领域服务；旧版本与旧回执读取不依赖仍可取得的输入目录。

`var/sn-workbench/workbench.sqlite` 是任务、追加人评、笔记、候选、人工决定、Task-session 关联、本地创建／消息意图与回执的权威源；Paseo 持有实际 Agent 会话和执行。`imports/<result-id>/snapshot.json` 冻结实际消费的题目、资料、答卷和评分，`COMMIT.json` 校验内容。原工件只读。任务导出包括本地关系、结果摘要及领域历史，**不包括完整结果快照、Paseo 会话备份或私有发送正文**；备份应暂停该工作台服务后复制整个 state 目录，或对 SQLite 做一致性备份并保留 imports。状态包含结果材料及用户记录，应按原语料权限保存；访问码不随任务导出。

## 实现与验证范围

前端为 Expo Web／React Native Web；控件独立实现，参考 Paseo 的导航投影、面板职责、灰阶主题、侧栏密度和输入几何，没有修改 Paseo 或拷贝其私有组件。Node 依赖锁在本目录 `package-lock.json`，Python HTTP 依赖声明在 workbench extra。本机实际验证 Node 24.20.0、Python 3.13、FastAPI 0.135.3、uvicorn 0.52.4；这些是本轮环境事实，不是产品固定版本。

定向 Python 校验 85 项通过，覆盖身份／篡改、真实分母、零分／缺失、batch-only、条件不适用、并发导入、幂等、修订冲突、HTTP 身份与 Origin。TS 类型检查和 Expo Web 构建通过。真实 Chromium 验证任务建立、接入、证据、人评、笔记、丢回执重试、刷新／历史、两栏开关与拖动、宽度记忆、右栏隐藏后草稿保留、960px 内容空间、390px 窄屏和深色；另经 Paseo 代理检查三个真实预置任务、MultiHop 零分与 ALCE pending，未写入预览库测试判断。截图在本机 `test-results/`，不纳入 Git。

浏览器脚本分别为 `tests/browser.mjs`（必须使用独立测试 state，会写测试记录）和 `tests/preview.mjs`（检查上述三份已接入结果，仅读取，另发送无效空命令验证 Origin）。2026-10-14 对话改版后，工作页对话流程由 `tests/chat-browser-server.py`＋`tests/chat-browser.mjs` 覆盖（合成 Agent，不调用 provider）；依赖旧笔记／会话面板控件的脚本见 `TRIAGE.md`。设置 `WORKBENCH_TEST_URL` 为访问链接，必要时设置 `WORKBENCH_CHROMIUM` 为本机 Chromium 路径。本轮 Chromium 的系统库／中文字体缺失通过 `var/browser-deps`、`var/browser-fonts` 本地解包解决，没有修改系统配置。

上述 85 项是首个结果工作面切片的历史验证；A 阶段当前 Python 工作台回归为 377 项通过，另有组件／hook 和临时 HTTP/browser 定向证据，逐切片结果见阶段记录。真实 SDK 与模型执行证据分开记录，离线替身通过不能替代真实服务验收。

消息父层当前通过 168 项实际 hook／浏览器检查和独立 31 场景回归；首次合法回执的消息身份及 input/body hash 持久冻结，合法格式替代值也被拒绝。同源排他写入、四类写前拒绝刷新恢复另经临时 App／HTTP／SQLite 验证。

身份冻结修复通过时，实际页面经 HTTP、SQLite、Python adapter 和 Node CLI 向保留测试 Agent 发送一次无上下文短消息，保存一条 acknowledged、release ok 回执。独立订阅的 user message ID 与该意图匹配，并观察同 turn 的指定回复及 `turn_completed`，没有工具事件。随后浏览器所有权及拒绝恢复修复未再次发送真实消息；无 prompt 创建及已有会话取源候选另有真实验收。这些证据只证明有限输入在当前本地 daemon 的路径，不证明通用模型完成追踪、长期可用性或正式 benchmark 成绩。Astra／Opus 最终委员会认可已披露范围内闭合；预览更新后只读核对三任务、能力声明与消息禁用原因，没有实发或写入测试记录。

已实现显式选题的计划草稿和只读结果比较；M4 已接界面 Job 执行与源码捕获（见上文）；自动模型建议、SN 改码与 R1 全闭环仍待实现／实际验收。后端已有显式captured-source CLI、固定Job输入／源码准备事实、私有配置及解释器／依赖启动核验；E3d进一步记录正常provider的有效Settings／loaded registry及scheduled Future调用代际，经独立审在该范围闭合，这些历史核验不证明新界面的真实模型运行成功。E4已将run的 `model-events.jsonl` 接入完整offline reader、aggregation和report的run级配对／pending／截断审计，并经owned及shared独立审闭合；legacy未结束计数原义保留，新pairs不归逐题事件。history_exhaustive恒false，不证明完整历史、HTTP／模型／scorer／Job完成；E3d原M1报告与结论保留，逐题Dashboard／UI展示仍未接入，启动比较相同不证明后续调用代际相同。真实owner／凭据与可达性未验，Job仍 `executable=false`，官方评分另行进行，详情见[源码执行记录](../../docs/superpowers/plans/2026-10-12-sn-workbench-source-execution.md)。旧 Dashboard 保留，正式比较继续复用既有 `benchmark_comparison`。当前访问码是单操作人本地开发接入，不是生产多用户认证。本次后端切片不改预览／用户state、不运行真实模型；最小 Agent 接入验收单独记录。

## R1 run 阅读与 H1 实验

可信服务器配置 `run_views.needs_read_thresholds` 按 suite 指定待读阈值。QASPER 默认 `answer_f1 < 0.5`，严格小于；非 success 状态始终待读，缺分保持未知，不当作零。其他 suite 维持既有阈值1，可显式配置0到1的有限数。待读仅为阅读筛选，侧栏待决数仍只统计真实 undecided 决策。

`run_views.hypotheses` 可显式登记 `{id, task_id, title, path, status}`，只在匹配 Task 的更改页展示，不按源码HEAD或分数推断关联。案例判断本轮先保存在失败账本；未绑定worktree的任务不展示全局run。H1独立Task、真实diff、固定题单验证及临时构建证据见 `test-results/r1-launch-20261013/h1/`；用户state与既有preview未切换。
