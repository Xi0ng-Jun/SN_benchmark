# 给服务器 Agent 的执行指令

```text
请同步评测分支 docs/public-benchmark-agent-expansion，保留服务器已有模型配置、并发和执行账本修复。阅读 AGENTS.md、docs/native-agent-evaluation.md、docs/sn-execution-tracing-validation.md，以及 integrations/silicon-notebook/README.md 和 manifest.json。

本轮改用 SN 原生 DeepEval 4.2.2。校验 SHA256，在“已应用旧 tracing 补丁”的服务器 SN 评测提交上新建独立 worktree，应用本包增量 patch（git am --3way）；不要从未打旧补丁的生产 HEAD 直接套用。解决冲突时保留服务器原有必要修复，形成干净提交。使用独立评测 Python 环境同时安装 SN 依赖与固定 SDK，不改生产环境、不重启生产服务。完成相关离线检查和跨仓库契约检查；既有非阻塞环境问题记录后继续，不反复审计历史结果。

用新的 scripts/run_notebook_agent.py 执行冻结 QMSum meeting18：先选 qmsum:18:specific:3，chunk/reasoning 各用新 run-dir，明确 --request-revision notebook-request-v2 --trajectory；--model-config 提供 SN 模型 TOML，--judge-config 提供独立 judge JSON。沿用已经成功承载最大完整轨迹的 DeepSeek judge 配置，准确记录实际 ID/参数；生成模型与 judge 分开。--case-id 只选题，完整会议资料仍全部导入。

确认最大题两模式都保存原始回答/组件、SDK 原生轨迹及组件和整轨迹分，且没有观测错误。链路通过后完成 meeting18 六题×两模式，不等待下一次批准，也不扩大到其他会议。可以用显式 case-id 选择剩余五题避免重问最大题，保留分批身份。judge 超限、超时或评分错误单独记录，不裁剪/摘要轨迹、不补零、不按低分重试；真正阻塞完整链路时停在具体问题上，不批量重复失败请求。

主要检查 outputs.jsonl、agent/components.jsonl、native-traces.jsonl、native-scores.jsonl、native-diagnostics.jsonl、native-summary.json 及 sdk/ 下报告：检索评分使用实际 query 与该次证据；合成评分使用实际上下文；多查询/分节分别关联；chunk 没计划记 N/A；评分失败不能改变答案状态。回答和组件必须先落盘，再调用 judge。

旧 QMSum request-v2/BM25 答案和客观分保留；旧 Agent 分、GLM 超窗记录归档，不能混入新协议。不要使用已删除的 --capture-agent-trace、离线 --judge/--dag 或 inspect_agent_inputs.py。不要跑 Dashboard、恢复 timer、全量重问旧 benchmark 或扩展 DAG。

完成后交付一份简明报告：实际 benchmark/SN 提交与配置身份；两模式题单覆盖及成功/澄清/错误；逐组件指标与真实查询/合成样本关联；整体 Agent 指标和 N/A/error 原因；产品与 judge 各自的耗时/usage；从低分及对照中选2–3个具体案例说明SN哪一步出了问题。保留全部原始工件，不只报告均分或“接口通过”。本轮任务不需要再生成旧数据审计报告。
```

参数完整示例及各文件职责见[原生协议](native-agent-evaluation.md)。该指令授权服务器执行本轮最大题和 meeting18 验收；本机交付只完成离线实现与验证。
