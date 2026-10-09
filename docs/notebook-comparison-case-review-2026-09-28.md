# 外部比较的案例复核与指标解释

核实日期：2026-09-28。本文记录已保存外部答卷的本地分析及新增复核入口；不是 SN 实验结论，没有运行新生成模型或服务器任务。返回[外部结果入口](notebook-external-results-2026-09-28.md)。

## 1. 为什么分数之后还要看答案

固定数据、官方 scorer 和逐题配对，是正确比较的必要条件。它们保证我们用同一规则计算，但不能保证指标覆盖所有语义质量，也不能消除不同模型、提示、输入和预算的差异。

本次用已取得的 Multi-Meta-RAG 两份完整答卷，以及 ALCE ASQA 四配置的 100 题公开答卷，完成了可重复的案例抽样。结果证明：总分相近可能掩盖很大的题型差异；官方字符串匹配可能给残缺答案分数，也可能不给同义表达分数。应保留官方分数并解释适用范围，不私自修 scorer 以得到“更合理”的主表。

证据分层：下方计数、答案原文和函数命中来自本地工件；对语义的解释属于助手辅助分析，未由独立人工复核。`annotations.jsonl` 仍为空白待复核状态，没有把助手判断登记成人工标签。

## 2. 新增的离线复核入口

[`scripts/review_benchmark_comparison.py`](../scripts/review_benchmark_comparison.py) 读取现有 `benchmark-comparison-v1` 报告，用原 bundle、submission、scores 重建比较，确认源文件哈希、题目范围、方法身份、分母和配对结果未变化，然后抽取案例。它不运行生成模型、不重新调用官方 scorer、不修改既有成绩。

选择规则固定为：

- 显式指定一个或多个已有逐题指标；只有 batch 分数、pending 或不共享的指标不能用于逐题抽样。
- 对每个方法对，按 `task × metric × 分差方向` 分层；四类为左侧分高、右侧分高、双方零分、双方同一非零分。
- 每个非空层默认最多 3 题，以 `SHA256(seed, metric, case_id)` 排序；不优先挑分差最大或“最漂亮”的案例。输出该层全部题数与抽样数。
- 同一题可进入多个方法对的复核项，题目文件只保存一份。正式全量成绩和分母保持不变。

这是**案例解释样本，不是总体质量的随机估计**。各层不等权，不能把复核样本的比例当全量错误率，也不能用它计算新的方法排名。分层依赖已经观测到的分数，不能将这些例子再用于调参并声称它们仍是独立测试集。

工具同时从原比较的**全部逐题结果**生成 `task_comparisons`，每个方法对、任务和所选指标各一行，记录该任务总题数、指标合格分母、两侧算术均值、右减左的均值差和四种分差方向的题数。这些统计独立于抽样配额和 seed，先于案例展示。条件指标只使用其适用题目；若某任务没有适用题目，均值保存为 `null`，不补零。这里的“全部”指声明的 comparison scope，例如 ALCE human 比较仍只有冻结的 100 题，不代表完整 ASQA。

这些是逐题值的算术汇总，不重新调用 scorer，也不替代 QMSum Perl ROUGE 等原 batch 估计。分数高低的题数不等于语义胜负或正确率，不额外声称任务级显著性。此前一次性脚本中的 MultiHop 题型分析已由该通用入口自动重现。

输出内容：

| 文件 | 内容与用途 |
| --- | --- |
| `review.json` | 原比较身份、全范围题型统计、选择规则、各层规模、复核项 ID、题目文件哈希 |
| `review.md` | 全范围题型统计表，以及抽样问题、参考答案、两侧最早保存的回答观测、提交答案、官方 bridge 输入及分数 |
| `cases/*.json` | 完整 gold 分组、方法状态、逐题指标、观测到的引用/证据/检索、原答卷和成绩路径 |
| `annotations.jsonl` | 独立人工记录模板：reviewer、judgment、explanation、evidence 初始均为空，status 为 unreviewed |

`record.raw_generation`、`record.answer`、提交 `prediction` 分别标明来源。比如 LAB 将 `unknown` 解析为 `unanswerable`，或 SN 将拒答归为空提交时，已保存的原表达仍可复查。没有保存的更早原文不推测恢复。官方 bridge 答案在后续作者 CLI 预处理之前，例如 ALCE 的首行处理还在 scorer 内，不能把 bridge 文本误称为全部预处理后的最终字符串。

复核包含公开 benchmark 的 gold，仅用于评测分析，不可进入生成请求。缺引用映射的旧 ALCE human 样本继续保持 unavailable；复核包不会凭 citation 编号补造所见文档。

## 3. 已实际生成的复核包

| 比较 | 原比较范围 | 方法对 | 复核项 | 不同题目 |
| --- | ---: | ---: | ---: | ---: |
| Multi-Meta-RAG GPT-4 / PaLM | 2,556 | 1 | 45 | 45 |
| ALCE ASQA vanilla / interactive / rerank / vicuna | 100 | 6 | 72 | 23 |

工件：

- `var/external-comparison/multimeta-full-e77e4638-20260928/case-review-task-summary/`
- `var/external-comparison/alce-human-246c476-20260928/asqa/case-review-task-summary/`
- `var/external-comparison/review-analysis-20260928/observations.json`：全量题型统计和固定 MultiHop scorer 的示例 token 交集。
- `var/external-comparison/review-analysis-20260928/alce-alias-traces.json`：固定 ALCE `exact_presence` 对四道具体题的逐 alias-group 命中。

上述 `var/` 被 Git 忽略，需随完整工件独立搬运。报告记录了本机绝对输入路径；迁移后应从新路径的原答卷/成绩重新生成 comparison，再生成 review，不手改哈希或假装旧路径仍可读取。早期 `case-review/` 和 `case-review-final/` 保留历史；当前入口是新增题型汇总的 `case-review-task-summary/`。本次重新生成核实了原 comparison 哈希、抽样 assignment 和案例 payload 均未改变，MultiHop 四个题型的均值及方向计数与此前独立保存的全量统计一致。

## 4. MultiHop：总分几乎相同，题型差异很大

以下是完整 2,556 题按原 task 划分的官方 weak-match 成绩，不是 45 题复核样本的估计。分数仅描述固定作者公开答卷。

| 题型 | 分母 | GPT-4 命中 / 比例 | PaLM 命中 / 比例 | PaLM − GPT-4（百分点） |
| --- | ---: | ---: | ---: | ---: |
| comparison_query | 856 | 327 / 38.201% | 462 / 53.972% | +15.771 |
| inference_query | 816 | 776 / 95.098% | 751 / 92.034% | −3.064 |
| null_query | 301 | 297 / 98.671% | 75 / 24.917% | −73.754 |
| temporal_query | 583 | 149 / 25.557% | 265 / 45.455% | +19.897 |
| 全部 | 2,556 | 1,549 / 60.603% | 1,553 / 60.759% | +0.156 |

两方法在 **758 题**上的二元官方得分不同，其中 GPT-4 单独命中 377 题、PaLM 单独命中 381 题；总计仅差 4 题，是不同题型差异抵消的结果。不能由这 4 题差异概括所有能力，更不能概括两类基础模型的一般优劣。此处原 group_id 只有一个 corpus 组，现有聚类 bootstrap 不给有效区间，不宣称统计显著。

固定 scorer 的原始函数已经对以下抽样案例再次执行，得到的结果与保存成绩相同：

| case_id | 可观察到的输出 | 官方结果与解释 |
| --- | --- | --- |
| `multihop_rag:980` | gold 为 `Taylor Swift`；PaLM 输出摘录标记和 `title: Taylor` | 仍得 1，因为 lower/split 后出现共同 token `taylor`。该输出并未完整给出参考实体；这证明 weak-match=1 不能自动称为完整语义正确 |
| `multihop_rag:1420` | gold 为 `Yes`；GPT-4 为 `Yes`；PaLM 以 `Yes. The article...` 开头 | 前者 1、后者 0。官方按空白分词，`yes.` 与 `yes` 不同；不能将这个分差直接归因于推理能力 |
| `multihop_rag:1775` | gold 为 `Sam Bankman-Fried`；GPT-4 输出 `SBF`；PaLM 输出完整姓名 | 前者 0、后者 1；官方不做缩写扩展。是否接受别名是额外语义判断，不能悄悄加入官方 scorer |

在 null 层的抽样中也确实出现两方法拒答行为不同，例如 `multihop_rag:2450` 的 GPT-4 输出信息不足、PaLM 输出具体地点。该层总体分差来自完整官方评分，但“幻觉率”仍不能直接等同于 weak-match 的补数；需要独立语义与来源复核。

## 5. ALCE：str_em 衡量 alias 覆盖，不等于完整答案正确性

本节只解释冻结样本及其保存参考，不回答这些问题在 2026 年的现实答案。问题中的 “currently”“last year” 等相对日期仍按该 benchmark 的来源时期理解，不替换成今天的知识。

原 `exact_presence` 函数的逐组检查显示：

| case_id | 观测与固定函数结果 | 比较时如何解释 |
| --- | --- | --- |
| `alce:84` | vanilla=1，interactive=2/3。后者说 `overall mediocrity of the team`，参考组要求 `overall mediocrity of the Cardinals`；其余两组命中 | 改写导致 alias 不匹配，分差不能直接等同于缺少一个语义原因 |
| `alce:63` | vanilla=0.2，interactive=0。参考日期为 `7 December 1941`，interactive 用 `December 7, 1941`，同时把 `attack` 改成 `bombing` | str_em 不统一日期语序、不接受该同义替换；两输出均未覆盖其它历史事件/立法分组，不能反向宣布语义满分 |
| `alce:72` | interactive 未回答参考中的首位球员，但得 0.25。其第四组 alias `SEC` 在规范化输出的 `second` 中形成 substring 命中 | 这是官方 substring 规则造成的命中，不代表正确覆盖该组含义；保留原分数并单列解释 |
| `alce:11` | vanilla 只回答年份而得 0；interactive 给出参考中的具体日期而得 0.5 | 此处也有答案具体程度差异，不应把所有零分都归咎于字符串规则。仍未命中另一个赛事名称分组 |

旧 human 样本没有完整生成时文档列表，因此本次不能判定引用支持正确性。句子里出现 `[1]` 不构成支持证据。新增 VANILLA 运行入口将保存所见文档，未来才可做完整官方 AutoAIS 比较。

## 6. 后续正式 SN 报告的处理

1. 主表保留固定官方指标及实际分母，完整方法条件随报告给出；不为了样例“修好看”而改变官方规则。
2. 同时报告题型/任务的全量分布和成绩，避免总分抵消掩盖拒答、时序或多答案覆盖差异。QMSum 的逐题均值与原 Perl batch 估计仍分开标注。
3. 在相同预声明规则下为 SN 与每个外部方法生成复核包，保留双方低分和持平例子；只讨论确有原文支持的原因。
4. 人工评价与助手辅助观察单独保存，说明评审者、口径和局限；不冒充官方指标或独立人工校准。
5. 若要额外评估语义正确性，先定义独立诊断协议再执行，不能覆盖官方分数。当前没有新增 judge 分数或跨 benchmark 总分。

## 7. 使用命令与验证

```bash
.venv/bin/python scripts/review_benchmark_comparison.py \
  --bundle var/benchmark-protocol-validation/multihop-acceptance-20260924/run-v2/bundle-v3 \
  --comparison var/external-comparison/multimeta-full-e77e4638-20260928/comparison/report.json \
  --metric upstream_weak_match_accuracy \
  --per-stratum 3 --seed 0 --output /fresh/path/multihop-review
```

ALCE 改为 `asqa-gtr/bundle`、对应 ASQA comparison 和 `--metric str_em`。对 QASPER/HotpotQA 可选已经完整评分的答案或证据逐题指标；QMSum 可选已有逐题 ROUGE。整个运行只处理本地已保存工件。

2026-09-28 历史验证快照：初始入口 4 项测试，新增全范围题型统计 2 项测试，覆盖抽样与全量分离、逐题均值与 batch 估计区分、条件分母及零适用题的空值。独立只读审查未发现重要问题，review/comparison 合计 **78 passed**；全量回归 **766 passed、2 skipped**。这些阶段计数不代表当前测试集合，后续最新已记录验证见[评测状态](evaluation-status.md)。初始抽样阶段曾验证四种方向、任务分层、抽样配额、行序不变性、seed 变化和条件指标资格。该快照尚未完成真实 SN 对外部方法的正式比较、新增方法的服务器模型执行与独立人工复核。
