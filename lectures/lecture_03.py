"""Lecture 03: DeepEval's five RAG metrics and judge-model boundaries."""
from __future__ import annotations

from lecture_util import code, heading, run, text


METRICS = [
    ("Contextual Recall", "Does context contain information needed by expected answer?", "retrieval coverage"),
    ("Contextual Precision", "Are relevant contexts ranked before irrelevant ones?", "ordering"),
    ("Contextual Relevancy", "Is the supplied context relevant to the question?", "context signal-to-noise"),
    ("Faithfulness", "Are answer claims supported by actual context?", "grounded generation"),
    ("Answer Relevancy", "Does the answer directly address the question?", "task fit"),
]


def table() -> None:
    heading("五个 metric，五个问题")
    for name, question, layer in METRICS:
        text(f"{name:24} | {layer:24} | {question}")


def contrast() -> None:
    heading("指标如何组合阅读")
    examples = {
        "高召回 + 低忠实性": "召回了证据，但合成阶段增加了无证据声明",
        "低召回 + 高答案相关性": "答案看起来切题，却缺少必要证据",
        "高忠实性 + 低答案相关性": "答案有证据支持，但没有回答问题",
        "低精确率 + 高召回": "检索找到了证据，但带入了太多噪声",
    }
    for label, explanation in examples.items():
        text(f"{label} -> {explanation}")


def optional_run() -> None:
    heading("可选的真实 DeepEval 运行")
    try:
        from rag_eval.deepeval_runner import build_metrics
        metrics = build_metrics()
    except Exception as exc:
        text(f"当前环境无法运行 judge metric：{exc}")
        text("这是环境状态，不是零分。")
        return
    code("constructed metric classes", [type(metric).__name__ for metric in metrics])
    text("Running them still requires actual records and a reachable judge model.")


def main() -> None:
    table(); contrast(); optional_run()
    heading("练习")
    text("针对一个中文流程问题，提出一个能帮助校准 Faithfulness 的人工标签。为什么不能只依赖一个 threshold？")


run(main)
