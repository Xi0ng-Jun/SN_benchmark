const test = require('node:test');
const assert = require('node:assert/strict');

const core = require('../src/rag_eval/dashboard/explorer-core.js');

function row(overrides = {}) {
  return {
    id: 'entry-a', observation_id: 'obs-a', run_key: 'run-a', run_id: 'run-a',
    case_id: 'meeting-1:q1', suite: 'qmsum', task: 'meeting-summary', track: 'R',
    mode: 'chunk', scorer: 'rouge1', metric_name: 'ROUGE-1', scope: 'answer',
    status: 'scored', output_status: 'success', score: 0, config_family: 'cfg-a',
    pairing_id: 'source-a', judge: null, sample_id: null, span_id: null, request_id: null,
    ...overrides,
  };
}

test('hash state roundtrips unicode identifiers and ignores unknown keys', () => {
  const state = {view: 'replay', run: '运行/甲', case: 'meeting 18:q#2', step: 'retrieve', span: 'span/7'};
  const hash = core.formatHash(state);
  assert.deepEqual(core.parseHash(hash + '&debug=1'), state);
  assert.equal(hash, '#view=replay&run=%E8%BF%90%E8%A1%8C%2F%E7%94%B2&case=meeting+18%3Aq%232&step=retrieve&span=span%2F7');
});

test('normalizeHashState falls back from unknown selections without losing valid view', () => {
  const data = {
    runs: [{key: 'run-a'}],
    observations: {'obs-a': {id: 'obs-a', run_key: 'run-a', case_id: 'case-a'}},
  };
  assert.deepEqual(core.normalizeHashState(
    {view: 'analysis', run: 'gone', case: 'missing', step: 'made-up', span: 'gone'}, data,
  ), {
    state: {view: 'analysis', run: 'run-a', case: 'obs-a', step: null, span: null},
    invalid: ['run', 'case', 'step', 'span'],
  });
});

test('filterEntries applies OR inside a dynamic facet and preserves zero scores', () => {
  const entries = [
    row({id: 'a', mode: 'chunk', scorer: 'rouge1', score: 0}),
    row({id: 'b', mode: 'reasoning', scorer: 'rouge1', score: 0.4}),
    row({id: 'c', mode: 'bm25', scorer: 'citation', score: null, status: 'missing'}),
  ];
  const filtered = core.filterEntries(entries, {mode: ['chunk', 'reasoning'], scorer: ['rouge1']});
  assert.deepEqual(filtered.map((entry) => entry.id), ['a', 'b']);
  assert.deepEqual(core.summarizeEntries(filtered), {
    observations: 1, scoringEntries: 2, validScores: 2, missingScores: 0, mean: 0.2,
  });
});

test('facetOptions keeps selected zero-count values available for recovery', () => {
  const entries = [row(), row({id: 'b', mode: 'reasoning', scorer: 'native.task_completion'})];
  assert.deepEqual(core.facetOptions(entries, 'scorer', {mode: ['reasoning'], scorer: ['rouge1']}), [
    {value: 'native.task_completion', count: 1},
    {value: 'rouge1', count: 0},
  ]);
});

test('pairEntries rejects different frozen sources, metrics and judges with explicit reasons', () => {
  const left = [row({run_key: 'left'})];
  assert.equal(core.pairEntries(left, [row({run_key: 'right', pairing_id: 'source-b'})]).code, 'different_source');
  assert.equal(core.pairEntries(left, [row({run_key: 'right', scorer: 'rouge2', metric_name: 'ROUGE-2'})]).code, 'different_metric');
  const judged = [row({run_key: 'left', scorer: 'native.task_completion', metric_name: 'Task Completion', judge: 'judge-a'})];
  assert.equal(core.pairEntries(judged, [row({run_key: 'right', scorer: 'native.task_completion', metric_name: 'Task Completion', judge: 'judge-b'})]).code, 'different_judge');
  assert.equal(core.pairEntries([row({suite: 'qmsum'})], [row({suite: 'qasper'})]).code, 'different_suite');
  assert.equal(core.pairEntries([row({config_family: 'cfg-a'})], [row({config_family: 'cfg-b'})]).code, 'different_configuration');
});

test('pairEntries never invents one-to-one native component pairing for multiple spans', () => {
  const left = [
    row({id: 'l1', run_key: 'left', scope: 'retrieval', scorer: 'native.retrieval', metric_name: 'Retrieval', span_id: 's1', request_id: 'r1'}),
    row({id: 'l2', run_key: 'left', scope: 'retrieval', scorer: 'native.retrieval', metric_name: 'Retrieval', span_id: 's2', request_id: 'r1'}),
  ];
  const right = [row({id: 'r1', run_key: 'right', scope: 'retrieval', scorer: 'native.retrieval', metric_name: 'Retrieval', span_id: 'other', request_id: 'r2'})];
  const result = core.pairEntries(left, right);
  assert.equal(result.ok, false);
  assert.equal(result.code, 'component_distribution_only');
  assert.deepEqual(result.distribution, {reference: 2, comparison: 1});
});

test('pairEntries returns paired question rows for compatible answer metrics', () => {
  const left = [row({id: 'l1', run_key: 'left', score: 0}), row({id: 'l2', run_key: 'left', case_id: 'q2', observation_id: 'lo2', score: null, status: 'error'})];
  const right = [row({id: 'r1', run_key: 'right', score: 0.5}), row({id: 'r2', run_key: 'right', case_id: 'q2', observation_id: 'ro2', score: 0.8})];
  const result = core.pairEntries(left, right);
  assert.equal(result.ok, true);
  assert.deepEqual(result.pairs.map((pair) => [pair.case_id, pair.delta, pair.state]), [
    ['meeting-1:q1', 0.5, 'scored'], ['q2', null, 'partial'],
  ]);
});

test('pairEntries permits BM25 and SN runs sharing frozen question and corpus identities', () => {
  const left = [row({id: 'bm25', run_key: 'bm25', pairing_id: 'manifest-bm25', source_id: 'source-qmsum', question_id: 'question-1', corpus_id: 'corpus-1', product_protocol: 'bm25-v1', configured_system: true, mode: 'bm25'})];
  const right = [row({id: 'sn', run_key: 'sn', pairing_id: 'manifest-sn', source_id: 'source-qmsum', question_id: 'question-1', corpus_id: 'corpus-1', product_protocol: 'sn-v1', configured_system: true, mode: 'chunk', score: 0.4, config_family: 'cfg-b'})];
  const result = core.pairEntries(left, right);
  assert.equal(result.ok, true);
  assert.equal(result.pairs[0].delta, 0.4);
});

test('pairEntries permits same-judge trajectory groups only as distribution, while answer native scores can pair', () => {
  const trajectory = core.pairEntries([
    row({scope: 'trajectory', scorer: 'native.trace', metric_name: 'Trace', source_id: 's', question_id: 'q', span_id: 'a'}),
  ], [
    row({scope: 'trajectory', scorer: 'native.trace', metric_name: 'Trace', source_id: 's', question_id: 'q', span_id: 'b'}),
  ]);
  assert.equal(trajectory.code, 'component_distribution_only');
  const answer = core.pairEntries([
    row({scope: 'answer', scorer: 'native.task_completion', metric_name: 'Task Completion', source_id: 's', question_id: 'q', evaluation_protocol: 'native-v1', judge: 'judge'}),
  ], [
    row({scope: 'answer', scorer: 'native.task_completion', metric_name: 'Task Completion', source_id: 's', question_id: 'q', evaluation_protocol: 'native-v1', judge: 'judge', score: 0.8}),
  ]);
  assert.equal(answer.ok, true);
});

test('selectDisplayTraces keeps only final snapshots when producer marks them', () => {
  const traces = [
    {request_id: 'r1', snapshot: 'before', selected_for_display: false},
    {request_id: 'r1', snapshot: 'after', selected_for_display: true},
    {request_id: 'r2', snapshot: 'only'},
  ];
  assert.deepEqual(core.selectDisplayTraces(traces).map((trace) => trace.snapshot), ['after', 'only']);
});

test('flattenTraceTree preserves native hierarchy and span identity', () => {
  const traces = [{request_id: 'r1', selected_for_display: true, trace: {root_spans: [
    {uuid: 'root', name: 'Ask', children: [{uuid: 'child', name: 'Search', children: []}]},
  ]}}];
  assert.deepEqual(core.flattenTraceTree(traces).map((node) => [node.id, node.parentId, node.depth]), [
    ['root', null, 0], ['child', 'root', 1],
  ]);
});

test('entriesForSpan uses exact span sample and request identities', () => {
  const entries = [
    row({id: 'span', span_id: 's1', request_id: 'r1'}),
    row({id: 'sample', sample_id: 'x1', request_id: 'r1'}),
    row({id: 'wrong-request', span_id: 's1', request_id: 'r2'}),
  ];
  assert.deepEqual(core.entriesForSpan(entries, {span_id: 's1', sample_id: 'x1', request_id: 'r1'}).map((entry) => entry.id), ['span', 'sample']);
  assert.deepEqual(core.entriesForSpan([row({id: 'trajectory', request_id: 'r1', scope: 'trajectory'})], {request_id: 'r1'}).map((entry) => entry.id), ['trajectory']);
});

test('diffConfig reports nested changed fields without treating missing as empty', () => {
  assert.deepEqual(core.diffConfig(
    {mode: 'chunk', model: {name: 'm1', temperature: 0}, corpus: 'a'},
    {mode: 'reasoning', model: {name: 'm1'}, corpus: 'b'},
  ), [
    {path: 'corpus', reference: 'a', comparison: 'b'},
    {path: 'mode', reference: 'chunk', comparison: 'reasoning'},
    {path: 'model.temperature', reference: 0, comparison: undefined},
  ]);
});
