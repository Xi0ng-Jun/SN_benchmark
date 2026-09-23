const test = require('node:test');
const assert = require('node:assert/strict');
const map = require('../src/rag_eval/dashboard/map-view.js');

const graph = {
  nodes: [
    {id: 'source', kind: 'dataset'},
    {id: 'generate', kind: 'run', operation: 'generation', run_key: 'g', source_id: 'source'},
    {id: 'answers', kind: 'answers', run_key: 'g', source_id: 'source'},
    {id: 'score', kind: 'scores', run_key: 'g', source_id: 'source'},
    {id: 'rescore', kind: 'run', operation: 'rescoring', run_key: 'r', source_id: 'source'},
    {id: 'score2', kind: 'scores', run_key: 'r', source_id: 'source'},
  ],
  edges: [
    {source: 'source', target: 'generate', kind: 'source'},
    {source: 'source', target: 'rescore', kind: 'source'},
    {source: 'generate', target: 'answers', kind: 'generate'},
    {source: 'answers', target: 'score', kind: 'evaluate'},
    {source: 'generate', target: 'score', kind: 'scoring_batch'},
    {source: 'answers', target: 'rescore', kind: 'rescore'},
    {source: 'answers', target: 'score2', kind: 'evaluate'},
    {source: 'rescore', target: 'score2', kind: 'scoring_batch'},
  ],
};

test('layout keeps generation and answer aligned and puts rescoring after its original answer', () => {
  const layout = map.layoutGraph(graph);
  const at = id => layout.nodes.find(n => n.id === id);
  assert.equal(at('generate').y, at('answers').y);
  assert.equal(at('generate').y, at('score').y);
  assert.ok(at('answers').x > at('generate').x);
  assert.ok(at('rescore').x > at('answers').x);
  assert.ok(at('score2').x > at('rescore').x);
  assert.equal(layout.nodes.length, graph.nodes.length);
  assert.deepEqual(layout.edges.map(e => e.kind), ['source', 'generate', 'evaluate', 'rescore', 'scoring_batch']);
  assert.equal(graph.edges.length, 8, 'display simplification must not change recorded provenance');
});

test('large and external-source graphs have no overlapping cards or unreachable bounds', () => {
  const expanded = {nodes: [...graph.nodes], edges: [...graph.edges]};
  for (let i = 0; i < 70; i++) expanded.nodes.push({id: 'g'+i, kind: 'run', operation:'generation', source_id:'source'});
  expanded.nodes.push({id:'external',kind:'external',source_id:'source'}, {id:'outside-answers',kind:'answers'});
  expanded.edges.push({source:'external',target:'outside-answers',kind:'saved_answers'});
  const layout = map.layoutGraph(expanded);
  assert.equal(layout.nodes.length, expanded.nodes.length);
  for (const a of layout.nodes) {
    assert.ok(a.x >= 0 && a.y >= 0);
    assert.ok(a.x + a.width <= layout.bounds.width && a.y + a.height <= layout.bounds.height);
    for (const b of layout.nodes) if (a.id !== b.id) {
      assert.ok(a.x+a.width <= b.x || b.x+b.width <= a.x || a.y+a.height <= b.y || b.y+b.height <= a.y, `${a.id} overlaps ${b.id}`);
    }
  }
  const fit = map.fitView(layout.bounds, 700, 480);
  assert.ok(fit.x <= 0 && fit.y <= 0);
  assert.ok(fit.x+fit.width >= layout.bounds.width && fit.y+fit.height >= layout.bounds.height);
  assert.ok(Math.abs(fit.width/fit.height - 700/480) < 1e-9);
});

test('zoom keeps the point under the pointer stable and enforces a finite range', () => {
  const view = {x:100,y:200,width:800,height:400};
  const zoom = map.zoomView(view, 2, 0.25, 0.75, 800, 0.1, 3);
  assert.deepEqual(zoom, {x:200,y:350,width:400,height:200});
  const capped = map.zoomView(view, 100, 0.5, 0.5, 800, 0.1, 3);
  assert.equal(800 / capped.width, 3);
  assert.ok(Object.values(map.fitView({width:0,height:0},0,0)).every(Number.isFinite));
});
