// Test-only preload for the actual CLI subprocess. Never opens a network socket.
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
const id = "b7abb649-7b0d-4ead-9368-b2a4c7c58209";
const cwd = fileURLToPath(new URL("../", import.meta.url));
let sends = 0;
globalThis.WebSocket = class {
  readyState = 1;
  constructor(url) { this.mode = new URL(url).hostname; queueMicrotask(() => this.onopen?.()); }
  message(message) { this.onmessage?.({ data: JSON.stringify({ type: "session", message }) }); }
  close() { this.readyState = 3; }
  send(data) {
    const frame = JSON.parse(String(data)); const request = frame.message;
    if (frame.type === "hello") queueMicrotask(() => this.message({ type: "status", payload: {
      status: "server_info", serverId: "offline", hostname: null, version: null,
      features: { ownedSubscriptions: true, workspaceMultiplicity: true } } }));
    else if (request?.type === "fetch_agent_request") queueMicrotask(() => this.message({ type: "fetch_agent_response", payload: {
      requestId: request.requestId, error: null, project: null, agent: {
        id, cwd, archivedAt: null, activeTurn: null, pendingPermissions: [], status: "idle", provider: "codex", model: "synthetic",
        createdAt: "2026-10-11T00:00:00Z", updatedAt: "2026-10-11T00:00:00Z", lastUserMessageAt: null,
        capabilities: { supportsStreaming: true, supportsSessionPersistence: true, supportsDynamicModes: false,
          supportsMcpServers: false, supportsReasoningStream: false, supportsToolInvocations: true },
        currentModeId: null, availableModes: [], persistence: null, title: null, labels: {} } } }));
    else if (request?.type === "send_agent_message_request") {
      assert.equal(++sends, 1, "CLI cannot resend");
      assert.deepEqual(Object.keys(request).sort(), ["type", "requestId", "agentId", "messageId", "text", "activeTurnBehavior"].sort());
      assert.equal(request.agentId, id);
      assert.equal(request.messageId, "2ac576eb-a0ad-4f85-8bf7-53d12a67c080");
      assert.equal(request.text, "  Synthetic text\n");
      assert.equal(request.activeTurnBehavior, "steer");
      queueMicrotask(() => {
        if (this.mode === "disconnect.offline") { this.readyState = 3; this.onclose?.({ code: 1006, reason: "private-sentinel" }); }
        else this.message({ type: "send_agent_message_response", payload: { requestId: request.requestId, agentId: "bad-ack-id",
          accepted: this.mode !== "reject.offline", error: this.mode === "reject.offline" ? "private-sentinel" : null } });
      });
    } else if (request) assert.fail(`Unexpected SDK request ${request.type}`);
  }
};
