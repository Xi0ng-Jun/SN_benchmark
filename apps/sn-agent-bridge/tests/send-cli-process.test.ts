import assert from "node:assert/strict";
import test from "node:test";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

const id = "b7abb649-7b0d-4ead-9368-b2a4c7c58209";
const messageId = "2ac576eb-a0ad-4f85-8bf7-53d12a67c080";
const cwd = fileURLToPath(new URL("../", import.meta.url));
const command = { op: "sendAgent", agentId: id, expectedCwd: cwd, messageId,
  text: "  Synthetic text\n", activeTurnBehavior: "steer" };
function invoke(input: string, fakeSocket = false) {
  return new Promise<{ stdout: string; stderr: string; code: number | null }>((resolve, reject) => {
    const args = [...(fakeSocket ? ["--import", fileURLToPath(new URL("./send-cli-socket.mjs", import.meta.url))] : []),
      fileURLToPath(new URL("../dist/cli.js", import.meta.url))];
    const child = spawn(process.execPath, args, { cwd, stdio: ["pipe", "pipe", "pipe"], signal: AbortSignal.timeout(5000) });
    let stdout = ""; let stderr = "";
    child.stdout.on("data", (chunk) => { stdout += chunk; }); child.stderr.on("data", (chunk) => { stderr += chunk; });
    child.on("error", reject); child.on("close", (code) => resolve({ stdout, stderr, code }));
    child.stdin.on("error", () => {}); child.stdin.end(input);
  });
}

test("actual CLI subprocess rejects recognizable send extras and malformed narrow values without SDK or input echo", async () => {
  for (const changed of [
    ...["workspaceId", "options", "attachments", "images", "parent", "env", "config", "tools", "mcp", "prompt", "toolPolicy", "systemPrompt"].map((key) => ({ [key]: "private-sentinel" })),
    { agentId: "short" }, { messageId: "short" }, { activeTurnBehavior: undefined }, { activeTurnBehavior: "reject" },
    { text: " \n" }, { text: "private-sentinel\0" }, { text: "x".repeat(16385) }, { text: "界".repeat(11000) },
  ]) {
    const output = await invoke(JSON.stringify({ config: { url: "ws://127.0.0.1:1/ws", password: "private-sentinel" }, command: { ...command, ...changed } }));
    assert.equal(output.code, 0); assert.equal(output.stderr, ""); assert.equal(output.stdout.split("\n").length, 2);
    assert.deepEqual(JSON.parse(output.stdout), { result: { status: "not_attempted", code: "INVALID_INPUT" }, release: { status: "not_needed" } });
    assert.equal(output.stdout.includes("private-sentinel"), false);
  }
  const output = await invoke(JSON.stringify({ config: { url: "http://private-sentinel/ws" }, command }));
  assert.equal(output.stderr, "");
  assert.deepEqual(JSON.parse(output.stdout), { result: { status: "not_attempted", code: "INVALID_CONFIG", agentId: id, messageId }, release: { status: "ok" } });
});

test("actual CLI subprocess real SDK with fake WebSocket returns one acknowledged/unknown envelope and releases", async () => {
  for (const mode of ["ack", "disconnect", "reject"]) {
    const output = await invoke(JSON.stringify({ config: { url: `ws://${mode}.offline/ws`, password: "private-sentinel" }, command }), true);
    assert.equal(output.code, 0); assert.equal(output.stderr, ""); assert.equal(output.stdout.split("\n").length, 2);
    assert.deepEqual(JSON.parse(output.stdout), { result: mode === "ack" ? { status: "acknowledged", agentId: id, messageId }
      : { status: "unknown", code: "SEND_OUTCOME_UNKNOWN", cause: mode === "disconnect" ? "CONNECTION_UNAVAILABLE" : "SEND_FAILED", agentId: id, messageId },
      release: { status: "ok" } });
    assert.equal(output.stdout.includes("private-sentinel"), false);
  }
});
