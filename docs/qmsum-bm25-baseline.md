# QMSum BM25 生成对照：设计与实现顺序

文档状态：当前对照说明。BM25 是独立系统基线，不能把差值直接归因于检索变量。


用户授权先实现 QMSum 的 BM25 + 显式生成模型对照。只在本地验证代码与合成数据，不运行产品或模型、不下载数据。复用冻结 QMSum bundle、scorer 和报告，使用独立 `sn-notebook-baseline-v1` 执行身份，`mode=bm25` 与 SN 两种模式区分。

## 设计

1. 从已校验 bundle 的原始 `meeting_transcripts` 读取整场会议，以 turn 为检索单位，保留说话人/顺序/空 turn；不会读取 query 的标准相关片段来选资料。检索 tokenize speaker+content，casefold 后按 Unicode word 分词，不把人工添加的 turn 编号用于检索。BM25 使用正 IDF `log(1+(N-df+0.5)/(df+0.5))`、k1=1.5、b=.75，无 stemming/停用词过滤；同分按原 turn ID。这是项目固定公式，不宣称复用旧 rank_bm25 的所有默认行为。
2. 默认 top-k=8、上下文字符预算=12000。先取排名前 k，再按排名逐个加入能完整放入预算的 turn，超长项跳过并记录，不截断或用标准证据补齐。最终按原会议顺序组织。预算含 turn/speaker 标记与分隔符，不含问题、系统/JSON schema 提示，也不是模型 token 上限。无匹配词时同分按原顺序；无可用 turn 时仍发空证据并明确不得编造，留存诊断。
3. 固定任务提示与 SN QMSum 用户侧任务要求语义一致，额外要求 JSON answer 以复用显式模型适配器；SN 内部提示与 baseline 提示不同，不宣称仅检索变量发生变化。显式 tested 配置需由服务器对齐 SN 最终回答角色的实际模型与采样设置，程序不因名称相同就自动证明同模型。不同配置可以作完整系统比较，不作检索因果归因。
4. 生成前保存 frozen input、源码/产品/依赖身份、计划和 manifest；逐次模型事件、每题输出和每项分数持久化。输出先保存再评分；技术异常与空回答保持独立状态，分数为 null。每次新目录，无隐式续跑。
5. 复用 QMSum 主指标与上下文诊断，不计划 SN 数据库引用存在率。仅生成的 answer 字段进入 scorer；保存原 JSON 返回与完整 prompt。只删除 [kN] 的历史正文 scorer 规则保持一致。
6. 只读加载验证原数据重建、分区、基线配置、计划及输出 prompt/context。Dashboard 显示 BM25 和 SN 配置族及原始证据；旧模式配对保持严格校验。跨系统配对通过独立报告核对同数据/题目/scorer，不伪造 SN pairing_id。

## 实施顺序

- [x] 检索与 prompt：手算分数、同分、空资料、完整 turn 字符预算、gold 变更不影响 prompt。
- [x] 执行：命令级合成数据验证真实模型适配器协议；运行/配置隔离；计划先于调用；中断保留既有输出。
- [x] 报告：完整冻结输入验证、跨会议汇总但不同预算隔离、基线详情和 Markdown 覆盖；同题对照及限制。
- [x] 回归与交接：禁止网络的 Python 回归、JS 回归、命令帮助、代码审阅；更新状态与使用说明。

## 服务器执行与报告

复用 SN QMSum 实验用的**同一份 bundle**，不重新选择问题或改写空发言；每个分区都对应一场完整会议。新增 baseline 不要求重跑正在进行的 SN 实验。`--project-root` 提供只读的 SN 源码与模型客户端；源码快照要求其已跟踪文件与 HEAD 一致。执行应使用独立 CLI 进程，运行目录不能与生产、源码、输入、模型配置重叠。SN `.env` 和模型服务 registry 不作为 baseline 的默认模型配置读取；缓存和日志使用本次私有 runtime。

1. 从 `configs/public-starter-models.example.json` 复制生成配置，只需 `tested`。填写 SN **最终答案生成角色**的实际模型 ID、endpoint/key 环境变量名，以及 temperature、top_p、max_tokens、timeout、max_retries（如适用再指定 thinking_mode）。不要直接把配置样例中的占位值用于实验。显式模型身份保存模型名、参数、endpoint 哈希，不保存密钥或明文地址到 manifest/Dashboard。
2. 在服务器上先验收一场会议：

```bash
python scripts/run_notebook_baseline.py \
  --bundle /eval/bundles/qmsum \
  --partition-id '<同一份 partitions.jsonl 中的 ID>' \
  --project-root /path/to/silicon-notebook/project \
  --model-config /eval/configs/qmsum-baseline-models.json \
  --run-dir /eval/runs/qmsum-bm25-meeting-001 \
  --top-k 8 --max-context-chars 12000
```

这条命令会实际请求模型。检查检索 turn、prompt、模型返回、评分状态与覆盖率；`finished_with_errors` 通常需要查看 scores/model-events，而不是当作已完成有效实验。ROUGE 要求本地已有 `rouge-score==0.1.2`，缺包记 error，不自动安装；上下文诊断可独立完成。空回答记 `no_answer`，生成错误记 `error`；这些回答的分数为 null。意外中断保留计划、已写回答和已写单项分数。当前无断点续跑，新尝试必须新目录，比较时每分区选择一次运行。

3. 对其余会议使用相同参数、独立运行目录。不要在查看 test 分数后挑选最优 top-k 并仍称它为事前固定对照；预算调整应记录成新的配置族。general 查询通常需要更广泛资料，top-k=8 只是初始方法配置，并不保证强基线或性能下界。
4. Dashboard 可同时加载多个方法：

```bash
python scripts/build_experiment_dashboard.py \
  /eval/runs/qmsum-bm25-meeting-001 /eval/runs/qmsum-chunk-meeting-001 \
  --output /eval/reports/qmsum-dashboard
```

5. 独立同题比较（只读，不调用模型、不重算分数）：

```bash
python scripts/compare_notebook_baseline.py \
  --baseline-runs /eval/runs/qmsum-bm25-meeting-001 \
  --sn-runs /eval/runs/qmsum-chunk-meeting-001 \
  --output /eval/reports/qmsum-bm25-vs-chunk
```

两组参数都接受多个分区目录；每组内部必须同模式、同方法预算、同模型/运行配置和代码身份。比较 reasoning 时另建报告，不把两种 SN 模式混成一组。报告输出不能放在任一输入 run 内。

比较核验完整冻结 source/cases/partition 身份、QMSum scorer ID、`notebook_scoring.py`/`notebook_data.py`/`protocol.py` 源码哈希及 rouge-score/nltk 版本；不要求不同系统整体源码身份完全相等。若已跑 SN 使用了不同 scorer 实现或数据修订，拒绝自动配对，不回写历史分数，也不要求为了更新报告重跑模型。应先核对差异，再另行安排必要的只读重评分方案。

如果只是评分依赖缺失、指标补充或评分规则修订，使用 [`rescore_notebook_run.py`](../scripts/rescore_notebook_run.py) 对已有答卷建立新的评分批次，不重新执行 BM25 或生成模型；完整规则见[Notebook 独立重评分](notebook-rescoring.md)。

## 如何读结果

| 产物 | 内容与用途 |
| --- | --- |
| `input/`、`product-bundle.json` | 原数据及冻结题单/会议资料；评分 gold 只供离线评分，不进入生成 prompt |
| `manifest.json`、`planned.jsonl` | 调用前固定的所有问题/指标分母、BM25 方法、预算、模型及源码身份 |
| `outputs.jsonl` | 每题 `prediction`、完整 prompt、排名前 k 的分数/排名、预算排除 turn、最终选中 turn、文档映射、生成状态与耗时 |
| `model-events.jsonl` | 真实适配器的 started/completed/failed、请求 schema/参数、JSON 返回；观测边界是 chat_json，不能当作全部底层网络重试或完整 token 账单 |
| `scores.jsonl` | ROUGE-1/2/L F1；specific 查询再加相关非空发言覆盖；旧 bundle 沿用其 v1 诊断，不能混合版本；无 SN 引用对象存在率 |
| `comparison.json` | 逐题双方状态/缺失原因/分数/差值，双方完整非密钥配置，覆盖率、单侧题与运行警告 |
| `comparison.md` | 按 general/specific 和单个指标报告计划数、评分覆盖、共同有效题上的两侧均值与 SN−BM25 差值 |

主指标的输入是生成 JSON 的 `answer`，统一使用现有 QMSum scorer：仅去除 `[kN]`，ROUGE 使用固定 rouge-score 实现及 stemming。上下文覆盖使用真正提供给生成模型的整段发言文本与文档映射，不以检索到文档就视作读完整场会议。重复文本仍不能证明唯一 turn 位置，沿用已有诊断边界。

共同有效题均值差不包含单侧失败项；因此必须同时看覆盖率和两侧计划数。若无共同有效题，差值为 null。报告始终标记 `model_alignment=not_verified`，由实验负责人核对两侧模型配置，不能仅凭分数把差异归因于 SN 检索或 reasoning。当前不提供总体跨指标总分、显著性结论、自动发布门禁或论文榜单排名。

## 本地验证范围

使用合成会议、真实 bundle 准备/验证逻辑和真实 ExplicitBenchmarkModel 适配器，模型传输替换为测试对象。覆盖手算 BM25、gold 不进入 prompt、预算/空上下文、数据篡改、输出与单项评分的中断落盘、Dashboard 汇总、跨系统同题比较与目录隔离。本机无 rouge-score，验证其缺依赖分支；比较分数测试使用显式合成数值，不能当作真实 ROUGE 验收。真实数据/模型实验仍由服务器执行。

验证记录：2026-09-18，362 项 Python 测试通过（新增 29 项），阻断网络后的请求尝试 0；18 项 JavaScript 测试通过；CLI 帮助、diff 检查与独立审阅完成。
