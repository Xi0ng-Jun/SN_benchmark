import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { createBridge } from "../dist/bridge.js";
import { encodeResponse, executeRequest, MAX_OUTPUT_BYTES } from "../dist/cli-protocol.js";
import { readFixture } from "./read-fixture.ts";

const config = { url: "ws://127.0.0.1:1/ws", password: "private-sentinel" };

test("one-shot CLI executes exactly one command and reports actual release separately", async () => {
  const f = readFixture();
  const output = await executeRequest({ config, command: { op: "readAgent", agentId: "agent-example" } },
    (options) => createBridge(options, () => f.client));
  assert.equal(output.result.status, "ok");
  assert.deepEqual(output.release, { status: "ok" });
  assert.equal(f.released(), true);
  assert.deepEqual(f.references, ["agent-example"]);
  assert.equal(JSON.stringify(output).includes("private-sentinel"), false);
});

test("a successful read cannot hide release failure", async () => {
  const f = readFixture();
  f.client.close = async () => { throw Error("private-sentinel"); };
  const output = await executeRequest({ config, command: { op: "listProviders" } },
    (options) => createBridge(options, () => f.client));
  assert.equal(output.result.status, "ok");
  assert.deepEqual(output.release, { status: "unavailable", code: "CLOSE_FAILED" });
  assert.equal(JSON.stringify(output).includes("private-sentinel"), false);
});

test("one-shot failure still closes; thrown factory/operation errors never escape as raw messages", async () => {
  const f = readFixture();
  f.client.connect = async () => { throw Error("private-sentinel"); };
  const output = await executeRequest({ config, command: { op: "listWorkspaces" } },
    (options) => createBridge(options, () => f.client));
  assert.deepEqual(output, { result: { status: "unavailable", code: "CONNECT_FAILED" }, release: { status: "ok" } });
  assert.equal(f.released(), true);
  assert.deepEqual(await executeRequest({ config, command: { op: "readAgent", agentId: "agent-example" } },
    () => { throw Error("private-sentinel"); }), {
    result: { status: "unavailable", code: "INTERNAL_ERROR" }, release: { status: "not_needed" },
  });
});

test("narrow CLI schema rejects writes, extra config and invalid cursors before constructing a client", async () => {
  for (const input of [null, [], { config, command: { op: "send", text: "private-sentinel" } },
    { config: { ...config, logger: "private-sentinel" }, command: { op: "listProviders" } },
    { config, command: { op: "readAgent", agentId: "" } },
    { config, command: { op: "readTimeline", agentId: "a", cursor: { epoch: "e", seq: 1.5 } } },
    { config, command: { op: "readTimeline", agentId: "a", limit: 0 } },
    { config, command: { op: "listWorkspaces", subscribe: {} } },
    { config, command: { op: "listModels", provider: "" } },
  ]) {
    assert.deepEqual(await executeRequest(input, () => { assert.fail("factory must not be reached"); }), {
      result: { status: "unavailable", code: "INVALID_INPUT" }, release: { status: "not_needed" },
    });
  }
});

test("CLI whitelist supports the five reads and archiveAgent", async () => {
  for (const command of [
    { op: "listProviders", cwd: "/example/repo" }, { op: "listModels", provider: "example" },
    { op: "listWorkspaces", limit: 10 }, { op: "readAgent", agentId: "agent-example" }, { op: "archiveAgent", agentId: "agent-example" },
    { op: "readTimeline", agentId: "agent-example", direction: "after", cursor: { epoch: "epoch-example", seq: 12 }, limit: 2 },
  ]) {
    const f = readFixture();
    const output = await executeRequest({ config, command }, (options) => createBridge(options, () => f.client));
    assert.equal(output.result.status, "ok");
    assert.deepEqual(output.release, { status: "ok" });
  }
});

test("an oversized serialized result fails as a whole while preserving release failure", () => {
  const output = encodeResponse({ result: { status: "ok", value: "x".repeat(MAX_OUTPUT_BYTES) },
    release: { status: "unavailable", code: "CLOSE_FAILED" } });
  assert.ok(Buffer.byteLength(output) < MAX_OUTPUT_BYTES);
  assert.deepEqual(JSON.parse(output), { result: { status: "unavailable", code: "RESPONSE_TOO_LARGE" },
    release: { status: "unavailable", code: "CLOSE_FAILED" } });
});

test("CLI readTimeline accepts expectedCwd and enforces scope with independent release", async () => {
  const f = readFixture();
  f.data.agent!.cwd = "/";
  const output = await executeRequest({ config, command: { op: "readTimeline", agentId: "agent-example", expectedCwd: "/" } },
    (options) => createBridge(options, () => f.client));
  assert.equal(output.result.status, "ok");
  assert.deepEqual(output.release, { status: "ok" });
  assert.deepEqual(f.queries[0]?.options, { limit: 50, projection: "projected" });
});

test("CLI expectedCwd is confined to readTimeline and invalid values never construct SDK", async () => {
  for (const command of [
    { op: "readAgent", agentId: "agent-example", expectedCwd: "/" },
    { op: "listProviders", expectedCwd: "/" }, { op: "listModels", provider: "example", expectedCwd: "/" },
    { op: "listWorkspaces", expectedCwd: "/" },
    ...["relative", "", null, "/nul\0path"].map((expectedCwd) => ({ op: "readTimeline", agentId: "agent-example", expectedCwd })),
  ]) {
    assert.deepEqual(await executeRequest({ config, command }, () => { assert.fail("factory must not be reached"); }),
      { result: { status: "unavailable", code: "INVALID_INPUT" }, release: { status: "not_needed" } });
  }
});

test("CLI expectedCwd mismatch cannot return history while release remains truthful", async () => {
  const f = readFixture();
  f.data.agent!.cwd = "/";
  const output = await executeRequest({ config, command: { op: "readTimeline", agentId: "agent-example", expectedCwd: process.cwd() } },
    (options) => createBridge(options, () => f.client));
  assert.deepEqual(output, { result: { status: "unavailable", code: "SCOPE_MISMATCH" }, release: { status: "ok" } });
  assert.deepEqual(f.queries, []);
});

test("CLI preserves a real trailing-space expectedCwd through JSON parsing and scope enforcement", async (t) => {
  const root = await fs.mkdtemp(fileURLToPath(new URL("../.scope-test-", import.meta.url)));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  const spaced = join(root, "owned ");
  const trimmed = join(root, "owned");
  await fs.mkdir(spaced);
  await fs.mkdir(trimmed);
  const f = readFixture();
  f.data.agent!.cwd = spaced;
  const request = JSON.parse(JSON.stringify({ config, command: {
    op: "readTimeline", agentId: "agent-example", expectedCwd: spaced, limit: 50, projection: "projected",
  } }));
  const output = await executeRequest(request, (options) => createBridge(options, () => f.client));
  assert.equal(output.result.status, "ok");
  assert.deepEqual(output.release, { status: "ok" });
  assert.deepEqual(f.queries[0]?.options, { limit: 50, projection: "projected" });

  const other = readFixture();
  other.data.agent!.cwd = trimmed;
  assert.deepEqual(await executeRequest(request, (options) => createBridge(options, () => other.client)), {
    result: { status: "unavailable", code: "SCOPE_MISMATCH" }, release: { status: "ok" },
  });
  assert.deepEqual(other.queries, []);
});
