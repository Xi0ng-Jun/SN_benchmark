// Real React hook with in-memory transpilation; only HTTP/Task producer are doubles.
import { chromium } from '@playwright/test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import ts from 'typescript';
const require = createRequire(import.meta.url);
const production = (name, file) => readFileSync(join(dirname(require.resolve(name)), 'cjs', file), 'utf8');
const transpile = path => ts.transpileModule(readFileSync(new URL(path, import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const modules = { react: production('react', 'react.production.js'), 'react-dom': production('react-dom', 'react-dom.production.js'),
  client: production('react-dom/client', 'react-dom-client.production.js'), scheduler: production('scheduler', 'scheduler.production.js'),
  '../contracts': transpile('../src/contracts.ts'), hook: process.env.WORKBENCH_CREATION_SOURCE
    ? ts.transpileModule(readFileSync(process.env.WORKBENCH_CREATION_SOURCE, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText
    : transpile('../src/agents/useAgentCreations.ts') };
const browser = await chromium.launch({ headless: true, executablePath: process.env.WORKBENCH_CHROMIUM });
const failures = []; let checks = 0;
try {
  const page = await browser.newPage(); page.on('pageerror', e => failures.push(String(e)));
  await page.route('https://creation-harness.invalid/**', route => route.fulfill({ body: '<div id="root"></div>', contentType: 'text/html' }));
  await page.goto('https://creation-harness.invalid/');
  await page.evaluate(sources => {
    const cache = {}; const load = id => { if (!cache[id]) { const m = { exports: {} }; cache[id] = m;
      new Function('require', 'module', 'exports', 'process', sources[id])(load, m, m.exports, { env: { NODE_ENV: 'production' } }); } return cache[id].exports; };
    const React = load('react'); const { createRoot } = load('client'); const { useAgentCreations } = load('hook');
    const t = window.test = {};
    t.record = body => ({ id: 'aaaaaaaaaaaa4aaa8aaaaaaaaaaaaaaa', task_id: t.task.id, actor: t.actor,
      profile_id: body.profile_id, title: body.title ?? null, request_key: body.request_key,
      agent_id: '12345678-1234-4123-8123-123456789abc', session_id: 'bbbbbbbbbbbb4bbb8bbbbbbbbbbbbbbb', status: 'created',
      prepared_at: '', started_at: '', finished_at: '', envelope: { result: { status: 'created',
        agentId: '12345678-1234-4123-8123-123456789abc', ready: true, readiness: 'idle',
        agent: { id: '12345678-1234-4123-8123-123456789abc', status: 'idle', provider: 'fixture', model: 'model' } }, release: { status: 'ok' } } });
    window.fetch = async (_url, options) => {
      if (options?.method === 'POST') { const body = JSON.parse(options.body); t.posts.push(body); const r = t.mutate(t.record(body));
        if (t.postStatus !== 200) return Response.json(t.postError, { status: t.postStatus });
        if (t.holdPost) return new Promise(resolve => { t.postReply = () => resolve(Response.json(r)); }); return Response.json(r); }
      if (t.holdGet) return new Promise(resolve => { const records = structuredClone(t.heldRecords); t.getReplies.push(() => resolve(Response.json(records))); });
      return Response.json(t.records);
    };
    let root; const Test = () => { t.last = useAgentCreations(t.task, t.actor, false, t.configured,
      t.profiles, id => t.refreshes.push(id));
      t.rendered = t.task.id + ':' + t.actor; return React.createElement('div', null, 'Creation hook'); };
    t.configure = (configured, profiles) => { t.configured = configured; t.profiles = profiles; root.render(React.createElement(Test)); };
    t.switch = (id, actor = t.actor) => { t.task = { id, title: id, sessions: [] }; t.actor = actor; root.render(React.createElement(Test)); };
    t.reset = () => { root?.unmount(); Object.assign(t, { task: { id: 'A', title: 'A', sessions: [] }, actor: 'alice', posts: [], refreshes: [],
      records: [], heldRecords: [], mutate: r => r, holdPost: false, holdGet: false, getReplies: [], postReply: null, last: null });
      t.postStatus = 200; t.postError = { error: 'Invalid creation request' }; t.configured = true;
      t.profiles = [{ id: 'allowed', provider: 'fixture', model: 'model' }, { id: 'second', provider: 'fixture', model: 'second' }];
      root = createRoot(document.getElementById('root')); root.render(React.createElement(Test)); }; t.reset();
  }, modules);
  const reset = async () => { await page.evaluate(() => window.test.reset()); await page.waitForFunction(() => window.test.last?.listStatus === 'ready');
    await page.evaluate(() => window.test.last.panel.onProfileSelect('allowed')); await page.waitForFunction(() => window.test.last.panel.selectedProfileId === 'allowed');
    await page.evaluate(() => window.test.last.panel.onTitleChange('frozen-title')); await page.waitForFunction(() => window.test.last.title === 'frozen-title'); };
  const settled = () => page.waitForFunction(() => ['created', 'unknown', 'error'].includes(window.test.last.status));
  const tick = () => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  const verify = async (name, fn) => { try { await fn(); checks++; } catch (e) { failures.push(name + ': ' + e.message); } };
  const mutations = [
    ['task', r => { r.task_id = 'B'; }], ['actor', r => { r.actor = 'bob'; }], ['key', r => { r.request_key = 'another-key'; }],
    ['profile', r => { r.profile_id = 'second'; }], ['title', r => { r.title = 'other-title'; }], ['blank profile', r => { r.profile_id = ''; }],
    ['numeric intent', r => { r.id = 1; }], ['short intent', r => { r.id = 'abc'; }],
    ['invalid agent UUID', r => { r.agent_id = r.envelope.result.agentId = r.envelope.result.agent.id = 'bogus'; }],
    ['invalid agent variant', r => { r.agent_id = r.envelope.result.agentId = r.envelope.result.agent.id = '12345678-1234-4123-0123-123456789abc'; }],
    ['short session', r => { r.session_id = 'abc'; }], ['numeric session', r => { r.session_id = 2; }],
    ['result agent mismatch', r => { r.envelope.result.agentId = '12345678-1234-4123-8123-123456789abd'; }],
    ['snapshot agent mismatch', r => { r.envelope.result.agent.id = '12345678-1234-4123-8123-123456789abd'; }],
    ['result status', r => { r.envelope.result.status = 'unknown'; }], ['release not_needed', r => { r.envelope.release = { status: 'not_needed' }; }],
    ['release ok with code', r => { r.envelope.release = { status: 'ok', code: 'CLOSE_FAILED' }; }],
    ['release missing code', r => { r.envelope.release = { status: 'unavailable' }; }], ['release invalid code', r => { r.envelope.release = { status: 'unavailable', code: 'made-up' }; }],
    ['ready true not_idle', r => { r.envelope.result.readiness = 'not_idle'; }], ['ready false idle', r => { r.envelope.result.ready = false; }], ['ready numeric', r => { r.envelope.result.ready = 1; }],
    ['provider mismatch', r => { r.envelope.result.agent.provider = 'other'; }], ['model mismatch', r => { r.envelope.result.agent.model = 'other'; }],
    ['ready true running snapshot', r => { r.envelope.result.agent.status = 'running'; }],
  ];
  for (const [name, mutate] of mutations) { await reset();
    await page.evaluate(source => { window.test.mutate = r => { new Function('r', '(' + source + ')(r)')(r); return r; }; window.test.last.panel.onCreate(); }, mutate.toString()); await settled();
    await verify(name, async () => { assert.equal(await page.evaluate(() => window.test.last.status), 'unknown');
      assert.equal(await page.evaluate(() => window.test.last.panel.receipt), null); assert.equal(await page.evaluate(() => window.test.refreshes.length), 0);
      await page.evaluate(() => { window.test.last.newIntent(); window.test.last.panel.onCreate(); }); assert.equal(await page.evaluate(() => window.test.posts.length), 1); }); }
  for (const readiness of ['idle', 'not_idle', 'archived', 'incomplete_snapshot']) { await reset();
    await page.evaluate(readiness => { window.test.mutate = r => { r.envelope.result.ready = readiness === 'idle'; r.envelope.result.readiness = readiness;
      if (readiness === 'incomplete_snapshot') delete r.envelope.result.agent.status; r.envelope.release = { status: 'unavailable', code: 'CLOSE_FAILED' }; return r; }; window.test.last.panel.onCreate(); }, readiness); await settled();
    await verify('valid ' + readiness, async () => { assert.equal(await page.evaluate(() => window.test.last.status), 'created'); const r = await page.evaluate(() => window.test.last.panel.receipt);
      assert.equal(r.readiness, readiness); assert.equal(r.ready, readiness === 'idle'); assert.equal(r.release.code, 'CLOSE_FAILED');
      assert.equal(r.agentStatus, readiness === 'incomplete_snapshot' ? null : 'idle'); assert.deepEqual(await page.evaluate(() => window.test.refreshes), ['A']); }); }
  // Corrected producer contract: unavailable/BRIDGE_RELEASE_UNKNOWN does not
  // negate an otherwise valid created observation (the old negative is moved).
  await reset(); await page.evaluate(() => { window.test.mutate = r => { r.envelope.release = { status: 'unavailable', code: 'BRIDGE_RELEASE_UNKNOWN' }; return r; }; window.test.last.panel.onCreate(); }); await settled();
  await verify('created BRIDGE_RELEASE_UNKNOWN remains fact', async () => { assert.equal(await page.evaluate(() => window.test.last.status), 'created');
    assert.equal(await page.evaluate(() => window.test.last.panel.receipt.release.code), 'BRIDGE_RELEASE_UNKNOWN'); });
  await reset(); await page.evaluate(() => window.test.last.panel.onCreate()); await settled();
  await page.evaluate(() => { const t = window.test; t.first = t.record(t.posts[0]); t.last.newIntent(); });
  await page.waitForFunction(() => window.test.last.status === 'idle');
  await page.evaluate(() => window.test.last.panel.onProfileSelect('second')); await page.waitForFunction(() => window.test.last.panel.selectedProfileId === 'second');
  await page.evaluate(() => window.test.last.panel.onTitleChange('second-title')); await page.waitForFunction(() => window.test.last.title === 'second-title');
  await page.evaluate(() => { const t = window.test; t.mutate = () => t.first; t.last.panel.onCreate(); }); await settled();
  await verify('new POST cannot acknowledge old known firstkey', async () => { assert.equal(await page.evaluate(() => window.test.last.status), 'unknown');
    assert.equal(await page.evaluate(() => window.test.last.title), 'second-title'); assert.equal(await page.evaluate(() => window.test.last.panel.selectedProfileId), 'second');
    assert.equal(await page.evaluate(() => window.test.last.panel.receipt), null); assert.deepEqual(await page.evaluate(() => window.test.refreshes), ['A']); });
  await page.evaluate(() => { const t = window.test; t.records = [t.first]; t.last.check(); }); await page.waitForFunction(() => !window.test.last.checking);
  await verify('check retains second intent when only firstkey exists', async () => { assert.equal(await page.evaluate(() => window.test.last.status), 'unknown');
    assert.equal(await page.evaluate(() => window.test.last.title), 'second-title'); assert.equal(await page.evaluate(() => window.test.last.panel.selectedProfileId), 'second'); });
  await page.evaluate(() => { const t = window.test; const r = t.record(t.posts[1]); r.envelope.result.agent.model = 'second'; t.records = [t.first, r]; t.last.check(); }); await page.waitForFunction(() => !window.test.last.checking);
  await verify('only captured second key can recover second intent', async () => { assert.equal(await page.evaluate(() => window.test.last.status), 'created');
    assert.equal(await page.evaluate(() => window.test.last.title), 'second-title');
    assert.equal(await page.evaluate(() => window.test.last.panel.receipt.profileId), 'second');
    assert.equal(await page.evaluate(() => window.test.last.panel.receipt.submittedTitle), 'second-title'); });
  await reset(); await page.evaluate(() => { window.test.postStatus = 422; window.test.last.panel.onTitleChange(' leading-title'); });
  await page.waitForFunction(() => window.test.last.title === ' leading-title'); await page.evaluate(() => window.test.last.panel.onCreate()); await settled();
  await verify('pre-write 422 offers explicit corrected intent', async () => {
    assert.equal(await page.evaluate(() => window.test.last.canNewIntent), true);
    assert.notEqual(await page.evaluate(() => window.test.last.panel.blockedReason), null, 'old failed attempt must not look creatable');
    await page.evaluate(() => { window.test.postStatus = 200; window.test.last.newIntent(); }); await tick();
    await page.evaluate(() => window.test.last.panel.onProfileSelect('allowed')); await tick();
    await page.evaluate(() => window.test.last.panel.onTitleChange('corrected-title')); await tick();
    await page.evaluate(() => window.test.last.panel.onCreate()); await settled();
    assert.equal(await page.evaluate(() => window.test.posts.length), 2); assert.notEqual(await page.evaluate(() => window.test.posts[0].request_key), await page.evaluate(() => window.test.posts[1].request_key));
    assert.equal(await page.evaluate(() => window.test.last.status), 'created');
  });
  for (const [status, error] of [[409, 'Invalid creation request'], [400, 'Invalid creation request'], [422, 'Other refusal']]) {
    await reset(); await page.evaluate(({ status, error }) => { window.test.postStatus = status; window.test.postError = { error }; window.test.last.panel.onCreate(); }, { status, error }); await settled();
    await verify('refusal ' + status + '/' + error + ' grants no new key', async () => {
      assert.equal(await page.evaluate(() => window.test.last.canNewIntent), false);
      await page.evaluate(() => window.test.last.newIntent()); await tick(); assert.equal(await page.evaluate(() => window.test.last.title), 'frozen-title');
      await page.evaluate(() => window.test.last.panel.onTitleChange('changed-payload')); await tick();
      assert.notEqual(await page.evaluate(() => window.test.last.panel.blockedReason), null, 'handler and disabled UI agree after correction without permission');
      await page.evaluate(() => window.test.last.panel.onCreate()); assert.equal(await page.evaluate(() => window.test.posts.length), 1);
      await page.evaluate(() => window.test.last.check()); await page.waitForFunction(() => !window.test.last.checking);
      assert.equal(await page.evaluate(() => window.test.last.status), 'unknown', 'GET missing never turns a generic refusal into creation absence');
      assert.equal(await page.evaluate(() => window.test.last.canNewIntent), false);
    });
  }
  await reset(); await page.evaluate(() => window.test.last.panel.onCreate()); await settled();
  await page.evaluate(() => window.test.configure(false, [])); await tick();
  await verify('config removal preserves read-only receipt facts', async () => {
    assert.equal(await page.evaluate(() => window.test.last.available), false);
    const restored = await page.evaluate(() => window.test.last.historicalReceipt);
    assert(restored, 'parent exposes safe historical receipt without an allowed creation profile');
    assert.equal(restored.receipt.profileId, 'allowed'); assert.equal(restored.record.profile_id, 'allowed');
    assert.equal(await page.evaluate(() => window.test.last.panel.profiles.length), 0);
    await page.evaluate(() => window.test.last.panel.onCreate()); assert.equal(await page.evaluate(() => window.test.posts.length), 1);
  });
  // No local intent on this mount: select the real historical record's profile
  // projection, never turn it into a current allowed creation choice.
  await reset(); await page.evaluate(() => { const t = window.test; t.records = [t.record({ profile_id: 'retired', request_key: 'historical-key', title: 'historical-title' })]; t.configure(false, []); t.switch('B'); });
  await page.waitForFunction(() => window.test.rendered === 'B:alice' && window.test.last.listStatus === 'ready');
  await page.evaluate(() => window.test.switch('A')); await page.waitForFunction(() => window.test.rendered === 'A:alice' && window.test.last.listStatus === 'ready');
  await verify('history with no config has no automatic selection', async () => { assert.equal(await page.evaluate(() => window.test.last.status), 'idle'); assert.equal(await page.evaluate(() => window.test.last.panel.profiles.length), 0); });
  await page.evaluate(() => window.test.last.selectRecord(window.test.records[0].id)); await tick();
  await verify('explicit historical selection/read works without config', async () => {
    assert.equal(await page.evaluate(() => window.test.last.status), 'created'); assert.equal(await page.evaluate(() => window.test.last.historicalReceipt.record.profile_id), 'retired');
    await page.evaluate(() => { window.test.last.newIntent(); window.test.last.panel.onCreate(); window.test.last.check(); }); await page.waitForFunction(() => !window.test.last.checking);
    assert.equal(await page.evaluate(() => window.test.last.status), 'created'); assert.equal(await page.evaluate(() => window.test.posts.length), 0);
    assert.equal(await page.evaluate(() => window.test.last.panel.profiles.length), 0);
  });
  for (const field of ['profile_id', 'title']) { await reset();
    await page.evaluate(() => { window.test.mutate = r => ({ ...r, status: 'unknown', session_id: null, envelope: null }); window.test.last.panel.onCreate(); }); await settled();
    await page.evaluate(field => { const t = window.test; const r = t.record(t.posts[0]); r[field] = field === 'title' ? 'forged-title' : 'second'; t.records = [r]; t.last.check(); }, field);
    await page.waitForFunction(() => !window.test.last.checking);
    await verify('GET frozen ' + field, async () => { assert.equal(await page.evaluate(() => window.test.last.status), 'unknown');
      assert.equal(await page.evaluate(() => window.test.last.title), 'frozen-title'); assert.equal(await page.evaluate(() => window.test.last.panel.selectedProfileId), 'allowed'); assert.equal(await page.evaluate(() => window.test.last.panel.receipt), null); }); }
  const startCreatedCheck = async () => { await reset(); await page.evaluate(() => window.test.last.panel.onCreate()); await settled();
    await page.evaluate(() => { const t = window.test; t.holdGet = true; t.heldRecords = [t.record(t.posts[0])]; t.last.check(); }); await page.waitForFunction(() => window.test.last.checking); };
  await reset(); await page.evaluate(() => window.test.last.panel.onCreate()); await settled();
  await page.evaluate(() => { const t = window.test; const callbacks = t.last; t.holdGet = true; t.heldRecords = [t.record(t.posts[0])];
    callbacks.check(); callbacks.newIntent(); callbacks.panel.onCreate(); callbacks.selectRecord(t.heldRecords[0].id); }); await tick();
  await verify('same-render check locks old action callbacks synchronously', async () => {
    assert.equal(await page.evaluate(() => window.test.last.checking), true); assert.equal(await page.evaluate(() => window.test.last.title), 'frozen-title');
    assert.equal(await page.evaluate(() => window.test.posts.length), 1);
  });
  await reset(); await page.evaluate(() => { window.test.oldCreate = window.test.last.panel.onCreate; window.test.configure(false, []); }); await tick();
  await page.evaluate(() => window.test.oldCreate()); await tick();
  await verify('old create callback observes current removed capability', async () => assert.equal(await page.evaluate(() => window.test.posts.length), 0));
  for (const action of ['canNewIntent', 'selectRecord', 'newIntent', 'create/edit']) {
    await startCreatedCheck();
    await verify('checking blocks ' + action, async () => {
      if (action === 'canNewIntent') assert.equal(await page.evaluate(() => window.test.last.canNewIntent), false);
      else if (action === 'selectRecord') await page.evaluate(() => window.test.last.selectRecord(window.test.heldRecords[0].id));
      else if (action === 'newIntent') await page.evaluate(() => window.test.last.newIntent());
      else await page.evaluate(() => { const t = window.test; t.last.panel.onTitleChange('changed'); t.last.panel.onProfileSelect('second'); t.last.panel.onCreate(); });
      await tick(); assert.equal(await page.evaluate(() => window.test.last.checking), true);
      assert.equal(await page.evaluate(() => window.test.last.title), 'frozen-title'); assert.equal(await page.evaluate(() => window.test.posts.length), 1);
    });
  }
  await startCreatedCheck();
  await page.evaluate(() => { window.test.holdGet = false; window.test.switch('B'); }); await page.waitForFunction(() => window.test.rendered === 'B:alice' && window.test.last.listStatus === 'ready');
  await page.evaluate(() => window.test.switch('A')); await page.waitForFunction(() => window.test.rendered === 'A:alice' && window.test.last.listStatus === 'ready');
  await page.evaluate(() => window.test.getReplies[0]()); await tick();
  await verify('stale checking A-B-A unlock', async () => assert.equal(await page.evaluate(() => window.test.last.checking), false));
  // A newer check after returning to A owns the lock; old success cannot
  // publish or clear it, including its finally block.
  await reset(); await page.evaluate(() => { window.test.mutate = r => ({ ...r, status: 'unknown', session_id: null, envelope: null }); window.test.last.panel.onCreate(); }); await settled();
  await page.evaluate(() => { const t = window.test; t.holdGet = true; t.heldRecords = [t.record(t.posts[0])]; t.last.check(); });
  await page.waitForFunction(() => window.test.last.checking);
  await page.evaluate(() => { window.test.holdGet = false; window.test.switch('B'); }); await page.waitForFunction(() => window.test.rendered === 'B:alice' && window.test.last.listStatus === 'ready');
  await page.evaluate(() => window.test.switch('A')); await page.waitForFunction(() => window.test.rendered === 'A:alice' && window.test.last.listStatus === 'ready');
  await verify('abandoned unknown check unlocks on return', async () => assert.equal(await page.evaluate(() => window.test.last.checking), false));
  await page.evaluate(() => { const t = window.test; t.holdGet = true; t.heldRecords = []; t.last.check(); });
  await tick();
  await page.evaluate(() => window.test.getReplies[0]()); await tick();
  await verify('old GET cannot publish success or unlock new GET', async () => { assert.equal(await page.evaluate(() => window.test.last.status), 'unknown');
    assert.equal(await page.evaluate(() => window.test.last.checking), true); assert.deepEqual(await page.evaluate(() => window.test.refreshes), []); });
  // Old implementation may never start the new check: record this failure
  // without hanging the rest of the independent counterexamples.
  if (await page.evaluate(() => window.test.getReplies.length > 1)) {
    await page.evaluate(() => window.test.getReplies[1]()); await page.waitForFunction(() => !window.test.last.checking);
  }
  await verify('new missing GET starts and stays unknown', async () => { assert.equal(await page.evaluate(() => window.test.getReplies.length), 2);
    assert.equal(await page.evaluate(() => window.test.last.status), 'unknown'); });
  for (const field of ['id', 'agent_id', 'session_id']) {
    await reset(); await page.evaluate(() => window.test.last.panel.onCreate()); await settled();
    await page.evaluate(field => { const t = window.test; const r = t.record(t.posts[0]);
      r[field] = field === 'agent_id' ? '12345678-1234-4123-8123-123456789abd' : 'cccccccccccc4ccc8ccccccccccccccc';
      if (field === 'agent_id') r.envelope.result.agentId = r.envelope.result.agent.id = r.agent_id;
      t.records = [r]; t.last.check(); }, field); await page.waitForFunction(() => !window.test.last.checking);
    await verify('known same-key ' + field + ' cannot rebind', async () => assert.equal(await page.evaluate(() => window.test.last.status), 'unknown'));
  }
  // A fresh mount/list is read-only until explicit selection; remote title is
  // independent of the optional submitted title.
  await reset(); await page.evaluate(() => { const t = window.test; t.records = [t.record({ profile_id: 'allowed', request_key: 'restored-key' })]; t.switch('B'); });
  await page.waitForFunction(() => window.test.rendered === 'B:alice' && window.test.last.listStatus === 'ready');
  await page.evaluate(() => window.test.switch('A')); await page.waitForFunction(() => window.test.rendered === 'A:alice' && window.test.last.listStatus === 'ready');
  await verify('list never auto-selects', async () => assert.equal(await page.evaluate(() => window.test.last.status), 'idle'));
  await page.evaluate(() => window.test.last.selectRecord(window.test.records[0].id)); await tick();
  await verify('first explicit record restores binding', async () => { assert.equal(await page.evaluate(() => window.test.last.status), 'created');
    assert.equal(await page.evaluate(() => window.test.last.title), ''); assert.equal(await page.evaluate(() => window.test.posts.length), 0); });
  for (const status of ['missing', 'prepared', 'unknown']) { await reset();
    await page.evaluate(() => { window.test.mutate = r => ({ ...r, status: 'unknown', session_id: null, envelope: null }); window.test.last.panel.onCreate(); }); await settled();
    await page.evaluate(status => { const t = window.test; t.records = status === 'missing' ? [] : [{ ...t.record(t.posts[0]), status, session_id: null, envelope: null }]; t.last.check(); }, status);
    await page.waitForFunction(() => !window.test.last.checking);
    await verify('read ' + status + ' keeps unknown', async () => { assert.equal(await page.evaluate(() => window.test.last.status), 'unknown');
      await page.evaluate(() => { window.test.last.newIntent(); window.test.last.panel.onCreate(); }); assert.equal(await page.evaluate(() => window.test.posts.length), 1); }); }
  await reset(); await page.evaluate(() => { const t = window.test; t.records = [{ ...t.record({ profile_id: 'allowed', title: 'server-pending', request_key: 'server-pending-key' }),
    status: 'prepared', session_id: null, envelope: null }]; t.switch('B'); });
  await page.waitForFunction(() => window.test.rendered === 'B:alice' && window.test.last.listStatus === 'ready');
  await page.evaluate(() => window.test.switch('A')); await page.waitForFunction(() => window.test.rendered === 'A:alice' && window.test.last.listStatus === 'ready');
  await verify('server prepared intent visibly blocks an idle new create', async () => {
    assert.notEqual(await page.evaluate(() => window.test.last.panel.blockedReason), null);
    await page.evaluate(() => { window.test.last.newIntent(); window.test.last.panel.onCreate(); }); assert.equal(await page.evaluate(() => window.test.posts.length), 0);
    await page.evaluate(() => window.test.last.selectRecord(window.test.records[0].id)); await tick();
    assert.equal(await page.evaluate(() => window.test.last.status), 'unknown');
    assert.equal(await page.evaluate(() => window.test.last.canNewIntent), false);
  });
  // Malformed pending envelopes must not establish server intent IDs either.
  const pendingMutations = [
    ['unknown code', e => { e.result.code = 'made-up'; }], ['unknown cause', e => { e.result.cause = 'made-up'; }],
    ['unknown not_needed', e => { e.release = { status: 'not_needed' }; }], ['unknown release code', e => { e.release = { status: 'unavailable', code: 'made-up' }; }],
    ['not_attempted code', e => { e.result = { status: 'not_attempted', code: 'made-up' }; e.release = { status: 'not_needed' }; }],
    ['not_attempted release code', e => { e.result = { status: 'not_attempted', code: 'AGENT_ALREADY_EXISTS' }; e.release = { status: 'ok', code: 'CLOSE_FAILED' }; }],
  ];
  for (const [name, mutate] of pendingMutations) { await reset();
    await page.evaluate(source => { window.test.mutate = r => { r.session_id = null; r.envelope = { result: { status: 'unknown', code: 'CREATE_OUTCOME_UNKNOWN',
      cause: 'CONNECTION_UNAVAILABLE', agentId: r.agent_id }, release: { status: 'ok' } };
      new Function('e', '(' + source + ')(e)')(r.envelope); r.status = r.envelope.result.status; return r; }; window.test.last.panel.onCreate(); }, mutate.toString()); await settled();
    await verify('invalid pending ' + name, async () => { assert.equal(await page.evaluate(() => window.test.last.status), 'unknown');
      assert.equal(await page.evaluate(() => window.test.last.intentId), null); assert.equal(await page.evaluate(() => window.test.refreshes.length), 0); });
  }
  for (const actorSwitch of [false, true]) { await reset(); await page.evaluate(() => { window.test.holdPost = true; window.test.last.panel.onCreate(); }); await page.waitForFunction(() => !!window.test.postReply);
    await page.evaluate(actorSwitch => window.test.switch(actorSwitch ? 'A' : 'B', actorSwitch ? 'bob' : 'alice'), actorSwitch);
    await page.waitForFunction(actorSwitch => window.test.rendered === (actorSwitch ? 'A:bob' : 'B:alice') && window.test.last.listStatus === 'ready', actorSwitch);
    await page.evaluate(() => window.test.switch('A', 'alice')); await page.waitForFunction(() => window.test.rendered === 'A:alice' && window.test.last.listStatus === 'ready');
    await page.evaluate(() => window.test.postReply()); await tick();
    await verify('late ACK ' + (actorSwitch ? 'actor' : 'task'), async () => { assert.equal(await page.evaluate(() => window.test.last.status), 'unknown'); assert.deepEqual(await page.evaluate(() => window.test.refreshes), []); });
    await page.evaluate(() => { const t = window.test; t.records = [t.record(t.posts[0])]; t.last.check(); }); await page.waitForFunction(() => !window.test.last.checking);
    await verify('explicit recovery ' + actorSwitch, async () => { assert.equal(await page.evaluate(() => window.test.last.status), 'created'); assert.deepEqual(await page.evaluate(() => window.test.refreshes), ['A']); }); }
  console.log(JSON.stringify({ checks, failures }, null, 2)); assert.deepEqual(failures, []);
} finally { await browser.close(); }
