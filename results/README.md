# 实验结果

仅维护 **QASPER、MultiHop-RAG、ALCE、QMSum、HotpotQA** 的评测结果。当前证据见[评测状态](../docs/evaluation-status.md)，公开答卷重评分与方法入口见[外部结果](../docs/notebook-external-results-2026-09-28.md)。服务器进度以实际工件为准。

正式结果包括冻结输入身份、原始逐题回答、submission、官方 scores、实际分母、缺失／错误／pending、代码与模型身份。Notebook run 内的诊断分、Agent／组件分与官方 benchmark 分分别报告，不合成总分。Dashboard 与比较报告是派生展示，不能替代原始工件。

完整运行目录、数据库、模型日志、源码快照和私有数据通常留在执行机器的 `var/` 或指定隔离目录，不随 Git 上传。clone 不会恢复历史实验；回传与脱敏要求见 [RUNBOOK](../RUNBOOK.md)。

2026-09-29 删除了旧协议混合检索汇总与退役套件状态页，详见 [DELETION_LOG](../DELETION_LOG.md)。这些旧结果没有被拆成看似现行的五套标准成绩；未操作 Git 外或服务器上的原始工件。
