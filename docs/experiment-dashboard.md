# 实验结果探索器

本页介绍离线 Dashboard v2。它读取已经保存的公开 Benchmark 运行，提供标签筛选、图表、逐项评分依据和多组比较。指标的实际计算方法见[Benchmark × 轨道 × Metrics 四列表](benchmark-metrics-reference.md)。

## 生成并打开

在评测仓库中执行，使用 Python 3.11+，不需要安装 DeepEval 或启动 SN：

```bash
python scripts/build_experiment_dashboard.py \
  --runs-root /absolute/path/to/experiment-runs \
  --output /absolute/path/to/reports/explorer-v2-001
```

也可显式选择运行目录，避免把不相关实验放进同一报告：

```bash
python scripts/build_experiment_dashboard.py \
  /absolute/path/to/boolq-chunk /absolute/path/to/boolq-reasoning \
  --output /absolute/path/to/reports/boolq-comparison-001
```

输出目录必须是新的目录，且不能与任一运行目录互相包含。命令沿用旧 Dashboard 入口；已有 HTML 不会自动升级，需要重新生成。

| 文件 | 用途 |
|---|---|
| `dashboard.html` | 双击在浏览器打开；图表、代码和数据全部内嵌，无 CDN 或网络请求 |
| `dashboard-data.json` | 页面数据：运行、评分条目、按题去重的原始观测、指标计算说明 |
| `summary.json` | 本次纳入运行的计划数、输出数、有效分数数等统计 |
| `audit.json` | 运行警告和解释边界 |

自动发现仅纳入 `public-starter-run-v1` 运行及有状态记录的未完成初始化，不把 `input/manifest.json` 当成运行。历史其他格式的实验不自动转换。没有任何运行时会明确报错；初始化阶段还没有题单时，页面可展示运行警告，但不会编造题目。

## 从标签到分析

一个条目是 **某次运行 × 某道题 × 某项指标**。同一回答的正确性、Faithfulness 和引用检查是不同评分条目，共享一份回答观测。问答次数按 `run × case` 去重，不能用评分条目数当题数；不同运行中的同题仍是不同问答尝试。

侧栏支持 Benchmark、Task、Track、Mode、Scorer、评分状态、输出状态、行为、分区、Run 和配置身份多选。同一标签下勾选多个值表示“或”；不同标签之间表示“且”。搜索覆盖题目、回答和已保存的原因。筛选后图表、计数和条目列表同步变化。

左侧标签也会动态联动：每组的选项和条目数量根据**其他组的已选条件及搜索词**计算，暂不施加本组自身的条件，以便继续追加同组多选。例如只选 BoolQ 后，Scorer 中隐藏仅属于 SQuAD 的指标；Benchmark 组仍可追加 SQuAD。数字表示该标签在其他条件下对应的评分条目数，不是累计勾选后额外增加的题数。

没有匹配条目的未选标签隐藏；已经勾选但变成 0 条的标签仍保留，显示 0 并允许取消，不擅自清除用户条件。没有任何选项的标签组隐藏。取消标签、清除条件、搜索和恢复比较组都会同步更新侧栏；已有展开状态、列表滚动与仍可见控件的焦点保留。

例如选择 `BoolQ + R + product.boolq.explicit_conclusion.v1`，可检查 SN 的 Yes/No 结论评分；进一步选择 `unparsed`，可逐题查看哪些回答没有满足结论提取规则。不要把这个失败子集的均分当成完整实验成绩。

| 展示 | 回答的问题 | 分母与限制 |
|---|---|---|
| 概览卡片 | 当前选择包含多少计划问答和评分 | 当前筛选范围；不代表未纳入运行的全题单 |
| 状态环图 | 有多少有效、错误、不适用、未解析或缺失评分 | 所有选中评分条目；缺失不变成零分 |
| 分数直方图 | 一个同口径指标的分数如何分布 | 仅有效分数；混合 Benchmark/Track/Scorer/配置时提示先筛选 |
| 分面均值与覆盖 | 每个口径的平均分及评分完成情况 | 均值仅含有效分数；有效数/计划数另行展示 |
| 比较组图表 | 多个标签组合各自的结果分布有何差异 | 各组按口径展示描述性结果，不能把异质口径混算总分 |
| 模式比较卡片 | 同一 benchmark/task/指标下 chunk 与 reasoning 的表现差多少 | 显示共同题、共同有效题、胜平负和缺失状态；差值只使用双方有效配对 |
| 分区差异表 | 差异集中在哪些资料分区 | 每个分区分别计算共同题、有效题、两组均值和差值，不把小分区当作同等权重的整体结果 |
| 逐题配对表 | 哪些题从一种模式变好或变差 | 显示两组分数、状态和差值；可直接打开两侧的完整详情 |

没有混合所有指标的“总质量分”，也没有未经人审校准的红绿质量门禁。

## 多组比较

先筛选一组、命名并保存，再调整筛选保存第二组、第三组。保存的组保留自己的筛选条件，不随当前视图变化；第一组作为参照，后续组逐一与它比较。比较组保存在当前页面会话，刷新页面后需重新选择。

推荐操作：

1. 选择 `BoolQ / R / 主指标 / chunk`，保持评分状态不限，保存为“BoolQ chunk”。
2. 把 Mode 改为 `reasoning`，保存为“BoolQ reasoning”。
3. 查看各组的配置、输出/评分状态、有效评分均值和覆盖，再看按分区及共同有效题的配对均值、差值以及提升/持平/下降数量。
4. 若配对被拒绝，按界面原因检查配置身份、重复运行或题目交集；不要为了得到图而删除校验。

配对比较必须满足：单一 Benchmark、Track、Scorer、配置身份；每组一种模式且两组模式不同；两组运行不重叠；`pairing_id + case_id + scorer` 唯一且有交集。配置身份保留数据来源、模型、代码、设置、服务与审计身份；非 selection 运行保留完整候选库身份，selection 运行保留共同分区计划哈希与语料协议。这允许同一冻结分区计划下的多个 notebook 汇总，不合并不同候选库或不同分区计划。具体题对还要求完整 `pairing_id` 相同，不把不同资料的同名题强行配对。

当前严格配对主要支持 **SN chunk ↔ reasoning**。页面会自动标出“模式对比”，并展开两组配置摘要（运行、模式、配置族）、评分状态和输出状态。按分区表能看出差异是否集中在某个资料分区；逐题表能打开参照组或比较组的原始回答、上下文、引用和 scorer details。Native 与 Product、不同模型版本或不同资料配置可以分面查看各自成绩，但不能直接获得配对提升结论。Task/状态筛选导致两组题目不同时，配对仅计算共同有效题，显示仅在单侧的题、单侧缺分和双方缺分；不做显著性检验或因果解释。

比较卡片回答的是一个明确的实验问题，而不是生成一张脱离上下文的柱状图：

```text
问题：同一 benchmark、task、资料分区、题目、配置下，reasoning 是否比 chunk 更好？
参照：chunk      计划 40 · 有效 37 · 均分 0.73 · 配置 cfg-chunk
比较：reasoning  计划 40 · 有效 35 · 均分 0.69 · 配置 cfg-reasoning
结果：共同计划 40 · 共同有效 34 · 差值 -0.04 · 胜 / 平 / 负 12 / 8 / 14
```

如果两组的 benchmark、task、scorer、配置族或题目身份不满足比较条件，页面显示不可比原因并停止计算差值。两组的状态构成仍可单独查看，例如 reasoning 是否有更多 clarification、error 或 no_answer。状态差异是解释线索，不会被换算成 0 分塞进质量均值。

## 条目详情与分数依据

点击条目可查看：

- 原始 case / raw_row、公开参考答案与冻结身份。
- Native 保存的模板请求，或 SN 实际 question；完整 `prediction`、产品 response 和状态。
- 已捕获的最终合成上下文、source/document 映射、引用对象和确定性回查结果。
- 该指标的输入说明、计算公式或评分步骤，以及实际 plan/result/details/reason。
- 已保存的模型调用：只有 `role=judge` 且 `request_id=本条目 result_id` 的事件显示为本次评分依据；本题其他事件保留在原始观测中。
- 对应运行 manifest、配置身份、告警和完整原始 JSON。

这是对已有记录的解释，不会重算分数。没有保存的 prompt、子判断或理由会显示未保存，不补造 judge 思考。计算说明对应当前代码；历史实验的最终依据仍是冻结代码/SDK 身份和实际记录。SN 内部并非所有模型调用都在 `model-events.jsonl`，已有 captures/trace 也不等于完整 Agent 轨迹。

例如引用存在率 0.75，应在详情里找到 `citation_valid_count=3` 与 `citation_count=4`，说明 4 个返回对象中 3 个通过来源与对象回查。这不证明被引用内容支持正文断言。GEval 则查看参考、实际回答、rubric 说明、保存的 judge 返回与理由，而不是将其解释为字符串匹配。

## 运行中实验与数据边界

生成器只读运行文件、写入新报告目录，不触发模型、下载数据、修改 SN 或恢复 timer。对正在运行的实验，页面是生成时快照；文件尚未写完的 JSONL 尾行按现有 loader 规则忽略并告警，身份/哈希或 ledger 校验失败则停止，不跳过出问题的运行。之后使用新目录重新生成即可看到进度变化。

报告分母仅覆盖提供的 run。还没有 run 目录的分区不会被猜测为已计划；完整题单的选题、待审核和未运行分区覆盖，继续使用 `report_public_selection.py`。

报告包含用于审查的题目、回答和上下文。结构化密钥/服务地址字段脱敏，内容以安全文本呈现；这不等于对自由文本的全面隐私审查。分享前应检查是否包含私有语料。HTML 和 JSON 输出默认仅当前用户可读。

## 给服务器 Agent 的交接 prompt

```text
请仅升级并生成 Silicon Notebook Benchmark 的离线 Dashboard，不重新执行实验或评分。

1. 读取仓库 AGENTS.md、docs/experiment-dashboard.md、docs/benchmark-metrics-reference.md。
2. 获取远程 docs/public-benchmark-agent-expansion 分支的最新报告代码。如果现有实验仍在运行，不在它使用的 checkout 中切分支、pull 或改代码；用独立 checkout/worktree 运行报告脚本，现有 run 目录通过绝对路径只读传入。
3. 找到本次实验的真实 run 目录；优先显式列出要报告的目录，或确认 --runs-root 只覆盖本次实验。保留所有缺失、错误、不适用和未解析记录。
4. 使用 Python 3.11+ 执行 scripts/build_experiment_dashboard.py，写入运行目录之外的新报告目录。该功能无需启动 SN、安装 DeepEval 或调用模型。
5. 检查输出 dashboard.html、dashboard-data.json、summary.json、audit.json。告知实际纳入的运行数、计划问答数、计划评分数、有效评分数和警告。若失败，报告具体校验原因，不更改原始实验文件来绕过它。
6. 用浏览器验证：多标签组合筛选；点击一题能看到真实问题、回答、引用、评分依据；保存相同 Benchmark/Track/Scorer/配置下的 chunk 与 reasoning 两组，查看比较图及共同有效题数量。身份不匹配时应显示拒绝配对原因。
7. 给我新 HTML 的绝对路径和获取方式。不要把演示数据当实验结果，不公开上传含题目/上下文的报告。不要改变在线 SN、运行中的实验、baseline 或 weekly timer。
```
