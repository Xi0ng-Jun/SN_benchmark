# 评测是什么

本页只讲整体模型。
公式版本见[形式化流程](../benchmark-evaluation-formal-workflow.md)。

## 一句话

系统先给模型一份不含 gold 的任务。
系统保存模型的答案和实际观测。
评分器再把答案与评分侧 gold 对齐。

## 四个对象

一次评测至少有四个对象：

1. **题目**：问题、公开资料和唯一 `case_id`。
2. **生成请求**：模型实际可以看到的内容。
3. **运行记录**：答案、引用、检索结果、错误和状态。
4. **评分记录**：固定 scorer 的指标、分母和依赖身份。

gold 只属于第四个对象。
模型请求不能包含 gold。

## 一道题的生命周期

```text
frozen bundle
    → public request
    → model or method run
    → raw output and observations
    → official submission
    → fixed scorer
    → score and audit record
```

每个箭头都有一个边界。
边界负责检查自己的输入和输出。

## 为什么要保存原始记录

提交文件适合评分。
原始运行记录适合审计。

例如，ALCE submission 需要答案和引用映射。
原始记录还需要保存原始答案、实际看到的候选文档和转换错误。

因此 compact submission 不能替代完整 run。

## 状态不是分数

下列状态必须分开：

- `success`：回答成功产生。
- `no_answer`：系统明确没有给出答案。
- `clarification`：系统要求澄清。
- `error`：运行失败。
- `missing`：没有对应输出。
- `pending`：某个指标还没有足够观测。

缺失不能自动变成零分。
pending 也不能自动变成零分。

## DeepEval 在哪里

DeepEval 负责项目的 Agent、组件和轨迹诊断。
这些诊断可以解释回答、检索和轨迹质量。
它们不替代 QASPER、MultiHop-RAG、ALCE、QMSum 和 HotpotQA 的官方指标。

## 继续阅读

- [五套 benchmark 一览](02-benchmark-map.md)
- [一次实验如何运行](03-run-lifecycle.md)
- [形式化流程](../benchmark-evaluation-formal-workflow.md)
- [标准与实现符合性](../notebook-benchmark-standards-and-conformance.md)
