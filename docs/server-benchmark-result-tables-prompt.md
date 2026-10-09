# 服务器 Agent：五套 Benchmark 结果表填报指令

日期：2026-10-09。本文件供用户转发给已有服务器 agent。
本机无法直接发送到服务器。
本文件不表示服务器已执行。

复制以下内容：

```text
请仅根据已经跑出的结果，填写 docs/benchmark-result-tables.md 中的整组结果表。
范围覆盖 QASPER、MultiHop-RAG、ALCE 三任务、QMSum、HotpotQA，不是只填 QMSum。
你本次不负责决定实验，不启动、安排或续跑任何实验。
不调用SN/LLM/judge，不执行新的scorer评分（包括轻量重评分），不下载或重建外部来源。
已有实验若正在运行，不中断、不更改；只读取已完成且稳定保存的工件。

一、先核对任务与版本
阅读 AGENTS.md、CURRENT_STATE.md、docs/benchmark-result-tables.md、docs/notebook-benchmark-standards-and-conformance.md 和 docs/result-storage-and-export.md。
核实本 worktree 的分支、HEAD 和未提交改动。需要取得新文档时，用隔离的文档副本或独立checkout阅读，不改变进行中实验使用的源码。不要reset、覆盖改动或中断现有实验。
保存实际 evaluator/SN SHA、模型配置身份、bundle/index pin、scorer 和依赖身份。
以真实工件确定各 benchmark 的完成状态，不照抄本机历史进度。
用户转述 QMSum chunk 已完成：优先检查它的 35 场会议、281 个 case ID、状态、submission 和 Perl 官方评分。

二、只把现有结果对应到表格
模板含当前主方法、外部公开答卷和历史候选。它是呈现目录，不是执行清单。
按实际方法、任务、mode、case IDs和scorer，将现有工件对应到具体表和row ID。
候选验收或smoke不能填到full行。smoke另存并标实际N。
未跑、未评分、工件缺失或方法条件未知时保留相应状态。只报告事实和原因，不建议或自行选择下一项实验。

三、先复用已保存输出
枚举实际 run 和 attempt，固定本次使用的目录清单，避免把多个 attempt 或不同 mode 混成一个方法。
不按分数选择 attempt，不删除失败、missing 或低分题。
全量导出显式列出全部实际 run 目录；按已有共享存储规则校验 canonical bundle。
原run、runtime、outputs、快照、分数和日志保持不变。必要的只读导出、已有分数间的离线比较和报告写到新目录。
如果已有full SN包含ALCE human_eval固定100题且身份兼容，可以导出subset。优先读现有subset分数；对于已有完整逐题值、且官方聚合明确为题宏平均的STR-EM/STR-HIT，可仅汇总该固定100题的已存分数，并标“由已有逐题分数汇编”。不能把MAUVE、ROUGE批量估计等full批量指标拆成subset总分，不为填表启动subset评分。无法从现有分数恢复的指标记缺口。
多个有效attempt的既定使用规则不明时，分别列事实并标“主结果attempt未指定”；不按高分或最新时间自行决定主结果。

四、逐表核验并填值
表1：固定数据、题单、模型、提示、预算、源码与 scorer 身份。每个方法保存配套协议记录。
表2：QASPER Answer F1；Evidence F1 只有整批显式来源投影通过才填，否则 pending。
表3：MultiHop QA 分母2556，检索分母2255。SN chunk 使用真实排名快照。SN reasoning 检索保持 pending。Multi-Meta 两份QA共享同一公开排名，不算两次独立检索实验。
表4：ASQA948、QAMPARI1000、ELI51000分别填报。ASQA列 STR-EM/STR-HIT 和条件 QA-EM/QA-F1/QA-Hit。QAMPARI列 precision/recall/recall@5/F1/F1@5。ELI5 correctness 用 claims-NLI recall。
ALCE full 与 human_eval100分开。公共100题只用从固定 human_eval 来源导入的 case IDs，不随机抽题。
读取现有评分模式：默认轻量score提供STR-EM/STR-HIT；--alce-answer-only是非引用模型批评分，不能解释成轻量文本模式。两侧进入同一比较必须有相同scorer模式和依赖身份。本次不执行这些评分命令。
缺 shown-doc/citation mapping 的公开答卷不报引用分；不得补造映射或零分。ROUGE/MAUVE/AutoAIS 等模型依赖未实际完成时写 pending。
表5：QMSum 固定281题，使用固定 Perl ROUGE-1.5.5 与 HMNet regex 分句。主结果取官方 Average_F，不能换为 Python ROUGE 或逐题均值。Socratic公开顺序映射的限制须披露。HMNet gold-span 不作为普通端到端 baseline。
表6：Hotpot distractor validation答案四项；显式句位预测及整批映射通过后才报SF四项和Joint四项，共12项。不与fullwiki/test混报。
表7：优先读取现有comparison；只有已有完整submission/scores时才用现有比较器做不调用模型的离线比较。核对共同case IDs、scorer、eligible IDs与missing/error。记录被拒绝的原因，不绕过gate。展开每个pair/metric。Official Δ与mean per-case Δ分别列。原报告right-minus-left转换到表中A−B时核对符号与CI方向。cluster CI对应逐题差值均值；batch-only无逐题CI。MultiHop当前只有1个自然group，CI unavailable，不能改分组制造显著性。
表8：对账生成状态和证据/排名映射错误。记录延迟边界、观测覆盖和实际provider usage。没有观测的token/cost写unavailable，不填零。

五、公开外部来源
docs/notebook-external-results-2026-09-28.md 中的数字是历史记录，不代表服务器当前工件已存在。
检查实际来源文件、SHA256、导入映射、submission和scored输出。var/被Git忽略，pull代码不会自动取得这些工件。
来源缺失时只列缺口，不下载、不重建、不临时更换版本。
仅有文档历史值时标“文档引用，服务器工件未核验”，不能填成服务器新成绩。已核实的论文汇总值单独标published-reference及setting/分母差异。现有公开答卷重评分标recomputed-subset并另写full/subset。已完成受控生成标controlled-rerun。

六、交付与每阶段反馈
在campaign/reports下的新批次目录保存：
1. result-tables.md：按模板编号填报整组表；未完成行保留状态。
2. result-ledger.json：每行每指标的来源路径/hash、范围、状态、分母、scorer、配置与比较身份。
3. result-gaps.md：逐项写缺口、原因、缺少的工件或身份字段；不提出新的实验安排。
三份文件是人工汇编约定，不假设已有自动生成命令。数值只从实际官方工件读取。
对每个已有结果报告“目标表/行、完成范围、状态、官方评分、来源、剩余缺口”。生成结束但没有官方评分的行必须保留“未评分”。
回传已填表和必要账本；按docs/result-storage-and-export.md生成并核验私有results/review包，提供receipt与hash。没有生成完整包则明确写未打包，不宣称已验收。
所有失败和pending保持真实。禁止按低分重问、补零、删题、改gold、删除runtime、迁移或生产操作。
首次反馈请先给当前表格覆盖矩阵与真实缺口。缺口不触发任何新的实验或评分。
```

参照：[结果表模板](benchmark-result-tables.md)、[已有实验导出升级](server-upgrade-running-export-prompt.md)、[共享存储回传指令](server-result-storage-export-prompt.md)。
