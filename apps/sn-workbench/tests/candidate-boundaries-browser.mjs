// Real React hooks and DOM; only the external session producer/fetch are doubles.
// No daemon, SQLite writes, or preview-state access. Source is transpiled in
// memory so testing this hook does not require rebuilding the application.
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
const modules = {
  react: production('react', 'react.production.js'),
  'react-dom': production('react-dom', 'react-dom.production.js'),
  client: production('react-dom/client', 'react-dom-client.production.js'),
  scheduler: production('scheduler', 'scheduler.production.js'),
  '../contracts': transpile('../src/contracts.ts'),
  hook: transpile('../src/agents/useCandidates.ts'),
};
const browser = await chromium.launch({ headless: true, executablePath: process.env.WORKBENCH_CHROMIUM });
const failures = [];
let checks = 0;
try {
  const page = await browser.newPage();
  page.on('pageerror', error => failures.push(String(error)));
  // HTTPS supplies crypto.randomUUID without any service or network request.
  await page.route('https://candidate-harness.invalid/**', route => route.fulfill({ body: '<div id="root"></div>', contentType: 'text/html' }));
  await page.goto('https://candidate-harness.invalid/');
  await page.evaluate(sources => {
    const cache = {};
    const load = id => {
      if (!cache[id]) {
        const module = { exports: {} }; cache[id] = module;
        new Function('require', 'module', 'exports', 'process', sources[id])(load, module, module.exports, { env: { NODE_ENV: 'production' } });
      }
      return cache[id].exports;
    };
    const React = load('react'); const { createRoot } = load('client');
    const { useCandidates } = load('hook');
    const original = {
      id: 'entry', label: 'Assistant', kind: 'assistant', text: '  source text\n  ', truncated: false,
      activityType: null, candidateBlockedReason: null,
      source_locator: { epoch: 'epoch', seq_start: 1, seq_end: 3,
        source_seq_ranges: [{ start_seq: 1, end_seq: 1 }, { start_seq: 3, end_seq: 3 }],
        turn_id: 'turn', sha256: 'a'.repeat(64) },
    };
    window.test = { original, posts: [], status: 403, body: '', last: null };
    window.fetch = async (_url, options) => {
      if (!options || options.method === 'GET') return new Response('[]', { status: 200 });
      window.test.posts.push(JSON.parse(options.body));
      return new Response(window.test.body, { status: window.test.status });
    };
    let root;
    const Test = () => {
      window.test.last = useCandidates(window.test.task, 'alice', window.test.sessions, () => {});
      window.test.rendered = window.test.sessions;
      return React.createElement('div', null, 'Hook harness');
    };
    window.test.reset = () => {
      root?.unmount();
      window.test.posts = [];
      window.test.task = { id: 'task', title: 'Synthetic task', goal: '', workspace_id: 'workspace', results: [], sessions: [], history: [] };
      window.test.sessions = { session: { id: 'session', task_id: 'task', agent_id: 'agent', linked_by: 'alice', linked_at: '' },
        remote: { status: 'available' }, page: { status: 'ready', freshness: 'current', pendingAction: 'none' },
        generation: 1, pageVersion: 1, data: { entries: [structuredClone(original)] } };
      window.test.last = null;
      root = createRoot(document.getElementById('root'));
      root.render(React.createElement(Test));
    };
    window.test.replace = (change, value) => {
      const entry = structuredClone(window.test.original);
      if (change.startsWith('locator.')) entry.source_locator[change.slice(8)] = value;
      else entry[change] = value;
      // Intentionally preserve id/generation/pageVersion to isolate the boundary.
      window.test.sessions = { ...window.test.sessions, data: { entries: [entry] } };
      root.render(React.createElement(Test));
    };
    window.test.reset();
  }, modules);
  const open = async () => {
    await page.evaluate(() => window.test.reset());
    await page.waitForFunction(() => window.test.last?.recordsPanel.listStatus === 'ready');
    await page.evaluate(() => window.test.last.requestCandidate('entry', 'finding'));
    await page.waitForFunction(() => window.test.last.draftPanel?.source.current === true);
    await page.evaluate(() => window.test.last.draftPanel.onTextChange('candidate text'));
    await page.waitForFunction(() => window.test.last.draftPanel?.text === 'candidate text');
  };
  // Both unchanged controls and same-ID mutations are real producer inputs.
  const replacements = [
    ['text', 'different source'], ['kind', 'user'], ['truncated', true],
    ['locator.epoch', 'new-epoch'], ['locator.sha256', 'b'.repeat(64)],
    ['locator.seq_start', 0], ['locator.seq_end', 4], ['locator.turn_id', 'different-turn'],
    ['locator.source_seq_ranges', [{ start_seq: 1, end_seq: 3 }]],
  ];
  for (const [field, value] of replacements) {
    await open();
    await page.evaluate(({ field, value }) => window.test.replace(field, value), { field, value });
    await page.waitForFunction(() => window.test.rendered === window.test.sessions);
    try {
      assert.equal(await page.evaluate(() => window.test.last.draftPanel.source.current), false, field + ' replacement must invalidate frozen source');
      await page.evaluate(() => window.test.last.draftPanel.onSave());
      assert.equal(await page.evaluate(() => window.test.posts.length), 0, 'invalid source must never POST');
      assert.equal(await page.evaluate(() => window.test.last.draftPanel.text), 'candidate text');
      checks++;
    } catch (error) { failures.push(error.message); }
  }
  for (const [status, body, expected] of [[400, '', 'error'], [403, '<html>denied</html>', 'error'],
    [422, '', 'error'], [500, '', 'unknown'], [200, '', 'unknown']]) {
    await open();
    await page.evaluate(({ status, body }) => { window.test.status = status; window.test.body = body; window.test.last.draftPanel.onSave(); }, { status, body });
    await page.waitForFunction(() => ['error', 'unknown'].includes(window.test.last.draftPanel.saveStatus));
    try {
      assert.equal(await page.evaluate(() => window.test.last.draftPanel.saveStatus), expected, 'HTTP ' + status);
      assert.equal(await page.evaluate(() => window.test.posts.length), 1);
      assert.equal(await page.evaluate(() => window.test.last.draftPanel.text), 'candidate text');
      checks++;
    } catch (error) { failures.push(error.message); }
  }
  assert.deepEqual(failures, []);
  assert.equal(checks, replacements.length + 5);
  console.log('Candidate hook boundary checks passed:', checks);
} finally { await browser.close(); }
