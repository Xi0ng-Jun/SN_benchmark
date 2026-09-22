"""Score saved native component samples using public LLMTestCase/evaluate APIs."""
from collections import Counter
from importlib.metadata import version
import json
from pathlib import Path
from uuid import uuid4

from .artifacts import digest, save_json, save_jsonl
from .starter_results import EventJournal


def load_samples(source_run, sample_ids, metrics):
    from .native_metrics import select_metrics
    select_metrics(metrics)
    if not sample_ids or len(set(sample_ids)) != len(sample_ids):
        raise ValueError('Select explicit unique sample IDs')
    path = Path(source_run) / 'agent/components.jsonl'
    source_hash = digest(path)
    records = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
    samples = [row for row in records if row.get('record_type') == 'sample' and row.get('sample_id') in sample_ids]
    if Counter(s.get('sample_id') for s in samples) != Counter(sample_ids):
        raise ValueError('Unknown or ambiguous sample ID in components.jsonl')
    for sample in samples:
        if sample.get('schema_version') != 'sn-deepeval-native-v1':
            raise ValueError('Only native component samples are supported')
        for field in ('case_id', 'request_id', 'sample_id', 'span_id', 'span_name', 'mode'):
            if not isinstance(sample.get(field), str) or not sample[field].strip():
                raise ValueError('Sample is missing identity: ' + field)
        name = sample['span_name']
        allowed = ('contextual_relevancy',) if name in {'sn.retrieve.chunks', 'sn.retrieve.chunks_multi'} else (
            ('faithfulness', 'answer_relevancy') if name.startswith('sn.synthesis.') else ())
        if set(metrics) - set(allowed):
            raise ValueError('Selected metric does not apply to selected component type')
        contexts = sample.get('retrieval_context')
        if not isinstance(contexts, list) or any(not isinstance(v, str) or not v.strip() for v in contexts):
            raise ValueError('Expected unmodified nonempty context strings (or an empty list)')
        for field in ('input', 'actual_output'):
            if sample.get(field) is not None and not isinstance(sample[field], str):
                raise ValueError('Component input/output must be text or null')
        # Initial native samples didn't duplicate status; their genuine span
        # event is authoritative. Never assume a missing event was successful.
        if 'component_status' not in sample:
            spans = [r for r in records if r.get('record_type') == 'span'
                     and r.get('request_id') == sample['request_id'] and r.get('span_id') == sample['span_id']]
            if len(spans) != 1 or spans[0].get('metadata', {}).get('status') not in {'completed', 'error', 'cancelled'}:
                raise ValueError('Component status unavailable from source span')
    if digest(path) != source_hash:
        raise ValueError('Source components changed while reading; stop the source run first')
    return samples, records, {'components_sha256': source_hash}


def rescore_components(source_run, output, *, sample_ids, metrics, judge_factory, judge_identity):
    """New derived batch, never resume/overwrite or invoke a product callback."""
    from deepeval import evaluate
    from deepeval.evaluate.configs import AsyncConfig, CacheConfig, DisplayConfig, ErrorConfig
    from deepeval.test_case import LLMTestCase
    from .native_metrics import CheckpointMetric, METRICS, METRIC_NAMES
    from .native_sdk import configure_local_sdk, sdk_timeout_identity

    source_run, output = Path(source_run).resolve(), Path(output).resolve()
    if output.is_relative_to(source_run) or source_run.is_relative_to(output):
        raise ValueError('Output must be separate from the source run')
    samples, records, source_identity = load_samples(source_run, sample_ids, metrics)
    if not isinstance(judge_identity, dict) or not judge_identity:
        raise ValueError('Explicit public judge identity is required')
    configure_local_sdk()
    output.mkdir(parents=True, exist_ok=False)
    batch_id = uuid4().hex
    source_files = {p.name: digest(p) for p in (source_run / 'manifest.json', source_run / 'source-identity.json',
                    source_run / 'agent/native-manifest.json') if p.is_file()}
    save_json(output / 'component-score-manifest.json', {
        'schema_version': 'sn-deepeval-component-scoring-v1', 'batch_id': batch_id,
        'source_run': str(source_run), **source_identity, 'source_artifacts': source_files,
        'sample_ids': sample_ids, 'metrics': metrics, 'judge': judge_identity, 'sdk_version': version('deepeval'),
        'sdk_timeout': sdk_timeout_identity(),
        'scoring_sources': {name: digest(Path(__file__).with_name(name)) for name in
                           ('native_component_scoring.py', 'native_metrics.py', 'native_sdk.py', 'starter_model.py')},
        'execution': 'saved components; one full sample and one metric per evaluate; no SN invocation',
    })
    save_jsonl(output / 'components.jsonl', samples)
    plans = [dict({key: sample[key] for key in ('case_id', 'mode', 'request_id', 'sample_id', 'span_id', 'span_name')},
                  batch_id=batch_id, judge=judge_identity, metric=METRIC_NAMES[key],
                  grouping='saved_component_evaluate', source_grouping=sample.get('grouping'),
                  query_index=sample.get('query_index')) for sample in samples for key in metrics]
    save_jsonl(output / 'planned-component-scores.jsonl', plans)
    finished = set()
    counts = Counter()
    state = {'batch_id': batch_id, 'status': 'running', 'score_status_counts': {}}
    try:
        with EventJournal(output / 'component-scores.jsonl') as score_sink, \
             EventJournal(output / 'metric-events.jsonl') as event_sink, \
             EventJournal(output / 'judge-events.jsonl') as judge_sink:
            def record_score(row):
                score_sink(row)
                finished.add((row['sample_id'], row['metric']))
                counts[row['status']] += 1
            try:
                judge = judge_factory(judge_sink)
                for sample in samples:
                    status = sample.get('component_status')
                    if status is None:
                        status = next(r['metadata']['status'] for r in records if r.get('record_type') == 'span'
                                      and r.get('span_id') == sample['span_id'] and r.get('request_id') == sample['request_id'])
                    common = {key: sample[key] for key in ('case_id', 'mode', 'request_id', 'sample_id', 'span_id', 'span_name')}
                    common.update(batch_id=batch_id, judge=judge_identity, grouping='saved_component_evaluate',
                                  source_grouping=sample.get('grouping'), query_index=sample.get('query_index'))
                    def completed(metric, record):
                        record_score({**common, 'metric': metric.__name__, **record})
                    for key in metrics:
                        reason = None
                        if status != 'completed':
                            reason = 'component failed or cancelled'
                        elif not sample.get('input') or not sample['input'].strip():
                            reason = 'missing component question'
                        elif key != 'contextual_relevancy' and not (sample.get('actual_output') or '').strip():
                            reason = 'missing component answer'
                        elif key != 'answer_relevancy' and not sample['retrieval_context']:
                            reason = 'empty component context'
                        if reason:
                            record_score({**common, 'metric': METRIC_NAMES[key], 'status': 'not_applicable',
                                        'score': None, 'reason': reason})
                            continue
                        metric = CheckpointMetric(METRICS[key](model=judge, async_mode=False),
                            link={'sample_id': sample['sample_id'], 'span_id': sample['span_id'], 'batch_id': batch_id},
                            on_start=lambda row: event_sink({**common, **row, 'event': 'started'}), on_result=completed)
                        try:
                            with judge.for_case(sample['case_id'], sample['request_id']):
                                evaluate(test_cases=[LLMTestCase(input=sample['input'], actual_output=sample.get('actual_output'),
                                            retrieval_context=sample['retrieval_context'] or None, name=sample['sample_id'])],
                                    metrics=[metric], identifier=metric.link['score_id'],
                                    async_config=AsyncConfig(run_async=False, max_concurrent=1),
                                    cache_config=CacheConfig(write_cache=False, use_cache=False),
                                    error_config=ErrorConfig(ignore_errors=True, skip_on_missing_params=False),
                                    display_config=DisplayConfig(show_indicator=False, print_results=False, inspect_after_run=False,
                                        results_folder=str(output / 'sdk' / metric.link['score_id'])))
                        finally:
                            metric.checkpoint()
            finally:
                for planned in plans:
                    if (planned['sample_id'], planned['metric']) not in finished:
                        record_score({**planned, 'status': 'error', 'score': None,
                                      'execution_status': 'not_started', 'elapsed_seconds': None,
                                      'reason': 'scoring batch stopped before this metric started'})
        state['status'] = 'finished_with_errors' if counts['error'] else 'finished'
    except BaseException as exc:
        state.update(status='interrupted' if not isinstance(exc, Exception) else 'failed', error_type=type(exc).__name__)
        raise
    finally:
        state['score_status_counts'] = dict(counts)
        save_json(output / 'component-score-summary.json', state)
    return state
