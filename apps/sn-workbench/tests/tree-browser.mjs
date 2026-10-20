// Iteration-tree page against a running workbench with at least one evaluated child
// (e.g. the smoke state). Read-only unless a request id is given:
//   WORKBENCH_TEST_URL=http://127.0.0.1:PORT/#access=CODE  (access code from the state dir)
//   WORKBENCH_CONFIRM_REQUEST=<id>  confirm that pending request through the page
//   WORKBENCH_REJECT_REQUEST=<id>   reject it through the page
//   WORKBENCH_CHROMIUM=<path>       browser binary when Playwright's own build is absent
import { chromium } from '@playwright/test';
import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';

const url = process.env.WORKBENCH_TEST_URL;
assert(url, 'Set WORKBENCH_TEST_URL to the dedicated test server access link');
await mkdir('test-results', { recursive: true });
const browser = await chromium.launch({ headless: true, executablePath: process.env.WORKBENCH_CHROMIUM });
const failures = [];
const shot = (page, name) => page.screenshot({ path: `test-results/tree-${name}.png` });

for (const scheme of ['light', 'dark']) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, colorScheme: scheme });
  page.on('pageerror', error => failures.push(`${scheme}: ${error}`));
  await page.goto(url);
  await page.getByTestId('lineage').waitFor({ timeout: 20000 });
  const rows = page.locator('[data-testid^="lineage-row-"]');
  assert((await rows.count()) >= 2, 'the tree needs a root and at least one child');
  await shot(page, `${scheme}-lineage`);
  if (scheme === 'dark') { await page.close(); continue; }

  // Landing: with nothing selected, the page opens the node that waits on the person,
  // whose detail leads with one sentence and one primary action; the sidebar lists the rest.
  if (await page.locator('[data-testid^="todo-step-"]').count()) {
    await page.getByTestId('next-step').waitFor({ timeout: 15000 });
    assert.match(await page.getByTestId('next-step').innerText(), /下一步/);
    await shot(page, 'landing');
  }
  // An Agent marker on a row opens that Agent's conversation; the header says what it is bound to.
  const marker = page.locator('[data-testid^="lineage-agent-"]').first();
  if (await marker.count()) {
    const bound = (await marker.getAttribute('data-testid')).replace('lineage-agent-', '');
    await marker.click();
    await page.getByTestId('agent-binding').waitFor();
    await page.waitForFunction(() => /(实现|分析) · 候选/.test(document.querySelector('[data-testid="agent-binding"]')?.textContent ?? ''), null, { timeout: 15000 });
    await shot(page, 'agent-bound');
    const before = (await page.getByTestId('agent-drawer').boundingBox()).height;
    await page.getByTestId('maximize-drawer').click();
    assert.ok((await page.getByTestId('agent-drawer').boundingBox()).height > before + 100, 'maximize grows the drawer');
    await shot(page, 'agent-max');
    await page.getByTestId('maximize-drawer').click();
    const handle = await page.getByTestId('resize-drawer').boundingBox();
    await page.mouse.move(handle.x + 200, handle.y + 3); await page.mouse.down(); await page.mouse.move(handle.x + 200, handle.y - 120); await page.mouse.up();
    assert.ok((await page.getByTestId('agent-drawer').boundingBox()).height > before + 60, 'dragging the top edge resizes the drawer');
    await page.getByTestId('toggle-drawer').click();
    void bound;
  }

  // Node detail: sections stack in one column; Δ父 comes from the stored record.
  await rows.nth(1).click();
  await page.getByTestId('node-detail').waitFor();
  await page.getByTestId('section-runs').waitFor();
  await page.getByTestId('parent-flips').waitFor({ timeout: 15000 });
  assert.match(await page.getByTestId('parent-flips').innerText(), /记录 [0-9a-f]{8}/);
  await shot(page, 'detail');

  // Analysis is per node: "分析" continues the node's own analyst, or offers to create one
  // bound to it (cancelled here, nothing is created).
  const nodeLabel = (await rows.nth(1).innerText()).trim().split(/\s+/)[0];
  await page.getByTestId('act-analyst').click();
  const target = page.getByTestId('analyst-target');
  await Promise.race([target.waitFor({ timeout: 15000 }),
    page.waitForFunction(() => /分析 · 候选/.test(document.querySelector('[data-testid="agent-binding"]')?.textContent ?? ''), null, { timeout: 15000 })]);
  if (await target.count()) {
    assert.match(await target.innerText(), new RegExp(`为候选 ${nodeLabel} 新建分析 Agent`));
    assert.ok(await target.locator('[data-testid^="target-create-"]').count() > 0, 'the target offers its model buttons in place');
    await shot(page, 'analyst-target');
    await target.getByText('取消').click();
    assert.equal(await target.count(), 0);
  } else {
    assert.match(await page.getByTestId('agent-binding').innerText(), new RegExp(`分析 · 候选 ${nodeLabel}`));
    await shot(page, 'analyst-bound');
  }
  await page.getByTestId('toggle-drawer').click();

  // Every write goes through the confirm dialog; cancelling runs nothing.
  const before = await page.evaluate(async () => (await (await fetch(location.origin + '/api/iterations')).json()).length);
  await page.getByTestId('act-derive').click();
  await page.getByTestId('confirm-dialog').waitFor();
  assert.equal(await page.getByTestId('confirm-run').isEnabled(), false, 'a manual derive needs a note');
  await shot(page, 'confirm');
  await page.getByTestId('confirm-cancel').click();
  assert.equal(await page.getByTestId('confirm-dialog').count(), 0);

  // Rank view is flat and leaves the tree order alone.
  const treeOrder = await rows.evaluateAll(els => els.map(el => el.dataset.testid));
  await page.getByTestId('lineage-rank').click();
  await shot(page, 'rank');
  await page.getByTestId('lineage-tree').click();
  assert.deepEqual(await rows.evaluateAll(els => els.map(el => el.dataset.testid)), treeOrder);

  // Compare matrix: rows are frozen cases, columns the picked candidate and the root.
  const childId = treeOrder[1].replace('lineage-row-', '');
  await page.getByTestId('lineage-pick-' + childId).click();
  await page.getByTestId('compare-picked').click();
  await page.getByTestId('compare-matrix').waitFor();
  await page.locator(`[data-testid^="matrix-cell-${childId}-"]`).first().waitFor({ timeout: 15000 });
  await shot(page, 'matrix');
  await page.locator(`[data-testid^="matrix-cell-${childId}-"]`).first().click();
  await page.getByTestId('run-case-detail').waitFor({ timeout: 30000 });
  assert.equal(await page.getByTestId('close-material').isVisible(), true, 'the case reader keeps its own header');
  await shot(page, 'case');
  await page.getByTestId('matrix-close').click();

  // Ctrl+K lists navigation and actions; choosing an action opens the confirm dialog.
  await page.keyboard.press('Control+k');
  await page.getByTestId('command-palette').waitFor();
  await page.getByTestId('palette-input').fill('运行');
  await shot(page, 'palette');
  await page.keyboard.press('Escape');

  // New iteration: the start defaults to HEAD; a short hash resolves to the full commit;
  // a frozen candidate can be the seed. Cancelling creates nothing.
  await page.getByTestId('new-iteration').click();
  await page.getByTestId('start-selected').waitFor({ timeout: 15000 });
  assert.match(await page.getByTestId('start-selected').innerText(), /[0-9a-f]{40}[\s\S]*当前 HEAD/);
  const rootCommit = await page.evaluate(async () => {
    const its = await (await fetch(location.origin + '/api/iterations')).json();
    const tree = await (await fetch(location.origin + '/api/iterations/' + its.at(-1).id)).json();
    return tree.start_commit;
  });
  await page.getByTestId('start-query').fill(rootCommit.slice(0, 9));
  await page.getByTestId('start-resolve').click();
  await page.waitForFunction(full => document.querySelector('[data-testid="start-selected"]')?.textContent?.includes(full), rootCommit);
  const seed = page.locator('[data-testid^="start-seed-"]').first();
  if (await seed.count()) { await seed.click(); assert.match(await page.getByTestId('start-selected').innerText(), /继续/); }
  await shot(page, 'new-iteration');
  await page.getByRole('button', { name: '取消', exact: true }).last().click();

  // The Agent drawer states what the selected Agent can see.
  await page.getByTestId('toggle-drawer').click();
  await page.getByTestId('agent-drawer').waitFor();
  await shot(page, 'drawer');
  if (await page.getByTestId('agent-scope').count()) assert.match(await page.getByTestId('agent-scope').innerText(), /可见/);
  await page.getByTestId('toggle-drawer').click();
  assert.equal(await page.evaluate(async () => (await (await fetch(location.origin + '/api/iterations')).json()).length), before, 'read-only pass changed state');

  for (const [env, testid, note] of [['WORKBENCH_CONFIRM_REQUEST', 'request-confirm-', '浏览器验收：确认'], ['WORKBENCH_REJECT_REQUEST', 'request-reject-', '浏览器验收：驳回']]) {
    const id = process.env[env];
    if (!id) continue;
    const node = await page.evaluate(async rid => (await (await fetch(location.origin + '/api/action-requests/' + rid)).json()).target_id, id);
    await page.getByTestId('lineage-row-' + node).click();
    await page.getByTestId(testid + id).click();
    await page.getByTestId('confirm-dialog').waitFor();
    await shot(page, env === 'WORKBENCH_CONFIRM_REQUEST' ? 'request-confirm' : 'request-reject');
    await page.getByTestId('confirm-note').fill(note);
    await page.getByTestId('confirm-run').click();
    await page.getByTestId('confirm-dialog').waitFor({ state: 'detached', timeout: 60000 });
    const record = await page.evaluate(async rid => (await (await fetch(location.origin + '/api/action-requests/' + rid)).json()), id);
    assert.equal(record.status, env === 'WORKBENCH_CONFIRM_REQUEST' ? 'executed' : 'rejected', JSON.stringify(record));
    await page.waitForTimeout(1500);
    await shot(page, env === 'WORKBENCH_CONFIRM_REQUEST' ? 'after-confirm' : 'after-reject');
  }
  await page.close();
}

// Narrow screens: lineage full width; detail opens over it.
const narrow = await browser.newPage({ viewport: { width: 420, height: 860 } });
narrow.on('pageerror', error => failures.push(`narrow: ${error}`));
await narrow.goto(url);
await narrow.getByTestId('lineage').waitFor({ timeout: 20000 });
await narrow.locator('[data-testid^="lineage-row-"]').nth(1).click();
await narrow.getByTestId('close-detail').waitFor();
await shot(narrow, 'narrow-detail');
await browser.close();
assert.deepEqual(failures, []);
console.log('tree browser check passed');
