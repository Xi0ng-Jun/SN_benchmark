// Run only against WORKBENCH_TEST_URL's dedicated state. Remote reads are
// controlled HTTP fixtures; this checks the real parent lifecycle and UI.
import { chromium } from '@playwright/test';
import assert from 'node:assert/strict';
const url = process.env.WORKBENCH_TEST_URL;
assert(url, 'A dedicated test-state URL is required');
const browser = await chromium.launch({ headless: true, executablePath: process.env.WORKBENCH_CHROMIUM });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const failures = [];
page.on('pageerror', error => failures.push(String(error)));
try {
  await page.goto(url);
  await page.getByTestId('new-task').waitFor();
  const previousTask = await page.evaluate(() => new URLSearchParams(location.hash.slice(1)).get('task'));
  await page.getByTestId('new-task').click();
  await page.getByTestId('task-title').fill('只读会话生命周期独立验收');
  await page.getByTestId('create-task').click();
  await page.waitForFunction(previous => new URLSearchParams(location.hash.slice(1)).get('task') !== previous, previousTask);
  await page.getByTestId('task-composer').waitFor();
  assert.equal(await page.getByTestId('task-session-panel').count(), 1);
  const panel = page.getByTestId('task-session-panel');
  assert.equal(await page.getByTestId('session-refresh').isEnabled(), false);
  assert.equal(await page.getByTestId('session-link').isEnabled(), false);
  const taskId = await page.evaluate(() => new URLSearchParams(location.hash.slice(1)).get('task'));
  const agentA = 'agent-browser-A';
  const agentB = 'agent-browser-B';
  for (const agentId of [agentA, agentB]) {
    await page.getByTestId('session-agent-id-input').fill(agentId);
    await page.getByTestId('session-link').click();
    await panel.getByRole('button', { name: new RegExp('选择已保存会话.*' + agentId) }).waitFor();
  }
  assert.equal(await page.getByTestId('session-refresh').isEnabled(), false, 'linking must not choose a destination');
  const sessions = await page.evaluate(async id => (await (await fetch('/api/tasks/' + id)).json()).sessions, taskId);
  const a = sessions.find(s => s.agent_id === agentA);
  const b = sessions.find(s => s.agent_id === agentB);
  const counters = { a: 0, b: 0 };
  let delayed;
  const scope = id => `/api/tasks/${taskId}/sessions/${id}`;
  const response = (agentId, epoch, seq, text) => ({
    result: { status: 'ok', agentId, direction: 'tail', projection: 'projected', epoch, reset: false,
      gap: false, staleCursor: false, window: { minSeq: 1, maxSeq: seq, nextSeq: seq + 1 },
      startCursor: { epoch, seq }, endCursor: { epoch, seq }, hasOlder: true, hasNewer: false, mergeWindow: null,
      entries: [{ id: `${epoch}:${seq}:${text}`, label: `序号 ${seq}`, kind: 'assistant', text, truncated: false,
        activityType: null, candidateBlockedReason: null,
        source_locator: { epoch, seq_start: seq, seq_end: seq, source_seq_ranges: [{ start_seq: seq, end_seq: seq }], turn_id: null, sha256: 'a'.repeat(64) } }] },
    release: { status: 'ok' }, observed_at: '2026-10-11T00:00:00Z',
  });
  for (const session of [a, b]) {
    await page.route('**' + scope(session.id) + '/agent', route => route.fulfill({ json: {
      result: { status: 'ok', agent: { id: session.agent_id, title: 'Controlled', status: 'idle', provider: 'fixture', model: 'fixture' } },
      release: { status: 'ok' }, observed_at: '2026-10-11T00:00:00Z',
    } }));
  }
  const calls = [];
  await page.route('**' + scope(a.id) + '/timeline', async route => {
    calls.push(route.request().postDataJSON()); counters.a += 1;
    if (counters.a === 1) { delayed = route; return; }
    await route.fulfill({ json: response(agentA, 'epoch-new-A', 11, '重新读取的A快照') });
  });
  await page.route('**' + scope(b.id) + '/timeline', async route => {
    calls.push(route.request().postDataJSON()); counters.b += 1;
    await route.fulfill({ json: response(agentB, 'epoch-B', 20, counters.b === 1 ? '当前B快照' : '较早B快照') });
  });
  const choose = agent => panel.getByRole('button', { name: new RegExp('选择已保存会话.*' + agent) }).click();
  await choose(agentA);
  await page.getByTestId('mode-message').click();
  await page.getByTestId('message-input').fill('A的消息草稿');
  await page.getByTestId('session-refresh').click();
  await page.waitForFunction(() => document.querySelector('[data-testid="session-refresh"]')?.getAttribute('aria-disabled') === 'true');
  await choose(agentB);
  await page.getByTestId('message-input').fill('B的消息草稿');
  await page.getByTestId('session-refresh').click();
  await panel.getByText('当前B快照', { exact: true }).waitFor();
  assert.equal(await page.getByTestId('session-newer').isEnabled(), false);
  await page.getByTestId('session-older').click();
  await panel.getByText('较早B快照', { exact: true }).waitFor();
  assert.deepEqual(calls.at(-1), { direction: 'before', cursor: { epoch: 'epoch-B', seq: 20 } });
  await choose(agentA);
  assert.equal(await page.getByTestId('message-input').inputValue(), 'A的消息草稿');
  assert.equal(await panel.getByText('较早B快照', { exact: true }).count(), 0);
  // A -> B -> A must not let the first A response revive its old generation.
  await delayed.fulfill({ json: response(agentA, 'epoch-old-A', 10, '应丢弃的旧A响应') }).catch(() => {});
  await page.getByTestId('session-refresh').click();
  await panel.getByText('重新读取的A快照', { exact: true }).waitFor();
  assert.equal(await panel.getByText('应丢弃的旧A响应', { exact: true }).count(), 0);
  await choose(agentB);
  assert.equal(await page.getByTestId('message-input').inputValue(), 'B的消息草稿');
  assert.equal(await page.getByTestId('send-message').isEnabled(), false);
  await page.getByTestId('theme-toggle').click();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForFunction(() => document.documentElement.scrollWidth <= innerWidth);
  assert.deepEqual(failures, []);
  console.log('Session parent checks passed: explicit selection, local links, independent drafts, real cursors and late-generation discard');
} finally { await browser.close(); }
