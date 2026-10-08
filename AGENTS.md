# benchmark-deepeval 工作约定

这是 Silicon Notebook 的评测实验与持续评测项目。评测方案仍在通过调研、实验和人工核验逐步收敛，不要把暂定做法写成固定产品契约。

## 当前已确定

- 目标是建立 Silicon Notebook 的持续评测闭环。
- 评测框架使用 DeepEval。
- Benchmark 仅保留 QASPER、MultiHop-RAG、ALCE、QMSum、HotpotQA；其他旧 benchmark 专属内容已从当前工作树删除，见 `DELETION_LOG.md`；剩余技术债见 `TRIAGE.md`。不得为通过旧测试重新引入退役套件或叠加兼容。
- 主要数据来源包括项目文档、真实用户问题、典型业务场景和公开评测集。

## 工作方式

- 先记录观察和实验结果，再决定是否固化为方案。
- 模型辅助生成的问题、答案和证据只能作为候选，正式基准需要人工核验。
- 评测结果应区分实验事实、人工判断和暂定假设。
- 不要因为月度计划提到某个方向，就假定其指标、阈值、数据格式或覆盖范围已经确定。
- 涉及私有语料、用户问题、密钥或服务地址时，结果文件只保留必要的脱敏信息。

## 进入任务前

先阅读 `CURRENT_STATE.md` 了解当前目标、不变量与执行范围，再阅读 `docs/evaluation-context.md` 和 `docs/evaluation-status.md` 查看背景与证据；根据任务需要查看 `RUNBOOK.md`、`ARCHITECTURE_CURRENT.md`、`TRIAGE.md`、代码、测试和调研材料。
