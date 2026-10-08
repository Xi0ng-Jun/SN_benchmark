# 当前状态

更新：2026-10-08。适用范围：Silicon Notebook 评测仓库。本文记录当前决策依据；范围改变时更新对应条目，历史证据另存，不在这里追加交接流水账。

## 目标

正确使用 benchmark 评测[agentic支持以及展示支持] SN，正确地与已有方法比较，得到可追溯、可解释的结果，支撑算法迭代与论文实验。

**只保留 QASPER、MultiHop-RAG、ALCE、QMSum、HotpotQA 五套 benchmark。** 其他旧 benchmark 专属内容已从当前工作树删除，共用职责已分离，见 [删除记录](DELETION_LOG.md)；服务器与 Git 外私有工件未清理。协议、官方评分与比较入口已实现并有离线验证；**代码就绪不等于服务器全量实验通过**。当前先验收 SN 自身，服务器进度以实际工件为准，本次未复核。

## 核心不变量

- **生成不见 gold。** 输入资料、选题、请求和 scorer 版本可追溯；不得向生成注入答案／标注，不偷偷删题或缩小任务资料范围。
- **结果状态真实。** success、no_answer、clarification、error、missing、pending／不适用按各自语义保留；缺分不伪装成有效零分，不按低分重跑或只选成功题，分母遵守明确评分协议。
- **证据来自实际输出。** 引用、检索排名和 Agent 轨迹必须有真实观测／显式预测及可核验映射；上下文覆盖诊断、组件分不能冒充官方证据分或完整 Agent 分。
- **失败不抹掉已完成工作。** 回答及组件先于 judge 评分保存，指标完成即落盘；补评写新批次，超时后的迟到结果不覆盖既定终态，保留原工件。
- **比较条件可核验。** 核对 bundle、scope、case IDs、scorer 和实际分母，披露模型／提示／预算差异；作者复现、适配方法和公开答卷分别标识，不把端到端分差直接归因于某模块。
- **实验与生产隔离。** 数据库、存储、日志和运行配置使用专用目录；不改生产数据、泄露密钥或将私有运行目录整体公开。

## 活跃约束

- 本机做开发、文档和不调用模型的离线检查／测试；实际 SN、生成模型、judge 和官方模型评分在服务器执行。当前整理任务不启动实验。
- 当前服务器范围为 **SN-only**：五套 chunk，另加 MultiHop reasoning；先 smoke 后 full。外部方法／reference 暂缓，专门 Agent judge 评测需单独安排，不由普通 reasoning 运行自动产生。
- 新正式 Notebook 运行使用 `notebook-data-v3`／`notebook-request-v3`；原生 Agent 使用 `sn-deepeval-native-v1` 和 DeepEval 4.2.2。Python API 部分旧默认仍存在，新调用显式选版本。
- 一个 partition × mode × attempt 使用新进程、新 run-dir；当前入口不支持原目录隐式续跑。实际源码、模型、scorer 和依赖身份在服务器冻结，结构预检不能代替环境或模型对齐验收。
- **共享输入／源码已实现，服务器迁移待验收。** `prepare --install-bundle ... --artifact-root ...` 安装已有 bundle，并将受信任输出 index ID 冻结到 campaign 配置；新 run 必须同时传 `--artifact-root`／`--artifact-index-id` 复用不可变 bundle/index/evaluator/SN 对象，不能每次从可变 pointer 重取预期 ID。runtime 仍独立；单 run 自动报告只校验所用 capsule，正式 export 完整校验 canonical bundle。
- **完整审计与回传答卷分开。** outputs/Agent 工件保留完整事实；SN submission 使用五套 compact scoring projection，保留证据回放字段、状态与来源 hash。`package_benchmark_results.py --mode results|review` 按角色生成私有包；无自动删除、TTL、GC，不改原 run/runtime。具体边界见[结果存储与导出](docs/result-storage-and-export.md)。

## 已废弃假设

- “必须维持旧成绩、不能大改”已不成立：正确性优先，允许必要系统修改和服务器重跑；五套与共用历史可保留，退役 benchmark 专属内容已删除，不恢复其旧测试／reader。
- “曾授权本机下载／校准，所以可以继续本机实验”已不成立：历史例外不扩大当前执行范围。
- “同 benchmark、同名指标或同名模型就能直接比较”不成立：还需验证输入、评分和方法条件；当前没有自动完成模型对齐的 gate。
- “运行结束／离线测试通过／已有公开答卷就代表正式结果完成”不成立：还需范围对账、官方评分和服务器证据；普通 reasoning 也不代表专门 Agent 评测已完成。

## 已知债务与暂缓原因

- **五套内部版本默认与输入规则仍分散。** 旧 benchmark 专属路径已删除，共用职责已迁到独立模块；Python API 旧默认、五套历史 reader、预检与运行器的题单解析差异仍待收敛，见 [TRIAGE](TRIAGE.md)。
- **重复不可变输入／源码已收敛，可变成本仍需实测。** 共享模式和 compact 导出已实现；旧物理副本仍逐份验证，导入／数据库／建索引仍按 run 隔离，campaign 靠执行者展开。服务器实际 runtime、证据体积、I/O、RSS 与阶段耗时尚未核实，不能据离线样例宣称全量性能收益。
- **环境交付与报告分散。** SN／scorer／权重分别准备，Dashboard、官方比较和组件补评使用不同工件。暂不做统一平台，先明确身份及转换契约，避免重复计数或误合分数。

以上是当前工程取舍，**不构成暂缓修复正确性缺陷的理由**。具体风险和验证入口见[当前架构](ARCHITECTURE_CURRENT.md)。

## 明确不做

- 不再维护五套之外的旧 benchmark；本阶段不新增 benchmark，不启动外部方法实验，不以整理为由重写整套框架。
- 不触碰生产部署／timer，不追加 KG、长期记忆、交互可靠性、资料更新、PDF/OCR 或完整模块消融 campaign。
- 不制造跨套件总分、自动发布门槛、无证据的显著性／收入结论；不将未验证项标为完成。

操作见 [RUNBOOK](RUNBOOK.md)；实验事实见[验证与实验记录](docs/evaluation-status.md)；评分契约见[标准与实现符合性](docs/notebook-benchmark-standards-and-conformance.md)。新决定改变上述状态时，同步受影响文档；删除事实记录于 DELETION_LOG，其余保留必要历史证据。
