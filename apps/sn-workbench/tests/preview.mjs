// Read-only inspection of already attached real results, including Paseo proxy.
import { chromium } from '@playwright/test';
import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
assert(process.env.WORKBENCH_TEST_URL, 'Set WORKBENCH_TEST_URL to a preview access link');
await mkdir('test-results', { recursive: true });
const browser = await chromium.launch({ headless: true, executablePath: process.env.WORKBENCH_CHROMIUM });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const errors = [];
page.on('pageerror', error => errors.push(String(error)));
await page.goto(process.env.WORKBENCH_TEST_URL);
await page.getByTestId('result-card').waitFor();
const boot = await page.evaluate(async () => (await fetch('/api/bootstrap')).json());
const tasks = await page.evaluate(async ids => Promise.all(ids.map(async id => (await fetch('/api/tasks/' + id)).json())), boot.workspaces.flatMap(w => w.tasks.map(t => t.id)));
assert.equal(tasks.length, 3);
const qmsum = tasks.find(t => t.results[0].suite === 'qmsum');
const multihop = tasks.find(t => t.results[0].suite === 'multihop_rag');
const alce = tasks.find(t => t.results[0].suite === 'alce');
assert(qmsum && multihop && alce);
assert.equal(qmsum.results[0].metrics.rouge1.denominator, 1);
assert.equal(multihop.results[0].case_count, 2556);
assert(Object.values(multihop.results[0].metrics).some(m => m.denominator === 2255));
assert(alce.results[0].pending_metrics.length > 0);
assert(tasks.every(t => t.history.every(e => !['note.saved', 'review.saved'].includes(e.kind))));
// Invalid body reaches validation through proxy Origin handling; it writes nothing.
const status = await page.evaluate(async id => (await fetch('/api/tasks/' + id + '/notes', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Workbench': '1' }, body: '{}' })).status, qmsum.id);
assert.equal(status, 422);
async function open(task, panel = 'scores', caseId) {
  const query = new URLSearchParams({ task: task.id, result: task.results[0].id, panel });
  if (caseId) query.set('case', caseId);
  await page.goto(new URL('/#' + query.toString(), page.url()).toString());
  await page.reload();
  await page.getByTestId('result-card').waitFor();
}
await open(qmsum);
await page.getByText('完整声明范围的成绩', { exact: true }).waitFor();
await page.screenshot({ path: 'test-results/preview-qmsum.png' });
await open(multihop, 'cases');
await page.getByText('含有效零分', { exact: true }).click();
await page.getByTestId('case-row').first().click();
await page.getByTestId('case-answer').waitFor();
await page.screenshot({ path: 'test-results/preview-multihop.png' });
await page.getByText('读取完整资料', { exact: true }).first().click();
await page.getByText(/完整保存文本/).waitFor();
await page.getByText('收起完整资料', { exact: true }).first().click();
const condition = await page.evaluate(async id => {
  const result = await (await fetch('/api/results/' + id)).json();
  const conditional = result.cases.find(row => Object.keys(row.metrics).length === 1);
  return (await fetch('/api/results/' + id + '/case?case_id=' + encodeURIComponent(conditional.case_id))).json();
}, multihop.results[0].id);
assert(Object.values(condition.metric_states).includes('not-applicable'));
await open(alce);
await page.getByText('评分缺口', { exact: true }).waitFor();
await page.screenshot({ path: 'test-results/preview-alce.png' });
assert.deepEqual(errors, []);
console.log('Preview passed: proxy login/Origin, three real tasks, original denominators, zero-score case, full document, conditional applicability, ALCE pending; no user records written');
await browser.close();
