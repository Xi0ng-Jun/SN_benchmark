// In-memory real React 19 + React Native Web rendering. Transport is the only
// controlled boundary; it deliberately settles obsolete/aborted requests.
// No app build, service, source file, user state, model, or browser ledger.
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from '@playwright/test';
import ts from 'typescript';

const require = createRequire(import.meta.url);
const app = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const hook = join(app, 'src/plans/usePlanDrafts.ts');
const editor = join(app, 'src/plans/PlanDraftEditor.tsx');
assert.ok(existsSync(hook), 'B03 must implement the actual plan hook before boundary checks can pass');
assert.ok(existsSync(editor), 'B03 must implement the controlled editor');
// Resolve a real CommonJS graph, replacing only the RN native entry with its
// web equivalents. No React/UI mocks and no generated files on disk.
const rn = '@harness/react-native';
const webRoot = dirname(require.resolve('react-native-web/package.json'));
const modules = {};
function collect(file) {
  if (modules[file]) return file;
  let source;
  if (file === rn) source = `module.exports=require(${JSON.stringify(join(webRoot, 'dist/cjs/index.js'))});`;
  else if (file.endsWith('.json')) source = 'module.exports=' + readFileSync(file, 'utf8');
  else {
    source = readFileSync(file, 'utf8');
    if (/\.tsx?$/.test(file) || /^\s*(?:import |export )/m.test(source)) source = ts.transpileModule(source, { compilerOptions: {
      target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true,
    } }).outputText;
  }
  modules[file] = { source, deps: {} };
  const local = file === rn ? require : createRequire(file);
  for (const match of source.matchAll(/require\(\s*['"]([^'"]+)['"]\s*\)/g)) {
    const specifier = match[1];
    const typed = specifier.startsWith('.') && ['.ts', '.tsx'].map(ext => resolve(dirname(file), specifier + ext)).find(existsSync);
    const dep = specifier === 'react-native' ? rn : specifier === 'react-native-svg' ? local.resolve('react-native-svg/lib/commonjs/elements.web.js') : typed || local.resolve(specifier === 'buffer' ? 'buffer/' : specifier);
    modules[file].deps[match[1]] = collect(dep);
  }
  return file;
}
const entries = Object.fromEntries(Object.entries({ React: require.resolve('react'), client: require.resolve('react-dom/client'),
  dom: require.resolve('react-dom'), hook, editor, ui: join(app, 'src/ui.tsx') }).map(([name, file]) => [name, collect(file)]));
const browser = await chromium.launch({ headless: true, executablePath: process.env.WORKBENCH_CHROMIUM });
const errors = []; let checks = 0;
try {
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  page.on('pageerror', error => errors.push(String(error)));
  await page.route('https://plans-harness.invalid/**', route => route.fulfill({ body: '<meta name="viewport" content="width=device-width,initial-scale=1"><div id="root"></div>', contentType: 'text/html' }));
  await page.goto('https://plans-harness.invalid/');
  const install = (page, development = false) => page.evaluate(({ modules, entries, development }) => {
    const cache = {};
    const load = file => {
      if (!cache[file]) {
        const m = { exports: {} }; cache[file] = m;
        const { source, deps } = modules[file];
        new Function('require', 'module', 'exports', 'process', source)(id => load(deps[id]), m, m.exports, { env: { NODE_ENV: development ? 'development' : 'production' } });
      }
      return cache[file].exports;
    };
    const React = load(entries.React), { createRoot } = load(entries.client), { flushSync } = load(entries.dom);
    const { usePlanDrafts } = load(entries.hook), { PlanDraftEditor } = load(entries.editor), { ThemeContext, themes } = load(entries.ui);
    const t = window.test = { storageWrites: 0, callbacks: [], alerts: 0 };
    t.input = (id = 'input-A', bundle = 'a'.repeat(64)) => ({ input_id: id, adapter: 'notebook-bundle', bundle_id: bundle,
      suite: t.suite, source_revision: 'sha256:' + 'b'.repeat(64), adaptation_revision: 'notebook-data-v3',
      case_ids: ['case-z', 'case-a', 'case-m'], partitions: [
        { partition_id: 'partition-z', case_ids: ['case-z', 'case-a'] }, { partition_id: 'partition-a', case_ids: ['case-m'] }] });
    t.taskOf = id => ({ id, title: 'Synthetic ' + id, goal: '', workspace_id: 'workspace-' + id, results: [], sessions: [], history: [] });
    t.record = (command, overrides = {}) => {
      const input = t.input(command.input_id, command.expected_bundle_id), chosen = new Set(command.case_ids);
      const { case_ids, partitions, ...identity } = input;
      return { plan_id: command.plan_id ?? 'new-plan', revision: 'revision-2', parent_revision: command.base_revision,
        task_id: 'A', workspace_id: 'workspace-A', actor: 'alice', title: command.title, state: 'draft', execution: false,
        content_hash: 'c'.repeat(64), saved_at: '2026-10-12T00:00:00Z', content: {
          format: 'sn-workbench-evaluation-plan-draft-v1', title: command.title, input: identity,
          selection: { kind: 'explicit', case_ids: case_ids.filter(id => chosen.has(id)),
            partition_ids: partitions.filter(part => part.case_ids.some(id => chosen.has(id))).map(part => part.partition_id), selection_hash: 'd'.repeat(64) },
          execution_intent: { entrypoint: 'scripts/run_notebook_benchmarks.py', mode: command.mode, request_revision: 'notebook-request-v3', model_services_ref: null, official_scorer_ref: null },
          source_intent: { kind: 'task-workspace-at-freeze', workspace_id: 'workspace-A', binding: 'not_captured' } }, ...overrides };
    };
    t.command = (overrides = {}) => ({ title: '  Preserve title  ', input_id: 'input-A', expected_bundle_id: 'a'.repeat(64),
      case_ids: ['case-m', 'case-z'], mode: 'chunk', plan_id: null, base_revision: null, request_key: 'example', ...overrides });
    for (const method of ['setItem', 'removeItem', 'clear']) Storage.prototype[method] = () => { t.storageWrites++; throw Error('No browser ledger'); };
    window.alert = () => { t.alerts++; };
    window.fetch = (url, options) => new Promise((resolve, reject) => {
      const r = { url: String(url), method: options?.method ?? 'GET', body: options?.body ? JSON.parse(options.body) : undefined,
        rawBody: options?.body, signal: options?.signal, headers: options?.headers, credentials: options?.credentials,
        reply: (value, status = 200) => resolve(Response.json(value, { status })), fail: () => reject(Error('Lost response')),
        invalidJson: () => resolve(new Response('{', { status: 200 })) };
      t.requests.push(r);
    });
    let root;
    const Test = () => {
      React.useEffect(() => { t.effectSetups++; return () => { t.effectCleanups++; }; }, []);
      t.last = usePlanDrafts(t.task, t.actor, t.inputs, record => t.callbacks.push({ task: t.task?.id, actor: t.actor, record }));
      return React.createElement(ThemeContext.Provider, { value: themes.dark }, React.createElement(PlanDraftEditor, { controller: t.last }));
    };
    t.render = () => flushSync(() => root.render(development
      ? React.createElement(React.StrictMode, null, React.createElement(Test)) : React.createElement(Test)));
    t.switch = (id, actor = t.actor) => { t.task = id === null ? undefined : t.taskOf(id); t.actor = actor; t.render(); };
    t.unmount = () => flushSync(() => root.unmount());
    t.reset = () => {
      if (root) flushSync(() => root.unmount());
      Object.assign(t, { task: t.taskOf('A'), actor: 'alice', suite: 'qmsum', effectSetups: 0, effectCleanups: 0, inputs: [
        { id: 'input-A', title: 'Input A', description: 'Complete canonical A' },
        { id: 'input-B', title: 'Input B', description: 'Complete canonical B' }], requests: [], callbacks: [] });
      root = createRoot(document.getElementById('root')); t.render();
    };
    t.reset();
  }, { modules, entries, development });
  await install(page);
  const tick = () => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  const run = async fn => { await page.evaluate(fn); await tick(); };
  const verify = async (name, fn) => { await fn(); checks++; console.log('ok', name); };
  const requests = () => page.evaluate(() => window.test.requests.map(r => ({ url: r.url, method: r.method, body: r.body })));
  const snapshot = () => page.evaluate(() => { const c = window.test.last; return { draft: c.draft, binding: c.binding, status: c.status,
    busy: c.busy, locked: c.locked, error: c.error, saved: c.saved, selected: c.selected, descriptor: c.descriptor, heads: c.heads, revisions: c.revisions }; });
  const reset = async () => { await run(() => window.test.reset()); await run(() => window.test.requests[0]?.reply([])); };
  const prepare = async () => {
    await reset();
    await run(() => { const c = window.test.last; c.setTitle('  Preserve title  '); c.setCaseIdsText('case-m\ncase-z'); c.selectInput('input-A'); });
    await run(() => window.test.requests.at(-1).reply(window.test.input()));
  };
  const save = () => run(() => { void window.test.last.save(); });
  const accept = () => run(() => { const t = window.test, r = t.requests.at(-1); r.reply({ request_key: r.body.request_key, record: t.record(r.body) }); });
  const lost = async () => { await prepare(); await save(); await run(() => window.test.requests.at(-1).fail()); };

  await reset();
  await verify('explicit empty initial input; only server heads GET', async () => {
    assert.equal((await snapshot()).draft.inputId, null);
    assert.deepEqual((await requests()).map(r => [r.method, r.url]), [['GET', '/api/tasks/A/plans']]);
  });
  await prepare();
  await run(() => { const t = window.test, c = t.last; t.old = c; void c.save(); void c.save(); c.newDraft(); c.setTitle('intruder'); c.selectInput('input-B'); void t.old.save(); });
  await verify('synchronous lock resists double click, stale handler, edit and new-draft before redraw', async () => {
    const rs = await requests(); assert.equal(rs.filter(r => r.method === 'POST').length, 1);
    assert.equal((await snapshot()).draft.title, '  Preserve title  ');
    assert.equal((await snapshot()).draft.inputId, 'input-A');
    const body = rs.at(-1).body;
    assert.deepEqual(Object.keys(body).sort(), ['title','input_id','expected_bundle_id','case_ids','mode','plan_id','base_revision','request_key'].sort());
    assert.deepEqual(body.case_ids, ['case-m', 'case-z']); assert.equal(body.title, '  Preserve title  ');
    assert.equal(body.plan_id, null); assert.equal(body.base_revision, null);
  });
  await accept();
  await verify('strict successful envelope binds full command in canonical case/partition order', async () => {
    const s = await snapshot(); assert.equal(s.status, 'saved'); assert.equal(s.saved.execution, false);
    assert.deepEqual(s.saved.content.selection.case_ids, ['case-z', 'case-m']);
    assert.deepEqual(s.saved.content.selection.partition_ids, ['partition-z', 'partition-a']);
    assert.equal(s.binding.baseRevision, 'revision-2');
    assert.equal(await page.evaluate(() => window.test.callbacks.length), 1);
    assert.match(await page.getByTestId('plan-status').innerText(), /已保存草稿/);
  });
  await run(() => { void window.test.old.save(); void window.test.last.save(); });
  assert.equal((await requests()).filter(r => r.method === 'POST').length, 1);

  for (const value of ['', 'case-z\n', 'case-z\ncase-z', ' case-z', 'case-z case-m', 'unknown']) {
    await prepare(); await page.evaluate(value => window.test.last.setCaseIdsText(value), value); await save();
    await verify('strict explicit selection rejects without trimming/dedup: ' + JSON.stringify(value), async () => {
      assert.equal((await requests()).filter(r => r.method === 'POST').length, 0); assert.ok((await snapshot()).error);
    });
  }
  await lost();
  const frozen = (await requests()).at(-1).body;
  await run(() => { const c = window.test.last; c.setTitle('changed'); c.newDraft(); c.openHead('other'); void c.save(); });
  await verify('lost response freezes body/key; ordinary save cannot POST again', async () => {
    assert.equal((await snapshot()).status, 'unknown'); assert.equal((await snapshot()).locked, true);
    assert.equal((await snapshot()).draft.title, frozen.title); assert.equal((await requests()).length, 3);
  });
  await run(() => { void window.test.last.checkReceipt(); void window.test.last.checkReceipt(); });
  await run(() => { const t = window.test; t.requests.at(-1).reply({ request_key: t.requests[2].body.request_key, record: null }); });
  await verify('explicit receipt GET only, null is unobserved and keeps intent locked', async () => {
    const rs = await requests(); assert.equal(rs.length, 4); assert.equal(rs[3].method, 'GET');
    assert.ok(rs[3].url.endsWith('/receipt?request_key=' + encodeURIComponent(frozen.request_key)));
    assert.equal((await snapshot()).status, 'unknown'); assert.equal((await snapshot()).locked, true);
  });
  await run(() => { void window.test.last.retrySave(); void window.test.last.retrySave(); });
  await verify('only explicit retry POSTs exact same frozen key/body', async () => assert.deepEqual((await requests()).at(-1).body, frozen));
  await accept(); assert.equal((await snapshot()).status, 'saved');

  for (const method of ['POST', 'GET']) {
    for (const corruption of ['wrong-key', 'bare-record', 'null-record', 'duplicate-case', 'wrong-cases', 'wrong-partitions', 'input', 'actor', 'task', 'workspace', 'title', 'mode', 'parent', 'execution', 'unknown-schema', 'private-revision', 'extra-content', 'hash-only']) {
      await lost();
      if (method === 'GET') await run(() => { void window.test.last.checkReceipt(); });
      else await run(() => { void window.test.last.retrySave(); });
      await page.evaluate(({ method, corruption }) => {
        const t = window.test, command = t.requests[2].body, record = t.record(command);
        let value = { request_key: command.request_key, record };
        if (corruption === 'wrong-key') value.request_key = 'another-key';
        if (corruption === 'bare-record') value = record;
        if (corruption === 'null-record') value.record = null;
        if (corruption === 'duplicate-case') record.content.selection.case_ids = ['case-z', 'case-z'];
        if (corruption === 'wrong-cases' || corruption === 'hash-only') record.content.selection.case_ids = ['case-z'];
        if (corruption === 'wrong-partitions') record.content.selection.partition_ids = ['partition-z'];
        if (corruption === 'input') record.content.input.bundle_id = 'e'.repeat(64);
        if (corruption === 'actor') record.actor = 'bob';
        if (corruption === 'task') record.task_id = 'B';
        if (corruption === 'workspace') record.workspace_id = record.content.source_intent.workspace_id = 'workspace-B';
        if (corruption === 'title') record.title = record.content.title = record.title.trim();
        if (corruption === 'mode') record.content.execution_intent.mode = 'reasoning';
        if (corruption === 'parent') record.parent_revision = 'old-revision';
        if (corruption === 'execution') record.execution = true;
        if (corruption === 'unknown-schema') record.content.format = 'future-v2';
        if (corruption === 'private-revision') record.content.input.source_revision = '/private/source';
        if (corruption === 'extra-content') record.content.input.gold = 'secret';
        t.requests.at(-1).reply(value);
      }, { method, corruption }); await tick();
      await verify(`${method} envelope/record rejects ${corruption} without misreporting saved`, async () => {
        const s = await snapshot(); assert.equal(s.status, 'unknown'); assert.equal(s.saved, null); assert.equal(s.locked, true);
        assert.equal(await page.evaluate(() => window.test.callbacks.length), 0);
      });
    }
  }

  for (const status of [404, 409, 422]) {
    await prepare(); await save(); const original = (await requests()).at(-1).body;
    await page.evaluate(status => { const r = window.test.requests.at(-1); r.reply({ error: 'Rejected original command', code: 'PLAN_SAVE_REJECTED', request_key: r.body.request_key }, status); }, status); await tick();
    await verify('typed rollback rejection ' + status + ' unlocks correction, preserves body and original key until edit', async () => {
      assert.equal((await snapshot()).status, 'rejected'); assert.equal((await snapshot()).locked, false);
      assert.equal((await snapshot()).draft.title, original.title);
      await save(); assert.deepEqual((await requests()).at(-1).body, original);
      await page.evaluate(status => { const r = window.test.requests.at(-1); r.reply({ error: 'Rejected', code: 'PLAN_SAVE_REJECTED', request_key: r.body.request_key }, status); }, status); await tick();
      await run(() => { window.test.last.setTitle('Corrected title'); }); await save();
      const corrected = (await requests()).at(-1).body;
      assert.notEqual(corrected.request_key, original.request_key); assert.equal(corrected.title, 'Corrected title');
      await accept(); assert.equal((await snapshot()).status, 'saved');
    });
  }
  for (const [status, code, key] of [[500,'PLAN_SAVE_REJECTED','same'], [409,'GENERIC','same'], [409,'PLAN_SAVE_REJECTED','wrong']]) {
    await prepare(); await save(); await page.evaluate(({ status, code, key }) => { const r = window.test.requests.at(-1);
      r.reply({ error: '/private/credential: must not display proxy body', code, request_key: key === 'same' ? r.body.request_key : 'other' }, status); }, { status, code, key }); await tick();
    await verify(`generic error ${status}/${code}/${key} conservatively retains intent`, async () => {
      assert.equal((await snapshot()).status, 'unknown'); assert.equal((await snapshot()).locked, true);
      assert.doesNotMatch(await page.getByTestId('plans-panel').innerText(), /credential/);
    });
  }

  for (const change of ['task ABA', 'actor ABA', 'workspace', 'deactivate', 'unmount']) {
    await prepare(); await save();
    await page.evaluate(change => { const t = window.test; t.old = t.last;
      if (change === 'task ABA') { t.switch('B'); t.switch('A'); }
      if (change === 'actor ABA') { t.switch('A', 'bob'); t.switch('A', 'alice'); }
      if (change === 'workspace') { t.task.workspace_id = 'changed'; t.render(); }
      if (change === 'deactivate') t.last.deactivate();
      if (change === 'unmount') t.unmount();
      const r = t.requests[2]; r.reply({ request_key: r.body.request_key, record: t.record(r.body) });
      void t.old.save(); void t.old.checkReceipt(); t.old.setTitle('stale');
    }, change); await tick();
    await verify('obsolete save and handlers isolated on ' + change, async () => {
      assert.equal(await page.evaluate(() => window.test.callbacks.length), 0);
      assert.equal((await requests()).filter(r => r.method === 'POST').length, 1);
      if (change !== 'unmount') { assert.equal((await snapshot()).saved, null); assert.notEqual((await snapshot()).draft.title, 'stale'); }
    });
    if (change.endsWith('ABA')) {
      await run(() => { void window.test.last.checkReceipt(); });
      await run(() => { const t = window.test, c = t.requests[2].body; t.requests.at(-1).reply({ request_key: c.request_key, record: t.record(c) }); });
      assert.equal((await snapshot()).status, 'saved');
    }
  }

  await reset();
  await run(() => { const c = window.test.last; c.selectInput('input-A'); c.selectInput('input-B'); });
  await run(() => { const t = window.test; t.requests[1].reply(t.input()); });
  await verify('late describe cannot overwrite current input or unlock newest read', async () => {
    assert.equal((await snapshot()).draft.inputId, 'input-B'); assert.equal((await snapshot()).descriptor, null);
  });
  await run(() => window.test.requests[2].reply(window.test.input('input-B')));
  assert.equal((await snapshot()).descriptor.input_id, 'input-B');

  await reset();
  await run(() => { const t = window.test; t.old = t.last; void t.last.refreshHeads(); t.switch('B'); t.switch('A');
    t.requests[1].reply([t.record(t.command())]); void t.old.refreshHeads(); });
  await verify('late list and old list handler cannot populate ABA heads', async () => assert.deepEqual((await snapshot()).heads, []));

  await reset();
  await run(() => { const t = window.test; t.task.history = [{ kind: 'plan.draft.saved', object_id: 'existing', content: {
    plan_id: 'existing', revision: 'revision-1', title: 'UNTRUSTED HISTORY BODY', content: { arbitrary: true } } }]; t.render(); });
  await verify('history supplies fixed locators only', async () => {
    assert.deepEqual((await snapshot()).revisions, [{ planId: 'existing', revision: 'revision-1' }]);
    assert.equal((await snapshot()).draft.title, '');
  });
  await run(() => { void window.test.last.openRevision('existing', 'revision-1'); });
  await run(() => { const t = window.test; t.requests.at(-1).reply(t.record(t.command({ plan_id: 'existing', base_revision: null }), { revision: 'revision-1' })); });
  await run(() => window.test.requests.at(-1).reply(window.test.input()));
  await verify('fixed GET adopts frozen old body and old base explicitly', async () => {
    const s = await snapshot(); assert.equal(s.binding.kind, 'fixed'); assert.equal(s.binding.baseRevision, 'revision-1');
    assert.equal(s.selected.plan_id, 'existing'); assert.equal(s.selected.revision, 'revision-1');
    assert.equal(s.saved, null, 'fixed reading is separate from the current command save receipt');
    assert.equal(s.draft.title, '  Preserve title  ');
    assert.match(await page.getByTestId('plans-panel').innerText(), /固定旧版本/);
    assert.ok((await requests()).some(r => r.url === '/api/tasks/A/plans/existing?revision=revision-1'));
  });
  await run(() => { void window.test.last.refreshHeads(); });
  await run(() => { const t = window.test; t.requests.at(-1).reply([t.record(t.command({ plan_id: 'existing', base_revision: 'revision-1' }), { revision: 'revision-9' })]); });
  await save();
  await verify('newer list head never silently replaces fixed base; server CAS remains decisive', async () => {
    assert.equal((await requests()).at(-1).body.base_revision, 'revision-1');
    assert.equal((await requests()).at(-1).body.plan_id, 'existing');
    assert.equal((await snapshot()).selected.revision, 'revision-1');
  });
  await run(() => { const r = window.test.requests.at(-1); r.reply({ error: 'CAS conflict', code: 'PLAN_SAVE_REJECTED', request_key: r.body.request_key }, 409); });
  assert.equal((await snapshot()).binding.baseRevision, 'revision-1');

  await reset(); await run(() => { void window.test.last.openHead('existing'); });
  await run(() => { const t = window.test; t.requests.at(-1).reply(t.record(t.command({ plan_id: 'existing' }), { revision: 'revision-old' })); });
  await run(() => window.test.requests.at(-1).reply(window.test.input('input-A', 'e'.repeat(64))));
  await save();
  await verify('changed descriptor never rebases old frozen bundle identity', async () => {
    assert.equal((await requests()).filter(r => r.method === 'POST').length, 0);
    assert.equal((await snapshot()).descriptor, null); assert.ok((await snapshot()).error);
  });

  await reset(); await run(() => { void window.test.last.openRevision('existing', 'revision-1'); });
  await run(() => { const t = window.test, record = t.record(t.command({ plan_id: 'existing' }), { revision: 'revision-1' });
    record.content.selection.partition_ids = ['partition-z']; t.requests.at(-1).reply(record); });
  await run(() => window.test.requests.at(-1).reply(window.test.input()));
  await save();
  await verify('catalogue verifies the loaded revision derived partitions before allowing append', async () => {
    assert.equal((await snapshot()).descriptor, null);
    assert.equal((await requests()).filter(r => r.method === 'POST').length, 0);
  });

  await prepare();
  await run(() => { const t = window.test; t.task.id = 'B'; t.last.setTitle('stale route mutation'); void t.last.save(); });
  await verify('real handler detects task mutation before parent redraw and publishes the new binding', async () => {
    assert.equal((await requests()).filter(r => r.method === 'POST').length, 0);
    assert.notEqual((await snapshot()).draft.title, '  Preserve title  ');
    assert.notEqual((await snapshot()).draft.title, 'stale route mutation');
  });

  await reset(); await run(() => { const t = window.test; void t.last.openRevision('existing', 'revision-1'); t.switch('B'); t.switch('A');
    t.requests[1].reply(t.record(t.command({ plan_id: 'existing' }), { revision: 'revision-1' })); });
  await verify('late fixed GET cannot overwrite current unsaved draft on ABA', async () => assert.equal((await snapshot()).draft.title, ''));

  await reset(); await run(() => { const t = window.test; t.inputs = []; t.render(); t.last.selectInput('not-registered'); void t.last.save(); });
  await verify('empty registration is usable without input/path fallback or POST', async () => {
    assert.equal((await snapshot()).draft.inputId, null); assert.equal((await requests()).filter(r => r.method === 'POST').length, 0);
    assert.match(await page.getByTestId('plans-panel').innerText(), /未登记/);
  });

  await lost(); await run(() => { const t = window.test; t.inputs = []; t.render(); void t.last.checkReceipt(); });
  await run(() => { const t = window.test, c = t.requests[2].body; t.requests.at(-1).reply({ request_key: c.request_key, record: t.record(c) }); });
  await verify('receipt recovery uses frozen catalogue after live registration disappears', async () => {
    assert.equal((await snapshot()).status, 'saved');
    assert.equal((await requests()).length, 4); assert.equal((await requests()).at(-1).method, 'GET');
  });

  await lost(); await run(() => { void window.test.last.checkReceipt(); });
  await run(() => { const t = window.test; t.old = t.last; t.switch('B'); t.switch('A'); void t.last.retrySave();
    const c = t.requests[2].body; t.requests[3].reply({ request_key: c.request_key, record: t.record(c) });
    t.last.newDraft(); t.last.setTitle('checking race'); void t.old.retrySave(); });
  await verify('late receipt finally cannot release a newer same-key retry lock after ABA', async () => {
    assert.equal((await snapshot()).status, 'saving'); assert.equal((await snapshot()).busy, true);
    assert.equal((await snapshot()).draft.title, '  Preserve title  ');
    assert.equal((await requests()).filter(r => r.method === 'POST').length, 2);
  });
  await accept(); assert.equal((await snapshot()).status, 'saved');

  await prepare(); await run(() => { const c = window.test.last; c.setTitle('latest ref title'); c.setMode('reasoning'); void c.save(); });
  await verify('pre-render handler reads latest title/mode body, not a captured React snapshot', async () => {
    const c = (await requests()).at(-1).body; assert.equal(c.title, 'latest ref title'); assert.equal(c.mode, 'reasoning');
  });
  await accept();
  await run(() => { const t = window.test; t.last.setTitle('Unsaved A'); t.last.setCaseIdsText('case-a'); t.switch('B');
    t.last.setTitle('Unsaved B'); t.switch('A'); });
  await verify('task navigation preserves each local unsaved body without cross-scope callbacks', async () => {
    assert.equal((await snapshot()).draft.title, 'Unsaved A'); assert.equal((await snapshot()).draft.caseIdsText, 'case-a');
  });

  for (const defect of ['outside', 'overlap', 'missing', 'duplicate', 'extra-private', 'wrong-input']) {
    await reset(); await run(() => window.test.last.selectInput('input-A'));
    await page.evaluate(defect => { const t = window.test, input = t.input();
      if (defect === 'outside') input.partitions[0].case_ids.push('foreign');
      if (defect === 'overlap') input.partitions[1].case_ids.push('case-z');
      if (defect === 'missing') input.partitions.pop();
      if (defect === 'duplicate') input.case_ids.push('case-z');
      if (defect === 'extra-private') input.rawcase = 'PRIVATE RAW';
      if (defect === 'wrong-input') input.input_id = 'input-B';
      t.requests.at(-1).reply(input);
    }, defect); await tick();
    await verify('runtime catalogue rejects ' + defect + ' without publishing it', async () => {
      assert.equal((await snapshot()).descriptor, null); assert.ok((await snapshot()).error);
      assert.doesNotMatch(await page.getByTestId('plans-panel').innerText(), /PRIVATE RAW/);
    });
  }

  await prepare(); await save(); await run(() => window.test.requests.at(-1).invalidJson());
  await verify('malformed HTTP JSON keeps the exact original intent unknown', async () => {
    assert.equal((await snapshot()).status, 'unknown'); assert.equal((await snapshot()).locked, true);
  });

  await reset(); await page.getByTestId('plan-title').fill('Browser edited title');
  if (!(await page.getByTestId('plan-case-ids').isVisible())) await page.getByTestId('plan-paste-toggle').click();
  await page.getByTestId('plan-case-ids').fill('case-m\ncase-z'); await page.getByTestId('input-input-A').click(); await tick();
  await run(() => window.test.requests.at(-1).reply(window.test.input()));
  await page.getByRole('button', { name: 'reasoning', exact: true }).click(); await tick();
  await page.getByTestId('plan-save').click(); await tick();
  await verify('real React19/RN web inputs and buttons use the controlled body and frozen transport', async () => {
    const c = (await requests()).at(-1).body; assert.equal(c.title, 'Browser edited title'); assert.equal(c.mode, 'reasoning');
    assert.deepEqual(c.case_ids, ['case-m', 'case-z']);
  });
  await accept();
  await verify('saved immutable server record also becomes the selected fixed detail', async () => {
    const s = await snapshot(); assert.deepEqual(s.selected, s.saved); assert.equal(s.selected.revision, s.binding.baseRevision);
  });
  await prepare();
  await run(() => { const t = window.test; t.inputs[0].title = '长来源标题'.repeat(90); t.render(); });
  await page.getByTestId('plan-new').click(); await tick();
  await verify('new draft resets binding; dark 390px editor exposes only plan intent', async () => {
    const s = await snapshot(); assert.equal(s.binding.kind, 'new'); assert.equal(s.binding.planId, null); assert.equal(s.draft.inputId, null);
    assert.equal(s.selected, null);
    const panel = page.getByTestId('plans-panel');
    assert.match(await panel.innerText(), /未捕获/); assert.match(await panel.innerText(), /model\/scorer 未选择/);
    assert.match(await panel.innerText(), /execution=false/); assert.doesNotMatch(await panel.innerText(), /ready|运行|启动|job|gold|rawcase/i);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= 390), true);
    for (const id of ['plan-title','plan-case-ids','plan-save','plan-check','plan-new','plan-status','input-input-A']) assert.equal(await page.getByTestId(id).count(), 1);
    assert.equal(await page.evaluate(() => window.test.storageWrites), 0); assert.equal(await page.evaluate(() => window.test.alerts), 0);
    const rs = await requests(); assert.ok(rs.every(r => /\/api\/(inputs\/|tasks\/[^/]+\/plans)/.test(r.url)));
  });

  // JSON type confusion must be stopped before publication, on both input and
  // stored-record boundaries. Coercing an array to a suite name is unsafe even
  // if the remaining fields match a valid DTO exactly.
  for (const suite of [['qmsum'], null, 12, true, { name: 'qmsum' }]) {
    await reset(); await run(() => { const c = window.test.last; c.setTitle('Invalid suite'); c.setCaseIdsText('case-z'); c.selectInput('input-A'); });
    await page.evaluate(suite => { const t = window.test; t.requests.at(-1).reply({ ...t.input(), suite }); }, suite); await tick();
    await save();
    await verify('non-string descriptor suite never publishes or POSTs: ' + JSON.stringify(suite), async () => {
      assert.equal((await snapshot()).descriptor, null); assert.equal((await requests()).filter(r => r.method === 'POST').length, 0);
    });
    await reset(); await run(() => { void window.test.last.openRevision('existing', 'revision-1'); });
    await page.evaluate(suite => { const t = window.test, r = t.record(t.command({ plan_id: 'existing' }), { revision: 'revision-1' });
      r.content.input.suite = suite; t.requests.at(-1).reply(r); }, suite); await tick();
    await verify('non-string fixed record suite never becomes selected: ' + JSON.stringify(suite), async () => {
      assert.equal((await snapshot()).selected, null); assert.equal((await snapshot()).draft.title, '');
    });
  }

  // Python len(str) counts code points, including combining marks separately.
  // Keep original spelling; neither normalization nor UTF-16 budgets are valid.
  for (const title of ['😀'.repeat(300), '😀'.repeat(500), 'a'.repeat(499) + '😀', '😀'.repeat(498) + 'e\u0301']) {
    await prepare(); await page.evaluate(title => window.test.last.setTitle(title), title); await save();
    await verify('domain-valid Unicode title saves unchanged at ' + [...title].length + ' code points', async () => {
      assert.equal((await requests()).at(-1).method, 'POST'); assert.equal((await requests()).at(-1).body.title, title);
    });
    await accept(); assert.equal((await snapshot()).saved.title, title);
    await reset(); await run(() => { void window.test.last.openRevision('existing', 'revision-1'); });
    await page.evaluate(title => { const t = window.test; t.requests.at(-1).reply(t.record(t.command({ plan_id: 'existing', title }), { revision: 'revision-1' })); }, title); await tick();
    await verify('domain-valid Unicode title remains fixed-readable at ' + [...title].length + ' code points', async () => {
      assert.equal((await snapshot()).selected.title, title); assert.equal((await snapshot()).draft.title, title);
    });
    await reset(); await run(() => { void window.test.last.refreshHeads(); });
    await page.evaluate(title => { const t = window.test; t.requests.at(-1).reply([
      t.record(t.command(), { plan_id: 'ordinary' }), t.record(t.command({ title }), { plan_id: 'unicode' })]); }, title); await tick();
    await verify('legal Unicode record does not poison a mixed heads list at ' + [...title].length + ' code points', async () => {
      assert.equal((await snapshot()).heads.length, 2); assert.equal((await snapshot()).heads[1].title, title);
      assert.equal(await page.evaluate(() => window.test.last.listError), null);
    });
  }
  for (const title of ['😀'.repeat(501), '😀'.repeat(499) + 'e\u0301']) {
    await prepare(); await page.evaluate(title => window.test.last.setTitle(title), title); await save();
    await verify('501 code points exceeds the unchanged domain title budget', async () => {
      assert.equal((await requests()).filter(r => r.method === 'POST').length, 0);
      assert.equal((await snapshot()).draft.title, title);
    });
  }
  for (const count of [200, 201]) {
    await reset(); await page.evaluate(count => { const t = window.test; t.actor = '😀'.repeat(count); t.render(); }, count); await tick();
    if (count === 200) {
      await run(() => window.test.requests.at(-1).reply([]));
      await run(() => { const c = window.test.last; c.setTitle('Unicode actor'); c.setCaseIdsText('case-z'); c.selectInput('input-A'); });
      await run(() => window.test.requests.at(-1).reply(window.test.input())); await save();
      await run(() => { const t = window.test, r = t.requests.at(-1); r.reply({ request_key: r.body.request_key, record: t.record(r.body, { actor: t.actor }) }); });
      await verify('200-codepoint actor activates and binds the unchanged real identity', async () => {
        assert.equal((await snapshot()).status, 'saved'); assert.equal((await snapshot()).saved.actor, '😀'.repeat(200));
      });
    } else await verify('201-codepoint actor cannot activate a save scope', async () => {
      assert.equal((await snapshot()).locked, true); assert.equal((await snapshot()).selected, null);
      assert.equal((await requests()).filter(r => r.method === 'POST').length, 0);
    });
  }

  // Enumerated by Python str.isspace() (Unicode Zs or bidi WS/B/S),
  // independently of JS trim()/\s. In particular BOM is text, while NEL
  // and the four information separators are whitespace. Preserve all bytes.
  const pythonSpaces = [9, 10, 11, 12, 13, 28, 29, 30, 31, 32, 133, 160, 5760,
    8192, 8193, 8194, 8195, 8196, 8197, 8198, 8199, 8200, 8201, 8202, 8232, 8233, 8239, 8287, 12288];
  for (const cp of pythonSpaces) {
    const space = String.fromCodePoint(cp), label = 'U+' + cp.toString(16);
    await prepare(); await page.evaluate(space => window.test.last.setTitle(space), space); await save();
    await verify('Python whitespace-only title blocked unchanged: ' + label, async () => {
      assert.equal((await requests()).filter(r => r.method === 'POST').length, 0);
      assert.equal((await snapshot()).draft.title, space);
    });
    const title = space + 'Keep original text' + space;
    await page.evaluate(title => window.test.last.setTitle(title), title); await save();
    await verify('Python whitespace around nonempty title preserved: ' + label, async () => {
      assert.equal((await requests()).at(-1).body.title, title);
    });
    await reset(); await run(() => window.test.last.selectInput('input-A'));
    await page.evaluate(space => {
      const t = window.test, input = t.input(), cid = 'case' + space + 'id';
      input.case_ids = [cid]; input.partitions = [{ partition_id: 'partition', case_ids: [cid] }];
      t.requests.at(-1).reply(input);
    }, space); await tick();
    await verify('Python whitespace inside catalogue case ID rejected: ' + label, async () => assert.equal((await snapshot()).descriptor, null));
    await reset(); await run(() => { void window.test.last.openRevision('existing', 'revision-1'); });
    await page.evaluate(space => {
      const t = window.test, r = t.record(t.command({ plan_id: 'existing' }), { revision: 'revision-1' });
      r.content.selection.partition_ids = ['partition' + space + 'id']; t.requests.at(-1).reply(r);
    }, space); await tick();
    await verify('Python whitespace inside fixed partition ID rejected: ' + label, async () => assert.equal((await snapshot()).selected, null));
  }
  for (const title of ['\ufeff', ' \ufeff ', '\u200b', '\u180e', '\u001b', '\u007f']) {
    await prepare(); await page.evaluate(title => window.test.last.setTitle(title), title); await save();
    await verify('Python non-whitespace text accepted unchanged: ' + JSON.stringify(title), async () => {
      assert.equal((await requests()).at(-1).method, 'POST'); assert.equal((await requests()).at(-1).body.title, title);
    });
    await accept(); assert.equal((await snapshot()).saved.title, title);
    await reset(); await run(() => { void window.test.last.openRevision('existing', 'revision-1'); });
    await page.evaluate(title => { const t = window.test; t.requests.at(-1).reply(
      t.record(t.command({ plan_id: 'existing', title }), { revision: 'revision-1' })); }, title); await tick();
    await verify('Python non-whitespace fixed text readable unchanged: ' + JSON.stringify(title), async () => assert.equal((await snapshot()).selected.title, title));
    await reset(); await run(() => { void window.test.last.refreshHeads(); });
    await page.evaluate(title => { const t = window.test; t.requests.at(-1).reply([
      t.record(t.command(), { plan_id: 'ordinary' }), t.record(t.command({ title }), { plan_id: 'nonspace' })]); }, title); await tick();
    await verify('Python non-whitespace record keeps mixed heads readable: ' + JSON.stringify(title), async () => {
      assert.equal((await snapshot()).heads.length, 2); assert.equal((await snapshot()).heads[1].title, title);
      assert.equal(await page.evaluate(() => window.test.last.listError), null);
    });
  }
  // BOM is also legal inside a Python domain ID: it cannot be trimmed away.
  await reset();
  await run(() => { const t = window.test; t.inputs = [{ id: 'input-\ufeff', title: 'BOM identity' }]; t.render();
    t.last.selectInput('input-\ufeff'); t.last.setTitle('BOM case identity'); t.last.setCaseIdsText('case-\ufeff'); });
  await run(() => { const t = window.test, input = t.input('input-\ufeff');
    input.case_ids = ['case-\ufeff']; input.partitions = [{ partition_id: 'partition-\ufeff', case_ids: ['case-\ufeff'] }];
    t.requests.at(-1).reply(input); });
  await save();
  await verify('BOM input and case IDs produce exact original command', async () => {
    const r = (await requests()).at(-1); assert.equal(r.method, 'POST');
    assert.equal(r.body.input_id, 'input-\ufeff'); assert.deepEqual(r.body.case_ids, ['case-\ufeff']);
  });
  await run(() => { const t = window.test, r = t.requests.at(-1), record = t.record(r.body);
    record.content.selection.case_ids = ['case-\ufeff']; record.content.selection.partition_ids = ['partition-\ufeff'];
    r.reply({ request_key: r.body.request_key, record }); });
  await verify('BOM input/case/derived partition IDs bind exact receipt', async () => {
    const s = await snapshot(); assert.equal(s.status, 'saved'); assert.equal(s.saved.content.input.input_id, 'input-\ufeff');
    assert.deepEqual(s.saved.content.selection.case_ids, ['case-\ufeff']);
    assert.deepEqual(s.saved.content.selection.partition_ids, ['partition-\ufeff']);
  });

  // Development React really replays mount effects. Keep all obsolete GETs
  // observable instead of filtering their count to fit production assumptions.
  const strictPage = await browser.newPage({ viewport: { width: 390, height: 844 } });
  strictPage.on('pageerror', error => errors.push(String(error)));
  await strictPage.route('https://plans-strict.invalid/**', route => route.fulfill({ body: '<div id="root"></div>', contentType: 'text/html' }));
  await strictPage.goto('https://plans-strict.invalid/'); await install(strictPage, true);
  const strictTick = () => strictPage.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  for (const suite of ['qasper', 'qmsum', 'multihop_rag', 'alce', 'hotpotqa']) {
    await strictPage.evaluate(suite => { const t = window.test; t.reset(); t.suite = suite; }, suite); await strictTick();
    await verify(suite + ' development StrictMode replays effects and aborts the obsolete list', async () => {
      const r = await strictPage.evaluate(() => ({ setups: window.test.effectSetups, cleanups: window.test.effectCleanups,
        obsolete: window.test.requests.some(r => r.signal.aborted) }));
      assert.equal(r.setups, 2); assert.equal(r.cleanups, 1); assert.equal(r.obsolete, true);
    });
    await strictPage.evaluate(() => { const t = window.test; t.requests.at(-1).reply([]); }); await strictTick();
    await strictPage.evaluate(() => { const c = window.test.last; c.setTitle('😀'.repeat(500)); c.setCaseIdsText('case-m\ncase-z'); c.selectInput('input-A'); }); await strictTick();
    await strictPage.evaluate(() => { const t = window.test; t.requests.at(-1).reply(t.input()); }); await strictTick();
    await strictPage.evaluate(() => { const t = window.test; t.old = t.last; void t.last.save(); void t.last.save(); t.old.newDraft(); t.old.setTitle('old'); }); await strictTick();
    await strictPage.evaluate(() => { const t = window.test, r = t.requests.at(-1); r.reply({ request_key: r.body.request_key, record: t.record(r.body) }); }); await strictTick();
    await verify(suite + ' typed DTO and Unicode title survive StrictMode command/receipt boundaries', async () => {
      const r = await strictPage.evaluate(() => ({ status: window.test.last.status, saved: window.test.last.saved,
        posts: window.test.requests.filter(r => r.method === 'POST').length, callbacks: window.test.callbacks.length }));
      assert.equal(r.status, 'saved'); assert.equal(r.saved.content.input.suite, suite); assert.equal(r.saved.title, '😀'.repeat(500));
      assert.equal(r.posts, 1); assert.equal(r.callbacks, 1);
    });
  }
  await strictPage.close();
  assert.deepEqual(errors, []);
  console.log(`PASS ${checks} plan boundary checks (React 19 + real RN web, dark390, no service/state/build)`);
} finally { await browser.close(); }
