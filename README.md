# Silicon Notebook RAG Benchmark + DeepEval

本轮 SN 主实验及后续对照的执行口径见 [Notebook 实验计划](docs/notebook-benchmark-experiment-plan.md)。

**2026-09-18 新增 QMSum 常规 RAG 对照：** BM25 发言检索 + 显式生成模型，复用冻结数据和评分器；支持 Dashboard 查看及独立同题比较。见[用法与比较边界](docs/qmsum-bm25-baseline.md)。已做离线合成测试，尚未执行真实模型验收。

已新增[Notebook 独立重评分](docs/notebook-rescoring.md)：读取已保存答卷生成新的评分批次，不重新调用 SN Ask 或生成模型，原运行目录保持只读。

已新增可选的 [SN Agent/DAG 离线评测](docs/superpowers/specs/2026-09-19-agent-dag-evaluation-design.md)：默认读取已有 `outputs.jsonl`，输出轨迹完整度、reasoning 动作诊断和 chunk/reasoning 配对信息；只有显式 `--judge` 才调用 DeepEval trajectory metrics，`--dag` 才运行产品证据路径 DAG。该命令不重新启动 SN、不覆盖原评分：

```bash
python scripts/evaluate_agent_traces.py \
  --run-dir /path/to/existing-run \
  --output-dir /path/to/agent-report
```

完整轨迹的 DeepEval 评分必须显式开启；当前 SN reasoning 输出通常会被保守标记为 `partial`，因此默认报告只做确定性诊断。

**2026-09-17 新增资料型评测接入：** QASPER、MultiHop-RAG、ALCE、QMSum；支持完整资料分区、隔离 SN Ask、独立指标及 Dashboard。见[实现与服务器命令](docs/notebook-benchmarks.md)。本地仅用构造数据验证，尚未下载真实数据或执行新实验。

实验完成或阶段性运行后，可用离线 Dashboard 汇总已保存结果：

```bash
python scripts/build_experiment_dashboard.py \
  --runs-root /path/to/runs \
  --output /path/to/reports/dashboard
```

打开新生成的 `dashboard.html`，可以组合 Benchmark / Task / Track / Mode / Scorer / 状态等标签，联动查看状态图、分数分布和分面均值；点击条目追查题目、SN 回答、上下文、引用与评分依据；保存多个筛选组进行可比结果分析。旧 HTML 需要重新生成才能使用新界面。该命令只读结果文件，不启动 Silicon Notebook、不调用模型、不重新评分。

[Dashboard 操作、比较规则与服务器 Agent prompt](docs/experiment-dashboard.md) · [十套 Benchmark 的轨道与指标实现四列表](docs/benchmark-metrics-reference.md)。输出需用运行目录之外的新目录；问答和评分分别计数，缺失分数保留，不混算质量总分。

侧栏选项与数量随其他已选标签和搜索词联动：无匹配的未选项隐藏，已选的零结果条件保留以便取消，同一组仍支持追加多选。

**2026-09-16 当前工作：** IFEval 已改为直接使用 DeepEval verifier，无需人工正反例审计，详见 [评分与服务器使用说明](docs/ifeval-direct-scoring.md)。服务端实验由用户另行执行；本地开发评分与离线报告，用合成记录验证，不据此声明服务器成绩。以下 2026-09-14 及更早章节保留为历史阶段记录，最新进度以 [evaluation-status.md](docs/evaluation-status.md) 顶部和当前代码为准。

这是独立的 RAG 评测脚手架。大体量数据保留在：

```text
/home/wabiwabi/rag_benchmark/.ragbench/datasets
```

完整原始数据集保留在上述外部目录；本目录保存评测代码、配置、报告，以及 `var/` 下实验所需的样本快照、导入文档和隔离运行时。

仓库：[Xi0ng-Jun/SN_benchmark](https://github.com/Xi0ng-Jun/SN_benchmark)。最新状态见 [评测状态](docs/evaluation-status.md)，运行快照见 [结果摘要](results/public-benchmark-status.md)，后续建议见 [开发路线讨论稿](docs/development-roadmap.md)。

2026-09-10 用户已暂停具体评测执行，当前聚焦评测项目设计及 Silicon Notebook 产品能力与 DeepEval 的对应关系。[公开 Benchmark 持续评测计划](docs/superpowers/plans/2026-09-09-public-benchmark-evaluation.md)保留为阶段方案；400 次主 Ask 已完成，baseline 评分尚未完成，人工校准待审，定时任务已停用。既有环境验证不充当产品质量结论。

Git 保存源码、测试、配置、冻结的公开样本、学习材料及结果摘要。`var/` 原始运行记录、产品源码快照、数据库、模型日志、私有领域候选和本地环境保留在原机器；GitHub 上无法直接打开指向这些本地产物的历史链接。文件分布见 [结果索引](results/README.md)。

## 产品能力评测设计

**当前按能力确定十套公开评测的选题范围**：见[任务与选题方案](docs/public-benchmark-selection-plan.md)。不预设每套题数，选定 task/split 内符合规则的原题全部纳入；MMLU 四个学科、BBH 四类推理任务的选择理由单独说明。原“每套 20 题”的[起步方案](docs/deepeval-public-starter-plan.md)保留为历史设计。随后按[能力广度扩展计划](docs/product-capability-breadth-plan.md)设计业务场景；[Memory 与 Agent 上下文说明](docs/memory-and-agent-context.md)保留为背景。交互可靠性与资料更新一致性明确排除；在线调用继续暂停。

2026-09-10 已按用户要求编写 P0/P1 准备代码，并串起 N/R 执行与离线报告入口：五套公开题的本地冻结、SDK 模板/scorer、显式模型适配、产品原生导入/Ask、BoolQ 解析和结果覆盖率。数据格式见[首批代码交接](docs/deepeval-public-starter-implementation.md)，命令、配置、产物和边界见[执行与报告说明](docs/deepeval-public-starter-orchestration.md)。当时未运行测试；最新离线回归进度见下方，新增正式数据尚未冻结，在线调用仍暂停。

第一阶段已形成[详细方案](docs/product-capability-evaluation-plan.md)、[能力矩阵](docs/product-capability-matrix.md)、[统一数据协议草案与隔离规则](docs/evaluation-data-contract.md)、[真实 DROP 样本全链路说明](docs/evaluation-case-walkthrough.md)及[后续离线实施计划](docs/superpowers/plans/2026-09-10-product-capability-evaluation.md)。[公开样例快照](docs/examples/drop-smoke-case.json)随 Git 保存，包含真实输入、完整合成上下文、引用与历史评分，独立克隆也可查看。

这些材料区分最终文档命中与检索排名、引用对象存在与正文引用支持、产品错误与澄清行为。新协议尚未接入 runner，人工校准未完成；当前工作没有恢复在线评分或定时任务。

## 当前扩展阶段

2026-09-14 最新仅沉淀选题文档，测试与实验再次暂停。题量不由现有 40 篇单库资料上限决定；后续选择器与分库、覆盖对账需要独立实现，当前 runner 尚不支持新方案的多分区执行。没有下载或冻结新数据，本次文档更新不改代码。

阶段设计见[公开 Benchmark 与 SN Agent 评测扩展设计](docs/public-benchmark-agent-expansion-design.md)。已补齐 MMLU、GSM8K、TruthfulQA MC1、HellaSwag 和 BIG-Bench Hard 的 Native 请求与官方评分分派，以及 Product 不适用记录、来源重建和报告兼容逻辑，详见[代码补齐计划](docs/superpowers/plans/2026-09-13-expansion-code-completion.md)。**2026-09-14 全量离线回归 205 项通过，无失败、跳过或警告；新增正式数据尚未冻结，真实 SN 运行待验证。** 结果与复现步骤见[离线回归记录](docs/offline-regression-2026-09-14.md)。

当前转入[SN 系统接入](docs/sn-public-system-adaptation.md)：在保留模型参照的基础上，为 LogiQA、GSM8K、BBH、MMLU、TruthfulQA MC1、HellaSwag、IFEval 编写 `sn-public-system-v1`。LogiQA 导入配套阅读材料；其他套件导入不含解答的原始题面，通过正常 SN Ask 分别测推理、知识/常识和指令遵循。候选选项不作为事实证据，IFEval 保留原始 prompt 和完整答案正文。

新七套的 R 路径默认使用系统协议；`--product-protocol legacy` 才保留之前的 N/A 记录。SQuAD/DROP/BoolQ 沿用原产品适配。新系统路径使用隔离 SN 模型服务配置，不要求 `--models` 或伪造人审标签。冻结数据中的旧 product=False 不回填，当前能力由独立 adapter registry 定义。**离线回归包含合成样本与真实 SDK 客观评分；SN 执行使用测试替身，尚未进行在线验收。** Agent/DAG 指标未接入；在线、baseline 和 timer 继续暂停。

扩展数据仍使用 `public-expansion-v2`，旧扩展 bundle 需重新准备，原五套起步数据协议保持兼容。以下为后续验证入口，本轮未执行：

```bash
.venv/bin/python scripts/check_public_expansion_offline.py /path/to/bundle-or-run
```

系统运行入口示例（会调用 SN 和模型，本轮不执行）：

```bash
.venv/bin/python scripts/run_public_starter.py --bundle /path/to/gsm8k-bundle --track R --mode chunk --product-protocol sn-public-system-v1 --run-dir var/public-starter/NEW_SYSTEM_RUN
```

现有单库协议中，每个 suite/mode 使用新运行目录、独立 notebook，每题独立会话。后续完整题单的分库设计见[选题方案](docs/public-benchmark-selection-plan.md)，尚未实现。IFEval 不需要 `--instruction-audits`；该参数仅兼容旧命令并被忽略，直接评分不受冻结数据中 pending 字段影响。已有实现进度见[实施计划](docs/superpowers/plans/2026-09-13-sn-public-system.md)。

## SQuAD / DROP 持续评测

冻结输入位于 `data/public-benchmark-v1/`，每集 100 题及 200 个候选段落。原文走产品原生导入，Ask 只接收问题；两种模式各自使用独立数据库、索引和日志。

```bash
.venv/bin/python scripts/run_public_benchmark.py --run-dir var/public-benchmark/NEW_RUN --stage prepare --jobs 2
.venv/bin/python scripts/run_public_benchmark.py --run-dir var/public-benchmark/NEW_RUN --stage ask --scope debug --limit 5 --jobs 2
.venv/bin/python scripts/run_public_benchmark.py --run-dir var/public-benchmark/NEW_RUN --stage ask --jobs 2
.venv/bin/python scripts/run_public_benchmark.py --run-dir var/public-benchmark/NEW_RUN --stage judge --jobs 2
.venv/bin/python scripts/run_public_benchmark.py --run-dir var/public-benchmark/NEW_RUN --stage judge --scope calibration --judge-repeat 1 --jobs 2
.venv/bin/python scripts/run_public_benchmark.py --run-dir var/public-benchmark/NEW_RUN --stage ask --scope smoke --limit 2 --product-repeat 1 --jobs 2
.venv/bin/python scripts/report_public_benchmark.py var/public-benchmark/NEW_RUN
```

`NEW_RUN` 每轮使用新名称；同名目录只用于相同配置的续跑。保存答案的 judge 重评与产品重新生成分别记录，失败条目不会被静默覆盖。人工校准待完成时，语义分数仅用于观察，不触发质量发布门禁。

### 本地自动运行

离线检查不调用产品或 judge 模型：

```bash
.venv/bin/python scripts/check_public_benchmark_offline.py
```

自动运行器为每轮创建唯一 UTC 时间戳目录，依次执行 prepare、Ask、judge、report 和 audit。默认运行固定 regression 集；首次在线集成 smoke 使用冻结的 `smoke: true` 题目，两种模式共 40 次 Ask。脚本不设置外层超时。

```bash
.venv/bin/python scripts/automate_public_benchmark.py --scope regression
.venv/bin/python scripts/automate_public_benchmark.py --scope smoke
```

比较必须显式传入兼容 baseline，或在 `var/public-benchmark/baseline.json` 保存 `{"run_dir":"/absolute/path/to/baseline"}`。运行器会在 Ask 前核对 `comparison_identity`，不会自动更新 baseline 指针。

```bash
.venv/bin/python scripts/automate_public_benchmark.py --scope regression --baseline /absolute/path/to/baseline
```

`systemd/public-benchmark.service` 与 `systemd/public-benchmark.timer` 是 user-unit 模板，默认每周运行 regression。本机曾安装并验证 smoke，现已按暂停要求执行 `systemctl --user disable --now public-benchmark.timer`；模板保留，克隆仓库不会自动启用任务。完整 baseline 及人工校准尚未完成。

## 当前支持的数据

- `crud-rag/full`：中文单文档问答；
- `multihop-rag/full`：英文多文档/多跳问答；
- `scifact/full`：科学声明证据检索。

数据目录必须包含 `questions.jsonl`、`annotations.jsonl` 和 `documents.jsonl`。loader 会统一产出 `question`、`expected_answer`、`gold_document_ids`、`gold_context` 等字段。

## 安装

首次获取项目（建议放在 Silicon Notebook 的 `project/` 旁边）：

```bash
git clone git@github.com:Xi0ng-Jun/SN_benchmark.git benchmark-deepeval
```

```bash
cd /home/wabiwabi/silicon-notebook/benchmark-deepeval
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev,deepeval]'
```

完整离线测试需要 `dev,deepeval` 依赖，但不调用模型。原生 judge JSON 适配测试另外需要同级 `../project/backend`；缺少时明确跳过，单独克隆可运行其余检查。产品在线运行还需 Silicon Notebook 后端及其 Python 依赖、本地 `.env` 和 `.local/model-services.toml`；它们不随本仓库分发。默认产品位置为同级 `../project`，在线命令也可通过 `--project-root /absolute/path/to/project` 指定。历史在线运行使用 Python 3.13.15、DeepEval 4.2.2；新安装的依赖范围不等同于历史锁定环境，续跑前须通过身份校验。`config.example.json` 的数值是早期示例，不是正式阈值。

## 测试

在 worktree 或产品位于其他目录时，可用 `SILICON_NOTEBOOK_PROJECT_ROOT=/absolute/path/to/project` 为原生 judge JSON 契约测试指定产品位置。它只加载纯校验代码并使用假模型客户端；显式路径无效时测试失败，未配置且没有同级产品检出时该项跳过。带网络阻断的本轮复现命令见[回归记录](docs/offline-regression-2026-09-14.md)。

```bash
pytest -q
```

## 中文可执行课件

DeepEval 第一阶段调研提供了 CS336 风格的 executable lectures。课件源码位于 `lectures/`，可先直接运行：

```bash
.venv/bin/python lectures/lecture_01.py
```

需要在浏览器中逐步查看源码、Markdown 讲解和变量快照时，先生成官方 `edtrace` trace：

```bash
PYTHONPATH=lectures:src .venv/bin/python scripts/build_lecture_traces.py
```

然后按 [`lectures/README.md`](lectures/README.md) 启动官方 viewer。研究讲义、DeepEval 官方材料索引和项目映射见 [`docs/deepeval-study.md`](docs/deepeval-study.md)。课件 trace 是教学材料，不等于正式评测结果。

## 检查数据

```bash
rag-eval /home/wabiwabi/rag_benchmark/.ragbench/datasets/crud-rag/full --limit 5
rag-eval /home/wabiwabi/rag_benchmark/.ragbench/datasets/multihop-rag/full --limit 5
rag-eval /home/wabiwabi/rag_benchmark/.ragbench/datasets/scifact/full --limit 5
```

当前已提供公开数据集 BM25 baseline、Silicon Notebook 内部只读检索 adapter，以及下方独立运行时的原生导入与 Ask 实验入口。历史公开 baseline 结果见 `results/experiment-report.md`。实际模型调用需要配置的 embedding/chat 服务可达；服务错误会记录或导致阶段失败，不会静默替换为基线或参考答案。

## 运行 DeepEval

项目适配器输出 JSONL 后，每行至少包含：

```json
{"question":"问题","answer":"答案","expected_answer":"参考答案","retrieval_context":["实际上下文"],"retrieved_ids":["chunk-id"]}
```

安装可选依赖后运行：

```bash
pip install -e '.[deepeval]'
rag-eval-deepeval results/sample.jsonl --output results/deepeval.json
```

DeepEval runner 默认执行 Contextual Recall、Contextual Precision、Contextual Relevancy、Faithfulness 和 Answer Relevancy。`retrieved_ids` 会保留给后续确定性 Recall@K/MRR/nDCG 报告；它不会被 LLM judge 替代。

## 公开集进入项目链路（实验性）

`scripts/run_public_system.py` 将 MultiHop-RAG、SciFact 的前 50 个问题及其正证据文档并集导入独立项目运行时，通过原生切块、embedding、ANN、Ask 保存真实结果。这是受限候选库实验，不是全库公开 benchmark。

```bash
.venv/bin/python scripts/run_public_system.py --run-dir var/public-system-50 --stage prepare
.venv/bin/python scripts/run_public_system.py --run-dir var/public-system-50 --stage ask
.venv/bin/python scripts/run_public_system.py --run-dir var/public-system-50 --stage judge
.venv/bin/python scripts/report_public_system.py var/public-system-50
```

也可用 `--stage all` 顺序运行。重现新实验请使用新的 run-dir；相同目录用于续跑已保存的阶段。脚本不设置外层运行时限。逐条 judge 错误会记录为缺失分数，已有失败条目不会静默重新评分；需要重新实验时使用新目录。

SciFact 没有参考答案，所以跳过 Contextual Recall/Precision；其余指标使用真实系统答案。运行目录保存输入、文档/chunk 映射、答案、确定性指标、judge 结果、配置和审计报告。历史运行的隔离和 provenance 偏差单列在 `provenance-notes.md`，不可省略。

本轮 100 条实际问答及评分已完成，详见 [`实验报告`](var/public-system-50/report.md)。400 项 DeepEval 评分中 394 项有效、6 项失败；确定性指标、错误清单和运行限制一并保留，分数不构成产品门禁。

## 设计边界

- 完整原始 benchmark 数据保留在外部目录，不改写；实验所选输入和导入副本保留在独立运行目录并记录哈希；
- 不在 loader 中重新实现 Silicon Notebook 的检索逻辑；
- DeepEval 是可选依赖，确定性数据和指标测试不依赖外部模型；
- 私有语料、用户问题和密钥不应写入公共报告。
