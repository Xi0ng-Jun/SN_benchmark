"""Explanations of current Notebook diagnostics, not scoring implementations."""
from copy import deepcopy

def _metric(name, method, inputs, formula, implementation, limitations, code):
    return dict(name=name, method=method, inputs=inputs, formula=formula,
                implementation=implementation, limitations=limitations, code=code)

CATALOG = {
    "product.citation_object.existence_ratio": _metric(
        "引用对象存在率", "确定性 / 隔离库回查", ["SN response.citations", "source→公开文档映射", "source_elements/chunks 查询结果"],
        "citation_valid_count / citation_count；没有引用对象 → N/A。",
        "source_id 必须来自当前导入映射，element_id + source_id 在 source_elements 或 chunks 表中存在才计有效。",
        "不检查正文每个 [k]、quoted_span 哈希或语义支持；不因返回 grounded=true 就判有效。",
        ["src/rag_eval/benchmark_runtime.py:evidence_checks", "src/rag_eval/run_support.py:citation_score"]),
}

from .notebook_scoring import METRIC_DESCRIPTIONS
CATALOG.update(METRIC_DESCRIPTIONS)

def describe_metric(scorer):
    return deepcopy(CATALOG.get(scorer, _metric(
        scorer, "未登记", [], "未登记；以保存的 score/details/judge 事件为准。",
        "该 scorer 不在当前代码目录中，页面不推测计算过程。", "不会重新计算或补造分数。", [])))


def benchmark_rows():
    rows = []
    from .notebook_runner import metric_specs
    from .notebook_data import ADAPTATION_REVISION
    for suite, tasks in {"qasper": ["extractive"], "multihop_rag": ["comparison_query"],
                         "alce": ["asqa", "qampari", "eli5"], "qmsum": ["general", "specific"],
                         "hotpotqa": ["bridge", "comparison"]}.items():
        for task in tasks:
            for spec in metric_specs({"suite": suite, "task": task, "adaptation_revision": ADAPTATION_REVISION}):
                rows.append((suite + "/" + task, "SN Product（R；chunk / reasoning）", spec["scorer"]))
    return rows
