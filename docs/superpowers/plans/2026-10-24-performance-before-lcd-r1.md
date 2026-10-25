# LCD R1 之前的性能与约束清理

> 给执行开发的 agent：先读 `AGENTS.md`、`CURRENT_STATE.md`、`docs/evaluation-context.md`、`docs/evaluation-status.md`，再读本文。本文的每一条都有实测或代码定位的依据，列在"证据"里；不要凭感觉加优化。不调用任何模型，不启动 SN 生产服务，不读写 `var/` 里的真实运行；性能验收用离线假模型与合成执行做。每个工作项一个提交，提交前跑完 `RUNBOOK.md` 的 pytest 命令（可用 `-n auto`，见 P6）。

**Goal:** 让 LCD R1（`docs/experiments/2026-10-24-lcd-r1.md`：导入约 49 篇、基线与每个候选各 2 次 attempt、簇级子集运行、3 个新鲜批次）在程序侧不再做重复的事、不再把可并行的工作串行、不再因一个超时让整次执行作废；并把"评估器改一行代码旧分数就不可比"这个会在实验中途咬人的问题修掉。六条不变量（生成不见 gold；结果状态真实；证据来自实际输出；失败不抹掉已完成工作；比较条件可核验；实验与生产隔离）一条不动。

**Architecture:** 改动集中在 `src/rag_eval/`（`notebook_runner.py`、`notebook_batch.py`、`runtime_environment.py`、`job_preparation.py`、`run_reader.py`、`notebook_bundle.py`、`artifact_store.py`、`benchmark_official.py`、`benchmark_comparison.py`）与 `src/rag_eval/workbench/`（`job_execution.py`、`notebook_job.py`、`repository.py`、`iterations.py`、`iterations_api.py`、`comparisons.py`、`run_views.py`、`splits.py`）。新增一个 `preparation_cache.py`。

**Tech Stack:** 现有 Python 3.13 + SQLite + pytest；`pytest-xdist` 已在 `.venv` 里。

## 证据（2026-10-24，三份只读审计的合并结论）

实测来自 `benchmark-protocol-correctness/var/sn-eval/` 的 442 道 chunk 题、135 道 reasoning 题、33 次工作台执行：

- 一题之内程序自身开销中位数 **0.03 s**（p90 0.3 s），模型调用占运行墙钟 **96% 以上**。题内检索、捕获、落盘不是问题。
- **重复导入**：197 个运行目录、108 篇不同论文，6242 s 导入时间里 **4225 s（68%）** 在重复导入已导入过的论文；一篇被导入 21 次，耗时 3 到 123 s 不等（导入时间由模型服务决定，重复导入是我们的选择）。seeded 路径（`notebook_runner.py:137-184`）存在但大多数运行没走。
- **串行**：分区内题目严格顺序（题间间隔 5 ms，askjob 从不交错）；`job_execution.parallel` 代码默认 1，实测最多 2 个分区并行；重试轮次和评分都在全部分区完成后才开始，评分再逐分区串行（`notebook_job.py:233-313`）。
- **固定开销**：每分区约 4 到 6 s（拷 28 MB / 1044 文件源码约 1 s、依赖探测约 1.7 s、SN 启动与重编 381 个 .pyc 约 1.6 s）；每次执行约 6 s（设置 3.3 s、评分 2.7 s）。对每分区 3 到 4 题的 QASPER 占比可观。
- **监督器 bug**：执行 `04bc6c71…` 的 `state.json` 终态写于 18:32:58，`execution-record.json` 完成于 18:39:24，差 **385.7 s**；其余执行 0.03 到 0.09 s。代码上 `_watch` 的 `_finish` 在 `except Exception: return` 里（`job_execution.py:294-331`），busy timeout 一旦命中，执行会永远停在 running，违反"状态真实"。
- **读路径**：UI 每 4 s 刷新（`App.tsx:98-104`），每次 `GET /api/iterations/{id}` 对每个候选的每个 attempt 的每个分区做 `_discover_executions` → 对无索引的 `history` 表全表扫描 (E+1) 次，再对每个候选 `reconcile_fresh` 重建整棵树；SQLite 未开 WAL、每次 `connect()` 新开连接、busy timeout 10 s（`repository.py:79-92`、`comparisons.py:72-75, 310`、`run_views.py:195-249`）。
- **重复校验**：`load_bundle` 完整规范重建（哈希全部 raw 数据并重建 benchmark）在每次 `run()`、评分阶段两次、单分区准入一次、`create_iteration` 时 1+2×(1+F) 次（`job_preparation.py:185`、`notebook_job.py:288`、`flip_report.py:181`、`plan_inputs.py:70`）；SN 源码 tar 每分区被哈希 6 到 7 次；`job_preparation.py:213-222` 把 19 MB 源码解到 /tmp 只为校验然后删掉；`plan_batch` 加载并哈希索引里**全部**分区的 capsule 而不只是选中的（`notebook_batch.py:71-78`）；阶段指标每分区算两遍（`run_report.py:91`、`notebook_batch.py:175`）；`validate_submission` 每分区约 4 次、每次 O(全部 bundle case)；准入通过 `_compare_objects` 每侧重复校验约 3 次（代码注释自承"数百分区要准入几分钟"）。
- **超时模型**：一个执行一个 timeout（默认 3600 s）罩住生成、重试和评分（`job_execution.py:268-293`）；超时杀整个进程组，不评分，Admission 判不准入，已完成分区的文件虽在但不可用。R1 的规模下这很可能触发。重试按整个分区重问，成功题也被替换（`notebook_job.py:237-257`）。
- **评分器身份**：`benchmark_official.py:72-89` 把评估器自身模块（`benchmark_official`、`notebook_scoring`、`qasper_evidence`、`hotpot_evidence`）的文件哈希放进 scorer identity，比较要求相等（`benchmark_comparison.py:390-392`）。于是本文任何一次改动都会让已有分数不可比，直到重评。实验中途改评估器代码就会断链。
- **seed 守卫**：`seed_guard_patterns` 子串匹配（`iterations.py:760-770`）既过宽（命中查询侧的 "embedding"、答案侧的 "parsing" 文件）又过窄（漏掉 `config.py` 里的 chunk 大小）；seed 相等性比较整个 Settings 哈希、`.env`、`feature_set`、`code.versions`（`notebook_runner.py:84-94, 127-133`），答案路径上改一个默认值就被拒绝 seed，然后整篇重导。
- **身份字段**：正式比较必须相等的只有 `bundle_id`、suite、scope、`case_ids`、profile、scorer identity、分母与 eligible ids、reasoning 协议；其余全是披露。`audits_sha256` 是常量、`settings_sha256` 无人读、`code.actual_manifest`（663 项）被复制进每个 manifest 与 submission 但只用于模块来源检查（其 sha 即可）。
- **开发回路**：1331 个测试约 9 分钟；`pytest-xdist` 已装未用；`test_sn_preparation_integration.py` 在本机会真的导入 100 篇文档（因为 `project/backend` 存在）；约 96 个测试各自 `git init` + 两次提交 + bundle 安装 + worktree + 源码捕获。
- 模型侧（不归我们改，只记录）：一批只有几十 token 却耗时 36 到 41 s 的成功调用占 chunk 调用时间 36%，9 月健康期没有；60 s 超时加退避占 28%；准备阶段 3 次 chat 调用串行。

## 边界

- 每个运行仍有自己的私有数据库副本、存储、日志；生成缓存保持关闭；记忆 / 画像 / 经验开关保持关闭。复用的是"准备好的 notebook 的内容"，不是运行时状态。
- 任何缓存都按内容地址键控，读取时核验字节；缓存命中写进 manifest 的披露字段（`prepared_from`），不改正式比较的必须相等集合。
- 不删除任何记录、不补零、不按分数重跑；重试只针对失败的 case，两次结果都保留，评分用明确声明的那一份。
- 不改 `generation never sees gold` 边界上的任何代码（`notebook_runner.py:443-450`、`benchmark_official.py:136-139`）。

## 工作项（按 R1 影响排序；每项含验收）

- [ ] **P0. 状态真实性与读路径的两处 bug 修复**（小而急）。
  `job_execution._watch`：`_finish` 失败要重试（指数退避，至少 60 s）并落一条 `execution.finish_failed` 历史，而不是吞掉返回；执行终态与 `state.json` 的差必须可解释。`repository`：`PRAGMA journal_mode=WAL`、`synchronous=NORMAL`；给 `history(object_id, kind)` 与 `history(task_id, kind, object_id)` 建索引（迁移只加索引）。
  验收：合成测试里让 `_finish` 第一次抛 busy，执行最终仍为终态；`EXPLAIN QUERY PLAN` 对 `_discover_executions` 的查询走索引；现有测试全绿。

- [ ] **P1. 按内容地址复用已准备的 notebook**（最大项）。
  新增 `preparation_cache.py`：键 = (bundle partition id, 准备代码身份, 准备 Settings 子集, embedding 服务身份)。准备代码身份 = 捕获源码中**准备路径集合**（ingest / chunking / embedding / structural_markdown / parsing / notebook_metadata / batch_ingest，以显式路径列表而非子串配置）的文件内容哈希，加 `config.py` 中准备相关键的值哈希；Settings 子集明确列出（chunk 大小、重叠、embedding 模型、元数据策略等），不再比较整个 Settings、`.env`、`feature_set`、`code.versions`。
  任何运行（根的第 2 次 attempt、候选、fresh-run、cluster-run、重试）在准备前查缓存：命中则从缓存的只读副本 `install_prepared_seed`（保留现有拷贝与 SQLite backup，去掉对全部列的 `instr` 扫描，只扫文本 / 路径列）；未命中则准备并登记。`seed_guard_patterns` 子串守卫与 `validate_prepared_seed` 的全量相等性检查由上述身份比较替代；改了准备代码的候选自然 miss，重导，不再 Conflict。manifest 新增披露字段 `prepared_from = {cache_key, source_run}`。
  验收：合成执行（假模型）里，根第 2 次 attempt 导入 0 篇；改答案路径文件的候选命中缓存；改 chunking 文件的候选 miss 并重导；缓存目录只读、每运行仍有私有 DB；比较记录的必须相等集合不变。

- [ ] **P2. 并行与屏障**。
  (a) 分区并行度：`parallel` 由 iteration 协议 / 执行配置给出，R1 用 8；增加跨进程的全局模型并发上限（文件信号量或计数文件），使 `parallel × 进程内 max_concurrency` 不超过配置的闸门，减少 429 风暴。(b) 分区内题目并发：`notebook_runner` 的问题循环改为可配置的小线程池（默认 1 保持现状，R1 用 4），每题独立 `conversation_id=null`，journal 写入加锁、每行仍 fsync，记录顺序按完成时间，`state.json` 按题更新保持。(c) 评分随分区完成即做，不等全部分区；`export_sn_runs` 用进程内已校验的 bundle。(d) 超时：每分区与每 case 各有 deadline（配置项），超时的 case 记 `error`（不是 missing），分区继续；执行级 timeout 只作为兜底，触发时**已完成的分区照常评分并可准入**。(e) 重试：只重问失败的 case id，seeded 自同一执行的已准备 notebook，结果按 case 合并，两次都保留并在 `retry-rounds.json` 里说明用了哪一份。
  验收：合成 10 分区 × 3 题假模型执行，并行 8 的墙钟低于串行的 1/4；一个分区人为挂起时其余分区被评分且执行可准入；每 case deadline 触发后状态为 `error` 并有 `error_phase`；重试只重跑失败 case，成功答案不被替换。

- [ ] **P3. 读路径与比较的计算时机**。
  `GET /api/iterations/{id}`：每请求发现一次执行、构建一次树并传给 `reconcile_fresh`；sealed 策略每执行检查一次（检查本身保留）；`Admission` 与 flips 的持久 memo 以 `scores_id + outputs_sha256` 键控；比较记录在执行进入终态时计算并落库，页面刷新只读。`list_runs` 的 `_ledger_rows` 按文件 stat 缓存。前端 4 s 轮询改为只在有 running 时轮询，且后端返回 ETag / 版本号避免重算。
  验收：合成 20 候选 × 2 attempt 的树，一次 GET 的 SQL 语句数有上界（测试断言数量不随候选数平方增长）；两次相同 GET 第二次不触发任何 `load_bundle`。

- [ ] **P4. 去掉重复校验与重复物化**。
  `load_bundle` 进程级缓存，键 = 对象 id + manifest 哈希，读时核验字节；`notebook_job` 评分阶段复用 `context.bundle`；`plan_batch` 只加载选中分区的 capsule；SN 源码 tar 每执行物化一次为只读目录并被所有分区复用（保留模块来源 `_loaded_source_identity` 检查），删除 `job_preparation.py:213-222` 的 /tmp 解压校验；`_publish` 对已存在对象不再 `resolve()`；依赖观测用 `packages_distributions()` 收窄到候选分发并按解释器前缀 + 文件 stat 缓存；阶段指标只算一次；`code.actual_manifest` 不再复制进 manifest 与 submission，只保留其 sha（模块来源检查改用 sha 比对）；删除常量 `audits_sha256` 与无人读的 `settings_sha256` 的写入（读侧兼容旧文件）。
  验收：合成执行里统计哈希字节数与 `load_bundle` 调用次数，前者下降一个数量级、后者每执行 ≤ 1；旧运行目录仍可被 `load_run` 读取。

- [ ] **P5. 评分器身份与评估器源码解耦**。
  scorer identity = 官方评分器源码 sha + 版本化的 `scorer_protocol_id`（例如 `qasper-v03-full-text-answer-v1/projection-v1`）；评估器模块文件哈希移到披露字段。`benchmark_comparison` 的必须相等集合改用新身份；保存的 prepared inputs 以哈希核验而不是用当前代码重建比较。旧 scores 文件按旧字段读取并映射到新身份（披露"旧格式"），不要求重评。
  验收：改动 `benchmark_official.py` 中非评分逻辑的一行后，既有合成分数仍可比；改动投影协议并升版本号后不可比并给出原因。

- [ ] **P6. 开发回路**。
  `pytest -n auto` 写进 RUNBOOK；`test_sn_preparation_integration.py` 与 `test_reasoning_no_clarification.py` 标 `integration`，默认不跑，RUNBOOK 给出单独命令；git / bundle fixture 改为 module 级共享（测试间用拷贝隔离）；忽略 SIGTERM 的子进程测试缩短 grace 到 0.5 s（grace 可配置）。
  验收：默认套件在本机 < 3 分钟；integration 子集单独可跑。

- [ ] **P7. 清理**（可选，若时间允许）：删除 readiness receipt 路径（`s3_readiness.py` 与 `--readiness-receipt`，roadmap 减法清单项），记入 `DELETION_LOG.md`；`notebook_runner.execute` 默认 request revision 与 CLI 统一为 v3；case 选择逻辑收敛到一处。

## P0–P3 完成后的修订（2026-10-25）

- **P4**：`code.actual_manifest` 不能直接删——P1 的缓存键从它读准备文件列表。顺序改为：先让 `preparation_cache` 从捕获源码对象（内容地址存储里的文件清单）读准备路径，再把 manifest 与 submission 里的 `actual_manifest` 换成其 sha；模块来源检查改用 sha 比对。评分阶段目前 bundle 加载一次 + `RunReadContext` 自己再加载一次，目标仍是每执行 ≤ 1，用同一个进程级缓存达成。`plan_batch` 现在每轮重试每分区都调一次，只加载选中 capsule 的收益更大。
- **P5** 不变。P0–P3 没碰 scorer identity 里的四个评估器模块，既有分数仍可比；P5 做完后再改这些模块才安全。
- **P6** 大部分已成：`-n auto` 全量 2547 个测试约 70 s，真实 SN 集成测试离线约 6 s。剩余只做：RUNBOOK 写明 `-n auto`，`integration` 标记为可选不强制，git/bundle fixture 的 module 级共享若收益 < 10 s 可跳过。
- **P7** 照做：readiness receipt 退役并记 DELETION_LOG；`execute()` 默认 request revision 与 CLI 统一为 v3；case 选择逻辑收敛。
- **R1 设置约束**：真实 SN 多线程提问未验证，R1 用 `question_workers = 1`、分区 `parallel = 8`、`model_concurrency` 按网关能力设；带预算的 iteration 强制 `retry_rounds = 0`，所以 P2(e) 的重试合并在 R1 不触发。

## 不做

不动模型客户端的超时与退避策略（60 s、2^n）——这是 SN 项目的配置，记录观察即可；不引入新的存储后端；不改 LCD 的门、簇、划分语义；不为了性能放松任何不变量。

## 执行记录

（开发 agent 按工作项追加：日期、提交、测试与性能验收数字、偏离与原因。）

### P0 · 2026-10-24 · 状态真实性与 history 索引

- **改动。** `job_execution._watch` 把终态记账交给 `_settle`：`sqlite3.OperationalError`（busy／locked）按 1、2、4…秒指数退避重试，单次等待上限 60 s，不设次数上限，直到写入终态或被取消（取消已写终态时 `_finish` 直接返回）；每次失败尽力落一条 `execution.finish_failed`（attempt、at、`process_exited_at`、错误类型与脱敏消息），当时因库忙没落下的失败记录与最终 `job.execution_finished` 在同一事务写入，所以"子进程退出 → 终态"的时差可以从 history 解释。非 SQLite 的确定性异常不重试，只记 `finish_failed`。超时分支原来在 `try` 外调 `_finish`、异常会直接杀死监督线程，现在走同一重试。`repository`：`journal_mode=WAL`（持久在库文件上）、每连接 `synchronous=NORMAL`；新增 `history_object_kind(object_id, kind)` 与 `history_task_kind_object(task_id, kind, object_id)`，`user_version` 5→6（只加索引；`evaluation_jobs`／`job_lifecycle_probe` 的版本白名单加 6）。
- **核对审计定位。** `job_execution.py:294-331` 与 `repository.py:79-92` 属实。审计的 385.7 s 差无法在本机复核（不读 `var/`）；代码上 busy timeout 是 10 s，单次 busy 不会产生 385 s，但 `_finish` 一旦抛出执行就永远停在 running，这一点属实并已修。
- **偏离。** `JobExecutionStore.records()` 原 `JOIN tasks` 写法即使有索引，SQLite 仍选择 `SCAN h`（按 `seq` 排序免排序）。改为 `task_id IN (SELECT id FROM tasks WHERE workspace_id=?)`，计划为 `SEARCH history USING COVERING INDEX history_task_kind_object`。语义不变。
- **验收。** 新增 `tests/test_workbench_supervisor_bookkeeping.py`（7 项）：`_finish` 前 3 次抛 busy 后终态为 finished，退避 1/2/4 s；9 次失败时累计等待 ≥ 60 s 且单次封顶 60 s；`finish_failed` 无法单独落库时随终态原子写入；超时分支重试；真实外部写锁持有 1.5 s 期间保持 running、释放后终态 finished；`EXPLAIN QUERY PLAN` 对 `records()`、`_read()`、`_finish()` 三条查询均走 `history_*` 索引、无 `SCAN history`；v5 库（删索引、DELETE 日志模式）重开后升到 v6、WAL、NORMAL，history 行不变。
- **实测（`scripts/measure_performance_offline.py history-lookup`，合成库，本机）。** 200 个执行 + 20 000 条无关 history：`records()` 无索引 **0.175 s** → 有索引 **0.0017 s**。
- **测试。** 全量 `-n auto`：**2519 passed, 7 skipped，墙钟 67 s**（改动前同命令 2512 passed / 7 skipped，114 s；`-n auto` 本来就能通过）。7 个 skip 均为本机缺可选工件（E5 run、官方 ROUGE、attempt-1 工件）。

### P1 · 2026-10-24 · 按内容地址复用已准备的 notebook

- **改动。** 新增 `src/rag_eval/preparation_cache.py`。条目是"导入完成、尚未提问"那一刻的 SN 数据库（SQLite backup，转 DELETE 日志模式）、storage、`product-artifacts/{state,document-map}.json`，外加 `entry.json`（每个文件的 sha256 与大小、键材料、来源运行、原 storage 前缀）。发布用临时目录 + `rename`，发布后文件 0444、目录 0555；读取时边复制边核验每个文件的字节与大小，不符即判 `rejected`、清掉半成品、照常从文档导入。键 = `sn-preparation-key-v1` 下的：分区 id + 分区 product manifest 哈希 + 文档容量；准备策略；SN 准备路径源码哈希；准备相关有效 Settings 哈希；模型服务配置（`service_config_sha256`）；依赖声明哈希；评测侧 `benchmark_runtime.py` 哈希。键材料只含哈希与公开坐标，不含 Settings 值或密钥。
  - 准备路径是显式列表 `PREPARATION_SOURCES`（文件、目录，及共享模块的 `path::定义名`）：ingest / chunking / embedding / structural_markdown / parsers / notebook_metadata / paper_meta / batch_ingest / indexing_pipeline / 持久层（`repositories/`、`models/`、`migration/`、`repository.py`、`sqlite_repository.py`）等；`retrieval.py` 只取准备用到的 `_payload_text`、`_PAYLOAD_SKIP_KEYS`、`relation_embed_text`、`RELATION_NAME_EMBED_CHARS`（AST 级，捕获字节不可用时退回整文件哈希，只会更严）。没有捕获文件清单的运行（git 快照模式）用整份源码身份，任何改动都 miss。
  - Settings 子集 `PREPARATION_SETTINGS` + 前缀族 `notebook_metadata_`、`indexing_pipeline_`、`source_element_enricher_`、`mineru_`、`kg_window_`；`chunk_mmr_k` 等答案侧设置不在内。
- **接线。** `notebook_runner.execute(preparation_cache=…)` / CLI `--preparation-cache`：准备前计算键并加**按键文件锁**（`fcntl.flock`，进程死亡自动释放），命中则复制、立即释放锁；未命中则持锁导入，导入完成且 `state.prepared` 为真后发布条目再释放，发布失败只记 `store_failed`、不影响运行。并发的同键运行（R1 根的两次 attempt 同时派发）因此只导入一次，另一次等待后命中，等待秒数记在回执里。manifest 新增披露字段 `preparation_cache = {cache_key, status}` 与命中时的 `prepared_from = {cache_key, source_run, entry_sha256}`；不进 `identity`，`pairing_id`／`protocol_id`／`sn_submission_identity` 不变；导出 provenance 复制 `prepared_from`。运行目录另存 `preparation-cache.json` 回执。`plan_batch(preparation_cache=…)` 透传，显式 seed 的作业不带缓存参数；工作台 `job_execution.preparation_cache` 缺省 `<output_root>/preparation-cache`，`false` 关闭。种子与缓存都改用只扫文本值的引用检查（`typeof='text'`，跳过 INTEGER/REAL/BLOB 声明列）。
- **退役。** `iterations` 删除 `_seed_runs`、`_guard_seeded_change`、`seed_from_root`、`seed_guard_patterns`；簇运行不再要求根已准备的 notebook。改了准备代码的候选不再 Conflict，而是自然 miss、重导。示例配置换成 `preparation_cache`。
- **偏离与原因。**
  1. 键里用整份模型服务配置哈希而非只取 embedding 服务：准备阶段还调用 chat 模型（source summary、paper metadata、notebook metadata），只取 embedding 会让换了摘要模型的运行错误命中。同一 iteration 内模型配置相同，不影响命中率。
  2. 显式 `--seed-prepared-run` / `seed_runs` 与 `validate_prepared_seed` 保留：它们是操作者的补跑入口（ASQA 948 用过），不走 iteration；工作台的重试轮次仍按同一执行的分区目录做种子（P2(e) 再改）。
  3. 被 adopt 的旧完成运行不进缓存：旧运行没记录完整 Settings 与准备时刻快照，无法算键；adopt 根之后第一个候选会 miss 一次。R1 的根是两次新 attempt，不走 adopt。
  4. 显式路径列表是已知的近似：只按 SN `970b6793` 前后的 import 关系整理；未列入、却影响准备的文件或设置会导致错误命中。列表与 `KEY_VERSION` 同处，扩大列表时升版本号让旧条目失效。
- **验收（`tests/test_preparation_cache.py` 9 项 + 真实 SN 探针 1 项，离线假模型）。** 根第 2 次 attempt：假 SN `prepare_notebook` 调用次数保持 1（导入 0 篇），命中运行的 DB `sources.file_path` 指向自己的 storage、`load_run` 无警告；改答案路径文件（`ask_service.py`）命中；改 `chunking.py` miss 并重导（共 2 个条目）；新增准备目录下文件 miss；改 `chunk_target_chars` miss、改 `chunk_mmr_k` 命中；条目文件 0444／目录 0555，篡改 storage 一个文件后判 `rejected` 并重导；两线程并发同键只导入一次、第二个等待后命中；缓存与显式 seed 互斥、缓存不得在运行目录内；无缓存运行与改动前形状一致（无新字段，`load_run` 正常）；共享模块只改非准备定义时键不变；引用检查跳过 BLOB 且能抓到无类型列中的文本引用。`identity`、`pairing_id`、`protocol_id`、`sn_submission_identity` 在命中与未命中运行间完全相等，比较必须相等集合未动。`test_sn_preparation_integration.py` 新增 `via=cache`：真实 SN（离线 fixture 模型）在仓库连接仍打开时发布，删除来源运行后从条目复制，检索与读取 source 正常。
- **实测。** 导入次数（假 SN，同键）：改动前每次 attempt 各导入一次（2 次 attempt → 2 次准备），改动后 1 次；并发派发同样 1 次。引用检查（`scripts/measure_performance_offline.py reference-scan`，104 MB 合成库、20 000 行 4 KB embedding）：全列 CAST 扫描 **0.065 s** → 只扫文本列 **0.014 s**（页缓存热；真实库按 blob 体积放大）。
- **测试。** 全量 `-n auto`：**2528 passed, 7 skipped，墙钟 64 s**。

### P2 · 2026-10-25 · 并行与屏障

- **改动。**
  - (a) 分区并行：`job_execution.parallel` 原本就存在（代码默认 1，示例配置 8）；新增 iteration 创建参数 `execution`（`parallel`、`question_workers`、`case_deadline_seconds`、`partition_deadline_seconds`，校验后存进 `protocol.execution`，派发时覆盖服务器配置；只影响调度，不进比较身份）。跨进程模型闸门 `src/rag_eval/model_gate.py`：包住与预算守卫相同的同步传输（`httpx.Client.send`、`requests.Session.send`），每个请求先取 N 个槽位文件之一的独占 `flock`，响应读完（流式则 `close()`）才释放，进程死亡自动释放；配置 `model_concurrency`，槽位在 `<output_root>/model-gate`，经 `plan_batch(model_gate=…)` → CLI `--model-gate/--model-gate-slots` 下发。
  - (b) 分区内并发：`predictions` 拆成 `ask`（SN 调用）/`complete`（证据投影）/`persist`（journal）；`question_workers>1` 或设了期限时走 `_ask_bounded`：守护线程每题独立请求，journal 原有锁与逐行 fsync 不变，按完成顺序写入，`state.json` 只由主线程写（含 `in_flight`）。默认 1 且无期限时仍是原顺序路径。Agent 评测与 MultiHop 排名捕获会修补共享对象，强制顺序、不施加期限。manifest 披露 `execution_controls`（非默认时），不进 `identity`。
  - (c) 评分随分区完成：`execute_batch` 新增 `after_run(job,row)` 钩子（协调线程，立即交给后续线程池）、`deadline`、共享 `ledger_appender`；`notebook_job` 每个分区结束后在后续线程池里做"重试失败题 → 官方评分"，评分串行（锁内）但与其他分区生成重叠，bundle 与 `RunReadContext` 进程内只加载一次。
  - (d) 期限：题级期限到时，该题以 `status=error`、`error_type=case_timeout`、`failed_phase=asking` 落盘并继续，被放弃的调用在守护线程里跑完后结果丢弃（attempts 记 `late_result_discarded`），不覆盖已落盘终态；分区期限到时在途题记 `partition_deadline`/`asking`，未开始的记 `partition_deadline`/`not_started`，分区照常进入 `finished_with_errors` 并评分。`_run_job` 在启动时把分区期限截到执行剩余时间、作为 `--partition-deadline-seconds` 传给 runner，超出 `PARTITION_KILL_GRACE=120 s` 才杀进程（导入阶段无法优雅中断）；执行截止后的分区记 `not_started`、不建目录。`notebook_job` 的软截止 = 监督超时 − max(300 s, 10%)（不少于一半），保证在监督兜底超时之前停手并给已完成分区评分。
  - (e) 重试：只重问 `error` 或缺失的题（`no_answer`／`clarification` 是真实结果，不按分数），种子仍是该分区自己的已准备运行；原运行保持为分区的登记运行，每轮重试单独评分到 `official/<name>-r<n>`，`batch-plan.json` 的 `retries[]` 与 `retry-rounds.json`（`sn-workbench-retry-rounds-v2`）写明每题用哪一份（最近一次真正答出该题的完整运行）。`RunViews` 把重试登记为 `<exec>-<n>-r<k>`（不算分区），`flips` 只对声明的题取重试运行的官方值，重试运行未准入则整对不准入；`admission` 同时要求重试运行准入。
  - 执行退出语义：分区走到自身终态（`finished`/`finished_with_errors`）即不算生成失败，题级 `error` 只是状态；只有分区崩溃、被杀、未启动或评分失败才让执行 `failed`。汇总新增 `partitions_with_case_errors`。
- **偏离与原因。**
  1. **行为变化需注意**：此前任何一题 `error` 都会让分区退出码 2 → 整个执行 `failed` → Admission 一律 `execution failed`。按计划"超时的 case 记 error、分区继续、执行可准入"，现在带题级错误的执行为 `finished`、可准入，错误题由比较的 infrastructure 计数单列，不补零。
  2. 字段名用现有的 `failed_phase`，不新造 `error_phase`。
  3. "一个分区挂起"的验收按两层实现：挂起的**题**由 runner 期限优雅收尾，分区被评分、执行可准入（`test_case_deadline…`、`test_case_errors_without_retries…`）；挂起的**进程**（如导入卡死）在期限 + 120 s 被杀，其余分区照常结束并评分，但该执行如实为 `failed`、不可准入——让缺分区的执行准入需要改比较的分区配对规则（会缩小必须相等的题集），不在本项范围。监督兜底超时（`timeout` 状态）仍不准入；子进程软截止让它基本不触发。
  4. 闸门是"同时在途请求 ≤ N"的硬上限，而非计算 `parallel × max_concurrency`：SN 进程内并发由其模型配置决定，评测侧看不到。
  5. 重试题在 `_infrastructure` 里仍按原运行的 `error` 计一次（那次失败确实发生过）；Admission 落盘缓存的签名未包含重试运行文件（重试运行在执行终态后不再变化）。
  6. 有预算的 iteration（R1）派发时强制 `retry_rounds=0`（既有规则：重试必须是新的记账 attempt），所以 (e) 在 R1 中不会触发。
  7. **未验证风险**：`question_workers>1` 只在假 SN 上验证；真实 SN `SQLiteRepository`、ContextVar 与模型调度在同一进程多线程提问下是否安全没有在本机验证（需要真实模型或更完整的离线 SN 问答 fixture）。建议 R1 在真实 SN 并发 smoke 通过前保持 `question_workers=1`，只用分区并行 8。
- **验收（离线，`tests/test_parallel_execution.py` 10 项 + 工作台 4 项）。** 10 分区 × 3 题合成分区进程（每题 0.2 s 假模型延迟）：串行 **6.18 s**，并行 8 **1.27 s**（比值 0.205 < 0.25，`scripts/measure_performance_offline.py parallel-batch`）；第一个分区可评分时刻 0.63 s（原流程须等全部分区结束：并行 8 下 1.27 s、串行 6.18 s），工作台测试断言第一个分区在第二个分区生成完成前已评分。分区内 2 题 × 0.3 s：顺序 0.72 s → `question_workers=4` 0.40 s，峰值并发 2，输出状态与 `identity` 一致。题级期限 1 s：挂起题记 `error`/`case_timeout`/`asking`，另一题成功，分区 `finished_with_errors`，整次 < 15 s；分区期限：在途题 `asking`、未开始题 `not_started`。挂起分区进程在 1.5 s + grace 后被杀（`killed`/`partition_deadline_kill`），另两个分区退出码 0；执行截止后分区记 `not_started` 且不建目录。闸门：6 线程、2 槽位，峰值在途 2；跨进程：子进程等待父进程释放槽位 ≥ 0.4 s。重试：只重问失败题（种子为原分区运行），原运行保留为分区运行，`selected_case_ids` 只在最后答出的那轮，汇总按选择取值；`flips` 只对声明题取重试值、重试运行被拒则不准入；旧 `batch-plan.json`（无 `retries`）与旧列表格式 `retry-rounds.json` 仍可发现与读取。iteration `execution` 策略校验并下发到 payload。
- **测试。** 全量 `-n auto`：**2541 passed, 7 skipped，墙钟 70 s**。

### P3 · 2026-10-25 · 读路径与比较的计算时机

- **改动。** `GET /api/iterations/{id}` 改由 `IterationAPI.show()` 提供：先算版本号（迭代相关表的行 + 任务 history 最大 seq + 全部 iteration 的行数／最大 rowid，因为 sealed 题来自所有 iteration 的协议；约 18 条固定语句），与上次返回的版本相同就直接返回内存中的树；否则在 `RunViews.request_scope()` 内只发现一次执行（线程本地，嵌套并入外层）、只建一次树并传给 `reconcile_fresh(tree=…)`（有批次完成才重建一次）和每个候选的 `for_candidate(reconcile=False)`；`primary` 直接用树上的 task id。`Admission` 的 sealed 检查改为每次请求每个执行一次（检查本身保留，请求外行为不变）。比较记录计算改用 `iteration_record()`（只读 iteration 行）取协议与任务，不再每条记录重建整棵树——这是首次 GET 平方增长的主因。响应带 `ETag` 与 `Cache-Control: no-cache`，`If-None-Match` 相同返回 304。`RunViews._ledger_rows` 按 (inode, size, mtime) 缓存解析结果，返回副本。
- **核对审计定位。** 前端 4 s 轮询已经只在有 running／provisioning 候选或执行中的请求时开启（`App.tsx` 的 `moving`），审计这条不成立，前端未改；`api()` 用 fetch 默认缓存模式，浏览器会凭 ETag 自动条件请求，无需改前端，所以没有跑 `typecheck`。`comparisons.py` 的 `Admission` 早已在执行终态后按执行缓存并落盘（尺寸＋mtime 签名），所以没有另加 `scores_id + outputs_sha256` 键的 memo。
- **偏离与原因。**
  1. 比较记录仍是"执行终态后第一次读取时计算并落库，之后只读"，没有挂到监督线程的终态回调里：那样要让 `job_execution` 反向依赖 iteration／比较层；度量键里含执行终态，所以等价于"终态后算一次"，之后刷新命中版本缓存或只读已存记录（测试断言 flips／admission 调用次数不再增加）。
  2. flips 没有单独的持久 memo：记录落库后不会再算 flips，重启后也只读记录。
  3. 版本号不覆盖"执行终态之后运行目录文件被改写"的情况（例如手工重评分）；这与 Admission 既有的内存缓存同一前提。
- **验收（`tests/test_workbench_iteration_read_path.py` 6 项，真实 iteration 树 + 桩化 RunViews，统计 `sqlite3` trace 语句数）。** 每个候选 2 次 attempt：

  | 候选数 | 首次 GET（改前 → 改后） | 刷新（改前 → 改后，未变化） | 刷新（树有变化，改后） |
  | --- | --- | --- | --- |
  | 4 | 445 → 94 | 178 → 18 | 88 |
  | 10 | 2353 → 214 | 742 → 18 | 160 |
  | 20 | 8733 → 414 | 2482 → 18 | 280 |

  改前随候选数近似平方增长（20/10 比 3.7、3.3），改后线性（每候选约 20 条，含首次落库），未变化的刷新为常数 18 条；第二、三次相同 GET 不调用 `load_bundle`／`RunReadContext.bundle`、不增加 flips／admission 调用。另测：任务内新事件使版本变化并重建；HTTP 带 ETag，未变化时 304 且无正文；请求作用域内 3 次 `execution_runs` + 3 次 Admission 只读一次执行记录、只做一次 sealed 检查，请求外行为不变，作用域按线程隔离；账本只在文件变化时重解析。
- **测试。** 全量 `-n auto`：**2547 passed, 7 skipped，墙钟 70 s**。

### P4 · 2026-10-25 · 去掉重复校验与重复物化

- **改动。**
  - **缓存键先改读源码对象。** runner 在快照后把 `actual_manifest` 从 code 里取出：内存里保留给模块来源检查（`_loaded_source_identity`）与准备缓存键（`preparation_key(manifest=…)`，来自内容地址存储里捕获源码对象的 `object.json`），保存的 `identity.code` 与 `source-identity.json` 只留 `source_manifest_sha256`（即 manifest 的指纹）。`source_identity.compact_code_identity` 在丢弃旧运行里的整份 manifest 前核对它与指纹一致；`sn_submission_identity`、`experiment_aggregation`、`build_comparison_identity` 与 `benchmark_comparison` 的方法差异披露都按同一投影比较，所以旧、新运行同条件时导出族与比较身份一致（旧运行可与新运行合并导出）。读侧：`validate_sources` 只比对象记录与指纹；`validate_runtime_model_services` 对新运行从源码对象取 manifest，旧运行仍用自带的。
  - **SN 源码每执行物化一次。** `notebook_job` 用 `materialize_shared_sn_source` 把捕获对象逐成员核验后解到 `invocation/sn-source`，`compileall` 一次，置只读（文件 0444、目录 0555）；分区与重试经 `plan_batch(shared_sn_source=…)` → `--shared-sn-source` 拿到它，`link_shared_sn_source` 核对标记（同一 ref 与指纹）与文件集合后，在各自 `captured-source` 下建硬链接树（跨文件系统时退回复制）。模块路径、`sys.path[0]` 仍在各运行目录内，启动依赖观测的 `<RUN>/captured-source/backend` 约定不变，每个已加载模块仍逐个对 manifest 重算哈希。单独运行 CLI 时仍按原方式私有物化，但 `materialize_captured_sn_source` 不再先哈希整个 tar（逐成员核验更严）。`_shared_snapshot_captured_sources` 的 `product_archive_sha256` 改取经 ref 认证的 `object.json` 文件哈希，不再三次 `resolve`／`digest` 整个 tar；评测器源码哈希只算一次（`install_files(hashes=…)`）。
  - `job_preparation` 删除 `/tmp` 解压校验：tar 哈希仍在准备时核验，成员与 manifest 的一致性在执行物化时核验。
  - **bundle 每执行 ≤ 1 次重建。** `run_reader.verified_bundle` 是进程级缓存，键 = 目录 + `manifest.json` 哈希，命中前重算 manifest 列出的每个文件哈希（字节核验保留，只省去原始数据的解析与重建）；`RunReadContext.bundle` 走它；`notebook_job` 评分改用同一个 `RunReadContext` 的 `bundle()`，导出与评分共用一次加载。`RunReadContext` 另按 (bundle/index ref, 分区, 修订) 缓存已核验的 capsule。
  - `plan_batch` 用 bundle 对象里的 `partitions.jsonl`（单文件核验）定位选中题所在分区，只加载这些 capsule；成员、跨分区与越界检查不变。
  - 读路径（`input_directory`、`validate_sources`）对源码对象只核验 `object.json`，不再哈希 tar：读已完成的运行不消费归档字节。`_publish` 遇到已存在对象只核验其记录。
  - 依赖观测 `_owners`：仍只认 RECORD 列出的路径（不用 `packages_distributions` 启发式，原注释的约束保留），但不再对每个发行包的每个 RECORD 条目做 `resolve()`，而是把模块路径换成相对发行包根的文本去查，命中后再 `resolve()` 确认；经链接或 `..` 才能到达的条目匹配不上，会以"缺少 owner"失败（失败关闭）。
  - 阶段指标：runner 的报告把已算的结果写成 `stage-metrics.json`；批次端 `saved_stage_metrics` 在各输入文件哈希与 `state.json` 都一致时直接复用，否则重算。
  - 不再写常量 `audits_sha256`（notebook runner 与 baseline runner）和无人读的 `settings_sha256`；读侧兼容（`run_ledger` 仍以它为最后回退，聚合不再要求它存在）。
- **实测（`PYTHONPATH=src:tests python tests/perf_p4_probe.py`，合成捕获源码 1000 个文件、tar 21 MB，12 分区 QASPER bundle）。**

  | 量 | 改前 | 改后 |
  | --- | --- | --- |
  | 每分区源码绑定哈希字节 | 112.8 / 110.9 / 110.9 MB | 6.5 / 2.6 / 2.6 MB（另每执行一次物化 20.4 MB） |
  | 读回一个分区的输入对象 | 23.3 MB（子进程报告与批次阶段指标各一次） | 0.4 MB |
  | 单分区重试的 `plan_batch` | 加载 12 个 capsule，0.71 MB | 1 个，0.12 MB |
  | 重发布已存在的评测器对象 | 3.65 MB | 1.87 MB（调用方算一次；绑定路径里传入后为 0） |
  | 评分阶段 `load_bundle` | 每执行 2 次（`load_bundle` + `context.bundle`） | 1 次（`test_scoring_loads_the_bundle_once_per_execution`，3 分区） |
  | 每分区 `load_partition` | 子进程 3 + 批次 1 | 子进程 2（runner 自身 + 读回共用 1），批次 0 |
  | 依赖 owner 查找（评测 venv，144 个发行包） | 0.71 s | 0.31 s |

- **偏离与原因。**
  1. 依赖观测没有按"解释器前缀 + 文件 stat"做缓存：每个分区是新进程，进程内缓存无效，跨进程缓存要求一个共享可写位置且不能写 venv；文本查找已把主要开销（逐条 `resolve`）去掉。也没有改用 `packages_distributions()`：原实现明确要求只认 RECORD，启发式会漏掉"同一文件被两个发行包声明"的歧义检查。
  2. 新运行的 `protocol_id`／`pairing_id` 因 identity 去掉两个字段而与旧运行不同；这与评测器源码哈希本来就进 identity 的情况相同（评测器一改就不同），导出族、提交身份与比较身份按投影比较，不受影响。
  3. `verified_bundle` 只经 `RunReadContext` 使用，没有改 `load_bundle` 本身：直接调用 `load_bundle` 的二十多处调用方可能修改返回值，共享对象有风险；`RunReadContext` 一向返回共享对象。
  4. 共享源码树需要与分区运行同一文件系统才能硬链接，否则逐文件复制（与改前同等开销，但仍省去 tar 哈希与重编译）。
- **验收测试。** `tests/test_shared_source_and_compact_identity.py`（6 项）：共享树逐成员核验、预编译、只读，分区内是硬链接（inode 相同），导入来自运行自身路径并复用 `.pyc`，标记不符或文件集合不符拒绝；带共享源码的快照只保存指纹，`product_archive_sha256` 等于 tar 真实哈希，`validate_sources` 通过，`source_manifest()` 从对象取回 manifest；旧 identity（整份 manifest + 常量 audits）与新 identity 投影相同，manifest 与指纹不一致则拒绝，非常量 audits 保留；已存在对象重发布只核验记录；**改动前形状的合成运行目录（整份 manifest、常量 `audits_sha256`、按旧 identity 重算的 protocol/plan/scores）`load_run` 正常，且与新运行投影一致**；runner 保存的阶段指标在输入不变时复用、变了即重算。另改：`test_sn_source_loading` 不带 manifest 的 code 现在是合法新格式（manifest 与指纹不一致仍拒绝）；`test_workbench_job_preparation` 中 manifest 自洽但与成员不符的源码对象改为在执行物化时被拒绝。
- **测试。** 全量 `-n auto`：**2554 passed, 7 skipped，墙钟 77 s**（含真实 SN 离线集成测试）。

### P5 · 2026-10-25 · 评分器身份与评估器源码解耦

- **改动。** `benchmark_official.SCORER_PROTOCOLS` 给五个评分 profile 各一个版本化 `scorer_protocol_id`（初版均为 `<profile>/projection-v1`）；`comparison_scorer_identity` = profile + 协议版本 + 去掉 `bridge` 后的全部依赖（上游官方源码 URL 与 sha、Python 版本、包版本、ROUGE／ALCE 批次／检索依赖等）。`score_prepared` 新写 `scorer_protocol_id`、`scorer_comparison_identity`、`scorer_comparison_id`，旧字段 `scorer_identity`／`scorer_id`（含评估器模块哈希）照写、照验，作披露。`benchmark_comparison` 的必须相等集合改为 `bundle_id`、`suite`、`scope`、`case_ids`、`profile` + 比较身份；不相等时给出原因（协议版本不同，或哪些官方依赖不同）。报告新增 `scorer_comparison_identity`、`scorer_disclosures`（每个方法的 bridge 哈希、格式、旧格式映射、是否用了保存的输入）、`scorer_bridge_differs`。身份分层披露（`build_comparison_identity`）的 scorer 层也改用比较身份。
  - 旧 scores 文件（无 `scorer_protocol_id`）按 `LEGACY_SCORER_PROTOCOLS`（冻结副本，之后升版本不会改它）映射到当时的协议，披露 `legacy-bridge-hash`，不要求重评。
  - prepared inputs：仍先用当前代码重建；若与记录的 `prepared_id` 不同，接受评分时保存在 scores 旁边的 `prepared.json`，条件是其哈希正是 `prepared_id`、profile 与 suite 一致，并披露"保存的输入按哈希核验、当前投影不同"。路径入口（`compare_submissions`）、flip 准入、run 视图与 ingest 都会带上这份文件；冻结快照入口没有该文件，仍要求重建一致。
- **偏离与原因。**
  1. 必须相等集合里只拿掉了 `bridge`（评估器自身模块哈希），没有拿掉 Python 与包版本：这两项此前就比较，删掉会让集合静默缩小。
  2. 冻结快照（`compare_frozen_submissions`、工作台结果比较）不携带 `prepared.json`，投影代码改变后这些快照需要重建一致才能读；要放开需改快照格式，未做。
- **验收（`tests/test_benchmark_comparison.py` 新增 4 项）。** 只改 `benchmark_official.py` 的非评分行（两次评分的 bridge 哈希不同）：仍可比，报告标出 `scorer_bridge_differs` 与两边哈希，比较身份不含 bridge；把一侧协议升为 `projection-v2`：拒绝，原因 "scorer protocol …/projection-v1 vs …/projection-v2"；旧格式 scores 与新格式 v1 可比并披露映射，篡改比较身份被拒；投影代码改变时，缺 `prepared.json` 的 scores 被拒，带哈希一致的 `prepared.json` 则通过并披露，篡改该文件再被拒。原有"bridge 不同即拒绝"的测试改为上述新契约，"Python 版本不同"仍拒绝。
- **测试。** 全量 `-n auto`：**2557 passed, 7 skipped，墙钟 74 s**。
