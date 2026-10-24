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

## 不做

不动模型客户端的超时与退避策略（60 s、2^n）——这是 SN 项目的配置，记录观察即可；不引入新的存储后端；不改 LCD 的门、簇、划分语义；不为了性能放松任何不变量。

## 执行记录

（开发 agent 按工作项追加：日期、提交、测试与性能验收数字、偏离与原因。）
