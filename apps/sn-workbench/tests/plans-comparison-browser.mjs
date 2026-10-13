// Actual built App + isolated HTTP/SQLite fixture. No model/Job/preview writes.
import { chromium } from '@playwright/test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';

const origin = process.env.WORKBENCH_BC_URL ?? 'http://127.0.0.1:18101';
const browser = await chromium.launch({ headless: true, executablePath: process.env.WORKBENCH_CHROMIUM });
const errors = [];
let checks = 0;
try {
  const context = await browser.newContext();
  const session = await context.request.post(`${origin}/api/session`, { data: { token: 'bc-browser-fixture' } });
  assert.equal(session.status(), 200);
  const boot = await (await context.request.get(`${origin}/api/bootstrap`)).json();
  const tasks = boot.workspaces.flatMap(workspace => workspace.tasks);
  const comparisonTask = tasks.find(task => task.title === 'Compare fixtures');
  assert.ok(comparisonTask);
  const task = await (await context.request.get(`${origin}/api/tasks/${comparisonTask.id}`)).json();
  const page = await context.newPage();
  page.on('pageerror', error => errors.push(String(error)));
  await page.goto(`${origin}/#task=${comparisonTask.id}`);
  await page.getByTestId('view-results').click();
  await page.getByTestId('open-comparison').click();
  await page.getByTestId(`left-${task.results[0].id}`).click();
  await page.getByTestId(`right-${task.results[1].id}`).click();
  await page.getByTestId('comparison-read').click();
  await page.getByTestId('comparison-official').waitFor();
  assert.match(await page.getByTestId('comparison-official').innerText(), /0\.4/);
  assert.match(await page.getByTestId('comparison-official').innerText(), /0\.5/);
  assert.match(await page.getByTestId('comparison-official').innerText(), /本次比较题数：2/);
  await page.getByTestId('comparison-paired').waitFor(); checks += 4;
  // Selection changes cannot retain an official report under new identities.
  await page.getByTestId(`right-${task.results[0].id}`).click();
  assert.equal(await page.getByTestId('comparison-official').count(), 1); // same-ID choice is rejected.
  checks++;
  await page.getByTestId('open-left-result').click();
  await page.getByText('完整声明范围的成绩', { exact: true }).waitFor(); checks++;
  await page.getByTestId('toggle-right-sidebar').click();
  await page.getByRole('button', { name: '工作区设置', exact: true }).first().click();
  await page.getByTestId('settings-theme-dark').click();
  await page.getByTestId('settings-close').click();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForFunction(() => document.documentElement.scrollWidth <= innerWidth);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false); checks++;
  const rejectedTask = tasks.find(task => task.title === 'Compare rejected');
  const rejected = await (await context.request.get(`${origin}/api/tasks/${rejectedTask.id}`)).json();
  await page.getByTestId('toggle-left-sidebar').click();
  await page.getByTestId(`task-nav-${rejectedTask.id}`).click();
  await page.getByTestId('view-results').click();
  await page.getByTestId('open-comparison').click();
  await page.getByTestId(`left-${rejected.results[0].id}`).click();
  await page.getByTestId(`right-${rejected.results[1].id}`).click();
  await page.getByTestId('comparison-read').click();
  await page.getByText('两份结果当前不能配对比较。', { exact: true }).waitFor();
  assert.equal(await page.getByTestId('comparison-official').count(), 0);
  assert.match(await page.getByTestId('comparison-status').innerText(), /scorer/i);
  await page.getByTestId('open-right-result').click();
  await page.getByText('完整声明范围的成绩', { exact: true }).waitFor(); checks += 3;
  // Task with no benchmark input/result retains an explicit readable reason.
  const planTask = tasks.find(task => task.title === 'Plan A');
  await page.getByTestId('close-material').click();
  await page.getByTestId('toggle-left-sidebar').click();
  await page.getByTestId(`task-nav-${planTask.id}`).click();
  await page.getByTestId('view-results').click();
  await page.getByTestId('open-comparison').click();
  assert.match(await page.getByTestId('comparison-panel').innerText(), /当前任务只有 0 份/);
  assert.equal(await page.getByTestId('comparison-read').getAttribute('aria-disabled'), 'true'); checks += 2;
  // Hold a real read across parent Task navigation. Aborted old responses
  // cannot publish into the next Task, even when the transport still settles.
  await page.getByTestId('toggle-left-sidebar').click();
  await page.getByTestId(`task-nav-${comparisonTask.id}`).click();
  await page.getByTestId('view-results').click();
  await page.getByTestId('open-comparison').click();
  await page.getByTestId(`left-${task.results[0].id}`).click();
  await page.getByTestId(`right-${task.results[1].id}`).click();
  let signalRead, releaseRead, finishRead;
  const seenRead = new Promise(resolve => { signalRead = resolve; });
  const heldRead = new Promise(resolve => { releaseRead = resolve; });
  const finishedRead = new Promise(resolve => { finishRead = resolve; });
  const comparisonPattern = `**/api/tasks/${comparisonTask.id}/comparison?*`;
  await page.route(comparisonPattern, async route => {
    const response = await route.fetch(); signalRead();
    await heldRead;
    try { await route.fulfill({ response }); } finally { finishRead(); }
  });
  await page.getByTestId('comparison-read').click(); await seenRead;
  await page.getByTestId('toggle-left-sidebar').click();
  await page.getByTestId(`task-nav-${planTask.id}`).click();
  releaseRead(); await finishedRead;
  await page.unroute(comparisonPattern);
  assert.equal(await page.getByTestId('comparison-official').count(), 0);
  assert.equal(await page.getByTestId('comparison-panel').isVisible(), false); checks += 2;
  await page.getByTestId('view-results').click();
  await page.getByTestId('open-plans').click();
  await page.getByTestId('plan-title').fill('First saved evaluation intent');
  await page.getByTestId('input-canonical').click();
  const descriptor = await (await context.request.get(`${origin}/api/inputs/canonical`)).json();
  if ((await page.getByTestId('plan-paste-toggle').innerText()).includes('粘贴')) await page.getByTestId('plan-paste-toggle').click();
  await page.getByTestId('plan-case-ids').fill(descriptor.case_ids.join('\n'));
  await page.getByTestId('plan-save').click();
  await page.getByText('已保存草稿', { exact: true }).waitFor();
  const first = (await (await context.request.get(`${origin}/api/tasks/${planTask.id}/plans`)).json())[0];
  assert.equal(first.state, 'draft'); assert.equal(first.execution, false);
  assert.equal(first.content.source_intent.binding, 'not_captured');
  assert.equal(first.content.execution_intent.model_services_ref, null);
  assert.equal(first.content.execution_intent.official_scorer_ref, null); checks += 5;
  await page.reload();
  await page.getByTestId('view-results').click();
  await page.getByTestId('open-plans').click();
  await page.getByTestId(`plan-record-${first.revision}`).click();
  await page.getByTestId('plan-fixed-detail').waitFor();
  assert.match(await page.getByTestId('plan-fixed-detail').innerText(), new RegExp(first.revision));
  await page.getByTestId('plan-title').fill('Second saved evaluation intent');
  await page.getByTestId('plan-save').click();
  await page.getByText('已保存草稿', { exact: true }).waitFor();
  const second = (await (await context.request.get(`${origin}/api/tasks/${planTask.id}/plans`)).json())[0];
  assert.equal(second.parent_revision, first.revision);
  assert.equal(second.plan_id, first.plan_id);
  assert.notEqual(second.revision, first.revision); checks += 4;
  await page.getByTestId(`plan-record-${second.revision}`).waitFor(); checks++;
  const fixed = await (await context.request.get(`${origin}/api/tasks/${planTask.id}/plans/${first.plan_id}?revision=${first.revision}`)).json();
  assert.deepEqual(fixed, first); checks++;
  await page.getByTestId(`plan-revision-${first.revision}`).click();
  await page.getByTestId('plan-title').fill('Stale base must remain rejected');
  await page.getByTestId('plan-save').click();
  await page.getByText('本次命令被拒绝，正文可修改', { exact: true }).waitFor();
  assert.equal(await page.getByTestId('plan-title').inputValue(), 'Stale base must remain rejected');
  assert.equal((await (await context.request.get(`${origin}/api/tasks/${planTask.id}/plans`)).json())[0].revision, second.revision); checks += 2;
  // Drop a response after actual server persistence. Recovery is GET only.
  await page.getByTestId('plan-new').click();
  await page.getByTestId('plan-title').fill('Lost response intent');
  await page.getByTestId('input-canonical').click();
  if ((await page.getByTestId('plan-paste-toggle').innerText()).includes('粘贴')) await page.getByTestId('plan-paste-toggle').click();
  await page.getByTestId('plan-case-ids').fill(descriptor.case_ids[0]);
  let saves = 0; let frozenCommand;
  await page.route(`**/api/tasks/${planTask.id}/plans`, async route => {
    if (route.request().method() !== 'POST') { await route.continue(); return; }
    saves++; frozenCommand = route.request().postDataJSON();
    await route.fetch(); await route.abort('failed');
  });
  await page.getByTestId('plan-save').click();
  await page.getByText('保存结果未知，原命令与 key 保留', { exact: true }).waitFor();
  await page.getByTestId('plan-check').click();
  await page.getByText('已保存草稿', { exact: true }).waitFor();
  assert.equal(saves, 1);
  const receipt = await (await context.request.get(`${origin}/api/tasks/${planTask.id}/plans/receipt?request_key=${encodeURIComponent(frozenCommand.request_key)}`)).json();
  assert.equal(receipt.request_key, frozenCommand.request_key);
  assert.equal(receipt.record.title, 'Lost response intent'); checks += 3;
  const revisited = await (await context.request.get(`${origin}/api/tasks/${planTask.id}`)).json();
  assert.equal(revisited.history.filter(event => event.kind === 'plan.draft.saved').length, 3); checks++;
  await page.unroute(`**/api/tasks/${planTask.id}/plans`);
  // A pre-commit list must not be the final list after a later save.
  let signalList, releaseList, listReads = 0;
  const seenList = new Promise(resolve => { signalList = resolve; });
  const heldList = new Promise(resolve => { releaseList = resolve; });
  await page.route(`**/api/tasks/${planTask.id}/plans`, async route => {
    if (route.request().method() !== 'GET' || listReads++ > 0) { await route.continue(); return; }
    const response = await route.fetch(); signalList(); await heldList;
    await route.fulfill({ response });
  });
  await page.getByTestId('plans-refresh').click(); await seenList;
  await page.getByTestId('plan-new').click();
  await page.getByTestId('plan-title').fill('Saved while old list is pending');
  await page.getByTestId('input-canonical').click();
  if ((await page.getByTestId('plan-paste-toggle').innerText()).includes('粘贴')) await page.getByTestId('plan-paste-toggle').click();
  await page.getByTestId('plan-case-ids').fill(descriptor.case_ids[0]);
  await page.getByTestId('plan-save').click();
  await page.getByText('已保存草稿', { exact: true }).waitFor();
  const latest = (await (await context.request.get(`${origin}/api/tasks/${planTask.id}/plans`)).json())
    .find(record => record.title === 'Saved while old list is pending');
  assert.ok(latest);
  releaseList();
  await page.getByTestId(`plan-record-${latest.revision}`).waitFor();
  assert.ok(listReads >= 2); checks += 2;
  await page.waitForFunction(() => document.documentElement.scrollWidth <= innerWidth);
  if (process.env.WORKBENCH_BC_EVIDENCE) {
    mkdirSync(process.env.WORKBENCH_BC_EVIDENCE, { recursive: true });
    await page.screenshot({ path: join(process.env.WORKBENCH_BC_EVIDENCE, 'plan-light.png'), fullPage: true });
    await page.getByRole('button', { name: '工作区设置', exact: true }).first().click();
  await page.getByTestId('settings-theme-dark').click();
  await page.getByTestId('settings-close').click();
    await page.screenshot({ path: join(process.env.WORKBENCH_BC_EVIDENCE, 'plan-dark.png'), fullPage: true });
  }
  assert.deepEqual(errors, []);
  console.log(`Actual App plans/comparison: ${checks} checks passed; zero page errors.`);
} finally {
  await browser.close();
}
