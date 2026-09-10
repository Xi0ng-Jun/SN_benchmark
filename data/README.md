# 数据入口

## 冻结的公开样本

`public-benchmark-v1/` 随 Git 保存 SQuAD/DROP 的问题、文档和 manifest。每集 100 题、200 段原文；原始文档不改写，问题与多参考答案保留。抽样和 JSON 字段属于产品评测适配，不代表官方排行榜任务。

来源、版本、下载 SHA-256、许可记录及 Hugging Face revision 在 `configs/public-benchmark-v1.json` 和 manifest 中。两集的许可记录均为 CC BY-SA 4.0，来源为 SQuAD 官方 dev 1.1 和 Allen Institute DROP dev；派生样本遵循对应上游许可。本仓库未为项目源码指定新的开源许可证。

- SQuAD：[官方数据页](https://rajpurkar.github.io/SQuAD-explorer/)。
- DROP：[官方数据页](https://allenai.org/data/drop)。
- [CC BY-SA 4.0 许可](https://creativecommons.org/licenses/by-sa/4.0/)。

`raw/` 下载归档留在本地。需要重建时使用新目录，避免改写已有冻结输入：

```bash
.venv/bin/python scripts/prepare_public_benchmark.py --output var/rebuilt-public-benchmark-v1
```

脚本下载配置中的官方归档并验证哈希，再生成相同样本；不需要产品或 judge 模型。

## 历史大型数据

原机器的大型数据位于 `/home/wabiwabi/rag_benchmark/.ragbench/datasets`。本地 `data/crud-rag`、`data/multihop-rag`、`data/scifact` 是指向该目录的符号链接，不随 Git 上传。克隆后使用自己准备的数据路径运行 loader，不能假定该机器路径存在。

旧 loader 所需文件为 `questions.jsonl`、`annotations.jsonl` 和 `documents.jsonl`；公开基准 v1 使用自己的冻结协议。
