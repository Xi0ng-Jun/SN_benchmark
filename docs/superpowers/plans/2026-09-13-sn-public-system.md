# SN Public System Implementation Plan

> 最新选题更新：用户要求规模不设预先上限，只沉淀[十套公开评测任务与选题方案](../../public-benchmark-selection-plan.md)，测试与实验暂停。本文后续“小样本”安排是历史建议，已被按 task/split 完整选题、独立设计执行分库的方案替代；新选择器与分库代码尚未实施。

> 2026-09-14 进度补记：用户已授权离线回归，完成 205 项测试，无跳过或警告。代码 `63214c7` 已于上一阶段推送；本轮测试与文档改动未提交。见[回归记录](../../offline-regression-2026-09-14.md)。下文不测试/不提交为 2026-09-13 实施时的历史边界，在线任务仍未恢复。

> 使用 subagent-driven-development 分工实施并独立静态审查。用户已授权实施，优先级高于技能中的测试/提交步骤：本轮不执行测试或应用，不提交。

**Goal:** 将 LogiQA、GSM8K、BBH、MMLU、TruthfulQA MC1、HellaSwag、IFEval 接到 SN Ask，保留 Native 参照。

**Architecture:** 新 `system_product.py` 构造白名单资料和问题；`system_scoring.py` 做产品答案提取和冻结 SDK 评分；runner 选择独立 `sn-public-system-v1`；`system_runtime.py` 复用隔离导入与采集，执行原生 intent/Ask。冻结数据协议不改。

**Tech Stack:** Python、JSON/JSONL、现有 rag_eval、DeepEval 4.2.2（仅源码阅读）。

**Spec:** `docs/sn-public-system-adaptation.md`

## Global Constraints

- 只在当前隔离工作树修改；不运行测试、导入 SDK、执行应用、下载数据、调用模型或修改生产。
- 回归用例先写，但不宣称红绿验证；已有未提交改动保留。
- 不修改冻结的 Native suite metadata；当前系统能力从新 registry 查询。

## Task 1：资料、请求与评分

文件：新增 `src/rag_eval/system_product.py`、`system_scoring.py` 与对应 tests。

- [x] 先写回归：答案/标签/解析哨兵不进资料或请求；TruthfulQA 选项顺序一致；IFEval prompt 字节不变；矛盾答案拒绝；BBH 按 task 答案域。
- [x] 实现 `SYSTEM_VERSION`、`SYSTEM_SUITES`、`build_system_bundle(cases, source, max_documents=40)`。返回现有 documents/questions/decisions/manifest 形状；questions 有 id/dataset/split/question/expected_answer/references/gold_document_ids/case_id/sample_id/suite/task，新增 product_protocol/material_role。不在生成侧携带答案字段。
- [x] 实现 `primary_scorer(suite)`、`score_system_answer(case, answer, source, instruction_audits=())`。返回 result_record 可用的 status/score/reason/normalized_answer/details；官方 SDK 调用只在显式评分函数中延迟导入。
- [x] 原始答案行唯一提取，IFEval 直接校验完整正文，原始 JSON bundle 验证复用已存在的重建逻辑。

## Task 2：执行与报告

文件：新增 `system_runtime.py`；修改 starter_runner/results/report 与 run_public_starter CLI；新增执行与报告回归。

- [x] 先写回归：七套 R 默认选新协议，显式 legacy 保留旧 N/A；缺少规则审核不丢 IFEval 回答；澄清不进入 Ask；独立会话；引用保留；没有无依据的 Agent 分数。
- [x] runner 增加 product_protocol=None，None 对新七套解析成 SYSTEM_VERSION；legacy 保留历史三套路径与旧 N/A。新路径无需 judge 配置，SN 模型身份由隔离 runtime 的服务配置与源码记录。
- [x] 使用 `repo.preview_reasoning_intent` 和 `AskIntentConfirmation` 原生类型；仅确认产品允许自动执行的完整 preview；保存意图和澄清。通过 repo.ask，复用 capture_synthesis/final_context/evidence_checks 并验证持久化。
- [x] 保存所有原始题目分母、SDK 请求身份、输入资料语义与评分协议；IFEval 规则审核只影响评分可用性。
- [x] 报告记录产品协议和资料角色、逐题行为观察、二元主指标答对覆盖率及解析覆盖率；新旧 protocol 不配对。

## Task 3：静态审阅与交接

- [x] 更新 README、context/status、roadmap、扩展设计及此前 N/A 边界说明，区分历史快照声明与当前 adapter。
- [x] 独立只读审查数据泄漏、SDK 接口、intent 提交、所有分母和历史兼容，修正具体问题。
- [x] `git diff --check` 仅检查补丁格式。测试、数据冻结、应用验证、在线实验和提交都保持未执行。

## Rulings / 进度

- 上述勾选表示代码、用例编写与静态审阅完成，不表示测试通过或运行验收完成。
- 使用已有 docs/public-benchmark-agent-expansion 工作树，保留上轮未提交代码；不另建工作树或重做 Native。
- 本轮回归用例只编写，不执行；最终仅报告代码写入和静态审阅，不宣称阶段验收完成。
- 独立静态审查参照已安装 DeepEval 4.2.2 的 schema、模板、exact-match 和 IFEval verifier 源码，未导入 SDK。审查指出解析覆盖率不应依赖评分完成度，以及上下文映射异常不应抹掉已保存答案；已写入对应处理和回归用例，经二次源码审阅，尚未执行验证。
- 报告核对产品协议、题目、资料和适配记录的身份；显式 legacy N/A 提示指向新协议，避免把历史不适用误认为当前缺少系统适配。

## 后续执行顺序（本轮不执行）

1. 运行新适配、答案提取、执行编排与报告的离线回归，修正失败项；再检查旧三套 R 和 Native 兼容性。
2. 准备每套少量公开题并冻结数据和 SDK 身份；检查资料不含答案标签，IFEval 完成规则正反例审核。资料并集上限 40 篇，超出时明确重选，不能静默截断。
3. 恢复在线任务后先对新路径做小规模 chunk/reasoning 验证，逐题核对真实答案、引用、澄清和持久化，再考虑扩大样本与 Native 参照。
4. 获得可核验 Agent 轨迹后另行设计 Agent/DAG 评分；当前黑盒结果不推断工具调用正确性、轨迹完整性或 Agent 分数。
