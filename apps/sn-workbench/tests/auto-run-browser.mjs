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

if (!process.env.WORKBENCH_V41_ONLY) {
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
// Sealed: round x score, the same markers, every node selectable from the chart.
const chartFacts = page => page.evaluate(() => {
  const chart = document.querySelector('[data-testid="sealed-chart"]').getBoundingClientRect();
  const column = document.querySelector('[data-testid="auto-run"]').getBoundingClientRect();
  const svg = document.querySelector('[data-testid="sealed-chart"] svg').getBoundingClientRect();
  const hits = [...document.querySelectorAll('[data-testid^="sealed-point-"]')].map(el => {
    const r = el.getBoundingClientRect();
    return { id: el.dataset.testid, x: r.left + r.width / 2, y: r.top + r.height / 2 };
  });
  const circles = [...document.querySelectorAll('[data-testid="sealed-chart"] circle')];
  const dashed = circles.filter(c => c.getAttribute('stroke-dasharray')).length;
  const hollow = circles.filter(c => c.getAttribute('r') === '5' && c.getAttribute('fill') !== c.getAttribute('stroke')).length;
  const texts = [...document.querySelectorAll('[data-testid="sealed-chart"] text')].map(t => t.textContent);
  return { chart: { left: chart.left, right: chart.right, width: chart.width }, column: { right: column.right }, svg: { right: svg.right }, hits, dashed, hollow, texts };
});
{
  const page = await open('auto-sealed');
  await status(page);
  await page.getByTestId('sealed-chart').waitFor();
  assert.match(await page.getByTestId('auto-next-action').innerText(), /部署节点/);
  assert.match(await page.getByTestId('sealed-axis-note').innerText(), /answer_f1 · n = 40 · k = 2 · 区间已做多重校正/);
  const facts = await chartFacts(page);
  assert.equal(facts.hits.length, 7, 'seed + six lineage nodes');
  assert.ok(facts.texts.includes('轮（0 = 种子）') && facts.texts.includes('封存集 answer_f1'), `axis labels: ${facts.texts}`);
  for (const r of ['0', '1', '2', '3', '4', '5']) assert.ok(facts.texts.includes(r), `x tick ${r}`);
  const at = Object.fromEntries(facts.hits.map(h => [h.id.slice('sealed-point-'.length), h]));
  assert.ok(at[0].x < at[1].x && at[1].x < at[2].x && at[2].x < at[3].x && at[3].x < at[4].x && at[4].x < at[5].x, 'x follows the round');
  assert.ok(at[6].x - at[5].x >= 8 && at[6].x - at[5].x < 30, `two nodes of round 5 sit side by side: ${at[6].x - at[5].x}`);
  assert.equal(facts.dashed, 2, 'two rolled-back markers are dashed');
  assert.equal(facts.hollow, 4, 'two rolled-back + two provisional markers are hollow');
  // Hover shows the numbers; press selects the node and the right column shows it.
  await page.getByTestId('sealed-point-1').hover();
  await page.getByTestId('sealed-tip').waitFor();
  assert.match(await page.getByTestId('sealed-tip').innerText(), /0\.434[\s\S]*Δ种子 \+0\.094 \[\+0\.053, \+0\.135\][\s\S]*成立[\s\S]*第 1 轮 · 已确认/);
  await page.getByTestId('sealed-chart').screenshot({ path: 'test-results/auto-sealed-chart-light.png' });
  await page.getByTestId('sealed-point-2').click();
  await page.getByTestId('auto-detail-attempt').waitFor();
  assert.match(await page.getByTestId('auto-sealed-facts').innerText(), /第 2 轮[\s\S]*已回滚，挂在父节点上/);
  await page.getByTestId('sealed-point-0').click();
  await page.getByTestId('auto-detail-node').waitFor();
  assert.match(await page.getByTestId('auto-sealed-facts').innerText(), /0\.340[\s\S]*第 0 轮/);
  await page.getByTestId('sealed-point-6').click();
  await page.getByTestId('auto-detail-attempt').waitFor();
  assert.match(await page.getByTestId('auto-sealed-facts').innerText(), /第 5 轮[\s\S]*主路径/);
  // The per-node numbers stay one press away: the chart's table view.
  await page.getByTestId('sealed-table').getByText('逐节点数值').click();
  assert.equal(await page.locator('[data-testid^="sealed-row-"]').count(), 7);
  assert.match(await page.getByTestId('sealed-row-1').innerText(), /第 1 轮[\s\S]*0\.434[\s\S]*\+0\.094 \[\+0\.053, \+0\.135\][\s\S]*成立/);
  await shot(page, 'sealed-light');
  await page.close();
}
{
  const page = await open('auto-sealed', { scheme: 'dark' });
  await status(page);
  await page.getByTestId('sealed-chart').waitFor();
  await page.getByTestId('sealed-point-5').hover();
  await page.getByTestId('sealed-tip').waitFor();
  await page.getByTestId('auto-sealed').screenshot({ path: 'test-results/auto-sealed-chart-dark.png' });
  await shot(page, 'sealed-dark');
  await page.close();
}
// Narrow: the chart fits the column, the tooltip of the last point stays inside the chart.
for (const width of [420, 360]) {
  const page = await open('auto-sealed', { width, height: 860 });
  await status(page);
  await page.getByTestId('sealed-chart').waitFor();
  const facts = await chartFacts(page);
  assert.ok(facts.chart.right <= facts.column.right + 1 && facts.svg.right <= facts.chart.right + 1, `chart inside the column at ${width}`);
  assert.equal(facts.hits.length, 7);
  await page.getByTestId('sealed-point-6').hover();
  await page.getByTestId('sealed-tip').waitFor();
  const tip = await page.getByTestId('sealed-tip').boundingBox();
  assert.ok(tip.x >= facts.chart.left - 1 && tip.x + tip.width <= facts.chart.right + 1, `tooltip inside the chart at ${width}`);
  const scroll = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  assert.ok(scroll <= 0, `no horizontal scroll at ${width}: ${scroll}`);
  await page.getByTestId('auto-sealed').screenshot({ path: `test-results/auto-sealed-chart-narrow-${width}.png` });
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
for (const [id, width] of [['auto-done', 1440], ['auto-done', 1280], ['auto-done', 1100], ['auto-done', 900], ['auto-done', 420], ['auto-running', 1100], ['auto-sealed', 1440], ['auto-sealed', 1100], ['auto-sealed', 900], ['auto-sealed', 420]]) {
  const page = await open(id, { width });
  await status(page);
  if (id === 'auto-sealed') await page.getByTestId('sealed-table').getByText('逐节点数值').click();
  const spills = await page.evaluate(() => {
    const out = [];
    const rows = document.querySelectorAll('[data-testid^="segment-"]:not([data-testid$="-status"]),[data-testid^="lineage-node-"],[data-testid^="attempt-"],[data-testid^="card-"],[data-testid^="kpi-"],[data-testid^="sealed-row-"],[data-testid="sealed-chart"],[data-testid="sealed-legend"]');
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
// method-v4: finished run with ablation and a test-set endpoint.
{
  const page = await open('v4-done');
  assert.equal(await status(page), '已停机');
  assert.match(await page.getByTestId('auto-next-action').innerText(), /测试集已评测/);
  assert.match(await page.getByTestId('v4-endpoint').innerText(), /Δ_test/);
  assert.match(await page.getByTestId('v4-lineage').innerText(), /合并/);
  assert.match(await page.getByTestId('v4-ablation').innerText(), /剪除/);
  await shot(page, 'v4-done-light');
  // Nothing on the page writes.
  for (const manual of ['act-run', 'act-derive', 'act-adopt', 'act-freeze', 'trigger-sealed-btn']) assert.equal(await page.getByTestId(manual).count(), 0, manual);
  await page.getByTestId('v4-detail-run').waitFor();
  assert.match(await page.getByTestId('v4-detail-run').innerText(), /与方法文档的偏离/);
  // A candidate: cascade, mechanism with control pairs, safeguards.
  await page.getByTestId('v41-tab-rounds').click();
  await page.getByTestId('v4-record-1-0').click();
  await page.getByTestId('v4-detail-record').waitFor();
  const detail = await page.getByTestId('v4-mechanism').innerText();
  assert.match(detail, /对照/);
  assert.match(await page.getByTestId('v4-detail-record').innerText(), /采纳门槛/);
  await shot(page, 'v4-record-light', false);
  // An eliminated candidate stays visible with its stage.
  assert.match(await page.getByTestId('v4-record-1-2').innerText(), /小批淘汰/);
  await page.getByTestId('v41-tab-overview').click();
  await page.getByTestId('v4-node-1').click();
  await page.getByTestId('v4-detail-node').waitFor();
  await page.close();
}
{
  const page = await open('v4-interrupted', { scheme: 'dark', width: 820 });
  assert.equal(await status(page), '已中断');
  assert.match(await page.getByTestId('auto-next-action').innerText(), /续跑/);
  await page.getByTestId('v41-tab-rounds').click();
  assert.match(await page.getByTestId('v4-round-2').innerText(), /等待全验证集/);
  await shot(page, 'v4-interrupted-dark');
  await page.close();
}
}
await (await import('./v41-browser.mjs')).checkV41({ browser, open, origin, shot });
await browser.close();
assert.deepEqual(failures, []);
console.log('auto-run browser checks passed');
