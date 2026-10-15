// Requires a dedicated fixture server configured with the no-model smoke executor.
import { chromium } from '@playwright/test';
import assert from 'node:assert/strict';
const origin = process.env.WORKBENCH_TEST_URL;
const taskId = process.env.WORKBENCH_TEST_TASK;
assert(origin && taskId && process.env.WORKBENCH_TEST_TOKEN, 'Use dedicated execution fixture state');
const browser = await chromium.launch({ headless: true, executablePath: process.env.WORKBENCH_CHROMIUM });
const context = await browser.newContext({ viewport: { width: 1440, height: 960 } });
await context.request.post(origin + '/api/session', { data: { token: process.env.WORKBENCH_TEST_TOKEN } });
const page = await context.newPage();
const errors = []; page.on('pageerror', e => errors.push(String(e)));
try {
  await page.goto(origin + '/#task=' + taskId + '&view=changes');
  const binding = page.getByTestId('bind-execution-worktree');
  await page.getByTestId('changes-page').waitFor();
  assert.equal(await binding.isEnabled(), false, 'multiple registrations require an explicit choice');
  const choices = page.locator('[data-testid^="execution-worktree-option-"]');
  assert.equal(await choices.count(), 2);
  assert.match(await choices.nth(1).innerText(), /Candidate source/);
  assert.match(await choices.nth(1).innerText(), /\/second/);
  await choices.nth(1).click();
  // Task navigation must not carry an unconfirmed worktree choice to another task.
  await page.getByTestId('new-task').click();
  await page.getByTestId('task-title').fill('Independent worktree selection');
  await page.getByTestId('create-task').click();
  await page.waitForFunction(previous => new URLSearchParams(location.hash.slice(1)).get('task') !== previous, taskId);
  const otherTaskId = await page.evaluate(() => new URLSearchParams(location.hash.slice(1)).get('task'));
  await page.getByTestId('view-changes').click();
  await binding.waitFor();
  assert.equal(await binding.isEnabled(), false, 'the other task needs its own explicit choice');
  await page.getByTestId('task-nav-' + taskId).click();
  await page.getByTestId('view-changes').click();
  await binding.waitFor();
  assert.equal(await binding.isEnabled(), false, 'an unsaved choice is reset when remounting the task');
  await choices.nth(1).click();
  const [bound] = await Promise.all([
    page.waitForRequest(request => request.url().endsWith(`/tasks/${taskId}/worktree`) && request.method() === 'POST'),
    binding.click(),
  ]);
  assert.deepEqual(bound.postDataJSON(), { worktree_id: 'second' });
  await page.getByTestId('execution-panel').waitFor({ timeout: 60000 });
  assert.equal(await choices.count(), 0, 'an existing binding must not be changed by the registration selector');
  await page.locator('[data-testid^="execution-plan-"]').first().click();
  assert.equal(await page.getByTestId('verify-run-button').count(), 0, 'preparation must precede launch');
  await page.getByTestId('verify-prepare').click();
  await page.getByTestId('verify-prepared').waitFor({ timeout: 60000 });
  assert.equal(await page.getByTestId('verify-budget').count(), 0);
  for (const invalid of ['0', '-1', '1.5', '86401', '']) {
    await page.getByTestId('verify-timeout').fill(invalid);
    assert.equal(await page.getByTestId('verify-run-button').isEnabled(), false, `invalid timeout: ${invalid}`);
  }
  for (const valid of ['1', '86400']) {
    await page.getByTestId('verify-timeout').fill(valid);
    assert.equal(await page.getByTestId('verify-run-button').isEnabled(), true, `valid timeout: ${valid}`);
  }
  await page.getByTestId('verify-timeout').fill('60');
  // A definite matching rejection must permit correction.
  await page.route('**/jobs/*/run', route => {
    const command = route.request().postDataJSON();
    assert.deepEqual(command.budget, { timeout_seconds: 60 }, 'new requests must not enforce a model cost budget');
    return route.fulfill({ status: 422, contentType: 'application/json', body: JSON.stringify({
      error: 'test run rejection', code: 'JOB_COMMAND_REJECTED', operation: 'run', request_key: command.request_key,
    }) });
  });
  await page.getByTestId('verify-run-button').click();
  await page.getByTestId('verify-error').getByText(/运行被拒绝/).waitFor();
  assert.equal(await page.getByTestId('verify-run-button').isEnabled(), true);
  await page.unroute('**/jobs/*/run');
  let originalCommand;
  // Lose the request before delivery, then reload: the immutable request must survive.
  await page.route('**/jobs/*/run', async route => {
    originalCommand = route.request().postDataJSON();
    await route.abort('failed');
  });
  await page.getByTestId('verify-run-button').click();
  await page.getByTestId('verify-recover').waitFor();
  await page.unroute('**/jobs/*/run');
  await page.reload();
  await page.getByTestId('verify-recover').waitFor();
  // Commit the recovered original request but lose its response.
  await page.route('**/jobs/*/run', async route => {
    assert.deepEqual(route.request().postDataJSON(), originalCommand);
    await route.fetch(); await route.abort('failed');
  });
  await page.getByTestId('verify-recover').click();
  await page.getByTestId('verify-status').getByText(/执行状态：finished/).waitFor({ timeout: 30000 });
  await page.unroute('**/jobs/*/run');
  const task = await (await context.request.get(origin + '/api/tasks/' + taskId)).json();
  assert.equal(task.history.filter(e => e.kind === 'job.execution_started').length, 1);
  await page.reload();
  await page.getByTestId('verify-status').getByText(/执行状态：finished/).waitFor({ timeout: 30000 });
  assert.equal(await page.getByTestId('verify-run-button').count(), 0);
  const jobs = await (await context.request.get(`${origin}/api/tasks/${taskId}/jobs`)).json();
  const actual = await (await context.request.get(`${origin}/api/tasks/${taskId}/jobs/${jobs.at(-1).job_id}/execution`)).json();
  // A terminal failure must explain the cause and point to the saved diagnostics,
  // while private traceback contents remain outside the browser projection.
  const failed = { ...actual, state: 'failed', terminal: true, exit_code: 1, run_state: {
    phase: 'failed', stage: 'scoring', failed_phase: 'official_scoring', error_type: 'RuntimeError',
    error_message: 'Official scorer subprocess exited with code 7',
    diagnostic_path: '/private/execution-fixture/official-score.log', traceback: 'PRIVATE_TRACEBACK_MUST_NOT_RENDER',
  } };
  await page.route('**/jobs/*/execution', route => route.fulfill({ json: failed }));
  await page.reload();
  await page.getByTestId('verify-status').getByText(/执行状态：failed/).waitFor();
  assert.match(await page.getByTestId('verify-status').innerText(), /Official scorer subprocess exited with code 7/);
  assert.match(await page.getByTestId('verify-status').innerText(), /official_scoring/);
  assert.match(await page.getByTestId('verify-status').innerText(), /RuntimeError/);
  assert.match(await page.getByTestId('verify-status').innerText(), /\/private\/execution-fixture\/official-score.log/);
  assert.doesNotMatch(await page.locator('body').innerText(), /PRIVATE_TRACEBACK_MUST_NOT_RENDER/);
  await page.unroute('**/jobs/*/execution');

  // Saved pre-change commands may contain a cost cap. Recovery replays all fields
  // unchanged; the new form must not reinterpret that already pending command.
  await page.locator('[data-testid^="execution-plan-"]').first().click();
  await page.getByTestId('verify-prepare').click();
  await page.getByTestId('verify-prepared').waitFor();
  await page.route('**/jobs/*/run', route => route.abort('failed'));
  await page.getByTestId('verify-run-button').click();
  await page.getByTestId('verify-recover').waitFor();
  await page.unroute('**/jobs/*/run');
  const legacy = await page.evaluate(id => {
    const key = `workbench-pending-run:${id}`;
    const value = JSON.parse(sessionStorage.getItem(key));
    value.command.budget = { confirmed: true, limit_cny: 2, timeout_seconds: 45 };
    sessionStorage.setItem(key, JSON.stringify(value));
    return value;
  }, taskId);
  await page.reload();
  await page.getByTestId('verify-recover').waitFor();
  const [replayed] = await Promise.all([
    page.waitForRequest(request => request.url().endsWith(`/jobs/${legacy.jobId}/run`) && request.method() === 'POST'),
    page.getByTestId('verify-recover').click(),
  ]);
  assert.deepEqual(replayed.postDataJSON(), legacy.command);
  await page.getByTestId('verify-recover').waitFor({ state: 'hidden' });
  await page.getByTestId('verify-status').getByText(/执行状态：finished/).waitFor({ timeout: 30000 });
  const afterLegacy = await (await context.request.get(origin + '/api/tasks/' + taskId)).json();
  assert.equal(afterLegacy.history.filter(e => e.kind === 'job.execution_started').length, 2);

  // A still-running execution retains its cancellation command.
  let cancellationState = { ...actual, state: 'running', terminal: false, exit_code: null, run_state: null };
  await page.route('**/jobs/*/execution', async route => {
    const response = await route.fetch();
    const record = await response.json();
    await route.fulfill({ json: record?.execution_id === actual.execution_id ? cancellationState : null });
  });
  await page.route(`**/executions/${actual.execution_id}`, route => route.fulfill({ json: cancellationState }));
  await page.route(`**/executions/${actual.execution_id}/cancel`, route => {
    const command = route.request().postDataJSON();
    assert.deepEqual(Object.keys(command), ['request_key']);
    assert.equal(typeof command.request_key, 'string');
    cancellationState = { ...cancellationState, state: 'cancelled', terminal: true };
    return route.fulfill({ json: { record: cancellationState } });
  });
  await page.reload();
  await page.getByTestId('verify-cancel').click();
  await page.getByTestId('verify-status').getByText(/执行状态：cancelled/).waitFor();

  // A single registration is preselected, including its displayed label/path.
  await page.route('**/api/bootstrap', async route => {
    const response = await route.fetch();
    const bootstrap = await response.json();
    await route.fulfill({ json: { ...bootstrap, execution_worktrees: bootstrap.execution_worktrees.slice(1) } });
  });
  await page.goto(origin + '/#task=' + otherTaskId + '&view=changes');
  await page.reload();
  await binding.waitFor();
  assert.equal(await choices.count(), 1);
  assert.equal(await binding.isEnabled(), true);
  const [singleBound] = await Promise.all([
    page.waitForRequest(request => request.url().endsWith(`/tasks/${otherTaskId}/worktree`) && request.method() === 'POST'),
    binding.click(),
  ]);
  assert.deepEqual(singleBound.postDataJSON(), { worktree_id: 'second' });
  await page.getByTestId('execution-panel').waitFor();
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ selectedSecondWorktree: true, selectedPlan: true, preparation: true, timeoutOnly: true, timeoutGuard: true,
    definiteRejectionRecoverable: true, unknownRequestSurvivesReload: true, lostResponseRecovered: true, totalSmokeExecutions: 2, finished: true, reloadRecovered: true,
    failureReasonAndLogPath: true, noRawTraceback: true, legacyRequestUnchanged: true,
    cancellation: true, singleWorktreePreselected: true, selectionResetsAcrossTasks: true }));
} finally { await browser.close(); }
