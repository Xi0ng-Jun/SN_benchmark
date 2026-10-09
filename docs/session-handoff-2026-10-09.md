# Silicon Notebook 评测项目交接

核实日期：2026-10-09（北京时间）。本文是交接快照，供新 Codex agent 在本 worktree 继续工作。只描述已核实的 Git、代码、文档和会话决策；服务器进程、服务器 run 和成绩均需以服务器实际工件重新核实。

## 1. 工作目录、分支和 Git 状态

目标 worktree：

```text
/home/wabiwabi/silicon-notebook/benchmark-deepeval/.worktrees/benchmark-protocol-correctness
```

已核实状态：

- 分支：`feat/benchmark-protocol-correctness`
- HEAD：`dbc5c73ec7d2e574ba057867fd2e95aae3ca3327`
- 最新提交：`perf: share immutable benchmark artifacts and streamline result exports`
- 远程分支：`origin/feat/benchmark-protocol-correctness` 与该提交一致
- 工作区：干净，无未提交、未跟踪改动
- 远程仓库：`git@github.com:Xi0ng-Jun/SN_benchmark.git`
- 前一关键提交：`bcea0c5`，`refactor: remove retired benchmarks and isolate shared evaluation modules`
- 更早服务器常见基线：`86addcf`。`dbc5c73` 包含 `86addcf` 之后的清理和本次导出改造连续历史，不要只 cherry-pick 最后一个提交到旧基线。

本次任务要求新 agent 继续在这个 worktree 直接工作。用户明确要求本交接文档以外不要修改文件；本交接动作没有提交或推送。

## 2. 项目目标与背景

项目目标是正确评测 Silicon Notebook，并支持可追溯的 benchmark 比较、证据/检索/Agent 诊断和 Dashboard 展示。当前只维护五套 benchmark：

- QASPER
- MultiHop-RAG
- ALCE
- QMSum
- HotpotQA

本机只做代码、文档、无模型离线测试和合成工件检查。真实 SN 运行、生成模型、judge、官方模型评分和大规模磁盘性能测量在服务器执行。当前服务器实验范围是 SN-only：五套 chunk，另加 MultiHop reasoning；外部方法、reference、BM25 和专门 Agent judge 任务不由本次存储改造自动启动。

本阶段的背景问题是：旧 run 反复复制 frozen benchmark 输入和源码快照，导出又会重新完整读取/适配数据，结果回传还可能包含不必要的 runtime、配置、日志和完整重复输入。目标是减少不可变重复数据、精简正式答卷、保留完整权威观测，并让回传包可复核。

## 3. 已完成的两轮代码工作

### 3.1 `bcea0c5`：代码精简和退役清理

已经完成：

- 删除五套之外的旧 benchmark 专属代码、CLI、配置、样本、结果和文档。
- 将仍需保留的通用身份、runtime、模型适配、结果账本、报告、Dashboard 和评分职责迁到独立模块。
- 更新 `AGENTS.md`、`CURRENT_STATE.md`、`RUNBOOK.md`、`ARCHITECTURE_CURRENT.md`、`TRIAGE.md`、`DELETION_LOG.md` 及文档导航。
- 不为旧 benchmark 恢复 reader、测试或兼容壳；不要为了旧测试重新引入退役套件。

### 3.2 `dbc5c73`：共享存储、精简导出和回传

已完成的主要实现：

- `src/rag_eval/artifact_store.py`：内容寻址、哈希校验、并发安全发布、引用解析、源码/输入对象角色校验。
- `src/rag_eval/bundle_index.py`：canonical bundle 安装、partition capsule、v1/v2/v3 request revision、capsule 与 bundle 身份绑定、固定 `artifact-index-id` 检查。
- `src/rag_eval/run_reader.py`：共享 `RunReadContext`，一次操作内复用 canonical rebuild、case/document/partition index 和对象校验。
- `src/rag_eval/scoring_projection.py`：五套 benchmark 的 compact scoring projection。完整记录先验证，再按官方 scorer 所需字段投影。
- `src/rag_eval/benchmark_submission.py`：复用读取上下文、保留 outputs 哈希、避免二次方 case 查找、拒绝 ledger 在导出期间变化。
- `src/rag_eval/result_package.py` 和 `scripts/package_benchmark_results.py`：`results`/`review` allowlist 包、logical/allocated inventory、私有权限、共享依赖去重、解包校验和回传 receipt。
- `scripts/prepare_notebook_benchmarks.py`：支持 `--install-bundle` 和 `--artifact-root`；新 shared run 还必须传冻结的 `--artifact-index-id`。
- Notebook runner、Agent runner、QMSum baseline、Dashboard、补评和 ALCE attachment 已接入共享输入/源码引用。
- 共享模式保持每个 run 独立的数据库、storage、配置、日志和逐事件 fsync；不合并可变 runtime。

## 4. 当前新旧数据对象差异

旧方式通常是每个 run 复制：

```text
run/input/                 # 整套 frozen bundle 的物理副本
run/source/                # evaluator 源码副本
run/product-source.tar     # SN 源码 archive
run/runtime/               # 独立数据库、storage、日志、配置
run/product-bundle.json    # 本 partition 的完整公开请求
run/planned.jsonl
run/outputs.jsonl          # 完整答案、证据、检索和状态
run/scores.jsonl
```

新 shared run 期望：

```text
campaign/artifacts/objects/{bundle,index,evaluator,sn}/<object-id>/
campaign/artifacts/bundle-indexes/<bundle-id>.json
campaign/runs/<run-id>/artifact-refs.json
campaign/runs/<run-id>/manifest.json
campaign/runs/<run-id>/product-bundle.json
campaign/runs/<run-id>/planned.jsonl
campaign/runs/<run-id>/outputs.jsonl
campaign/runs/<run-id>/scores.jsonl
campaign/runs/<run-id>/runtime/       # 仍逐 run 独立
```

共享的只有不可变 bundle、partition index/capsule 和源码快照；`product-bundle.json`、完整 outputs、scores、runtime、Agent 工件、数据库和日志仍属于各 run。compact submission 只用于正式评分/回传，不能替代完整 outputs 或补评输入。

旧 run 仍可读，但**当前代码没有旧 run 迁移工具**。已有 `run/input` 不会仅因 checkout 更新就变成 `artifact-refs.json`；旧物理副本仍要逐份实际校验。不得手工给旧 run 加 refs、修改 manifest 或跳过校验。

## 5. 已做出的关键决策及理由

1. **生成不见 gold。** bundle 保留评分侧 gold，v3 product request 只给生成所需公共资料和请求；capsule 必须与 canonical `partition_bundle` 公共请求等价。
2. **完整观测优先。** QASPER/Hotpot 的 response、captures、evidence snapshot；MultiHop 的真实 ranking capture；ALCE anchors、source mapping 和错误；QMSum answer/status/failure 必须保留。不能为缩小答卷而删除 scorer 必需字段。
3. **完整记录与正式答卷分离。** 原始 outputs 是权威事实；submission 是由完整记录验证后得到的 compact projection。错误、missing、clarification、no_answer 和分母语义不改。
4. **共享不可变，runtime 隔离。** 共享 bundle/source 可节省重复存储；数据库、storage、日志、配置继续按 partition × mode × attempt 隔离，避免改变生命周期和恢复语义。
5. **正式导出做 canonical audit。** 单 run bounded report 可标注 `partition-capsule`，不能冒充官方验收；正式 export、Dashboard/聚合等仍需完整 canonical 验证。
6. **固定安装身份。** shared run 必须提供 installer 输出的 `--artifact-index-id`；不允许每次从可变 pointer 自行选择新 index。capsule 文件哈希与 bundle object identity 绑定。
7. **不自动删除。** `results` 包和 `review` 包都不包含 runtime、密钥配置或原始服务日志；没有自动 TTL、GC 或旧 run 删除命令。
8. **旧结果不用重跑生成。** 若旧 run 的答案、证据和账本完整，可以重新读取/导出；但旧输入副本的首次迁移/校验仍可能很慢。

## 6. 当前已完成的验证

最近完整离线验证：

- Python：`685 passed, 1 skipped`；skip 是未配置本地官方 Perl ROUGE。
- Node：`34 passed, 0 failed`。
- 独立 storage/index/projection/submission/package/ALCE 审查：`162` 项相关检查通过。
- `git diff --check` 和 Python compileall 通过。
- 合成 1k/5k/10k 导出：每次 canonical rebuild 1 次；review 包迁移读回题数一致；无模型/SN/judge 调用。
- 针对 capsule 替换、bundle/index 同时替换、源码身份不匹配、store 路径重叠、缓存跨 partition 污染、包解包和 runtime 排除均有反例测试。

这些是本机代码和合成工件证据，不是服务器实验结果、正式成绩或服务器性能结论。Birdview 当前结果：revision 2 活动已闭合；revision 3 实际架构快照已生成并通过结构校验。浏览器视觉复核因缺少系统库未执行。

## 7. 未完成事项

### 7.1 高优先级：服务器当前 Hotpot 导出

用户最近转述服务器状态：Hotpot 有 7,405 个 run 正在导出，已剔除 37 个重复 case；旧导出 PID 为 `2657613`，已读约 110 GB / 约 1.49 TB，预计还需约 17 小时。必须到服务器核实 PID、命令、父子进程、实际输出和 run 清单，不能把转述当实时事实。

用户已表示希望停下旧 Hotpot 导出。正常中断前先确认它是只读 export 进程；不要误停生成、评分或其他任务，不用 `kill -9`，不删已有部分输出。

### 7.2 旧 run 迁移工具尚未实现

用户现在希望释放空间：已有实验按新方式整理，验收后删除重复旧输入/源码副本。迁移工具必须：

- 在共享 store 安装并验证旧 run 的 bundle/evaluator/SN source；
- 原 run 路径保留，写入 `artifact-refs.json` 或生成可明确关联的新 run 表示；
- 保留答案、证据、planned/scores、runtime、Agent、补评、ALCE attachment 和完整历史身份；
- 迁移有事务/中断恢复/幂等记录；
- 迁移前后 reader/export/package/证据回放等价；
- 删除前再次读回并确认共享引用可用；
- 只删除已被共享对象替代且有哈希证据的旧 `input/`、`source/`、`product-source.tar`；不删除整个 run、runtime、outputs、scores、官方成绩或原始来源；
- 每项删除记录旧路径、哈希、替代对象身份和实际 logical/allocated 字节；
- 删除后测量实际释放空间，区分逻辑字节和 filesystem allocated bytes。

不要先写删除脚本再补校验。先实现迁移和 dry-run/manifest，使用合成和少量真实完成 run 验证，再允许按冻结清单删除。当前工作区没有这个工具，不能在服务器直接执行 `migrate` 命令。

### 7.3 QASPER/QMSum 已完成导出

用户转述：

- QASPER：answer_f1 0.3147，evidence_f1 0.4745，1451/1451。
- QMSum：Perl ROUGE1 0.2775，ROUGE2 0.0904，ROUGE-L 0.2425，281/281。

需从实际 submission、scores、scorer audit 和 run 工件核实。旧成绩有效时不需要为迁移重跑模型或重新评分；可新建 compact submission/pack，但旧 submission、官方 score 和审计必须原样保留。QASPER 旧 evidence recovery 可能依赖 `runtime/database.db` 与 `product-artifacts/document-map.json`，因此不能删除这些对象。

## 8. 下一步具体行动

### 服务器侧（用户已授权迁移/删除，但须按顺序执行）

1. 核实并正常停止旧 Hotpot export，保存命令、日志、进度、选定 run 清单和部分输出。
2. 在独立 checkout 检出 `dbc5c73`，验证 `rag_eval.__file__`、Python 环境、依赖和官方 scorer。
3. 盘点 campaign：run 布局、legacy/shared 数量、input/source/runtime/outputs/Agent/official bytes、可用空间和正在写入的目录。
4. 实现迁移工具和迁移 manifest，先 dry-run；不要改变旧 manifest 的实验身份。
5. 用合成工件和少量真实已完成 run 验收：文件哈希、公共 request、scorer 输入、证据回放、状态/分母、补评/attachment/Agent 关联。
6. 小批量迁移 Hotpot、QASPER、QMSum，读回并生成新 compact export；确认结果和身份关系后才删除旧 input/source/archive。
7. 对 Hotpot 全部 7,405 个计划 case 使用固定 run 清单全量导出；37 个重复 case 的处理依据必须有记录，不能按答案/分数选择。
8. 迁移其余已完成 run；生成 `results` 包，必要时再生成 `review` 包。不要把整 campaign tar 回传。
9. 汇报迁移成功/例外数、删除清单、实际释放空间、读写/I/O/RSS、导出时间、逐题覆盖和官方分母。

### 本机侧

若服务器 agent 报告迁移工具缺陷或等价性失败，在本 worktree 新建实现并走 TDD/审查；不要为了服务器方便手工修改共享引用。任何实现改动都需重新跑相关离线测试并记录提交/推送。

## 9. 约束和常见坑

- 不在本机运行 SN、模型、judge、真实官方模型评分或下载大数据。
- 不恢复退役 benchmark，不为旧测试添加兼容层。
- 不把历史服务器转述写成当前事实；读取实际工件和进程。
- 不改生产数据库、服务配置、密钥或 timer。
- 不把 compact submission 当作完整 run；不删除 QASPER/Hotpot 必要 captures/snapshots。
- 不按成功题重试、低分选择或重复 case 的文件枚举顺序改变 scope。
- 不把上下文覆盖诊断冒充 evidence F1，也不把组件分冒充完整 Agent 分。
- 不用 `validated=true`、mtime 或旧 receipt 跳过实际哈希校验。
- 不把 `artifact_root` 放在 run、bundle、code/project 内；不将输出写入 immutable store。
- shared run 的运行入口要同时提供 `--artifact-root` 和 installer 冻结的 `--artifact-index-id`；旧 export CLI 不支持伪造这个参数。
- QMSum baseline 仍可能完整读取 raw turns；SN bounded startup 的性能不能泛化到所有方法。
- QASPER 旧 evidence recovery 可能需要 runtime SQLite 只读访问；迁移或删除前要检查依赖。
- 删除是不可逆操作：只能依据冻结清单和逐 run 验收收据执行；出错时停止删除，不用通配符 `rm -rf`。
- 当前 branch/worktree 是评测仓库，不是 Silicon Notebook 产品仓库；不要把 SN 代码修复误提交到这里。

## 10. 相关文件和文档路径

入口和状态：

- `AGENTS.md`
- `CURRENT_STATE.md`
- `RUNBOOK.md`
- `ARCHITECTURE_CURRENT.md`
- `TRIAGE.md`
- `DELETION_LOG.md`
- `docs/evaluation-context.md`
- `docs/evaluation-status.md`

本次存储/导出：

- `docs/result-storage-and-export.md`
- `docs/result-storage-export-verification-2026-10-08.md`
- `docs/server-result-storage-export-prompt.md`
- `docs/server-upgrade-running-export-prompt.md`
- `docs/superpowers/specs/2026-10-08-result-storage-export-design.md`
- `docs/superpowers/plans/2026-10-08-result-storage-export.md`
- `src/rag_eval/artifact_store.py`
- `src/rag_eval/bundle_index.py`
- `src/rag_eval/run_reader.py`
- `src/rag_eval/scoring_projection.py`
- `src/rag_eval/result_package.py`
- `scripts/package_benchmark_results.py`
- `scripts/measure_result_storage_offline.py`

执行与补评：

- `src/rag_eval/notebook_runner.py`
- `src/rag_eval/notebook_baseline_runner.py`
- `src/rag_eval/notebook_rescoring.py`
- `src/rag_eval/notebook_alce_results.py`
- `src/rag_eval/runtime_environment.py`
- `src/rag_eval/benchmark_submission.py`
- `src/rag_eval/run_report.py`
- `src/rag_eval/qasper_evidence.py`
- `scripts/run_notebook_benchmarks.py`
- `scripts/run_notebook_agent.py`
- `scripts/run_notebook_baseline.py`
- `scripts/benchmark_protocol.py`

## 11. 子 agent 工作结论

本轮使用的子 agent 名称与结论如下。它们没有提交或推送代码；主 agent 复核后才纳入当前提交。

### Pauli（compact_export）

- 实现并测试 `scoring_projection.py`、submission/export 的 compact projection 和读取上下文复用。
- 确认五套评分所需字段保留：QASPER/Hotpot evidence captures，MultiHop retrieval ranking，ALCE anchors/mappings/errors，QMSum answer/status。
- 确认 full record 在过滤前校验，拒绝 gold、凭据、非有限 JSON；保留 run_id 和 outputs hash。
- 修复两个旧测试假设：QASPER fixture 改为真实 run/input；MultiHop fixture 从 canonical request 重建 capture request。
- 报告 145 项 targeted tests 通过，最终完整 Python 回归由主 agent 复核为 685 passed、1 skipped。

### Dirac（result_package）

- 实现 `result_package.py` 和 `package_benchmark_results.py`。
- 提供 results/review 两种 allowlist 包、inventory、私有权限、凭据键检查、路径/符号链接/依赖校验、共享对象去重和迁移后 readback。
- 复核后加入一次读取上下文和对象验证缓存，避免重复复制/哈希；runtime 内容只统计不读入包。
- 合成四个 shared run 的 package 测量：1k/5k/10k 每 run 的 package 时间约 0.547/2.092/3.898 秒，review archive 约 273,748/1,191,667/2,304,810 字节（具体条件见验证文档）。
- 未运行 SN/模型，未提交。

### Nietzsche（projection_review）

- 独立审查发现并推动修复：partial reader cache 不能跨 partition 串用；源码对象必须与 run code identity 精确匹配；capsule 必须与 canonical request 等价；输出不能写入共享 immutable store。
- 进一步发现并推动固定 installer 的 `artifact-index-id`，防止同时替换 bundle/index 后仍构造自洽伪 capsule；现在 shared runner 需要显式 pin index ID。
- 确认 package 外部 store 重叠写入会破坏 immutable object，已加入发布路径拒绝。
- 最终独立审查结论：当前审查范围内没有剩余重大问题；162 项相关测试通过。

### 多 agent 协作边界

- 所有 agent 共享同一 filesystem；子 agent 的修改会立即出现于 worktree，不能仅依据 agent 报告判断完成。
- 主 agent 负责查看 `git diff`、运行测试和确认最终提交；本次交接前状态已由 `git status` 核实干净。
- 如果重新启用子 agent，先为每个 agent 划清文件范围，避免同时编辑同一模块。

## 12. 新 agent 启动建议

新 agent 先执行：

```bash
cd /home/wabiwabi/silicon-notebook/benchmark-deepeval/.worktrees/benchmark-protocol-correctness
git status --short --branch
git log -3 --oneline --decorate
cat AGENTS.md CURRENT_STATE.md
```

然后根据用户最新目标读取相关文档。若要开发旧 run 迁移，先做设计和 dry-run 清单，不要直接删除；需要改变代码时沿用现有 TDD、离线验证、独立 review 和服务器 only 边界。
