# 对话交接：Silicon Notebook 评测项目

核实日期：2026-09-23（北京时间）。这是一次对话切换快照，不是新实验报告。用户本轮只要求整理交接，不开展开发或实验。

## 1. 新对话先看这里

- 项目目标：用公开任务评价 **Silicon Notebook 系统**，解释回答、检索、引用和 Agent 各阶段的表现，并用离线 Dashboard 展示数据来源、执行过程和可比结果。
- 当前代码分布在多个 worktree。**Dashboard 最新代码与原生评分恢复代码尚未合并；主目录 `main` 也不是最新实现。** 不要只在主目录阅读后判断功能缺失。
- 本地最近完成的是 Dashboard 地图拖动、缩放及视觉修订，已经推送。服务器另外推进真实模型实验；最新短文档任务尚未收到完成报告。
- 新对话先读个人 `~/.codex/AGENTS.md`、目标 worktree 的 `AGENTS.md`、本文，再按任务查阅对应协议。现有 `evaluation-context.md` / `evaluation-status.md` 保留大量按日期累积的历史，不要把旧段落中的“尚未实现”“禁止修改 SN”“下一步运行 meeting18”当成最新指令。
- 历史上已授权修改独立 SN 评测分支，并已实施。生产主目录的受 Git 管理文件没有本地修改；在线实验主要由公司服务器执行。本轮未授权启动本机 SN、judge、下载、测试、timer 或新实验。

## 2. 实际工作目录、分支与提交

以下由本轮 `git worktree list --porcelain`、`git status --short --branch --untracked-files=all` 和 `git log` 核实。路径缩写仅用于本文：

- `ROOT` = `/home/wabiwabi/silicon-notebook`
- `BENCH` = `/home/wabiwabi/silicon-notebook/benchmark-deepeval`
- 评测远程 = `git@github.com:Xi0ng-Jun/SN_benchmark.git`

| 工作目录（相对上述路径） | 分支 | HEAD | 整理前工作区状态 / 用途 |
| --- | --- | --- | --- |
| `BENCH` | `main` | `0f64dd8a1987ab3568d34959980061d2393ba190` | 干净；相对本地 `origin/main` ahead 10，但仍早于近期开发功能 |
| `BENCH/.worktrees/dashboard-experiment-map` | `feat/dashboard-experiment-map` | `a85fdef804b5f083d8377bc2ea6e110326946213` | 干净；本轮交接文档保存于此，Dashboard 后续工作入口 |
| `BENCH/.worktrees/native-scoring-reliability` | `feat/native-scoring-reliability` | `4d5c68502c6bb4e7f91a7e0a2908c3942d2f95d2` | 受管理文件干净；17 个未跟踪汇报文件；原生评分恢复入口 |
| `BENCH/.worktrees/public-benchmark-agent-expansion` | `docs/public-benchmark-agent-expansion` | `e045114c9aa14e3899633535b0d91c3c450460ff` | 干净；原生 DeepEval 首次交付，早于上面两个分支 |
| `ROOT/project` | `master` | `74e9c61e4600102e5324553b55a85be15339660f` | 受管理文件干净；5 个未跟踪历史产物 |
| `ROOT/.worktrees/sn-evaluation-tracing` | `feat/evaluation-tracing` | `052b73734eec606c5e162d840c90219557665c64` | 干净；SN 观测修改所在位置 |

本轮只读执行 `git ls-remote --heads origin ...`，确认远程三个开发分支分别指向表中对应 HEAD；远程 `main` 为 `8b4b4deed0c3984976c192cc3fe9ce55f2fa282a`。没有 fetch、切分支、合并、提交或推送。

两个当前开发分支共同祖先为 `e045114c9aa14e3899633535b0d91c3c450460ff`，之后分别有：

| 分支 | 独有提交 | 意义 |
| --- | --- | --- |
| Dashboard | `4d330ae`、`a85fdef` | 实验地图重构、地图交互及说明修订 |
| 原生评分恢复 | `4d5c685` | 逐项分数持久化、指标选择、已保存组件独立补评 |

因此，Dashboard 分支**不包含** `score_native_components.py` 等恢复实现；恢复分支**不包含**新地图。若后续任务确实需要两者，应先在合适分支整合，保留双方修改；本次没有代为合并。服务器可能已有自己的合并提交，不能把本地 SHA 当作服务器当前 SHA。

## 3. 未提交和仅本地工件

### 原生评分 worktree：17 个未跟踪文件

目录：`BENCH/.worktrees/native-scoring-reliability/docs/presentation/`。

```text
NotoSansSC-OFL.txt
README.md
sn-agent-evaluation.drawio
sn-agent-evaluation.pdf
sn-evaluation-agent-architecture.png
sn-evaluation-agent-architecture.svg
sn-evaluation-agent-scoring.png
sn-evaluation-agent-scoring.svg
sn-evaluation-agent-span.png
sn-evaluation-agent-span.svg
sn-evaluation-flow.drawio
sn-evaluation-flow.pdf
sn-evaluation-overview.png
sn-evaluation-overview.svg
sn-evaluation-presentation.drawio
sn-evaluation-sequence.png
sn-evaluation-sequence.svg
```

这些是用户要求的可编辑汇报图：两页系统流程、三页 Agent 实现、五页合集及预览。它们已经制作，但**没有进入 Git，服务器仅拉取仓库得不到它们**。不要清理或误当成临时测试垃圾；后续如需同步应单独纳入提交。此处只记录，没有替用户提交。

### SN 主目录：5 个未跟踪文件

```text
.deepeval/.deepeval-cache.json
.deepeval/.latest_run_full.json
.deepeval/.latest_test_run.json
results/domain-goldens-current.jsonl
results/domain-goldens-current.rejected.json
```

未读取其正文，也未移动、删除或纳入提交。SN 主目录没有 staged / unstaged 的受管理代码变更。

### Dashboard 本地演示

`BENCH/.worktrees/dashboard-experiment-map/var/dashboard-map-preview-20260923/report/dashboard.html` 为此前构造数据演示，截图在相邻 `map-preview.png`。`var/`、运行目录、虚拟环境等忽略项不等于已经上传；演示也不是实际 SN 成绩。本次 Git 清单不是所有忽略文件的完整备份清单。

### 本轮新增的未提交文档

本轮在 Dashboard worktree 新增本文，并在 `docs/evaluation-status.md` 顶部添加交接入口。这两项在本次结束时保持未提交；其余工作区内容不变。

## 4. 已完成实现与阅读入口

### 公开任务、SN 运行与常规对照

- 早期十套公开评测：SQuAD、DROP、BoolQ、LogiQA、IFEval、MMLU、GSM8K、TruthfulQA、HellaSwag、BBH。数据选择及 Native 模型参照 / SN Product 适配的范围见[选题方案](public-benchmark-selection-plan.md)和[指标实现表](benchmark-metrics-reference.md)。这些轨道不是同一种实验，不能合成一个总成绩。
- 后续 Notebook 场景重点：QASPER、QMSum、MultiHop-RAG、ALCE（当前主要实验是 ASQA；也有 QAMPARI/ELI5 适配）。它们是独立公开研究数据集，不是四个 DeepEval 内置 Benchmark 类。见[Notebook 协议](notebook-benchmarks.md)。
- 按完整任务资料组织隔离 notebook/runtime，参考答案和证据标注留在评分侧。QASPER 按论文、QMSum 按会议、MultiHop-RAG 用完整 corpus、ALCE 按单题完整候选集；不能用统一文档数上限拆散一道题所需资料。
- SN chunk/reasoning 使用后端业务接口及隔离运行配置，不是通过生产 UI 逐题点击。问答实际保存的输入、上下文、答案和引用供后续评分、诊断与展示。
- QMSum 已有 BM25 turn 检索加显式生成模型的对照、同题比较与答案独立重评分。见[三条路径逐步说明](qmsum-three-paths-walkthrough.md)、[BM25](qmsum-bm25-baseline.md)、[答案重评分](notebook-rescoring.md)。不要把 BM25 叫作 SN 的第三种内部模式。

### 原生 Agent 评测

当前协议是 `sn-deepeval-native-v1`，固定 DeepEval `4.2.2`。SN 的可选封装使用公开 `observe` / `update_current_span`，SDK 管理 span 树；评测运行器使用逐题串行 `EvaluationDataset.evals_iterator()`，在 judge 前保存回答及组件。旧私有 `_trace_dict` 注入和手工回灌路径已退役，不为旧轨迹另造迁移层。

| 对象 | 记录与评分 |
| --- | --- |
| 检索 | 实际 query 与该次返回文本；ContextualRelevancy |
| 合成 | 实际问题、真正送入合成的上下文及答案；Faithfulness、AnswerRelevancy |
| 完整请求 | 原生执行轨迹；显式开启 TaskCompletion、StepEfficiency |
| 有显式计划的 reasoning | 计划、修订和动作；PlanQuality、PlanAdherence；chunk 无计划不扣分 |

多查询保留逐查询对应关系，分节合成保留各次输入输出。轨迹是可观察业务调用与模型输入输出，不是模型隐藏思维链。组件分不能替代完整 Agent 分，原生接入也不自动解决超窗、超时或不完整 JSON。

代码入口：SN `backend/app/core/evaluation_tracing.py`、`services/evaluation_trace_projection.py` 及 Ask/检索/reasoning/LLM 埋点；评测 `scripts/run_notebook_agent.py`、`src/rag_eval/native_agent.py`。详细契约见[原生协议](native-agent-evaluation.md)。

恢复分支还提供 `scripts/score_native_components.py`、`src/rag_eval/native_component_scoring.py`、`native_metrics.py`：从已保存组件用公开 `LLMTestCase/evaluate` 独立评分，逐项落盘，记录来源哈希、judge 身份和调用事件。恢复说明仅存在于 `feat/native-scoring-reliability` 的 `docs/native-scoring-recovery.md`；不要在本分支误找。完整轨迹不通过私有字段离线回灌。

### SN 更改如何通过评测仓库交付

[补丁目录](../integrations/silicon-notebook/manifest.json)随评测代码发布，包含 `0001-feat-native-deepeval-evaluation.patch`、manifest、说明和跨仓库检查。

- SN 实现提交：`052b73734eec606c5e162d840c90219557665c64`。
- 补丁基准：`1b4eb2b3b0e7db95353c9128bb9f6bfcf37707cb`（旧 tracing 已应用）。它是**增量补丁**，不能直接套到未打旧补丁的生产分支。
- 本轮重新计算补丁 SHA256：`b2baf1beea74ec77c4d84fb3cd814b2f2033bd0d17fd48ed722cc7d6bebb6f25`，与 manifest 一致。
- 服务器需保留自己的模型配置和既有修复，在独立 SN worktree 应用；已经应用则不要重复打补丁。SN 分支没有本轮确认过的直接远程推送，交付渠道是评测仓库补丁包。

### Dashboard

已实现三个视图：**实验地图**看来源、生成、答卷和重评分关系；**单题回放**看数据和真实 span；**结果分析**按动态标签筛选、比较配置与共同有效题。它只读已有工件，不重新算实验分数。主要入口是 `scripts/build_experiment_dashboard.py`、`explorer_artifacts.py`、`explorer_steps.py` 和 `src/rag_eval/dashboard/`。

最近 `a85fdef` 修复地图无法拖动和裁切，增加鼠标/触摸平移、缩放、显示全部、定位所选、展开及键盘操作；采用白底细线和蓝色选择强调，补充地图含义和节点配置说明。没有修改评分逻辑。

用法见[Dashboard 指南](experiment-dashboard.md)。更新代码后要重新生成报告，旧 HTML 不会自动更新；分享要带整个输出目录及 `details/`。独立组件补评目录是否可被 Dashboard 直接接纳，不能仅凭两个分支各自存在就宣称已支持，应以实际读取协议为准。

## 5. 验证证据：本轮核实与历史验证分开

**本轮实际做了：**读取约定和状态文档、检查全部六个 worktree 的 Git 元信息与未跟踪清单、比较两个分支的独有提交及共同祖先、只读核实远程分支头、核对补丁 SHA256；写入后检查文档链接和差异。没有重新运行以下测试、构建、浏览器、SN 或 judge。

| 历史本地验证 | 当时记录 | 证据与适用边界 |
| --- | --- | --- |
| 原生 DeepEval 首次交付 | benchmark/配对检查 419 passed；SN 最终定向 32 passed；网络尝试 0 | [交付记录](sn-execution-tracing-validation.md)；真实 SDK、替身业务/judge，不是模型实验 |
| SN 标准 gate | 后端 12762 passed、1 failed；contracts 54 passed；前端 Node 2730、组件 1187 passed | 同一交付记录；已有打包迁移测试的 dotenv 环境失败，不能说 gate 全绿 |
| 评分恢复 | 432 passed、0 skipped、网络尝试 0 | 恢复分支 `docs/native-scoring-recovery.md`；包含逐项保存、超时后迟到结果与补评，不证明网关可用 |
| Dashboard v3 重构 | 433 passed、1 skipped；浏览器 smoke | 本分支 `docs/evaluation-status.md`；构造数据，非真实大规模服务器验收 |
| 最新地图修订 | 28 项 Python、34 项 JS；Chromium 交互与 312 个构造节点，页面错误/HTTP 请求 0 | [Dashboard 指南](experiment-dashboard.md)；此前本地验证，本轮未复跑 |
| 可编辑汇报图 | XML 模型/浏览器渲染与人工查看排版 | 恢复 worktree `docs/presentation/README.md`；图解实现，不代表评分成功 |

历史状态页中某些提交号或“服务器尚未验收”是当时记录，不覆盖本文核实的 Git 身份和以下较新的服务器反馈。

## 6. 服务器实验：全部来自用户转交，未本机复算

本轮没有连接公司服务器，没有检查其进程、原始日志、配置或产物。以下是会话内汇报，不能当作本机验收事实，更不能据最后一次“正在运行”断言此刻进程仍活着。

| 阶段 | 最近已知结果 | 保留的解释边界 |
| --- | --- | --- |
| Notebook 数据准备 | 服务器报告 QASPER v2 1120 题/391 分区；QMSum 原始 35 场/281 题及 17 个空 turn；MultiHop-RAG 完整 corpus；ALCE-ASQA 已准备 | 数量为服务器文件口径；未在本机下载重算，不按论文数量强行删题 |
| QMSum request-v1 | chunk 281 success；reasoning 132 success、148 clarification、1 error | 旧 0.246/0.235 均分来自不同有效题集，不能作同题结论；修订报告共同 132 题为 0.2506/0.2427 |
| QMSum request-v2 | 两模式各 281 success、0 clarification；分区均值 ROUGE-1 0.268/0.216；281 共同题 reason−chunk 约 −0.0522 | v2 是评测提问包装版本，不是 SN 模型版本；消除澄清是该批结果，不是保证所有请求都无澄清 |
| QMSum BM25 对照 | 35 分区完成；general ROUGE-1 BM25 0.2688 / chunk 0.2913 / reasoning 0.2553；specific 0.2829 / 0.2727 / 0.2180 | `model_alignment=not_verified`；是系统对照，不能把差值全归因于检索，也没有显著性结论 |
| 旧采集器 Agent 分 | meeting18 chunk 12 项、reasoning 24 项 DeepSeek 评分已完成；GLM 完整轨迹超窗记录保留 | 旧协议归档，不混入新版原生结果；字节数不能充当精确 token 数 |
| 原生最大题 | chunk `specific:3` 有 Faithfulness 0.7、AR 0.89、TC 0.95、SE 0.75；两份检索 CR 超时；reasoning 回答/组件/轨迹保存，评分未完整完成 | 答案成功与 judge 成功是两件事；网关 180s 与 SDK 任务时限也需分开 |
| 已保存组件补评 | DeepSeek reasoning AR 0.85；chunk CR 9 次成功、下一次 HTTP 500 整项失败；Faith 首次调用失败；补齐耗时约 180s | 只说明相应批次失败；没有证据证明该 benchmark 或模型永久不可评 |
| GLM Faith 排查 | 后续报告 raw JSON 缺最后闭合 `}`；truths 元素类型本身合规 | 先前“schema 类型不匹配”说法已更正。响应不完整有依据；究竟模型、token 设置、客户端或网关何处导致截断，尚无独立充分证据 |

需要继续保留 QMSum request-v2/BM25 答案和客观分、失败记录及配置身份。旧 Agent 分只作历史；不同 judge、协议、prompt 和重评分批次分开。error / N/A / 未执行不补零；不挑最好的一次覆盖旧结果。turn 证据覆盖率提高本身不证明检索更精确，还受返回资料量影响。

### 最近交给服务器的工作

1. **短文档链路验收。** 长 QMSum 评分反复遇到网关/JSON 问题后，讨论转用较短 QASPER 样本走通功能；用户最近表示服务器仍在跑上一项任务。此处没有最终报告、精确题单、当前提交、run-dir 或进程状态；新对话应承接其已有任务与结果，不能擅自另起同一批或再默认回到 QMSum 全量。
2. **已完成结果的新版 Dashboard。** 用户要另一个服务器对话在独立 checkout 只读已有可用结果，不扰动运行实验。最新地图提交 `a85fdef804b5f083d8377bc2ea6e110326946213` 已推送，也已给出拉取/独立 worktree 与重新生成报告的指令；尚未收到该次新版地图的服务器验收报告。
3. **服务器自有修复。** 此前曾有 `--model-config` 注入、并发竞态、`finished_with_errors` 和模型配置修复。用户报告它们存在，但本机未获得全部对应补丁，不宣称已经集成，也不得在服务器同步时覆盖。

本地 `docs/server-agent-tracing-prompt.md` 及恢复说明保留历史阶段指令，不是要求现在从头重跑所有步骤。最新服务器路径和完成状态应向实际产物核实；本次未替用户向服务器发消息。

## 7. 下一步与明确边界

本轮交接到此结束。后续优先顺序是：

1. 用户提供服务器短样本结果后，核对该批实际运行身份、答案/组件/整体分哪些存在，从真实案例解释 SN 表现；不先开展又一轮泛化审计或重跑成功步骤。
2. 服务器用最新 Dashboard 显示已完成、身份明确的结果，确认地图、单题过程和对比是否便于导师理解。补评独立目录的接入若不支持，先明确这一具体缺口，不改数据伪装成常规 run。
3. 若下一项开发需要统一代码基线，再整合 Dashboard 与评分恢复分支；这是尚未执行的集成工作。汇报图如需远程同步，另外提交 `docs/presentation/`，不要漏掉未跟踪文件。
4. 由具体结果决定是否处理 judge 链路、添加功能或扩大规模。无需为了保旧代码增加复杂迁移层；必要重跑可以讨论，但不能自动重跑全部历史问答。

持续边界：隔离 notebook/runtime、数据和生产配置；gold 不进资料库；记录实际生成模型与独立 judge；不自动裁剪轨迹或用摘要冒充原轨迹；不按低分重试、不补零、不设置未经人工校准的发布门槛；不恢复 weekly timer；当前不扩展新 benchmark、DAG、KG、PDF/OCR 或用户此前排除的交互可靠性/资料更新一致性专项。

用户偏好：用大白话解释对象与接口、配合真实数据和可编辑图；减少反复确认与无止境验证，按有证据支持的下一步推进。不同任务的已有授权应保留，但本次交接本身不授权任何新实验。

## 8. 新对话启动文本

```text
继续 Silicon Notebook 评测项目。先阅读 ~/.codex/AGENTS.md，以及：
/home/wabiwabi/silicon-notebook/benchmark-deepeval/.worktrees/dashboard-experiment-map/docs/session-handoff-2026-09-23.md

按交接记录选择实际 worktree，读取该目录的 AGENTS.md、evaluation-context/status 和任务对应协议，并核实 Git 状态。注意 Dashboard 与原生评分恢复目前是不同分支，汇报图尚未提交；不要把主目录 main 当作最新代码。区分历史本地测试与服务器转述，不重复启动正在交给服务器执行的任务。

本次具体任务：［填写要继续解决的问题，或附服务器的新汇报］。
```
