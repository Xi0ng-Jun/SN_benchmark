# 公开评测状态快照

观察时间：2026-09-10 10:23（Asia/Shanghai）。这是执行状态摘要，不是完整质量报告。

## 范围与实际进度

SQuAD 1.1 与 DROP 各固定 100 题、200 段候选原文；每集 20 题 debug、80 题 regression。原文进入隔离 notebook，问题进入原生 Ask，官方答案仅进入评分。

| 数据集 / 模式 | 成功答案 | 产品失败 | 已记录指标 / 计划指标 | 有效评分 | 跳过 |
|---|---:|---:|---:|---:|---:|
| SQuAD / chunk | 100 | 0 | 360 / 360 | 340 | 20 |
| SQuAD / reasoning | 90 | 10 | 169 / 360 | 133 | 36 |
| DROP / chunk | 100 | 0 | 123 / 360 | 103 | 20 |
| DROP / reasoning | 93 | 7 | 6 / 360 | 5 | 1 |
| 合计 | 383 | 17 | 658 / 1440 | 581 | 77 |

共 400 次主 Ask；已落盘的主评分无 judge 错误，尚缺 782 项记录。每份输出计划三项主指标，debug 输出再加三项诊断指标，因此记录数不等于问题数。Contextual Precision 因无可比排序明确跳过；产品未生成答案时对应指标也跳过。

评分进程最后写入于 2026-09-10 10:01:07；10:23 检查时进程已不存在，未找到正常完成记录，退出原因未证实。用户随后要求暂停具体任务。原运行目录中 `report.md` 为 9 月 9 日中间报告，根目录聚合文件不保证包含最新 cell 评分；最终 `audit.json` 和 `operational-summary.json` 尚未生成。judge 重复评分、产品重复运行均未执行。

## 已完成的自动 smoke

- 运行：`20260909T122132Z-smoke-7f0fd2da`。
- 40 个计划输出，38 个成功答案、2 个 reasoning 产品错误。
- 120 项评分记录：114 有效、6 跳过、0 judge 错误。
- prepare、ask、judge、report、audit 全部完成；完整性审计通过。
- 启动时尚无完整 baseline，未生成对完整 baseline 的差异报告，不能声称整套比较闭环已验收。
- 并发触发测试 `20260909T123048Z-smoke-606884a3` 被锁拒绝，状态明确为 failed，未调用产品，不计入评测样本。
- 2026-09-10 已停用每周 `public-benchmark.timer`，模板和运行记录保留。

## 解释与限制

17 个 baseline 失败的只读诊断均复现为原生歧义澄清检查，提示要求明确对象名称或背景，诊断过程未调用模型。原始异常类型为 `ValueError`。这与回答合成后的“无法保证完整枚举”提示不是同一种行为，不能把全部失败归因于完整性请求。历史异常缺少详细栈，因此诊断重现不能冒充当时的异常栈。

正确性使用 DeepEval GEval 定制规则；Faithfulness 和 Answer Relevancy 为主指标，Contextual 指标为诊断，证据/引用另做确定性检查。生成和 judge 共用配置中的模型，人工校准仍待完成，语义分数只作观察。引用对象存在不等于声明被引用支持；embedding token 与货币费用未知。

冻结输入可独立重建；准备阶段的数据身份修正、六份持久化空格校验修正和 judge 身份扩展均留有本地原始记录，未重放已成功 Ask。初始化本 Git 仓库不改变历史运行身份。

## 本地证据位置

`var/public-benchmark/20260909-baseline-v1/` 保存 `inputs/`、四个 `cells/`、`failure-diagnostics.json`、`preask-provenance/`、`persistence-correction/`、`judge-identity-finalization/`、`review.md` 与待审 `human-labels.jsonl`。

smoke 同级目录保存 `automation.json`、`audit.json`、`operational-summary.json` 与原始输出。GitHub 只发布本摘要，完整工件保留在执行机器。
