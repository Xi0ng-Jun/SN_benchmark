# 评测项目开发方向与保留的设计依据

本页保留共用工程路线的取舍。2026-09-29 起仅支持 QASPER、MultiHop-RAG、ALCE、QMSum、HotpotQA；当前执行范围与下一步以[当前状态](../CURRENT_STATE.md)及[评测状态](evaluation-status.md)为准。

## 当前目标与验收顺序

先在服务器验收 SN 自身的五套 chunk 和 MultiHop reasoning，按独立 runtime 执行 smoke 后 full。冻结数据、模型、scorer 和依赖身份，先对账完整分母再解释分数。外部方法执行及专门 Agent judge 另行安排；代码和离线测试通过不代表正式实验完成。操作见[实验计划](notebook-benchmark-experiment-plan.md)及[服务器手册](notebook-external-campaign-runbook.md)。

原生 Agent 使用真实 SDK span，回答和组件先保存再评分，完整轨迹显式开启。组件补评从保存工件建立新批次，不迁移旧私有 Agent 分；错误与超时不能抹掉已完成回答。见[原生 Agent](native-agent-evaluation.md)和[评分恢复](native-scoring-recovery.md)。

## 共用工程的交付判断

| 工作 | 交付 | 验收依据 |
| --- | --- | --- |
| 能力与观测对应 | 场景、预期行为、真实采集字段、指标与缺失条件 | 每个主指标对应产品问题及可靠输入 |
| 逐题解释 | 同一 ID 的原文、请求、答案、真实上下文、引用及评分 | 产品事实、judge 结果和人工判断分别标识 |
| 运行状态与报告 | 计划、完成、失败、跳过、缺失和报告新鲜度 | 没有结束记录时不因进程消失推断成功；报告可从保存工件刷新 |
| 人工复核 | 正确性、忠实性、引用支持、可回答性标签 | 人填写标签；能区分产品行为、数据适配、judge 分歧和采集缺陷 |
| 比较 | 固定 scope、身份、逐题配对和模型条件 | 协议／输入不同不宣称质量回归；成本未知不补零 |

## 保留的后续设计

[能力矩阵](product-capability-matrix.md)、[数据契约草案](evaluation-data-contract.md)及[能力详细方案](product-capability-evaluation-plan.md)保留为分析产品行为的依据。它们的字段和预算仍是历史设计，是否进入实现取决于当前需求。

[业务广度候选](product-capability-breadth-plan.md)包括综合比较、摘要、来源范围、澄清／拒答、清单完整性、多轮及 Memory／偏好；[上下文说明](memory-and-agent-context.md)区分 Memory、笔记本理解、策略经验与会话历史。它们不构成新增 benchmark 或当前执行清单。中文和半导体候选需来源、许可、脱敏、版本与人工证据，模型生成问题只作为候选。

新增缓存、调度或统一平台前，先取得实际耗时、磁盘及失败数据并明确失效规则。KG、重排、PDF/OCR、生产部署、timer、交互可靠性和资料更新一致性仍按当前状态中的非目标处理。质量门槛要有独立人工校准依据。
