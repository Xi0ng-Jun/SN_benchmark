"""Lecture 01: DeepEval as an evaluation program.

Run from benchmark-deepeval:
    .venv/bin/python lectures/lecture_01.py
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from lecture_util import code, heading, run, text


@dataclass
class MiniTestCase:
    """A dependency-free model of the fields passed to DeepEval."""
    input: str
    actual_output: str
    expected_output: str | None
    retrieval_context: list[str]


def motivation() -> None:
    heading("为什么要评测 RAG 应用？")
    text("一个答案可能在多个环节失败：")
    text("1. 检索没有召回必要分块；2. 排序把它排得太靠后；3. 生成阶段编造声明；4. 引用指向错误位置。")
    text("单一的端到端分数无法诊断这四类失败。")


def objects() -> None:
    heading("可执行的心智模型")
    case = MiniTestCase(  # @inspect case
        input="如何配置检索索引？",
        actual_output="先配置来源，再构建检索索引。",
        expected_output="参考答案由人工确认。",
        retrieval_context=["build_retrieval_index 触发检索索引重建。"],
    )
    case_dict = asdict(case)  # @inspect case_dict
    code("one test case", case_dict)
    text("DeepEval 的 LLMTestCase 携带同样的核心字段。关键点是：retrieval_context 必须是本次真正送给生成器的上下文，不能替换成 gold context。")


def three_modes() -> None:
    heading("三种评测范围")
    text("端到端：黑盒答案是否完成了任务？")
    text("组件级：retriever、planner、tool 或 generator 是否正常？")
    text("轨迹级：完整的推理路径是否得到好结果？")
    text("Silicon Notebook needs all three eventually, but the current adapter"
         "先从端到端 RAG case 和确定性的检索 ID 开始。")


def main() -> None:
    motivation(); objects(); three_modes()
    heading("练习")
    text("修改 actual_output，让它包含 retrieval_context 中没有支持的声明。哪个 metric 应该暴露这个问题？哪个 metric 无法单独证明它？")


run(main)
