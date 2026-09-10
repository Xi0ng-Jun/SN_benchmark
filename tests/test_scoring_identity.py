from rag_eval.scoring_identity import build_scoring_identity


def test_scoring_identity_covers_judge_sources_versions_and_settings(tmp_path):
    root, project = tmp_path / "benchmark", tmp_path / "project"
    for path in (root / "src/rag_eval", project / "backend/app/core",
                 project / "backend/app/services"):
        path.mkdir(parents=True, exist_ok=True)
    for relative in ("src/rag_eval/quality_metrics.py", "src/rag_eval/benchmark_judge.py",
                     "src/rag_eval/cases.py"):
        (root / relative).write_text(relative)
    for relative in ("backend/app/core/config.py", "backend/app/core/llm.py",
                     "backend/app/core/model_json.py", "backend/app/services/model_provider.py",
                     "backend/app/services/model_registry.py"):
        (project / relative).write_text(relative)
    config = {"services": {"judge": {"model": "m", "kind": "chat"}},
              "bindings": {"ask_answer": "judge"}, "thinking": {"ask_answer": "disabled"}}

    first = build_scoring_identity(root, project, config,
        settings={"MODEL_JSON_REPAIR_MODE": "on"}, versions={"deepeval": "4.2.2", "pydantic": "2"})
    (root / "src/rag_eval/benchmark_judge.py").write_text("changed")
    changed_code = build_scoring_identity(root, project, config,
        settings={"MODEL_JSON_REPAIR_MODE": "on"}, versions={"deepeval": "4.2.2", "pydantic": "2"})
    changed_version = build_scoring_identity(root, project, config,
        settings={"MODEL_JSON_REPAIR_MODE": "on"}, versions={"deepeval": "4.3", "pydantic": "2"})

    assert first != changed_code
    assert changed_code != changed_version
    assert "MODEL_JSON_REPAIR_MODE" not in str(first)
