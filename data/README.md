# 数据入口

仅支持 **QASPER、MultiHop-RAG、ALCE、QMSum、HotpotQA**。官方来源见[资料索引](../docs/notebook-benchmark-official-resources.md)，输入、切分与评分契约见[标准符合性](../docs/notebook-benchmark-standards-and-conformance.md)。

正式输入使用冻结 bundle：保存原始字节、来源、公共资料、题目／gold、分区、适配版本和哈希。`scripts/prepare_notebook_benchmarks.py` 从已取得的本地原始数据准备新 bundle；它不下载数据。新正式输入显式使用 notebook-data-v3，生成请求使用 notebook-request-v3。

大体量公开数据、scorer 及上传包不随 Git 交付；服务器目录与校验步骤见 [RUNBOOK](../RUNBOOK.md)。仓内小型测试 fixture 位于 `tests/fixtures/`，不代表正式实验数据。

2026-09-29 已删除退役套件的受跟踪样本及旧重建入口，见 [DELETION_LOG](../DELETION_LOG.md)。Git 外本地数据／符号链接与服务器资产未操作；不能从源码清理推断磁盘数据已被删除。
