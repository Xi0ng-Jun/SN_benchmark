# 如何解释分数

本页说明结果报告的写法。
它不增加新的 benchmark 指标。

## 先报告范围

每个结果至少写清：

- benchmark 和 setting
- 数据版本
- split
- case 数量
- 指标分母
- scorer 版本
- 模型和方法条件
- error、missing 和 pending 数量

同一个 benchmark 的 full 和 subset 必须分开写。

例如，ALCE ASQA 948 题和 human_eval 100 题不能放在同一行。

## 再报告指标

建议按 benchmark 分表。

| Benchmark | 主结果 | 条件结果 |
| --- | --- | --- |
| QASPER | Answer F1 | Evidence F1 |
| MultiHop-RAG | QA weak-match accuracy | Hits、MAP、MRR |
| ALCE-ASQA | `str_em`、`str_hit` | QA、ROUGE、MAUVE、citation |
| ALCE-QAMPARI | precision、recall、F1、@5 | citation |
| ALCE-ELI5 | claims recall | ROUGE、MAUVE、citation |
| QMSum | ROUGE-1/2/L | query type 分组 |
| HotpotQA | Answer EM/F1/Precision/Recall | Supporting Fact、Joint |

条件指标缺少必要观测时写 `pending`。
不要写成零。

## 不要合成跨 benchmark 总分

五套任务的输入、答案形式和指标不同。
它们没有共同的官方总分。

因此不要计算：

- 五套平均分。
- 一个总准确率。
- 一个无依据的 SOTA 排名。

可以分别讨论每套任务的表现。

## 不要把字符串分数解释成完整语义

MultiHop weak-match 只需要一个共同 token。
ALCE `str_em` 使用答案子串。
QMSum ROUGE 衡量词面重合。

这些指标不能单独证明完整事实正确。
应结合案例和输入条件解释。

案例复核不是新的主指标。
人工判断也不能冒充官方分数。

## 如何描述方法差异

如果两个方法的生成模型不同，可以写：

> 我们比较了两个完整系统在固定 benchmark 协议下的端到端表现。由于生成模型和推理预算不同，结果不能用于单独归因于检索或引用模块。

只有在其他关键条件固定时，才可以讨论某个模块的影响。

同条件重跑也不自动等于消融实验。

## 论文中的结果表

每行建议包含：

1. 方法名和具体配置。
2. 结果类别：published-reference、recomputed-subset 或 controlled-rerun。
3. 数据范围和分母。
4. 主要指标。
5. 重要的输入或模型差异。

表注应说明：

- 哪些结果是论文参考值。
- 哪些结果是公开答卷重评分。
- 哪些结果由当前系统重新生成。
- 哪些指标仍 pending。

## 可信结果的最低条件

一个结果可以进入正式主表，至少需要：

- 固定 bundle 身份。
- 固定 case IDs。
- 生成请求没有 gold 泄漏。
- scorer 文件和依赖身份可核验。
- 实际分母可追溯。
- 失败状态没有被隐藏。
- 方法条件没有被猜测。

如果缺少其中一项，应降低结果类别或保留 pending。

## 继续阅读

- [已有方法和结果](04-methods-and-results.md)
- [比较案例复核](../notebook-comparison-case-review-2026-09-28.md)
- [评测状态](../evaluation-status.md)
- [实验计划](../notebook-benchmark-experiment-plan.md)
