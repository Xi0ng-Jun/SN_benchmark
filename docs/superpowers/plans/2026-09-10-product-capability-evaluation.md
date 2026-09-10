# 产品能力评测离线实现计划

> **优先级更新：** 用户后续选择[广度优先](../../product-capability-breadth-plan.md)。本文三项任务保留为深度工作参考，新套件只复用其必要部分，不要求先完成全部任务。交互可靠性与资料更新一致性不进入当前范围，在线暂停继续有效。

> **For agentic workers:** 使用 superpowers:executing-plans 在后续实施任务中逐项执行；本文件不启动执行、不恢复在线运行。步骤用 checkbox 记录，实际完成后才勾选。

**Goal:** 从已保存输出形成能解释检索、回答、引用和行为的离线能力报告。

**Architecture:** 在 benchmark 内新增只读导出层，保留历史 JSONL，输出带身份的 sidecar。协议归一化、确定性检查和报告分别独立，不调用产品 repository、judge 或网络；语义判定继续引用历史记录并标明待人审。

**Tech Stack:** Python 标准库、现有 pytest；复用 rag_eval 的 artifacts、protocol、quality_metrics、span_evidence。无新增运行依赖。

**Spec:** [详细方案](../../product-capability-evaluation-plan.md)、[矩阵](../../product-capability-matrix.md)、[数据协议](../../evaluation-data-contract.md)。本次只完成方案与公开样例；下列代码工作尚未实施。

## 全局约束

- 生产项目代码和配置不能修改，在线模型评测暂时不要重新启动。
- 不继续未完成的 baseline 在线评分，不恢复 weekly timer，不制定未经人工校准的质量阈值。
- 新输出放新目录，禁止覆盖 baseline 与历史 smoke。
- 不扩展 KG、重排、PDF/OCR、完整性算法或 UI 专项。
- 模型候选/本文解释不是人工标签；未观测值为 null/unknown，不补造排名、trace、引用支持或分数。

## 任务 1：纯离线协议归一化

**文件：**新增 `src/rag_eval/capability_records.py`、`tests/test_capability_records.py`；读取现有 `docs/examples/drop-smoke-case.json`；补充 `docs/evaluation-data-contract.md` 的实际兼容字段。

**接口：**`normalize_legacy_output(record: dict, *, run_id: str, cell_id: str, raw_artifact_ref: str) -> dict`。消费历史 output，不读 DB、不调用模型；产出协议 Output。只有可信结构化原因才能映射 behavior，普通 ValueError 默认 unknown，外部人工 sidecar 可以补澄清标签。

- [ ] 写失败测试：成功题完整 context/ID/锚点保持；普通 ValueError 不等于澄清；未知前缀、分节、空答案与重复身份分别处理。

```python
def test_generic_value_error_is_not_clarification():
    from rag_eval.capability_records import normalize_legacy_output
    row = {"id": "q", "dataset": "product", "mode": "reasoning",
           "repeat": 0, "attempt_id": "a", "question": "why?",
           "status": "product_error", "error_type": "ValueError",
           "answer": "", "response": {}, "context_supported": False,
           "context_unavailable_reason": "no_successful_synthesis",
           "retrieval_context": [], "captures": []}
    out = normalize_legacy_output(row, run_id="r", cell_id="c",
                                  raw_artifact_ref="outputs.jsonl#q")
    assert out["product_status"] == "product_error"
    assert out["behavior"]["action"] == "unknown"
    assert out["retrieval"]["ranking_available"] is False
    assert out["retrieval"]["ranked_items"] is None
```

- [ ] 运行 `.venv/bin/python -m pytest -q tests/test_capability_records.py`，确认先因模块缺失失败。
- [ ] 实现协议映射：id→case_id、repeat→product_repeat、执行状态原样保存；用 capture_context/id_map 建立 context items，前缀项对象字段为 null；没有可核验证据时保留 unknown。输出 schema_version/raw_artifact_ref，不原地修改输入。
- [ ] 重跑测试，对公开样例规范记录核对原文、完整 context 与身份；JSON 写出使用 allow_nan=False。
- [ ] 更新兼容说明，审阅后提交此项独立变更。

**验收：**无同级产品 checkout 也可测试；缺数据不产生“正确行为”或伪造观测。人工 Case 标签不在本任务生成。

## 任务 2：正文引用与证据来源审计

**文件：**新增 `src/rag_eval/capability_checks.py`、`tests/test_capability_checks.py`；复用 `src/rag_eval/span_evidence.py`，不改变历史检查结果。

**接口：**`check_citation_links(output: dict, *, document_map: dict, documents: dict) -> list[dict]`。消费任务 1 Output 与 public 原文映射；返回 Assessment 形状的确定性记录。无 DB 快照时不重新宣称对象存在，历史 existence 仅作 historical_observation。

- [ ] 写失败测试：真实 `[k1]` / 16 citation 对象分母差异、悬空 `[k999]`、source 归属错、原文哈希不符、quoted_span 截断、context 前缀无法映射。
- [ ] 运行 `.venv/bin/python -m pytest -q tests/test_capability_checks.py`，确认新接口缺失失败。
- [ ] 提取正文 `[kN]`，按 anchors.key 与 capture id_map 双向检查；source 映射 public 文档并核对哈希。观测充分时悬空锚点记 valid/0，观测缺失记 skipped/null；对象数量与正文不同不直接判错，不自动判 claim 语义。

```python
# 在测试中加载公开样例并经任务 1 归一化得到 output；
# document_map/document 字典按公开 ID 建立，不查询产品 DB。
checks = check_citation_links(output, document_map=document_map,
                              documents=documents)
by_id = {row["metric_id"]: row for row in checks}
assert by_id["citation.anchor_resolution"]["denominator"] == 1
assert by_id["citation.anchor_resolution"]["numerator"] == 1
assert by_id["citation.semantic_support"]["status"] == "skipped"
assert by_id["citation.semantic_support"]["score"] is None
```

- [ ] 重跑测试，缺原文与对象不存在必须产生不同 reason_code；改动支持文本不能得到自动“语义支持=true”。
- [ ] 更新一题到底的已实现边界，审阅后提交。

**验收：**准确报告正文锚点分母与证据来源，不用 Faithfulness 代替 citation precision，不把元数据检查升级成实时 DB 检查。

## 任务 3：能力分桶、缺失分母与配对解释

**文件：**新增 `src/rag_eval/capability_report.py`、`scripts/report_product_capabilities.py`、`tests/test_capability_report.py`；README 增加离线入口。旧 report_public_benchmark.py 保持历史可读。

**接口：**`build_capability_report(cases: list[dict], outputs: list[dict], assessments: list[dict], manifest: dict) -> dict`。输入前两项 sidecar 和保存 scores，输出分母、均分、行为矩阵、配对差与 provenance。

- [ ] 写失败测试：计划两题缺一输出、judge error 不填零、skipped 不算 valid、未计划诊断不记 missing、重复评分不增加题数、仅一题双方 valid、输入哈希不同拒绝比较。
- [ ] 运行 `.venv/bin/python -m pytest -q tests/test_capability_report.py`，确认预期失败。
- [ ] 用计划键减观测键生成 missing；每个计划 metric 分 valid/skipped/error/missing；mean 仅取 valid 有限值；缺能力标签归 unclassified。配对依协议身份白名单，差值只用双方 valid，全部题状态转移另报。

```python
# 构造两题、仅一题双方有有效评分的输入验证报告，不调用 judge。
report = build_capability_report(cases, outputs, assessments, manifest)
metric = report["metrics"]["Answer Correctness"]
assert metric["planned"] == sum(metric[s] for s in
                                ("valid", "skipped", "error", "missing"))
assert report["paired_modes"]["Answer Correctness"]["paired_valid"] == 1
```

- [ ] CLI 只接受显式输入及全新 --output-dir，拒绝覆盖已有目录；不导入 benchmark_runtime，不提供 prepare/ask/judge/timer 子命令。
- [ ] 重跑测试和 `scripts/check_public_benchmark_offline.py`；用公开样例生成临时报告，核对三个历史分数、16/1 引用数量和 pending 人审。
- [ ] 更新 README/状态，审阅后提交。

**验收：**无模型即可从保存输入生成可追溯报告；无质量总分或未经校准的 pass/fail，协议不同不直接宣称回归。

## 后续顺序与进入条件

1. **真实人审**：复用 40 份已准备材料；正确性/限定条件、事实一致性、引用支持、可回答性/行为分别标 correct/partial/incorrect/unjudgeable 并给证据。先隐藏 mode/judge 分数盲审，分歧再仲裁；模型不得代填。记录混淆表、一致率与无法判断比例，不设发布阈值。
2. **产品场景集**：事实、跨文档比较、数字/日期、引用、澄清/拒答/限制，每类先少量经人审样本；debug/calibration/regression 按问题族与原文分组。数量是预算建议，非覆盖保证。数值规范化与正式 EM/F1 scorer 另立最小计划，保留 DROP 复合 span 及 SQuAD 备选规则。
3. **新在线小实验**：用户明确恢复模型调用后，核对隔离/版本/数据身份，使用新 run-dir。不得自动续跑暂停 baseline；新协议不回填历史运行；配对比较 chunk/reasoning，不扩大专项。
4. **自动化**：可解释报告、人工校准、完整 baseline 与用户明确恢复 timer 的意图齐备后另行处理。tracing、ConversationalTestCase、多轮指标和状态 UI 是后续独立需求，不因本计划自动进入实施。

## 计划自审

任务 1 覆盖统一结构和历史兼容；任务 2 覆盖实际证据及引用可追溯；任务 3 覆盖主/诊断指标、错误/缺失和配对解释。notebook/runtime 规则在协议中定义，在线执行另有进入条件。引用语义、行为恰当性与数值操作数需真实人审；无可靠观测保持未知。此次不需要新增代码即可完成第一阶段交付。
