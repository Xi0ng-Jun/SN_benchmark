# 已有方法和结果

本页解释已有方法如何进入比较。
方法登记表是机器可读的事实入口。
详细条件和来源见[外部答卷与受控方法](../notebook-external-results-2026-09-28.md)。

## 从哪里方便地查成绩

HotpotQA 有正式榜单，MultiHop-RAG 有作者结果表。
ALCE、QASPER、QMSum 可以从原论文和后续方法论文取得成绩。
LongBench v1 也有 Qasper、QMSum 和 HotpotQA 的结果表，但每项是其自己的 200 题版本。
入口、核查日期、后续论文实例和版本差异见[官方资料手册](../notebook-benchmark-official-resources.md)。

没有独立提交榜单，不等于没有可引用结果。
五套任务覆盖多跳问答、引用、文档阅读和摘要；它们服务于不同研究问题。
不能只从榜单是否活跃判断任务是否适合 SN。

## 三种结果类别

### 发表参考值

直接读取论文或作者页面的汇总值。
它可用于论文中的系统表现参考。
同轨结果可以注明来源后并列；数据或评分条件不同的结果分组展示。
引用不要求逐题答卷，也不要求统一重评分或重跑模型。
保留原作者的数值、尺度和条件，缺失字段写 unknown。

机器标签是 `published-reference`。

### 公开答卷重评分

使用公开逐题答卷。
将题目映射到当前 frozen bundle。
使用当前固定 scorer 重评分。

它可以支持描述性比较。
它不证明原论文实验被复现。
它不证明生成条件相同。

机器标签是 `recomputed-subset`。

### 受控重跑

按当前声明的输入和方法重新生成答案。
保存实际模型、提示、预算和运行记录。
使用统一 scorer 评分。

它可以支持受控端到端比较。
如果修改了作者方法，必须标记为 adapted 或 controlled。

机器标签是 `controlled-rerun`。

## 当前已取得的结果

### MultiHop-RAG：Multi-Meta-RAG

已有两份完整公开答卷：

- GPT-4：2,556 题，QA 命中 1,549，weak-match accuracy 为 0.606025。
- PaLM：2,556 题，QA 命中 1,553，weak-match accuracy 为 0.607590。
- 两者检索分母都是 2,255。
- Hits@4 为 0.792018。
- Hits@10 为 0.904213。
- MAP@10 为 0.338816。
- MRR@10 为 0.674762。

这些是公开答卷重评分。
它们不是 SN 结果。
它们也不是同条件算法归因。

### ALCE：ASQA human_eval

已有四种公开配置。
每种配置覆盖同一组 100 题。

| 方法 | `str_em` | `str_hit` |
| --- | ---: | ---: |
| GPT-3.5 VANILLA | 0.3530 | 0.0900 |
| GPT-3.5 interactive | 0.3820 | 0.0900 |
| GPT-3.5 sample4 RERANK | 0.3655 | 0.1100 |
| Vicuna-13B | 0.264667 | 0.0500 |

空答案仍在 100 题分母中。
原始 shown-document 列表不完整。
因此引用指标保持 pending。

### ALCE：ELI5 human_eval

已有四种公开配置。
每种配置覆盖 100 题。
当前没有可报告的 claims-NLI、MAUVE 或引用分数。
这些指标需要模型评分或完整引用映射。

### QMSum：Socratic SegEnc

已有一份公开答卷。
按公开代码顺序映射到当前 281 题。
固定 Perl ROUGE 重评分为：

- ROUGE-1：0.38955
- ROUGE-2：0.13960
- ROUGE-L：0.33942

原始运行清单不可得。
因此这是公开答卷重评分，不是完整论文复现。

## 已准备但仍 pending 的方法

- QASPER LAB LongChat citation：输入和 parser 已准备，真实模型未运行。
- HotpotQA KG2RAG adapted：公共输入和适配器已准备，真实模型未运行。
- ALCE VANILLA：完整 prompt 和 shown-doc 准备完成，真实模型未运行。
- SN 五套 chunk：正式服务器结果待实际工件核实。
- MultiHop SN reasoning：QA 可以评分，检索排名保持 pending。

## 根据研究问题选择比较方式

| 想回答的问题 | 合适的路径 |
| --- | --- |
| SN 与已有系统相比表现如何？ | 引用论文/作者结果；核对任务、划分、输入和指标，披露条件差异 |
| 相同问题上的分差是多少？ | 有答卷时统一重评分，再做同题配对 |
| 某个模块是否有效？ | 固定其他关键因素，设计有针对性的受控实验或消融 |

三类结果不是必须逐级完成的流程。
BM25 控制组不是所有外部比较的前提。
模型不同仍可做端到端系统比较；不能把分差单独归因于检索或其他模块。

## 同题配对前必须检查什么

1. 方法是否使用同一题目集合。
2. 方法是否使用同一 scorer。
3. 方法是否有完整状态记录。
4. 方法是否使用 ordinary 输入，而不是 oracle 输入。
5. 模型、提示、检索器和预算是否已知。
6. 结果是否来自完整范围或明确固定子集。

未知条件必须写成 unknown。
不能从方法名猜模型和预算。
这些工件条件适用于本项目的严格配对报告，不要求每份被引用论文都提供本项目格式的答卷。

## 继续阅读

- [如何解释分数](05-results-and-paper.md)
- [外部方法来源审计](../notebook-external-method-audit-2026-09-26.md)
- [外部答卷与受控方法](../notebook-external-results-2026-09-28.md)
- [机器可读方法登记表](../../configs/notebook-external-method-registry-v1.json)
