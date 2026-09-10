# Silicon Notebook RAG Benchmark + DeepEval

这是独立的 RAG 评测脚手架。大体量数据保留在：

```text
/home/wabiwabi/rag_benchmark/.ragbench/datasets
```

完整原始数据集保留在上述外部目录；本目录保存评测代码、配置、报告，以及 `var/` 下实验所需的样本快照、导入文档和隔离运行时。

仓库：[Xi0ng-Jun/SN_benchmark](https://github.com/Xi0ng-Jun/SN_benchmark)。最新状态见 [评测状态](docs/evaluation-status.md)，运行快照见 [结果摘要](results/public-benchmark-status.md)，后续建议见 [开发路线讨论稿](docs/development-roadmap.md)。

2026-09-10 用户已暂停具体评测执行，当前聚焦评测项目设计及 Silicon Notebook 产品能力与 DeepEval 的对应关系。[公开 Benchmark 持续评测计划](docs/superpowers/plans/2026-09-09-public-benchmark-evaluation.md)保留为阶段方案；400 次主 Ask 已完成，baseline 评分尚未完成，人工校准待审，定时任务已停用。既有环境验证不充当产品质量结论。

Git 保存源码、测试、配置、冻结的公开样本、学习材料及结果摘要。`var/` 原始运行记录、产品源码快照、数据库、模型日志、私有领域候选和本地环境保留在原机器；GitHub 上无法直接打开指向这些本地产物的历史链接。文件分布见 [结果索引](results/README.md)。

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
