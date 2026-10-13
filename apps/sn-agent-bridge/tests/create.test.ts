import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { createBridge } from "../dist/bridge.js";
import { executeRequest, encodeResponse, MAX_OUTPUT_BYTES } from "../dist/cli-protocol.js";
import { readFixture } from "./read-fixture.ts";

const id = "b7abb649-7b0d-4ead-9368-b2a4c7c58209";
const config = { url: "ws://127.0.0.1:1/ws", password: "private-sentinel" };
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}
async function fixture(t: test.TestContext) {
  const root = await fs.mkdtemp(fileURLToPath(new URL("../.create-test-", import.meta.url)));
  const cwd = `${root}/owned `;
  await fs.mkdir(cwd);
  await fs.symlink(cwd, `${root}/alias`);
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  const f = readFixture();
  const snapshot = { ...f.data.agent!, id, cwd, workspaceId: "workspace-example", activeTurn: null };
  const options = { workspaceId: snapshot.workspaceId, expectedCwd: cwd, agentId: id,
    idempotencyKey: "create-example-1", config: { provider: "example/model-example", modeId: "mode-example", thinkingOptionId: "effort-example" }, title: "Example" };
  const calls: unknown[] = [];
  let exists = false;
  let refreshCalls = 0;
  const workspace = { id: options.workspaceId, workspaceDirectory: cwd, archivingAt: null, status: "done" };
  const wh = { refresh: async () => workspace,
    agents: { create: async (params: unknown) => { calls.push(params); return { id, refresh: async () => ({ agent: snapshot, project: null }) }; } } };
  Object.assign(f.client.workspaces, { ref: () => wh });
  f.handle.refresh = async () => { refreshCalls++; return exists ? { agent: snapshot, project: null } : null; };
  const bridge = createBridge(config, () => f.client);
  t.after(() => bridge.close());
  await bridge.connect();
  return { ...f, root, cwd, options, snapshot, workspace, wh, calls, bridge,
    exists: () => { exists = true; }, refreshCalls: () => refreshCalls };
}

test("create uses public workspace handle once, exact ID/key/config, no prompt; canonical trailing-space cwd", async (t) => {
  const f = await fixture(t);
  const result = await f.bridge.createAgent({ ...f.options, expectedCwd: `${f.root}/alias` });
  assert.equal(result.status, "created");
  if (result.status !== "created") assert.fail();
  assert.equal(result.agentId, id);
  assert.equal(result.ready, true);
  assert.equal(result.readiness, "idle");
  assert.equal(result.agent.cwd, f.cwd);
  assert.deepEqual(f.calls, [{ agentId: id, idempotencyKey: f.options.idempotencyKey, config: f.options.config, title: "Example" }]);
  assert.equal(f.refreshCalls(), 1);
  assert.equal(JSON.stringify(result).includes("private-sentinel"), false);
});

test("prechecks reject mismatched/missing/archiving workspace and existing ID with zero creates", async (t) => {
  for (const kind of ["scope", "missing", "identity", "archiving", "status", "exists", "existingAlias", "readError"]) {
    const f = await fixture(t);
    if (kind === "scope") f.workspace.workspaceDirectory = f.root;
    if (kind === "missing") f.wh.refresh = async () => null as never;
    if (kind === "identity") f.workspace.id = "another-workspace";
    if (kind === "archiving") Object.assign(f.workspace, { archivingAt: "2026-10-11" });
    if (kind === "status") f.workspace.status = "unknown";
    if (kind === "exists") f.exists();
    if (kind === "existingAlias") f.handle.refresh = async () => ({ agent: { ...f.snapshot, id: "another" }, project: null });
    if (kind === "readError") f.handle.refresh = async () => { throw Error("private-sentinel"); };
    const result = await f.bridge.createAgent(f.options);
    assert.equal(result.status, "not_attempted", kind);
    assert.equal(f.calls.length, 0, kind);
    const codes = { scope: "SCOPE_MISMATCH", missing: "WORKSPACE_NOT_FOUND", identity: "IDENTITY_MISMATCH",
      archiving: "WORKSPACE_UNAVAILABLE", status: "WORKSPACE_UNAVAILABLE", exists: "AGENT_ALREADY_EXISTS", existingAlias: "IDENTITY_MISMATCH", readError: "READ_FAILED" };
    assert.equal(result.code, codes[kind as keyof typeof codes]);
    assert.equal(JSON.stringify(result).includes("private-sentinel"), false);
  }
});

test("strict closed create fields and UUID/key/path bounds fail before any SDK precheck", async (t) => {
  const f = await fixture(t);
  f.wh.refresh = async () => { assert.fail("invalid input must not read workspace"); };
  for (const changed of [
    { prompt: "private-sentinel" }, { env: {} }, { parent: id }, { git: {} }, { worktree: {} }, { images: [] }, { attachments: [] },
    { config: { provider: "example/model-example", options: {} } }, { config: { provider: "example/model-example", mcpServers: {} } },
    { config: { provider: "example" } }, { config: { provider: "example/" } },
    { agentId: "b7abb649" }, { idempotencyKey: "x".repeat(513) }, { title: "x".repeat(201) },
    { expectedCwd: "relative" }, { expectedCwd: "" }, { expectedCwd: `${f.cwd}\0` },
  ]) {
    const result = await f.bridge.createAgent({ ...f.options, ...changed } as never);
    assert.deepEqual(result, { status: "not_attempted", code: "INVALID_INPUT" });
  }
  assert.equal(f.calls.length, 0);
});

test("close/disconnect during workspace or final existence precheck never starts create", async (t) => {
  for (const stage of ["workspace", "exists"]) for (const end of ["close", "disconnect"]) {
    const f = await fixture(t);
    const gate = deferred<any>();
    const entered = deferred<void>();
    if (stage === "workspace") f.wh.refresh = () => { entered.resolve(); return gate.promise; };
    else f.handle.refresh = () => { entered.resolve(); return gate.promise; };
    const pending = f.bridge.createAgent(f.options);
    await entered.promise;
    if (end === "close") await f.bridge.close();
    else f.client.getConnectionState = () => ({ status: "disconnected" });
    gate.resolve(stage === "workspace" ? f.workspace : null);
    const result = await pending;
    assert.equal(result.status, "not_attempted");
    assert.equal(result.code, end === "close" ? "CLOSED" : "CONNECTION_UNAVAILABLE");
    assert.equal(f.calls.length, 0);
  }
});

test("close microtask after final precheck race resolves but before create resumes still means zero creates", async (t) => {
  const f = await fixture(t);
  const gate = deferred<any>(); const entered = deferred<void>();
  f.handle.refresh = () => { entered.resolve(); return gate.promise; };
  const pending = f.bridge.createAgent(f.options);
  await entered.promise;
  gate.resolve(null);
  // race reaction -> untilClosed continuation -> create continuation. Close
  // between the last two while SDK release is still merely queued.
  const closed = deferred<Awaited<ReturnType<typeof f.bridge.close>>>();
  queueMicrotask(() => queueMicrotask(() => {
    const closing = f.bridge.close();
    assert.equal(f.released(), false);
    closing.then(closed.resolve);
  }));
  const result = await pending;
  await closed.promise;
  assert.equal(result.status, "not_attempted");
  assert.equal(result.code, "CLOSED");
  assert.equal(f.calls.length, 0);
});

test("failed release or replaced instance during existence precheck prevents creation on the old client", async (t) => {
  for (const failure of [true, false]) {
    const f = await fixture(t);
    const replacement = readFixture();
    const clients = [f.client, replacement.client];
    const bridge = createBridge(config, () => clients.shift()!);
    t.after(() => bridge.close());
    await bridge.connect();
    const gate = deferred<any>(); const entered = deferred<void>();
    f.handle.refresh = () => { entered.resolve(); return gate.promise; };
    const pending = bridge.createAgent(f.options);
    await entered.promise;
    f.client.getConnectionState = () => ({ status: "disconnected" });
    if (failure) f.client.close = async () => { throw Error("private-sentinel"); };
    const reconnect = await bridge.connect();
    assert.equal(reconnect.status, failure ? "unavailable" : "ok");
    // Even a misleading late connected state on the old instance cannot own it.
    f.client.getConnectionState = () => ({ status: "connected" });
    gate.resolve(null);
    const result = await pending;
    assert.equal(result.status, "not_attempted");
    assert.equal(result.code, failure ? "CLOSE_FAILED" : "CONNECTION_UNAVAILABLE");
    assert.equal(f.calls.length, 0);
  }
});

test("close during precheck path IO or invalid real directory never starts create", async (t) => {
  for (const kind of ["close", "missing", "actual", "file"]) {
    const f = await fixture(t);
    if (kind === "missing") f.options.expectedCwd = `${f.root}/missing`;
    if (kind === "actual") f.workspace.workspaceDirectory = `${f.root}/missing`;
    if (kind === "file") { await fs.writeFile(`${f.root}/file`, "offline"); f.options.expectedCwd = `${f.root}/file`; }
    const gate = deferred<string>(); const entered = deferred<void>();
    if (kind === "close") t.mock.method(fs, "realpath", () => { entered.resolve(); return gate.promise; });
    const pending = f.bridge.createAgent(f.options);
    if (kind === "close") { await entered.promise; await f.bridge.close(); gate.resolve(f.cwd); }
    const result = await pending;
    assert.equal(result.status, "not_attempted");
    assert.equal(result.code, kind === "close" ? "CLOSED" : kind === "actual" ? "READ_FAILED" : "INVALID_INPUT");
    assert.equal(f.calls.length, 0);
    t.mock.restoreAll();
  }
});

test("create invocation reject/synchronous throw/close all mean unknown, once, never auto-retry", async (t) => {
  for (const kind of ["reject", "throw", "close"]) {
    const f = await fixture(t);
    const entered = deferred<void>();
    f.wh.agents.create = ((params: unknown) => {
      f.calls.push(params); entered.resolve();
      if (kind === "throw") throw Error("private-sentinel");
      return kind === "close" ? new Promise(() => {}) : Promise.reject(Error("provider denied private-sentinel"));
    }) as typeof f.wh.agents.create;
    const pending = f.bridge.createAgent(f.options);
    await entered.promise;
    if (kind === "close") await f.bridge.close();
    const result = await pending;
    assert.equal(result.status, "unknown");
    assert.equal(result.code, "CREATE_OUTCOME_UNKNOWN");
    assert.equal(result.agentId, id);
    assert.equal(f.calls.length, 1);
    assert.equal(JSON.stringify(result).includes("private-sentinel"), false);
  }
});

test("handle mismatch or readback failure retain observed facts as unknown and do not create again", async (t) => {
  for (const kind of ["handle", "missing", "reject", "scope", "workspace", "provider", "model", "incompleteIdentity", "disconnect"]) {
    const f = await fixture(t);
    const other = "64124a44-9dce-480f-b8ac-587041c26f9b";
    f.wh.agents.create = async (params) => {
      f.calls.push(params);
      return { id: kind === "handle" ? other : id, refresh: async () => {
        if (kind === "reject") throw Error("private-sentinel");
        if (kind === "missing") return null as never;
        if (kind === "disconnect") f.client.getConnectionState = () => ({ status: "disconnected" });
        const agent = { ...f.snapshot };
        if (kind === "scope") agent.cwd = f.root;
        if (kind === "workspace") agent.workspaceId = "another";
        if (kind === "provider") agent.provider = "another";
        if (kind === "model") agent.model = "another";
        if (kind === "incompleteIdentity") delete (agent as Partial<typeof agent>).workspaceId;
        return { agent, project: null };
      } };
    };
    const result = await f.bridge.createAgent(f.options);
    assert.equal(result.status, "unknown", kind);
    if (result.status !== "unknown") assert.fail();
    assert.equal(result.agentId, id);
    assert.equal(result.observed?.handleId, kind === "handle" ? other : id);
    assert.equal(f.calls.length, 1);
    assert.equal(JSON.stringify(result).includes("private-sentinel"), false);
  }
});

test("server intent is copied before await so caller mutation cannot change create arguments", async (t) => {
  const f = await fixture(t);
  const gate = deferred<any>(); const entered = deferred<void>();
  f.wh.refresh = () => { entered.resolve(); return gate.promise; };
  const pending = f.bridge.createAgent(f.options);
  await entered.promise;
  f.options.agentId = "64124a44-9dce-480f-b8ac-587041c26f9b";
  f.options.config.provider = "another/model";
  f.options.idempotencyKey = "changed";
  gate.resolve(f.workspace);
  const result = await pending;
  assert.equal(result.status, "created");
  assert.equal(result.agentId, id);
  assert.deepEqual(f.calls, [{ agentId: id, idempotencyKey: "create-example-1",
    config: { provider: "example/model-example", modeId: "mode-example", thinkingOptionId: "effort-example" }, title: "Example" }]);
});

test("known creation is retained when nonidle/archived/incomplete; mode and effort never claimed verified", async (t) => {
  for (const kind of ["nonidle", "activeTurn", "archived", "incomplete"]) {
    const f = await fixture(t);
    if (kind === "nonidle") f.snapshot.status = "running";
    if (kind === "activeTurn") Object.assign(f.snapshot, { activeTurn: { turnId: "turn-example", startedAt: null } });
    if (kind === "archived") f.snapshot.archivedAt = "2026-10-11";
    if (kind === "incomplete") delete (f.snapshot as Partial<typeof f.snapshot>).activeTurn;
    const result = await f.bridge.createAgent(f.options);
    assert.equal(result.status, "created");
    if (result.status !== "created") assert.fail();
    assert.equal(result.ready, false);
    assert.equal(result.agent.id, id);
    assert.equal("modeId" in result.agent, false);
    assert.equal("thinkingOptionId" in result.agent, false);
  }
});

test("close during readback/realpath cannot publish ready and keeps handle observation", async (t) => {
  for (const stage of ["refresh", "realpath"]) {
    const f = await fixture(t);
    const gate = deferred<any>(); const entered = deferred<void>();
    if (stage === "refresh") f.wh.agents.create = async (params) => {
      f.calls.push(params); return { id, refresh: () => { entered.resolve(); return gate.promise; } };
    };
    else {
      const realpath = fs.realpath;
      let calls = 0;
      t.mock.method(fs, "realpath", async (path: any) => {
        if (++calls === 3) { entered.resolve(); return gate.promise; }
        return realpath(path);
      });
    }
    const pending = f.bridge.createAgent(f.options);
    await entered.promise;
    await f.bridge.close();
    gate.resolve(stage === "refresh" ? { agent: f.snapshot, project: null } : f.cwd);
    const result = await pending;
    assert.equal(result.status, "unknown");
    if (result.status !== "unknown") assert.fail();
    assert.equal(result.observed?.handleId, id);
    assert.equal(f.calls.length, 1);
    t.mock.restoreAll();
  }
});

test("CLI create returns independent created/release failure; input and connect failures are not_attempted", async (t) => {
  const f = await fixture(t);
  f.client.close = async () => { throw Error("private-sentinel"); };
  const result = await executeRequest({ config, command: { op: "createAgent", ...f.options } },
    (conf) => createBridge(conf, () => f.client));
  assert.equal(result.result.status, "created");
  assert.deepEqual(result.release, { status: "unavailable", code: "CLOSE_FAILED" });
  assert.equal(f.calls.length, 1);
  const invalid = await executeRequest({ config, command: { op: "createAgent", ...f.options, prompt: "private-sentinel" } },
    () => { assert.fail("invalid input must not construct"); });
  assert.deepEqual(invalid, { result: { status: "not_attempted", code: "INVALID_INPUT" }, release: { status: "not_needed" } });
  const bad = await fixture(t);
  bad.client.connect = async () => { throw Error("private-sentinel"); };
  const failed = await executeRequest({ config, command: { op: "createAgent", ...bad.options } },
    (conf) => createBridge(conf, () => bad.client));
  assert.deepEqual(failed.result, { status: "not_attempted", code: "CONNECT_FAILED", agentId: id });
  assert.deepEqual(failed.release, { status: "ok" });
});

test("CLI encoding fallback preserves started creation facts, never converts to not_attempted/unavailable", () => {
  for (const failure of ["size", "cycle"]) {
    const agent: any = { title: failure === "size" ? "x".repeat(MAX_OUTPUT_BYTES) : "Example" };
    if (failure === "cycle") agent.loop = agent;
    const response = { result: { status: "created", agentId: id, agent, ready: true, readiness: "idle" }, release: { status: "unavailable", code: "CLOSE_FAILED" } };
    const output = JSON.parse(encodeResponse(response as never));
    assert.equal(output.result.status, "unknown");
    assert.equal(output.result.code, "CREATE_OUTCOME_UNKNOWN");
    assert.equal(output.result.agentId, id);
    assert.equal(output.result.observed?.handleId, id);
    assert.deepEqual(output.release, response.release);
  }
});

test("single-create connection timer is cleared on success/reject/close/disconnect and consumes a late rejection", async (t) => {
  for (const outcome of ["success", "reject", "close", "disconnect"]) {
    const f = await fixture(t);
    const intervals = new Map<object, () => void>();
    let starts = 0; let clears = 0;
    t.mock.method(globalThis, "setInterval", (callback: () => void, delay: number) => {
      assert.equal(delay, 100);
      const token = {}; starts++;
      intervals.set(token, callback);
      return token as NodeJS.Timeout;
    });
    t.mock.method(globalThis, "clearInterval", (token: object) => {
      assert.equal(intervals.delete(token), true, "owned interval must be cleared exactly once");
      clears++;
    });
    const gate = deferred<any>(); const entered = deferred<void>();
    f.wh.agents.create = (params) => { f.calls.push(params); entered.resolve(); return gate.promise; };
    const pending = f.bridge.createAgent(f.options);
    await entered.promise;
    assert.equal(intervals.size, 1, outcome);
    const tick = [...intervals.values()][0]!;
    let settled = false;
    void pending.then(() => { settled = true; });
    for (let i = 0; i < 20; i++) tick(); // still connected: no deadline or RPC
    await Promise.resolve();
    assert.equal(settled, false);
    assert.equal(f.refreshCalls(), 1);
    if (outcome === "success") gate.resolve({ id, refresh: async () => {
      assert.equal(intervals.size, 0, "create timer must not extend into readback");
      return { agent: f.snapshot, project: null };
    } });
    if (outcome === "reject") gate.reject(Error("private-sentinel"));
    if (outcome === "close") await f.bridge.close();
    if (outcome === "disconnect") { f.client.getConnectionState = () => ({ status: "disconnected" }); tick(); }
    const result = await pending;
    assert.equal(result.status, outcome === "success" ? "created" : "unknown");
    if (result.status === "unknown") assert.equal(result.cause, outcome === "close" ? "CLOSED" : outcome === "disconnect" ? "CONNECTION_UNAVAILABLE" : "CREATE_FAILED");
    assert.equal(intervals.size, 0, outcome);
    assert.equal(starts, 1); assert.equal(clears, 1);
    assert.equal(f.calls.length, 1);
    // Losing the race must leave a rejection handler on the SDK operation.
    gate.reject(Error("late private-sentinel"));
    await new Promise<void>((resolve) => setImmediate(resolve));
    t.mock.restoreAll();
  }
});

test("connection wait retains closed/releaseFailed priority over a timer disconnect", async (t) => {
  for (const outcome of ["closed", "releaseFailed"]) {
    const f = await fixture(t);
    let tick!: () => void;
    let cleared = false;
    t.mock.method(globalThis, "setInterval", (callback: () => void) => { tick = callback; return {} as NodeJS.Timeout; });
    t.mock.method(globalThis, "clearInterval", () => { cleared = true; });
    const entered = deferred<void>();
    f.wh.agents.create = (params) => { f.calls.push(params); entered.resolve(); return new Promise(() => {}); };
    const pending = f.bridge.createAgent(f.options);
    await entered.promise;
    f.client.getConnectionState = () => ({ status: "disconnected" });
    if (outcome === "closed") { const closing = f.bridge.close(); tick(); await closing; }
    else {
      f.client.close = async () => { throw Error("private-sentinel"); };
      assert.deepEqual(await f.bridge.connect(), { status: "unavailable", code: "CLOSE_FAILED" });
      tick();
    }
    const result = await pending;
    assert.equal(result.status, "unknown");
    if (result.status !== "unknown") assert.fail();
    assert.equal(result.cause, outcome === "closed" ? "CLOSED" : "CLOSE_FAILED");
    assert.equal(cleared, true);
    assert.equal(f.calls.length, 1);
    t.mock.restoreAll();
  }
});
