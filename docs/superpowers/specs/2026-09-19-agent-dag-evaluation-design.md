# Silicon Notebook Agent/DAG 评测设计

## 目标

在不修改 Silicon Notebook 生产代码、不重新运行 SN Ask 的前提下，利用已有运行目录中的 `reasoning_trace` 和最终问答结果，建立一条可审计的 Agent/DAG 离线评测路径。第一阶段优先回答“reasoning 到底做了什么”，再对足够完整的轨迹使用 DeepEval 的 trajectory metrics 和 DAGMetric。

## 范围与边界

- 输入是已有 `outputs.jsonl`、公开 benchmark case 和保存的产品响应；工具不下载数据、不启动 SN、不修改运行目录。
- `chunk` 没有 Agent 轨迹时标记为 `none`，只参与最终答案和确定性模式对比。
- SN 当前的 reasoning steps 默认标记为 `partial`，因为它们通常没有完整的模型调用、工具输入输出和终止事件。
- 只有满足完整性合同的记录才运行 DeepEval trajectory metrics；缺失轨迹不能因“没有计划”而得到默认通过分数。
- Agent 分数是诊断结果，不与 benchmark 主分数合成，也不设置发布门禁。
- 先实现 reasoning Ask 的通用路径；外部 plugin、跨用户 memory、多代理 handoff 和底层模型调用树暂不纳入。

## 统一轨迹协议

评测项目使用 `AgentTraceEnvelope`，与 SN 的内部数据库 ID 解耦：

```json
{
  "schema_version": "sn-agent-trace-v1",
  "trace_id": "opaque-trace-id",
  "case_id": "benchmark-case-id",
  "mode": "reasoning",
  "status": "success",
  "completeness": "partial",
  "completeness_reason": "reasoning steps lack call spans",
  "steps": [
    {"index": 0, "type": "intent", "status": "completed", "duration_ms": 120, "summary": "...", "detail": {}}
  ],
  "final_output_available": true,
  "context_available": true,
  "citations_available": true
}
```

`none` 表示没有轨迹，`partial` 表示有序的 SN reasoning 步骤但缺少完整执行树，`complete` 需要显式完整性声明、每一步都有 index/type/status、存在终止步骤，并且最终输出、上下文和引用可用性已被记录。适配器宁可降级为 `partial`，不把未知当作完整。

## 确定性诊断

对所有可读取轨迹计算以下字段：动作序列及计数、检索动作数、重复动作数、反思轮数、fallback 次数、是否存在计划/合成/答案、终止原因、引用 anchor 数、总步骤耗时、步骤缺失和轨迹完整度。诊断结果保存在独立 JSONL，不替换主指标。

chunk/reasoning 对比按同一 `case_id` 配对，输出 reasoning 相对 chunk 的动作数、耗时和可观察状态差异；缺失配对不补零。

## DeepEval trajectory 适配

评测侧把统一轨迹转换为 DeepEval `LLMTestCase`：

- `input` 使用 benchmark 原始问题；
- `actual_output` 使用 SN 保存的最终回答；
- `expected_output` 仅在 benchmark 提供参考答案时设置；
- `retrieval_context` 使用已捕获上下文；
- `_trace_dict` 使用脱敏后的有序步骤；
- `metadata` 只保留 case、suite、mode、轨迹完整度和确定性诊断摘要。

首批允许的 DeepEval 指标是 `TaskCompletionMetric`、`StepEfficiencyMetric`、`PlanQualityMetric` 和 `PlanAdherenceMetric`。默认离线命令不调用这些指标；显式 `--judge` 才运行，并记录 judge 模型、SDK 版本、错误和 reason。`ToolCorrectnessMetric` 与 `ArgumentCorrectnessMetric` 等待 SN 有标准化 tool name/arguments/result 后再接入。

## DAGMetric

第一版提供“证据路径”DAG：先判断轨迹是否展示了资料检索，再判断是否有可用上下文/引用，最后让 judge 判断回答是否得到任务和证据支持。已知硬门通过 DAG 的固定分支映射到 0、0.5 或 1；LLM 只负责分支判断，不负责自由生成最终分数。DAG 的每个节点和最终路径会进入 `reason`/verbose 诊断。

## 结果与可重复性

离线评估器写入一个新的输出目录：

```text
agent-traces.jsonl
agent-diagnostics.jsonl
agent-scores.jsonl
agent-summary.json
agent-report.md
```

确定性诊断可以在无网络环境运行。LLM 评分只有显式开启，且不覆盖原 `scores.jsonl`。所有输出保留 `case_id`、mode、completeness、metric、status、score、reason 和错误原因；缺失项使用 `not_applicable` 或 `error`，不补 0。

2026-09-20 扩展：`agent-diagnostics.jsonl.execution` 独立保存从现有 request/intent_preview/response 读出的实际请求、原题、澄清内容、Ask 进入情况和终止阶段。它与 `trace.steps` 分开，避免将预览字段伪造为模型轨迹。`agent-summary.json` 增加输出状态、终止阶段、澄清理由计数；`agent-report.md` 展示这些统计。没有轨迹时步骤总耗时为 null，已存在的答案/上下文/引用可用性仍保留。

## 后续扩展

如果 SN 测试环境未来能提供完整 trace hook，再增加底层模型 span、tool call 参数、memory 读写和 plugin handoff；届时可以安全地启用 DeepEval tool/argument metrics。当前不为这些未来字段修改生产代码。
