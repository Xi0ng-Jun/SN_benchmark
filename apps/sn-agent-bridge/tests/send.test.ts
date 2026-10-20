import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { createBridge } from "../dist/bridge.js";
import { executeRequest, encodeResponse, MAX_OUTPUT_BYTES } from "../dist/cli-protocol.js";
import { readFixture } from "./read-fixture.ts";

const id = "b7abb649-7b0d-4ead-9368-b2a4c7c58209";
const messageId = "2ac576eb-a0ad-4f85-8bf7-53d12a67c080";
const config = { url: "ws://offline.test/ws", password: "private-sentinel" };
function deferred<T>() {
  let resolve!: (value: T) => void; let reject!: (error: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
async function fixture(t: test.TestContext) {
  const root = await fs.mkdtemp(fileURLToPath(new URL("../.send-test-", import.meta.url)));
  const cwd = `${root}/owned `;
  await fs.mkdir(cwd); await fs.symlink(cwd, `${root}/alias`);
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  const f = readFixture();
  const snapshot = { ...f.data.agent!, id, cwd, archivedAt: null, activeTurn: null, pendingPermissions: [] };
  // Existing Task references need not carry creation binding or remote workspace.
  delete snapshot.workspaceId;
  const options = { agentId: id, expectedCwd: cwd, messageId, text: "  Synthetic text\n", activeTurnBehavior: "steer" as "steer" | "interrupt" };
  const calls: unknown[] = [];
  let reads = 0;
  f.handle.refresh = async () => { reads++; return { agent: snapshot, project: null }; };
  f.handle.send = async (text, params) => { calls.push({ text, params }); };
  f.client.workspaces.ref = () => { assert.fail("send must not require remote workspace/creation binding"); };
  const bridge = createBridge(config, () => f.client);
  t.after(() => bridge.close()); await bridge.connect();
  return { ...f, root, cwd, snapshot, options, calls, bridge, reads: () => reads };
}
const acknowledged = { status: "acknowledged", agentId: id, messageId };

test("send preserves exact frozen text/UUID/explicit behavior once with canonical trailing-space cwd and no creation binding", async (t) => {
  for (const behavior of ["interrupt", "steer"] as const) {
    const f = await fixture(t);
    assert.deepEqual(await f.bridge.sendAgent({ ...f.options, expectedCwd: `${f.root}/alias`, activeTurnBehavior: behavior }), acknowledged);
    assert.deepEqual(f.calls, [{ text: "  Synthetic text\n", params: { messageId, activeTurnBehavior: behavior } }]);
    assert.equal(f.reads(), 1); assert.equal(f.queries.length, 0);
  }
});

test("an Agent whose last turn ended in a provider error can be sent to again", async (t) => {
  const f = await fixture(t);
  Object.assign(f.snapshot, { status: "error" });
  assert.deepEqual(await f.bridge.sendAgent(f.options), acknowledged);
  assert.equal(f.calls.length, 1);
});

test("closed send input schema rejects UUID aliases, missing behavior, extras, empty/NUL/oversize text before SDK reads", async (t) => {
  const f = await fixture(t);
  for (const changed of [
    { agentId: "b7abb649" }, { messageId: "2ac576eb" }, { agentId: "00000000-0000-0000-0000-000000000000" },
    { activeTurnBehavior: undefined }, { activeTurnBehavior: "reject" }, { workspaceId: "guessed" },
    { text: "" }, { text: " \n\t" }, { text: "bad\0text" }, { text: "x".repeat(16385) },
    { text: "界".repeat(11000) }, { expectedCwd: "relative" }, { expectedCwd: "" }, { expectedCwd: `${f.cwd}\0` },
    ...["options", "attachments", "images", "parent", "env", "config", "tools", "prompt", "mcp", "toolPolicy", "systemPrompt"].map((key) => ({ [key]: "private-sentinel" })),
  ]) assert.deepEqual(await f.bridge.sendAgent({ ...f.options, ...changed } as never), { status: "not_attempted", code: "INVALID_INPUT" });
  assert.equal(f.reads(), 0); assert.equal(f.calls.length, 0);
  // Exact bounds do not truncate; original UTF16 and UTF8 both fit.
  const text = "😀".repeat(8192);
  assert.deepEqual(await f.bridge.sendAgent({ ...f.options, text }), acknowledged);
  assert.equal((f.calls[0] as any).text, text);
});

test("send prechecks reject missing/alias/archived/incomplete/permissions/nonready snapshots with zero writes", async (t) => {
  const cases = [
    ["missing", "AGENT_NOT_FOUND"], ["alias", "IDENTITY_MISMATCH"], ["archived", "AGENT_ARCHIVED"],
    ["missingArchive", "INCOMPLETE_SNAPSHOT"], ["missingStatus", "INCOMPLETE_SNAPSHOT"], ["missingPermissions", "INCOMPLETE_SNAPSHOT"],
    ["badPermissions", "INCOMPLETE_SNAPSHOT"], ["missingTurn", "INCOMPLETE_SNAPSHOT"], ["badTurn", "INCOMPLETE_SNAPSHOT"],
    ["permissions", "PERMISSION_REQUIRED"], ["permissionAttention", "PERMISSION_REQUIRED"],
    ["initializing", "AGENT_UNAVAILABLE"], ["closed", "AGENT_UNAVAILABLE"],
    ["providerUnavailable", "AGENT_UNAVAILABLE"], ["readError", "READ_FAILED"],
  ] as const;
  for (const [kind, code] of cases) {
    const f = await fixture(t);
    if (kind === "missing") f.handle.refresh = async () => null;
    else if (kind === "readError") f.handle.refresh = async () => { throw Error("private-sentinel"); };
    else {
      if (kind === "alias") f.snapshot.id = "another";
      if (kind === "archived") Object.assign(f.snapshot, { archivedAt: "2026-10-11" });
      if (kind === "missingArchive") delete (f.snapshot as any).archivedAt;
      if (kind === "missingStatus") delete (f.snapshot as any).status;
      if (kind === "missingPermissions") delete (f.snapshot as any).pendingPermissions;
      if (kind === "badPermissions") Object.assign(f.snapshot, { pendingPermissions: null });
      if (kind === "missingTurn") delete (f.snapshot as any).activeTurn;
      if (kind === "badTurn") Object.assign(f.snapshot, { activeTurn: {} });
      if (kind === "permissions") Object.assign(f.snapshot, { pendingPermissions: [{ secret: "private-sentinel" }] });
      if (kind === "permissionAttention") Object.assign(f.snapshot, { attentionReason: "permission" });
      if (["initializing", "closed"].includes(kind)) Object.assign(f.snapshot, { status: kind });
      if (kind === "providerUnavailable") Object.assign(f.snapshot, { providerUnavailable: true });
    }
    const result = await f.bridge.sendAgent(f.options);
    assert.deepEqual(result, { status: "not_attempted", code, agentId: id, messageId }, kind);
    assert.equal(f.calls.length, 0); assert.equal(JSON.stringify(result).includes("private-sentinel"), false);
  }
});

test("running with existing turn can send only with explicit behavior; result does not invent completion/turn/receipt", async (t) => {
  const f = await fixture(t);
  Object.assign(f.snapshot, { status: "running", activeTurn: { turnId: "synthetic-turn", startedAt: null } });
  assert.deepEqual(await f.bridge.sendAgent(f.options), acknowledged);
  assert.equal(f.calls.length, 1);
});

test("send canonical scope mismatch, nonexistent or non-directory paths are sanitized before invocation", async (t) => {
  for (const kind of ["scope", "expectedMissing", "actualMissing", "file"] as const) {
    const f = await fixture(t);
    if (kind === "scope") f.snapshot.cwd = f.root;
    if (kind === "expectedMissing") f.options.expectedCwd = `${f.root}/private-sentinel`;
    if (kind === "actualMissing") f.snapshot.cwd = `${f.root}/private-sentinel`;
    if (kind === "file") { await fs.writeFile(`${f.root}/file`, "synthetic"); f.options.expectedCwd = `${f.root}/file`; }
    assert.deepEqual(await f.bridge.sendAgent(f.options), { status: "not_attempted", code: kind === "scope" ? "SCOPE_MISMATCH"
      : kind === "actualMissing" ? "READ_FAILED" : "INVALID_INPUT", agentId: id, messageId });
    assert.equal(f.calls.length, 0);
  }
});

test("server send intent is copied before refresh so caller mutation cannot change target/text/options/scope", async (t) => {
  const f = await fixture(t); const gate = deferred<any>(); const entered = deferred<void>();
  f.handle.refresh = () => { entered.resolve(); return gate.promise; };
  const pending = f.bridge.sendAgent(f.options); await entered.promise;
  Object.assign(f.options, { agentId: "64124a44-9dce-480f-b8ac-587041c26f9b", messageId: "64124a44-9dce-480f-b8ac-587041c26f9b",
    expectedCwd: f.root, text: "changed", activeTurnBehavior: "interrupt" });
  gate.resolve({ agent: f.snapshot, project: null });
  assert.deepEqual(await pending, acknowledged);
  assert.deepEqual(f.calls, [{ text: "  Synthetic text\n", params: { messageId, activeTurnBehavior: "steer" } }]);
});

test("close/disconnect during refresh or directory IO prevents send, including late snapshot", async (t) => {
  for (const stage of ["refresh", "path"] as const) for (const end of ["close", "disconnect"] as const) {
    const f = await fixture(t); const gate = deferred<any>(); const entered = deferred<void>();
    if (stage === "refresh") f.handle.refresh = () => { entered.resolve(); return gate.promise; };
    else {
      const realpath = fs.realpath; let calls = 0;
      t.mock.method(fs, "realpath", (path: any) => { if (++calls === 2) { entered.resolve(); return gate.promise; } return realpath(path); });
    }
    const pending = f.bridge.sendAgent(f.options); await entered.promise;
    if (end === "close") await f.bridge.close(); else f.client.getConnectionState = () => ({ status: "disconnected" });
    gate.resolve(stage === "refresh" ? { agent: f.snapshot, project: null } : f.cwd);
    assert.deepEqual(await pending, { status: "not_attempted", code: end === "close" ? "CLOSED" : "CONNECTION_UNAVAILABLE", agentId: id, messageId });
    assert.equal(f.calls.length, 0); t.mock.restoreAll();
  }
});

test("close microtask after final path precheck fulfilled but before send resumes still prevents invocation", async (t) => {
  const f = await fixture(t); const gate = deferred<any>(); const entered = deferred<void>();
  const stat = fs.stat; let stats = 0;
  t.mock.method(fs, "stat", (path: any) => { if (++stats === 2) { entered.resolve(); return gate.promise; } return stat(path); });
  const pending = f.bridge.sendAgent(f.options); await entered.promise;
  gate.resolve({ isDirectory: () => true });
  // canonicalDirectory -> race -> untilClosed -> send continuation.
  const closed = deferred<any>();
  queueMicrotask(() => queueMicrotask(() => queueMicrotask(() => {
    const closing = f.bridge.close(); assert.equal(f.released(), false); closing.then(closed.resolve);
  })));
  assert.deepEqual(await pending, { status: "not_attempted", code: "CLOSED", agentId: id, messageId });
  await closed.promise; assert.equal(f.calls.length, 0);
});

test("replaced or failed-release instance cannot send after stale connected precheck resumes", async (t) => {
  for (const failure of [true, false]) {
    const f = await fixture(t); const replacement = readFixture(); const clients = [f.client, replacement.client];
    const bridge = createBridge(config, () => clients.shift()!); t.after(() => bridge.close()); await bridge.connect();
    const gate = deferred<any>(); const entered = deferred<void>();
    f.handle.refresh = () => { entered.resolve(); return gate.promise; };
    const pending = bridge.sendAgent(f.options); await entered.promise;
    f.client.getConnectionState = () => ({ status: "disconnected" });
    if (failure) f.client.close = async () => { throw Error("private-sentinel"); };
    await bridge.connect(); f.client.getConnectionState = () => ({ status: "connected" });
    gate.resolve({ agent: f.snapshot, project: null });
    assert.deepEqual(await pending, { status: "not_attempted", code: failure ? "CLOSE_FAILED" : "CONNECTION_UNAVAILABLE", agentId: id, messageId });
    assert.equal(f.calls.length, 0);
  }
});

test("send invocation throw/reject/close/disconnect becomes unknown, never replays, late rejection handled", async (t) => {
  for (const kind of ["throw", "reject", "close", "disconnect"] as const) {
    const f = await fixture(t); const gate = deferred<void>(); const entered = deferred<void>();
    f.handle.send = (text, params) => { f.calls.push({ text, params }); entered.resolve();
      if (kind === "throw") throw Error("private-sentinel");
      if (kind === "reject") return Promise.reject(Error("provider denied private-sentinel"));
      return gate.promise;
    };
    const pending = f.bridge.sendAgent(f.options); await entered.promise;
    if (kind === "close") await f.bridge.close();
    if (kind === "disconnect") { f.client.getConnectionState = () => ({ status: "disconnected" }); gate.reject(Error("private-sentinel")); }
    const result = await pending;
    assert.deepEqual(result, { status: "unknown", code: "SEND_OUTCOME_UNKNOWN", cause: kind === "close" ? "CLOSED"
      : kind === "disconnect" ? "CONNECTION_UNAVAILABLE" : "SEND_FAILED", agentId: id, messageId });
    assert.equal(f.calls.length, 1);
    if (kind === "close" || kind === "disconnect") gate.reject(Error("late private-sentinel"));
    await new Promise<void>((resolve) => setImmediate(resolve));
    assert.equal(JSON.stringify(result).includes("private-sentinel"), false);
  }
});

test("SDK resolution followed by close microtask remains unknown with observed sdkResolved; no execution claim", async (t) => {
  const f = await fixture(t); const gate = deferred<void>(); const entered = deferred<void>();
  f.handle.send = (text, params) => { f.calls.push({ text, params }); entered.resolve(); return gate.promise; };
  const pending = f.bridge.sendAgent(f.options); await entered.promise;
  gate.resolve();
  const closing = f.bridge.close();
  const result = await pending; await closing;
  assert.deepEqual(result, { status: "unknown", code: "SEND_OUTCOME_UNKNOWN", cause: "CLOSED", agentId: id, messageId,
    observed: { sdkResolved: true } });
  assert.equal(f.calls.length, 1);
});

test("failure releasing an in-flight instance takes priority over SDK send rejection", async (t) => {
  const f = await fixture(t); const gate = deferred<void>(); const entered = deferred<void>();
  f.handle.send = (text, params) => { f.calls.push({ text, params }); entered.resolve(); return gate.promise; };
  const pending = f.bridge.sendAgent(f.options); await entered.promise;
  f.client.getConnectionState = () => ({ status: "disconnected" });
  f.client.close = async () => { throw Error("private-sentinel"); };
  assert.deepEqual(await f.bridge.connect(), { status: "unavailable", code: "CLOSE_FAILED" });
  gate.reject(Error("private-sentinel"));
  assert.deepEqual(await pending, { status: "unknown", code: "SEND_OUTCOME_UNKNOWN", cause: "CLOSE_FAILED", agentId: id, messageId });
  assert.equal(f.calls.length, 1);
});

test("CLI send acknowledgement and unknown retain actual independent release failure", async (t) => {
  for (const reject of [false, true]) {
    const f = await fixture(t);
    if (reject) f.handle.send = async () => { throw Error("private-sentinel"); };
    f.client.close = async () => { throw Error("private-sentinel"); };
    const output = await executeRequest({ config, command: { op: "sendAgent", ...f.options } }, (config) => createBridge(config, () => f.client));
    assert.deepEqual(output, { result: reject ? { status: "unknown", code: "SEND_OUTCOME_UNKNOWN", cause: "SEND_FAILED", agentId: id, messageId } : acknowledged,
      release: { status: "unavailable", code: "CLOSE_FAILED" } });
    assert.equal(JSON.stringify(output).includes("private-sentinel"), false);
  }
});

test("recognizable invalid send command never constructs SDK and retains not_attempted", async () => {
  assert.deepEqual(await executeRequest({ config, command: { op: "sendAgent", agentId: id, messageId, text: "private-sentinel", activeTurnBehavior: "steer", expectedCwd: "/", images: [] } },
    () => { assert.fail("invalid command must not construct SDK"); }), { result: { status: "not_attempted", code: "INVALID_INPUT" }, release: { status: "not_needed" } });
});

test("send encoding overflow/circular failure retains unknown planned identities, SDK observation and release", () => {
  for (const status of ["acknowledged", "unknown", "not_attempted"] as const) for (const circular of [false, true]) {
    const result: any = { status, agentId: id, messageId };
    if (status === "unknown") { result.code = "SEND_OUTCOME_UNKNOWN"; result.cause = "SEND_FAILED"; }
    if (status === "not_attempted") result.code = "INVALID_INPUT";
    result.extra = circular ? result : "x".repeat(MAX_OUTPUT_BYTES);
    const output = JSON.parse(encodeResponse({ result, release: { status: "unavailable", code: "CLOSE_FAILED" } }));
    assert.deepEqual(output, { result: status === "not_attempted" ? { status, code: circular ? "INTERNAL_ERROR" : "RESPONSE_TOO_LARGE", agentId: id, messageId }
      : { status: "unknown", code: "SEND_OUTCOME_UNKNOWN", cause: circular ? "INTERNAL_ERROR" : "RESPONSE_TOO_LARGE", agentId: id, messageId,
        ...(status === "acknowledged" ? { observed: { sdkResolved: true } } : {}) }, release: { status: "unavailable", code: "CLOSE_FAILED" } });
  }
});
