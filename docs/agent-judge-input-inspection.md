# Agent 评分输入过长：现状、检查与推进顺序

> 历史记录：本页描述的是此前阶段。2026-09-22 起 Agent 采集/评分以[原生 DeepEval 协议](native-agent-evaluation.md)为准；旧 `--capture-agent-trace`、`--judge/--dag` 和 `inspect_agent_inputs.py` 已退役。旧实验结论及原始工件保留，不执行下文的旧命令。

2026-09-21。目标是复用已保存的 QMSum meeting18 轨迹，继续推进 Agent 评测。当前先量清评分输入，再选择能承载完整轨迹的 judge；暂不实现轨迹压缩，不重跑 SN。

## 目前发生了什么

以下是用户转交的服务器 `prompt6` 报告，本机未读取服务器原始评分文件：

| 项目 | chunk | reasoning |
| --- | --- | --- |
| SN 回答与完整轨迹 | 6/6 成功 | 6/6 成功 |
| 报告中的轨迹字符数/题 | 约 12.8–14 万 | 约 98–113 万 |
| Agent judge 结果 | 12 项 scored | 24 项 error，均为上下文超限 |
| 指标 | TaskCompletion、StepEfficiency | 另加 PlanQuality、PlanAdherence |

服务器使用 DeepEval 4.2.2、GLM-5.2-spec。网关报告上下文上限 202752 tokens，输入为 **at least 202752** tokens；这不是输入恰好等于 202752 的精确计数。reasoning 此次没有 Agent 分数，不代表 Agent 得零分。整轮 run-dir 的 109M/124M 包含其它工件，不能用作一次 judge 请求的大小。

chunk 的 TaskCompletion 均为 0.9，StepEfficiency 均为 0.75。相同分数本身不能证明 judge 失效；SDK 的效率量表本就包含 0.75 锚点。已有理由仍可用于案例解释，不要求为获得分数差异而反复评分。

服务器已验收轨迹采集、闭合与阶段覆盖，本次无需重做采集验收。局限是 **评分模型装不下完整的 reasoning 输入**。

## 本次最小实现

新增 `scripts/inspect_agent_inputs.py`，只读取现有 `outputs.jsonl`，输出三份体积诊断工件。它复用正式评分的记录适配、完整性/指标前提检查和 DeepEval 官方 span 投影，不调用 SN、judge 或在线 token 计数接口。

```text
outputs.jsonl
  → 原有记录适配 / 完整性检查
  → envelope.to_deepeval_dict()（与评分相同的树）
  → DeepEval serialize_to_json(indent=2)
  → SDK 原版模板（能提前确定的请求）
  → 大小、来源、重复内容位置报告
```

| 产物 | 内容 |
| --- | --- |
| `agent-inputs.jsonl` | 逐题轨迹/提示词字符数、UTF-8 字节数和 SHA256；逐步骤字符串大小；重复字符串哈希与位置；各指标前提 |
| `agent-input-summary.json` | 输入文件 SHA256、SDK 版本、检查状态计数、可选字节预算、测量范围 |
| `agent-input-report.md` | 逐题表与每题占用最大的三个步骤，供人阅读 |

报告不复制证据正文、完整 prompt 或原始轨迹。原始内容继续在源 run 中；新输出目录必须位于源 run 外且此前不存在。SDK 转换/模板失败记为检查 error，不冒充成功；CLI 有此类错误时返回 1。

### 量的究竟是什么

当前默认文本轨迹指标中的以下请求可以原样提前构造，无需模型输出：

| SDK 模板 | 使用指标 | 是否包含完整轨迹 |
| --- | --- | --- |
| `TaskCompletionMetric.extract_task_and_outcome_from_trace` | TaskCompletion | 是 |
| `StepEfficiencyMetric.extract_task_from_trace` | StepEfficiency、PlanQuality、PlanAdherence | 是 |
| `PlanAdherenceMetric.extract_plan_from_trace` | PlanQuality、PlanAdherence | 是 |

这些是可以确定的静态请求，**不代表全部请求**。后续效率、遵循度、质量评分还需要 judge 提取的任务、计划或结果，不能在离线阶段猜造这些返回值。PlanQuality/PlanAdherence 缺显式计划仍不适用；无答案仍不评分。已测 prompt 列表可能为空，不是零 token 请求。

代码直接使用安装版本的 `deepeval.templates.resolve_template`，没有抄写评分指令。真实 SDK + 本地假 judge 回归按完整 prompt 的哈希核对三类模板；本次验证版本为 4.2.2。SDK 升级或服务器自定义 `evaluation_template` 后，需要重新确认该对应关系；本工具当前仅覆盖项目默认模板，不覆盖 DAG。

**字节/字符不是 token。** 报告的 `token_count=null`、`context_fit=unknown` 是有意保留的不确定性。HTTP 消息封装、JSON schema、服务商额外提示、tokenizer 和输出预留都不在这些文本大小里。`--max-prompt-bytes` 只支持操作者自定的正整数字节预算，检查已测静态 prompt；`within_byte_budget` 不表示模型一定能装下，`over_byte_budget` 也不是质量失败。默认不设预算，此开关不会修改或自动拦截现有评分命令。

步骤来源统计按**自身字段中的字符串值**计数，包含 JSON 引号/转义，不包含子步骤。`subtree_string_json_bytes` 是含子步骤的诊断值，父子不可相加。`other_json_bytes` 包括键名、非字符串标量和 JSON 排版。整个轨迹的字节数以完整 SDK 序列化结果为准。

重复检测只匹配至少 256 字符的**完整字符串值**，按 SHA256 分组；报告显示最大的 20 组，每组最多 10 个位置，另保存全部符合条件组的统计。256 是展示筛选规则，不是截断输入的规则。不检测长 prompt 内嵌的相同段落；重复字节数不是“可安全删掉的字节数”。工具返回与模型输入中出现同一段证据，可能正是实际数据流；发现重复后需结合位置判断原因。

## 服务器现在执行什么

安全合并评测开发分支的更新，保留服务器已有 `--model-config`、并发和其它修复。**不需要重新应用 SN 补丁。** 使用原评分虚拟环境的 Python，无需新增依赖或下载数据。仓库根目录执行：

```bash
python scripts/inspect_agent_inputs.py \
  --run-dir runs/qmsum-trace-accept/meeting18-chunk \
  --output-dir reports/agent-input-meeting18-chunk

python scripts/inspect_agent_inputs.py \
  --run-dir runs/qmsum-trace-accept/meeting18-reasoning \
  --output-dir reports/agent-input-meeting18-reasoning
```

如果这些报告目录已存在，使用新的后缀，不覆盖原报告。命令关闭 DeepEval telemetry 和 dotenv 自动加载，不要求 judge 密钥。运行结束只需汇报：

1. 两模式共 12 题的检查状态、最大已测 prompt 大小及对应 case_id。
2. reasoning 主要空间来自哪些步骤/字段；重复位置是否指向实际多次读取，或暴露了额外复制。不要仅凭重复率宣称适配器有错。
3. 服务器现有可用 judge 的实际输入限制与输出预留；如有匹配的本地 tokenizer，再记录计数方法。不要用字符数除常数估算出“精确 token”。

这轮只做上述离线检查和配置核对；不重问 SN、不再调用已确定超限的评分请求、不跑 Dashboard。

## 拿到结果后如何继续

优先判断现有可用 judge 是否支持完整输入。若存在合适配置，下一轮先对**最大的已保存 reasoning 轨迹**跑 TaskCompletion 和 StepEfficiency，验证整套多阶段请求，而非只验证第一段提取。成功后在同一新 judge 配置下对 chunk/reasoning 各 6 题形成新的可比较批次；旧 judge 的分数独立保留。若仍发生超限，停止扩大该配置，保留 error 和调用信息。

如果没有足够窗口的 judge，转向 DeepEval 支持的组件评测：例如最终合成的答案/上下文，或一次检索与其输入/输出。这需要单独定义组件的数据与指标；本次未实施，组件分数也不求平均冒充整条 Agent 的分数。完整轨迹仍保留供检视。

这是基于 SDK 限制的项目推进顺序，不是 DeepEval 官方提供的自动长轨迹压缩方案。官方支持的两类评测范围可参考[轨迹评测](https://deepeval.com/docs/evaluation-trajectory-based-llm-evals)与[组件评测](https://deepeval.com/docs/evaluation-component-level-llm-evals)。自定义裁剪、摘要、证据字典替换属于新的适配方案，本次不实现，也不将其宣称为原指标等价输入。

## 本地验证范围

新增 3 项定向回归，连同 Agent 评分适配、真实执行树、离线编排相关回归共 25 项通过。覆盖父子不重复计数、中文 JSON 转义、重复来源定位、三类实际 SDK prompt 一致性、无网络调用、指标不适用条件、输入不变与输出隔离。CLI 另在阻断网络的构造样本上实际执行，三份工件和源文件哈希核对通过；帮助入口、Python 编译与 Git 空白检查通过。SDK 有一条既有 asyncio 弃用警告。数据为构造 fixture，不是服务器真实 12 题；本机未调用在线模型或下载数据。
