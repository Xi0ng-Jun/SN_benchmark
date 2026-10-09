# 一次实验如何运行

本页描述一次服务器实验。
本机只做代码和离线检查。

## 1. Freeze

先固定以下对象：

- evaluator commit
- Silicon Notebook commit
- frozen bundle
- scorer commit 和文件 hash
- 模型和 tokenizer
- prompt 和 request revision
- chunk、检索和生成预算
- timeout、retry 和随机性规则

实验清单写入 `experiment-manifest.json`。
不要从 shell history 恢复身份。

## 2. Prepare

`prepare_notebook_benchmarks.py` 创建或安装 frozen bundle。
bundle 包含：

- 原始数据
- 公开 documents
- cases
- partitions
- 评分侧 gold
- source identity

共享模式还会安装不可变 bundle、index 和 evaluator 对象。
共享对象可以复用。
数据库、storage、配置和日志仍按 run 隔离。

## 3. Partition

runner 一次处理一个 partition。
partition 是运行隔离和资料边界。
它不改变 benchmark 的题目分母。

smoke 题单必须属于当前 partition。
完整资料不能因为 smoke 而被删除。

full 运行需要枚举全部 partitions。

## 4. Run

典型入口是：

```bash
python scripts/run_notebook_benchmarks.py \
  --bundle <bundle> \
  --partition-id <partition> \
  --mode chunk \
  --request-revision notebook-request-v3 \
  --project-root <sn-checkout> \
  --model-config <model-config> \
  --run-dir <new-run-dir>
```

每个 `partition × mode × attempt` 使用新 run 目录。
当前入口不支持原目录隐式续跑。

运行中保存：

- manifest
- public request
- planned cases
- raw outputs
- answer status
- retrieval or citation observations
- runtime events
- scores or scoring errors

模型完成后再关联评分侧信息。

## 5. Export

所有有效 run 完成后，使用 `export-sn` 创建正式 submission。
export 必须收到真实 run 目录列表。
它不会自动递归查找父目录。

submission 是 compact scoring projection。
它不是完整 run。
完整 outputs 和 runtime 必须继续保留。

## 6. Score

通用评分入口是：

```bash
python scripts/benchmark_protocol.py score \
  --bundle <bundle> \
  --submission <submission.json> \
  --sources <official-scorers> \
  --output <new-score-dir>
```

QMSum 还需要显式传 `--rouge-home`。
ALCE 默认只运行轻量文本指标。
ALCE 模型指标必须显式选择 `--alce-answer-only` 或 `--alce-full`。

## 7. Audit

评分后检查：

- case IDs
- scope
- 实际分母
- status counts
- scorer identity
- pending metrics
- source and output hashes
- model and budget identity

只有审计通过，结果才进入比较报告。

## 8. 服务器回传

results 包包含 compact submission 和官方成绩。
review 包还包含完整 outputs 和必要映射。
runtime、密钥配置和原始服务日志不进入公开结果包。

## 继续阅读

- [外部 campaign 手册](../notebook-external-campaign-runbook.md)
- [共享结果存储与导出](../result-storage-and-export.md)
- [如何解释分数](05-results-and-paper.md)
