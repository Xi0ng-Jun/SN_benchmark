// The automatic-run page against tests/auto_run_fixture.py:
//   WORKBENCH_TEST_URL=http://127.0.0.1:8097  node tests/auto-run-browser.mjs
//   WORKBENCH_CHROMIUM=<path>  browser binary when Playwright's own build is absent
import { chromium } from '@playwright/test';
import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';

const origin = process.env.WORKBENCH_TEST_URL;
assert(origin, 'Set WORKBENCH_TEST_URL to the fixture server');
await mkdir('test-results', { recursive: true });
const browser = await chromium.launch({ headless: true, executablePath: process.env.WORKBENCH_CHROMIUM });
const failures = [];
const open = async (id, { scheme = 'light', width = 1440, height = 1000 } = {}) => {
  const page = await browser.newPage({ viewport: { width, height }, colorScheme: scheme });
  page.on('pageerror', error => failures.push(`${id}: ${error}`));
  await page.request.post(`${origin}/api/session`, { data: { token: 'test-access' } });
  await page.goto(`${origin}/#it=${id}`);
  return page;
};
const shot = (page, name, fullPage = true) => page.screenshot({ path: `test-results/auto-${name}.png`, fullPage });
const status = async page => { await page.getByTestId('auto-status-label').waitFor({ timeout: 20000 }); return page.getByTestId('auto-status-label').innerText(); };

// Finished: lineage in segments, a rolled-back branch, an open segment, every attempt kept.
{
  const page = await open('auto-done');
  assert.equal(await status(page), '已停机');
  assert.match(await page.getByTestId('auto-next-action').innerText(), /封存集/);
  assert.match(await page.getByTestId('segment-1-status').innerText(), /已确认/);
  assert.match(await page.getByTestId('segment-2-status').innerText(), /已回滚/);
  assert.match(await page.getByTestId('segment-3-status').innerText(), /暂定中/);
  await shot(page, 'done-light');
  // Nothing on the page writes.
  for (const manual of ['act-run', 'act-derive', 'act-adopt', 'act-reject', 'act-freeze', 'lineage']) assert.equal(await page.getByTestId(manual).count(), 0, manual);
  const refused = await page.evaluate(async () => (await fetch('/api/candidates/root-auto-done/decision', { method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Workbench': '1' }, body: JSON.stringify({ decision: 'adopted' }) })).status);
  assert.equal(refused, 409);
  await page.getByTestId('auto-config').waitFor();
  // Selecting shows the evidence behind it.
  await page.getByTestId('segment-1').click();
  await page.getByTestId('auto-confirmation').waitFor();
  assert.match(await page.getByTestId('auto-attribution').innerText(), /可能有偏|未归因/);
  await shot(page, 'segment-light', false);
  await page.getByTestId('segment-2').click();
  await page.getByTestId('auto-confirmation').waitFor();
  await shot(page, 'rollback-light', false);
  await page.getByTestId('round-1').getByRole('button').first().click().catch(() => undefined);
  const failed = page.locator('[data-testid^="attempt-1:"]');
  if (!(await failed.count())) await page.getByTestId('round-1').getByText('第 1 轮').click();
  assert.equal(await failed.count(), 3, 'failed attempts stay visible');
  await failed.first().click();
  await page.getByTestId('auto-detail-attempt').waitFor();
  await shot(page, 'attempt-light', false);
  await page.close();
}
{
  const page = await open('auto-done', { scheme: 'dark' });
  await status(page);
  await shot(page, 'done-dark');
  await page.close();
}
// Running: no action asked of the person, the round's cards in flight.
{
  const page = await open('auto-running');
  assert.equal(await status(page), '运行中');
  assert.equal(await page.getByTestId('auto-next-action').count(), 0);
  await page.getByTestId('auto-open-round').waitFor();
  assert.equal(await page.getByTestId('todo-auto-action').count(), 0);
  await shot(page, 'running-light');
  await page.close();
}
{
  const page = await open('auto-running', { scheme: 'dark' });
  await status(page);
  await shot(page, 'running-dark');
  await page.close();
}
// Interrupted and budget: the one thing the person has to do leads, and the sidebar says so.
for (const [id, label, pattern] of [['auto-interrupted', '已中断', /续跑/], ['auto-budget', '已停机', /预算/]]) {
  const page = await open(id);
  assert.equal(await status(page), label);
  assert.match(await page.getByTestId('auto-next-action').innerText(), pattern);
  await page.getByTestId('todo-auto-action').waitFor();
  await shot(page, id);
  await page.close();
}
// Sealed: the curve with the same markers.
{
  const page = await open('auto-sealed');
  await status(page);
  await page.getByTestId('auto-sealed').waitFor();
  assert.match(await page.getByTestId('auto-next-action').innerText(), /部署节点/);
  await shot(page, 'sealed-light');
  await page.close();
}
{
  const page = await open('auto-new');
  assert.equal(await status(page), '未开始');
  await shot(page, 'new');
  await page.close();
}
// Narrow: the detail opens over the page.
{
  const page = await open('auto-done', { width: 420, height: 860 });
  await status(page);
  await shot(page, 'narrow');
  await page.getByTestId('segment-1').click();
  await page.getByTestId('auto-confirmation').waitFor();
  await shot(page, 'narrow-detail', false);
  await page.close();
}
// Nothing spills out of its row at any window size: side panels leave a narrow column at mid widths.
for (const [id, width] of [['auto-done', 1440], ['auto-done', 1280], ['auto-done', 1100], ['auto-done', 900], ['auto-done', 420], ['auto-running', 1100], ['auto-sealed', 1100], ['auto-sealed', 420]]) {
  const page = await open(id, { width });
  await status(page);
  const spills = await page.evaluate(() => {
    const out = [];
    const rows = document.querySelectorAll('[data-testid^="segment-"]:not([data-testid$="-status"]),[data-testid^="lineage-node-"],[data-testid^="attempt-"],[data-testid^="card-"],[data-testid^="kpi-"]');
    const column = document.querySelector('[data-testid="auto-run"]').getBoundingClientRect();
    for (const row of rows) {
      const box = row.getBoundingClientRect();
      if (box.right > column.right + 1) out.push(row.dataset.testid + ' leaves the column');
      for (const child of row.querySelectorAll('*')) {
        const r = child.getBoundingClientRect();
        if (r.width && r.right > box.right + 1) { out.push(`${row.dataset.testid}: ${(child.textContent || child.tagName).slice(0, 24)}`); break; }
      }
    }
    return out;
  });
  assert.deepEqual(spills, [], `${id} at ${width}`);
  await page.close();
}
// The markers and the branch are what they claim to be.
{
  const page = await open('auto-done');
  await status(page);
  assert.ok(await page.getByTestId('rail-branch').count() >= 2, 'a rolled-back segment hangs off the trunk');
  assert.equal(await page.getByTestId('rail-branch').first().evaluate(el => getComputedStyle(el).borderLeftStyle), 'dashed');
  const gaps = await page.evaluate(() => {
    const rows = [...document.querySelectorAll('[data-testid="auto-lineage"] [data-testid^="segment-"]:not([data-testid$="-status"]),[data-testid="auto-lineage"] [data-testid^="lineage-node-"],[data-testid="lineage-seed"]')]
      .map(el => el.getBoundingClientRect()).sort((a, b) => a.top - b.top);
    return rows.slice(1).map((r, i) => Math.round(r.top - rows[i].bottom));
  });
  assert.ok(gaps.every(gap => gap === 0), `the trunk has no breaks between rows: ${gaps}`);
  await page.close();
}
// A manual iteration keeps its page.
{
  const page = await open('manual');
  await page.getByTestId('lineage').waitFor({ timeout: 20000 });
  assert.equal(await page.getByTestId('auto-run').count(), 0);
  await page.close();
}
await browser.close();
assert.deepEqual(failures, []);
console.log('auto-run browser checks passed');
