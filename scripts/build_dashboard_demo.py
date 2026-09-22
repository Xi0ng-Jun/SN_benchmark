#!/usr/bin/env python3
"""Build a labelled, entirely synthetic QMSum walkthrough; no network or SN calls."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from rag_eval.artifacts import digest, save_json, save_jsonl
from rag_eval.experiment_aggregation import write_dashboard
from rag_eval.notebook_baseline import (BASELINE_VERSION, baseline_config, partition_turns,
                                        plan_rows as baseline_plans, run_qmsum_case)
from rag_eval.notebook_bundle import prepare, partition_bundle
from rag_eval.notebook_data import VERSION
from rag_eval.notebook_rescoring import rescore_run
from rag_eval.notebook_runner import plan_rows, score_outputs
from rag_eval.starter_protocol import fingerprint
from rag_eval.starter_report import load_run

DEMO = 'Synthetic demonstration: no SN or judge was called. Not experimental evidence.'
JUDGE = {'model': 'synthetic-judge', 'provider': 'none', 'synthetic': True}
NATIVE = 'sn-deepeval-native-v1'
MEETING = {
    'meeting_transcripts': [
        {'speaker': 'Maya', 'content': 'The prototype launch was planned for May. Today we need to review launch risks and budget.'},
        {'speaker': 'Leo', 'content': 'Battery safety testing needs another four weeks. We must delay the launch until June.'},
        {'speaker': 'Maya', 'content': 'Agreed. Launch in June after the safety tests pass. Leo owns testing and will report next Friday.'},
        {'speaker': 'Nora', 'content': 'The budget stays at 50000 dollars. We will postpone the marketing campaign, not cut testing.'},
        {'speaker': 'Maya', 'content': 'The decisions are a June launch, unchanged budget, and a Friday testing update.'},
    ],
    'general_query_list': [{'query': 'Summarize the meeting.',
        'answer': 'The team delayed the launch from May to June for battery safety testing, kept the 50000 dollar budget, postponed marketing, and assigned Leo a testing update next Friday.'}],
    'specific_query_list': [
        {'query': 'Why was the launch delayed, and what follow-up was agreed?',
         'answer': 'Battery safety testing needs four more weeks, so launch moves to June. Leo will report testing progress next Friday.',
         'relevant_text_span': [['1', '2']]},
        {'query': 'What did the team decide about the budget and marketing?',
         'answer': 'The budget remains 50000 dollars and the marketing campaign is postponed to protect safety testing.',
         'relevant_text_span': [['3', '4']]},
    ],
}
ANSWERS = {
    'chunk': ['The team moved launch to June for battery testing, retained the budget and postponed marketing. Leo will update the team next Friday.',
              'Four more weeks of battery safety tests are needed. Launch moves to June; Leo will report next Friday.',
              'The budget remains 50000 dollars; the marketing campaign is postponed.'],
    'reasoning': ['The meeting reviewed the prototype schedule and budget. Safety testing must finish before a June launch. Leo is responsible for a Friday update.',
                  'Battery safety testing delayed the launch until June. Leo owns testing and will report next Friday.', ''],
    'bm25': ['Launch is planned for June. The budget stays unchanged.',
             'Battery safety testing needs four weeks. Leo will provide an update.',
             'The budget stays at 50000 dollars and marketing is postponed.'],
}


def native_example(run, mode, cases, outputs):
    """Synthetic SDK-shaped snapshots and sample links for inspecting the UI."""
    directory = run / 'agent'
    save_json(directory / 'native-manifest.json', dict(schema_version=NATIVE, sdk_version='4.2.2',
        judge=JUDGE, whole_trace_metrics=True, synthetic=True, note=DEMO))
    components, snapshots, scores, diagnostics = [], [], [], []
    for number, (case, output) in enumerate(zip(cases, outputs)):
        if not output['output_available']:
            continue  # Demonstrates an answer failure with no captured component tree.
        request = f'demo-{mode}-{number}'
        common = dict(schema_version=NATIVE, case_id=case['case_id'], mode=mode,
                      request_id=request, judge=JUDGE, synthetic=True)
        record = output['product_record']
        root_id = request + '-root'
        children = []
        if mode == 'reasoning':
            children.append(dict(uuid=request + '-plan', parent_uuid=root_id, name='sn.reasoning.initial_plan',
                type='tool', input={'question': record['question']},
                output={'subqueries': ['Find the launch decision', 'Find follow-up actions']},
                metadata={'stage': 'planning', 'synthetic': True}, children=[]))
        for index in range(2 if mode == 'reasoning' else 1):
            span_id, sample_id = f'{request}-retrieve-{index}', f'{request}-sample-{index}'
            query = record['question'] if index == 0 else 'Find the agreed follow-up and owner'
            candidates = [dict(chunk_id=f'demo-chunk-{i}', text=text) for i, text in enumerate(record['retrieval_context'])]
            event = dict(name='sn.retrieve.chunks', span_id=span_id, parent_span_id=root_id,
                stage='retrieval', input={'query': query}, output=candidates, metadata={'status': 'success', 'synthetic': True})
            components.append({**common, 'record_type': 'span', **event})
            link = dict(sample_id=sample_id, span_id=span_id, span_name=event['name'], grouping='native_span', query_index=None)
            components.append({**common, **link, 'record_type': 'sample', 'input': query,
                               'actual_output': json.dumps(candidates), 'retrieval_context': record['retrieval_context']})
            scores.append({**common, **link, 'metric': 'Contextual Relevancy', 'status': 'scored',
                           'score': .82 if index == 0 else .58, 'reason': '构造分数：展示逐查询组件归属，不代表 judge 实测。'})
            children.append(dict(uuid=span_id, parent_uuid=root_id, name=event['name'], type='retriever',
                                 input=event['input'], output=candidates, metadata=event['metadata'], children=[]))
        span_id, sample_id = request + '-synthesis', request + '-answer-sample'
        name = 'sn.synthesis.' + ('reasoning' if mode == 'reasoning' else 'chunks')
        link = dict(sample_id=sample_id, span_id=span_id, span_name=name, grouping='native_span', query_index=None)
        metadata = dict(status='success', question=record['question'],
                        context_block='\n\n'.join(record['retrieval_context']), synthetic=True)
        event = dict(name=name, span_id=span_id, parent_span_id=root_id, stage='synthesis',
                     input={'question': record['question']}, output={'answer': record['answer']}, metadata=metadata)
        components.append({**common, 'record_type': 'span', **event})
        components.append({**common, **link, 'record_type': 'sample', 'input': record['question'],
                           'actual_output': record['answer'], 'retrieval_context': [metadata['context_block']]})
        for metric, value in [('Faithfulness', .9), ('Answer Relevancy', .85)]:
            scores.append({**common, **link, 'metric': metric, 'status': 'scored', 'score': value,
                           'reason': '构造分数：这里展示合成上下文与回答的对应关系。'})
        children.append(dict(uuid=span_id, parent_uuid=root_id, name=name, type='agent',
                             input=event['input'], output=event['output'], metadata=metadata, children=[]))
        for metric, value in [('Task Completion', .8), ('Step Efficiency', 0.0 if mode == 'reasoning' else .75),
                              ('Plan Quality', .7), ('Plan Adherence', .6)]:
            applicable = mode == 'reasoning' or not metric.startswith('Plan ')
            scores.append({**common, 'grouping': 'whole_trace', 'metric': metric,
                           'status': 'scored' if applicable else 'not_applicable', 'score': value if applicable else None,
                           'reason': '构造分数，非真实测量。' if applicable else 'explicit reasoning plan unavailable'})
        tree = dict(uuid=request, input=record['question'], output=record['answer'],
                    root_spans=[dict(uuid=root_id, parent_uuid=None, name='sn.request', type='agent',
                                     input=record['question'], output=record['answer'], children=children,
                                     metadata={'synthetic': True})])
        snapshots.extend([{**common, 'phase': phase, 'trace': tree} for phase in ('before_scoring', 'completed')])
        diagnostics.append({**common, 'status': 'completed', 'sdk_span_count': len(children) + 1,
                            'evidence_repeat_count': len(record['retrieval_context']) if mode == 'reasoning' else 0,
                            'product_seconds': None, 'judge_seconds': None, 'observation_errors': []})
    for name, rows in [('components', components), ('native-traces', snapshots), ('native-scores', scores),
                       ('native-diagnostics', diagnostics), ('native-errors', [])]:
        save_jsonl(directory / (name + '.jsonl'), rows)


def make_run(root, bundle, mode):
    run = root / 'runs' / ('demo-qmsum-' + mode)
    run.mkdir(parents=True)
    shutil.copytree(root / 'bundle', run / 'input')
    part = bundle['partitions'][0]['partition_id']
    revision = 'notebook-request-v1' if mode == 'bm25' else 'notebook-request-v2'
    product = partition_bundle(bundle, part, request_revision=revision)
    save_json(run / 'product-bundle.json', product)
    context = dict(partition_id=part, selected_cases=bundle['manifest']['selected_cases'],
                   partition_count=bundle['manifest']['partition_count'])
    if mode != 'bm25':
        context['request_revision'] = revision
    identity = dict(source=bundle['manifest'], models={}, code={'revision': 'synthetic-demo-v1'},
                    runtime_settings='synthetic-settings', product_services='synthetic-services',
                    product_bundle=product['manifest'], audits_sha256=fingerprint([]), track='R', notebook_context=context)
    protocol = VERSION
    if mode == 'bm25':
        protocol = BASELINE_VERSION
        identity['baseline'] = baseline_config(top_k=2, max_context_chars=12000)
        identity['product_bundle'] = dict(protocol_version=BASELINE_VERSION, material_manifest=product['manifest'])
    else:
        identity['agent_evaluation'] = dict(protocol=NATIVE, sdk_version='4.2.2', trajectory=True, judge=JUDGE)
    pid = fingerprint({**identity, 'mode': mode})
    cases = bundle['cases']
    planned = baseline_plans(cases, run.name, pid) if mode == 'bm25' else plan_rows(cases, run.name, pid, mode)
    save_jsonl(run / 'planned.jsonl', planned)
    save_json(run / 'manifest.json', dict(format='public-starter-run-v1', run_id=run.name, suite='qmsum',
        track='R', mode=mode, protocol_id=pid, pairing_id=fingerprint(identity), models={}, source_manifest=bundle['manifest'],
        product_protocol=protocol, planned_sha256=digest(run / 'planned.jsonl'), planned_predictions=len(cases),
        planned_scores=len(planned), identity=identity, human_calibration='synthetic-demo', release_gate=False, demo=DEMO))
    outputs, scores = [], []
    turns = partition_turns(run / 'input', product)
    for i, case in enumerate(cases):
        answer = ANSWERS[mode][i]
        if mode == 'bm25':
            output, values = run_qmsum_case(case, lambda prompt, a=answer: a, turns=turns, top_k=2,
                                            run_id=run.name, protocol_id=pid)
            output['product_record']['latency_seconds'] = None  # No simulated model timing.
            outputs.append(output)
            scores.extend(values)
            continue
        question = product['questions'][i]
        contexts = [f"[turn {t['id']}] {t['speaker']}: {t['content']}" for t in turns]
        record = dict(question=question['question'], answer=answer, mode=mode,
            status='success' if answer else 'error', reason=None if answer else 'Synthetic generation failure example',
            context_supported=True, retrieval_context=contexts if answer else [],
            retrieved_document_ids=case['material_document_ids'] if answer else [],
            response={'citations': [{'source_id': 'demo-source', 'element_id': 'demo-chunk-1'}] if answer else [], 'anchors': []},
            deterministic={'citation_count': 1 if answer else 0, 'citation_valid_count': 1 if answer else 0},
            source_to_document={'demo-source': case['material_document_ids'][0]}, synthetic=True)
        outputs.append(dict(case_id=case['case_id'], sample_id=case['sample_id'], suite='qmsum', task=case['task'],
            product_protocol=VERSION, material_role='source_documents', status=record['status'],
            output_available=bool(answer), prediction=answer, product_record=record, reason=record['reason']))
    save_jsonl(run / 'outputs.jsonl', outputs)
    if mode != 'bm25':
        score_outputs(run, cases, planned, scores.append)
        native_example(run, mode, cases, outputs)
        save_json(run / 'product-artifacts/state.json', dict(notebook_id='synthetic-notebook-' + mode,
            prepared=True, documents=1, chunks=5, embedded_chunks=5, kg_present=False, synthetic=True))
        save_json(run / 'product-artifacts/document-map.json', {product['documents'][0]['id']: {
            'source_id': 'demo-source', 'chunk_ids': [f'demo-chunk-{i}' for i in range(5)], 'synthetic': True}})
    save_jsonl(run / 'scores.jsonl', scores)
    save_jsonl(run / 'model-events.jsonl', [])
    save_jsonl(run / 'judge-events.jsonl', [])
    save_json(run / 'state.json', {'phase': 'finished_with_errors' if mode == 'reasoning' else 'finished', 'synthetic': True})
    load_run(run)  # Same validator as real reports; never bypass source/plan validation.
    return run


def build_demo(output):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    save_jsonl(output / 'meeting.jsonl', [MEETING])
    save_json(output / 'source.json', dict(dataset='qmsum', split='synthetic', revision='demo-v1',
        source_url='https://example.invalid/synthetic-demo', license='CC0-1.0', synthetic=True, note=DEMO))
    bundle = prepare('qmsum', output / 'meeting.jsonl', output / 'source.json', output / 'bundle')
    runs = [make_run(output, bundle, mode) for mode in ('chunk', 'reasoning', 'bm25')]
    derived = rescore_run(runs[0], output / 'runs/demo-qmsum-rescore', all_scores=True)
    runs.append(derived)
    report = write_dashboard(runs, output / 'report')
    (output / 'README.txt').write_text(DEMO + '\n\nOpen report/dashboard.html. Keep its details/ directory beside it.\n'
        'BM25 retrieval and objective metrics are computed on synthetic data. Native scores and spans are illustrative.\n', encoding='utf-8')
    return report / 'dashboard.html'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path, help='New demo directory (includes synthetic runs and report)')
    args = parser.parse_args()
    print(build_demo(args.output))


if __name__ == '__main__':
    main()
