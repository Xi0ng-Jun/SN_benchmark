"""Lecture 02: RAG cases, gold evidence, and deterministic retrieval metrics."""
from __future__ import annotations

from lecture_util import code, heading, run, text


def sample_case() -> dict:
    return {
        "question": "Which evidence supports the claim?",
        "answer": "The answer cites both document A and document B.",
        "expected_answer": "Both documents are required.",
        "gold_document_ids": ["A", "B"],
        "retrieved_ids": ["noise", "A", "B"],
        "retrieval_context": ["A: first premise.", "B: second premise."],
    }


def explain_schema() -> None:
    heading("一行结果，三种证据视角")
    row = sample_case()
    code("adapter output", row)
    text("gold_document_ids 表示人工确认的必要证据。")
    text("retrieved_ids 表示系统实际返回的对象。")
    text("retrieval_context 是送给生成器的文本。这三个字段不能静默互相替换。")


def metrics() -> None:
    heading("手算一个小型 Recall@K 和 MRR")
    gold = {"A", "B"}
    retrieved = ["noise", "A", "B"]
    k = 3
    hit = bool(gold.intersection(retrieved[:k]))
    recall = len(gold.intersection(retrieved[:k])) / len(gold)
    first_rank = min(i + 1 for i, item in enumerate(retrieved[:k]) if item in gold)
    mrr = 1 / first_rank
    code("Hit@3", hit); code("Evidence Recall@3", recall); code("MRR@3", mrr)
    text("这就是 DeepEval 不能替代确定性指标的原因：这些值不需要 judge model，只根据 ID 就能复现。")


def failure_modes() -> None:
    heading("诊断矩阵")
    for symptom, likely in [
        ("top K 中没有必要 ID", "召回 / query rewrite / index"),
        ("必要 ID 排在第 9 位", "排序 / reranker / fusion"),
        ("上下文包含很多无关分块", "top-K / chunking / filters"),
        ("答案增加了无证据声明", "prompt / synthesis / grounding"),
    ]:
        text(f"{symptom} -> inspect {likely}")


def main() -> None:
    explain_schema(); metrics(); failure_modes()
    heading("练习")
    text("把 retrieved_ids 改成 [A, noise, noise]。哪个确定性分数会改善？哪一种完整证据覆盖会变差？")


run(main)
