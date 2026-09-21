# SN 执行轨迹：采集、离线评分与服务器交接

本阶段给 SN 增加可选的执行观测，并让评测项目读取它。默认不采集；只有新的 Notebook 实验显式传 `--capture-agent-trace` 才启用。评分仍由另一条命令执行。

SN 修改交付在 [Git 补丁包](../integrations/silicon-notebook/README.md)，实现依据见[计划](superpowers/plans/2026-09-21-sn-execution-tracing.md)。这次授权允许修改 SN 的观测代码；原来的“不改 SN”是此前阶段的边界。本次不部署、不启动模型实验、不改运行中的生产配置。

## 两边分别做什么

| 所属项目 | 入口 | 责任 |
| --- | --- | --- |
| SN | `app.core.evaluation_tracing` | 标准库、请求级 ContextVar；记录具名 span，默认关闭，无网络、文件或日志输出 |
| SN | AskService / RetrievalService / ReasoningRetriever / `chat_json` | 显式投影业务输入输出；标记 intent、retrieve、plan、reflect、action、synthesis、llm |
| benchmark | `scripts/run_notebook_benchmarks.py --capture-agent-trace` | 验证补丁 API；运行身份记录采集 schema/scope；照原有隔离协议导入与提问 |
| benchmark | `system_runtime.run_system_question` | 在 intent preview **之前**建立 `sn.request` 根，结束后保存原始 `execution_trace` |
| benchmark | `sn_trace.py` | 验证树与生命周期；转换成 DeepEval typed spans 和其官方轨迹投影 |
| benchmark | `scripts/evaluate_agent_traces.py` | 默认只做本地诊断；显式 `--judge` 才调用 Agent LLM 指标 |

这里没有给生产 SN 装 DeepEval。SN 记录通用执行数据，评测侧使用 DeepEval 的 `AgentSpan`、`RetrieverSpan`、`ToolSpan`、`LlmSpan` 和 `trace_manager.create_nested_spans_dict`，与 SDK 的轨迹评测使用同一投影。没有调用 `start_new_trace`、`@observe` 或上传队列；`create_nested_spans_dict` / `LLMTestCase._trace_dict` 属于 SDK 内部接口，升级 SDK 需跑已有兼容测试。本次实际兼容验证版本为 DeepEval 4.2.2。

这符合 [DeepEval 的 trace/span 评测模型](https://deepeval.com/docs/evaluation-llm-tracing)：trace 表示一次应用调用，span 表示其中的组件或步骤。我们选择本地采集、离线转换，以满足不把 SN 绑定到评测 SDK、不隐式上传内容的约束。它不是 Confident AI 云端 trace 导入流程。

## 一道 QMSum 问题的轨迹

以下为结构示意，具体步骤以原生执行为准，不是新增实验结果：

```text
sn.request                         实际提交的问题；输出状态与最终答案
├── sn.intent                      reasoning 才执行；可能在此澄清退出
│   └── sn.llm.chat_json            意图模型实际消息与返回正文（若实际调用）
└── sn.ask
    ├── sn.reasoning.run           reasoning 检索
    │   ├── sn.reasoning.plan      实际产生的子查询计划
    │   ├── sn.action.search_chunks
    │   ├── sn.reasoning.reflect   结构化继续/停止/动作决策
    │   └── …                     实际执行的其它动作
    └── sn.synthesis.reasoning
        └── sn.llm.chat_json        最终回答所用消息与返回正文
```

chunk 路径没有 reasoning 的意图预览/规划循环，通常为 `sn.request → sn.ask → chunk 检索与合成 → chat_json`。不会为它捏造 plan 或 reflect。BM25 不走 SN，本阶段采集开关只接 Notebook 的 SN chunk/reasoning 路径。

`chat_json` 是一次**逻辑模型调用**：内部可能命中缓存、发生重试或多次 HTTP 请求。span 数量不是付费请求次数；费用/token 沿用原有 usage 产物。provider 的 `reasoning_content` 等隐藏推理字段不采集；显式返回的子查询/动作/理由属于程序可观察输出。

并发检索的兄弟节点按实际进入顺序落盘，保留各自开始/结束时间及 parent_id；不把顺序解释为串行。已有 `copy_context()` 传播请求作用域；请求结束后过期线程不能继续改写已封存轨迹。非请求上下文的后台工作和扩展内部过程不属于完整性承诺。

## 产物与完整性

新运行的 `outputs.jsonl` 每条 `product_record.execution_trace` 保存：

```json
{
  "schema_version": "sn-execution-trace-v1",
  "trace_id": "…",
  "closed": true,
  "spans": [{
    "span_id": "…", "parent_id": null,
    "name": "sn.request", "kind": "agent",
    "metadata": {"stage": "request", "mode": "reasoning"},
    "input": "实际问题", "output": {"status": "success", "answer": "实际答案"},
    "status": "completed", "start_time": 1.0, "end_time": 2.0, "duration_ms": 1000.0
  }],
  "capture_errors": [],
  "duration_ms": 1000.0
}
```

时间为同进程 monotonic 时钟，不能拿它直接对齐另一台机器。以上 JSON 仅说明字段，不是一条可以评为完整的 success 样本：真实 success 还需要 Ask、检索、合成与 LLM 观测。

旧 `trace` 字段继续表示兼容的 reasoning 摘要，常规答案分数产物不会复制整棵原始调用树。Agent 命令优先读取 `product_record.execution_trace`，没有新字段时保留旧诊断路径。

完整性检查包括 schema、关闭状态、采集错误、唯一 ID、父节点可达性、一个 request 根、时间有效性与父子包含关系、根输出状态、应有阶段。`complete` 指 **native 同步 Ask 的已声明观测范围**；它不是“没有程序错误”“有答案”或“答案正确”。澄清和业务错误路径也可以被完整记录。

输出捕获/投影失败不重做原操作，原结果与原异常保持；轨迹记为 partial。正常返回的 error/clarification 会保存；进程被强制终止时内存轨迹无法保证落盘，不能把缺记录当作零调用。

这些产物包含题目、证据与模型消息，属于本地评测内容，不是 content-free 运行日志。显式导出的私有语料结果不得作为普通源码提交到 Git；共享时按已有数据规则筛选。服务器先检查单分区轨迹体积，再扩大采集范围。

## 可算哪些指标

| 项目 | 方法 | 前提与解释 |
| --- | --- | --- |
| 轨迹闭合、步骤/动作数、终止阶段、耗时 | 确定性检查 | 不调用 judge；总耗时用 request 墙钟，按类型累计耗时可能重叠 |
| TaskCompletion | DeepEval LLM Judge | 有通过完整性检查的新轨迹和非空答案；答案是否完成任务由 judge 判断 |
| StepEfficiency | DeepEval LLM Judge | 同上；观察轨迹的执行效率，不能替代真实费用或延迟测量 |
| PlanQuality / PlanAdherence | DeepEval LLM Judge | 额外需要可观察的显式 plan；chunk 或缺 plan 时 N/A |
| SN Evidence Path DAG | DeepEval DAGMetric | 额外需要最终答案和检索上下文；沿预设判断分支打分，不是执行轨迹图 |

DAG 的 BinaryJudgementNode **每个节点都用 LLM 判断**；提供给它的检索/引用观测来自确定性诊断。最终支持性节点现在也拿到 `retrieval_context`，而不只是问题和答案。尚未人工校准，不作为发布门禁。

旧摘要即使自报 complete，也不会启动新的轨迹 judge。DeepEval 4.2.2 的这些指标要求非空 actual_output，所以完整澄清轨迹仍保留 complete，但不进入这组 judge，记 `final_output_missing`；不伪造一段答案绕过 SDK 前提。没有显式计划不会扣计划分；没有答案/上下文不会启动证据支持 DAG；N/A 保留理由，不填零。

## 服务器使用顺序

1. 拉取评测分支并阅读补丁包 README。检查服务器 SN 版本，在新的 SN worktree 应用补丁；保留服务器现有 model-config/并发修复。
2. 沿用服务器已有的隔离模型配置方式，为新 worktree 提供只读配置输入。worktree 不自动携带原仓库被忽略的 `.env` / `.local/model-services.toml`，不要因此修改生产配置或误用空配置。
3. 首先在一个已冻结 QMSum 分区分别运行 chunk 和 reasoning。使用新的 run-dir，并沿用 request-v2：

```bash
python scripts/run_notebook_benchmarks.py \
  --project-root /path/to/sn-evaluation-worktree \
  --bundle /path/to/frozen-qmsum-bundle \
  --partition-id ACTUAL_PARTITION_ID --mode reasoning \
  --request-revision notebook-request-v2 --capture-agent-trace \
  --run-dir /path/to/new-traced-run
```

服务器已存在的 `--model-config` 等参数按其现有代码保留；本机未收到该补丁，不在这里假定参数实现细节。chunk 用相同分区、不同 run-dir 和 `--mode chunk`。

4. 默认离线命令先查看实际树/采集错误与阶段。此步骤不调用 SN 或 judge：

```bash
python scripts/evaluate_agent_traces.py \
  --run-dir /path/to/new-traced-run \
  --output-dir /path/to/new-agent-diagnostics
```

输出 `agent-traces.jsonl`（含原始执行数据）、`agent-diagnostics.jsonl`、`agent-summary.json`、`agent-report.md`；默认 `agent-scores.jsonl` 为空。完整性不足时先定位具体缺失 span，不扩大实验规模掩盖问题。

5. 确认真实轨迹可用后，以独立评分目录启动已配置的 judge，无需重问 SN：

```bash
python scripts/evaluate_agent_traces.py \
  --run-dir /path/to/new-traced-run \
  --output-dir /path/to/new-agent-scores \
  --judge --judge-model YOUR_CONFIGURED_JUDGE \
  --metrics task_completion step_efficiency plan_quality plan_adherence
```

`--dag` 是额外选择，不要把 DAG 得分合并进 ROUGE。追踪打开/关闭会改变运行身份且增加采集开销，不能将不同开关下耗时差直接解释为检索模式收益。服务器真实验收后再决定批量范围；无需重跑已经完成的 QMSum 质量对照。
