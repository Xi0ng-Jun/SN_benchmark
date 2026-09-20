import json

from rag_eval.agent_evaluator import evaluate_run
from rag_eval.artifacts import save_jsonl
from rag_eval.starter_runner import read_rows


def partial_trace():
    return {
        "trace_id": "trace-1",
        "completeness": "partial",
        "spans": [{"step_type": "retrieve", "summary": "retrieve"}],
    }


def test_evaluate_run_writes_separate_diagnostic_artifacts_without_judging(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    save_jsonl(run / "outputs.jsonl", [
        {
            "case_id": "case-1", "sample_id": "case-1", "suite": "qasper", "task": "qa",
            "mode": "reasoning", "status": "success", "output_available": True,
            "prediction": "An event", "trace": partial_trace(),
            "product_record": {
                "question": "What happened?", "answer": "An event",
                "context_available": True, "citations_available": True,
                "retrieval_context": ["The event happened."],
            },
        },
        {
            "case_id": "case-2", "sample_id": "case-2", "suite": "qasper", "task": "qa",
            "mode": "chunk", "status": "success", "output_available": True,
            "prediction": "A result", "trace": {"completeness": "none", "spans": []},
            "product_record": {"question": "What result?", "answer": "A result"},
        },
    ])
    output = tmp_path / "agent-report"

    summary = evaluate_run(run, output)

    assert summary["format"] == "sn-agent-evaluation-v1"
    assert summary["judge_enabled"] is False
    assert summary["completeness_counts"] == {"none": 1, "partial": 1, "complete": 0}
    assert len(read_rows(output / "agent-traces.jsonl")) == 2
    assert len(read_rows(output / "agent-diagnostics.jsonl")) == 2
    assert read_rows(output / "agent-scores.jsonl") == []
    assert summary["mode_comparison"][0]["status"] == "unpaired"
    assert not (run / "agent-scores.jsonl").exists()
    json.loads((output / "agent-summary.json").read_text(encoding="utf-8"))


def test_saved_intent_preview_is_reported_without_inventing_agent_steps(tmp_path):
    run = tmp_path / 'run'
    run.mkdir()
    save_jsonl(run / 'outputs.jsonl', [
        dict(case_id='qmsum:18:general:0', status='clarification', output_available=False,
             product_record=dict(mode='reasoning', original_question='Summarize the whole meeting.',
                 question='Summarize the whole meeting.\n\nSummarize with respect to this query.',
                 reason='native intent requires clarification; no answers supplied', response={},
                 trace=dict(completeness='none', spans=[]),
                 intent_preview=dict(needs_clarification=True, intent_type='explain', ambiguities=[
                     dict(id='ambiguity-input', reason='Unresolved reference', question='Which object?',
                          required=True, options=[])]))),
        dict(case_id='qmsum:0:specific:3', status='error', product_record=dict(mode='reasoning',
             reason='native answer synthesis failed', intent_preview=dict(needs_clarification=False),
             response=dict(llm_mode='synthesis_failed'))),
        dict(case_id='unknown', mode='chunk', status='error', product_record={}),
        dict(case_id='answered', mode='chunk', status='success', prediction='Answer.',
             product_record=dict(answer='Answer.', response=dict(answer='Answer.'))),
    ])
    before = (run / 'outputs.jsonl').read_bytes()
    output = tmp_path / 'report'
    summary = evaluate_run(run, output)
    rows = {r['case_id']: r for r in read_rows(output / 'agent-diagnostics.jsonl')}
    d = rows['qmsum:18:general:0']['execution']
    assert d['ask_entered'] is False
    assert d['termination_phase'] == 'intent_preview'
    assert d['clarification_reasons'] == ['Unresolved reference']
    assert 'this query' in d['submitted_question']
    assert rows['qmsum:18:general:0']['diagnostics']['completeness'] == 'none'
    assert rows['qmsum:18:general:0']['diagnostics']['total_duration_ms'] is None
    assert rows['qmsum:0:specific:3']['execution']['termination_phase'] == 'answer_synthesis'
    assert rows['qmsum:0:specific:3']['execution']['ask_entered'] is True
    assert rows['unknown']['execution']['ask_entered'] is None
    assert rows['unknown']['execution']['termination_phase'] == 'unknown'
    assert rows['answered']['diagnostics']['final_output_available'] is True
    assert summary['execution_stage_counts']['intent_preview'] == 1
    assert summary['output_status_counts'] == {'clarification': 1, 'error': 2, 'success': 1}
    assert (output / 'agent-report.md').is_file()
    assert 'Unresolved reference' in (output / 'agent-report.md').read_text()
    assert read_rows(output / 'agent-scores.jsonl') == []
    assert (run / 'outputs.jsonl').read_bytes() == before
