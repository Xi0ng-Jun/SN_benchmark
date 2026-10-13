// Real App, HTTP and durable intents; creation-browser-server.py substitutes
// the external creation adapter. No daemon, model, or preview state is used.
import { chromium } from '@playwright/test';
import assert from 'node:assert/strict';
assert(process.env.WORKBENCH_TEST_URL, 'Dedicated temporary-state URL required');
const browser = await chromium.launch({ headless: true, executablePath: process.env.WORKBENCH_CHROMIUM });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const errors = [];
page.on('pageerror', error => errors.push(String(error)));
const taskId = () => page.evaluate(() => new URLSearchParams(location.hash.slice(1)).get('task'));
const newTask = async title => {
  const before = await taskId();
  await page.getByTestId('new-task').click();
  await page.getByTestId('task-title').fill(title);
  await page.getByTestId('create-task').click();
  await page.waitForFunction(old => new URLSearchParams(location.hash.slice(1)).get('task') !== old, before);
  await page.getByTestId('agent-creation-open').waitFor();
  return taskId();
};
const open = () => page.getByTestId('agent-creation-open').click();
const check = async () => {
  const pending = page.getByTestId('agent-creation-check-pending');
  if (await pending.count()) await pending.click();
  else await page.getByTestId('agent-creation-check').click();
};
const records = id => page.evaluate(async id => (await fetch(`/api/tasks/${id}/agent-creations`)).json(), id);
const saved = () => page.getByTestId('agent-creation-receipt').getByText('已读回 Agent，并关联当前任务', { exact: true }).waitFor();
try {
  await page.goto(process.env.WORKBENCH_TEST_URL);
  await page.getByTestId('new-task').waitFor();
  if (process.env.WORKBENCH_CREATION_REPRO !== 'config-removed') {
    const rejectedTask = await newTask('真实校验 422 纠正路径'); await open();
    await page.getByTestId('agent-creation-profile-allowed').click();
    await page.getByTestId('agent-creation-title').fill(' leading-title');
    const rejectedResponse = page.waitForResponse(response => response.url().endsWith(`/tasks/${rejectedTask}/agent-creations`) && response.request().method() === 'POST');
    await page.getByTestId('agent-creation-create').click(); const rejection = await rejectedResponse; assert.equal(rejection.status(), 422);
    const rejectedBody = rejection.request().postDataJSON(); assert.equal(rejectedBody.title, ' leading-title', 'submitted title is preserved');
    await page.getByTestId('agent-creation-panel').getByText(/创建失败/).first().waitFor();
    assert.deepEqual(await records(rejectedTask), [], 'real validation 422 persisted zero intents');
    assert.equal(await page.getByTestId('agent-creation-new-intent').count(), 1, 'pre-write rejection has an explicit correction path');
    assert.equal(await page.getByTestId('agent-creation-create').isEnabled(), false, 'cannot silently reuse failed frozen payload');
    await page.getByTestId('agent-creation-new-intent').click();
    await page.getByTestId('agent-creation-profile-allowed').click();
    await page.getByTestId('agent-creation-title').fill('corrected-title');
    const correctedResponse = page.waitForResponse(response => response.url().endsWith(`/tasks/${rejectedTask}/agent-creations`) && response.request().method() === 'POST');
    await page.getByTestId('agent-creation-create').click(); await saved();
    assert.notEqual((await correctedResponse).request().postDataJSON().request_key, rejectedBody.request_key);
    assert.equal((await records(rejectedTask)).length, 1);
  }
  const a = await newTask('创建空白 Agent 独立验收 A');
  await open();
  assert.equal(await page.getByTestId('agent-creation-create').isEnabled(), false, 'no default profile');
  await page.getByTestId('agent-creation-profile-allowed').click();
  await page.getByTestId('agent-creation-title').fill('   ');
  assert.equal(await page.getByTestId('agent-creation-create').isEnabled(), false, 'invalid title blocks');
  await page.getByTestId('agent-creation-title').fill('');
  const bodies = [];
  await page.route(`**/api/tasks/${a}/agent-creations`, async route => {
    if (route.request().method() === 'POST') bodies.push(route.request().postDataJSON());
    await route.continue();
  });
  await page.getByTestId('agent-creation-create').click();
  await saved();
  assert.deepEqual(Object.keys(bodies[0]).sort(), ['profile_id', 'request_key']);
  assert.equal((await records(a))[0].title, null, 'optional submitted title is not actual default title');
  const firstCreated = (await records(a))[0];
  await page.getByTestId('task-session-panel').getByRole('button', { name: new RegExp('Agent ' + firstCreated.agent_id) }).waitFor();
  assert.equal(await page.getByTestId('task-session-panel').getByRole('button', { name: new RegExp('Agent ' + firstCreated.agent_id) }).count(), 1,
    'created Task association becomes locally selectable without selecting it');
  assert.equal(await page.getByTestId('agent-creation-create').isEnabled(), false);
  assert.equal(await page.getByTestId('session-refresh').isEnabled(), false, 'created does not select session');
  await page.getByTestId('agent-creation-new-intent').click();
  await page.getByTestId('agent-creation-profile-allowed').click();
  await page.getByTestId('agent-creation-title').fill('not-ready');
  await page.getByTestId('agent-creation-create').click();
  await saved();
  await page.getByTestId('agent-creation-receipt-ready').getByText('读回就绪：否', { exact: true }).waitFor();
  assert.match(await page.getByTestId('agent-creation-receipt').innerText(), /读回时非空闲.*连接清理尚未确认/s);
  assert.equal((await records(a)).length, 2);
  const unreadyRecord = (await records(a)).find(record => record.title === 'not-ready');
  await page.getByTestId('task-session-panel').getByRole('button', { name: new RegExp('Agent ' + unreadyRecord.agent_id) }).waitFor();
  assert.equal(await page.getByTestId('session-refresh').isEnabled(), false);
  // Each same-key payload corruption is independent; GET must retain the
  // submitted form and unknown lock, then recover only the original payload.
  for (const field of ['profile_id', 'title']) {
    const originals = await records(a);
    await page.unroute(`**/api/tasks/${a}/agent-creations`);
    await page.route(`**/api/tasks/${a}/agent-creations`, route => route.fulfill({ json: originals.map(record => record.id === unreadyRecord.id
      ? { ...record, [field]: field === 'title' ? 'forged-title' : 'second' } : record) }));
    await check();
    await page.getByTestId('agent-creation-panel').getByText(/创建记录与冻结意图不一致/).waitFor();
    assert.equal(await page.getByTestId('agent-creation-title').inputValue(), 'not-ready');
    assert.equal(await page.getByTestId('agent-creation-receipt').getByText('已读回 Agent，并关联当前任务', { exact: true }).count(), 0);
    assert.equal(await page.getByTestId('agent-creation-create').isEnabled(), false);
    assert.equal(await page.getByTestId('agent-creation-new-intent').count(), 0);
    await page.unroute(`**/api/tasks/${a}/agent-creations`);
    await check(); await saved();
  }
  // Commit once, lose the browser response, then reconcile by exact local key.
  await page.getByTestId('agent-creation-new-intent').click();
  await page.getByTestId('agent-creation-profile-allowed').click();
  await page.getByTestId('agent-creation-title').fill('lost-response');
  await page.unroute(`**/api/tasks/${a}/agent-creations`);
  let lostBody;
  await page.route(`**/api/tasks/${a}/agent-creations`, async route => {
    if (route.request().method() === 'POST') {
      lostBody = route.request().postDataJSON(); await route.fetch(); await route.abort();
    } else await route.continue();
  });
  await page.getByTestId('agent-creation-create').click();
  await page.getByTestId('agent-creation-panel').getByText('创建结果尚未确认，请核对本地创建记录', { exact: true }).first().waitFor();
  assert.equal(await page.getByTestId('agent-creation-title').isEditable(), false);
  assert.equal(await page.getByTestId('agent-creation-new-intent').count(), 0);
  await page.getByTestId('agent-creation-cancel').click();
  await open();
  assert.equal(await page.getByTestId('agent-creation-create').isEnabled(), false, 'closing retains unknown intent');
  await page.unroute(`**/api/tasks/${a}/agent-creations`);
  await page.route(`**/api/tasks/${a}/agent-creations`, route => route.fulfill({ json: [] }));
  await check();
  await page.getByTestId('agent-creation-panel').getByText(/尚未找到本地创建记录/).waitFor();
  assert.equal(await page.getByTestId('agent-creation-create').isEnabled(), false);
  await page.unroute(`**/api/tasks/${a}/agent-creations`);
  await check();
  await saved();
  const recovered = (await records(a)).find(record => record.request_key === lostBody.request_key);
  assert(recovered, 'local original key is the recovery identity');
  assert.equal((await records(a)).length, 3, 'GET reconciliation never creates');
  // Keep an ACK pending across A -> B -> A; it must await an explicit check.
  await page.getByTestId('agent-creation-new-intent').click();
  await page.getByTestId('agent-creation-profile-second').click();
  await page.getByTestId('agent-creation-title').fill('late-A');
  let held; let releaseHeld;
  const pending = new Promise(resolve => { releaseHeld = resolve; });
  await page.route(`**/api/tasks/${a}/agent-creations`, async route => {
    if (route.request().method() === 'POST') {
      held = { route, response: await route.fetch() }; releaseHeld();
    } else await route.continue();
  });
  await page.getByTestId('agent-creation-create').click();
  await pending;
  const b = await newTask('创建验收 B');
  await open();
  await page.getByTestId('agent-creation-profile-allowed').click();
  await page.getByTestId('agent-creation-title').fill('B 未提交草稿');
  await page.getByTestId('task-nav-' + a).click();
  await page.waitForFunction(id => new URLSearchParams(location.hash.slice(1)).get('task') === id, a);
  await page.getByTestId('agent-creation-title').waitFor();
  await held.route.fulfill({ response: held.response });
  await page.unroute(`**/api/tasks/${a}/agent-creations`);
  assert.equal(await page.getByTestId('agent-creation-receipt').getByText('已读回 Agent，并关联当前任务', { exact: true }).count(), 0);
  await check();
  await saved();
  // Abandon a read-only check across A -> B -> A. Its late result cannot
  // publish, and the returned scope must permit a new explicit check.
  const lateRecord = (await records(a)).find(record => record.title === 'late-A');
  const beforeCheck = await records(a);
  await page.route(`**/api/tasks/${a}/agent-creations`, route => route.fulfill({ json: beforeCheck.map(record => record.id === lateRecord.id
    ? { ...record, title: 'forged-late-title' } : record) }));
  await check(); await page.getByTestId('agent-creation-panel').getByText(/创建记录与冻结意图不一致/).waitFor();
  await page.unroute(`**/api/tasks/${a}/agent-creations`);
  let oldCheck; let checkEntered;
  const readEntered = new Promise(resolve => { checkEntered = resolve; });
  await page.route(`**/api/tasks/${a}/agent-creations`, async route => {
    if (!oldCheck && route.request().method() === 'GET') { oldCheck = { route, response: await route.fetch() }; checkEntered(); }
    else await route.continue();
  });
  await check(); await readEntered;
  assert.equal(await page.getByTestId('agent-creation-title').isEditable(), false);
  for (const button of await page.locator('[data-testid^="agent-creation-record-"]').all()) assert.equal(await button.isEnabled(), false);
  await page.getByTestId('task-nav-' + b).click();
  await page.waitForFunction(id => new URLSearchParams(location.hash.slice(1)).get('task') === id, b);
  await page.getByTestId('agent-creation-title').waitFor();
  await page.getByTestId('task-nav-' + a).click();
  await page.waitForFunction(id => new URLSearchParams(location.hash.slice(1)).get('task') === id, a);
  await page.getByTestId('agent-creation-title').waitFor();
  await oldCheck.route.fulfill({ response: oldCheck.response });
  await page.unroute(`**/api/tasks/${a}/agent-creations`);
  assert.equal(await page.getByTestId('agent-creation-receipt').getByText('已读回 Agent，并关联当前任务', { exact: true }).count(), 0);
  await page.getByTestId('agent-creation-check').waitFor();
  await check(); await saved();
  await page.getByTestId('task-nav-' + b).click();
  await page.waitForFunction(id => new URLSearchParams(location.hash.slice(1)).get('task') === id, b);
  assert.equal(await page.getByTestId('agent-creation-title').inputValue(), 'B 未提交草稿');
  assert.equal((await records(b)).length, 0);
  await page.reload();
  await page.getByTestId('agent-creation-open').waitFor();
  await page.getByTestId('task-nav-' + a).click();
  await open();
  await page.getByTestId('agent-creation-record-' + recovered.id).click();
  await saved();
  assert.equal(await page.getByTestId('agent-creation-title').inputValue(), 'lost-response');
  await page.getByTestId('agent-creation-new-intent').click();
  assert.equal(await page.getByTestId('agent-creation-create').isEnabled(), false, 'new intent has no default profile');
  await page.getByTestId('agent-creation-profile-allowed').click();
  await page.getByTestId('agent-creation-title').fill('incomplete');
  await page.getByTestId('agent-creation-create').click(); await saved();
  await page.getByTestId('agent-creation-receipt-ready').getByText('读回就绪：否', { exact: true }).waitFor();
  assert.match(await page.getByTestId('agent-creation-receipt').innerText(), /读回信息不完整/);
  const incomplete = (await records(a)).find(record => record.title === 'incomplete');
  assert.equal(incomplete.envelope.result.agent.status, undefined);
  assert.equal(incomplete.envelope.result.readiness, 'incomplete_snapshot');
  await page.getByTestId('task-session-panel').getByRole('button', { name: new RegExp('Agent ' + incomplete.agent_id) }).waitFor();
  assert.equal(await page.getByTestId('session-refresh').isEnabled(), false, 'incomplete created is not automatically selected');
  // Legal release-unknown is separate from observed creation and readiness.
  await page.getByTestId('agent-creation-new-intent').click();
  await page.getByTestId('agent-creation-profile-allowed').click();
  await page.getByTestId('agent-creation-title').fill('release-unknown');
  const releaseResponse = page.waitForResponse(response => response.url().endsWith(`/tasks/${a}/agent-creations`) && response.request().method() === 'POST');
  await page.getByTestId('agent-creation-create').click();
  await releaseResponse;
  if (process.env.WORKBENCH_CREATION_REPRO !== 'config-removed') await saved();
  const releaseUnknown = (await records(a)).find(record => record.title === 'release-unknown');
  assert.equal(releaseUnknown.envelope.release.code, 'BRIDGE_RELEASE_UNKNOWN');
  await page.evaluate(async () => { const response = await fetch('/__fixture/remove-creation-config', { method: 'POST' }); if (!response.ok) throw new Error('fixture config removal failed'); });
  await page.reload(); await page.getByTestId('new-task').waitFor();
  const remaining = await records(a);
  assert(remaining.some(record => record.id === incomplete.id), 'real GET retains durable records without config');
  assert.equal(await page.getByTestId('agent-creation-open').count(), 1, 'read-only recovery remains reachable without creation capability');
  await open();
  assert.equal(await page.getByTestId('agent-creation-profile-allowed').count(), 0, 'historical profile is never a new allowed choice');
  await page.getByTestId('agent-creation-record-' + incomplete.id).click();
  await page.getByTestId('agent-creation-history-receipt').waitFor();
  assert.match(await page.getByTestId('agent-creation-history-receipt').innerText(), /allowed.*incomplete_snapshot/s);
  assert.equal(await page.getByTestId('agent-creation-create').isEnabled(), false);
  await check(); await page.getByTestId('agent-creation-history-receipt').waitFor();
  await page.getByTestId('agent-creation-record-' + releaseUnknown.id).click();
  assert.match(await page.getByTestId('agent-creation-history-receipt').innerText(), /BRIDGE_RELEASE_UNKNOWN/);
  assert.equal((await records(a)).length, remaining.length, 'read-only restoration never POSTs');
  await page.getByTestId('theme-toggle').click();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForFunction(() => document.documentElement.scrollWidth <= innerWidth);
  assert.deepEqual(errors, []);
  console.log('Creation parent checks passed: optional title, frozen GET fields, incomplete created, readiness/release, Task session refresh, loss recovery, late POST/GET isolation and local reopen');
} finally { await browser.close(); }
