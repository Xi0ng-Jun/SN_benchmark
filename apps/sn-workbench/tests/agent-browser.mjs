// This URL must be agent-browser-server.py's independent temporary state.
// Report only counts/booleans; assertion failures never include source text/hash.
import { chromium } from '@playwright/test';
import assert from 'node:assert/strict';
const url = process.env.WORKBENCH_TEST_URL;
const agentId = process.env.WORKBENCH_EXISTING_AGENT_ID;
assert(url?.startsWith('http://127.0.0.1:18094/#access=a-existing-agent-browser-test'));
assert(agentId);
const browser = await chromium.launch({ headless: true, executablePath: process.env.WORKBENCH_CHROMIUM });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
let pageFailed = false;
page.on('pageerror', () => { pageFailed = true; });
try {
  await page.goto(url);
  await page.getByTestId('new-task').waitFor();
  await page.getByTestId('new-task').click();
  await page.getByTestId('task-title').fill('Existing Agent read-only integration');
  await page.getByTestId('create-task').click();
  await page.waitForFunction(() => Boolean(new URLSearchParams(location.hash.slice(1)).get('task')));
  await page.getByTestId('task-session-panel').waitFor();
  const taskId = await page.evaluate(() => new URLSearchParams(location.hash.slice(1)).get('task'));
  await page.getByTestId('session-agent-id-input').fill(agentId);
  await page.getByTestId('session-link').click();
  const choice = page.getByTestId('task-session-panel').getByRole('button', { name: new RegExp('选择已保存会话.*' + agentId) });
  await choice.waitFor();
  await choice.click();
  const readResponses = [];
  page.on('response', response => {
    if (response.url().includes('/api/tasks/' + taskId + '/sessions/') && /\/(agent|timeline)$/.test(response.url())) readResponses.push(response);
  });
  await page.getByTestId('session-refresh').click();
  const extract = page.getByTestId('task-session-panel').getByRole('button', { name: /提取发现候选/ });
  await extract.first().waitFor();
  let selected = false;
  for (let index = 0; index < await extract.count(); index++) {
    if (await extract.nth(index).isEnabled()) { await extract.nth(index).click(); selected = true; break; }
  }
  assert(selected, 'No current complete assistant entry is available');
  await page.getByTestId('candidate-source-text').waitFor();
  const exactText = await page.getByTestId('candidate-source-text').innerText();
  const exactHash = (await page.getByTestId('candidate-source-hash').innerText()).replace(/^SHA-256[：:]\s*/, '');
  await page.getByTestId('candidate-text').fill('Read-only SDK browser integration candidate');
  assert(await page.getByTestId('candidate-save').isEnabled());
  await page.getByTestId('candidate-save').click();
  await page.getByTestId('candidate-draft-panel').getByText('候选已保存', { exact: true }).waitFor();
  const facts = await page.evaluate(async ({ taskId, agentId, exactText, exactHash }) => {
    const records = await (await fetch('/api/tasks/' + taskId + '/candidates')).json();
    const task = await (await fetch('/api/tasks/' + taskId)).json();
    return { count: records.length, origin: records[0]?.origin === 'agent',
      sourceMatched: records[0]?.source.text === exactText && records[0]?.source.sha256 === exactHash,
      identityMatched: records[0]?.source.agent_id === agentId,
      judgments: records.reduce((n, item) => n + item.decisions.length, 0),
      sessions: task.sessions.length, history: task.history.map(item => item.kind) };
  }, { taskId, agentId, exactText, exactHash });
  assert(facts.count === 1 && facts.origin && facts.sourceMatched && facts.identityMatched);
  assert(facts.judgments === 0 && facts.sessions === 1);
  assert.deepEqual(facts.history, ['task.created', 'session.attached', 'candidate.saved']);
  assert(readResponses.length === 2, 'Expected independent agent and timeline reads');
  for (const response of readResponses) {
    const envelope = await response.json();
    assert(envelope.result.status === 'ok' && envelope.release.status === 'ok', 'Read result/release is not fully confirmed');
  }
  assert(!pageFailed, 'Browser encountered a runtime error');
  console.log('Existing Agent browser passed: agent/timeline reads plus source reread, exact-source candidate, 1 session, 0 human judgments');
} finally { await browser.close(); }
