# 评测状态

核实基线：2026-09-24（北京时间）。本页是当前快照；详细历史、旧命令和交接上下文见[文档导航](README.md)和[历史归档](archive/README.md)。

## 当前代码与 Git

- 本地 `benchmark-deepeval/main` 已合并 Dashboard、原生评分恢复及文档整理，HEAD 为 `e022c60`。
- 本地 `main` 相对已保存的 `origin/main` ahead 59；本轮没有 fetch/push，不能把本地跟踪引用称为刚核实的远程现状。
- 当前实现位于 `.worktrees/benchmark-protocol-correctness`，分支 `feat/benchmark-protocol-correctness`，起点 `e022c60`；改动尚未提交、合并或推送。
- Dashboard 合并提交为 `e4c5333`，原生评分恢复合并提交为 `03ca577`。
- 旧远程开发分支信息见归档，本轮未重新查询远程。
- 主线原有未提交文档已复制到实现worktree继续整理，主线原文件保留。产品checkout没有新增受管理改动；原有 `.deepeval/`、`results/` 未清理。

## 当前可用入口

### Dashboard

`scripts/build_experiment_dashboard.py` 读取已有 run，生成实验地图、单题流程/原生 span 回放和结果分析。它不会重新问答、重新评分或修改原始工件。使用和比较限制见[实验 Dashboard](experiment-dashboard.md)。

### Notebook 场景

当前分支：prepare默认 `notebook-data-v3`，SN/Agent CLI默认无gold的 `notebook-request-v3`。新增 `run_benchmark_reference.py`（BM25/full-context/ALCE candidate-topk）、`benchmark_protocol.py`（答卷导出/官方评分）和 `compare_benchmark_submissions.py`（严格身份、分母和实际成本观测）。执行与比较限制见[当前实验计划](notebook-benchmark-experiment-plan.md)。旧Notebook诊断、QMSum单会议baseline和Dashboard入口保留。

四套 benchmark 的标准定义、官方依据、精确评分规则、代码对应及已验证/适配/待验证边界已整理为[标准与实现符合性](notebook-benchmark-standards-and-conformance.md)。Python API 的旧默认值仍保留；新调用需显式选择 v3，不能把 CLI 默认推广为所有接口默认。

### 原生 Agent 与组件补评

`scripts/run_notebook_agent.py` 使用 `sn-deepeval-native-v1`，默认评检索/合成组件；`--trajectory` 才评完整 Agent 轨迹。回答和组件先保存，judge 随后评分。`scripts/score_native_components.py` 可从已保存组件建立独立补评目录。入口、失败语义和超时边界见[原生 Agent](native-agent-evaluation.md)与[评分恢复](native-scoring-recovery.md)。

## 验证证据

本分支2026-09-24最新代码回归（包含ALCE分句资源隔离与QASPER最终引用证据接入）：

- Python：显式指定产品checkout、真实Perl ROUGE目录和隔离Perl依赖后运行全量pytest，**697 passed，0 skipped，10.74秒**；本次新增40项QASPER证据测试，准确命令见[证据接入实施记录](superpowers/plans/2026-09-24-qasper-sn-evidence.md)。此前657项基线记录保留。
- Dashboard JavaScript：`node --test tests/dashboard_core.test.cjs tests/explorer_core.test.cjs tests/map_view.test.cjs`，**34 passed，0 skipped**。
- 独立审查复核了ALCE引用预处理/实际分母、QASPER证据槽、v3请求限制和依赖身份。已发现的问题均修复；未发现仍开放的可复现重要得分/比较错误。这不代表已经验收ALCE真实模型推理。

本分支已验证完整QASPER（416篇/1451题）和QMSum（35场/281题）适配，逐题gold变更不会改变公共材料/请求。QMSum真实Perl与独立pyrouge封装已校准。QASPER与QMSum各一题的SN/BM25真实生成、官方评分与比较报告成功，均只作链路验收，不作排名。QMSum SN原Python ROUGE诊断缺可选包而error，答案保留后由新Perl评分独立成功；没有把旧诊断错误隐藏为正常运行。

2026-09-24已补齐QASPER最终引用→原段落→官方Evidence F1：新v3 SN运行自动保存有界证据快照，旧运行可用 `export-sn --qasper-evidence` 只读恢复到新submission；评分回放不依赖live数据库。未知引用保留假阳性槽，缺观测/歧义为映射错误，保留答案且不报部分分母的Evidence F1。规则、职责及适配声明见[标准文档§3.5](notebook-benchmark-standards-and-conformance.md#35-2026-09-24-已实现的最终引用投影与特殊情况)。

真实保存的一题 `[k1]` 恢复为 `4:0`，与未修改原版CLI一致：Answer F1=2/3、Evidence F1=1，分母各1；151个旧文件hash未变。416篇公开论文经实际parser生成的11,065个完整块和81,316个选定截短检查全通过；23,111个公开单位均可达，gold变更不影响映射。独立审查发现的缺观测误判、分区尾部和Markdown来源边界已修复并回归。该验收没有新增模型调用或修改产品；不代表1451题正式成绩或真实reasoning所有路径已通过。

2026-09-24经用户启用的代理取得MultiHop/ALCE固定完整文件并通过身份校验。MultiHop全2556题/609文章、ALCE的5个普通候选文件均已完整适配。MultiHop官方QA/检索脚本、ALCE原始CLI文本指标和真实NLTK分句范围已对照；详细数据/隔离证据及适用范围见[真实数据验收](notebook-benchmark-real-data-validation.md)。本次未运行新SN/LLM生成；ALCE大型模型评分、正式全量和外部论文方法比较仍未完成。

## 服务器实验边界

服务器反馈曾报告 QMSum request-v2、BM25、DeepSeek Agent 批次和原生评分超时恢复。数字来自用户转述，本机没有读取服务器原始 run、当前进程或配置；不能把“最近正在运行”当作此刻仍在运行，也不能把缺分当作零分。

用户已明确优先保证新评测正确，允许必要修改及重跑；不要求提供历史服务器目录。正式执行在服务器，本机这次仅按后续授权下载MultiHop/ALCE并做不调用生成模型的验收。新实验均使用独立runtime，不触碰生产数据、部署和timer。服务器旧运行是否仍在进行仍须以实际产物核实，不从历史转述推断。

## 当前下一步

1. 协议修正分支的审查与回归已完成；正式全量配置尚未冻结。固定代码、模型和运行配置后扩大已具备数据/评分环境的QASPER、QMSum实验。
2. 使用已核实的MultiHop/ALCE文件身份在服务器准备数据及ALCE固定模型环境；下载本身已不再是本机阻塞项，不能用替代模型产出官方完整分数。
3. 按当前实验计划核对外部方法公开答卷/检查点，建立同题重评分或明确不同条件的复现。
4. 新submission比较报告暂以JSON/Markdown交付，尚未接入Dashboard；不要改工件伪装成旧run。
