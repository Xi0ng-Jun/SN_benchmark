# Notebook Benchmark：正确评测与比较

实验进展快照：2026-09-28；事实与范围表述澄清：2026-10-09。开发分支为 `feat/benchmark-protocol-correctness`；服务器执行时以实际 checkout SHA 为准。本页保留实验协议与候选计划；当前执行范围以 [CURRENT_STATE.md](../CURRENT_STATE.md) 为准，服务器先做 SN-only，外部方法/reference 暂缓。[先前计划](archive/2026-09/notebook-benchmark-experiment-plan-pre-v3.md)保留历史背景。历史服务器结果不再是前置条件。正式模型实验只在服务器执行；本机已完成数据与评分适配核对，不再启动模型调用。生产部署、定时任务和远程发布不在本轮范围。

## 目标与流程

2026-09-28 外部方法代码补齐进展：QASPER LAB、HotpotQA KG2RAG 和 ALCE VANILLA 均已有受控运行入口及完整无 gold 输入准备；ALCE 三任务 2,948 题的提示与所见文档已对作者 main 校准，可在新生成时保存完整引用映射。公开 MultiHop/QMSum 答卷的重评分已完成。Hotpot SN supporting-fact 已接通最终引用投影与离线重放，服务器真实观测仍待验收。正式 SN 配对及新增方法的真实模型执行仍未完成，入口和差异以[外部结果](notebook-external-results-2026-09-28.md)为准。

在预先固定的数据、输入权限、回答要求和评分规则下，运行 Silicon Notebook（SN）及对照方法，保存可检查的答卷，得到可解释的比较。这里要把模型服务分成三层记录：回答生成模型、检索/重排等方法组件、以及 AutoAIS/QA/MAUVE 等评分模型。评分模型由官方 scorer 固定，不属于被比较方法的模型优势。

主比较（SN chunk/reasoning 与项目 BM25 或 full-context control）要求两边解析后的 `answer_generation` 身份一致：同一模型或服务版本、tokenizer、请求提示版本、temperature/top-p/max-tokens、超时、重试和随机性规则。`SN_MODEL_CONFIG` 与 `REFERENCE_MODEL_CONFIG` 可以是不同文件以适配两套 CLI，但提交前必须在 `experiment-manifest.json` 中证明解析后的身份相同。SN 额外使用的意图、embedding 或 reranker 仍要单独记录；只要回答模型或这些关键预算不一致，比较就标记为 `mixed-model-end-to-end`，不能把差值归因于检索器。

作者方法复现遵循作者要求的模型和组件，不能为了表面公平替换成 SN 模型；若替换或改写提示/预算，方法名必须带 `adapted`/`controlled`，只能作端到端或适配比较。已经发布的逐题答卷保留原始模型身份，只能作 `recomputed-subset` 或 `published-reference` 的描述性比较。服务器阶段的候选、smoke/full 顺序和回传工件见[外部比较 campaign 手册](notebook-external-campaign-runbook.md)；模板只冻结计划结构，实际环境身份仍须在服务器生成 `experiment-manifest.json` 后才能开始 full。

核实原始数据 → 冻结 v3 bundle → 无 gold 请求 → 保存答卷及失败状态 → 官方评分 → 校验比较身份 → 核验案例与统计不确定性 → 扩大实验。小样本先检查链路；正式比较固定全题或预先声明的子集，不按测试分数挑题、重试或调参，不生成跨套件总分。

## 五套具体协议

规则的逐项依据、精确公式、实现入口和符合程度见[Benchmark 标准与实现符合性](notebook-benchmark-standards-and-conformance.md)。本页提供执行摘要与命令；官方评分一致性、原论文复现和受控比较分别验收。

| 套件 | 新实验输入 | 官方评分口径及限制 |
| --- | --- | --- |
| QASPER | v0.3全题；每篇标题、摘要、章节正文及公开caption；FLOAT/unmapped只记录，不删题 | 固定作者evaluator，Answer F1多参考最大值；text_evidence_only只过滤gold的FLOAT证据项；Evidence F1须方法明确预测原始段落，不能用上下文覆盖冒充 |
| MultiHop-RAG | queries与完整corpus；保留标题、URL、来源、日期、作者、类别及正文 | 固定作者QA弱词匹配；真实BM25和 SN chunk-native ranking 可评官方Hits/MAP/MRR，null不进检索分母；SN reasoning 无单一排名，保持pending |
| ALCE | ASQA/QAMPARI/ELI5分开；ordinary候选全集保留顺序，实际模型选用资料另记 | 固定CLI首行截断、去结束标记、每单元最多3引用；真实citation映射；模型指标显式批评分，MAUVE不可逐题平均 |
| QMSum | 全会议、全查询、speaker与原turn，不注入gold span | 作者确认Perl ROUGE-1.5.5，参数 `-c 95 -r 1000 -n 2 -m -a`；明确使用HMNet regex分句；保留批量Average_F |
| HotpotQA | distractor validation 全题；每题完整 context 与句子 source units | Answer EM/F1/Precision/Recall 按固定 evaluator；Supporting Fact/Joint 只有显式 `[title, sent_id]` 预测才评分，SN 当前 pending |

QASPER作者reader对FLOAT只统计、不实际删题，其full_text轨道仅章节标题与段落；本项目加入摘要/caption是公开输入变体，不宣称复现LED论文表。QMSum原论文未完整规定分句与文件编号，也不宣称完全复现历史数字。出处见[官方资料手册](notebook-benchmark-official-resources.md)。

HotpotQA 第一版只进入 `distractor/validation`：每题的 context 是完整不可拆分资料范围，生成请求隐藏 answer、type、level 和 supporting_facts。原始 JSON 与 HF 列式 dict shape 都要在 prepare 阶段审计。服务器 smoke 要覆盖 bridge/comparison、多个支持句、自然干扰段落、`no_answer`/error 和答案规范化；正式 SN 主表先报告 Answer 四项，Supporting Fact/Joint 只有在 projection policy、mapping error 和 replay snapshot 固定后才解除 pending。`fullwiki` 和无 gold test 另建 setting/source identity。

## 实际验证与未完成项

2026-09-28 入口补齐前的历史回归快照为 749 项 Python 测试通过、2 项因缺少显式可选环境而跳过；Dashboard JavaScript 当时最近记录为 34 项通过。跳过项不涉及 HotpotQA 适配。这些计数保留为阶段证据，不代表当前测试数量；后续最新已记录验证见[评测状态](evaluation-status.md)，此前审查修复见[协议实施记录](superpowers/plans/2026-09-23-benchmark-protocol-correctness.md)与[QASPER证据接入记录](superpowers/plans/2026-09-24-qasper-sn-evidence.md)。测试通过不替代下表的真实数据、模型和外部方法验收。

| 验证 | 实际结果 | 结论范围 |
| --- | --- | --- |
| QASPER完整适配 | 416篇、1451题、0排除；全题gold变更不影响公共资料/请求；1451份答案、类型、证据与官方raw parser一致 | 验证数据分母、公共输入、gold隔离及参考答案 |
| QMSum完整适配 | 35场、281题、20718 turns（17空turn）、0排除；全题gold变更不影响请求 | 当前文件完整性；不是论文279题 |
| HotpotQA 完整数据与评分 | HF 固定 7,405 题/73,700 段落已适配；49 个空白句位和一处越界 gold 原样保留；12 项评分与原版 CLI 在全量合成校准上误差小于 1e-12 | 校准不是模型成绩；SN 运行及 supporting-fact projection 仍 pending |
| QMSum HMNet公开答卷校准 | 同序同分句时，当前SPL与独立pyrouge SEE均36.464/11.374/31.558 | 两种封装调用同一Perl一致；未严格复现README 36.51/11.41/31.60 |
| QASPER一题新实验 | SN chunk与BM25真实生成、答卷导出、官方评分、比较CLI成功 | 链路smoke，不支持方法排名 |
| QASPER最终引用证据 | 已保存一题只读恢复 `4:0`，原CLI与框架Evidence F1=1、Answer F1=2/3；416篇11,065块/81,316截短验收通过，8 个手算评分器校准反例 | 新快照与旧显式导出可用；校准反例不是独立人工评测；无新生成，不代表全量或所有reasoning路径 |
| QMSum一题新实验 | SN chunk与BM25真实生成、Perl评分、比较CLI成功；SN原Python ROUGE诊断缺包error，未覆盖此错误 | 验证答卷独立于旧诊断保存，可单独重评分；不是正式全量实验 |
| MultiHop完整适配与评分 | 固定官方下载校验成功；2556题/609文章/6084证据，0排除；独立BM25排名、全题正负QA校准和官方完整检索/QA CLI对齐 | 真实完整数据、输入隔离与评分桥接验收；无新SN生成 |
| ALCE完整数据与文本评分 | 包SHA256匹配；ASQA948、QAMPARI1000、ELI51000；5个普通候选文件完整适配，原始CLI文本指标与预处理对齐 | 实际候选数/重复/空别名保留；具体输入隔离及oracle核验见[验收记录](notebook-benchmark-real-data-validation.md) |
| ALCE模型指标 | 真实NLTK和官方AutoAIS控制流的参与题目对齐；尚未运行真实模型批评分 | 无AutoAIS/QA/MAUVE真实成绩；控制流探针不提供语义分数 |
| 外部方法正式对照 | MultiHop 两模型完整答卷与检索重评、ALCE 八份样本和 ASQA 配对、QMSum Socratic 281 题 Perl 重评已完成；QASPER LAB/HotpotQA KG2RAG 受控代码与全量公共输入已准备；真实执行与 SN 配对仍 pending | 已有外部答卷描述性比较，不宣称 SN 已优于外部方法 |

HMNet公开答卷为 **gold-input、279份**；273份可唯一匹配当前test，6份不匹配，当前有8题未匹配。它用于评分校准，不能作为281题端到端对照。Perl的bootstrap总分受文件编号顺序影响，固定题目顺序且不重算逐题均值。

实际工件在本工作树被Git忽略的 `var/benchmark-protocol-validation/`：全量数据报告、`smoke-qasper-sn/`、`smoke-qasper-reference-validated-20260924/`、`smoke-qmsum-{sn,reference}-validated-20260924/`、`comparison-{qasper,qmsum}-validated-20260924/`。Perl校准在 `var/qmsum-official-calibration/verified-calibration/`。这些本机工件不随Git自动分发。

## 准备与生成

以下 `python` 指具备本项目及SN依赖的解释器，本机为 `.venv/bin/python`。输出必须新目录；source.json填写实际dataset/revision/split/URL/许可，prepare冻结本地文件而不代替来源认证。

```bash
python scripts/prepare_notebook_benchmarks.py \
  --suite qasper --raw /data/qasper-test-v0.3.json \
  --source /data/qasper-source.json --output /eval/bundles/qasper-v3 \
  --adaptation-revision notebook-data-v3

python scripts/run_notebook_benchmarks.py \
  --bundle /eval/bundles/qasper-v3 --partition-id '<partition-id>' \
  --mode chunk --request-revision notebook-request-v3 \
  --project-root /path/to/project --model-config /private/model-services.toml \
  --run-dir /eval/runs/qasper-partition-chunk

python scripts/run_benchmark_reference.py \
  --bundle /eval/bundles/qasper-v3 --strategy bm25 \
  --project-root /path/to/project --model-config /private/reference-model.json \
  --top-k 10 --max-context-chars 16000 --chunk-window 256 --chunk-overlap 32 \
  --run-dir /eval/runs/qasper-bm25

# HotpotQA distractor validation smoke/full run: one partition is one question context.
python scripts/prepare_notebook_benchmarks.py \
  --suite hotpotqa --raw /data/hotpot_dev_distractor_v1.json \
  --source /data/hotpot-distractor-source.json --max-documents 10 \
  --output /eval/bundles/hotpotqa-distractor-v3
python scripts/run_notebook_benchmarks.py \
  --bundle /eval/bundles/hotpotqa-distractor-v3 --partition-id '<partition-id>' \
  --mode chunk --request-revision notebook-request-v3 \
  --project-root /path/to/project --model-config /private/model-services.toml \
  --run-dir /eval/runs/hotpotqa-distractor-smoke
```

SN一次一个资料分区，遍历全部分区形成全量；两类生成CLI均可重复 `--case-id` 声明小样本，或用 `--case-id-file` 读取每行一个 ID 的题单，完整资料不变。文件拒绝空文件、空行、重复和含空白 ID；与命令行组合后的范围仍由选择器校验。reference默认全bundle。`full-context`预算不足记录错误并拒绝推理，不静默截断；ALCE的`candidate-topk`是控制组，不能称官方VANILLA提示复现。BM25保留实际排名、上下文及预算排除。

参考模型配置引用环境变量，不写地址/密钥：

```json
{"tested":{"model_id":"<actual-model>","base_url_env":"BENCH_MODEL_URL","api_key_env":"BENCH_MODEL_KEY","parameters":{"temperature":0,"top_p":1,"max_tokens":1024,"max_retries":0,"timeout":60}}}
```

## 答卷、评分与比较

```bash
python scripts/benchmark_protocol.py fetch-sources --output /eval/scorers
python scripts/benchmark_protocol.py export-sn \
  --bundle /eval/bundles/qasper-v3 --runs /eval/runs/qasper-partition-chunk \
  --output /eval/submissions/sn-qasper
python scripts/benchmark_protocol.py score \
  --bundle /eval/bundles/qasper-v3 --submission /eval/submissions/sn-qasper/submission.json \
  --sources /eval/scorers --output /eval/scores/sn-qasper
python scripts/benchmark_protocol.py score \
  --bundle /eval/bundles/qasper-v3 --submission /eval/runs/qasper-bm25/submission.json \
  --sources /eval/scorers --output /eval/scores/bm25-qasper
python scripts/compare_benchmark_submissions.py \
  --bundle /eval/bundles/qasper-v3 \
  --entry /eval/submissions/sn-qasper/submission.json /eval/scores/sn-qasper/scores.json \
  --entry /eval/runs/qasper-bm25/submission.json /eval/scores/bm25-qasper/scores.json \
  --output /eval/comparisons/qasper
```

全量SN导出须提供全部分区run，否则未提供的题保持missing，正式比较拒绝。子集导出显式用 `--case-ids '<id>' ...` 或 `--case-id-file /path/to/case-ids.txt`，不能把成功题自动当完整范围。重复case/混用配置拒绝，事前指定哪次尝试有效。已有公开答卷可通过 `import-predictions` 导入显式method及prediction JSON，调用 `--help` 查看参数。`import-external` 同样支持文件题单；完整 MultiHop 与 ALCE 原生文件的导入/评分命令见[最新外部结果入口](notebook-external-results-2026-09-28.md)，[146 题执行说明](notebook-multihop-subset-comparison.md)保留为历史子集。

外部方法的原始逐题答卷使用单独的审计入口，不能依赖行顺序猜测题目：

```bash
python scripts/benchmark_protocol.py import-external \
  --bundle /eval/bundles/qmsum-v3 \
  --source /eval/external/qmsum-method/raw \
  --method /eval/external/qmsum-method/method.json \
  --case-map /eval/external/qmsum-method/mapping.json \
  --case-ids 'qmsum:0:general:0' \
  --output /eval/submissions/qmsum-method-subset
```

`--source` 下必须只有一个 `predictions.json`、`raw_predictions.json` 或对应 JSONL 文件；`--case-map` 的 `mappings` 每行至少包含 `external_id` 和 `case_id`，可附带 `source_row` 与 `decision`。method JSON 的 `configuration.comparison_category` 必须明确写成 `recomputed-subset` 或 `controlled-rerun`，不能由导入器猜测。导入器会保存原始行、规范化答案、证据/引用/排序字段、源文件及映射文件 SHA256，并拒绝重复、未知、歧义或未映射源行。省略 `--case-ids` 表示完整 frozen bundle；声明子集表示该子集必须完整生成。带 `oracle`、`gold-input` 或 `gold-evidence` 条件的来源不能进入 ordinary paired comparison，应登记为单独校准或 published-reference。

QASPER新v3运行会自动保存最终引用证据快照，以上普通导出即可同时准备答案和证据。旧无快照运行只有显式加 `--qasper-evidence` 才尝试恢复，例如：

```bash
python scripts/benchmark_protocol.py export-sn \
  --bundle /eval/bundles/qasper-v3 --runs /eval/runs/old-qasper-partition-chunk \
  --qasper-evidence --output /eval/submissions/sn-qasper-recovered
```

旧run须保留 `product-artifacts/document-map.json`、只读runtime数据库、实际导入源文件及成功合成观测；恢复仅写新submission。缺失/歧义保留mapping error，不猜测或覆盖旧答案。若任一题映射失败，该声明范围不报部分Evidence F1，仍可评答案。新快照评分不需要live数据库；同批不得混入不同投影政策/实现/恢复方式。完整处理表见[标准文档§3.5](notebook-benchmark-standards-and-conformance.md#35-2026-09-24-已实现的最终引用投影与特殊情况)。

QMSum评分加 `--rouge-home /deps/ROUGE-1.5.5`，必要时设置 `PERL5LIB`。Perl源码、数据、本地库内容及版本进入身份，路径不同不影响比较；依赖放隔离目录，不写共享.venv。本机完整校准重跑：`python var/qmsum-official-calibration/reproduce_calibration.py --output-dir <fresh-dir>`。

ALCE默认只算无模型字符串指标，完整批评分显式添加：

```bash
python scripts/benchmark_protocol.py score \
  --bundle /eval/bundles/alce-asqa-v3 --submission /eval/submissions/alce/submission.json \
  --sources /eval/scorers --output /eval/scores/alce-full \
  --alce-full --alce-python /deps/alce/bin/python \
  --alce-hf-cache /deps/hf/hub --alce-nltk-data /deps/nltk_data --alce-timeout 3600
```

缺完整 shown-doc 映射的公开答卷可将 `--alce-full` 换成互斥的 `--alce-answer-only`。该模式不算引用分；ASQA 不需 AutoAIS，ELI5 claims-NLI 仍需它。模式进入评分身份，两侧必须一致；实际模型评分尚未执行。

完整模式预先准备AutoAIS `google/t5_xxl_true_nli_mixture`；ASQA另需 `gaotianyu1350/roberta-large-squad` 与 `gpt2-large`；ELI5另需 `gpt2-large`。环境需要作者脚本要求的torch、transformers、numpy、nltk、rouge-score、mauve、tqdm及模型依赖。保存resolved commit、模型文件哈希、包版本、NLTK资源与随机种子；强制离线，不自动下载权重。NLTK仅搜索显式指定的目录，并在评分前实际分句预检，禁止使用未记录的用户/系统资源。模型缺失或CLI失败不会生成完整成绩。

## 结果解释

比较器重建bundle、submission和官方输入，核对评分身份、范围、逐题状态与每项指标实际参与的case IDs。相同分母数量但题目不同仍拒绝比较；ALCE空答题的引用分母按官方规则单列。missing/error阻止正式比较；clarification/no_answer仍保留状态，按官方空答卷规则处理。旧SN诊断null和官方分母下的零分分别保存，不互相覆盖。

只比较共同指标，披露pending、独有指标、模型、输入策略及预算。批量总分直接读取官方结果；有逐题分数时给配对差与论文/会议group。没有逐题值的官方批量指标必须写入 `batch_only_metrics`，只能保留批量差异。比较器按逐题差异的自然group做固定种子的、case 等权的 group_id cluster bootstrap 95% CI，并披露 group 数和有效 case 数；group 不足时明确标记 unavailable。它不执行显著性检验、不生成跨benchmark总分或 SOTA 排名，不能把同会议各题视为独立样本。

比较报告汇总实测延迟及覆盖题数，SN provider日志的调用数/已返回token按兼容观测口径分组汇总，原始usage仍保留。reference缺token观测时明确unavailable；没有价格/币种则不估算货币成本。不同计时边界不能作单一检索器速度因果结论。MultiHop chunk run 还保存 native selection snapshot，可在官方 scorer 环境中独立重放检索指标；reasoning run 没有这项排名契约。Dashboard仍读原Notebook run；新增submission/scores/comparison单独输出JSON/Markdown，尚未接入Dashboard。

QASPER LAB LongChat citation 与 HotpotQA KG2RAG 的独立运行入口分别为 `scripts/run_lab_qasper.py`、`scripts/run_kg2rag.py`。两者均支持 `--prepare-only`，已完成各自全量公共输入验证；实际权重/tokenizer/模型执行未验收。命令与所有原方法差异见[外部结果](notebook-external-results-2026-09-28.md)。

## 外部方法候选

机器可读的登记表见 [`notebook-external-method-registry-v1.json`](../configs/notebook-external-method-registry-v1.json)。它记录候选条件与已执行结果；候选条目本身不是成绩。问题/标签/scorer 对齐可以支持公开答卷的描述性配对；生成条件待核实的字段仍禁止同条件排名或算法归因。QMSum HMNet 的排除依据见[来源审计](notebook-external-method-audit-2026-09-26.md)，新增完整答卷与实际报告见[2026-09-28 结果](notebook-external-results-2026-09-28.md)。

| 套件 | 优先候选 | 比较路径与边界 |
| --- | --- | --- |
| QASPER | [作者LED](https://github.com/allenai/qasper-led-baseline)，BM25/full-context控制组 | 获取同split逐题输出或重跑检查点后重评；摘要/caption输入差异须单列，本地控制组不是LED复现 |
| MultiHop | [作者检索+QA](https://github.com/yixuantt/MultiHop-RAG)，[Multi-Meta-RAG](https://github.com/mxpoliakov/Multi-Meta-RAG) | 固定提交 GPT-4/PaLM 各 2,556 题已完整导入和配对；官方 QA 分别 0.606025/0.607590，共享排名的 Hits@10=0.904213（2,255 题）。作者完整索引身份仍未证明；SN 答卷尚未生成 |
| ALCE | [官方VANILLA/RERANK](https://github.com/princeton-nlp/ALCE)，[Self-RAG ASQA](https://github.com/AkariAsai/self-rag) | 官方 human_eval 的 ASQA/ELI5 各四配置、各100题已导入；ASQA 文本指标已配对。完整生成条件和显示文档列表缺失；仅答案模型评分入口已实现，真实模型执行与引用指标 pending |
| QMSum | [Socratic SegEnc](https://github.com/salesforce/socratic-pretraining)、[SegEnc](https://github.com/salesforce/query-focused-sum)、[SummN](https://github.com/psunlpgroup/Summ-N) | Socratic 281 题已按公开代码顺序映射和 Perl 重评，原运行清单不可得；SegEnc 原链接 403；SummN 279 行未映射；HMNet gold-input 仅校准 |
| HotpotQA | [官方 baseline / reader](https://github.com/hotpotqa/hotpot)、[论文](https://aclanthology.org/D18-1259/)，BM25/full-context 控制组；future fullwiki retriever 单列 | distractor validation 可在固定 context 上受控重跑；论文 leaderboard/test 结果为 `published-reference`；fullwiki 不与本轨配对。明确 distractor/fullwiki、validation/test、句子级 supporting-fact 预测；Answer 与 Supporting Fact/Joint 分开，SN 投影已离线校准，服务器真实回放待验收 |

报告类别使用“发表参考值”（`published-reference`）、“公开答卷重评分”（机器标签 `recomputed-subset`）和“受控重跑”（`controlled-rerun`）。公开答卷重评分可以覆盖 full 或 subset，必须另列实际范围和分母。论文或代码链接不能代替实际执行结果；参考值需注明模型/检查点、split/setting、输入条件、scorer/版本与分母，缺项明确标未知。

下一步在服务器按固定官方文件准备运行环境、补齐ALCE评分模型，并扩大事前冻结的SN chunk/reasoning/BM25/full-context实验及核对外部答卷。MultiHop/ALCE下载阻塞已解除，本次真实数据证据见[验收记录](notebook-benchmark-real-data-validation.md)。五套正式全量和外部方法结论尚未产出；不要求用户找回旧run或代替开发者选择技术实现。
