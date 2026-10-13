import assert from "node:assert/strict";
import test from "node:test";
import { createPaseoClient, type PaseoWorkspace } from "@getpaseo/client";
import { readFixture } from "./read-fixture.ts";
import { createBridge } from "../dist/bridge.js";
import { executeRequest } from "../dist/cli-protocol.js";
import { fileURLToPath } from "node:url";

// Replace the host WebSocket in the test environment, keeping the public factory
// configuration within its package-root declaration. No private SDK import.
class Socket {
  readyState = 1;
  onopen: ((...args: unknown[]) => void) | null = null;
  onmessage: ((...args: unknown[]) => void) | null = null;
  onclose: ((...args: unknown[]) => void) | null = null;
  onerror: ((...args: unknown[]) => void) | null = null;
  sent: any[] = [];
  onSend?: (frame: any) => void;
  send(data: string | ArrayBuffer | Uint8Array) {
    const frame = JSON.parse(String(data));
    this.sent.push(frame);
    this.onSend?.(frame);
  }
  close() { this.readyState = 3; }
  disconnect() { this.readyState = 3; this.onclose?.({ code: 1006, reason: "private-sentinel" }); }
  message(message: object) { this.onmessage?.({ data: JSON.stringify({ type: "session", message }) }); }
}

test("installed public workspace create supports supplied UUID/key and omits prompt on modern host", { timeout: 3000 }, async (t) => {
  const ws = new Socket();
  t.mock.method(globalThis, "WebSocket", function () { return ws; });
  const client = createPaseoClient({ url: "ws://offline.test/ws",
    reconnect: { enabled: false }, logger: { debug() {}, info() {}, warn() {}, error() {} } });
  try {
    const connecting = client.connect();
    ws.onopen?.();
    ws.message({ type: "status", payload: { status: "server_info", serverId: "offline", hostname: null,
      version: null, features: { creationLifecycle: true } } });
    await connecting;
    const workspace: PaseoWorkspace = { id: "workspace-offline", projectId: "project-offline",
      projectDisplayName: "Offline", projectRootPath: "/offline/repo", workspaceDirectory: "/offline/repo",
      projectKind: "directory", workspaceKind: "directory", name: "Offline", archivingAt: null,
      status: "done", statusEnteredAt: null, activityAt: null, scripts: [], gitRuntime: null, githubRuntime: null };
    const id = "b7abb649-7b0d-4ead-9368-b2a4c7c58209";
    const creating = client.workspaces.ref(workspace).agents.create({ agentId: id, idempotencyKey: "offline-key",
      config: { provider: "codex/model-test", modeId: "mode-test", thinkingOptionId: "effort-test" }, title: "Offline" });
    const request = ws.sent.at(-1)?.message;
    assert.equal(request?.type, "agent.create.request");
    assert.equal(request.agentId, id);
    assert.equal(request.idempotencyKey, "offline-key");
    assert.equal(request.workspaceId, workspace.id);
    assert.deepEqual(request.config, { provider: "codex", model: "model-test", cwd: "/offline/repo",
      modeId: "mode-test", thinkingOptionId: "effort-test", title: "Offline" });
    for (const key of ["initialPrompt", "prompt", "images", "attachments", "callerAgentId", "env"]) assert.equal(Object.hasOwn(request, key), false, key);
    const agent = { ...readFixture().data.agent!, id, provider: "codex", model: "model-test", cwd: "/offline/repo",
      workspaceId: workspace.id, activeTurn: null };
    ws.message({ type: "agent.create.response", payload: { requestId: request.requestId, agent, error: null } });
    const handle = await creating;
    assert.equal(handle.id, id);
    assert.equal(ws.sent.filter((frame) => frame.message?.type === "agent.create.request").length, 1);
    assert.equal(ws.sent.some((frame) => frame.message?.type === "send_agent_message_request"), false);
  } finally { await client.close(); }
});

test("installed public SDK retains creation pending after disconnect before creation receipt with reconnect disabled", { timeout: 3000 }, async (t) => {
  const ws = new Socket();
  t.mock.method(globalThis, "WebSocket", function () { return ws; });
  const client = createPaseoClient({ url: "ws://offline.test/ws", reconnect: { enabled: false },
    logger: { debug() {}, info() {}, warn() {}, error() {} } });
  let timer: NodeJS.Timeout | undefined;
  try {
    const connecting = client.connect(); ws.onopen?.();
    ws.message({ type: "status", payload: { status: "server_info", serverId: "offline", hostname: null,
      version: null, features: { creationLifecycle: true } } });
    await connecting;
    const creating = client.agents.create({ agentId: "b7abb649-7b0d-4ead-9368-b2a4c7c58209",
      idempotencyKey: "offline-disconnect", cwd: "/offline/repo", config: { provider: "codex/model-test" } });
    assert.equal(ws.sent.at(-1)?.message?.type, "agent.create.request");
    const settled = creating.then(() => "resolved", () => "rejected");
    ws.disconnect();
    assert.equal(client.getConnectionState().status, "disconnected");
    const outcome = await Promise.race([settled,
      new Promise<string>((resolve) => { timer = setTimeout(() => resolve("pending"), 150); })]);
    assert.equal(outcome, "pending");
    assert.equal(ws.sent.filter((frame) => frame.message?.type === "agent.create.request").length, 1);
    await client.close();
    assert.equal(await settled, "rejected");
  } finally { clearTimeout(timer); await client.close(); }
});

test("bridge must settle unknown on public SDK disconnect before creation receipt, without waiting for close", { timeout: 3000 }, async (t) => {
  const ws = new Socket();
  t.mock.method(globalThis, "WebSocket", function () { return ws; });
  const cwd = fileURLToPath(new URL("../", import.meta.url));
  const workspace: PaseoWorkspace = { id: "workspace-offline", projectId: "project-offline",
    projectDisplayName: "Offline", projectRootPath: cwd, workspaceDirectory: cwd,
    projectKind: "directory", workspaceKind: "directory", name: "Offline", archivingAt: null,
    status: "done", statusEnteredAt: null, activityAt: null, scripts: [], gitRuntime: null, githubRuntime: null };
  let sdk!: ReturnType<typeof createPaseoClient>;
  let created!: () => void;
  const sent = new Promise<void>((resolve) => { created = resolve; });
  ws.onSend = (frame) => {
    const request = frame.message;
    if (request?.type === "fetch_workspaces_request") queueMicrotask(() => ws.message({
      type: "fetch_workspaces_response", payload: { requestId: request.requestId, entries: [workspace],
        pageInfo: { nextCursor: null, prevCursor: null, hasMore: false } } }));
    if (request?.type === "fetch_agent_request") queueMicrotask(() => ws.message({
      type: "fetch_agent_response", payload: { requestId: request.requestId, agent: null, project: null, error: null } }));
    if (request?.type === "agent.create.request") created();
  };
  const bridge = createBridge({ url: "ws://offline.test/ws" }, (config) => { sdk = createPaseoClient(config); return sdk; });
  let timer: NodeJS.Timeout | undefined;
  try {
    const connecting = bridge.connect(); ws.onopen?.();
    ws.message({ type: "status", payload: { status: "server_info", serverId: "offline", hostname: null,
      version: null, features: { creationLifecycle: true, workspaceMultiplicity: true, ownedSubscriptions: true } } });
    assert.deepEqual(await connecting, { status: "ok" });
    const pending = bridge.createAgent({ workspaceId: workspace.id, expectedCwd: cwd,
      agentId: "b7abb649-7b0d-4ead-9368-b2a4c7c58209", idempotencyKey: "offline-disconnect",
      config: { provider: "codex/model-test" } });
    await Promise.race([sent, pending.then((result) => assert.fail(`precheck unexpectedly settled: ${result.status}`)),
      new Promise<void>((_, reject) => { timer = setTimeout(() => reject(Error(`create not sent; types: ${ws.sent.map((frame) => frame.message?.type ?? frame.type).join(",")}`)), 500); })]);
    clearTimeout(timer);
    ws.disconnect();
    assert.equal(sdk.getConnectionState().status, "disconnected");
    const result = await Promise.race([pending,
      new Promise<"still_pending">((resolve) => { timer = setTimeout(() => resolve("still_pending"), 150); })]);
    assert.equal(ws.sent.filter((frame) => frame.message?.type === "agent.create.request").length, 1);
    assert.notEqual(result, "still_pending", "known disconnect must terminate the bridge wait before close");
    assert.deepEqual(result, { status: "unknown", code: "CREATE_OUTCOME_UNKNOWN", cause: "CONNECTION_UNAVAILABLE",
      agentId: "b7abb649-7b0d-4ead-9368-b2a4c7c58209" });
    assert.deepEqual(await bridge.close(), { status: "ok" });
    assert.equal(JSON.stringify(result).includes("private-sentinel"), false);
  } finally { clearTimeout(timer); await bridge.close(); }
});

test("installed public SDK rejects caller-selected ID on legacy host without a legacy create RPC", { timeout: 3000 }, async (t) => {
  const ws = new Socket();
  t.mock.method(globalThis, "WebSocket", function () { return ws; });
  const client = createPaseoClient({ url: "ws://offline.test/ws",
    reconnect: { enabled: false }, logger: { debug() {}, info() {}, warn() {}, error() {} } });
  try {
    const connecting = client.connect(); ws.onopen?.();
    ws.message({ type: "status", payload: { status: "server_info", serverId: "offline", hostname: null,
      version: null, features: {} } });
    await connecting;
    await assert.rejects(client.agents.create({ agentId: "b7abb649-7b0d-4ead-9368-b2a4c7c58209",
      idempotencyKey: "offline-key", cwd: "/offline/repo", config: { provider: "codex/model-test" } }));
    assert.equal(ws.sent.some((frame) => ["agent.create.request", "create_agent_request"].includes(frame.message?.type)), false);
  } finally { await client.close(); }
});

test("CLI finally releases the public SDK after one disconnected create, retaining unknown and actual release", { timeout: 3000 }, async (t) => {
  for (const failedRelease of [false, true]) await t.test(`release failure=${failedRelease}`, async (t) => {
    const ws = new Socket();
    t.mock.method(globalThis, "WebSocket", function () {
      queueMicrotask(() => ws.onopen?.());
      return ws;
    });
    const cwd = fileURLToPath(new URL("../", import.meta.url));
    const workspace: PaseoWorkspace = { id: "workspace-offline", projectId: "project-offline",
      projectDisplayName: "Offline", projectRootPath: cwd, workspaceDirectory: cwd,
      projectKind: "directory", workspaceKind: "directory", name: "Offline", archivingAt: null,
      status: "done", statusEnteredAt: null, activityAt: null, scripts: [], gitRuntime: null, githubRuntime: null };
    ws.onSend = (frame) => {
      const request = frame.message;
      if (frame.type === "hello") queueMicrotask(() => ws.message({ type: "status", payload: {
        status: "server_info", serverId: "offline", hostname: null, version: null,
        features: { creationLifecycle: true, workspaceMultiplicity: true, ownedSubscriptions: true } } }));
      if (request?.type === "fetch_workspaces_request") queueMicrotask(() => ws.message({
        type: "fetch_workspaces_response", payload: { requestId: request.requestId, entries: [workspace],
          pageInfo: { nextCursor: null, prevCursor: null, hasMore: false } } }));
      if (request?.type === "fetch_agent_request") queueMicrotask(() => ws.message({
        type: "fetch_agent_response", payload: { requestId: request.requestId, agent: null, project: null, error: null } }));
      if (request?.type === "agent.create.request") queueMicrotask(() => ws.disconnect());
    };
    let sdk!: ReturnType<typeof createPaseoClient>;
    let closes = 0;
    const pending = executeRequest({ config: { url: "ws://offline.test/ws" }, command: {
      op: "createAgent", workspaceId: workspace.id, expectedCwd: cwd,
      agentId: "b7abb649-7b0d-4ead-9368-b2a4c7c58209", idempotencyKey: "offline-cli-disconnect",
      config: { provider: "codex/model-test" } } },
    (config) => createBridge(config, (options) => {
      sdk = createPaseoClient(options);
      const close = sdk.close;
      sdk.close = async () => { closes++; await close(); if (failedRelease) throw Error("private-sentinel"); };
      return sdk;
    }));
    let timer: NodeJS.Timeout | undefined;
    try {
      const result = await Promise.race([pending,
        new Promise<"still_pending">((resolve) => { timer = setTimeout(() => resolve("still_pending"), 500); })]);
      assert.notEqual(result, "still_pending", "CLI must reach finally without external close");
      assert.deepEqual(result, { result: { status: "unknown", code: "CREATE_OUTCOME_UNKNOWN", cause: "CONNECTION_UNAVAILABLE",
        agentId: "b7abb649-7b0d-4ead-9368-b2a4c7c58209" },
        release: failedRelease ? { status: "unavailable", code: "CLOSE_FAILED" } : { status: "ok" } });
      assert.equal(closes, 1);
      assert.equal(sdk.getConnectionState().status, "disposed");
      assert.equal(ws.sent.filter((frame) => frame.message?.type === "agent.create.request").length, 1);
      assert.equal(JSON.stringify(result).includes("private-sentinel"), false);
    } finally {
      clearTimeout(timer);
      // Test failure cleanup only; the successful assertions above require CLI
      // finally to close before we call this. Late SDK reject stays handled.
      await sdk.close().catch(() => {});
      await pending;
    }
  });
});
