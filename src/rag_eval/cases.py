from .protocol import validate_result


def to_test_case(record):
    try:
        from deepeval.test_case import LLMTestCase
    except ImportError:
        from dataclasses import make_dataclass
        LLMTestCase = make_dataclass('LLMTestCase', [
            ('input', str), ('actual_output', str), ('expected_output', object), ('retrieval_context', list)
        ])
    validate_result(record)
    return LLMTestCase(input=record['question'], actual_output=record['answer'],
                       expected_output=record.get('expected_answer') or None,
                       retrieval_context=record['retrieval_context'])
