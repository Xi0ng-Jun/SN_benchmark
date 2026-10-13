// Runs against a dedicated test state, never the user's review database.
import { chromium } from '@playwright/test';
import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
const url = process.env.WORKBENCH_TEST_URL;
assert(url, 'Set WORKBENCH_TEST_URL to the dedicated test server access link');
await mkdir('test-results', { recursive: true });
const browser = await chromium.launch({ headless: true, executablePath: process.env.WORKBENCH_CHROMIUM });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const failures = [];
page.on('pageerror', error => failures.push(String(error)));
await page.goto(url);
await page.getByTestId('new-task').waitFor();
assert.equal(await page.getByTestId('toggle-left-sidebar').count(), 1, 'desktop sidebar must have a collapse control');
await page.getByTestId('new-task').click();
await page.getByTestId('task-title').fill('浏览器验收任务');
await page.getByTestId('task-goal').fill('核对真实分数、证据和持久保存');
await page.getByTestId('create-task').click();
await page.getByTestId('import-qmsum-sn-smoke').click();
await page.getByTestId('result-card').waitFor();
await page.getByTestId('open-cases').click();
await page.getByTestId('case-row').first().click();
await page.getByTestId('case-answer').waitFor();
// Controlled composer branches keep separate drafts and purposes.
await page.getByRole('button', { name: '引用此案例到调查笔记', exact: true }).click();
assert.match(await page.getByTestId('task-composer').innerText(), /案例引用/);
await page.getByTestId('note-input').fill('笔记甲');
await page.getByTestId('mode-message').click();
assert.doesNotMatch(await page.getByTestId('task-composer').innerText(), /案例引用/);
await page.getByTestId('message-input').fill('消息乙');
assert.equal(await page.getByTestId('send-message').isEnabled(), false);
await page.getByText('发送尚未接入', { exact: true }).waitFor();
await page.getByTestId('mode-note').click();
assert.equal(await page.getByTestId('note-input').inputValue(), '笔记甲');
await page.getByTestId('mode-message').click();
assert.equal(await page.getByTestId('message-input').inputValue(), '消息乙');
await page.getByTestId('mode-note').click();
await page.getByTestId('review-reason').fill('未保存的理由应在切换材料后保留');
await page.getByTestId('toggle-right-sidebar').click();
assert.equal(await page.getByTestId('workspace-materials').isVisible(), false);
await page.keyboard.press('Control+e');
assert.equal(await page.getByTestId('review-reason').inputValue(), '未保存的理由应在切换材料后保留');
await page.getByTestId('panel-scores').click();
await page.getByTestId('panel-cases').click();
assert.equal(await page.getByTestId('review-reason').inputValue(), '未保存的理由应在切换材料后保留');
await page.getByTestId('review-reason').fill('浏览器验收：仅记录观察，不作为正式人工判断');
await page.getByTestId('save-review').click();
await page.getByTestId('review-receipt').waitFor();
await page.getByTestId('note-input').fill('浏览器验收调查笔记：费用与真实失败原因尚未核验');
await page.getByTestId('save-note').dispatchEvent('click');
await page.getByText('浏览器验收调查笔记：费用与真实失败原因尚未核验', { exact: true }).waitFor();
// Commit the note on the server, then lose the response. Retry must use the
// original key and return one event, while keeping the user's input on failure.
const taskId = await page.evaluate(() => new URLSearchParams(location.hash.slice(1)).get('task'));
const before = await page.evaluate(async id => (await (await fetch('/api/tasks/' + id)).json()).history.filter(e => e.kind === 'note.saved').length, taskId);
await page.route('**/api/tasks/*/notes', async route => { await route.fetch(); await route.abort('failed'); });
await page.getByTestId('note-input').fill('回执丢失验收：只能保存一次');
await page.getByTestId('save-note').click();
await page.getByText('Failed to fetch', { exact: true }).waitFor();
assert.equal(await page.getByTestId('note-input').inputValue(), '回执丢失验收：只能保存一次');
await page.unroute('**/api/tasks/*/notes');
await page.getByTestId('save-note').click();
await page.getByText('回执丢失验收：只能保存一次', { exact: true }).waitFor();
const after = await page.evaluate(async id => (await (await fetch('/api/tasks/' + id)).json()).history.filter(e => e.kind === 'note.saved').length, taskId);
assert.equal(after, before + 1);

function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}

async function createTask(target, title) {
  const previousTask = await target.evaluate(() => new URLSearchParams(location.hash.slice(1)).get('task'));
  await target.getByTestId('new-task').click();
  await target.getByTestId('task-title').fill(title);
  await target.getByTestId('task-goal').fill('验证延迟笔记回执的任务与草稿归属');
  await target.getByTestId('create-task').click();
  await target.waitForFunction(previous => new URLSearchParams(location.hash.slice(1)).get('task') !== previous, previousTask);
  await target.getByTestId('task-composer').waitFor();
  return target.evaluate(() => new URLSearchParams(location.hash.slice(1)).get('task'));
}

async function holdNoteRequest(target, response) {
  const seen = deferred();
  const completed = deferred();
  const release = deferred();
  await target.route('**/api/tasks/*/notes', async route => {
    seen.resolve();
    await release.promise;
    if (response) await route.fulfill(response);
    else await route.continue();
    completed.resolve();
  });
  return { seen, completed, release };
}

// A late success for task A must not publish a notice, refresh or saved status
// on the current task B. The server still receives the A note.
const delayedSuccessTask = await createTask(page, '延迟成功归属 A');
const delayedSuccess = await holdNoteRequest(page);
await page.getByTestId('note-input').fill('任务A延迟成功事实');
assert.equal(await page.getByTestId('note-input').inputValue(), '任务A延迟成功事实');
assert.equal(await page.getByTestId('save-note').isEnabled(), true);
await page.getByTestId('save-note').dispatchEvent('click');
await delayedSuccess.seen;
const switchedTask = await createTask(page, '切换后当前任务 B');
assert.notEqual(switchedTask, delayedSuccessTask);
let switchedTaskRefreshes = 0;
await page.route('**/api/tasks/' + switchedTask, async route => {
  switchedTaskRefreshes += 1;
  await route.continue();
});
delayedSuccess.release.resolve();
await delayedSuccess.completed;
assert.equal(await page.getByText('调查笔记已持久保存', { exact: true }).count(), 0);
assert.equal(await page.getByText('已保存', { exact: true }).count(), 0);
assert.equal(await page.getByText('任务A延迟成功事实', { exact: true }).count(), 0);
assert.equal(switchedTaskRefreshes, 0);
await page.unroute('**/api/tasks/*/notes');
await page.unroute('**/api/tasks/' + switchedTask);
await page.getByTestId('task-nav-' + delayedSuccessTask).click();
await page.getByText('任务A延迟成功事实', { exact: true }).waitFor();

// A late failure after changing the same task's reference must not mark the
// new draft as failed or surface the old error globally.
const secondPage = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
await secondPage.goto(url);
await secondPage.getByTestId('task-composer').waitFor();
const referenceTask = await createTask(secondPage, '同任务引用归属 C');
await secondPage.getByTestId('resource-import-qmsum-sn-smoke').click();
await secondPage.getByTestId('result-card').waitFor();
await secondPage.getByTestId('open-cases').click();
await secondPage.getByTestId('case-row').first().click();
await secondPage.getByTestId('case-answer').waitFor();
await secondPage.getByRole('button', { name: '引用此案例到调查笔记', exact: true }).click();
await secondPage.getByTestId('note-input').fill('同任务引用变更后保留草稿');
const delayedFailure = await holdNoteRequest(secondPage, {
  status: 500,
  contentType: 'application/json',
  body: JSON.stringify({ error: '延迟失败旧草稿' }),
});
assert.equal(await secondPage.getByTestId('note-input').inputValue(), '同任务引用变更后保留草稿');
assert.equal(await secondPage.getByTestId('save-note').isEnabled(), true);
await secondPage.getByTestId('save-note').dispatchEvent('click');
await delayedFailure.seen;
await secondPage.getByRole('button', { name: '返回案例列表', exact: true }).click();
await secondPage.getByTestId('case-row').nth(1).click();
await secondPage.getByTestId('case-answer').waitFor();
await secondPage.getByRole('button', { name: '引用此案例到调查笔记', exact: true }).click();
delayedFailure.release.resolve();
await delayedFailure.completed;
assert.equal(await secondPage.getByText('延迟失败旧草稿', { exact: true }).count(), 0);
assert.equal(await secondPage.getByTestId('note-input').inputValue(), '同任务引用变更后保留草稿');
assert.equal(await secondPage.getByTestId('task-composer').getByText(/案例引用/).count(), 1);
assert.equal(await secondPage.getByText('保存失败', { exact: false }).count(), 0);
assert.equal(await secondPage.getByText('保存中…', { exact: true }).count(), 0);
assert.equal(await secondPage.getByText('调查笔记已持久保存', { exact: true }).count(), 0);
await secondPage.unroute('**/api/tasks/*/notes');
assert.equal(await secondPage.evaluate(() => new URLSearchParams(location.hash.slice(1)).get('task')), referenceTask);
await secondPage.close();
await page.getByTestId('task-nav-' + taskId).click();
await page.getByTestId('open-cases').click();
await page.getByTestId('case-row').first().click();
await page.getByTestId('case-answer').waitFor();
await page.reload();
await page.getByTestId('case-answer').waitFor();
assert.match(await page.getByTestId('review-reason').inputValue(), /浏览器验收/);
await page.screenshot({ path: 'test-results/workspace-desktop.png' });
async function resize(testId, delta) {
  const handle = await page.getByTestId(testId).boundingBox();
  assert(handle);
  const x = handle.x + handle.width / 2, y = handle.y + 100;
  await page.mouse.move(x, y); await page.mouse.down();
  await page.mouse.move(x + delta, y, { steps: 8 }); await page.mouse.up();
}
const leftBefore = (await page.getByTestId('workspace-sidebar').boundingBox()).width;
await resize('resize-left-sidebar', 70);
const leftAfter = (await page.getByTestId('workspace-sidebar').boundingBox()).width;
assert(leftAfter > leftBefore + 50);
await page.getByTestId('toggle-left-sidebar').click();
assert.equal(await page.getByTestId('workspace-sidebar').isVisible(), false);
await page.getByTestId('toggle-left-sidebar').click();
const rightBefore = (await page.getByTestId('workspace-materials').boundingBox()).width;
await resize('resize-right-sidebar', -70);
const rightAfter = (await page.getByTestId('workspace-materials').boundingBox()).width;
assert(rightAfter > rightBefore + 50);
await page.reload(); await page.getByTestId('case-answer').waitFor();
assert.equal((await page.getByTestId('workspace-sidebar').boundingBox()).width, leftAfter);
assert.equal((await page.getByTestId('workspace-materials').boundingBox()).width, rightAfter);
await page.setViewportSize({ width: 960, height: 1000 });
// RN's resize observation settles after the browser viewport call returns.
await page.waitForFunction(() => document.querySelector('[data-testid="work-surface"]').getBoundingClientRect().width >= 400);
assert((await page.getByTestId('work-surface').boundingBox()).width >= 400);
assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth), false);
await page.setViewportSize({ width: 1440, height: 1000 });
await page.getByTestId('panel-history').click();
await page.getByTestId('history-review').first().click();
await page.getByText('固定历史记录', { exact: true }).waitFor();
await page.setViewportSize({ width: 390, height: 844 });
await page.screenshot({ path: 'test-results/workspace-compact.png' });
assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth), false);
await page.getByTestId('close-material').click();
await page.getByTestId('toggle-left-sidebar').click();
await page.getByTestId('close-sidebar').click();
await page.getByTestId('theme-toggle').click();
await page.screenshot({ path: 'test-results/workspace-dark.png' });
assert.deepEqual(failures, []);
console.log('Browser flow passed: real result, evidence, draft retention, review, lost response retry, note, reload, history, sidebar toggles/resize/persistence/960px, compact and dark theme');
await browser.close();
