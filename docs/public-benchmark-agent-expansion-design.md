# 公开 Benchmark 与 SN Agent 评测扩展设计

更新时间：2026-09-11

## 目标

在现有 SQuAD、DROP、BoolQ、LogiQA、IFEval 起步方案上扩大 DeepEval 官方公开 benchmark 覆盖，并为 Silicon Notebook 的 Agent 过程评测预留统一 trace 接口。公开 benchmark、SN 端到端结果和 Agent 轨迹结果分开报告。

## 第一批范围

| Suite | 主要能力 | Native | SN Product | 说明 |
|---|---|---:|---:|---|
| MMLU | 多领域知识与选择题 | 是 | 是 | 按领域分 task；产品路径需固定资料范围 |
| GSM8K | 多步数学推理 | 是 | 是 | 最终数值为主判据，过程作诊断 |
| TruthfulQA | 错误前提识别、事实诚实性 | 是 | 是 | 需要人工核验纠正和拒答行为 |
| HellaSwag | 语境理解与常识推断 | 是 | 暂不 | 与 notebook 检索链路关联较弱 |
| BIG-Bench Hard | 复杂推理子任务 | 是 | 暂不 | 先选文本推理子任务 |

HumanEval、KG、重排、PDF/OCR、交互可靠性和资料更新一致性不在本阶段范围内。

## 两条评测路径

Native track 使用 DeepEval 官方 benchmark 的题目模板和 scorer，衡量被测模型完成通用任务的能力。Product track 将题目所需材料导入隔离 notebook，通过 SN Ask 保存最终答案、实际上下文、引用和状态，再使用适用的 DeepEval 指标及确定性检查。两类分数不合并、不相减。

## Agent 评测预留

当前不修改 Silicon Notebook 生产代码。统一结果协议增加可选 `trace`：

```json
{"trace_id":"...","completeness":"none|partial|complete","spans":[]}
```

未来 span 类型包括 `intent`、`query_rewrite`、`retriever`、`reasoning`、`memory`、`answer_generation` 和 `citation_assembly`。只记录结构化动作、输入输出摘要、source/chunk ID、工具参数、状态和耗时，不记录完整 chain-of-thought。若现有 API 或日志无法提供真实有序轨迹，先标记缺失，不虚构 trajectory metric 输入。

轨迹完整后再接入 `TaskCompletionMetric`、`StepEfficiencyMetric`、`ToolCorrectnessMetric` 和 `ArgumentCorrectnessMetric`；只有存在可观察正式计划时才考虑 `PlanAdherenceMetric`、`PlanQualityMetric`。

## DAG 的位置

`DAGMetric` 是 DeepEval 的自定义决策树指标，用于把“是否有引用 → 引用对象是否属于当前 notebook → 是否支持主要断言”等条件拆成判断节点，并为终点分支指定分数。它不是 Agent 执行轨迹，也不替代公开 benchmark scorer。待引用、澄清和拒答规则完成人工校准后，再选择一两个场景试用；在此之前只保留设计，不设置门禁阈值。

## 分阶段交付

1. 扩展 suite、数据 manifest、Native/Product 适用性和离线重建检查。
2. 扩展 runner、结果协议和报告，保持失败、澄清、拒答和不适用可区分。
3. 定义最小 trace/span schema，并用已有 captures 离线构造 `none/partial` 轨迹。
4. 在用户明确恢复在线评测后，选择小样本验证新增 suite；不恢复旧 baseline 或 weekly timer。
5. 根据真实观测和人工校准决定 Agent metrics、DAG 指标及后续最小观测改动。

## 验收标准

- 每个新增 suite 保存来源、revision、hash、原始输入和答案映射；
- 明确 Native/Product 适用性和不适用原因；
- 报告分别展示模型、产品和轨迹层结果；
- 缺失 trace 时不会伪造 Agent 指标；
- 生产项目、配置、timer 和在线服务保持不变；
- 未经人工校准不设置质量门禁。
