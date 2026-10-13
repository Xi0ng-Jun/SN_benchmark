// Actual React 19 hook, in-memory compilation, controlled HTTP responses.
// No application build, daemon, preview state, scoring, or persistence.
import { chromium } from '@playwright/test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import ts from 'typescript';

const require = createRequire(import.meta.url);
const reactSource = (name, file) => readFileSync(join(dirname(require.resolve(name)), 'cjs', file), 'utf8');
const transpile = path => ts.transpileModule(readFileSync(new URL(path, import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const sources = {
  react: reactSource('react', 'react.production.js'),
  'react-dom': reactSource('react-dom', 'react-dom.production.js'),
  client: reactSource('react-dom/client', 'react-dom-client.production.js'),
  scheduler: reactSource('scheduler', 'scheduler.production.js'),
  '../contracts': transpile('../src/contracts.ts'),
  hook: process.env.WORKBENCH_COMPARISON_SOURCE
    ? ts.transpileModule(readFileSync(process.env.WORKBENCH_COMPARISON_SOURCE, 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
    }).outputText : transpile('../src/comparison/useTaskComparison.ts'),
};
const browser = await chromium.launch({ headless: true, executablePath: process.env.WORKBENCH_CHROMIUM });
const errors = []; let checks = 0;
try {
  const page = await browser.newPage();
  page.on('pageerror', error => errors.push(String(error)));
  await page.route('https://comparison-harness.invalid/**', route => route.fulfill({ body: '<div id="root"></div>', contentType: 'text/html' }));
  await page.goto('https://comparison-harness.invalid/');
  const install = async (modules, strict = false) => page.evaluate(({ modules, strict }) => {
    const cache = {};
    const load = id => {
      if (!cache[id]) {
        const m = { exports: {} }; cache[id] = m;
        new Function('require', 'module', 'exports', 'process', modules[id])(load, m, m.exports,
          { env: { NODE_ENV: strict ? 'development' : 'production' } });
      }
      return cache[id].exports;
    };
    const React = load('react'); const { createRoot } = load('client');
    const { flushSync } = load('react-dom'); const { useTaskComparison } = load('hook');
    const t = window.test = { storageWrites: 0, alerts: 0 };
    const ids = t.ids = ['1'.repeat(64), '2'.repeat(64), '3'.repeat(64)];
    const source = { dataset: 'QMSum', split: 'test', revision: 'synthetic', source_url: 'https://example.org/fixture', license: 'test' };
    const caseIds = ['case-1', 'case-2'];
    t.summary = id => {
      const index = ids.indexOf(id);
      return { id, suite: 'qmsum', scope: 'full', method: { name: 'synthetic-' + index, kind: 'reference',
        configuration: { comparison_category: 'controlled-rerun' }, model_identity: { model: 'synthetic-' + index },
        citation_style: 'none', input_policy: 'full-transcript' },
        case_ids: caseIds, case_count: 2, bundle_case_count: 2,
        coverage: { planned: 2, success: 2, no_answer: 0, clarification: 0, error: 0, missing: 0, generation_complete: true },
        metrics: { rougeL: { value: index === 0 ? 0 : .5, denominator: 2, kind: 'per-case' },
          mauve: { value: .2 + .1 * index, denominator: 2, kind: 'batch-only' } },
        pending_metrics: ['citation_support'], scores_id: (index + 4).toString().repeat(64),
        submission_id: (index + 7).toString().repeat(64), scorer_id: 'a'.repeat(64), profile: 'qmsum-perl-rouge-v1',
        source, source_hashes: { submission: 'b'.repeat(64), scores: 'c'.repeat(64) } };
    };
    t.taskOf = id => ({ id, title: 'Synthetic ' + id, goal: '', workspace_id: 'workspace', sessions: [], history: [],
      results: ids.map(id => ({ ...t.summary(id), original_at: null })) });
    t.payload = (taskId, left, right, status = 'comparable') => {
      const summaries = [t.summary(left), t.summary(right)];
      const sides = summaries.map(summary => ({ result_id: summary.id, status: 'readable', summary,
        read_reference: { kind: 'result', result_id: summary.id }, reason: null }));
      const methods = summaries.map((summary, index) => ({ method_id: (index ? 'e' : 'd').repeat(64),
        method: summary.method, metrics: Object.fromEntries(Object.entries(summary.metrics).map(([k, m]) => [k, m.value])),
        scores_id: summary.scores_id, submission_id: summary.submission_id,
        metric_denominators: { rougeL: 2, mauve: 2 }, metric_case_ids: { rougeL: caseIds, mauve: caseIds },
        pending_metrics: ['citation_support'], batch_only_metrics: ['mauve'], unshared_metrics: [], unavailable_metrics: [],
        files: { snapshot_id: summary.id, source_hashes: summary.source_hashes } }));
      // Official batch values intentionally differ from per-case estimators.
      const report = { format: 'benchmark-comparison-v1', report_id: 'f'.repeat(64), suite: 'qmsum', scope: 'full',
        case_count: 2, case_ids: caseIds, bundle_id: '0'.repeat(64), source, profile: 'qmsum-perl-rouge-v1', scorer_id: 'a'.repeat(64),
        metric_denominators: { rougeL: 2, mauve: 2 }, metric_case_ids: { rougeL: caseIds, mauve: caseIds },
        common_metrics: ['mauve', 'rougeL'], paired_metrics: ['rougeL'], methods,
        comparisons: [{ left_method_id: methods[0].method_id, right_method_id: methods[1].method_id,
          direction: 'right minus left', batch_differences: { rougeL: .5, mauve: .1 },
          paired_mean_differences: { rougeL: .1 },
          paired: caseIds.map((case_id, i) => ({ case_id, group_id: 'group-' + i, task: 'general',
            left_status: 'success', right_status: 'success', left: { rougeL: i ? .9 : .1 },
            right: { rougeL: i ? .9 : .3 }, differences: { rougeL: i ? 0 : .2 } })),
          group_differences: [{ group_id: 'group-0', case_count: 1, mean_differences: { rougeL: .2 } },
            { group_id: 'group-1', case_count: 1, mean_differences: { rougeL: 0 } }],
          uncertainty: { rougeL: { computed: true, group_count: 2, case_count: 2, mean: .1, ci95: [0, .2] } } }],
        method_differences: { model_identity: [{ method_id: methods[0].method_id, value: summaries[0].method.model_identity },
          { method_id: methods[1].method_id, value: summaries[1].method.model_identity }] },
        interpretation: ['Synthetic official batch values and paired estimates are separate.'] };
      return { task_id: taskId, left_result_id: left, right_result_id: right, left: sides[0], right: sides[1], status,
        reason: status === 'comparable' ? null : { code: 'comparison_rejected', message: '无法同题比较', detail: 'Different scorer_id' },
        report: status === 'comparable' ? report : null };
    };
    for (const method of ['setItem', 'removeItem', 'clear']) Storage.prototype[method] = () => { t.storageWrites++; throw Error('No storage writes allowed'); };
    window.alert = () => { t.alerts++; };
    window.fetch = (url, options) => {
      const parsed = new URL(url, location.origin);
      const request = { url: String(url), method: options?.method ?? 'GET', credentials: options?.credentials,
        headers: options?.headers, signal: options?.signal, body: options?.body };
      t.requests.push(request);
      const taskId = decodeURIComponent(parsed.pathname.split('/')[3]);
      const left = parsed.searchParams.get('left_result_id'), right = parsed.searchParams.get('right_result_id');
      const payload = structuredClone(t.payload(taskId, left, right));
      return new Promise((resolve, reject) => {
        request.reply = (value = payload, status = 200) => resolve(Response.json(value, { status }));
        request.fail = () => reject(Error('Synthetic network failure'));
        request.invalidJson = () => resolve(new Response('{', { status: 200 }));
      }); // Intentionally ignores AbortSignal, so obsolete replies still settle.
    };
    let root;
    const Test = () => { t.last = useTaskComparison(t.task); return React.createElement('div', null, 'Comparison hook'); };
    const tree = () => strict ? React.createElement(React.StrictMode, null, React.createElement(Test)) : React.createElement(Test);
    t.render = () => flushSync(() => root.render(tree()));
    t.switch = id => { t.task = id === null ? undefined : t.taskOf(id); t.render(); };
    t.unmount = () => flushSync(() => root.unmount());
    t.reset = () => { root?.unmount(); Object.assign(t, { task: t.taskOf('A'), requests: [], last: null });
      root = createRoot(document.getElementById('root')); t.render(); };
    t.reset();
  }, { modules, strict });
  await install(sources);
  const tick = () => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  const reset = async () => { await page.evaluate(() => window.test.reset()); await tick(); };
  const choose = async (side, index) => { await page.evaluate(({ side, index }) => window.test.last.select(side, index === null ? null : window.test.ids[index]), { side, index }); await tick(); };
  const pair = async () => { await choose('left', 0); await choose('right', 1); };
  const start = async () => { await page.evaluate(() => { void window.test.last.read(); }); await tick(); };
  const settled = () => page.waitForFunction(() => !window.test.last.busy);
  const verify = async (name, check) => { await check(); checks++; console.log('ok', name); };

  await reset(); await start();
  await verify('no automatic selection or reads', async () => {
    assert.deepEqual(await page.evaluate(() => [window.test.last.leftId, window.test.last.rightId]), [null, null]);
    assert.equal(await page.evaluate(() => window.test.requests.length), 0);
  });
  await choose('left', 0); await choose('right', 0);
  await page.evaluate(() => { window.test.last.select('right', 'unlinked'); void window.test.last.read(); });
  await verify('two distinct linked results required', async () => {
    assert.equal(await page.evaluate(() => window.test.last.rightId), null);
    assert.equal(await page.evaluate(() => window.test.requests.length), 0);
  });
  await choose('right', 1);
  await page.evaluate(() => { const t = window.test; t.oldRead = t.last.read; void t.last.read(); void t.last.read(); });
  await tick();
  await verify('explicit GET synchronous double-click lock', async () => {
    assert.equal(await page.evaluate(() => window.test.requests.length), 1);
    const request = await page.evaluate(() => { const r = window.test.requests[0]; return { url: r.url, method: r.method, credentials: r.credentials, headers: r.headers, body: r.body }; });
    assert.equal(request.method, 'GET'); assert.equal(request.credentials, 'same-origin');
    assert.equal(request.headers['X-Workbench'], '1'); assert.equal(request.body, undefined);
    const url = new URL(request.url, 'https://comparison-harness.invalid');
    assert.equal(url.pathname, '/api/tasks/A/comparison');
    assert.deepEqual([...url.searchParams.keys()].sort(), ['left_result_id', 'right_result_id']);
  });
  await page.evaluate(() => window.test.requests[0].reply()); await settled();
  await verify('official zero pending batch-only and independent pairing preserved', async () => {
    const data = await page.evaluate(() => window.test.last.data);
    assert.equal(data.status, 'comparable'); assert.equal(data.left.summary.metrics.rougeL.value, 0);
    assert.deepEqual(data.report.methods[0].pending_metrics, ['citation_support']);
    assert.deepEqual(data.report.methods[0].batch_only_metrics, ['mauve']);
    assert.equal(data.report.methods[0].metrics.mauve, .2);
    assert.equal(data.report.comparisons[0].batch_differences.rougeL, .5);
    assert.equal(data.report.comparisons[0].paired_mean_differences.rougeL, .1);
    assert.equal(data.report.comparisons[0].group_differences[0].mean_differences.rougeL, .2);
    assert.deepEqual(data.report.comparisons[0].uncertainty.rougeL.ci95, [0, .2]);
    assert.deepEqual(data, await page.evaluate(() => window.test.payload('A', window.test.ids[0], window.test.ids[1])));
  });
  await start(); await page.evaluate(() => window.test.requests[1].reply()); await settled();
  await verify('read-only explicit reread allowed', async () => assert.equal(await page.evaluate(() => window.test.requests.length), 2));

  await page.evaluate(() => { const t = window.test; t.oldRead = t.last.read; t.last.select('right', t.ids[2]); void t.oldRead(); });
  await tick();
  await verify('selection clears report and old read cannot issue prior choice', async () => {
    assert.equal(await page.evaluate(() => window.test.last.data), null);
    assert.equal(await page.evaluate(() => window.test.requests.length), 2);
  });

  for (const change of ['task ABA', 'choice ABA', 'cancel']) {
    await reset(); await pair(); await page.evaluate(() => { window.test.oldRead = window.test.last.read; void window.test.last.read(); }); await tick();
    if (change === 'task ABA') { await page.evaluate(() => window.test.switch('B')); await tick(); await page.evaluate(() => window.test.switch('A')); await tick(); await pair(); }
    else if (change === 'choice ABA') { await choose('right', 2); await choose('right', 1); }
    else { await page.evaluate(() => window.test.last.cancel()); await tick(); }
    await page.evaluate(() => { void window.test.oldRead(); void window.test.last.read(); }); await tick();
    await page.evaluate(() => window.test.requests[0].reply()); await tick();
    await verify('late reply and old handler isolated: ' + change, async () => {
      assert.equal(await page.evaluate(() => window.test.requests.length), 2);
      assert.equal(await page.evaluate(() => window.test.requests[0].signal.aborted), true);
      assert.equal(await page.evaluate(() => window.test.last.data), null);
      assert.equal(await page.evaluate(() => window.test.last.busy), true);
      await page.evaluate(() => { void window.test.last.read(); }); await tick();
      assert.equal(await page.evaluate(() => window.test.requests.length), 2, 'old finally must not release new lock');
    });
    await page.evaluate(() => window.test.requests[1].reply()); await settled();
    assert.equal(await page.evaluate(() => window.test.last.data.status), 'comparable');
  }

  await reset(); await pair(); await start(); await choose('right', 2); await start();
  await page.evaluate(() => window.test.requests[0].fail()); await tick();
  await verify('late rejection does not overwrite newer busy/error', async () => {
    assert.equal(await page.evaluate(() => window.test.last.error), null);
    assert.equal(await page.evaluate(() => window.test.last.busy), true);
  });
  await page.evaluate(() => window.test.requests[1].reply()); await settled();
  assert.equal(await page.evaluate(() => window.test.last.data.right_result_id), await page.evaluate(() => window.test.ids[2]));

  for (const redraw of [false, true]) {
    await reset(); await pair(); await start();
    await page.evaluate(redraw => { const t = window.test; t.oldRead = t.last.read; t.oldSelect = t.last.select;
      t.task.results = t.task.results.filter(r => r.id !== t.ids[1]); if (redraw) t.render();
      void t.oldRead(); t.oldSelect('right', t.ids[1]); t.requests[0].reply(); }, redraw);
    await tick();
    await verify('live association removal ' + (redraw ? 'rendered' : 'before redraw'), async () => {
      assert.equal(await page.evaluate(() => window.test.requests.length), 1);
      assert.equal(await page.evaluate(() => window.test.last.rightId), null);
      assert.equal(await page.evaluate(() => window.test.last.data), null);
      assert.equal(await page.evaluate(() => window.test.last.busy), false);
    });
  }
  await reset(); await pair();
  await page.evaluate(() => { const t = window.test; t.oldRead = t.last.read; t.last.cancel(); void t.oldRead(); t.switch(null); }); await tick();
  await verify('parent route invalidation before task redraw sends zero stale GET', async () => {
    assert.equal(await page.evaluate(() => window.test.requests.length), 0);
    assert.equal(await page.evaluate(() => window.test.last.canRead), false);
  });

  for (const status of ['not_comparable', 'unavailable']) {
    await reset(); await pair(); await start();
    await page.evaluate(status => { const t = window.test; const payload = t.payload('A', t.ids[0], t.ids[1], status);
      if (status === 'unavailable') { payload.right.status = 'unavailable'; payload.right.summary = null;
        payload.right.reason = { code: 'snapshot_unavailable', message: '快照不可用', detail: 'Imported result hash mismatch' }; }
      t.requests[0].reply(payload); }, status); await settled();
    await verify('failure DTO retains readable side: ' + status, async () => {
      assert.equal(await page.evaluate(() => window.test.last.data.status), status);
      assert.equal(await page.evaluate(() => window.test.last.data.left.summary.metrics.rougeL.value), 0);
      assert.equal(await page.evaluate(() => window.test.last.data.report), null);
      assert.equal(await page.evaluate(() => window.test.last.error), null);
    });
  }
  const mutations = [
    ['task binding', p => { p.task_id = 'B'; }],
    ['left binding', p => { p.left_result_id = p.right_result_id; }],
    ['side binding', p => { p.right.result_id = p.left_result_id; }],
    ['read reference', p => { p.right.read_reference.result_id = p.left_result_id; }],
    ['summary binding', p => { p.left.summary.id = p.right_result_id; }],
    ['missing report', p => { p.report = null; }],
    ['refusal report', p => { p.status = 'not_comparable'; p.reason = { code: 'x', message: 'x', detail: 'x' }; }],
    ['comparable unavailable side', p => { p.right.status = 'unavailable'; p.right.summary = null; p.right.reason = { code: 'x', message: 'x', detail: 'x' }; }],
    ['reason pairing', p => { p.reason = { code: 'x', message: 'x', detail: 'x' }; }],
    ['unknown status', p => { p.status = 'success'; }],
    ['array method kind', p => {
      for (const side of ['left', 'right']) p[side].summary.method.kind = ['reference'];
      for (const method of p.report.methods) method.method.kind = ['reference'];
    }],
    ['array summary scope on refusal', p => {
      p.status = 'not_comparable'; p.report = null; p.reason = { code: 'x', message: 'x', detail: 'x' };
      for (const side of ['left', 'right']) p[side].summary.scope = ['full'];
    }],
    ['array metric kind on refusal', p => {
      p.status = 'not_comparable'; p.report = null; p.reason = { code: 'x', message: 'x', detail: 'x' };
      p.left.summary.metrics.rougeL.kind = ['per-case'];
    }],
    ['array unavailable status', p => {
      p.status = ['unavailable']; p.report = null; p.reason = { code: 'x', message: 'x', detail: 'x' };
    }],
    ['summary metrics', p => { p.left.summary.metrics.rougeL.value = '0'; }],
    ['report metrics', p => { p.report.methods[0].metrics.rougeL = null; }],
    ['method identity', p => { p.report.methods[0].scores_id = p.right.summary.scores_id; }],
    ['report scope', p => { p.report.scope = 'subset'; }],
    ['missing group_differences', p => { p.report.comparisons[0].groups = p.report.comparisons[0].group_differences; delete p.report.comparisons[0].group_differences; }],
  ];
  for (const [name, mutate] of mutations) {
    await reset(); await pair(); await start();
    await page.evaluate(source => { const t = window.test; const p = t.payload('A', t.ids[0], t.ids[1]);
      new Function('p', '(' + source + ')(p)')(p); t.requests[0].reply(p); }, mutate.toString()); await settled();
    await verify('malformed DTO fails closed: ' + name, async () => {
      assert.equal(await page.evaluate(() => window.test.last.data), null);
      assert.equal(typeof await page.evaluate(() => window.test.last.error), 'string');
    });
  }
  for (const failure of ['fail', 'invalidJson', 'http']) {
    await reset(); await pair(); await start();
    await page.evaluate(failure => { const r = window.test.requests[0];
      if (failure === 'http') r.reply({ error: 'Synthetic HTTP failure' }, 403); else r[failure](); }, failure); await settled();
    await verify('transport error local and retryable: ' + failure, async () => {
      assert.equal(await page.evaluate(() => window.test.last.data), null);
      assert.equal(typeof await page.evaluate(() => window.test.last.error), 'string');
    });
    await start(); await page.evaluate(() => window.test.requests[1].reply()); await settled();
    assert.equal(await page.evaluate(() => window.test.last.data.status), 'comparable');
  }
  await reset(); await pair(); await start();
  await page.evaluate(() => { const t = window.test; t.abandoned = t.requests[0]; t.reset(); }); await tick();
  await pair(); await start(); await page.evaluate(() => window.test.abandoned.reply()); await tick();
  await verify('unmounted old response cannot affect successor', async () => {
    assert.equal(await page.evaluate(() => window.test.abandoned.signal.aborted), true);
    assert.equal(await page.evaluate(() => window.test.last.busy), true);
    assert.equal(await page.evaluate(() => window.test.last.data), null);
  });
  await page.evaluate(() => window.test.requests[0].reply()); await settled();
  await verify('no POST storage or global success signals', async () => {
    assert.equal(await page.evaluate(() => window.test.storageWrites), 0);
    assert.equal(await page.evaluate(() => window.test.alerts), 0);
    assert.equal(await page.evaluate(() => window.test.requests.some(r => r.method !== 'GET')), false);
  });

  await page.evaluate(() => window.test.unmount());
  await install({ ...sources, react: reactSource('react', 'react.development.js'),
    'react-dom': reactSource('react-dom', 'react-dom.development.js'), client: reactSource('react-dom/client', 'react-dom-client.development.js'),
    scheduler: reactSource('scheduler', 'scheduler.development.js') }, true);
  await tick(); await pair(); await start(); await page.evaluate(() => window.test.requests[0].reply()); await settled();
  await verify('React 19 development StrictMode effect replay', async () => {
    assert.equal(await page.evaluate(() => window.test.requests.length), 1);
    assert.equal(await page.evaluate(() => window.test.last.data.status), 'comparable');
  });
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ checks, react: '19.1.0', writes: 0 }));
} finally { await browser.close(); }
