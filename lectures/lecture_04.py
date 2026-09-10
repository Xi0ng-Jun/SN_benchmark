"""Lecture 04: reproduce the public retrieval baseline without an LLM judge."""
from __future__ import annotations

import json

from lecture_util import code, heading, repo_root, run, text


def report() -> None:
    heading("当前公开数据实验")
    path = repo_root() / "benchmark-deepeval/results/public-retrieval-50.json"
    if not path.exists():
        text(f"Missing artifact: {path}")
        return
    artifact = json.loads(path.read_text(encoding="utf-8"))
    code("artifact keys", list(artifact))
    code("dataset summaries", artifact["datasets"])
    text("仓库中的报告汇总了每个数据集前 50 条问题。这是 BM25 检索 baseline，不是 Silicon Notebook 线上质量分数。")


def interpret() -> None:
    heading("把结果当作实验来读")
    values = {
        "MultiHop-RAG Hit@10": 0.9512,
        "MultiHop-RAG Evidence Recall@10": 0.7642,
        "SciFact Hit@10": 0.8000,
        "SciFact Evidence Recall@10": 0.7730,
    }
    for name, value in values.items():
        text(f"{name}: {value:.4f}")
    text("Hit@10 与完整证据集合率之间的差距是本课重点：找到一段相关文本，不等于满足多跳问题所需的完整证据集合。")


def boundary() -> None:
    heading("这节课不能得出什么结论")
    text("这里没有展示 DeepEval judge 分数，因为记录实验时模型端点不可用。")
    text("不能从这个公开 BM25 baseline 推出 Silicon Notebook 的领域质量结论。")
    text("下一次实验必须运行真实项目 adapter，保存实际上下文，并先由人工审核生成的 goldens。")


def main() -> None:
    report(); interpret(); boundary()
    heading("练习")
    text("选择一个公开数据集，写下它验证了哪种能力，以及它没有覆盖 Silicon Notebook 的哪种产品行为。")


run(main)
