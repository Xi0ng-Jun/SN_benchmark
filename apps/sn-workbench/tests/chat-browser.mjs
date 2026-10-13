// Chat flow against tests/chat-browser-server.py (synthetic Agent, never a real provider):
// new task -> new Agent from the composer menu -> Enter sends -> reply appears
// without manual refresh -> attach a material -> second send carries it.
import { chromium } from '@playwright/test';
import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
const url = process.env.WORKBENCH_TEST_URL ?? 'http://127.0.0.1:18097/#access=a-chat-browser-test';
const shots = process.env.WORKBENCH_SHOTS ?? 'test-results/chat-20261014';
await mkdir(shots, { recursive: true });
const browser = await chromium.launch({ headless: true, executablePath: process.env.WORKBENCH_CHROMIUM });
const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
const failures = [];
page.on('pageerror', error => failures.push(String(error)));
const origin = new URL(url).origin;
const stats = async () => (await (await page.request.get(origin + '/api/chat-fixture/stats')).json());
// Hold the run catalog until the role brief has arrived. This reproduces the
// real loading order that used to consume the draft's only attachment attempt.
let releaseRuns;
const runsReady = new Promise(resolve => { releaseRuns = resolve; });
await page.route('**/api/run-views/runs', async route => {
  await runsReady;
  await route.continue();
});

await page.goto(url);
await page.getByTestId('new-task').waitFor();
await page.getByTestId('new-task').click();
await page.getByTestId('task-title').fill('聊天验收');
await page.getByTestId('task-goal').fill('确认能从工作页直接和 Agent 对话');
await page.getByTestId('create-task').click();
await page.getByTestId('chat-empty').waitFor();
// Notes mode and session management are gone from the work surface.
assert.equal(await page.getByTestId('mode-note').count(), 0);
assert.equal(await page.getByTestId('task-session-panel').count(), 0);
await page.screenshot({ path: `${shots}/01-empty.png` });

await page.getByTestId('chat-new-agent').click();
await page.getByTestId('agent-menu').waitFor();
await page.screenshot({ path: `${shots}/02-agent-menu.png` });
await page.getByTestId('agent-create-claude-opus').click();
await page.getByTestId('agent-chat').waitFor();
await page.getByText('还没有对话。在下方输入第一条消息。').waitFor();
// The first draft carries this Agent's role brief (visible, removable).
await page.getByRole('button', { name: /查看材料 分析 Agent 工作说明/ }).waitFor({ timeout: 15000 });
assert.match(await page.getByTestId('agent-picker-toggle').innerText(), /分析 · claude · claude-fixture/);
assert.match(await page.getByTestId('agent-picker-toggle').innerText(), /空闲/);

await page.getByTestId('message-input').fill('你好，先看一下失败账本');
assert.equal(await page.getByTestId('send-message').isEnabled(), true);
await page.getByTestId('message-input').press('Enter');
await page.getByTestId('chat-user').first().waitFor();
// The reply arrives through polling, without any refresh click.
await page.getByText(/收到：你好，先看一下失败账本（附带材料：分析 Agent 工作说明/).waitFor({ timeout: 15000 });
assert.match((await stats()).sends[0].body.context[0].text, /scripts\/sn-eval/);
assert.equal(await page.getByTestId('message-input').inputValue(), '', 'composer is free for the next message');
assert.match(await page.getByTestId('agent-chat').innerText(), /工具 · 读取 docs\/failure-ledger\.md/);
await page.screenshot({ path: `${shots}/03-first-reply.png` });

await page.getByTestId('message-context-toggle').click();
await page.getByTestId('context-menu').waitFor();
await page.getByRole('button', { name: '选择材料 任务目标' }).click();
await page.getByTestId('context-preview-text').waitFor();
assert.match(await page.getByTestId('context-preview-text').innerText(), /确认能从工作页直接和 Agent 对话/);
await page.screenshot({ path: `${shots}/04-context.png` });
await page.getByTestId('context-add').click();
await page.getByRole('button', { name: /查看材料 任务目标/ }).waitFor();
await page.getByTestId('message-input').fill('结合目标说说下一步');
await page.getByTestId('send-message').click();
await page.getByText(/收到：结合目标说说下一步（附带材料：任务目标/).waitFor({ timeout: 15000 });
assert.match(await page.getByTestId('agent-chat').innerText(), /附材料：任务目标/);
assert.equal(await page.getByRole('button', { name: /查看材料 任务目标/ }).count(), 0, 'attachments belong to the sent message');
await page.screenshot({ path: `${shots}/05-second-reply.png` });

const sent = await stats();
assert.equal(sent.sends.length, 2);
assert.deepEqual(sent.sends.map(s => s.behavior), ['interrupt', 'interrupt']);
assert.deepEqual(sent.sends[1].body.context.map(c => c.title.split(' · ')[0]), ['任务目标'], 'the brief is not sent twice');
// The user bubble shows typed text, not the JSON body.
assert.doesNotMatch(await page.getByTestId('agent-chat').innerText(), /"message"/);

// A second Agent can be created and switched to from the same menu.
await page.getByTestId('agent-picker-toggle').click();
await page.getByTestId('agent-create-codex-high').click();
await page.getByText('还没有对话。在下方输入第一条消息。').waitFor();
assert.match(await page.getByTestId('agent-picker-toggle').innerText(), /codex · gpt-fixture/);
await page.getByTestId('agent-picker-toggle').click();
await page.getByTestId('agent-menu').locator('[data-testid^="agent-option-"]').filter({ hasText: 'claude · claude-fixture' }).click();
await page.getByText(/收到：结合目标说说下一步/).waitFor();

// Records list outcomes in words, without prepared/started bookkeeping.
await page.getByTestId('view-records').click();
const records = await page.getByTestId('task-view-records').innerText();
assert.match(records, /发送消息 · 已送达/);
assert.doesNotMatch(records, /agent\.message\.prepared/);
await page.screenshot({ path: `${shots}/06-records.png` });

// A task with runs: each new draft carries the run summary until this Agent has it.
await page.getByText('有运行的任务').first().click();
await page.getByTestId('chat-new-agent').click();
await page.getByTestId('agent-create-codex-high').click();
const runChip = page.getByRole('button', { name: /查看材料 本任务的评测运行 · 1 个/ });
await page.getByRole('button', { name: /查看材料 分析 Agent 工作说明/ }).waitFor();
assert.equal(await runChip.count(), 0, 'run catalog is still loading');
releaseRuns();
await runChip.waitFor({ timeout: 15000 });
await page.getByRole('button', { name: /查看材料 分析 Agent 工作说明/ }).waitFor();
await page.getByTestId('message-input').fill('many-activities 这次实验的结果怎么样');
await page.getByTestId('message-input').press('Enter');
const reply = page.getByText(/收到：many-activities 这次实验的结果怎么样（附带材料：分析 Agent 工作说明.*本任务的评测运行/);
await reply.waitFor({ timeout: 20000 });
// 120 tool calls push the echoed user entry off the read page; the local
// bubble must still sit before the reply, not under it.
const asked = page.getByTestId('agent-chat').getByText('many-activities 这次实验的结果怎么样', { exact: true });
assert.ok((await asked.boundingBox()).y < (await reply.boundingBox()).y, 'user message is above the reply');
await page.screenshot({ path: `${shots}/07-run-summary.png` });
const last = (await stats()).sends.at(-1);
const summary = last.body.context.find(c => c.title.startsWith('本任务的评测运行'));
assert.equal(JSON.parse(summary.text).runs[0].id, 'e5');
await page.waitForTimeout(1500);
assert.equal(await runChip.count(), 0, 'an unchanged summary is not attached twice');

// The analyst's draft card waits for a person on the Changes page.
await page.getByTestId('view-changes').click();
const card = page.getByTestId('hypothesis-card');
await card.waitFor();
assert.match(await card.innerText(), /草稿·待你批准/);
assert.match(await card.innerText(), /守护 · 不变差/);
await page.screenshot({ path: `${shots}/08-hypothesis-draft.png` });
await page.getByTestId('hypothesis-approve').click();
await page.getByText('已批准·待实现').waitFor();
// The approved card creates a separate actual Git worktree through the fixture.
await page.getByTestId('hypothesis-worktree-create').click();
await page.getByText(/独立改动：sn-hyp-/).waitFor();
await page.getByTestId('hypothesis-worktree-activate').click();
await page.getByText('当前更改页已选中此改动').waitFor();
// Creating the implementer reuses that tree; its context excludes exam material.
await page.getByTestId('hypothesis-implementer-codex-high').click();
await page.getByRole('button', { name: /查看材料 实现 Agent 工作说明/ }).waitFor({ timeout: 15000 });
assert.equal(await runChip.count(), 0, 'implementer gets no run summary');
await page.getByTestId('message-input').fill('按假设卡实施最小改动');
await page.getByTestId('send-message').click();
await page.getByText(/收到：按假设卡实施最小改动/).waitFor({ timeout: 15000 });
const implementation = (await stats()).sends.at(-1);
assert.match(implementation.cwd, /sn-hyp-/);
assert.deepEqual(implementation.body.context.map(c => c.title.split(' · ')[0]), ['实现 Agent 工作说明']);
assert.doesNotMatch(JSON.stringify(implementation.body), /qasper:|397a1e85|context precision/);
// The earlier analyst still reads in its original directory after switching trees.
await page.getByTestId('agent-picker-toggle').click();
await page.getByTestId('agent-menu').locator('[data-testid^="agent-option-"]').filter({ hasText: '分析 · codex' }).click();
await reply.waitFor();
await page.getByTestId('view-changes').click();
assert.match(await card.innerText(), /实现中/);

assert.deepEqual(failures, []);
await browser.close();
console.log('chat browser checks passed');
