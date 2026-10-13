import assert from "node:assert/strict";
import test from "node:test";
import { createPaseoClient } from "@getpaseo/client";
import { createBridge } from "../dist/bridge.js";
import { executeRequest } from "../dist/cli-protocol.js";
import { readFixture } from "./read-fixture.ts";
import { fileURLToPath } from "node:url";

const id = "b7abb649-7b0d-4ead-9368-b2a4c7c58209";
const messageId = "2ac576eb-a0ad-4f85-8bf7-53d12a67c080";
const text = "  Synthetic offline text\n";
class Socket {
  readyState = 1;
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: ((event: { code: number; reason: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  sent: any[] = [];
  onSend?: (frame: any) => void;
  send(data: string | ArrayBuffer | Uint8Array) { const frame = JSON.parse(String(data)); this.sent.push(frame); this.onSend?.(frame); }
  close() { this.readyState = 3; }
  disconnect() { this.readyState = 3; this.onclose?.({ code: 1006, reason: "private-sentinel" }); }
  message(message: object) { this.onmessage?.({ data: JSON.stringify({ type: "session", message }) }); }
  requests(type = "send_agent_message_request") { return this.sent.filter((f) => f.message?.type === type).map((f) => f.message); }
  hello() { this.onopen?.(); this.message({ type: "status", payload: { status: "server_info", serverId: "offline", hostname: null,
    version: null, features: { ownedSubscriptions: true, workspaceMultiplicity: true } } }); }
  ack(request: any, extra = {}) { this.message({ type: "send_agent_message_response", payload: {
    requestId: request.requestId, agentId: id, accepted: true, error: null, ...extra } }); }
}
async function connected(t: test.TestContext) {
  const ws = new Socket();
  let sockets = 0;
  t.mock.method(globalThis, "WebSocket", function () { sockets++; return ws; });
  const client = createPaseoClient({ url: "ws://offline.test/ws", reconnect: { enabled: false },
    logger: { debug() {}, info() {}, warn() {}, error() {} } });
  t.after(() => client.close());
  const pending = client.connect(); ws.hello(); await pending;
  return { ws, client, agent: client.agents.ref(id), sockets: () => sockets };
}
const settled = (p: Promise<unknown>) => p.then(() => ({ status: "resolved" }), (error: any) => ({ status: "rejected", code: error.code }));
async function microtasks() { for (let i = 0; i < 8; i++) await Promise.resolve(); }

test("public SDK send wire options, void ACK, bad ACK identity and absence of execution query", async (t) => {
  for (const behavior of [undefined, "interrupt", "steer"] as const) {
    const f = await connected(t);
    const pending = f.agent.send(text, { messageId, ...(behavior ? { activeTurnBehavior: behavior } : {}) });
    const req = f.ws.requests()[0];
    assert.deepEqual(Object.keys(req).sort(), ["type", "requestId", "agentId", "text", "messageId", ...(behavior ? ["activeTurnBehavior"] : [])].sort());
    assert.equal(req.agentId, id); assert.equal(req.messageId, messageId); assert.equal(req.text, text); assert.equal(req.activeTurnBehavior, behavior);
    let done = false; void pending.then(() => { done = true; });
    f.ws.ack(req, { requestId: "wrong" }); await microtasks(); assert.equal(done, false);
    f.ws.ack(req, { agentId: "different-agent" }); assert.equal(await pending, undefined);
    assert.equal(f.agent.current(), null); assert.equal(f.ws.requests("wait_for_finish_request").length, 0);
    await f.client.close(); t.mock.restoreAll();
  }
});

test("public SDK rejects before-ACK disconnect without create-style pending or retries", async (t) => {
  const f = await connected(t);
  const pending = settled(f.agent.send(text, { messageId, activeTurnBehavior: "steer" }));
  f.ws.disconnect(); assert.equal(f.client.getConnectionState().status, "disconnected");
  let timer: NodeJS.Timeout | undefined;
  try {
    const result = await Promise.race([pending, new Promise((resolve) => { timer = setTimeout(() => resolve("pending"), 150); })]);
    assert.deepEqual(result, { status: "rejected", code: "DAEMON_CONNECTION_LOST" });
    assert.equal(f.ws.requests().length, 1); assert.equal(f.sockets(), 1);
  } finally { clearTimeout(timer); }
});

test("public SDK connected-no-ACK timeout is 60s, accepted:false rejects, no automatic replay", async (t) => {
  const f = await connected(t);
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let done = false;
  const pending = settled(f.agent.send(text, { messageId })).then((value) => { done = true; return value; });
  await microtasks(); t.mock.timers.tick(59999); await microtasks(); assert.equal(done, false);
  t.mock.timers.tick(1); assert.deepEqual(await pending, { status: "rejected", code: "DAEMON_REQUEST_TIMEOUT" });
  assert.equal(f.ws.requests().length, 1);
  const rejected = settled(f.agent.send(text, { messageId }));
  f.ws.ack(f.ws.requests()[1], { accepted: false, error: "private-sentinel" });
  assert.deepEqual(await rejected, { status: "rejected", code: undefined });
  assert.equal(f.ws.requests().length, 2);
});

test("CLI public SDK sends once after scoped refresh, bad ACK only acknowledged, disconnect unknown and finally release", async (t) => {
  for (const outcome of ["ack", "disconnect", "reject"] as const) for (const failedRelease of [false, true]) {
    const ws = new Socket();
    t.mock.method(globalThis, "WebSocket", function () { queueMicrotask(() => ws.onopen?.()); return ws; });
    const cwd = fileURLToPath(new URL("../", import.meta.url));
    const agent = { ...readFixture().data.agent!, id, cwd, archivedAt: null, activeTurn: null, pendingPermissions: [] };
    ws.onSend = (frame) => {
      if (frame.type === "hello") queueMicrotask(() => ws.message({ type: "status", payload: { status: "server_info", serverId: "offline", hostname: null,
        version: null, features: { ownedSubscriptions: true, workspaceMultiplicity: true } } }));
      const request = frame.message;
      if (request?.type === "fetch_agent_request") queueMicrotask(() => ws.message({ type: "fetch_agent_response", payload: {
        requestId: request.requestId, agent, project: null, error: null } }));
      if (request?.type === "send_agent_message_request") queueMicrotask(() => {
        if (outcome === "disconnect") ws.disconnect();
        else ws.ack(request, outcome === "reject" ? { accepted: false, error: "private-sentinel" } : { agentId: "bad-ack-id" });
      });
    };
    let client!: ReturnType<typeof createPaseoClient>;
    const response = await executeRequest({ config: { url: "ws://offline.test/ws" }, command: {
      op: "sendAgent", agentId: id, expectedCwd: cwd, text, messageId, activeTurnBehavior: "steer" } },
    (config) => createBridge(config, (options) => {
      client = createPaseoClient(options); const close = client.close;
      client.close = async () => { await close(); if (failedRelease) throw Error("private-sentinel"); };
      return client;
    }));
    assert.deepEqual(response, { result: outcome === "ack" ? { status: "acknowledged", agentId: id, messageId } : {
      status: "unknown", code: "SEND_OUTCOME_UNKNOWN", cause: outcome === "disconnect" ? "CONNECTION_UNAVAILABLE" : "SEND_FAILED", agentId: id, messageId },
      release: failedRelease ? { status: "unavailable", code: "CLOSE_FAILED" } : { status: "ok" } });
    assert.equal(ws.requests().length, 1);
    assert.deepEqual(ws.requests()[0], { type: "send_agent_message_request", requestId: ws.requests()[0].requestId,
      agentId: id, text, messageId, activeTurnBehavior: "steer" });
    assert.equal(ws.requests("fetch_agent_request").length, 1);
    assert.equal(ws.requests("wait_for_finish_request").length, 0);
    assert.equal(client.getConnectionState().status, "disposed");
    assert.equal(JSON.stringify(response).includes("private-sentinel"), false);
    t.mock.restoreAll();
  }
});
