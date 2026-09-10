# 公开起步方案：执行与报告入口

2026-09-10：本轮已编写执行编排和报告入口，**没有运行新代码、没有测试、没有新评测成绩**。这些命令说明供后续使用，当前在线暂停要求仍有效。

本轮续接[首批实现](deepeval-public-starter-implementation.md)，对应[编排实施计划](superpowers/plans/2026-09-10-public-starter-orchestration.md)。不改生产项目和历史 baseline/smoke 入口，不恢复 timer。

## 本次串起的流程

模型参照 N：本地冻结题 → SDK 模板/schema → 显式被测模型 → 逐题保存预测 → 显式 judge 或确定性 scorer → 独立报告。

产品路径 R：本地冻结题与人审记录 → 固定同集资料库 → 独立 runtime 导入/切块/索引 → 原生 Ask → 保存答案/实际上下文/引用对象 → 产品评分 → 独立报告。

每次执行只处理一个 suite 和一条路径；R 还必须明确 chunk 或 reasoning。多套件或两种模式分别启动独立进程，之后用报告入口组合展示。这样产品导入时使用的进程级环境和捕获钩子不会在同进程中混用。

| 新文件 | 职责 |
|---|---|
| `src/rag_eval/starter_runtime.py` | 显式模型配置、环境隔离、产品服务配置私有副本、源码和依赖身份、模型客户端构造 |
| `src/rag_eval/starter_runner.py` | 输入与人审绑定、计划账本、N/R 预测、先保存后评分、阶段状态与失败记录 |
| `src/rag_eval/starter_report.py` | 只读已存结果、核对身份与分母、Markdown/JSON 报告、逐题资料、两模式配对 |
| `scripts/run_public_starter.py` | 单套件执行入口；结束或失败后尝试生成本轮报告 |
| `scripts/report_public_starter.py` | 一个或多个运行目录的离线报告入口 |
| `configs/public-starter-models.example.json` | 被测模型和 judge 的显式配置格式；环境变量引用，无真实凭据 |

## 后续使用接口（本轮未执行）

先按[数据准备协议](deepeval-public-starter-implementation.md)产生一个 suite 的冻结目录。新的 runner 不读取历史 baseline 数据格式，也不下载缺少的数据。

N 的命令形状如下。它会调用被测模型；SQuAD 还会调用显式 judge：

```bash
.venv/bin/python scripts/run_public_starter.py \
  --bundle var/public-starter/boolq-frozen-v1 \
  --run-dir var/public-starter/boolq-native-v1 \
  --track N \
  --models /absolute/path/to/starter-models.json
```

R 的命令形状如下。它会调用产品导入所需的模型、Ask 和语义 judge：

```bash
.venv/bin/python scripts/run_public_starter.py \
  --bundle var/public-starter/boolq-frozen-v1 \
  --run-dir var/public-starter/boolq-chunk-v1 \
  --track R --mode chunk \
  --models /absolute/path/to/starter-models.json \
  --reviews /absolute/path/to/boolq-suitability-reviews.json
```

reasoning 使用独立 run-dir 和 `--mode reasoning`。`--project-root` 默认指向评测仓库同级的 `project`，可显式指定。已有运行目录被拒绝，本轮不实现续跑、自动重评或批量调度。

IFEval N 可以提供 `--instruction-audits /absolute/path/to/audits.jsonl`。必须是此前 `audit_instruction` 产生、经人工理解其正反例的参数化记录；runner 会核对 verifier 哈希并重查正反例。任一指令缺少有效记录，该题不发模型请求，并记为不适用；不会使用未经审计的默认通过分支补分。

离线报告入口仅阅读保存产物，不创建模型或产品 runtime：

```bash
.venv/bin/python scripts/report_public_starter.py \
  var/public-starter/boolq-chunk-v1 \
  var/public-starter/boolq-reasoning-v1 \
  --output var/public-starter/boolq-comparison-report-v1
```

报告输出目录也须是新目录，包含 `report.md`、`summary.json` 与逐题 `cases/*.json`。自动生成的单轮报告位于运行目录下的 `report/`。

## 模型与隔离身份

N 必须提供 `tested` 角色，SQuAD N 还需 `judge`；R 的 Ask 使用产品已有模型服务配置，R 的语义评分必须另外提供 `judge`。配置示例中的 model ID 必须替换；各角色的 endpoint/key 从明确指定的环境变量读取，不从默认供应商补齐。

显式参数包括 temperature、top_p、max_tokens、max_retries、timeout，可选 thinking_mode。示例数字仅表示格式，不代表已核对模型兼容性或费用。timeout 是客户端请求超时，不是整轮评测的外层时限。

运行 manifest 保存模型 ID、endpoint 哈希、参数和配置身份，不保存 endpoint 或 key。模型适配沿用产品 `OpenAICompatibleClient`，需要产品后端依赖；N 不创建 SQLiteRepository 或 notebook。客户端会添加 JSON 输出 system prompt，已纳入适配器/产品源码身份；完整 HTTP 观测仍受原生日志长度与字段限制。

R 读取生产 `.env` 作为配置输入，独立覆盖数据库、存储、缓存和日志路径，禁用 shadow database、用户画像、检索经验与相关 Memory 注入。服务 TOML 复制到权限受限的 `run/runtime/` 并指向副本，防止后续生产文件热更新改变本轮配置。原文件不写入。实际运行时还检查解析后的路径/禁用标志和仓库数据库位置。

每个新运行复制冻结输入、记录实际 benchmark 源码文件哈希与副本、产品 Git archive、依赖版本、模型和运行设置哈希。包含秘密的产品服务配置仅放本地私有 runtime；报告不复制其明文。

## 实际评分安排

N 沿用首批接口：SQuAD 内置 judge、DROP 归一化字符串列表匹配、BoolQ/LogiQA 精确匹配、IFEval 已审计规则。它仍是小样本指定协议，不冒称全量官方榜单复现。

R 的评分计划每题四项：

| 检查 | 适用范围 | 限制 |
|---|---|---|
| 主要答案检查 | BoolQ 用明确首行标签；SQuAD/DROP 复用已有 GEval Answer Correctness | DROP 本轮使用 GEval 完整标注参照；未新增可靠数值/日期提取器 |
| Faithfulness | 存在可靠实际最终合成上下文 | 缺少上下文或分节合成不强行评分；不以 gold 原文补齐 |
| 最终证据文档覆盖 | 有 gold 文档及完整最终上下文 ID 映射 | 不报告 MRR/nDCG，不解释成候选排名质量 |
| 引用对象存在比例 | 至少有一个引用对象 | 零引用为不适用并保留缺失现象；不代表正文锚点或逐断言引用支持 |

GEval/Faithfulness 复用现有 `quality_metrics.py` 中的构造逻辑，只测量计划中的指标。SDK 的内部 success/threshold 不被用作发布门槛，没有新增质量阈值。

R 的人审文件与题目哈希绑定；同集干扰原文直接取自同一冻结 `raw.jsonl`，不再依赖任意外部字符串列表。approved 后才进入执行计划；pending/excluded/缺完整 DROP 标注的数量保留在筛选记录和报告中。没有任何可用题时拒绝启动，不把空运行当作完成。

## 失败、报告与比较

运行前写 `planned.jsonl`，每个预测写 `outputs.jsonl`，每项评分写 `scores.jsonl`。先完成并保存预测阶段，再进入评分阶段；一次 judge 错误不会抹掉回答，单项错误也不阻止记录后续评分。底层事件、usage 和原生产品 attempts 保留在各自文件。

`state.json` 保存 importing/predicting/asking/scoring 等当前阶段。正常结束为 finished 或 finished_with_errors；失败/中断记录错误类型、发生阶段与题目身份。硬终止可能来不及写终态，离线报告会标为“无终态，仍运行或已停止未知”，不凭最后更新时间判定超时。

报告分别显示筛选数量、计划预测、预测错误/不适用/缺失、已存输出、计划评分、有效分数及全部未评分状态。评分中断时，已保存但未评分的回答仍计入输出覆盖；BoolQ 另显示已完成标签解析数与已存输出数，不把未完成的比率称为最终解析成功率。

完整性检查拒绝重复/计划外输出、变更的计划哈希、身份不一致和非法分数。最后一行写到一半的 JSONL 可保留此前完整记录，但必须报告截断和缺额；中间损坏不能静默跳过。这里是结果结构核对，不能替代尚未进行的运行验收。

报告按五套公开任务列出 N、R chunk、R reasoning 覆盖，未提供的路径写未覆盖。不同运行分别列指标，不合并总分。chunk/reasoning 仅在 pairing_id 相同且各恰有一个运行时逐题并列；identity 包括原题、语料、人审、物理模型配置、源码、依赖和去除运行目录差异后的设置。N/R 从不配对相减。

自动报告中的“未结束模型调用”只统计显式适配器的事件，不等于产品内部所有 API 请求数；实际 token 记录同样受原生日志 coverage 限制，不自行补成本。

## 验证状态与后续

这轮只阅读代码和 Git 差异并修改文件，没有 import、编译、运行、pytest、mock 调用或在线评分。所有新入口都属于**待验证实现**，不能继承历史 47 项测试的通过记录。

下一步先按[实施计划](superpowers/plans/2026-09-10-public-starter-orchestration.md)验证显式模型配置/schema、产物身份、R 隔离、失败保留、报告分母与两模式配对；再冻结真实数据、完成人审和 IFEval 规则审计。只有用户重新要求在线评测后，才实际执行 N/R。交互可靠性、资料更新一致性、KG、重排、PDF/OCR 等范围不扩展。
