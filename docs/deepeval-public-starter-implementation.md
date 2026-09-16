# 公开评测起步：代码交接

**2026-09-16 更新：IFEval 已取消人工正反例审计前置条件，N/R 直接调用 DeepEval 4.2.2 verifier。冻结数据中的 pending/audited 字段仅作来源归档，不阻止问答或评分。新 scorer 与历史记录分开，详见 [IFEval 直接评分与服务器使用说明](ifeval-direct-scoring.md)。下文早期阶段记录中的审计要求已被此决定取代。**

2026-09-10：已写入 P0/P1 准备代码，**未执行、未测试、未验收**。用户本轮明确要求先专注代码和逻辑。没有下载或冻结新数据，没有创建模型客户端、产品 notebook/runtime，没有新成绩。

后续已按用户指示继续编写 N/R 执行编排和离线报告入口，见[执行与报告说明](deepeval-public-starter-orchestration.md)。下文保留基础模块协议；最新入口已串起调用关系，但仍未运行或测试。

对应[起步方案](deepeval-public-starter-plan.md)和[本轮实施计划](superpowers/plans/2026-09-10-public-starter-implementation.md)。新增实现独立于历史 `run_public_benchmark.py`，不改变 baseline、smoke 或 weekly 入口。

## 代码分工

| 文件 | 已写逻辑 | 当前边界 |
|---|---|---|
| `src/rag_eval/starter_protocol.py` | 五套 suite 定义；保留原题和标注；按原始顺序选题；重复 ID/偏移检查；冻结包读取和重建对账 | 只用本地 JSONL，不下载；不 import DeepEval |
| `scripts/prepare_public_starter.py` | 来源声明与导出哈希检查；冻结 raw/cases/manifest；SDK 源码副本；可读题卡与 IFEval 待审计清单 | 单次准备一个 suite；拒绝已有输出目录；尚未执行 |
| `src/rag_eval/starter_native.py` | 使用 4.2.2 原生模板和 schema 构造 N 请求；原生 scorer；IFEval 直接调用 SDK verifier | 延迟导入 SDK；不调用自动下载的 loader；SQuAD 评分接口属于在线操作 |
| `src/rag_eval/starter_model.py` | `ExplicitBenchmarkModel` 接收显式客户端、角色、模型身份、配置哈希；请求/响应/错误留痕；schema 校验 | 不负责创建客户端；未配置不得回落默认供应商；未做真实调用 |
| `src/rag_eval/starter_product.py` | 人审后转换 SQuAD/DROP/BoolQ 产品题；最多 40 段固定候选库；BoolQ 结论解析；隔离路径描述 | 不创建产品对象；DROP 需要完整官方标注；来源、人审不可由代码代填 |
| `src/rag_eval/starter_results.py` | 独占创建的事件日志；计划/结果身份；分组均分、覆盖率、缺失和失败数量 | 单进程、支持线程写日志；无自动续跑，不提供跨套件总分 |

基础阶段未增加 runner；后续编排阶段已新增独立的执行与报告脚本，仍没有调度任务或新测试文件。不能据此认定 P0/P1 已通过验收。

## 本地数据输入

准备入口接收 `--suite`、`--raw-jsonl`、`--source`、`--output` 四个必填参数。`--output` 必须是新目录，建议在 `var/public-starter/` 下；失败后目录没有完整 manifest 就视为未完成，应换新目录重新准备。本文仅说明接口，本轮没有执行该脚本。

JSONL 必须保留整个原始 split 的顺序，一行一条原始对象，不允许空行。SQuAD/DROP/BoolQ/IFEval 使用 SDK 所期待的公开数据字段，LogiQA 使用 SDK URL 指向的逐行 JSON。不得把历史的 `questions.jsonl` 当作原始公开数据导出。

| suite | 原始字段 | 选择逻辑 |
|---|---|---|
| squad | `id/title/context/question/answers.text/answers.answer_start` | Normans、Steam_engine 各前 10 题 |
| drop | `query_id/section_id/passage/question/answers_spans.spans/types` | 原始顺序中第一个有至少 10 题的 history/nfl section，各前 10 题 |
| boolq | `question/passage/answer`，answer 必须是 JSON boolean | 前 20 题；无原生 ID 时使用整条 raw row 的哈希 |
| logiqa | `text/question/options/answer/type`；answer 为 0..3 整数 | Necessary/Sufficient Conditional Reasoning 各前 10 题；保留任务归属重叠 |
| ifeval | `prompt/instruction_id_list/kwargs`，可带 `key` | 前 20 题，每个指令实例单列，不因同名 ID 而覆盖 |

少于目标题数时记录缺额，不补造数据。完全无匹配题也保留 0 题选择及原因，不产生分数。首轮是顺序前缀，不能宣称代表性随机样本。

来源 JSON 的必填字段：

| 字段 | 意义 |
|---|---|
| `dataset`、`split` | 必须与 `SUITES` 声明完全一致；例如 BoolQ 是 `boolq/default`、`validation` |
| `revision` | 固定上游 commit 或明确版本发布标识，禁止 main/master/latest |
| `source_url` | 实际下载源的公开地址，仅记录，不请求 |
| `license`、`license_url` | 人工核对后的许可说明与依据，不由脚本判断法律许可 |
| `origin_sha256` | 上游原文件字节哈希，由准备者提供；本地 JSONL 不能代证原文件 |
| `export_sha256` | 本地 JSONL 字节哈希，准备工具实际比对 |
| `conversion` | 从上游文件到当前逐题行的转换说明；无转换时说明字节相同 |
| `original_order_preserved` | 必须为 true，是准备者声明，不是脚本推断出的事实 |

manifest 明确写 `source_provenance_status=operator_declared; export hash checked`。原文件获取、许可核对及转换复核仍须后续完成，不能仅凭字段齐全就称“来源已验证”。LogiQA 的仓库名不能代替实际文件版本身份。

生成目录包含 `raw.jsonl`、`cases.jsonl`、`cards.md`、`instruction-audit.jsonl`、`sdk-source/` 与最后写入的 `manifest.json`。SDK 快照覆盖 benchmark、scorer 和提供归一化逻辑的 `utils.py`；不是完整 Python 环境锁。运行阶段仍需保存依赖版本、评测源码和模型配置快照。

## 原生模型参照 N

后续编排顺序是：`load_bundle` → `build_request` → 显式 tested adapter → 保存预测 → `score_prediction` → 保存结果。`build_request` 返回可序列化请求和 Pydantic schema；这一步不调用模型。SQuAD/BoolQ/LogiQA 使用 SDK 自带模板，DROP 在 `n_shots=0` 下不需要获取训练示例，IFEval 使用原 prompt。

这复用的是 SDK 的模板/schema/scorer 语义，并非直接调用 `benchmark.evaluate()`。目标是保存逐题身份、失败与评分分母，也避免 SDK loader 自动获取可变数据。后续离线验证必须比对这层接口与 SDK 单题路径的一致性，验证前不能称为已复现原生成绩。

调用方分别创建 `role="tested"` 和 `role="judge"` 的适配器；即使用同一物理模型，也要分别声明和留痕。模型 ID、解析后的配置哈希、明确的调用参数和事件 sink 均由调用方提供，没有 API key、URL 或默认供应商选项。调用前通过 `for_case(case_id, request_id)` 绑定身份；异步接口沿用该绑定。

适配器在模型调用前写 started 事件，完成后写客户端原返回及 schema 解析结果，失败写错误类型。schema 错误不会触发 SDK 的 TypeError 回退导致额外调用。客户端自身的重试仍可能产生多次请求，不能把事件条数直接算成 API 调用数。中断遗留 started 事件可查，不补写成功。

**观测边界是 `chat_json` 接口。** 产品客户端会增加要求 JSON 的 system prompt、可能覆盖服务参数；因此日志保存的是传给客户端的 prompt/messages/schema/参数，不冒称完整 HTTP 请求或最终有效参数。当前使用完整 JSON Schema 作为 schema hint，也属于适配器协议。后续 P2 必须绑定物理模型、客户端版本、有效配置以及必要的底层日志捕获，才能解释模型参照。配置哈希不替代这一过程。

SQuAD 必须传入 judge-role adapter 才能评分。DROP 使用原生字符串列表匹配并保留多 span 限制；BoolQ/LogiQA 使用原生精确匹配。所有二元 scorer 返回值须为 0 或 1，异常不填零。

IFEval 静态清单和 pending 字段保留为 v1 来源归档，当前不参与评分准入。`score_prediction` 直接调用固定 SDK verifier，按全部指令的布尔结果计算整题分数，并保存位置、参数和理由。旧 `audit_instruction` 接口已删除，不再要求准备或复核正反例；详细兼容约定见 [IFEval 直接评分](ifeval-direct-scoring.md)。

## 产品适配 R

`product_bundle` 要求单个数据集的 cases、独立的人审记录和固定顺序的同集干扰原文。人审记录形状为：`sample_id → {status, reason, reviewer, raw_row_sha256}`；approved/excluded 必须绑定原题哈希并写明人员与理由。pending 不产生可执行题。问题在脱离单独段落后是否明确，必须由人判断。

候选库使用原文字节去重：先保留所有入选题的 gold 原文，再按固定输入顺序补干扰，最多 40 段。标题是中性的文档哈希标识；上传用 documents 中的原文，不包含答案或 gold 标记。questions 中的 gold/reference 只供评测侧使用，Ask 只取 `question` 字符串。人审文件、干扰输入和最终库哈希都进入身份记录；同集来源声明仍须调用方从源 manifest 绑定。

SQuAD 复用现有位置检查和问题结构。DROP 在只有 SDK 的 `answers_spans` 扁平表示时明确排除 R 输入；需要把官方完整 `_annotations` 随带有转换说明的原始导出冻结后再适配，不能猜测各 span 是组成项还是替代标注。这不影响 DROP 的 N 路径，历史正式标注也没有被改写。

BoolQ 添加固定首行 `Final answer: Yes` 或 `Final answer: No` 要求，然后允许解释和引用。资料不足时允许产品直接说明不足，不强迫猜标签。`check_boolq` 保留完整答案；首行不匹配或全文同时出现 Yes/No 两种词时为 unparsed。这个规则有意保守，例如引述相反标签可能降低解析覆盖率，应人审；它不理解自然语言解释中的语义矛盾。

`isolation_plan` 只描述独立路径和禁用 Memory/画像/检索经验等配置，不操作环境变量或磁盘。后续新增的 `starter_runtime`/`starter_runner` 已写入 runtime 创建、配置覆盖、路径检查、native ID 映射和合成上下文捕获的连接逻辑；仍未执行，不能称“新运行隔离已验证”。

## 结果与分母

`planned_result` 必须在执行前建立预期结果身份。`protocol_id` 由调用方对来源/SDK/适配器/模型配置，以及 R 的候选库、人审和隔离配置计算；`run_id` 隔离不同运行。R 必须明确传入产品 scorer 名称，不能默认继承原生 scorer。

`result_record` 保存 scored/error/unparsed/not_applicable/unscored；尚无记录的题在汇总时计为 missing。未评分必须有 reason 且 score 为 null；已评分必须确有输出且分数为有限的 0..1 数。详细字段放在 details 中，包括请求、完整输出、模型/事件引用、上下文、引用和规则明细，不只保留一个分数。

`summarize` 按 run/protocol/suite/track/mode/scorer 分组，检查重复和计划外结果。每组展示计划数、不同问题数、输出数、评分数、缺失数、状态计数与有效评分均分。BoolQ 产品结论评分另给“已解析标签数 / 正常输出数”；同时保留“有效评分数 / 计划数”。所有未评分原因保持可见，零分只表示实际评分得零。

日志 `EventJournal` 仅支持本进程线程共享，文件以 0600 权限独占创建，不隐式恢复历史日志。未来若增加多进程或自动重试，需要另行设计合并、续跑与 attempt 身份，不能用同一 result_id 覆盖失败记录。

## 接下来的实施顺序

1. 用户允许验证后，先做本地契约与预存响应检查：来源哈希/偏移、选题/重叠/缺额、冻结目录、SDK 模板等价性、schema 成功/错误、结果分母、BoolQ 冲突与解析失败。
2. 获取并核对公开原文件和许可，执行冻结工具，完成问题适用性审阅；IFEval 准备实际 ID/参数对应的正反例，再决定适用子集。
3. N 编排器代码已写：显式客户端配置、请求和源码身份、逐题预测与单独评分、错误日志及离线报告；SQuAD judge 显式配置。后续验证这些连接，不接 timer。
4. R 编排代码已写：复用隔离导入/Ask/捕获，接入 BoolQ 结论检查、既有 GEval/Faithfulness 和确定性文档/引用对象检查。后续验证隔离与指标输入；DROP 可靠数值/日期提取器尚未新增。
5. 用户明确恢复在线工作后才做 P2/P3 小样本；随后人审解释结果。没有人工校准之前不设置质量阈值，不合并 N/R 总分。

交互可靠性、资料更新一致性、业务 Memory/多轮/偏好、KG、重排、PDF/OCR 均未扩展。当前交付只说明代码已写，所有运行和测试结论保持空白。
