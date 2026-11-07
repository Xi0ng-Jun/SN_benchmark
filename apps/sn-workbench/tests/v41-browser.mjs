import assert from 'node:assert/strict';

// UI lifecycle tests; process starts are intercepted. Real confirmed controls and
// signals are exercised separately by test_workbench_auto_run_control.py.
export async function checkV41({ browser, open, origin, shot }) {
  for (const width of [1440, 420]) {
    const page = await open('v4-done', { width, scheme: width === 420 ? 'dark' : 'light' });
    await page.getByTestId('v41-tab-overview').waitFor();
    await page.getByTestId('v41-human-review').waitFor();
    await page.getByTestId('v41-candidate-chart').waitFor();
    await shot(page, `v41-overview-${width}`);
    await page.getByTestId('v41-tab-calibration').click();
    await page.getByTestId('v4-calibration').waitFor();
    assert.match(await page.getByTestId('v4-calibration').innerText(), /匿名题|灰色/);
    await shot(page, `v41-calibration-${width}`);
    await page.getByTestId('v41-tab-time').click();
    await page.getByTestId('v41-gantt').waitFor();
    assert.match(await page.getByTestId('auto-run').innerText(), /实际总费用：未汇总/);
    await page.getByTestId('v41-tab-config').click();
    await shot(page, `v41-config-${width}`);
    await page.close();
  }

  const page = await browser.newPage({ viewport: { width: 1100, height: 1000 } });
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.request.post(`${origin}/api/session`, { data: { token: 'test-access' } });
  const sample = await (await page.request.get(`${origin}/api/test-v41`)).json();
  const finished = await (await page.request.get(`${origin}/api/iterations/v4-done/auto-run`)).json();
  let calibrated = false, running = false, missingProposal = false, confirmations = 0, previews = 0, patches = 0;
  let epoch = 0, activeToken = null, calibrationInputs = null, recovering = false;
  await page.route('**/api/iterations/v41-wizard/auto-run', async route => {
    await route.fulfill({ json: running ? { ...finished, run: { ...finished.run, run_id: 'v41-wizard', iteration_id: 'v41-wizard', status: 'running', alive: true }, next_action: null }
      : { schema_version: 1, method: 'v4', run: { method: 'v4', run_id: 'v41-wizard', iteration_id: 'v41-wizard', status: 'not_started', alive: false },
          phase: null, next_action: null, progress: null, config: null, variants: {}, calibration: null, feasibility: null, lineage: [], rounds: [], ablation: null, time_account: null, endpoint: null, deviations: [] } });
  });
  await page.route('**/api/iterations/v41-wizard/v4/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname.split('/v4/')[1];
    const body = request.method() === 'GET' ? {} : request.postDataJSON();
    if (path === 'calibration') return route.fulfill({ json: calibrated ? sample.calibration : { complete: false, alive: false, phase: recovering ? 'calibration' : 'not_started', ...(recovering ? { launch_inputs: calibrationInputs, execution_timeout_seconds: 14400, measurement_active_wall_s: 6600 } : {}) } });
    if (path === 'proposal') {
      if (missingProposal) return route.fulfill({ status: 404, json: { error: '建议书工件不可用' } });
      if (request.method() === 'PATCH') {
        patches++; epoch++;
        sample.proposal.proposal.items[body.key].value = body.value;
        sample.proposal.proposal.items[body.key].source = 'user';
      }
      return route.fulfill({ json: sample.proposal });
    }
    if (path === 'simulate') {
      if (body.params.b === 3) await new Promise(resolve => setTimeout(resolve, 650));
      const result = structuredClone(sample.proposal.forecast);
      result.sim_id = 'preview-' + JSON.stringify(body.params);
      result.params = { ...result.params, ...body.params };
      result.resolution.n_val = body.params.validation_size ?? result.resolution.n_val;
      if (body.params.b) result.bottleneck = 'draft-b-' + body.params.b;
      return route.fulfill({ json: result });
    }
    if (['calibrate', 'run/preview', 'run', 'run/pause', 'run/resume', 'run/stop'].includes(path)) {
      if (!body.confirmation_token) {
        if (path === 'calibrate' && !calibrated) {
          assert.equal(body.inputs.calibration_concurrency, 3);
          if (recovering) { assert.deepEqual(body.inputs, calibrationInputs); assert.equal(body.recovery_execution_timeout_seconds, 21600); }
          else calibrationInputs = body.inputs;
        }
        previews++;
        activeToken = `token-${epoch}-${previews}`;
        return route.fulfill({ json: { action: path === 'run/preview' ? 'run' : path.split('/').at(-1), confirmation_token: activeToken,
          incurs_cost: !['run/pause', 'run/stop'].includes(path), expires_in_seconds: 600, budget_hours: 8,
          agent_profiles: { analyst_profile: 'server-model', implementer_profile: 'server-model', reviewer_profile: 'server-model', calibrator_profile: 'server-model' },
          execution_timeout_seconds: body.recovery_execution_timeout_seconds ?? calibrationInputs?.adapter?.execution_timeout_seconds ?? 7200,
          recovery_execution_timeout_seconds: body.recovery_execution_timeout_seconds,
          test_set_authorized: false, config: { b: sample.proposal.proposal.items.b.value, calibration_concurrency: calibrationInputs?.calibration_concurrency ?? 2 }, config_sha256: 'synthetic', search_size: 100, validation_size: 60 } });
      }
      assert.equal(body.confirmation_token, activeToken);
      confirmations++; activeToken = null;
      if (path === 'calibrate') {
        if (!calibrated) assert.deepEqual(body.inputs, calibrationInputs);
        calibrated = true; missingProposal = false;
      }
      if (path === 'run' || path === 'run/resume') running = true;
      return route.fulfill({ json: { accepted: true } });
    }
    throw new Error('Unexpected control route ' + path);
  });
  await page.goto(`${origin}/#it=v41-wizard`);
  await page.getByTestId('v41-wizard').waitFor();
  await page.getByTestId('v41-k-3').click();
  await page.getByTestId('v41-hours').fill('8');
  assert.equal(await page.getByTestId('v41-calibration-concurrency').inputValue(), '2');
  await page.getByTestId('v41-calibration-concurrency').fill('17');
  await page.getByTestId('v41-calibrate').click();
  await page.getByText('校准执行并发需要 1–16 的整数', { exact: true }).waitFor();
  assert.equal(previews, 0);
  await page.getByTestId('v41-calibration-concurrency').fill('3');
  await page.getByTestId('v41-execution-timeout').fill('14400');
  await page.getByTestId('v41-calibrate').click();
  await page.getByTestId('v41-confirmation').waitFor();
  assert.match(await page.getByTestId('v41-confirmation').innerText(), /校准执行并发上限：3/);
  assert.match(await page.getByTestId('v41-confirm-profiles').innerText(), /校准顾问模型配置：server-model/);
  assert.match(await page.getByTestId('v41-confirm-timeout').innerText(), /14400 秒/);
  assert.equal(calibrationInputs.adapter.execution_timeout_seconds, 14400);
  assert.equal(confirmations, 0, 'preview must never start paid calibration');
  // Simulate an interrupted server run; recovery must retain the original inputs.
  recovering = true;
  await page.reload();
  await page.getByTestId('v41-recovery-timeout').fill('21600');
  await page.getByTestId('v41-calibrate').click();
  await page.getByTestId('v41-confirmation').waitFor();
  assert.match(await page.getByTestId('v41-confirm-timeout').innerText(), /21600 秒.*恢复时限/);
  await shot(page, 'v41-confirm-recovery');
  await page.getByTestId('v41-confirm').click();
  await page.getByTestId('v41-param-b').waitFor();
  assert.equal(confirmations, 1);
  // Several dirty fields block launch, including invalid / non-simulator values.
  await page.getByTestId('v41-edit-b').fill('3');
  await page.waitForTimeout(500);
  await page.getByTestId('v41-edit-b').fill('4');
  await page.waitForFunction(() => document.querySelector('[data-testid="v41-forecast"]')?.textContent.includes('draft-b-4'));
  await page.waitForTimeout(800);
  assert.match(await page.getByTestId('v41-forecast').innerText(), /draft-b-4/);
  await page.getByTestId('v41-edit-adapter.infra_retry').fill('invalid');
  assert.equal(await page.getByTestId('v41-run-preview').getAttribute('aria-disabled'), 'true');
  await page.getByTestId('v41-save-b').click();
  await page.waitForFunction(() => document.querySelector('[data-testid="v41-param-b"]')?.textContent.includes('来源 用户'));
  assert.equal(await page.getByTestId('v41-run-preview').getAttribute('aria-disabled'), 'true');
  await page.getByTestId('v41-edit-adapter.infra_retry').fill(JSON.stringify(sample.proposal.proposal.items['adapter.infra_retry'].value));
  await page.getByTestId('v41-run-preview').click();
  assert.equal(confirmations, 1, 'run preview must not launch a process');
  await shot(page, 'v41-confirm-run');
  await page.getByTestId('v41-confirm').click();
  await page.getByTestId('v41-pause').waitFor();
  assert.equal(confirmations, 2);
  assert.equal(patches, 1);
  await page.getByTestId('v41-pause').click();
  assert.equal(confirmations, 2);
  assert.match(await page.getByTestId('v41-confirmation').innerText(), /保留远端/);
  await page.getByTestId('v41-confirm').click();
  assert.equal(confirmations, 3);
  // Persisted crash window: frozen configuration exists but no runtime state yet.
  running = false;
  sample.proposal.frozen = true;
  await page.reload();
  await page.getByTestId('v41-frozen-resume').click();
  assert.equal(confirmations, 3);
  await page.getByTestId('v41-confirm').click();
  await page.getByTestId('v41-pause').waitFor();
  assert.equal(confirmations, 4);
  // Persisted crash window: calibration complete, proposal not yet written.
  running = false;
  sample.proposal.frozen = false;
  missingProposal = true;
  await page.reload();
  await page.getByTestId('v41-recover-proposal').click();
  assert.equal(confirmations, 4);
  await page.getByTestId('v41-confirm').click();
  await page.getByTestId('v41-param-b').waitFor();
  assert.equal(confirmations, 5);
  // The creation dialog registers a deferred pool before showing the wizard.
  const bootstrap = await (await page.request.get(`${origin}/api/bootstrap`)).json();
  bootstrap.inputs = [{ id: 'browser-pool', title: '离线题池', description: 'synthetic' }];
  await page.route('**/api/bootstrap', route => route.fulfill({ json: bootstrap }));
  await page.route('**/api/project/commits', route => route.fulfill({ json: { commits: [{ commit: 'a'.repeat(40), subject: 'synthetic start', date: '2026-11-06T00:00:00Z', refs: [], head: true }], seeds: [] } }));
  await page.route('**/api/inputs/browser-pool', route => route.fulfill({ json: { input_id: 'browser-pool', bundle_id: 'synthetic-bundle', case_ids: ['q1', 'q2', 'q3'], partitions: [{ partition_id: 'p1', case_ids: ['q1', 'q2'] }, { partition_id: 'p2', case_ids: ['q3'] }] } }));
  let createdBody;
  await page.route('**/api/iterations', async route => {
    if (route.request().method() !== 'POST') return route.continue();
    createdBody = route.request().postDataJSON();
    return route.fulfill({ json: { id: 'browser-created', title: createdBody.title, candidates: [] } });
  });
  await page.route('**/api/iterations/browser-created/v4/calibration', route => route.fulfill({ json: { complete: false, alive: false, phase: 'not_started' } }));
  await page.reload();
  await page.getByTestId('new-iteration').click();
  const dialog = page.getByTestId('new-iteration-dialog');
  await dialog.getByTestId('iteration-auto').click();
  await dialog.getByTestId('iteration-title').fill('离线自动优化');
  await dialog.getByTestId('iteration-input-browser-pool').click();
  await dialog.getByTestId('picker-partition-p1').click();
  await dialog.getByTestId('picker-partition-p2').click();
  await dialog.getByTestId('iteration-create').click();
  await dialog.getByTestId('v41-calibrate').waitFor();
  await dialog.getByText('时间预算（小时）', { exact: true }).waitFor();
  await dialog.getByText('回答率允许下降（0.02 = 2 个百分点）', { exact: true }).waitFor();
  assert.match(await dialog.innerText(), /3 题/);
  assert.equal(createdBody.split_mode, 'deferred');
  assert.deepEqual(createdBody.case_ids, ['q1', 'q2', 'q3']);
  assert.equal(createdBody.start_commit, 'a'.repeat(40));
  await shot(page, 'v41-new-wizard');
  assert.deepEqual(errors, []);
  await page.close();
}
