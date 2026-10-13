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
  if (await binding.count()) await binding.click();
  await page.getByTestId('execution-panel').waitFor({ timeout: 60000 });
  await page.locator('[data-testid^="execution-plan-"]').first().click();
  assert.equal(await page.getByTestId('verify-run-button').count(), 0, 'preparation must precede launch');
  await page.getByTestId('verify-prepare').click();
  await page.getByTestId('verify-prepared').waitFor({ timeout: 60000 });
  await page.getByTestId('verify-budget').fill('0');
  assert.equal(await page.getByTestId('verify-run-button').isEnabled(), false);
  await page.getByTestId('verify-budget').fill('1');
  await page.getByTestId('verify-timeout').fill('60');
  // A definite matching rejection must permit correction.
  await page.route('**/jobs/*/run', route => {
    const command = route.request().postDataJSON();
    return route.fulfill({ status: 422, contentType: 'application/json', body: JSON.stringify({
      error: 'test budget rejection', code: 'JOB_COMMAND_REJECTED', operation: 'run', request_key: command.request_key,
    }) });
  });
  await page.getByTestId('verify-run-button').click();
  await page.getByTestId('verify-error').getByText(/运行被拒绝/).waitFor();
  assert.equal(await page.getByTestId('verify-run-button').isEnabled(), true);
  await page.unroute('**/jobs/*/run');
  let originalKey;
  // Lose the request before delivery, then reload: the immutable request must survive.
  await page.route('**/jobs/*/run', async route => {
    originalKey = route.request().postDataJSON().request_key;
    await route.abort('failed');
  });
  await page.getByTestId('verify-run-button').click();
  await page.getByTestId('verify-recover').waitFor();
  await page.unroute('**/jobs/*/run');
  await page.reload();
  await page.getByTestId('verify-recover').waitFor();
  // Commit the recovered original request but lose its response.
  await page.route('**/jobs/*/run', async route => {
    assert.equal(route.request().postDataJSON().request_key, originalKey);
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
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ selectedPlan: true, preparation: true, budgetGuard: true,
    definiteRejectionRecoverable: true, unknownRequestSurvivesReload: true, lostResponseRecovered: true, executionCount: 1, finished: true, reloadRecovered: true }));
} finally { await browser.close(); }
