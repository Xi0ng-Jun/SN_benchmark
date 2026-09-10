# 实验结果

## GitHub 交付与当前状态（2026-09-10）

- [公开评测状态摘要](public-benchmark-status.md)：当前基线进度、已完成 smoke 与暂停边界，可在 GitHub 直接阅读。
- `public-retrieval-50.json` 和 `public-retrieval-current.json`：历史公开集 BM25 汇总，供课程示例使用。
- 下文 `var/`、`domain-*` 和 `experiment-report.md` 属于原机器的本地工件索引；原始答案、日志、私有领域候选、产品源码快照和完整运行目录不在 Git 中，历史链接仅在本地有效。
- 冻结的 SQuAD/DROP 样本位于 `data/public-benchmark-v1/`，保留来源和哈希。原始下载归档可按 `data/README.md` 重建。

## 历史本地产物索引

结果文件不包含原始私有语料或密钥。每个结果应记录数据集版本、代码 revision、检索器、top-k、样本数和可用标注范围。

当前已完成：

- `public-retrieval-50.json`：三个已下载公开数据集的本地 BM25 top-10 基线；
- `domain-goldens.jsonl`：现有 notebook 生成的 2 条候选问题，未经人工审核，不作为正式基准；
- `domain-deepeval-unbounded.json`：上述候选的完整五指标试运行，10 项结果，无外层人为时限；
- [`公开集实际项目链路报告`](../var/public-system-50/report.md)：MultiHop-RAG / SciFact 各 50 条，经独立项目运行时导入、向量检索及 Ask；同时保留确定性指标和 DeepEval 结果，400 项评分中 394 项有效、6 项失败。

实际项目链路实验使用所选问题的正证据文档并集，不是全库评测，不与全库 BM25 数字直接比较。SciFact 没有参考答案，跳过两个依赖参考答案的指标。输入、配置、版本、实际答案、完整上下文、全部问答尝试和哈希索引均在 `var/public-system-50/`；初始日志隔离与代码快照偏差详见该目录的 `provenance-notes.md`。

当前没有把任何“参考答案复制为实际答案”的结果写入报告，因为那会让 Faithfulness/Answer Relevancy 失去实验意义。

## 本轮结果文件索引

绝对目录：`/home/wabiwabi/silicon-notebook/benchmark-deepeval/var/public-system-50/`。

| 文件或子目录 | 内容 |
|---|---|
| [report.md](../var/public-system-50/report.md) | 中文完整报告、指标口径、结果与限制 |
| [summary.json](../var/public-system-50/summary.json) | 确定性指标、judge 均分及有效数、失败项、漏检问题 ID |
| `multihop-rag/`、`scifact/` | 两个数据集各自的逐条记录，文件说明见下方 |
| [run.json](../var/public-system-50/run.json) | 完成状态、数据来源与版本、模型、检索配置、项目 revision |
| [environment.json](../var/public-system-50/environment.json) | Python、依赖版本与评测模块哈希 |
| [answer-attempts.json](../var/public-system-50/answer-attempts.json) | 101 次问答尝试，包括首次失败后恢复的记录 |
| [runtime-observations.json](../var/public-system-50/runtime-observations.json) | 已隔离日志中可见的调用与 token 用量，不等同于完整账单 |
| [provenance-notes.md](../var/public-system-50/provenance-notes.md) | 初始日志隔离与版本记录偏差 |
| [artifact-sha256.json](../var/public-system-50/artifact-sha256.json)、`code/` | 非 runtime 产物的哈希索引及评测代码快照 |
| `runtime/` | 独立 SQLite、导入文档、索引、缓存和日志 |

两个数据集子目录均包含：

- `questions.jsonl`：本轮问题及原有标注。
- `document-map.json`：公开文档 ID 与项目 source/chunk 的映射及文档哈希。
- `answers.jsonl`：实际答案、项目响应、完整生成上下文和逐条确定性指标。
- `retrieval-results.jsonl`：检索上下文与映射记录。
- `deterministic-metrics.json`：确定性检索指标汇总。
- `deepeval-results.json`：逐项分数、理由、耗时、错误与跳过记录。

本轮运行已结束，无需为查看结果重新执行模型调用。新实验使用新的运行目录，保留本轮记录供比较；各实验候选库不同，比较前须统一语料与指标口径。
