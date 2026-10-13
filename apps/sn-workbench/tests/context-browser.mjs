// Dedicated state only. Goal previews use the real API; file responses are
// controlled snapshots to exercise late replies and changing source content.
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
  const previous = await page.evaluate(() => new URLSearchParams(location.hash.slice(1)).get('task'));
  await page.getByTestId('new-task').click();
  await page.getByTestId('task-title').fill('上下文快照独立验收');
  await page.getByTestId('create-task').click();
  await page.waitForFunction(old => new URLSearchParams(location.hash.slice(1)).get('task') !== old, previous);
  await page.getByTestId('task-composer').waitFor();
  await page.getByTestId('note-input').fill('独立笔记内容');
  assert.equal(await page.getByTestId('context-picker').count(), 0, 'note mode does not contain the picker');
  await page.getByTestId('mode-message').click();
  assert.equal(await page.getByTestId('context-picker').count(), 1);
  const picker = page.getByTestId('context-picker');
  await picker.getByRole('button', { name: '选择任务材料 任务目标', exact: true }).click();
  await page.getByTestId('context-preview').click();
  await picker.getByText('此来源文本为空', { exact: true }).waitFor();
  assert.equal(await page.getByTestId('context-add').isEnabled(), false, 'reading needs no destination, adding does');
  assert.match(await page.getByTestId('context-preview-hash').innerText(), /e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855/);
  const panel = page.getByTestId('task-session-panel');
  for (const agent of ['context-agent-A', 'context-agent-B']) {
    await page.getByTestId('session-agent-id-input').fill(agent);
    await page.getByTestId('session-link').click();
    await panel.getByRole('button', { name: new RegExp('选择已保存会话.*' + agent) }).waitFor();
  }
  const choose = agent => panel.getByRole('button', { name: new RegExp('选择已保存会话.*' + agent) }).click();
  await choose('context-agent-A');
  assert.equal(await page.getByTestId('context-add').isEnabled(), false, 'target change invalidates a completed preview');
  await page.getByTestId('message-input').fill('消息原文保持');
  await page.getByTestId('context-preview').click();
  await page.getByTestId('context-add').waitFor({ state: 'visible' });
  await page.waitForFunction(() => document.querySelector('[data-testid="context-add"]')?.getAttribute('aria-disabled') !== 'true');
  await page.getByTestId('context-add').click();
  const composer = page.getByTestId('task-composer');
  const openGoal = composer.getByRole('button', { name: /^打开引用 任务目标/ });
  await openGoal.waitFor();
  assert.equal(await page.getByTestId('message-input').inputValue(), '消息原文保持');
  assert.equal(await page.getByTestId('context-add').isEnabled(), false, 'same normalized source/hash is not duplicated');
  await page.getByTestId('mode-note').click();
  assert.equal(await page.getByTestId('note-input').inputValue(), '独立笔记内容');
  await page.getByTestId('mode-message').click();
  await choose('context-agent-B');
  assert.equal(await composer.getByRole('button', { name: /^打开引用/ }).count(), 0);
  await choose('context-agent-A');
  await openGoal.waitFor();
  assert.equal(await page.getByTestId('context-add').isEnabled(), false);
  await openGoal.click();
  await picker.getByText('此来源文本为空', { exact: true }).waitFor();

  const task = await page.evaluate(() => new URLSearchParams(location.hash.slice(1)).get('task'));
  const calls = [];
  let held;
  let heldReady;
  let markHeld;
  const armHeld = () => { heldReady = new Promise(resolve => { markHeld = resolve; }); };
  let version = 1;
  const original = '  开始\n' + '界'.repeat(21000) + '\n完整末尾  ';
  const snapshot = (path, text, hash) => ({ reference: { kind: 'file', path }, title: path, text, sha256: hash,
    source_refs: { workspace_id: 'synthetic-workspace' } });
  await page.route('**/api/tasks/' + task + '/context', async route => {
    const { reference } = route.request().postDataJSON(); calls.push(reference);
    if (reference.kind !== 'file') { await route.continue(); return; }
    if (reference.path === 'old.md') { held = route; markHeld(); return; }
    await route.fulfill({ json: snapshot('docs/current.md', version === 1 ? original : '第二次读取的内容',
      (version === 1 ? 'a' : 'b').repeat(64)) });
  });
  await picker.getByRole('button', { name: '代码/文档路径', exact: true }).click();
  await page.getByTestId('context-path-input').fill('old.md');
  armHeld();
  await page.getByTestId('context-preview').click();
  await heldReady;
  await page.waitForFunction(() => document.querySelector('[data-testid="context-preview"]')?.getAttribute('aria-disabled') === 'true');
  await page.getByTestId('context-path-input').fill('./docs/current.md');
  assert.equal(await page.getByTestId('context-add').isEnabled(), false);
  await page.getByTestId('context-preview').click();
  await picker.getByText('docs/current.md', { exact: true }).waitFor();
  assert.equal(await page.getByTestId('context-preview-text').innerText(), original);
  assert.equal(await page.getByTestId('context-preview-hash').innerText(), 'SHA-256：' + 'a'.repeat(64));
  assert.deepEqual(calls.at(-1), { kind: 'file', path: './docs/current.md' }, 'request uses the exact path');
  await held.fulfill({ json: snapshot('old.md', '应丢弃的旧路径', 'c'.repeat(64)) }).catch(() => {});
  await page.getByTestId('context-add').click();
  const openFile = composer.getByRole('button', { name: /^打开引用 docs\/current.md/ });
  await openFile.waitFor();
  assert.equal(await picker.getByText('应丢弃的旧路径', { exact: true }).count(), 0);
  version = 2;
  await page.getByTestId('context-preview').click();
  await picker.getByText('第二次读取的内容', { exact: true }).waitFor();
  await page.getByTestId('context-add').click();
  assert.equal(await openFile.count(), 2, 'new hash preserves both deliberately chosen snapshots');
  await openFile.first().click();
  assert.equal(await page.getByTestId('context-preview-text').innerText(), original, 'opening an attachment reads the stored snapshot');
  await composer.getByRole('button', { name: /^移除引用 docs\/current.md/ }).first().click();
  assert.equal(await openFile.count(), 1);
  await page.getByTestId('context-path-input').fill('old.md');
  armHeld();
  await page.getByTestId('context-preview').click();
  await heldReady;
  await page.waitForFunction(() => document.querySelector('[data-testid="context-preview"]')?.getAttribute('aria-disabled') === 'true');
  await choose('context-agent-B');
  await choose('context-agent-A');
  assert.equal(await page.getByTestId('context-add').isEnabled(), false);
  await held.fulfill({ json: snapshot('old.md', '应丢弃的旧目的地', 'd'.repeat(64)) }).catch(() => {});
  await page.getByTestId('context-path-input').fill('./docs/current.md');
  await page.getByTestId('context-preview').click();
  await picker.getByText('第二次读取的内容', { exact: true }).waitFor();
  assert.equal(await picker.getByText('应丢弃的旧目的地', { exact: true }).count(), 0);
  assert.equal(await openFile.count(), 1, 'late preview cannot transfer or add an attachment');
  assert.equal(await page.getByTestId('send-message').isEnabled(), false);
  await page.getByTestId('theme-toggle').click();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForFunction(() => document.documentElement.scrollWidth <= innerWidth);
  assert.deepEqual(failures, []);
  console.log('Context parent checks passed: empty real goal, explicit destination, exact snapshots, stale path rejection, isolation and changed-source preservation');
} finally { await browser.close(); }
