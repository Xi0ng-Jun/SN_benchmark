// Actual built App + run-views-browser-server.py (isolated state, real E5 run). Read-only: no saves, no models.
import { chromium } from '@playwright/test';
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

const origin = process.env.WORKBENCH_RV_URL ?? 'http://127.0.0.1:18737';
const out = process.env.WORKBENCH_RV_OUT ?? '/tmp/sn-workbench-h1-ui-20261013/e5-screens';
mkdirSync(out, { recursive: true });
const browser = await chromium.launch({ headless: true, executablePath: process.env.WORKBENCH_CHROMIUM });
const errors = [], checks = [];
const check = (name, ok) => { assert.ok(ok, name); checks.push(name); };
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 960 } });
  assert.equal((await context.request.post(`${origin}/api/session`, { data: { token: 'run-views-browser-fixture' } })).status(), 200);
  const boot = await (await context.request.get(`${origin}/api/bootstrap`)).json();
  const task = boot.workspaces.flatMap(w => w.tasks).find(t => t.title === '核查 QASPER 单题链路');
  // Same persisted preference a drag of the right-panel edge writes; wider for reading paper text.
  await context.addInitScript(() => localStorage.setItem('sn-workbench-shell-v1', JSON.stringify({ left: 280, right: 560, leftOpen: true })));
  const page = await context.newPage();
  page.on('pageerror', error => errors.push(String(error)));
  page.on('console', message => { if (message.type() === 'error' && !message.text().startsWith('Failed to load resource')) errors.push(message.text()); });
  page.on('response', response => { if (response.status() >= 400) errors.push(response.status() + ' ' + new URL(response.url()).pathname); });
  await page.goto(`${origin}/#task=${task.id}`);
  await page.getByTestId('run-card').waitFor();
  await page.getByTestId('run-official-answer_f1').waitFor();
  check('temporary needs-read is not a pending decision', await page.getByTestId('task-pending-' + task.id).count() === 0);
  check('run card official answer_f1', /0\.667/.test(await page.getByTestId('run-official-answer_f1').innerText()));
  check('run card stage summary', /1\/16/.test(await page.getByTestId('run-stage').innerText()));
  // The fixture worktree is shared with live H1 work: accept clean or a real file count, never assume.
  await page.waitForFunction(() => /无改动|\d+ 文件/.test(document.querySelector('[data-testid=diff-card]')?.textContent ?? ''));
  check('diff card reflects actual worktree', /无改动|\d+ 文件/.test(await page.getByTestId('diff-card').innerText()));
  // Backend rule (QASPER answer_f1 strict < 0.5): 0.667 is not to-read; reading entries are not decisions.
  check('backend needs-read rule shown', /0\.5/.test(await page.getByTestId('run-attention-rule').innerText()));
  check('no to-read row at 0.667', await page.getByTestId('read-row').count() === 0);
  check('no fake undecided decision', await page.getByTestId('decision-row').count() === 0);
  check('budget decision row', await page.getByTestId('decision-verify').count() === 1);
  await page.screenshot({ path: join(out, '01-default.png') });
  await page.getByTestId('run-card').screenshot({ path: join(out, '02-run-card.png') });

  // Default filter is needsRead=true; with attention=0 the case must not disappear.
  await page.getByTestId('run-provenance').click();
  await page.getByTestId('run-case-list').waitFor();
  await page.waitForFunction(() => document.querySelectorAll('[data-testid=run-case-row]').length > 0);
  check('attention=0 run still lists its case under default filter', await page.getByTestId('run-case-row').count() === 1);
  await page.getByTestId('close-provenance').click();

  // Run card -> case evidence: (1) all cases, (2) case row; highlight is auto-scrolled into view.
  let steps = 0;
  await page.getByTestId('run-all-cases').click(); steps++;
  await page.getByTestId('run-case-row').first().click(); steps++;
  await page.getByTestId('run-case-detail').waitFor();
  const gold = page.getByTestId('highlight-gold').first();
  await gold.waitFor();
  await page.waitForTimeout(900);
  const box = await gold.boundingBox(), view = page.viewportSize();
  check(`gold highlight visible after ${steps} steps`, steps <= 3 && box && box.y >= 0 && box.y < view.height);
  check('answer and gold shown', /220 human-human dialogs/.test(await page.getByTestId('run-case-answer').innerText())
    && /3,044 sentences/.test(await page.getByTestId('run-case-gold').innerText()));
  check('cited object listed', /k1/.test(await page.getByTestId('run-case-citations').innerText()));
  check('judgment entry points to failure ledger', /failure-ledger\.md/.test(await page.getByTestId('run-case-judgment').innerText()));
  await page.screenshot({ path: join(out, '03-case-evidence.png') });
  await page.getByTestId('run-case-detail').evaluate(el => el.scrollIntoView({ block: 'start' }));
  await page.screenshot({ path: join(out, '03b-case-header.png') });

  await page.getByTestId('panel-run-flips').click();
  await page.getByTestId('flip-self').waitFor();
  check('admitted self flip rows', await page.getByTestId('flip-row').count() === 1);
  await page.screenshot({ path: join(out, '04-flips.png') });

  await page.getByTestId('toggle-provenance').click();
  await page.getByTestId('provenance-drawer').waitFor();
  const drawer = await page.getByTestId('provenance-drawer').innerText();
  check('provenance fields', /0680aa12/.test(drawer) && /未投影/.test(drawer) && /qwen-turbo/.test(drawer) && /e5a59629/.test(drawer));
  await page.screenshot({ path: join(out, '05-provenance.png') });
  await page.getByTestId('close-provenance').click();

  await page.getByTestId('view-plans').click();
  await page.getByTestId('input-qasper-input').click();
  await page.getByTestId('case-picker').waitFor({ timeout: 60000 });
  await page.getByTestId('picker-sample-count').fill('3');
  await page.getByTestId('picker-sample').click();
  const summary = await page.getByTestId('case-picker-summary').innerText();
  check('picker seeded sample counts papers', /3 篇论文/.test(summary));
  const first = summary;
  await page.getByTestId('picker-clear').click();
  await page.getByTestId('picker-sample').click();
  check('same seed reproduces selection', (await page.getByTestId('case-picker-summary').innerText()) === first);
  await page.getByTestId('picker-search').fill('qasper-group-09a0');
  await page.waitForTimeout(300);
  await page.screenshot({ path: join(out, '06-case-picker.png') });

  await page.getByTestId('view-changes').click();
  await page.getByTestId('changes-page').waitFor();
  check('changes lists actual diff state', await page.getByTestId('changes-empty').count() + await page.getByTestId('changes-file').count() >= 1);
  check('changes run listed', await page.getByTestId('changes-run').count() === 1);
  check('verify command', /run_notebook_batch\.py run/.test(await page.getByTestId('verify-dry-run').innerText()));
  await page.screenshot({ path: join(out, '07-changes.png'), fullPage: true });

  await page.getByTestId('open-interpretation').click();
  await page.getByTestId('interpretation').waitFor();
  await page.screenshot({ path: join(out, '08-interpretation.png') });
  await page.getByRole('button', { name: '关闭', exact: true }).click();
  const unbound = boot.workspaces.flatMap(w => w.tasks).find(t => t.title === '未绑定 worktree 的任务');
  await page.getByTestId('task-nav-' + unbound.id).click();
  await page.getByTestId('task-timeline').waitFor();
  check('unbound task shows related-run entry only', await page.getByTestId('task-runs-empty').count() === 1);
  check('unbound task has no unrelated run cards', await page.getByTestId('task-timeline').getByTestId('run-card').count() === 0);
  await page.getByTestId('open-run-directory').click();
  await page.getByTestId('global-run-catalog').waitFor();
  check('global catalog retains registered runs', await page.getByTestId('global-run-catalog').getByTestId('run-card').count() === 1);
  await page.getByRole('button', { name: '工作区设置', exact: true }).first().click();
  await page.getByTestId('settings-theme-dark').click();
  await page.getByTestId('settings-close').click();
  await page.screenshot({ path: join(out, '09-global-dark.png') });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForFunction(() => document.documentElement.scrollWidth <= innerWidth);
  check('narrow viewport does not overflow', await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.screenshot({ path: join(out, '10-global-narrow.png') });
  check('no page errors', errors.length === 0);
} finally {
  writeFileSync(join(out, 'checks.json'), JSON.stringify({ origin, checks, errors }, null, 2));
  await browser.close();
}
console.log(JSON.stringify({ checks: checks.length, errors }, null, 2));
