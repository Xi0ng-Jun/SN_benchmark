# SN 原生 DeepEval 评测实施计划

用户已批准实施。起点：benchmark `6f64fee`，SN 隔离分支 `1b4eb2b3`。实际开始于北京时间 2026-09-22 08:56；此前未建立零点定时任务。

## 目标和边界

SN 在实际运行中通过 DeepEval 4.2.2 的公开 observe/update_current_span 产生原生 span；评测进程用 EvaluationDataset.evals_iterator 同时组织组件与可选完整轨迹指标。替换自制采集树和私有 _trace_dict 回放，不保留第二套评分链。SN 可修改并允许服务器重跑；本机只用离线数据和模型替身。不开 Dashboard、不扩展 DAG、新数据集或发布门槛。

## 实施任务和所有权

1. SN 工作树：替换 evaluation_tracing，复用业务埋点并补逐查询/合成观测；加入可选依赖与测试，更新成对的 operations 文档。关闭时无 SDK 导入，开启时业务仅执行一次。
2. benchmark 清理：删除旧离线 trajectory 私有桥接和输入模板检查命令；保留不依赖 SDK 的历史执行诊断。旧 JSON 只作档案，不迁移，不混入新结果。
3. benchmark 原生运行器：显式 judge 配置、逐题 iterator、回答与组件样本先落盘、原生 SDK 轨迹和评分导出、确定性诊断。正常 notebook 客观评分仍保留。全部分数与 judge 身份关联，错误与 N/A 不填 0。
4. 交付：相关离线回归和 SN 必需检查；提交 SN 修改并生成增量 Git 补丁/manifest；更新协议和服务器 prompt。服务器先最大轨迹题的两模式，再完成 meeting18 六题两模式，不自动扩到全量。

## 数据职责

- SN 的观测封装只拥有请求开关、具名 JSON 投影和完成通知；DeepEval 拥有 span 父子关系和生命周期。benchmark 回调定义组件样本和指标，不让 SN 业务代码依赖 judge 或 gold。
- 检索组件：实际 query 与本次文本结果。多查询必须保留逐查询映射，不能把合并结果当成各查询的结果。空检索保存事实和原因。
- 合成组件：实际 question、实际 context_block、答案正文。分节分别记录，不伪造一次全文合成。
- 轨迹：真实计划、反思、动作、逻辑 LLM 调用。无计划时 PlanQuality/PlanAdherence 不适用。完整树不截断、不摘要；超限保留 error。
- 一个题目的产品调用及回答落盘先结束，然后 SDK 执行该题评分；产品 latency/usage 与 judge latency/usage 分开。保存中途结果与原始 SDK 报告。

## 验证与重跑规则

先验证公开 SDK、线程 ContextVar、先保存后评分、错误不丢答案；已完成构造探针，实施须以新代码测试。定向测试覆盖业务等价、隔离、取消、逐查询、分节上下文、评分失败落盘及 SDK 关联。再运行评测仓库离线回归与 SN scripts/check.sh。既有失败须记录实证与适用边界，不扩散修无关代码。

QMSum request-v2 和 BM25 的旧答案/客观指标继续可用；旧 Agent 得分归档，新原生批次重新采集评分。交付不要求恢复已暂停模型任务或修改在线生产部署。

## 进度

- [x] SN 原生观测（提交 `052b7373`）
- [x] 旧桥接清理（历史纯诊断保留）
- [x] 原生运行器与输出（组件默认、整轨迹显式）
- [x] 验证、补丁、使用文档

交付证据：[验证记录](../../sn-execution-tracing-validation.md)。419 项 benchmark/跨仓库检查通过、网络尝试零；SN 最终 32 项定向回归通过，标准 gate 保留一个既有 dotenv 打包环境失败。服务器真实实验仍待执行，[prompt](../../server-agent-tracing-prompt.md)包含最大题到 meeting18 的明确顺序。
