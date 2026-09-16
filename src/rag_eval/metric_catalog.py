"""Explanations of existing scorers, not additional scoring implementations.

Descriptions refer to the checked-in runner and pinned SDK 4.2.2. Historical
scores always retain their own source/model identities and saved judge events.
"""
from copy import deepcopy

from .starter_protocol import SUITES
from .public_expansion_protocol import EXPANSION_SUITES


def _metric(name, method, inputs, formula, implementation, limitations, code):
    return dict(name=name, method=method, inputs=inputs, formula=formula,
                implementation=implementation, limitations=limitations, code=code)


EXACT = _metric(
    "最终答案精确匹配", "确定性 / DeepEval Scorer", ["schema 解析后的 prediction", "expected_output"],
    "prediction 非空且 prediction.strip() == expected_output.strip() → 1；否则 0。",
    "按冻结 SDK 模板构造参考值，使用 Scorer.exact_match_score；不把诊断归一化结果替换成评分输入。",
    "格式/请求失败单独记录；0/1 不评价推导步骤。GSM8K 产品数值不做数值等价换算。",
    ["src/rag_eval/starter_native.py:score_prediction", "src/rag_eval/public_expansion_native.py:score_prediction"])

IFEVAL = _metric(
    "全部指令满足", "确定性 / 已审计 verifier", ["完整回答正文", "instruction_id_list", "kwargs", "instruction-audits"],
    "所有指令均通过 → 1；有任意一条失败 → 0；有任意未审计指令 → N/A。",
    "逐条匹配 instruction_id、kwargs、verifier 哈希和正反例审计，复核正反例，再执行 verify_instruction_compliance；保存逐指令结果。",
    "Native 缺审计时不发起模型预测；Product 可保存回答但不给规则分。只评价列出的指令。",
    ["src/rag_eval/starter_native.py:score_prediction", "src/rag_eval/starter_native.py:audit_instruction"])

CATALOG = {
    **{info["scorer"]: deepcopy(EXACT) for suite, info in {**SUITES, **EXPANSION_SUITES}.items()
       if suite not in {"squad", "drop", "ifeval"}},
    "deepeval.squad_score.binary_judge": _metric(
        "DeepEval SQuAD 二元 judge", "LLM / 内置 Benchmark scorer", ["native_input（含模板上下文）", "prediction", "expected_output"],
        "显式 judge 按 SDK 提示输出 JSON answer=0 或 1；校验只接受二元结果。",
        "Scorer.squad_score 提交问题/上下文、预测与参考；允许如 2/two 的表达差异。",
        "不是 GEval，也不是原始 SQuAD EM/token-F1。评分理由未返回时不补造。",
        ["src/rag_eval/starter_native.py:score_prediction"]),
    "deepeval.quasi_contains_score": _metric(
        "DROP 归一化备选匹配", "确定性 / DeepEval Scorer", ["prediction", "SDK expected_output 列表"],
        "normalize_text(prediction) 在 normalize_text(targets) 列表中 → 1；否则 0。",
        "使用 SDK quasi_contains_score；名字含 contains，但实际检查归一化后的整串是否等于任一列表项。",
        "不是数值运算验证，也不等同 DROP 多 span 完整性 F1；Product 使用另一套 GEval 口径。",
        ["src/rag_eval/starter_native.py:score_prediction"]),
    "deepeval.ifeval.audited_all_instructions": deepcopy(IFEVAL),
    "product.ifeval.audited_all_instructions.full_body.v1": deepcopy(IFEVAL),
    "product.deepeval.exact_match_score.final_answer.v1": _metric(
        "SN 最终答案精确匹配", "确定性 / 产品提取 + SDK Scorer", ["SN 完整回答", "原题答案域", "SDK expected_output"],
        EXACT["formula"],
        "完整回答必须恰有一个 Final answer 标记和一行严格的 Final answer: VALUE；检查枚举/数字/任务域后，将原样文本交给 exact_match_score。",
        "提取失败记 unparsed/null；GSM8K 不去逗号、不作整数强制转换；不是 Native 榜单协议。",
        ["src/rag_eval/system_scoring.py:parse_system_answer", "src/rag_eval/system_scoring.py:score_system_answer"]),
    "product.boolq.explicit_conclusion.v1": _metric(
        "SN Yes/No 结论匹配", "确定性 / 项目规则", ["SN 完整回答", "Yes/No 参考标签", "产品状态"],
        "解析出的 Yes/No 与参考一致 → 1；不同 → 0；格式不合法 → unparsed/null。",
        "首个非空行须严格为 Final answer: Yes 或 Final answer: No；全文不能同时出现 Yes/No 两个词。",
        "解释里出现另一标签也会使解析失败；正常回答才评分。",
        ["src/rag_eval/starter_product.py:check_boolq"]),
    "product.GEval.AnswerCorrectness": _metric(
        "答案正确性与完整性", "LLM / 自定义 GEval", ["问题", "完整回答", "公开参考答案的 JSON 数组"],
        "judge 按 0–2 错误、3–6 部分正确、7–10 正确完整的 rubric 评分；由 SDK 返回 0–1 metric.score。",
        "显式 evaluation_steps 检查事实、数值、日期、单位、限定条件、遗漏与矛盾；任一完整备选可接受，DROP 增加有效计算说明。保存 score/reason 和已记录的 judge 调用。",
        "不要求 Final answer 行，不先抽短答案；不是固定的字符串公式。分数非正确概率，人工校准待完成。",
        ["src/rag_eval/quality_metrics.py:build_quality_metrics", "src/rag_eval/starter_runner.py:_product_score"]),
    "product.Faithfulness": _metric(
        "回答与实际上下文的一致性", "LLM / 内置 FaithfulnessMetric", ["问题", "完整回答", "实际最终合成上下文"],
        "本地 SDK 4.2.2 默认：verdict != no 的声明数 / 总声明数；无声明返回 1；idk 默认不扣分。",
        "LLM 从上下文提取事实、从回答提取声明，再生成逐声明 verdict；SDK 聚合为分数。项目使用 async_mode=False，其余默认参数。",
        "需 context_supported 且上下文非空；无可靠上下文 N/A。高分不保证逐条引用支持断言；历史版本以冻结 SDK 为准。",
        ["src/rag_eval/quality_metrics.py:build_quality_metrics", "src/rag_eval/starter_runner.py:_product_score"]),
    "product.final_context.document_coverage": _metric(
        "最终上下文的目标文档覆盖", "确定性 / 集合比较", ["gold_document_ids", "source_ids", "retrieved_document_ids"],
        "|gold_document_ids ∩ retrieved_document_ids| / |gold_document_ids|。",
        "先将 SN 实际合成上下文中的 source ID 映射回公开文档 ID，再计算交集；source 与文档映射数量必须一致。",
        "无可靠上下文、gold 或完整映射 → N/A；不是完整候选召回率/排名指标，也不证明证据内容充分。",
        ["src/rag_eval/benchmark_runtime.py:evidence_checks", "src/rag_eval/starter_runner.py:_product_score"]),
    "product.citation_object.existence_ratio": _metric(
        "引用对象存在率", "确定性 / 隔离库回查", ["SN response.citations", "source→公开文档映射", "source_elements/chunks 查询结果"],
        "citation_valid_count / citation_count；没有引用对象 → N/A。",
        "source_id 必须来自当前导入映射，element_id + source_id 在 source_elements 或 chunks 表中存在才计有效。",
        "不检查正文每个 [k]、quoted_span 哈希或语义支持；不因返回 grounded=true 就判有效。",
        ["src/rag_eval/benchmark_runtime.py:evidence_checks", "src/rag_eval/starter_runner.py:_product_score"]),
}


def describe_metric(scorer):
    return deepcopy(CATALOG.get(scorer, _metric(
        scorer, "未登记", [], "未登记；以保存的 score/details/judge 事件为准。",
        "该 scorer 不在当前代码目录中，页面不推测计算过程。", "不会重新计算或补造分数。", [])))


def benchmark_rows():
    rows = []
    for suite, info in {**SUITES, **EXPANSION_SUITES}.items():
        rows.append((suite, "Native（N）", info["scorer"]))
        primary = ("product.GEval.AnswerCorrectness" if suite in {"squad", "drop"} else
                   "product.boolq.explicit_conclusion.v1" if suite == "boolq" else
                   "product.ifeval.audited_all_instructions.full_body.v1" if suite == "ifeval" else
                   "product.deepeval.exact_match_score.final_answer.v1")
        metrics = [primary]
        if suite in {"squad", "drop", "boolq"}:
            metrics += ["product.Faithfulness", "product.final_context.document_coverage"]
        if suite == "logiqa":
            metrics += ["product.final_context.document_coverage"]
        metrics += ["product.citation_object.existence_ratio"]
        rows.extend((suite, "SN Product（R；chunk / reasoning）", scorer) for scorer in metrics)
    return rows
