// Mount the actual controlled component and ui.tsx on real React Native Web.
// Bundling happens in memory, without rebuilding App or mocking React/controls.
import { chromium } from '@playwright/test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';

const require = createRequire(import.meta.url);
const modules = {};
const componentFile = fileURLToPath(new URL('../src/agents/TaskComposer.tsx', import.meta.url));
function bundle(file) {
  if (modules[file]) return file;
  const entry = { code: '', dependencies: {} };
  modules[file] = entry;
  const source = ts.transpileModule(readFileSync(file === componentFile && process.env.WORKBENCH_COMPOSER_SOURCE
    ? process.env.WORKBENCH_COMPOSER_SOURCE : file, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.React, esModuleInterop: true },
  }).outputText;
  const local = createRequire(file);
  for (const [, name] of source.matchAll(/\brequire\(["']([^"']+)["']\)/g)) {
    let resolved;
    if (name === 'react-native') resolved = require.resolve('react-native-web');
    else {
      try { resolved = local.resolve(name); }
      catch (error) {
        if (!name.startsWith('.')) throw error;
        const target = resolve(dirname(file), name);
        resolved = [target + '.ts', target + '.tsx'].find(candidate => existsSync(candidate));
        if (!resolved) throw error;
      }
    }
    entry.dependencies[name] = bundle(resolved);
  }
  entry.code = source;
  return file;
}
const component = bundle(componentFile);
const ui = bundle(fileURLToPath(new URL('../src/ui.tsx', import.meta.url)));
const react = bundle(require.resolve('react'));
const client = bundle(require.resolve('react-dom/client'));
const browser = await chromium.launch({ headless: true, executablePath: process.env.WORKBENCH_CHROMIUM });
const errors = [];
let checks = 0;
try {
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  page.on('pageerror', error => errors.push(String(error)));
  await page.route('https://composer-harness.invalid/**', route => route.fulfill({ body: '<div id="root"></div>', contentType: 'text/html' }));
  await page.goto('https://composer-harness.invalid/');
  await page.evaluate(({ modules, component, ui, react, client }) => {
    const cache = {};
    const load = id => {
      if (!cache[id]) {
        const module = { exports: {} }; cache[id] = module;
        new Function('require', 'module', 'exports', 'process', modules[id].code)(
          name => load(modules[id].dependencies[name]), module, module.exports,
          { env: { NODE_ENV: 'production' } });
      }
      return cache[id].exports;
    };
    const React = load(react), { createRoot } = load(client);
    const { TaskComposer } = load(component), { ThemeContext, themes } = load(ui);
    const root = createRoot(document.getElementById('root'));
    window.test = { sends: 0, changes: [], removed: 0, opened: 0, mode: [], serial: 0 };
    window.test.render = (patch = {}, dark = false, mode = 'message') => {
      window.test.serial++;
      const message = {
        value: '正文原值', references: [{ id: 'reference', label: '长引用 · '.repeat(12) }],
        onChange: value => window.test.changes.push(value), onSend: () => window.test.sends++,
        destinationLabel: '完整 Agent 目标', blockedReason: null, status: 'idle',
        behavior: 'steer', onBehaviorChange: value => window.test.changes.push(value),
        onRemoveReference: () => window.test.removed++, onOpenReference: () => window.test.opened++,
        ...patch,
      };
      const serial = window.test.serial;
      root.render(React.createElement(ThemeContext.Provider, { value: dark ? themes.dark : themes.light },
        React.createElement('div', { 'data-serial': serial }, React.createElement(TaskComposer, {
          compact: true, mode, onModeChange: value => window.test.mode.push(value), message,
          note: { value: '笔记', references: [], onChange: () => {}, onSave: () => {}, status: 'idle' },
        }))));
      return serial;
    };
    window.test.render();
  }, { modules, component, ui, react, client });
  const render = async (patch = {}, dark = false, mode = 'message') => {
    // Undefined properties must be representable across Playwright's boundary.
    const serial = await page.evaluate(({ patch, dark, mode }) => {
      if (patch.omitBehavior) { patch.behavior = undefined; delete patch.omitBehavior; }
      if (patch.omitHandler) { patch.onBehaviorChange = undefined; delete patch.omitHandler; }
      if (patch.omitSend) { patch.onSend = undefined; delete patch.omitSend; }
      return window.test.render(patch, dark, mode);
    }, { patch, dark, mode });
    await page.waitForFunction(serial => document.querySelector('[data-serial]')?.getAttribute('data-serial') === String(serial), serial);
  };
  const send = page.getByTestId('send-message');
  for (const patch of [{ omitBehavior: true }, { behavior: null }, { behavior: 'other' },
    { omitHandler: true }, { destinationLabel: null }, { destinationLabel: ' ' },
    { value: ' ' }, { blockedReason: '' }, { omitSend: true }]) {
    await render(patch);
    assert.equal(await send.isEnabled(), false, JSON.stringify(patch)); checks++;
  }
  await render({ behavior: null });
  await page.getByTestId('message-behavior-interrupt').click();
  await page.getByTestId('message-behavior-steer').click();
  assert.deepEqual(await page.evaluate(() => window.test.changes), ['interrupt', 'steer']);
  assert.equal(await page.evaluate(() => window.test.sends), 0);
  assert.equal(await send.isEnabled(), false, 'callbacks do not mutate controlled selection'); checks++;
  for (const status of ['sending', 'unknown']) {
    await render({ status });
    assert.equal(await page.getByTestId('message-input').isEditable(), false);
    assert.equal(await page.getByTestId('message-behavior-interrupt').isEnabled(), false);
    assert.equal(await page.getByTestId('message-behavior-steer').isEnabled(), false);
    assert.equal(await page.getByRole('button', { name: /^移除引用/ }).isEnabled(), false);
    await page.getByRole('button', { name: /^打开引用/ }).click();
    await page.getByTestId('mode-note').click();
    assert.equal(await send.isEnabled(), false); checks++;
  }
  assert.equal(await page.evaluate(() => window.test.opened), 2);
  assert.deepEqual(await page.evaluate(() => window.test.mode), ['note', 'note']);
  for (const patch of [{ blockedReason: '目标当前不可用' }, { blockedReason: '' },
    { destinationLabel: null }, { destinationLabel: ' ' }, { omitHandler: true },
    { omitBehavior: true }, { omitSend: true }]) {
    await render({ ...patch, status: 'accepted' });
    await page.getByText('SDK已返回发送ACK，不代表执行完成', { exact: true }).waitFor();
    assert.equal(await send.isEnabled(), false); checks++;
  }
  for (const status of ['idle', 'error']) {
    await render({ status });
    assert.equal(await send.isEnabled(), true);
    await send.click(); checks++;
  }
  assert.equal(await page.evaluate(() => window.test.sends), 2);
  await render({ status: 'unknown' }, true);
  assert.match(await page.getByTestId('message-behavior-description').innerText(), /steer/);
  await page.waitForFunction(() => document.documentElement.scrollWidth <= innerWidth);
  await render({ status: 'unknown' }, true, 'note');
  assert.equal(await page.getByTestId('note-input').isEditable(), true);
  assert.equal(await page.getByTestId('save-note').isEnabled(), true);
  assert.equal(await page.getByTestId('message-behavior-interrupt').count(), 0); checks++;
  assert.deepEqual(errors, []);
  console.log('Controlled composer checks passed:', checks);
} finally { await browser.close(); }
