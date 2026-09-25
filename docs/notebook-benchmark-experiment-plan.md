# Notebook Benchmark：正确评测与比较

更新：2026-09-24。当前开发分支为 `feat/benchmark-protocol-correctness`，起点 `e022c60`。本页是新实验入口；[先前计划](archive/2026-09/notebook-benchmark-experiment-plan-pre-v3.md)保留历史背景。用户已授权必要修改与重跑，历史服务器结果不再是前置条件。正式模型实验在服务器执行；本机后续单独获授权下载MultiHop/ALCE并验收数据与评分实现。生产部署、定时任务和远程发布不在本轮范围。

## 目标与流程

在预先固定的数据、输入权限、回答要求和评分规则下，运行 Silicon Notebook（SN）及对照方法，保存可检查的答卷，得到可解释的比较。完整系统允许模型不同，但必须披露；模型、提示、预算未控制时不能归因于单一检索器。

核实原始数据 → 冻结 v3 bundle → 无 gold 请求 → 保存答卷及失败状态 → 官方评分 → 校验比较身份 → 核验案例与统计不确定性 → 扩大实验。小样本先检查链路；正式比较固定全题或预先声明的子集，不按测试分数挑题、重试或调参，不生成跨套件总分。

## 四套具体协议

规则的逐项依据、精确公式、实现入口和符合程度见[Benchmark 标准与实现符合性](notebook-benchmark-standards-and-conformance.md)。本页提供执行摘要与命令；官方评分一致性、原论文复现和受控比较分别验收。

| 套件 | 新实验输入 | 官方评分口径及限制 |
| --- | --- | --- |
| QASPER | v0.3全题；每篇标题、摘要、章节正文及公开caption；FLOAT/unmapped只记录，不删题 | 固定作者evaluator，Answer F1多参考最大值；text_evidence_only只过滤gold的FLOAT证据项；Evidence F1须方法明确预测原始段落，不能用上下文覆盖冒充 |
| MultiHop-RAG | queries与完整corpus；保留标题、URL、来源、日期、作者、类别及正文 | 固定作者QA弱词匹配；真实BM25排名可评作者Hits/MAP/MRR，null不进检索分母；SN无同义排名输出，保持pending |
| ALCE | ASQA/QAMPARI/ELI5分开；ordinary候选全集保留顺序，实际模型选用资料另记 | 固定CLI首行截断、去结束标记、每单元最多3引用；真实citation映射；模型指标显式批评分，MAUVE不可逐题平均 |
| QMSum | 全会议、全查询、speaker与原turn，不注入gold span | 作者确认Perl ROUGE-1.5.5，参数 `-c 95 -r 1000 -n 2 -m -a`；明确使用HMNet regex分句；保留批量Average_F |

QASPER作者reader对FLOAT只统计、不实际删题，其full_text轨道仅章节标题与段落；本项目加入摘要/caption是公开输入变体，不宣称复现LED论文表。QMSum原论文未完整规定分句与文件编号，也不宣称完全复现历史数字。出处见[官方资料手册](notebook-benchmark-official-resources.md)。

## 实际验证与未完成项

2026-09-24最新代码回归为697项Python测试全部通过、无跳过；此前Dashboard JavaScript为34项通过，本次无前端改动未重复运行。具体命令、审查修复见[协议实施记录](superpowers/plans/2026-09-23-benchmark-protocol-correctness.md)与[QASPER证据接入记录](superpowers/plans/2026-09-24-qasper-sn-evidence.md)。测试通过不替代下表的真实数据、模型和外部方法验收。

| 验证 | 实际结果 | 结论范围 |
| --- | --- | --- |
| QASPER完整适配 | 416篇、1451题、0排除；全题gold变更不影响公共资料/请求；1451份答案、类型、证据与官方raw parser一致 | 验证数据分母、公共输入、gold隔离及参考答案 |
| QMSum完整适配 | 35场、281题、20718 turns（17空turn）、0排除；全题gold变更不影响请求 | 当前文件完整性；不是论文279题 |
| QMSum HMNet公开答卷校准 | 同序同分句时，当前SPL与独立pyrouge SEE均36.464/11.374/31.558 | 两种封装调用同一Perl一致；未严格复现README 36.51/11.41/31.60 |
| QASPER一题新实验 | SN chunk与BM25真实生成、答卷导出、官方评分、比较CLI成功 | 链路smoke，不支持方法排名 |
| QASPER最终引用证据 | 已保存一题只读恢复 `4:0`，原CLI与框架Evidence F1=1、Answer F1=2/3；416篇11,065块/81,316截短验收通过，8个人工评分反例校准 | 新快照与旧显式导出可用；无新生成，不代表全量或所有reasoning路径 |
| QMSum一题新实验 | SN chunk与BM25真实生成、Perl评分、比较CLI成功；SN原Python ROUGE诊断缺包error，未覆盖此错误 | 验证答卷独立于旧诊断保存，可单独重评分；不是正式全量实验 |
| MultiHop完整适配与评分 | 固定官方下载校验成功；2556题/609文章/6084证据，0排除；独立BM25排名、全题正负QA校准和官方完整检索/QA CLI对齐 | 真实完整数据、输入隔离与评分桥接验收；无新SN生成 |
| ALCE完整数据与文本评分 | 包SHA256匹配；ASQA948、QAMPARI1000、ELI51000；5个普通候选文件完整适配，原始CLI文本指标与预处理对齐 | 实际候选数/重复/空别名保留；具体输入隔离及oracle核验见[验收记录](notebook-benchmark-real-data-validation.md) |
| ALCE模型指标 | 真实NLTK和官方AutoAIS控制流的参与题目对齐；尚未运行真实模型批评分 | 无AutoAIS/QA/MAUVE真实成绩；控制流探针不提供语义分数 |
| 外部方法正式对照 | 已建立候选与重评分入口，完整答卷获取及复现尚未完成 | 不宣称已经比较SOTA |

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
```

SN一次一个资料分区，遍历全部分区形成全量；两类生成CLI均可重复 `--case-id` 声明小样本，完整资料不变。reference默认全bundle。`full-context`预算不足记录错误并拒绝推理，不静默截断；ALCE的`candidate-topk`是控制组，不能称官方VANILLA提示复现。BM25保留实际排名、上下文及预算排除。

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

全量SN导出须提供全部分区run，否则未提供的题保持missing，正式比较拒绝。子集导出显式用 `--case-ids '<id>' ...`，不能把成功题自动当完整范围。重复case/混用配置拒绝，事前指定哪次尝试有效。已有公开答卷可通过 `import-predictions` 导入显式method及prediction JSON，调用 `--help` 查看参数。

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

预先准备AutoAIS `google/t5_xxl_true_nli_mixture`；ASQA另需 `gaotianyu1350/roberta-large-squad` 与 `gpt2-large`；ELI5另需 `gpt2-large`。环境需要作者脚本要求的torch、transformers、numpy、nltk、rouge-score、mauve、tqdm及模型依赖。保存resolved commit、模型文件哈希、包版本、NLTK资源与随机种子；强制离线，不自动下载权重。NLTK仅搜索显式指定的目录，并在评分前实际分句预检，禁止使用未记录的用户/系统资源。模型缺失或CLI失败不会生成完整成绩。

## 结果解释

比较器重建bundle、submission和官方输入，核对评分身份、范围、逐题状态与每项指标实际参与的case IDs。相同分母数量但题目不同仍拒绝比较；ALCE空答题的引用分母按官方规则单列。missing/error阻止正式比较；clarification/no_answer仍保留状态，按官方空答卷规则处理。旧SN诊断null和官方分母下的零分分别保存，不互相覆盖。

只比较共同指标，披露pending、独有指标、模型、输入策略及预算。批量总分直接读取官方结果；有逐题分数时给配对差与论文/会议group。当前不自动进行置信区间、显著性检验或排名；正式统计分析应按论文/会议聚类，不能把同会议各题视为独立样本。

比较报告汇总实测延迟及覆盖题数，SN provider日志的调用数/已返回token按兼容观测口径分组汇总，原始usage仍保留。reference缺token观测时明确unavailable；没有价格/币种则不估算货币成本。不同计时边界不能作单一检索器速度因果结论。Dashboard仍读原Notebook run；新增submission/scores/comparison单独输出JSON/Markdown，尚未接入Dashboard。

## 外部方法候选

| 套件 | 优先候选 | 比较路径与边界 |
| --- | --- | --- |
| QASPER | [作者LED](https://github.com/allenai/qasper-led-baseline)，BM25/full-context控制组 | 获取同split逐题输出或重跑检查点后重评；摘要/caption输入差异须单列，本地控制组不是LED复现 |
| MultiHop | [作者检索+QA](https://github.com/yixuantt/MultiHop-RAG)，[Multi-Meta-RAG](https://github.com/mxpoliakov/Multi-Meta-RAG) | 固定历史版本公开输出，核query/gold/范围与非oracle条件；公开GPT4/Voyage文件下载未完成，不以部分输出算成绩 |
| ALCE | [官方VANILLA/RERANK](https://github.com/princeton-nlp/ALCE)，[Self-RAG ASQA](https://github.com/AkariAsai/self-rag) | 固定retriever、ordinary候选、prompt、ndoc、模型；RERANK多候选和AutoAIS选择成本必须计入 |
| QMSum | [SegEnc](https://github.com/salesforce/query-focused-sum)，[SummN](https://github.com/psunlpgroup/Summ-N) | 优先非gold span完整会议方法；检查点/答卷尚需对齐；SegEnc仓库已归档；HMNet gold-input仅评分校准 |

标签：`published-reference`为条件不同的论文值；`recomputed-subset`为题目对齐后的公开答卷重评分；`controlled-rerun`为本协议重新运行。论文或代码链接不能代替实际执行结果。

下一步在服务器按固定官方文件准备运行环境、补齐ALCE评分模型，并扩大事前冻结的SN chunk/reasoning/BM25/full-context实验及核对外部答卷。MultiHop/ALCE下载阻塞已解除，本次真实数据证据见[验收记录](notebook-benchmark-real-data-validation.md)。四套正式全量和外部方法结论尚未产出；不要求用户找回旧run或代替开发者选择技术实现。
