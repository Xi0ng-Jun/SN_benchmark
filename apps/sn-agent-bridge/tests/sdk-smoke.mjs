import assert from "node:assert/strict";
import { createServer } from "node:net";
import test from "node:test";
import { createAgentBridge } from "@silicon-notebook/sn-agent-bridge";

// Real installed SDK + native WebSocket, isolated local TCP listener. No daemon,
// model process, preview state, or external credentials are used.
test("public package import releases a real SDK transport after a rejected handshake", { timeout: 5000 }, async () => {
  const server = createServer((socket) => socket.destroy());
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  assert.ok(address && typeof address === "object");
  const bridge = createAgentBridge({
    url: `ws://127.0.0.1:${address.port}/ws`, password: "isolated-test-secret", connectTimeoutMs: 500,
  });
  try {
    assert.deepEqual(await bridge.connect(), { status: "unavailable", code: "CONNECT_FAILED" });
    assert.deepEqual(await bridge.listProviders(), { status: "unavailable", code: "NOT_CONNECTED" });
    assert.deepEqual(await bridge.close(), { status: "ok" });
    assert.deepEqual(await bridge.connect(), { status: "unavailable", code: "CLOSED" });
  } finally {
    await bridge.close();
    await new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
  }
});
