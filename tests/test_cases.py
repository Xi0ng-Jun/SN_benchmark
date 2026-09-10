from rag_eval.cases import to_test_case


def test_to_test_case_maps_project_record_to_deepeval_shape():
    record = {
        "question": "问题",
        "answer": "答案",
        "expected_answer": "参考答案",
        "retrieval_context": ["证据"],
    }

    case = to_test_case(record)

    assert case.input == "问题"
    assert case.actual_output == "答案"
    assert case.expected_output == "参考答案"
    assert case.retrieval_context == ["证据"]
