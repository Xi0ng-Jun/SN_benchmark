# 五套 benchmark 一览

本页给出每套任务的最短路径。
精确公式和来源请打开对应专题文档。

## QASPER

### 任务

系统阅读科研论文并回答问题。
系统还可以提交支持答案的论文段落。

### 当前范围

- v0.3 test
- 416 篇论文
- 1,451 道问题

当前公开输入包含标题、摘要、正文和公开 caption。
这个输入与作者 LED reader 不完全相同。

### 评分

- Answer F1：多参考答案中取最高 token F1，再对题目宏平均。
- Evidence F1：比较显式预测段落和 gold evidence。

最终上下文覆盖不能代替 Evidence F1。
SN 的引用先经过 chunk 到原始段落的投影。
映射不完整时，Answer F1 可以保留，Evidence F1 保持 pending。

### 相关文档

[QASPER 详细标准](../notebook-benchmark-standards-and-conformance.md#3-qasper：科研论文问答与证据)

## MultiHop-RAG

### 任务

系统在 609 篇新闻文章中回答跨文档问题。
部分问题需要多个文档。
301 道 null 问题表示资料可能不足。

### 当前范围

- 2,556 道问题
- 609 篇文章
- 301 道 null 问题

### 评分

QA 使用作者的 weak token-intersection match。
只要预测和 gold 有一个共同 token，就算命中。
QA 分母是 2,556。

检索评分排除 null 问题。
检索分母是 2,255。
指标包括 Hits@4、Hits@10、MAP@10 和 MRR@10。

SN chunk 保存有序检索快照。
SN reasoning 没有单一排名。
因此 reasoning 可以有 QA 分数，但检索分保持 pending。

### 相关文档

[MultiHop 详细标准](../notebook-benchmark-standards-and-conformance.md#4-multihop-rag：跨文档问答与排名检索)

## ALCE

### 任务

ALCE 包含三个独立任务：

| 任务 | 冻结题数 | 任务输出 |
| --- | ---: | --- |
| ASQA | 948 | 长答案和引用 |
| QAMPARI | 1,000 | 答案列表和引用 |
| ELI5 | 1,000 | 解释性答案和引用 |

这三个任务不合成一个 ALCE 总分。

### 评分

默认轻量路径：

- ASQA：`str_em`、`str_hit`。
- QAMPARI：precision、recall、recall@5、F1、F1@5。
- ELI5：默认不产生 claims 语义分。

`--alce-answer-only` 会运行真实评分模型，但关闭引用评分。
`--alce-full` 还需要完整 shown-document 和 citation mapping。

公开 human_eval 答卷只有 ASQA/ELI5 各配置 100 题。
这 100 题不能当作 ASQA 全量 948 题。

### 相关文档

[ALCE 详细标准](../notebook-benchmark-standards-and-conformance.md#5-alce：答案正确性、流畅性与引用支持)

## QMSum

### 任务

系统根据查询总结会议内容。
输入是完整会议 transcript 和 query。
gold relevant span 不进入生成请求。

### 当前范围

- 35 场会议
- 281 道 query
- 37 道 general
- 244 道 specific

### 评分

正式评分使用 Perl ROUGE-1.5.5：

- ROUGE-1 F1
- ROUGE-2 F1
- ROUGE-L F1

当前固定作者参数和 HMNet regex 分句。
论文历史表中的 279 题不能替换当前冻结文件的 281 题。

### 相关文档

[QMSum 详细标准](../notebook-benchmark-standards-and-conformance.md#7-qmsum：查询驱动的会议摘要)

## HotpotQA

### 任务

系统在每题约十个 Wikipedia 段落中回答多跳问题。
系统还可以提交 supporting facts。

### 当前范围

- distractor validation
- 7,405 道问题
- 73,700 个段落

当前轨道不是 fullwiki。
fullwiki 和 test leaderboard 必须单独记录。

### 评分

答案指标包括：

- Answer EM
- Answer Precision
- Answer Recall
- Answer F1

完整证据指标包括 Supporting Fact 和 Joint 的 EM、Precision、Recall、F1。
这些指标要求显式的 `[title, sentence_id]` 预测。
SN 的最终引用经过句位投影。
映射失败时答案指标仍可评分，Supporting Fact 和 Joint 保持 pending。

### 相关文档

[HotpotQA 详细标准](../notebook-benchmark-standards-and-conformance.md#6-hotpotqa：distractor 多跳问答与 supporting facts)

## 继续阅读

- [一次实验如何运行](03-run-lifecycle.md)
- [已有方法和结果](04-methods-and-results.md)
- [五套 benchmark 官方资料](../notebook-benchmark-official-resources.md)
