import sys
from pathlib import Path
import pytest


PROJECT_BACKEND = Path(__file__).resolve().parents[2] / "project" / "backend"
sys.path.insert(0, str(PROJECT_BACKEND))


def test_deepeval_schema_is_converted_to_product_schema_hint():
    if not (PROJECT_BACKEND / "app/core/model_json.py").is_file():
        pytest.skip("Native judge contract requires the sibling Silicon Notebook project/backend checkout")
    pytest.importorskip("deepeval", reason="Native judge contract requires the deepeval optional dependency")
    from app.core.model_json import validate_model_json_shape
    from deepeval.metrics.g_eval.schema import ReasonScore
    from rag_eval.benchmark_judge import BenchmarkJudge

    raw = '{"reason":"The answer matches.","score":8}'

    class Client:
        model = "configured-model"

        def chat_json(self, messages, response_schema_hint):
            assert isinstance(response_schema_hint, str)
            validate_model_json_shape(raw, response_schema_hint)
            assert set(__import__("json").loads(response_schema_hint)) == {
                "reason", "score"
            }
            return raw

    class Repo:
        def chat(self, workload_id):
            assert workload_id == "ask_answer"
            return Client()

    result = BenchmarkJudge(Repo()).generate("score this", schema=ReasonScore)

    assert result == ReasonScore(reason="The answer matches.", score=8)
