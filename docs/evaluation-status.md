# 评测状态

核实基线：2026-09-28（北京时间）。本页是当前快照；详细历史、旧命令和交接上下文见[文档导航](README.md)和[历史归档](archive/README.md)。

## 当前代码与 Git

- 本地 `benchmark-deepeval/main` 已合并 Dashboard、原生评分恢复及文档整理，HEAD 为 `e022c60`。
- 当前实现位于 `.worktrees/benchmark-protocol-correctness`，分支 `feat/benchmark-protocol-correctness`，远程 HEAD 为 `3f194e6`；HotpotQA、外部方法比较、文件题单及文档改动已提交并推送，尚未合并到本地 `main`。
- Dashboard 合并提交为 `e4c5333`，原生评分恢复合并提交为 `03ca577`。
- 旧远程开发分支信息见归档，本轮未重新查询远程。
- 主线原有未提交文档已复制到实现worktree继续整理，主线原文件保留。产品checkout没有新增受管理改动；原有 `.deepeval/`、`results/` 未清理。

## 当前可用入口

### Dashboard

`scripts/build_experiment_dashboard.py` 读取已有 run，生成实验地图、单题流程/原生 span 回放和结果分析。它不会重新问答、重新评分或修改原始工件。使用和比较限制见[实验 Dashboard](experiment-dashboard.md)。

MultiHop 的 SN chunk 路径另保存 native retrieval snapshot（`sn-multihop-chunk-selected-passages-v1`），可按官方检索脚本回放；reasoning 路径没有等价单一排名，检索指标仍 pending。

### Notebook 场景

HotpotQA 已加入当前 suite：本地已完成固定 distractor validation 全量 7,405 题/73,700 段落适配、句子 source units、12 项指标与原版 CLI 的合成校准及原生 answer/sp 导入；SN 最终引用到句子级 supporting-fact 的投影已实现，完整公开语料经实际 parser/chunker 校准；服务器真实运行和回放尚未完成。

当前分支：prepare默认 `notebook-data-v3`，SN/Agent CLI默认无gold的 `notebook-request-v3`。新增 `run_benchmark_reference.py`（BM25/full-context/ALCE candidate-topk）、`benchmark_protocol.py`（答卷导出/官方评分/外部答卷导入）和 `compare_benchmark_submissions.py`（严格身份、分母、逐题配对、分组bootstrap及实际成本观测）。外部答卷导入要求显式 case map，保留原始预测和源文件哈希，支持完整 scope 或明确子集；published-reference、oracle/gold-input 条件不能进入 paired comparison。执行与比较限制见[当前实验计划](notebook-benchmark-experiment-plan.md)。旧Notebook诊断、QMSum单会议baseline和Dashboard入口保留。

五套 benchmark 的标准定义、官方依据、精确评分规则、代码对应及已验证/适配/待验证边界已整理为[标准与实现符合性](notebook-benchmark-standards-and-conformance.md)。Python API 的旧默认值仍保留；新调用需显式选择 v3，不能把 CLI 默认推广为所有接口默认。

外部方法登记见[机器可读登记表](../configs/notebook-external-method-registry-v1.json)：五个固定 scope、二十九个 method 条目，包含 SN 主方法、候选和已重评分公开答卷；仅 `results` 及对应工件代表已执行结果，新增 SN 条目仍为 pending。QMSum gold-input 排除依据见[来源审计](notebook-external-method-audit-2026-09-26.md)，最新完整答卷与复现命令见[外部结果入口](notebook-external-results-2026-09-28.md)。

KG2RAG 新增受控入口 `scripts/run_kg2rag.py`，全量 7,405 题无 gold 输入已准备。保留作者检索/图流程，QA 换用训练示例，KG 提取去掉验证示例，缓存按完整 context 区分，错误独立记录。它是明确标识的 adapted 方法，真实依赖和模型运行尚未验收。QASPER LAB 公共包不含已发布预测，但已新增 `run_lab_qasper.py`，复用作者 LongChat citation；完整 416 篇/1,451 题的无 gold 数据、三训练示例、原图渲染/配置/解析均已离线验证，真实模型仍待服务器执行；详见[外部结果](notebook-external-results-2026-09-28.md)。

ALCE VANILLA 新增 `scripts/run_alce_vanilla.py`，复用固定作者的两示例、前五候选提示和 HF 生成函数，ASQA 948、ELI5 1,000、QAMPARI 1,000 题的提示/所见文档与作者 main 逐题一致。运行时保存实际引用映射，输出可进入完整官方评分；本地没有执行真实权重或 AutoAIS。加载、随机数和 BOS 预算差异明确记录，见[外部结果 §7](notebook-external-results-2026-09-28.md#7-alce完整答案与引用的-vanilla-受控方法)。

比较后的案例复核新增 `scripts/review_benchmark_comparison.py`，从已核验报告按任务/逐题指标/分差方向抽样，输出答案、参考、观测证据和独立空白人工记录；另从全范围逐题结果汇总各任务均值、分母和分差方向计数，与抽样及原 batch 估计明确分开。真实 MultiHop/ALCE 最新 `case-review-task-summary/` 工件及指标解释见[案例复核文档](notebook-comparison-case-review-2026-09-28.md)。

### 原生 Agent 与组件补评

`scripts/run_notebook_agent.py` 使用 `sn-deepeval-native-v1`，默认评检索/合成组件；`--trajectory` 才评完整 Agent 轨迹。回答和组件先保存，judge 随后评分。`scripts/score_native_components.py` 可从已保存组件建立独立补评目录。入口、失败语义和超时边界见[原生 Agent](native-agent-evaluation.md)与[评分恢复](native-scoring-recovery.md)。

## 验证证据

2026-09-28 当前代码完整回归：**769 passed，2 skipped**。新增 SN MultiHop chunk native ranking capture、官方检索回放字段及 3 项定向测试；`sn_retrieval.py`、runner 接入、官方检索回放和既有 v3 runtime 共 47 项定向测试通过。题型自动汇总新增 2 项测试，review/comparison 合计 78 项通过；独立只读复核未发现实质问题。MultiHop/ALCE 已生成新汇总报告，原比较哈希、抽样选择及案例内容不变；MultiHop 2,556 题的四个题型统计与此前独立分析一致。两个跳过项仍需显式 native judge checkout 或本地 Perl ROUGE 环境；QMSum 的真实 Perl 重评分已另行完成。

此前案例复核初始阶段完整回归为 **764 passed，2 skipped**。新增比较案例复核入口的 4 项测试，MultiHop/ALCE 真实复核工件已生成；此前 760 项为 Hotpot 投影阶段。Hotpot SN 投影新增 7 项定向测试，涵盖 SQL 快照、跨文档句位、截短、无 gold、失败状态、完整 run/export/score 输入及伪造证据拒绝；此前 753 项为 ALCE VANILLA 阶段。ALCE VANILLA 新增 4 项定向测试；2,948 题与实际作者 main 的提示和所见文档逐题一致，实际 HF 生成分支仅以替身模型验收。真实生成、模型加载与引用模型评分未执行。此前 749 项是该入口加入前的回归。其中 KG2RAG 新增 5 项、LAB 新增 4 项定向测试；以下 734/729 等计数为此前阶段记录。HotpotQA 完整 7,405 题的合成扰动校准已实际执行原版 CLI，12 项指标最大绝对差 1.78e-14，分母均为 7,405；这些不是模型成绩。独立只读复核逐题对照 73,700 段落、原始标注和句子编号，并通过 10 项定向测试，未发现重要评分问题。

2026-09-28 后续已完成 Socratic SegEnc 的 281 条公开测试答案导入与真实 Perl ROUGE：R1=0.38955、R2=0.13960、RL=0.33942，分母均为 281，35 场会议，0 空答案/缺失。逐行映射来自作者公开预处理和保存顺序，原运行 input manifest 不可得；保留该限制，不声称完全恢复生成条件。ALCE 新增互斥的 `--alce-answer-only`，执行原始 CLI 的非引用指标，引用分保持 pending；未启动真实模型。针对两项新增功能的 31 项测试通过，独立只读审查未发现重要问题。此前 734 项全量回归为这些新增功能之前的基线。

2026-09-28 此前全量回归 `.venv/bin/python -m pytest -q`：**734 passed，2 skipped**，跳过原因仍为显式 native judge 产品 checkout 和本地 Perl ROUGE。新增原生 Multi-Meta/ALCE 导入验证，以及公开排名评分、`model_answer` 识别、ALCE 缺引用映射时阻止完整引用评分的回归。实际完成完整 MultiHop 导入/重评分、原官方 CLI 对照、八份 ALCE 样本导入，以及两份外部答卷比较报告。

本轮独立只读审查还重建并核对两份完整 Multi-Meta 与八份 ALCE 的导入行/映射、来源哈希、空答案分母及 ALCE 完整评分阻止边界，未发现本次范围内需修复的重要问题；没有重复运行全量测试或改变源工件。这不代表已验收服务器模型执行。

2026-09-28 本轮全量回归使用 `.venv/bin/python -m pytest -q`：**729 passed，2 skipped**，跳过原因与下方一致。新增文件题单回归覆盖空/重复/含空白 ID、真实 CLI 导入与 SN 导出范围、reference 不意外跑全量、完整资料保留和 missing 分母。直接调用 pytest console script 曾因 `scripts` 不在导入路径而在收集阶段失败，改用上述模块入口后通过，未修改产品逻辑绕过测试。

本工作树2026-09-26最新代码回归（包含 HotpotQA 适配、ALCE 分句资源隔离与 QASPER 最终引用证据接入）：

- Python：运行全量 pytest，**721 passed，2 skipped**；2 个跳过项分别需要 sibling project/native judge 或显式本地 Perl ROUGE，不涉及 HotpotQA。HotpotQA、外部答卷导入、条件指标逐题配对、比较准入、批量-only 指标和 cluster bootstrap 测试均包含在这次回归中。此前704/716项基线记录保留。
- Dashboard JavaScript：`node --test tests/dashboard_core.test.cjs tests/explorer_core.test.cjs tests/map_view.test.cjs`，**34 passed，0 skipped**。
- 独立审查复核了ALCE引用预处理/实际分母、QASPER证据槽、v3请求限制和依赖身份。已发现的问题均修复；未发现仍开放的可复现重要得分/比较错误。这不代表已经验收ALCE真实模型推理。

本分支已验证完整QASPER（416篇/1451题）和QMSum（35场/281题）适配，逐题gold变更不会改变公共材料/请求。QMSum真实Perl与独立pyrouge封装已校准。QASPER与QMSum各一题的SN/BM25真实生成、官方评分与比较报告成功，均只作链路验收，不作排名。QMSum SN原Python ROUGE诊断缺可选包而error，答案保留后由新Perl评分独立成功；没有把旧诊断错误隐藏为正常运行。

2026-09-24已补齐QASPER最终引用→原段落→官方Evidence F1：新v3 SN运行自动保存有界证据快照，旧运行可用 `export-sn --qasper-evidence` 只读恢复到新submission；评分回放不依赖live数据库。未知引用保留假阳性槽，缺观测/歧义为映射错误，保留答案且不报部分分母的Evidence F1。规则、职责及适配声明见[标准文档§3.5](notebook-benchmark-standards-and-conformance.md#35-2026-09-24-已实现的最终引用投影与特殊情况)。

真实保存的一题 `[k1]` 恢复为 `4:0`，与未修改原版CLI一致：Answer F1=2/3、Evidence F1=1，分母各1；151个旧文件hash未变。416篇公开论文经实际parser生成的11,065个完整块和81,316个选定截短检查全通过；23,111个公开单位均可达，gold变更不影响映射。独立审查发现的缺观测误判、分区尾部和Markdown来源边界已修复并回归。该验收没有新增模型调用或修改产品；不代表1451题正式成绩或真实reasoning所有路径已通过。

2026-09-24经用户启用的代理取得MultiHop/ALCE固定完整文件并通过身份校验。MultiHop全2556题/609文章、ALCE的5个普通候选文件均已完整适配。MultiHop官方QA/检索脚本、ALCE原始CLI文本指标和真实NLTK分句范围已对照；详细数据/隔离证据及适用范围见[真实数据验收](notebook-benchmark-real-data-validation.md)。本次未运行新SN/LLM生成；ALCE大型模型评分、正式全量和外部论文方法比较仍未完成。ALCE 的 5 个普通文件和 3 个 `reranked_oracle` 文件是候选资料/标注包，不是模型生成答卷；QMSum HMNet 公开答卷是 gold-input 校准资料，不能作为普通端到端对照，详见[来源审计](notebook-external-method-audit-2026-09-26.md)。

Multi-Meta-RAG 固定提交的 GPT-4/PaLM 完整答卷及实际排名均已取得：两方法各 2,556 题，逐题 query、标注与前六段组成的实际 prompt 全部匹配。固定官方 QA 分别为 **1,549/2,556（0.606025）**和 **1,553/2,556（0.607590）**；PaLM 30 条空答案保留在分母。共享检索的 Hits@10=0.904213，Hits@4=0.792018，MAP@10=0.338816，MRR@10=0.674762，分母为 2,255 非 null 题；原版 CLI 输出一致。已有两公开答卷的描述性配对报告，尚未与 SN 配对。作者完整索引身份未证明、生成模型/提示/预算不同，不能作算法归因；group 数为 1，CI 明确不可计算。

ALCE 固定仓库的 `human_eval` 包含真实生成答案，与先前 ordinary/oracle 候选资料不同。ASQA、ELI5 各四配置/100题已导入，原问题 ID 通过问题原文唯一映射；聚合行不当作题目，空答案不删除。ASQA 四配置的官方 str_em 为 0.353/0.382/0.3655/0.264667，配对报告已生成；ELI5 仅完成输入/预处理，模型指标 pending。原文件没有完整 shown-doc 列表和生成设置，统一限定为文本重评分，完整引用评分被显式阻止。详情及全部命令见[外部结果入口](notebook-external-results-2026-09-28.md)。

历史 146 题前缀仍保留：QA=97/146，[子集执行文档](notebook-multihop-subset-comparison.md)与 `var/external-comparison/multimeta-146-inputs-20260928.tar.gz` 供回放。当前 MultiHop 主范围已升级为完整 2,556 题，输入锁见 `configs/comparison-scopes/multimeta-e77e4638-full.json`。SN、reference、export/import 支持共享 `--case-id-file`。这段记录对应历史准备快照；当前协议已提交并推送，服务器真实实验仍待执行。

## 服务器实验边界

服务器反馈曾报告 QMSum request-v2、BM25、DeepSeek Agent 批次和原生评分超时恢复。数字来自用户转述，本机没有读取服务器原始 run、当前进程或配置；不能把“最近正在运行”当作此刻仍在运行，也不能把缺分当作零分。

用户已明确优先保证新评测正确，允许必要修改及重跑；不要求提供历史服务器目录。正式执行在服务器，本机这次仅按后续授权下载MultiHop/ALCE并做不调用生成模型的验收。新实验均使用独立runtime，不触碰生产数据、部署和timer。服务器旧运行是否仍在进行仍须以实际产物核实，不从历史转述推断。

## 当前下一步

1. 协议修正分支的审查与回归已完成；本地已准备 [campaign 机器可读模板](../configs/notebook-external-campaign-v1.json)、[执行题单](../configs/notebook-external-execution-plan-v1.jsonl)、[预检脚本](../scripts/validate_external_campaign.py) 和 [服务器手册](notebook-external-campaign-runbook.md)。预检已在当前四套本地 bundle 上通过；服务器上的 bundle、模型、scorer、依赖和运行身份仍未冻结，不能把模板当作正式实验。
2. 使用已核实的MultiHop/ALCE文件身份在服务器准备数据及ALCE固定模型环境；下载本身已不再是本机阻塞项，不能用替代模型产出官方完整分数。
3. 已有 MultiHop 两完整答卷、ALCE 八份样本及 QMSum Socratic 281 题答卷；ALCE 仅答案模型评分路径已实现，等待真实模型执行。QASPER LAB 与 HotpotQA KG2RAG 受控方法的代码及全量输入已准备，ALCE VANILLA 已新增保存真实所见文档和引用映射的受控入口，2,948 题输入已验证；接下来在服务器验收实际模型执行与完整引用评分。原 SegEnc/HGN/SAE 下载链接的访问失败和协议限制已记录，不能视为可直接重跑。
4. 新比较报告以JSON/Markdown交付，暂不涉及Dashboard。服务器阶段生成 MultiHop 完整 2,556 题与 ALCE 已冻结 100 题的 SN 答卷，两侧在同环境重评后再比较；当前没有 SN 对公开方法的正式结果。完整来源和 `var/` 工件被 Git 忽略，须独立搬运；操作入口见[最新外部结果](notebook-external-results-2026-09-28.md)。
