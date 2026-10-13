// Changes page over the real H1 binding. Read-only; refuses to screenshot an empty diff (exit 3).
import { chromium } from '@playwright/test';
import assert from 'node:assert/strict';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

const origin = process.env.WORKBENCH_H1_URL;
const token = process.env.WORKBENCH_TOKEN;
const out = process.env.WORKBENCH_H1_OUT ?? new URL('../test-results/r1-launch-20261013/h1/ui', import.meta.url).pathname;
const receipt = JSON.parse(readFileSync(new URL('../test-results/r1-launch-20261013/h1/task-binding.json', import.meta.url)));
mkdirSync(out, { recursive: true });
const browser = await chromium.launch({ headless: true, executablePath: process.env.WORKBENCH_CHROMIUM });
const errors = [], checks = [];
const check = (name, ok) => { assert.ok(ok, name); checks.push(name); };
let code = 0;
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 960 } });
  assert.equal((await context.request.post(`${origin}/api/session`, { data: { token } })).status(), 200);
  const page = await context.newPage();
  page.on('pageerror', error => errors.push(String(error)));
  page.on('response', response => { if (response.status() >= 400 && !response.url().endsWith('/favicon.ico')) errors.push(response.status() + ' ' + new URL(response.url()).pathname); });
  await page.goto(`${origin}/#task=${receipt.task.id}&view=changes`);
  await page.getByTestId('changes-page').waitFor({ timeout: 60000 });
  check('hypothesis card from trusted config', await page.getByTestId('changes-hypothesis').count() >= 1);
  check('bound via Task worktree service', /Task worktree 绑定/.test(await page.getByTestId('changes-page').innerText()));
  const files = await page.getByTestId('changes-file').count();
  if (!files) { code = 3; checks.push('diff empty: screenshot deferred'); }
  else {
    await page.getByTestId('changes-file').first().locator('[aria-expanded]').first().click();
    await page.screenshot({ path: join(out, 'changes-h1-real-diff.png'), fullPage: true });
    checks.push(`diff files ${files}`);
  }
  check('no page errors', errors.length === 0);
} finally {
  writeFileSync(join(out, 'changes-browser-checks.json'), JSON.stringify({ checks, errors }, null, 2));
  await browser.close();
}
console.log(JSON.stringify({ checks, errors }, null, 2));
process.exit(code);
