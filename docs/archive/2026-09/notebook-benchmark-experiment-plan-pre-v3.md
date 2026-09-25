# Notebook 公开 Benchmark 实验计划

文档状态：**历史设计，已于2026-09-24归档并由v3实验入口替代。** 下文保留当时的范围、建议和证据；先取得旧服务器结果及禁止本机新实验的阶段限制已经被用户后续授权替代。当前操作与未完成项见[新实验计划](../../notebook-benchmark-experiment-plan.md)。


既有 SN 主实验计划已由用户确认；当前可执行范围是现有 SN 主实验、QMSum BM25 对照与评分，向量检索和全文输入对照仍是后续目标。下方“外部比较的推进顺序”是 2026-09-23 根据讨论形成的建议与核查记录，不表示新增模型实验已经启动或获得执行安排。命令和参数以 [服务器使用说明](../../notebook-benchmarks.md) 为准，数据迁移见 [修正记录](../../notebook-data-corrections.md)。

## 目标与结论边界

本轮实验回答两个问题：

1. Silicon Notebook（SN）在公开的论文问答、多文档推理、带引用回答和会议摘要任务上表现如何。
2. 在相同题目、资料和评分协议下，SN 的 chunk 与 reasoning 模式，以及后续加入的常规 RAG 对照，有什么差异。

公开 benchmark 定义了任务、数据和评分方式，但不要求所有系统采用相同的内部检索实现。因此“导入资料到 notebook，再使用 SN Ask”是有效的系统适配方式；它不自动等于论文原实验的完全复现。任何比较都必须同时记录数据版本、题目 ID、可访问资料、回答格式、评分代码和聚合方式。

论文中公布的数字分为参考资料，除非上述条件完全一致，不直接当作排行榜或 SN 的同条件基线。实验报告使用三种标签：`published-reference`（论文原值，条件不同）、`recomputed-subset`（同题目子集重新评分）和 `controlled-rerun`（同一协议重新运行）。

## 外部比较的推进顺序（2026-09-23）

暂按内部研发和项目汇报确定优先级：先建立可信的 SN 能力描述，再回答与代表性方法相比的差距，最后用控制变量实验定位改进方向。算法、评分实现和复现路径由开发者判断；用户主要提供服务器产物位置，以及新增运行时可用的模型、资源和费用边界。资料与代码核查可以先行。

| 顺序 | 具体工作 | 本阶段结束条件 |
| --- | --- | --- |
| 1. 四套协议核查 | 对照固定官方版本与当前实现，检查题目范围、输入权限、gold 隔离、请求、评分、失败分母；使用独立官方函数做差分验证，并核实真实 run | 每套逐项标为已验证一致、已知适配差异、实现缺陷或证据不足；每个影响比较的缺口都有证据和处理路径 |
| 2. 处理影响结论的缺口 | 先处理数据泄漏、题目错配、评分错误及失败被静默忽略；评分问题优先利用保存的答卷独立重评，生成输入有问题才评估重跑 | 能说明哪些历史结果仍可使用、哪些需重评分、哪些必须新实验；缺证据的套件单独等待，不阻塞已核实的套件 |
| 3. 建立第一次可信比较 | 优先验收已有 QMSum 的 SN chunk、reasoning 与 BM25 同题比较；先确认实际模型和 scorer 身份，再选择预先固定的代表性会议 | 有逐题差异、计划题目数、完成与评分覆盖、失败原因、可观测成本及人工核验记录；小规模验收不产生排名或显著性结论 |
| 4. 纳入外部方法 | 每套先筛选约 3–5 个原始/后续代表性候选，核查论文配置、公开代码、逐题预测和复现成本，推荐 1–2 个 | 每套有推荐比较路径或明确不可比原因；材料足以支持选择后结束该轮检索，不无限扩展候选 |
| 5. 冻结并扩大 | 固定题单、输入、参数、scorer 和失败处理后运行正式比较；按论文/会议关联性分析配对差异，再选择一个有证据的改进点 | 输出可追溯的比较报告和开发优先级，明确统计不确定性与适用范围；调参使用独立 train/dev 或预先留出集合 |

第 4 步的公开资料检索可与第 1 步交错进行；模型复现及正式比较依赖协议核查结果。QMSum 的优先级来自已有 BM25、运行工件和比较命令可复用，不意味着它已经具备与原论文直接比较的条件。如果真实数据/评分身份无法核实，先处理该缺口；必要时改选材料齐全的套件，不为了完成四套全量实验降低证据要求。

产品评测与外部比较可以保留不同的报告视图：前者记录完整回答、澄清、失败和产品诊断；后者遵循选定外部协议的输入、预处理与分母。新增官方口径视图属于后续实现建议，当前没有自动生成能力。它应读取原始记录并记录转换规则，不能把历史 null 改写成零；统一重评分也不能修复生成阶段的资料差异。

### 当前第一步的证据

本地核实基线为 `e022c6034189f618cb5f377cf2c0af7c2acfb66c`。初步静态核对已确认 QASPER 子集筛选、ALCE 多行正文/引用参数、QMSum ROUGE 等差异，详见[官方资料与适配说明](../../notebook-benchmark-official-resources.md)。这些是已知边界，不自动判定为实现缺陷。

2026-09-23 做了一次无需模型的探索性探针：保存固定提交的官方脚本及 SHA256，用 AST 原样提取确定性函数，排除模型导入和 CLI 入口；将构造输入交给官方函数和当前 `score_case`。正文公式对照在两侧采用相同的 SN 引用标记清理，ALCE 分数统一到 0..1。Python 为 3.13.15，NumPy 为 2.5.3。

| 对照范围 | 指标比较次数 | 超出绝对容差 1e-12 的差异 |
| --- | --- | --- |
| QASPER 答案 F1、多个参考答案取最大值 | 126 | 0 |
| MultiHop-RAG 答案抽取和弱匹配 | 68 | 0 |
| ALCE-ASQA STR-EM / STR-HIT | 34 | 0 |
| ALCE-QAMPARI 五项列表指标 | 120 | 0 |

348 是构造输入上的指标比较次数，不是 348 道真实 benchmark 题。探针还验证了两个反例：

- QASPER 两题中一题正确、另一题无预测，官方全题 Answer F1 为 0.5；当前实现对失败题保存 `unscored/null`。如只汇总已评分题，就不能把该均值称为同分母的官方分数。
- ALCE 构造回答 `falcon\nowl`（中间为实际换行），两组 gold 分别包含 falcon 和 owl。当前完整正文 STR-EM 为 1.0；应用从官方 CLI 提取的首行处理后为 0.5。此数值用于证明预处理会影响结果，不是产品能力结果。

官方函数来源：[QASPER evaluator](https://github.com/allenai/qasper-led-baseline/blob/afd0fb96bf78ce8cd8157639c6f6a6995e4f9089/scripts/evaluator.py)、[MultiHop QA evaluator](https://github.com/yixuantt/MultiHop-RAG/blob/c1c1287aa60a94acf9c4d20c891c9cd611a0f6e8/qa_evaluate.py)、[ALCE eval](https://github.com/princeton-nlp/ALCE/blob/246c476a4edfc564266b7346b6e29ef4861ae937/eval.py) 与 [ALCE utils](https://github.com/princeton-nlp/ALCE/blob/246c476a4edfc564266b7346b6e29ef4861ae937/utils.py)。

探针、源码副本、来源哈希和逐项结果保存在本机 `var/official-protocol-probe-20260923/`，属于被 Git 忽略的探索工件，不随仓库分发，也不是新增的正式测试套件。在该目录完整保留的本机可用 `.venv/bin/python var/official-protocol-probe-20260923/probe.py` 离线复查。当前 scorer 的 SHA256 为 `ecb8ac9120ce98d7d86c7bd668bb2cca59541c1fb001051d98e201287bf9ce87`。

这轮尚未验证真实数据筛选、模型实际输入、服务器 run 汇总、ALCE 模型评分或 QMSum 官方 ROUGE 等价性。本机 `.venv` 未安装 `rouge-score`；没有自动安装依赖、下载数据/模型或发起推理。公式探针通过不能替代完整协议验收。

### 下一批需要的服务器证据

先取得以下路径和已有产物清单，不要求重新生成答案：

- 每套 bundle 的 `manifest.json`、`source.json`、`decisions.jsonl`、`partitions.jsonl`；完整题目 ID/原始文件哈希在服务器核对，不能只用题目数量判断版本一致。
- 一组同会议 QMSum chunk、reasoning、BM25 run 的 `manifest.json`、`planned.jsonl`、`outputs.jsonl`、`scores.jsonl`、`state.json` 及冻结 `input/` 的位置；缺某种模式就如实列出。
- 两侧实际生成模型、请求修订、依赖和 scorer 身份；沿用脱敏配置及哈希，不索取密钥或公开服务地址。ALCE 如已补分，还需其派生 run 和评分调用身份。

首批典型回答用于回放和人工核验；完整计划与状态账本用于核对覆盖率，不能以精选成功样例代替全量分母。公开资料核查不因这些产物暂缺而停止；服务器实验与新增费用另按实际资源安排。

## 已冻结的 SN 主实验

协议名为 `sn-notebook-benchmarks-v1`。每个分区 × 模式使用独立进程、独立 notebook 和独立 runtime；问题之间 `conversation_id=None`，不共享历史。chunk 与 reasoning 使用同一题目、资料、模型服务配置和 scorer 身份。reasoning 的澄清请求不填入标准答案，记录为 clarification 并从质量分数中保持未评分。

| 套件 | 资料条件 | 模式 | 主要问题 |
| --- | --- | --- | --- |
| QASPER | 一篇论文的完整正文；按论文分区 | chunk、reasoning | 论文问答和证据利用是否受模式影响 |
| MultiHop-RAG | 完整 609 篇 corpus；一个完整 corpus 分区 | chunk、reasoning | 多文档检索、推理和 null query 处理 |
| ALCE-ASQA | 每题官方候选资料全集，保留顺序 | chunk、reasoning | 长答案覆盖与真实引用支持 |
| QMSum | 一场会议的全部发言，保留 speaker 和空 turn | chunk、reasoning | 面向 query 的长会议定位与摘要 |
| ALCE-QAMPARI / ELI5 | 按各自 bundle 的完整候选资料 | chunk、reasoning | 列表答案或 claim 支持；按准备状态加入 |

正式运行使用冻结规则下全部符合条件的 case，不设置题目数量上限。QASPER 的文本可适配筛选、QMSum 的数据版本差异和空值修正必须在 manifest 中保留，不能事后为匹配论文数量删题。

## 运行阶段

### 1. 数据与协议验收

服务器 agent 先核对来源文件、SHA256、split、case 数、排除决定和 partition 数；按 `docs/notebook-data-corrections.md` 重新准备 QASPER/QMSum，复用仍有效的 MultiHop/ASQA bundle。先运行一个分区的 chunk 和 reasoning，核对导入资料、题目、回答正文、最终上下文、引用对象和状态账本，再开始全量。

### 2. SN 全量主实验

枚举 `partitions.jsonl × {chunk, reasoning}`，每个组合写入独立 run 目录。保留 planned、outputs、scores、manifest、state 和源代码/配置身份。统计计划数、完成数、正常回答、clarification、no-answer、error、缺评分和覆盖率；失败和缺评分不能用 0 替代，也不能从分母中静默删除。

### 3. 离线评分与引用补分

先使用确定性 scorer 生成主结果。ALCE 的 AutoAIS/claims 等模型评分单独导出，在固定 ALCE commit 和本地模型上运行，再挂接为派生 run；原始 SN run 保留。Dashboard 选择派生 run 时，不把父 run 和派生 run 计为两次尝试。

### 4. 对照实验

当前代码已支持 SN 两种模式，并已加入第一条可执行的 QMSum BM25 生成基线。向量检索和全文输入仍是后续新增的生成基线。基线与 SN 使用相同冻结资料、题目和评分器，并尽量对齐实际生成模型与采样设置。常规基线之间固定生成提示；SN 保留内部原生提示，因此目前比较的是端到端系统，不是只改变检索器的消融：

| 对照 | 作用 | 约束 |
| --- | --- | --- |
| BM25 + 同一生成模型 | 透明的关键词检索参照（不保证是下界） | QMSum 已支持；固定 turn、top-k 和字符上下文预算 |
| Dense retrieval + 同一生成模型 | 常规语义 RAG 参照 | 固定 embedding、top-k、分块和上下文预算 |
| Full-context（可行时） | 小资料集的全文输入参照（不保证是上限） | 资料必须完整放入上下文；截断要单独报告 |
| Gold-evidence（诊断） | 诊断给定标准证据时的生成/推理 | 与自行检索结果分组，不能混作主结果 |

QMSum BM25 使用 `scripts/run_notebook_baseline.py`，只支持一个完整会议分区一次运行；它按 turn 做 BM25，恢复原会议顺序，在预算内构造 prompt，再调用显式 `tested` 生成模型。回答和检索 turn、分数、模型事件保存为 `mode=bm25` 的现有 run 格式，Dashboard 可与 SN run 分面查看。基线代码、模型和运行身份必须写入新的实验协议；不通过复制论文数字替代运行基线。

示例（会实际调用配置的模型，先用一个分区验收）：

```bash
python scripts/run_notebook_baseline.py \
  --bundle /eval/bundles/qmsum \
  --partition-id '<partitions.jsonl 中的 ID>' \
  --project-root /path/to/silicon-notebook/project \
  --model-config /eval/configs/qmsum-baseline-models.json \
  --run-dir /eval/runs/qmsum-partition-bm25 \
  --top-k 8 --max-context-chars 12000
```

`--model-config` 使用 `tested` 角色的显式 endpoint 环境变量；不把 SN 的内部检索结果或 gold span 注入 baseline。当前实现不下载数据、不自动安装依赖，也不恢复或修改 SN 服务。

## 指标与比较

每个 suite 单独报告主指标，不生成跨 benchmark 综合分数：

- QASPER：答案 token F1 为主；上下文段落 F1 为诊断，不能称为官方 evidence F1。
- MultiHop-RAG：固定版本的答案 scorer 为主；fact coverage 为诊断。当前 SN 结果没有检索排序，因此不能与 Hits@K/MAP/MRR 直接比较。
- ALCE：ASQA/QAMPARI 的官方字符串指标，以及显式运行的引用/claim 模型指标。
- QMSum：ROUGE-1/2/L F1；specific query 的 turn coverage 仅作诊断，并明确当前实现不是论文完全复现声明。

所有结果同时展示质量、覆盖率、澄清/失败率、延迟、调用次数和可获得的 token/成本。chunk 与 reasoning 以共同 case 的配对差异为主要比较；按 question type、资料规模和模式配置分层，避免只看总体均值。

## 与论文和其他方法比较的规则

只有以下条件全部匹配时，才可称为同条件分数：split 和 case ID、输入资料范围、是否 gold evidence、回答格式、预处理、官方 scorer 版本、聚合方式。完整系统比较允许模型、算法不同，但要披露；若归因到模式或流程，需控制基础模型等变量。否则使用“参考值”措辞并列出差异。

对于 QMSum，论文同时展示自行定位和 gold span 输入，不能把 SN 自行检索结果与 gold span 结果放在同一主表。对于 MultiHop-RAG，论文也分别报告 retrieved chunk 与 ground-truth evidence；两者应是两个实验条件。对于 ALCE，配置中的 top-100 候选和实际送入生成模型的 ndoc=5 是不同概念，报告必须记录实际资料预算。

若要说明“SN reasoning 流程优于 chunk”，应控制基础模型、资料、题目和 scorer；若要比较完整系统，则允许检索和模型不同，但结论只能描述完整系统表现差异。论文分数只作背景，最可信的外部比较是取得逐题预测后用同一 scorer 重算，或在本机同协议重跑。

## 服务器验收产物

每套 suite 至少提交：冻结 manifest、partition 对账、两种模式的 run 目录、逐题 outputs/scores、异常与澄清清单、配置快照、评分器身份、引用转换记录、Dashboard、以及一份比较表。比较表必须标注 `published-reference`、`recomputed-subset` 或 `controlled-rerun`，并列出未对齐条件。

实验完成只表示运行和审计完成，不表示形成发布门槛。阈值、综合质量分和产品结论仍需人工抽样核验后再讨论。

## 边界

本阶段不恢复 weekly timer，不修改 SN 生产代码，不下载或执行本机开发环境中的数据/模型，不扩展 KG、重排、PDF/OCR，也不把不完整的 reasoning trace 当作 Agent 得分。QMSum BM25 已实现代码与离线验证，真实模型验收由服务器安排；dense、全文输入和其他数据集的基线仍属于后续代码工作。不会因更新代码自动启动任何新实验。


## 执行约定与检查清单

- [ ] 记录 Git commit、SN commit、实际模型配置与依赖；保留已有未提交文件和历史运行，不执行 reset/clean。
- [ ] 新建本轮 campaign 目录，保存 `experiment-manifest.json`、`execution-plan.jsonl`、`execution-status.jsonl`、`summary.md`；这些是服务器维护的交接产物，不是已有 CLI 自动生成的文件。
- [ ] execution-plan 每行记录 suite/task/bundle 哈希/partition_id/mode/run_dir；正式题量来自 bundle，不从 Dashboard 已发现的 run 数推断。
- [ ] 验收选择覆盖正常回答、无答案和代表性数据边界的分区，执行真实导入及两种模式，检查资料不含 gold、上下文映射和引用。选择及失败均记录；验收不是人为限量正式集合。
- [ ] 本轮默认保留已有 runner 的原问题、任务提示和正文评分，不临时修改提示/抽取答案。需要改变时创建新协议并单列，不能混入本轮。
- [ ] 冻结后每个组合首次执行一次；不得因低分重试。技术失败保留原尝试，在新目录记录重试原因与父运行，主结果预先约定使用首个技术有效尝试，并报告全部失败。诊断重复运行单列，不挑最高分。
- [ ] 从串行执行开始，确认共享模型服务资源后再调整并发。无需人为给实验设置总时限；保留产品已有超时设置及异常。不得停止线上服务。
- [ ] ALCE 所需官方代码/模型/依赖缺失时记录缺口，可在服务器按许可获取并记录版本；模型评分未完成就保持 unscored，不阻塞其他套件。
- [ ] Dashboard 对账全部预期分区；只展示共同成功题的配对差异时，同时展示固定题单上的完成与缺分情况。当前无官方全题失败补零视图，不自行改写 null。
- [ ] 本轮没有在测试集上调参。后续参数选择使用 train/dev；MultiHop 发布集合不得伪称独立 test，如需调参先固定独立留出方案。
- [ ] 最终给出完成/部分完成及具体缺口，不把 ALCE 原 run 与补分派生 run 算作重复；不宣称未核对的论文可比性。

成本字段仅报告可观测值，缺失用 unavailable；均值差异不自动解释成显著提升。若进一步统计置信区间，应保留同题配对，并考虑 QASPER 同论文、QMSum 同会议内样本相关性。QMSum BM25 的逐题对照、覆盖率和配对均值差已由独立比较命令实现，见 [BM25 使用与边界](../../qmsum-bm25-baseline.md)；按会议聚类的置信区间、显著性和完整成本分析尚未实现。

## 官方依据

- [QASPER evaluator](https://github.com/allenai/qasper-led-baseline/blob/afd0fb96bf78ce8cd8157639c6f6a6995e4f9089/scripts/evaluator.py)：text_evidence_only 不等同于删除图表相关问题；本项目子集不能冒称完整官方测试集。
- [MultiHop-RAG 论文 v1](https://arxiv.org/html/2401.15391v1)：区分检索、检索后生成和标准证据生成；复现特定论文版本前须重新核对其设置。
- [ALCE 官方配置](https://github.com/princeton-nlp/ALCE/blob/246c476a4edfc564266b7346b6e29ef4861ae937/configs/asqa_turbo_shot2_ndoc5_gtr_default.yaml) 与 [评分代码](https://github.com/princeton-nlp/ALCE/blob/246c476a4edfc564266b7346b6e29ef4861ae937/eval.py)：本项目保留完整多行正文、at_most_citations=None，与 CLI 默认首行/最多三引用处理不同。
- [QMSum 论文](https://aclanthology.org/2021.naacl-main.472.pdf)：区分定位片段与标准片段输入；表 1 的 test 为 279，服务器文件报告 281，应核对版本和 ID，不为凑数删题。
