# Silicon Notebook 评测上下文

本页记录当前仍有效的范围、证据规则和运行边界。按日期累积的阶段记录放在[历史归档](archive/README.md)，不要把历史命令当作当前入口。

当前决策与执行范围先看根目录 [CURRENT_STATE.md](../CURRENT_STATE.md)。本页的日期授权记录用于理解背景，不扩大当前“实际实验在服务器执行”的范围。

## 目标与范围

项目目标是建立 Silicon Notebook 的持续评测闭环，解释回答、检索、引用、组件和 Agent 过程的表现，并保留可复查的输入、输出、评分和配置身份。

当前评测框架使用 DeepEval，数据来源包括项目文档、真实用户问题、典型业务场景和公开评测集。评测方案仍通过实验和人工核验收敛；指标组合、阈值、样本规模和运行频率不是固定产品契约。

当前实现覆盖四条主要路径：

- Notebook 场景：QASPER、MultiHop-RAG、ALCE、QMSum、HotpotQA，协议为 `sn-notebook-benchmarks-v1`。
- 原生 Agent：SN 原生 DeepEval span，协议为 `sn-deepeval-native-v1`；默认评组件，`--trajectory` 才评完整轨迹。
- 已保存组件补评：`scripts/score_native_components.py`，从原始组件工件建立独立评分批次。
- 离线 Dashboard：`scripts/build_experiment_dashboard.py`，只读已保存 run，展示实验地图、单题回放和结果分析。

**2026-09-29 用户已明确只保留上述五套 benchmark，仓内退役清理已执行。** 其他旧 benchmark 专属实现、入口、测试、配置、受跟踪样本／结果和文档已删除；身份、runtime、状态和报告能力已迁出。QMSum BM25、五套内外部比较与 Agent／Dashboard 保留。实际删除见 [DELETION_LOG](../DELETION_LOG.md)，剩余债务见 [TRIAGE](../TRIAGE.md)。服务器与 Git 外私有工件未清理。

## 证据分层

每个结论标明来源：

1. **本地核实**：当前 checkout 中的代码、Git 元信息、离线测试或本地产物直接验证。
2. **服务器转述**：用户提供的服务器报告；不等同于本机读取原始日志或当前进程状态。
3. **人工判断**：对答案、引用、拒答或解释质量的人工标签。
4. **暂定假设**：等待实验或人工核验的设计选择。

错误、未执行、缺失和不适用保持独立状态；不补零、不按低分重试、不用摘要或候选资料冒充真实输入。组件分不能替代完整 Agent 分，旧协议的分数不自动迁移到新协议。

## 隔离与隐私边界

- 每个 Notebook 分区和 mode 使用独立 notebook、数据库、存储、索引、缓存、日志和运行目录。
- 生产 SN 主目录不由评测代码修改；SN 观测补丁通过独立 worktree 和评测仓库补丁交付。
- gold、私有语料、密钥和服务地址不进入公开结果；结果文件只保存必要的脱敏身份和哈希。
- 当前 Notebook 入口使用新的 run-dir，不支持原目录隐式续跑；重试新建 attempt 并记录来源与原因，补评写新批次，不静默重评成功题。
- Dashboard 生成新输出目录，不改原始 run，不启动 SN、judge 或数据下载。

## 当前边界

2026-09-23用户授权为正确评测与比较进行必要系统修改和重跑，不依赖历史服务器产物。当时允许在独立runtime准备公开数据、校准评分并运行新实验；生产部署和timer不在范围内。这是历史授权背景，不代表当前允许本机运行实际实验。已完成与未完成证据见[当前实验计划](notebook-benchmark-experiment-plan.md)。

2026-09-24用户进一步明确正式执行在服务器；随后单独授权利用本机代理下载MultiHop-RAG与ALCE并进行真实数据/评分实现验收。本次下载及不调用生成模型的本地校准已执行，证据见[真实数据验收](notebook-benchmark-real-data-validation.md)。后续正式SN/LLM生成及完整ALCE模型评分在服务器进行。

Benchmark 支持集合固定为 QASPER、MultiHop-RAG、ALCE、QMSum、HotpotQA，不再维护其他旧套件，也不追加新套件。交互可靠性、资料更新一致性、KG、DAG、PDF/OCR 仍不在本轮范围；未来改变支持集合须另行记录新决策。

## 阅读顺序

先看[文档导航](README.md)和[评测状态](evaluation-status.md)，再按任务查看[实验 Dashboard](experiment-dashboard.md)、[Notebook 场景](notebook-benchmarks.md)、[原生 Agent](native-agent-evaluation.md)或[评分恢复](native-scoring-recovery.md)。
