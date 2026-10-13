import assert from "node:assert/strict";
import { createServer } from "node:net";
import { spawn } from "node:child_process";
import test from "node:test";
import { fileURLToPath } from "node:url";

function invoke(input: string | Buffer) {
  return new Promise<{ stdout: string; stderr: string; code: number | null }>((resolve, reject) => {
    const child = spawn(process.execPath, [fileURLToPath(new URL("../dist/cli.js", import.meta.url))], {
      stdio: ["pipe", "pipe", "pipe"], signal: AbortSignal.timeout(5000),
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => { stdout += chunk; });
    child.stderr.on("data", (chunk) => { stderr += chunk; });
    child.on("error", reject);
    child.on("close", (code) => resolve({ stdout, stderr, code }));
    child.stdin.on("error", () => {}); // oversized input may close its pipe early
    child.stdin.end(input);
  });
}

test("CLI subprocess bad JSON, multiple documents, invalid UTF-8 and input overflow produce exactly one fixed JSON", async () => {
  for (const input of ['{"private-sentinel":', '{}\n{}', Buffer.from([255]), "x".repeat(65537)]) {
    const output = await invoke(input);
    assert.equal(output.code, 0);
    assert.equal(output.stderr, "");
    assert.equal(output.stdout.split("\n").length, 2);
    assert.deepEqual(JSON.parse(output.stdout), { result: { status: "unavailable", code: "INVALID_INPUT" },
      release: { status: "not_needed" } });
    assert.equal(output.stdout.includes("private-sentinel"), false);
  }
});

test("CLI subprocess config/operation rejection never starts SDK connection or echoes input", async () => {
  for (const request of [
    { config: { url: "ws://127.0.0.1:1/ws", password: "private-sentinel" }, command: { op: "send", text: "private-sentinel" } },
    { config: { url: "http://private-sentinel/ws" }, command: { op: "listWorkspaces" } },
  ]) {
    const output = await invoke(JSON.stringify(request));
    const response = JSON.parse(output.stdout);
    assert.equal(output.code, 0);
    assert.equal(output.stderr, "");
    assert.equal(response.result.status, "unavailable");
    assert.equal(output.stdout.includes("private-sentinel"), false);
    assert.deepEqual(response.release, { status: request.command.op === "send" ? "not_needed" : "ok" });
  }
});

test("CLI subprocess real SDK handshake failure is quiet and still reports release", async () => {
  const server = createServer((socket) => socket.destroy());
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  assert.ok(address && typeof address === "object");
  try {
    const output = await invoke(JSON.stringify({
      config: { url: `ws://127.0.0.1:${address.port}/ws`, password: "private-sentinel", connectTimeoutMs: 100 },
      command: { op: "readAgent", agentId: "agent-example" },
    }));
    assert.equal(output.code, 0);
    assert.equal(output.stderr, "");
    assert.equal(output.stdout.split("\n").length, 2);
    assert.deepEqual(JSON.parse(output.stdout), { result: { status: "unavailable", code: "CONNECT_FAILED" }, release: { status: "ok" } });
    assert.equal(output.stdout.includes("private-sentinel"), false);
  } finally {
    await new Promise<void>((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
  }
});

test("CLI subprocess create rejects forbidden fields and invalid connection as explicitly not_attempted", async () => {
  const command = { op: "createAgent", workspaceId: "workspace-example", expectedCwd: "/example/repo",
    agentId: "b7abb649-7b0d-4ead-9368-b2a4c7c58209", idempotencyKey: "create-example",
    config: { provider: "codex/model-example" } };
  for (const forbidden of ["prompt", "env", "parent", "git", "worktree", "images", "attachments", "mcp", "features", "toolPolicy", "systemPrompt", "options"]) {
    const output = await invoke(JSON.stringify({ config: { url: "ws://127.0.0.1:1/ws", password: "private-sentinel" },
      command: { ...command, [forbidden]: "private-sentinel" } }));
    assert.equal(output.code, 0);
    assert.equal(output.stderr, "");
    assert.deepEqual(JSON.parse(output.stdout), { result: { status: "not_attempted", code: "INVALID_INPUT" }, release: { status: "not_needed" } });
    assert.equal(output.stdout.includes("private-sentinel"), false);
  }
  const output = await invoke(JSON.stringify({ config: { url: "http://private-sentinel/ws" }, command }));
  assert.equal(output.stderr, "");
  assert.deepEqual(JSON.parse(output.stdout), { result: { status: "not_attempted", code: "INVALID_CONFIG", agentId: command.agentId }, release: { status: "ok" } });
});
