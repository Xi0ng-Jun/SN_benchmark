# SN 执行轨迹与离线 DeepEval 接入计划

> 执行方式：已获用户实施授权。按工程规则分配有明确所有权的任务，完成必要验证后交付，不新增审批阶段。

目标：新运行保存真实的意图检查、检索、动作、LLM 调用与合成过程；后续评分复用轨迹，不重复 Ask。通过评测仓库交付 SN Git 补丁，服务器不直接覆盖现有文件。

## 设计与边界

- SN 基准提交为 `74e9c61e`，在 `feat/evaluation-tracing` 独立 worktree 开发。服务器必须核对自身版本，不假定相同。
- SN 的 `app.core.evaluation_tracing` 只依赖标准库，默认关闭、无文件/网络 I/O；显式 `capture_evaluation_trace()` 建立请求级 ContextVar，`evaluation_span()` 记录具名输入输出与父子关系。仅同步 native Ask 调用树属于本期范围；线程池沿用已有 `copy_context()`。脱离请求的后台线程/插件不宣称已覆盖。
- 生产者只投影具名业务字段，不递归序列化 repository/client/settings，不记录凭据、服务地址或异常正文；LLM 记录提供给模型的消息和返回正文，不采集 provider 隐藏推理字段。这些内容只进入主动启用的本地评测产物，和 content-free telemetry 分开。
- 捕获失败不重复执行操作、不吞掉原有异常/取消、不改变回答。父 span 在进入时确定；并发子 span 保留开始时间，不能把列表顺序当作串行执行。
- benchmark 的 `--capture-agent-trace` 显式启用。根 span 覆盖 `submit_system_question`，所以澄清退出也有轨迹。记录 schema/覆盖范围/采集错误；“轨迹完整”与“回答成功”分别判断，不因答案缺失伪造步骤。
- DeepEval 使用真实 span 树的 SDK 数据模型及轨迹投影。采集阶段不启动 SDK trace uploader，不挂 judge；离线评分才加载可选依赖。旧 trace 保留 partial 语义，不能凭空补全。
- TaskCompletion/StepEfficiency 需要有效轨迹；PlanQuality/PlanAdherence 还需要显式可观察的计划。DAG 是评分决策图，不是执行图。无证据或缺少适用前提时记录 N/A，不补 0。

## 实施任务

1. SN：增加请求级采集模块和最小埋点，覆盖 Ask/intent、chunk 与 reasoning 检索、reasoning 工具动作、合成和 `chat_json`；加入隔离/嵌套/异常测试，更新中英文 operations 文档。所有改动由 SN 任务独占。
2. benchmark：增加可选采集开关、冻结运行身份、保留 trace 原始协议；构造 DeepEval 输入并修正轨迹诊断（嵌套耗时不能累加当墙钟耗时）。证明真实 SDK 能消费输入，使用假的模型响应，禁止在线 judge。
3. 针对性验证后运行 SN `scripts/check.sh` 标准 gate；不得下载数据、启动在线实验或恢复 timer。失败需区分新增回归和本机环境缺失，保留真实结果。
4. 在 SN 分支提交，生成 `integrations/silicon-notebook` 补丁包，包含 base/head、SHA256、文件清单、应用/验证/回退指引；用干净基准验证补丁可用。评测仓库保存代码、文档与补丁，SN 上游不推送。

## 最小证伪点与验收

- 如果 SDK 不能消费当前结构，修正转换边界，不把旧 steps 字典冒充原生树。
- 两个交错请求应各有自己的根与子节点；异常/取消后下一个请求无残留。
- 关闭时无新 I/O、无 DeepEval 依赖；开启时原操作恰好执行一次。
- native 澄清能保存 intent 根路径，不应被标成“遗漏 Ask”；LLM judge 不在采集路径调用。
- 服务器先用一个新的隔离 notebook 分区跑 chunk/reasoning，确认真实模型轨迹与体积；再决定实验范围。本机不以合成测试替代服务器验收。

## 实施记录

四项任务已落地：SN 实现提交 `1b4eb2b3`；补丁在原基准成功回放且 tree 一致；benchmark 能保存新执行树并转换成 SDK 原生投影。相关离线验证见 [交付验证](../../sn-execution-tracing-validation.md)。标准 gate 有一个未修改基准也复现的打包 Python 环境失败，明确保留，未修改无关产品代码。

实际 SDK 接口核验后的决定：轨迹“完整”与“非空答案”分开；DeepEval 4.2.2 要求 actual_output 非空，因此完整澄清路径保留诊断、trajectory judge 为 N/A。SN 不导入 DeepEval，评测侧只在离线评分时构造 typed spans，不使用 SDK 的 live observer/upload 生命周期。这避免运行时依赖和全局 manager 状态对生产请求的影响。
