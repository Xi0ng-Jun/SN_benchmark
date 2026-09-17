# Notebook benchmarks Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans for the shared protocol/runtime integration; bounded scoring work and final review may use superpowers:subagent-driven-development. Continue the user-authorized implementation without another approval checkpoint.

**Goal:** 将 QASPER、MultiHop-RAG、ALCE、QMSum 接入真实 SN Product R 路径，并可离线检查和展示结果。

**Architecture:** 新增 `sn-notebook-benchmarks-v1` 数据协议，保留旧十套协议。输入为服务器已获取的官方本地文件；原始资料与参考答案分离，准备阶段不加载 SN/DeepEval/网络。执行复用现有隔离 runtime、导入、原生 Ask、上下文捕获、追加日志和 Dashboard。

**Tech Stack:** Python 3.11、pytest、现有 SN runtime；ROUGE 可选依赖固定版本；ALCE 模型指标走独立、显式的官方 scorer 接口。

**Spec:** [已确认的数据和 scorer 来源](../../notebook-benchmark-data-availability.md)。

## Global Constraints

- 不下载数据/权重，不运行 SN/在线评分，不修改生产代码/配置，不恢复 timer。
- 全量选择指定文件中的有效题；无样本数配额。排除项逐条记录。
- 不增加官方数据人工审核门槛；不设质量阈值。
- 每题完整资料集合不得拆散；单题超容量即准备失败，不截断证据。
- MultiHop 全语料保持可达；默认一个完整 corpus notebook，需要显式容量。不得仅取 gold 文章冒充检索评测。
- ALCE 每题使用全部给定候选，同候选集合可共用 notebook；不拼接不同题的候选扩大检索范围。普通/oracle 变体由来源元数据区分。
- QASPER 每篇论文、QMSum 每场会议一个资料分区；两种 mode 独立 runtime，每题无会话历史。
- 连续主分与旧二元主分分开解释；澄清/错误/缺评分保持 null。

## Task 1: 本地数据协议和多文档资料分区

Files: `src/rag_eval/notebook_data.py`, `src/rag_eval/notebook_bundle.py`, `scripts/prepare_notebook_benchmarks.py`, `tests/test_notebook_data.py`。

接口：`adapt(suite, raw, *, corpus=None, task=None)` 返回 `{cases, documents, decisions}`。case 含 `case_id/sample_id/suite/task/question/references/gold/material_document_ids/gold_document_ids/group_id`。document 含 `id/title/text/text_sha256/document_sha256`。`prepare(...)/load_bundle(...)` 固定来源文件哈希并重新构建验证；`partition_bundle(...)` 产出已有 runtime 接受的 questions/documents/decisions/manifest。

- [x] 构造四套官方字段形状的最小样本；验证 gold 不进入资料、跨文档证据完整、图表题记录排除、会议多段定位保留、ALCE 候选顺序保留。
- [x] 运行 `pytest tests/test_notebook_data.py -q`，先观察缺实现失败，再实现适配。
- [x] 来源要求 dataset/split/revision/source_url/license；ALCE 要求 task/retriever/variant。JSON/QMSum JSONL 严格解析，不自动联网和解压。
- [x] 保存原始文件、cases/documents/decisions/partitions/source；manifest 最后落盘，加载核对哈希并确定性重建。
- [x] 验证变更原文件、case、分区、重复 ID、失配 evidence 的错误都可被捕获。

## Task 2: 独立评分和 ALCE 引用转换

Files: `src/rag_eval/notebook_scoring.py`, `src/rag_eval/notebook_alce.py`, `tests/test_notebook_scoring.py`。

接口：`metric_specs(case)` 返回 scorer/role/score_kind；`score_case(case, record, scorer)` 返回 status/score/reason/details。record 为已保存的 SN product_record；附 `source_to_document` 映射。不自动调用模型。

- [x] 手工算例先验证：QASPER 答案 F1、MultiHop 弱词重合、ALCE ASQA/QAMPARI、QMSum ROUGE 与缺依赖。
- [x] QASPER 正文仅去合法 SN citation marker，独立命名正文适配分；最终上下文映射整段 evidence 的分数明确为诊断，不冒充模型输出 evidence 的官方成绩。
- [x] MultiHop 标明官方弱匹配实现；证据 fact 覆盖是 final-context 诊断，不称 Hits@k/MAP/MRR（当前没有完整检索排序）。null_query 无检索 gold 时 N/A。
- [x] ALCE 引用仅根据实际 anchor key/source 映射为官方编号，不补造不存在的引用。保存原始正文、转换结果、映射错误。官方模型评分是显式独立命令，冻结官方代码身份，保存输入/输出；不隐式安装或下载。
- [x] QMSum 使用 rouge-score==0.1.2，use_stemmer=True，ROUGE-1/2/L F1，缺依赖记 error；代码未运行过真实实验不声称原论文复现。

## Task 3: SN 执行与报告接通

Files: `src/rag_eval/notebook_runner.py`, `scripts/run_notebook_benchmarks.py`, `src/rag_eval/starter_report.py`, `src/rag_eval/starter_results.py`, `src/rag_eval/metric_catalog.py`, `src/rag_eval/experiment_aggregation.py`, `tests/test_notebook_execution.py`。

- [x] 用 fake SN 边界做执行→落盘→load_run→Dashboard 集成回归，正常/澄清/缺评分均有覆盖。
- [x] 每个分区和 mode 单独进程、独立路径；资料容量显式配置并写入比较身份，runtime 实际容量不足即失败。
- [x] 复用 configure_environment/snapshot_sources/prepare_notebook/run_system_question；planned 在预测前保存，异常仍可离线出报告。
- [x] 新协议报告验证冻结输入和 partition，连续主分允许 0..1，不显示为答对率；旧 binary 语义不变。
- [x] 增加指标目录说明、来源详情、同配置 chunk/reasoning 配对支持。

## Task 4: 文档、离线验证与复核

Files: `docs/notebook-benchmarks.md`, `README.md`, `docs/evaluation-status.md`, `docs/evaluation-context.md`。

- [x] 写服务器准备/执行/报告命令，明确 ALCE 官方模型评分需要的独立依赖与边界。
- [x] 使用已有 venv，阻断网络执行新增测试和现有回归；不安装包。
- [x] 独立代码审阅，修复实质问题，再运行受影响回归和 `git diff --check`。
- [x] 记录本地验证与尚未执行的真实数据验收，代码完成不等于实验完成。此阶段未要求推送，不自动发布。
- [x] 用户 2026-09-17 明确要求同步到远程后，提交并推送开发分支；不合并 `main`，不改变验收状态。

## 实施记录

2026-09-17：Task 1–4 的代码、文档及本地验证完成。新增 `notebook_alce_results.py` 和 `score_notebook_alce.py`，将独立模型评分挂接到新 run，供既有 Dashboard 使用。未下载公开文件/权重或执行任何真实模型实验。

验证为 319 Python + 18 JavaScript；Python 测试进程网络尝试 0。独立审阅后修复了 reasoning 前导说明与 Dashboard 分区身份；复核无剩余发现。QMSum 真实 ROUGE 包不在本机，实际库输出验收留服务器执行，不以替身测试代替。保留现有分支/worktree，未做提交、推送、合并。使用与当前边界见 `docs/notebook-benchmarks.md`。

2026-09-17 后续：按用户要求将本轮改动拆分为数据、评分/引用转换、执行与报告、ALCE 挂接、文档五个提交，推送到 `origin/docs/public-benchmark-agent-expansion`。推送仍属本地实现与离线回归，真实数据与服务器验收状态不变。

技术决定：完整资料范围优先于旧 40 篇默认值，新增路径允许显式容量；其代价是 MultiHop 全 corpus 和 ALCE 完整候选会增加服务器导入量。旧路径不调整容量。人工校准不作为官方确定性规则执行门槛。
