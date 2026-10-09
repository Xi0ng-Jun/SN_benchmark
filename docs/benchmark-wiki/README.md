# Silicon Notebook Benchmark Wiki

这是 Silicon Notebook benchmark 评测的 wiki 入口。

先读本页。
再按你的问题选择下一页。
现有专题文档仍是事实来源。
本 wiki 只说明入口、关系和阅读顺序。

## 你要先知道什么

Silicon Notebook 当前维护五套 benchmark：

- QASPER
- MultiHop-RAG
- ALCE
- QMSum
- HotpotQA

系统先固定数据和题目范围。
系统再生成答案。
系统最后使用官方 scorer 评分。

参考答案留在评分侧。
参考答案不进入生成请求。

## 推荐阅读路径

### Level 1：快速理解

1. [评测是什么](01-evaluation-model.md)
2. [五套 benchmark 一览](02-benchmark-map.md)
3. [一次实验如何运行](03-run-lifecycle.md)

读完这三页，你可以回答：

- 评测输入是什么。
- 模型输出什么。
- 分数从哪里来。
- 一次实验产生哪些文件。

### Level 2：理解比较

4. [已有方法和结果](04-methods-and-results.md)
5. [如何解释分数](05-results-and-paper.md)
6. [实验结果表与填报协议](../benchmark-result-tables.md)

读完这些页面，你可以回答：

- 论文分数和重评分有什么区别。
- 公开答卷是否可以和 SN 配对。
- 为什么不同 benchmark 不能合成一个总分。

### Level 3：查实现细节

按需要打开这些专题文档：

- [当前状态](../../CURRENT_STATE.md)
- [执行指南](../../RUNBOOK.md)
- [评测上下文](../evaluation-context.md)
- [评测状态](../evaluation-status.md)
- [官方资料](../notebook-benchmark-official-resources.md)
- [标准与实现符合性](../notebook-benchmark-standards-and-conformance.md)
- [实验计划](../notebook-benchmark-experiment-plan.md)
- [外部答卷与受控方法](../notebook-external-results-2026-09-28.md)
- [比较案例复核](../notebook-comparison-case-review-2026-09-28.md)
- [外部 campaign 手册](../notebook-external-campaign-runbook.md)
- [共享结果存储与导出](../result-storage-and-export.md)

## 按问题跳转

| 问题 | 入口 |
| --- | --- |
| Benchmark 的数据从哪里来？ | [五套 benchmark 一览](02-benchmark-map.md) |
| 生成请求会看到 gold 吗？ | [评测是什么](01-evaluation-model.md) |
| 如何启动一次服务器实验？ | [一次实验如何运行](03-run-lifecycle.md) |
| QASPER Evidence F1 如何得到？ | [五套 benchmark 一览](02-benchmark-map.md#qasper) |
| MultiHop 的 QA 和检索分数有什么区别？ | [五套 benchmark 一览](02-benchmark-map.md#multihop-rag) |
| ALCE 的 100 题和 948 题是什么关系？ | [五套 benchmark 一览](02-benchmark-map.md#alce) |
| 哪些结果已经存在？ | [已有方法和结果](04-methods-and-results.md) |
| 有榜单吗？必须重跑外部方法吗？ | [成绩入口与比较方式](04-methods-and-results.md#从哪里方便地查成绩) |
| 本轮实验要产出哪些表？ | [整组结果表模板](../benchmark-result-tables.md) |
| 如何让服务器 agent 按表交付？ | [填报指令](../server-benchmark-result-tables-prompt.md) |
| 为什么不能直接说 SN 超过某方法？ | [如何解释分数](05-results-and-paper.md) |
| 论文实验部分怎么写？ | [如何解释分数](05-results-and-paper.md#论文中的结果表) |

## 文档状态

本 wiki 描述当前代码和已记录证据。
服务器状态必须以实际工件为准。
历史交接中的服务器转述不自动成为当前事实。
