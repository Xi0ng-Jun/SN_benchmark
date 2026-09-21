# 给服务器 Agent 的执行指令

```text
请安全同步 SN_benchmark 的 docs/public-benchmark-agent-expansion 分支，保留服务器已有 model-config、并发和执行账本修改。先阅读 AGENTS.md、docs/sn-execution-tracing.md、docs/sn-execution-tracing-validation.md、integrations/silicon-notebook/README.md 和 manifest.json。

本轮目标是启用新的 SN Agent 执行轨迹。按补丁包说明核验 SHA256 与 SN 基准，在独立 SN 分支/worktree 用 git am --3way 应用补丁；有冲突时保留两侧必要修改，形成干净提交。不要覆盖生产文件或重启正在运行的 SN，不中断正在进行的实验。复用已有模型配置注入，注意新 worktree 不自带被忽略的配置文件。无需给 SN 安装 DeepEval。

完成必要离线检查后，选一个已冻结的 QMSum 分区，chunk/reasoning 各创建新 run-dir，沿用 notebook-request-v2，并加 --capture-agent-trace。先查看 product_record.execution_trace：父子 span、实际输入输出、关闭状态、capture_errors、意图/检索/计划/动作/合成/LLM 阶段。接着用 evaluate_agent_traces.py 默认离线模式出报告，确认澄清、错误与缺计划不会被误计成分数。

本轮先完成这个单分区真实验收，不扩大到全量、不重新跑旧 QMSum 对照、不跑 Dashboard 或在线 judge、不恢复 timer。遇到与本次无关且不阻塞验收的既有问题如实记为限制，继续推进。汇报实际 SN/benchmark 提交、应用补丁情况、两个 run-dir、成功/澄清/错误数、轨迹完整度及缺失原因、代表轨迹和产物体积；不要只汇报“接口正常”。
```
