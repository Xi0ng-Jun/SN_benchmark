const test = require('node:test');
const assert = require('node:assert/strict');

const core = require('../src/rag_eval/dashboard/core.js');

function entry(overrides = {}) {
  return {
    id: 'e-1', observation_id: 'o-1', run_key: 'rk-1', run_id: 'run-1',
    suite: 'squad', task: 'qa', track: 'product', mode: 'chunk',
    scorer: 'exact', status: 'scored', output_status: 'success',
    behavior: 'answer', partition: 'p1', config_family: 'cfg-a',
    pairing_id: 'dataset-v1', case_id: 'case-1', score: 1,
    reason: null, plan: {}, result: {},
    ...overrides,
  };
}

test('filterEntries uses OR within a facet and AND across facets', () => {
  const rows = [
    entry({id: '1', suite: 'squad', mode: 'chunk'}),
    entry({id: '2', suite: 'drop', mode: 'chunk'}),
    entry({id: '3', suite: 'squad', mode: 'reasoning'}),
    entry({id: '4', suite: 'boolq', mode: 'chunk'}),
  ];
  const got = core.filterEntries(rows, {suite: ['squad', 'drop'], mode: ['chunk']}, '');
  assert.deepEqual(got.map((row) => row.id), ['1', '2']);
});

test('filterEntries searches case, output and scalar entry fields', () => {
  const rows = [entry({id: '1', task: 'reading', search_text: '火星 检索答案'}), entry({id: '2', task: 'logic'})];
  assert.deepEqual(core.filterEntries(rows, {}, '火星').map((row) => row.id), ['1']);
  assert.deepEqual(core.filterEntries(rows, {}, 'READ').map((row) => row.id), ['1']);
});

test('indexSearchText includes source questions, product input and predictions', () => {
  const rows = [entry({id: '1', observation_id: 'obs'})];
  const observations = {obs: {case: {question: '火星有几颗卫星'}, output: {prediction: '两颗', product_record: {question: '请回答火星问题'}}}};
  const indexed = core.indexSearchText(rows, observations);
  assert.equal(indexed[0].id, '1');
  assert.deepEqual(core.filterEntries(indexed, {}, '两颗').map((row) => row.id), ['1']);
  assert.deepEqual(core.filterEntries(indexed, {}, '请回答').map((row) => row.id), ['1']);
});

test('summarize counts distinct predictions separately from scoring entries', () => {
  const rows = [
    entry({id: '1', scorer: 'exact'}),
    entry({id: '2', scorer: 'faithfulness'}),
    entry({id: '3', observation_id: 'o-2', case_id: 'case-2', score: null, status: 'missing'}),
  ];
  assert.deepEqual(core.summarize(rows), {
    predictions: 2, scoringEntries: 3, scoredEntries: 2, missingEntries: 1,
  });
});

test('groupScores never combines distinct suite track scorer or config families', () => {
  const rows = [
    entry({id: '1', score: 1}),
    entry({id: '2', observation_id: 'o-2', case_id: 'case-2', score: 0}),
    entry({id: '3', scorer: 'faithfulness', score: 0.2}),
    entry({id: '4', config_family: 'cfg-b', score: 0.8}),
  ];
  const groups = core.groupScores(rows);
  assert.equal(groups.length, 3);
  assert.deepEqual(groups.map((g) => [g.scorer, g.config_family, g.valid, g.mean]), [
    ['exact', 'cfg-a', 2, 0.5],
    ['exact', 'cfg-b', 1, 0.8],
    ['faithfulness', 'cfg-a', 1, 0.2],
  ]);
});

test('groupScores separates mode and task and isolates missing config by run', () => {
  const rows = [
    entry({id: '1', mode: 'chunk', task: 'qa'}),
    entry({id: '2', mode: 'reasoning', task: 'qa'}),
    entry({id: '3', mode: 'chunk', task: 'logic'}),
    entry({id: '4', run_key: 'a', run_id: 'a', config_family: null}),
    entry({id: '5', run_key: 'b', run_id: 'b', config_family: null}),
  ];
  const groups = core.groupScores(rows);
  assert.equal(groups.length, 5);
  assert.deepEqual(groups.slice(0, 3).map((group) => [group.mode, group.task]).sort(), [['chunk', 'logic'], ['chunk', 'qa'], ['reasoning', 'qa']]);
});

test('scoreHistogram refuses mixed metric families and reports why', () => {
  const mixed = [entry(), entry({id: '2', scorer: 'faithfulness', score: 0.5})];
  assert.deepEqual(core.scoreHistogram(mixed), {available: false, reason: 'mixed_metric_families', families: ['exact', 'faithfulness']});
  const histogram = core.scoreHistogram([entry(), entry({id: '2', case_id: 'case-2', score: 0.5})], 2);
  assert.equal(histogram.available, true);
  assert.deepEqual(histogram.bins.map((bin) => bin.count), [0, 2]);
});

test('scoreHistogram refuses different suite track or config families even for the same scorer', () => {
  const mixed = [entry(), entry({id: '2', suite: 'drop', score: 0.5})];
  assert.equal(core.scoreHistogram(mixed).reason, 'incompatible_dimensions');
});

test('scoreHistogram handles large compatible arrays without argument spreading', () => {
  const rows = Array.from({length: 150000}, (_, index) => entry({id: String(index), case_id: String(index), score: index % 2}));
  const result = core.scoreHistogram(rows, 2);
  assert.equal(result.available, true);
  assert.equal(result.valid, 150000);
});

test('scoreHistogram blocks missing config identities spanning multiple runs', () => {
  const rows = [entry({config_family: null, run_key: 'a', run_id: 'a'}), entry({id: '2', case_id: '2', config_family: null, run_key: 'b', run_id: 'b'})];
  assert.equal(core.scoreHistogram(rows).reason, 'incompatible_dimensions');
});

test('compareCohorts pairs common planned identities and counts partial scores', () => {
  const a = [entry({id: 'a1', run_key: 'a', run_id: 'a', mode: 'native', score: 0.2}), entry({id: 'a2', run_key: 'a', run_id: 'a', mode: 'native', case_id: 'case-2', score: null, status: 'missing'})];
  const b = [entry({id: 'b1', run_key: 'b', run_id: 'b', mode: 'chunk', score: 0.7}), entry({id: 'b2', run_key: 'b', run_id: 'b', mode: 'chunk', case_id: 'case-2', score: 0.8})];
  const got = core.compareCohorts(a, b);
  assert.equal(got.ok, true);
  assert.equal(got.commonPlanned, 2);
  assert.equal(got.commonValid, 1);
  assert.equal(got.partialPairs, 1);
  assert.ok(Math.abs(got.meanDelta - 0.5) < 1e-12);
  assert.ok(Math.abs(got.referenceMean - 0.2) < 1e-12);
  assert.ok(Math.abs(got.comparisonMean - 0.7) < 1e-12);
  assert.deepEqual({wins: got.wins, ties: got.ties, losses: got.losses}, {wins: 1, ties: 0, losses: 0});
});

test('compareCohorts discloses identities absent from either cohort', () => {
  const a = [entry({run_key: 'a', run_id: 'a', mode: 'native'}), entry({id: 'a2', run_key: 'a', run_id: 'a', mode: 'native', case_id: 'case-2'})];
  const b = [entry({run_key: 'b', run_id: 'b'}), entry({id: 'b3', run_key: 'b', run_id: 'b', case_id: 'case-3'})];
  const got = core.compareCohorts(a, b);
  assert.equal(got.ok, true);
  assert.deepEqual({common: got.commonPlanned, onlyA: got.onlyA, onlyB: got.onlyB}, {common: 1, onlyA: 1, onlyB: 1});
});

test('compareCohorts rejects mismatched dimensions, modes, missing identities, duplicates and overlapping runs', () => {
  const baseA = [entry({run_key: 'a', run_id: 'a', mode: 'native'})];
  const baseB = [entry({run_key: 'b', run_id: 'b', mode: 'chunk'})];
  assert.equal(core.compareCohorts(baseA, [entry({run_key: 'b', run_id: 'b', mode: 'chunk', scorer: 'other'})]).code, 'incompatible_dimensions');
  assert.equal(core.compareCohorts([...baseA, entry({id: 'a2', run_key: 'a2', run_id: 'a2', mode: 'chunk', case_id: 'case-2'})], baseB).code, 'multiple_modes');
  assert.equal(core.compareCohorts([entry({run_key: 'a', run_id: 'a', mode: 'native', pairing_id: ''})], baseB).code, 'missing_identity');
  assert.equal(core.compareCohorts([entry({run_key: 'a', run_id: 'a', mode: 'native', config_family: null})], baseB).code, 'missing_dimension');
  assert.equal(core.compareCohorts([...baseA, entry({id: 'a2', run_key: 'a2', run_id: 'a2', mode: 'native'})], baseB).code, 'duplicate_pair');
  assert.equal(core.compareCohorts(baseA, [entry({run_key: 'a', run_id: 'same-basename', mode: 'chunk'})]).code, 'overlapping_runs');
  assert.equal(core.compareCohorts(baseA, [entry({run_key: 'different/path', run_id: 'a', mode: 'chunk'})]).ok, true);
  assert.equal(core.compareCohorts(baseA, [entry({run_key: 'b', run_id: 'b', mode: 'native'})]).code, 'same_mode');
});
