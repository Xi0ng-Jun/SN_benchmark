"""Saved QASPER evidence and score sheets; no private E5 artifacts or model calls."""
import json

from rag_eval.benchmark_official import prepare_inputs, scorer_identity
from rag_eval.benchmark_submission import build_submission
from rag_eval.identity import fingerprint
from test_benchmark_comparison import rehash
from test_notebook_data_v3 import freeze, qasper_raw
from test_qasper_evidence import fixture, capture
from test_scheduled_generation_audit import attempt, terminal


def event_pair(call_id=1, stamp='2026-10-13T00:00:01+00:00'):
    return [attempt(call_id, recorded_at=stamp), terminal(call_id, recorded_at=stamp)]


def saved_qasper(tmp_path):
    root = tmp_path / 'synthetic-evidence'
    root.mkdir()
    raw = qasper_raw()
    raw['p']['qas'][0]['question'] = 'Which creatures are described as flying in this synthetic paper?'
    bundle = freeze(root / 'source', 'qasper', raw)
    f = fixture(root)
    record = f[4]
    record['qasper_evidence'] = capture(f)
    record.update(context_supported=True, retrieval_context=['k1: Results\nBirds fly.\nFish swim.'] +
                  [f'k{i}: Unrelated block {i}.' for i in range(2, 17)],
                  usage=dict(calls=[dict(latency_ms=10), dict(latency_ms=20)], call_count=2))
    output = dict(case_id='qasper:q', sample_id='q', suite='qasper', task='qa',
                  product_protocol='sn-notebook-benchmarks-v1', material_role='source_documents',
                  status='success', output_available=True, prediction=record['answer'], product_record=record)
    run = root / 'run'
    run.mkdir()
    import shutil
    shutil.copytree(root / 'source/bundle', run / 'input')
    def save(path, obj):
        path.write_text(json.dumps(obj))
    from rag_eval.notebook_bundle import partition_bundle
    from rag_eval.notebook_runner import plan_rows
    from rag_eval.notebook_data import VERSION
    from rag_eval.artifacts import digest, save_jsonl
    product = partition_bundle(bundle, bundle['partitions'][0]['partition_id'], request_revision='notebook-request-v3')
    save(run / 'product-bundle.json', product)
    identity = dict(source=bundle['manifest'], models={'judge': 'synthetic'}, track='R', code={'head_revision': 'f' * 40},
                    runtime_settings='settings', product_services='services', product_bundle=product['manifest'],
                    audits_sha256=fingerprint([]), notebook_context=dict(partition_id=bundle['partitions'][0]['partition_id'],
                    selected_cases=1, partition_count=1, request_revision='notebook-request-v3'))
    protocol = fingerprint(dict(identity, mode='chunk'))
    planned = plan_rows(bundle['cases'], 'synthetic', protocol, 'chunk')
    save_jsonl(run / 'planned.jsonl', planned)
    output['product_protocol'] = VERSION
    save_jsonl(run / 'outputs.jsonl', [output])
    save(run / 'manifest.json', dict(format='public-starter-run-v1', run_id='synthetic', suite='qasper', track='R', mode='chunk',
         product_protocol=VERSION, source_manifest=bundle['manifest'], release_gate=False, protocol_id=protocol,
         pairing_id=fingerprint(identity), identity=identity, planned_sha256=digest(run / 'planned.jsonl'),
         planned_predictions=1, planned_scores=len(planned)))
    save(run / 'state.json', dict(phase='finished'))
    events = [row for i in range(1, 21) for row in event_pair(i,
              '2026-10-13T00:00:01+00:00' if i <= 4 else '2026-10-12T00:00:01+00:00')]
    (run / 'model-events.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in events))
    (run / 'product-artifacts').mkdir()
    (run / 'product-artifacts/attempts.jsonl').write_text(''.join(json.dumps(dict(event=e, case_id='qasper:q', recorded_at=t)) + '\n'
        for e, t in [('started', '2026-10-13T00:00:00+00:00'), ('finished', '2026-10-13T01:00:00+00:00')]))
    method = dict(name='synthetic', kind='reference', citation_style='none', model_identity={'model': 'fixture'},
                  input_policy='public-context', configuration={'comparison_category': 'controlled-rerun'})
    submission = build_submission(bundle, method=method, predictions=[output])
    prepared = prepare_inputs(bundle, submission)
    deps = {'bridge': {'benchmark_official.py': 'a' * 64}, 'python': '3.13.15', 'packages': {}}
    identity = scorer_identity(prepared['profile'], deps)
    score = dict(format='benchmark-scored-submission-v1', bundle_id=submission['bundle_id'], submission_id=fingerprint(submission),
        suite='qasper', scope=submission['scope'], case_ids=submission['case_ids'], method=method, method_id=submission['method_id'],
        coverage=submission['coverage'], profile=prepared['profile'], dependencies=deps, prepared_id=fingerprint(prepared),
        scorer_identity=identity, scorer_id=fingerprint(identity), metrics={'answer_f1': .5}, batch_only_metrics=[],
        per_case=[dict(case_id='qasper:q', status='success', group_id=bundle['cases'][0]['group_id'], task='qa', metrics={'answer_f1': .5})],
        pending_metrics=['evidence_f1'], official_paper_reproduction=False)
    rehash(score)
    save(run / 'submission.json', submission)
    save(run / 'scores.json', score)
    return run, run / 'scores.json', 'qasper:q'
