# 公开评测扩展代码补齐

后续更新：本文件记录 Native 补齐阶段，其“新增 Product 不适用”边界已由[SN 系统适配实施计划](2026-09-13-sn-public-system.md)扩展。旧 N/A 保留为显式 legacy 路径；这里不回填历史验收结论。

本轮执行已批准的补齐范围，仅编写代码与回归用例，不运行应用、测试或在线评测。此前 2026-09-11 计划被全部勾选不代表已验收。

## 根因与实现顺序

1. 新套件准备未保存 SDK 身份，Native 请求只支持旧套件：统一扩展 case 重建，冻结本地 DeepEval 4.2.2 模板、scorer 和提示词资源，为五套扩展实现原生请求/评分分派。TruthfulQA 使用官方 MC1 协议，不用自由文本完全匹配代替 truthfulness。
2. Product 缺少审核语料时不能生成成绩：集中声明当前支持状态，在任何模型配置、SDK 导入或产品运行时之前保存逐题 not_applicable 计划、输出、评分和终态；不删除这些题目分母。MMLU/GSM8K/TruthfulQA 仍为未来 Product 候选，语料适配不在本次猜造。
3. 新字段破坏旧报告：保留旧身份字段的验证，在汇总层建立不修改原记录的兼容视图；完成度与 Agent metrics 是否实现分开报告。
4. 审计不能只检查字段存在：复用 bundle 的原始数据重建和字节 hash 校验，拒绝格式不明、缺失工件和路径越界。
5. 写入请求构造、答案篡改、N/A 无依赖记录、历史报告兼容等回归用例，更新状态与协议说明。本轮仅静态阅读，不宣称测试通过。

## 文件边界

- Native/数据：public_expansion_native.py、public_expansion_sources.py、public_expansion_protocol.py、starter_native.py、prepare_public_starter.py。
- 编排/报告：starter_runner.py、starter_not_applicable.py、starter_results.py、starter_report.py、run_public_starter.py。
- 离线审计与交付：check_public_expansion_offline.py、对应回归用例、README、evaluation-context/status、development-roadmap、扩展设计。

## 后续验收（本轮不执行）

- 使用本地 SDK 和模拟模型贯通五套 Native 的 prepare/request/prediction/scoring/report。
- 证明扩展 R 的 N/A 路径无 SDK、模型、SN 依赖；分母等于请求题数，分数为 null。
- 测试修改参考答案并重写 cases hash 仍被 raw 重建拒绝。
- 验证旧五套兼容性与 runtime 隔离；得到用户恢复意图后另开小样本在线实验。

## 本轮代码交接

| 范围 | 写入的逻辑 | 验证状态 |
|---|---|---|
| Native | 五套官方模板/schema/score 分派，TruthfulQA MC1，BBH 按 task 选择 schema | 对照本地 SDK 源码静态审阅；未执行 |
| 数据身份 | 扩展 v2、raw 重建、字节与语义 hash、SDK 源码和提示词资源快照 | 已写防答案篡改与 SDK 身份回归用例；未执行 |
| Product N/A | 无模型配置也可逐题记录，保留分母、null 分数和空调用日志 | 已写五套编排至报告的隔离回归用例；未执行 |
| 报告 | 旧台账兼容视图、不适用计数、诊断字段、Agent 未实现标记 | 已写旧身份篡改与 complete trace 回归用例；未执行 |
| 审计 | bundle 重建与 run 台账检查分开；拒绝缺失输入和目录外工件 | 已写缺失路径、无 SDK 身份和路径越界回归用例；未执行 |

本轮修改集中在上述代码、对应 tests、README、evaluation-context/status、development-roadmap、扩展设计与原实施清单。源码静态审阅不能证明运行成功；未导入 SDK、未编译应用、未执行 pytest 或 smoke，也未冻结新数据。

独立静态审阅已对照本地 DeepEval 4.2.2 源码核对五套 Native 的模板签名、答案映射、schema 和 scorer，并检查 N/A 与历史报告兼容。审阅发现的 SDK 快照根目录符号链接越界已增加 loader 约束与回归用例；这些变更仍待执行验证。`git diff --check` 仅用于检查补丁格式，不算测试或接口验收。

后续先做离线接口与模拟模型回归，再准备真实公开数据，最后在在线任务恢复后做小样本 Native。MMLU/GSM8K/TruthfulQA 的 Product 语料适配、真实 Agent 轨迹采集及 metrics、DAG 均需后续独立实施；当前候选标记与 trace 字段不表示它们已完成。生产代码、未完成 baseline、weekly timer 和质量阈值不在本轮修改范围。
