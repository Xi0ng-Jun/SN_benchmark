import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { createBridge } from "../dist/bridge.js";
import { readFixture } from "./read-fixture.ts";

const config = { url: "ws://127.0.0.1:1/ws" };

async function scopeDirectories(t: { after(fn: () => Promise<void>): void }) {
  const root = await fs.mkdtemp(fileURLToPath(new URL("../.scope-test-", import.meta.url)));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  const owned = join(root, "owned-private-sentinel");
  const other = join(root, "other-private-sentinel");
  const alias = join(root, "alias");
  await fs.mkdir(owned);
  await fs.mkdir(other);
  await fs.symlink(owned, alias, "dir");
  return { root, owned, other, alias };
}

test("readAgent projects identity and actual capability flags without private snapshot fields", async () => {
  const f = readFixture();
  const bridge = createBridge(config, () => f.client);
  await bridge.connect();
  const result = await bridge.readAgent("agent-example");
  assert.deepEqual(result, { status: "ok", agent: {
    id: "agent-example", cwd: "/example/repo", workspaceId: "workspace-example", archivedAt: null,
    status: "idle", title: "Example investigation", provider: "example", model: "model-example",
    activeTurn: { turnId: "turn-example", startedAt: null },
    capabilities: { supportsStreaming: true, supportsSessionPersistence: true, supportsSessionListing: null,
      supportsDynamicModes: false, supportsMcpServers: false, supportsReasoningStream: true,
      supportsToolInvocations: true, supportsRewindConversation: null, supportsRewindFiles: null, supportsRewindBoth: null },
  } });
  assert.equal(JSON.stringify(result).includes("private-sentinel"), false);
  assert.deepEqual(f.references, ["agent-example"]);
  await bridge.close();
});

test("missing agent is distinct from unavailable; missing history never fetches timeline", async () => {
  const f = readFixture();
  f.data.agent = null;
  const bridge = createBridge(config, () => f.client);
  assert.deepEqual(await bridge.readAgent("agent-example"), { status: "unavailable", code: "NOT_CONNECTED" });
  await bridge.connect();
  assert.deepEqual(await bridge.readAgent("agent-example"), { status: "missing", code: "AGENT_NOT_FOUND" });
  assert.deepEqual(await bridge.readTimeline("agent-example"), { status: "missing", code: "AGENT_NOT_FOUND" });
  assert.deepEqual(f.queries, []);
  f.handle.refresh = async () => { throw Error("private-sentinel"); };
  assert.deepEqual(await bridge.readAgent("agent-example"), { status: "unavailable", code: "READ_FAILED" });
  await bridge.close();
});

test("archiveAgent archives once, reports an already archived Agent and keeps missing distinct", async () => {
  const f = readFixture();
  const bridge = createBridge(config, () => f.client);
  await bridge.connect();
  assert.deepEqual(await bridge.archiveAgent("agent-example"), { status: "ok", archivedAt: "2026-10-21T00:00:00Z", already: false });
  assert.deepEqual(await bridge.archiveAgent("agent-example"), { status: "ok", archivedAt: "2026-10-21T00:00:00Z", already: true });
  assert.deepEqual(f.archived, ["agent-example"]);
  f.data.agent = null;
  assert.deepEqual(await bridge.archiveAgent("agent-example"), { status: "missing", code: "AGENT_NOT_FOUND" });
  await bridge.close();
});

test("optional snapshot fields that the SDK did not observe remain unknown", async () => {
  const f = readFixture();
  delete f.data.agent!.workspaceId;
  delete f.data.agent!.archivedAt;
  delete f.data.agent!.activeTurn;
  const bridge = createBridge(config, () => f.client);
  await bridge.connect();
  const result = await bridge.readAgent("agent-example");
  assert.equal(result.status, "ok");
  if (result.status !== "ok") return;
  assert.equal(Object.hasOwn(result.agent, "workspaceId"), false);
  assert.equal(Object.hasOwn(result.agent, "archivedAt"), false);
  assert.equal(Object.hasOwn(result.agent, "activeTurn"), false);
  await bridge.close();
});

test("timeline preserves server pagination, disjoint source ranges and recovery flags with bounded tool detail", async () => {
  const f = readFixture();
  Object.assign(f.data.page, {
    reset: true, staleCursor: true, gap: true, hasOlder: true, hasNewer: true,
    startCursor: { epoch: "epoch-example", seq: 20 }, endCursor: { epoch: "epoch-example", seq: 26 },
    entries: [
      { provider: "example", timestamp: "now", seqStart: 20, seqEnd: 25,
        sourceSeqRanges: [{ startSeq: 20, endSeq: 20 }, { startSeq: 25, endSeq: 25 }],
        collapsed: ["assistant_merge"], turnId: "turn-example", item: { type: "assistant_message", text: "hello" } },
      { provider: "example", timestamp: "now", seqStart: 26, seqEnd: 26,
        sourceSeqRanges: [{ startSeq: 26, endSeq: 26 }], collapsed: ["tool_lifecycle"],
        item: { type: "tool_call", callId: "private-sentinel", name: "Read file", status: "completed", error: null,
          detail: { type: "read", filePath: "/repo/a.ts", content: "body", offset: 3 }, metadata: { secret: "private-sentinel" } } },
    ],
  });
  const bridge = createBridge(config, () => f.client);
  await bridge.connect();
  const result = await bridge.readTimeline("agent-example", {
    direction: "before", cursor: { epoch: "old-epoch", seq: 41 }, limit: 2, projection: "canonical",
  });
  assert.deepEqual(f.queries, [{ id: "agent-example", options: {
    direction: "before", cursor: { epoch: "old-epoch", seq: 41 }, limit: 2, projection: "canonical",
  } }]);
  assert.deepEqual(result, { status: "ok", agentId: "agent-example", direction: "before", projection: "projected",
    epoch: "epoch-example", reset: true, staleCursor: true, gap: true, mergeWindow: null,
    window: { minSeq: 10, maxSeq: 90, nextSeq: 91 }, startCursor: { epoch: "epoch-example", seq: 20 },
    endCursor: { epoch: "epoch-example", seq: 26 }, hasOlder: true, hasNewer: true, entries: [
      { provider: "example", timestamp: "now", seqStart: 20, seqEnd: 25,
        sourceSeqRanges: [{ startSeq: 20, endSeq: 20 }, { startSeq: 25, endSeq: 25 }],
        collapsed: ["assistant_merge"], turnId: "turn-example",
        item: { type: "assistant_message", text: { value: "hello", truncated: false, originalLength: 5, lengthUnit: "utf16" }, title: null, status: null } },
      { provider: "example", timestamp: "now", seqStart: 26, seqEnd: 26,
        sourceSeqRanges: [{ startSeq: 26, endSeq: 26 }], collapsed: ["tool_lifecycle"], turnId: null,
        item: { type: "tool_call", text: null, title: { value: "Read file", truncated: false, originalLength: 9, lengthUnit: "utf16" }, status: "completed",
          error: null, detail: { kind: "read", fields: { offset: 3,
            filePath: { value: "/repo/a.ts", truncated: false, originalLength: 10, lengthUnit: "utf16" },
            content: { value: "body", truncated: false, originalLength: 4, lengthUnit: "utf16" } } } } },
    ] });
  assert.equal(JSON.stringify(result).includes("private-sentinel"), false);
  await bridge.close();
});

test("empty timeline retains exact empty-window cursors rather than inventing offsets", async () => {
  const f = readFixture();
  Object.assign(f.data.page, { epoch: "empty-epoch", direction: "tail", window: { minSeq: 0, maxSeq: 0, nextSeq: 1 } });
  const bridge = createBridge(config, () => f.client);
  await bridge.connect();
  const result = await bridge.readTimeline("agent-example");
  assert.equal(result.status, "ok");
  if (result.status !== "ok") return;
  assert.deepEqual(result.entries, []);
  assert.equal(result.startCursor, null);
  assert.equal(result.endCursor, null);
  assert.deepEqual(result.window, { minSeq: 0, maxSeq: 0, nextSeq: 1 });
  assert.deepEqual(f.queries[0]?.options, { limit: 50, projection: "projected" });
  await bridge.close();
});

test("long user text is explicitly truncated without a split surrogate or changed entry identity", async () => {
  const f = readFixture();
  const long = "x".repeat(8191) + "😀" + "more";
  f.data.page.entries = [{ provider: "example", timestamp: "now", seqStart: 90, seqEnd: 90,
    sourceSeqRanges: [{ startSeq: 90, endSeq: 90 }], collapsed: [], item: { type: "user_message", text: long } }];
  const bridge = createBridge(config, () => f.client);
  await bridge.connect();
  const result = await bridge.readTimeline("agent-example");
  assert.equal(result.status, "ok");
  if (result.status !== "ok") return;
  assert.deepEqual(result.entries[0]?.item.text, { value: "x".repeat(8191), truncated: true, originalLength: 8197, lengthUnit: "utf16" });
  assert.equal(result.entries[0]?.seqStart, 90);
  await bridge.close();
});

test("agent alias and mismatched timeline identities fail explicitly instead of reading another session", async () => {
  const f = readFixture();
  const bridge = createBridge(config, () => f.client);
  await bridge.connect();
  assert.deepEqual(await bridge.readAgent("agent"), { status: "unavailable", code: "IDENTITY_MISMATCH" });
  f.data.page.agentId = "another-agent";
  assert.deepEqual(await bridge.readTimeline("agent-example"), { status: "unavailable", code: "IDENTITY_MISMATCH" });
  await bridge.close();
});

test("invalid/unbounded timeline options and oversized SDK pages cannot masquerade as complete history", async () => {
  const f = readFixture();
  const bridge = createBridge(config, () => f.client);
  await bridge.connect();
  for (const options of [{ limit: 0 }, { limit: 101 }, { cursor: { epoch: "e", seq: -1 } }]) {
    assert.deepEqual(await bridge.readTimeline("agent-example", options), { status: "unavailable", code: "INVALID_INPUT" });
  }
  assert.deepEqual(f.queries, []);
  f.data.page.entries = Array.from({ length: 101 }, (_, i) => ({ provider: "example", timestamp: "now",
    seqStart: i, seqEnd: i, sourceSeqRanges: [{ startSeq: i, endSeq: i }], collapsed: [],
    item: { type: "reasoning", text: "private-sentinel" } }));
  assert.deepEqual(await bridge.readTimeline("agent-example"), { status: "unavailable", code: "RESPONSE_TOO_LARGE" });
  f.data.page.entries = [];
  f.handle.timeline.refetch = async () => { throw Error("private-sentinel"); };
  assert.deepEqual(await bridge.readTimeline("agent-example"), { status: "unavailable", code: "READ_FAILED" });
  await bridge.close();
});

test("closing during timeline's identity read prevents a late follow-up timeline request", async () => {
  const f = readFixture();
  let resolve!: (value: Awaited<ReturnType<typeof f.handle.refresh>>) => void;
  f.handle.refresh = () => new Promise((yes) => { resolve = yes; });
  const bridge = createBridge(config, () => f.client);
  await bridge.connect();
  const pending = bridge.readTimeline("agent-example");
  assert.deepEqual(await bridge.close(), { status: "ok" });
  assert.deepEqual(await pending, { status: "unavailable", code: "CLOSED" });
  resolve({ agent: f.data.agent!, project: null });
  // Give the SDK refresh and its dependent operation their explicit microtasks.
  await Promise.resolve();
  await Promise.resolve();
  assert.deepEqual(f.queries, []);
});

const frag = (value: string, originalLength = value.length) =>
  ({ value, truncated: value.length < originalLength, originalLength, lengthUnit: "utf16" });

test("non-message activities expose reasoning, errors, notifications and task items as bounded text", async () => {
  const f = readFixture();
  f.data.page.entries = [
    { type: "reasoning", text: "think" },
    { type: "error", message: "boom" },
    { type: "notification", message: "note", level: "warning" },
    { type: "todo", items: [{ text: "a", completed: true }, { text: "b", completed: false, status: "in_progress" }] },
    { type: "plugin", id: "p", pluginId: "private-sentinel", kind: "private-sentinel", version: 1, data: "private-sentinel" },
    { type: "compaction", status: "loading", extra: "private-sentinel" },
  ].map((item, i) => ({ provider: "example", timestamp: "now", seqStart: i + 10, seqEnd: i + 10,
    sourceSeqRanges: [{ startSeq: i + 10, endSeq: i + 10 }], collapsed: [], item })) as typeof f.data.page.entries;
  const bridge = createBridge(config, () => f.client);
  await bridge.connect();
  const result = await bridge.readTimeline("agent-example");
  assert.equal(result.status, "ok");
  if (result.status !== "ok") return;
  assert.deepEqual(result.entries.map((entry) => entry.item), [
    { type: "reasoning", text: frag("think"), title: frag("Reasoning"), status: null },
    { type: "error", text: frag("boom"), title: frag("Error"), status: null },
    { type: "notification", text: frag("note"), title: frag("Notification"), status: "warning" },
    { type: "todo", text: null, title: frag("Tasks"), status: null, itemsOmitted: 0, items: [
      { text: frag("a"), completed: true, status: null }, { text: frag("b"), completed: false, status: "in_progress" }] },
    { type: "plugin", text: null, title: frag("Plugin activity"), status: null },
    { type: "compaction", text: null, title: frag("Compaction"), status: "loading" },
  ]);
  // Plugin payloads and unlisted item fields still stay out.
  assert.equal(JSON.stringify(result).includes("private-sentinel"), false);
  await bridge.close();
});

test("tool details flatten every SDK kind, keep failures and wrap future kinds as unknown input", async () => {
  const f = readFixture();
  const tool = (detail: unknown, extra: Record<string, unknown> = {}) =>
    ({ type: "tool_call", callId: "c", name: "t", status: "completed", error: null, detail, ...extra });
  f.data.page.entries = [
    tool({ type: "shell", command: "ls", cwd: "/r", output: "x", exitCode: null }, { status: "failed", error: { reason: "denied" } }),
    tool({ type: "edit", filePath: "a", unifiedDiff: "@@" }),
    tool({ type: "search", query: "q", filePaths: ["a", "b"], webResults: [{ title: "T", url: "u" }], truncated: true }),
    tool({ type: "sub_agent", log: "l", actions: [{ index: 0, toolName: "Read", summary: "s" }] }),
    tool({ type: "unknown", input: { a: 1 }, output: null }),
    tool({ type: "future_kind", value: 1 }),
    tool(undefined),
  ].map((item, i) => ({ provider: "example", timestamp: "now", seqStart: i + 1, seqEnd: i + 1,
    sourceSeqRanges: [{ startSeq: i + 1, endSeq: i + 1 }], collapsed: [], item })) as typeof f.data.page.entries;
  const bridge = createBridge(config, () => f.client);
  await bridge.connect();
  const result = await bridge.readTimeline("agent-example");
  assert.equal(result.status, "ok");
  if (result.status !== "ok") return;
  const items = result.entries.map((entry) => entry.item);
  assert.equal(items[0]!.status, "failed");
  assert.deepEqual(items[0]!.error, frag(JSON.stringify({ reason: "denied" }, null, 2)));
  assert.deepEqual(items.map((item) => item.detail), [
    { kind: "shell", fields: { command: frag("ls"), cwd: frag("/r"), output: frag("x"), exitCode: null } },
    { kind: "edit", fields: { filePath: frag("a"), unifiedDiff: frag("@@") } },
    { kind: "search", fields: { query: frag("q"), truncated: true, filePaths: frag("a\nb"), webResults: frag("T u") } },
    { kind: "sub_agent", fields: { log: frag("l"), actions: frag("Read: s") } },
    { kind: "unknown", fields: { input: frag(JSON.stringify({ a: 1 }, null, 2)), output: frag("null") } },
    { kind: "unknown", fields: { input: frag(JSON.stringify({ type: "future_kind", value: 1 }, null, 2)) } },
    null,
  ]);
  await bridge.close();
});

test("activity text keeps the 8192-unit bound and a page budget spent newest first", async () => {
  const f = readFixture();
  const long = "y".repeat(10000);
  f.data.page.entries = Array.from({ length: 100 }, (_, i) => ({ provider: "example", timestamp: "now",
    seqStart: i + 1, seqEnd: i + 1, sourceSeqRanges: [{ startSeq: i + 1, endSeq: i + 1 }], collapsed: [],
    item: { type: "tool_call", callId: "c", name: "t", status: "failed", error: long,
      detail: { type: "edit", filePath: "a", oldString: long, newString: long, unifiedDiff: long } } })) as typeof f.data.page.entries;
  const bridge = createBridge(config, () => f.client);
  await bridge.connect();
  const result = await bridge.readTimeline("agent-example");
  assert.equal(result.status, "ok");
  if (result.status !== "ok") return;
  assert.ok(Buffer.byteLength(JSON.stringify(result)) < 3 * 1024 * 1024);
  const newest = result.entries[99]!.item.detail!.fields.unifiedDiff as { value: string; truncated: boolean; originalLength: number };
  assert.deepEqual([newest.value.length, newest.truncated, newest.originalLength], [8192, true, 10000]);
  const oldest = result.entries[0]!.item.detail!.fields.unifiedDiff as { value: string; truncated: boolean; originalLength: number };
  // Older entries get what is left once the budget runs out, still flagged truncated.
  assert.deepEqual([oldest.value.length < 100, oldest.truncated, oldest.originalLength], [true, true, 10000]);
  await bridge.close();
});

test("an unknown future timeline item becomes activity without losing source ranges or leaking payload", async () => {
  const f = readFixture();
  f.data.page.entries = [{ provider: "example", timestamp: "now", seqStart: 33, seqEnd: 37,
    sourceSeqRanges: [{ startSeq: 33, endSeq: 34 }, { startSeq: 37, endSeq: 37 }], collapsed: [], turnId: "turn-example",
    item: { type: "future_event", payload: "private-sentinel", text: "private-sentinel" },
  }] as unknown as typeof f.data.page.entries;
  const bridge = createBridge(config, () => f.client);
  await bridge.connect();
  const result = await bridge.readTimeline("agent-example");
  assert.equal(result.status, "ok");
  if (result.status !== "ok") return;
  assert.equal(result.epoch, "epoch-example");
  assert.deepEqual(result.entries[0]?.sourceSeqRanges, [{ startSeq: 33, endSeq: 34 }, { startSeq: 37, endSeq: 37 }]);
  assert.deepEqual(result.entries[0]?.item, { type: "activity", text: null, status: null,
    title: { value: "Activity", truncated: false, originalLength: 8, lengthUnit: "utf16" },
    sourceType: { value: "future_event", truncated: false, originalLength: 12, lengthUnit: "utf16" },
  });
  assert.equal(JSON.stringify(result).includes("private-sentinel"), false);
  await bridge.close();
});

test("expectedCwd mismatch rejects the read before any timeline RPC and does not echo paths", async (t) => {
  const dirs = await scopeDirectories(t);
  const f = readFixture();
  f.data.agent!.cwd = dirs.other;
  const bridge = createBridge(config, () => f.client);
  t.after(async () => { await bridge.close(); });
  await bridge.connect();
  const result = await bridge.readTimeline("agent-example", { expectedCwd: dirs.owned });
  assert.deepEqual(result, { status: "unavailable", code: "SCOPE_MISMATCH" });
  assert.deepEqual(f.queries, []);
  assert.equal(JSON.stringify(result).includes("private-sentinel"), false);
});

test("expectedCwd canonical symlink and dot paths match but never reach SDK options", async (t) => {
  const dirs = await scopeDirectories(t);
  const f = readFixture();
  f.data.agent!.cwd = dirs.alias + "/.";
  const bridge = createBridge(config, () => f.client);
  t.after(async () => { await bridge.close(); });
  await bridge.connect();
  const result = await bridge.readTimeline("agent-example", {
    expectedCwd: dirs.owned + "/../owned-private-sentinel/", projection: "projected", limit: 50,
    direction: "after", cursor: { epoch: "epoch-example", seq: 12 },
  });
  assert.equal(result.status, "ok");
  assert.deepEqual(f.queries[0]?.options, { projection: "projected", limit: 50,
    direction: "after", cursor: { epoch: "epoch-example", seq: 12 } });
});

test("expectedCwd preserves a real trailing-space directory without checking its trimmed sibling", async (t) => {
  const dirs = await scopeDirectories(t);
  const spaced = join(dirs.root, "owned ");
  const trimmed = join(dirs.root, "owned");
  const alias = join(dirs.root, "spaced-alias");
  await fs.mkdir(spaced);
  await fs.mkdir(trimmed);
  await fs.symlink(spaced, alias, "dir");
  const f = readFixture();
  f.data.agent!.cwd = spaced;
  const bridge = createBridge(config, () => f.client);
  t.after(async () => { await bridge.close(); });
  await bridge.connect();
  assert.equal((await bridge.readTimeline("agent-example", { expectedCwd: alias })).status, "ok");
  assert.equal((await bridge.readTimeline("agent-example", { expectedCwd: spaced })).status, "ok");
  assert.deepEqual(f.queries.map(({ options }) => options), [
    { limit: 50, projection: "projected" }, { limit: 50, projection: "projected" },
  ]);
  f.data.agent!.cwd = trimmed;
  assert.deepEqual(await bridge.readTimeline("agent-example", { expectedCwd: spaced }),
    { status: "unavailable", code: "SCOPE_MISMATCH" });
  assert.equal(f.queries.length, 2);
});

test("expectedCwd invalid or missing paths fail closed without a timeline request", async (t) => {
  const dirs = await scopeDirectories(t);
  const file = join(dirs.root, "file-private-sentinel");
  await fs.writeFile(file, "synthetic");
  const f = readFixture();
  f.data.agent!.cwd = dirs.owned;
  const bridge = createBridge(config, () => f.client);
  t.after(async () => { await bridge.close(); });
  await bridge.connect();
  for (const expectedCwd of ["relative", "", null, dirs.owned + "\0", join(dirs.root, "missing-private-sentinel"), file]) {
    const result = await bridge.readTimeline("agent-example", { expectedCwd } as Parameters<typeof bridge.readTimeline>[1]);
    assert.deepEqual(result, { status: "unavailable", code: "INVALID_INPUT" });
    assert.equal(JSON.stringify(result).includes("private-sentinel"), false);
  }
  assert.deepEqual(f.queries, []);
});

test("expectedCwd missing agent remains missing; unusable actual cwd is READ_FAILED", async (t) => {
  const dirs = await scopeDirectories(t);
  const f = readFixture();
  const agent = f.data.agent!;
  f.data.agent = null;
  const bridge = createBridge(config, () => f.client);
  t.after(async () => { await bridge.close(); });
  await bridge.connect();
  assert.deepEqual(await bridge.readTimeline("agent-example", { expectedCwd: dirs.owned }),
    { status: "missing", code: "AGENT_NOT_FOUND" });
  f.data.agent = agent;
  for (const cwd of ["relative", "", join(dirs.root, "missing-private-sentinel")]) {
    agent.cwd = cwd;
    assert.deepEqual(await bridge.readTimeline("agent-example", { expectedCwd: dirs.owned }),
      { status: "unavailable", code: "READ_FAILED" });
  }
  assert.deepEqual(f.queries, []);
});

test("expectedCwd canonicalization exceptions are fixed codes without raw errors", async (t) => {
  const dirs = await scopeDirectories(t);
  const f = readFixture();
  f.data.agent!.cwd = dirs.other;
  const bridge = createBridge(config, () => f.client);
  t.after(async () => { await bridge.close(); });
  await bridge.connect();
  const realpath = fs.realpath;
  let failedPath = dirs.owned;
  t.mock.method(fs, "realpath", async (path: string) => {
    if (path === failedPath) throw Error("permission-private-sentinel");
    return realpath(path);
  });
  assert.deepEqual(await bridge.readTimeline("agent-example", { expectedCwd: dirs.owned }),
    { status: "unavailable", code: "INVALID_INPUT" });
  failedPath = dirs.other;
  assert.deepEqual(await bridge.readTimeline("agent-example", { expectedCwd: dirs.owned }),
    { status: "unavailable", code: "READ_FAILED" });
  failedPath = "";
  const stat = fs.stat;
  t.mock.method(fs, "stat", async (path: string) => {
    if (path === dirs.other) throw Error("stat-private-sentinel");
    return stat(path);
  });
  assert.deepEqual(await bridge.readTimeline("agent-example", { expectedCwd: dirs.owned }),
    { status: "unavailable", code: "READ_FAILED" });
  assert.deepEqual(f.queries, []);
});

test("expectedCwd close during canonicalization prevents a late timeline RPC", async (t) => {
  const dirs = await scopeDirectories(t);
  const f = readFixture();
  f.data.agent!.cwd = dirs.owned;
  const bridge = createBridge(config, () => f.client);
  t.after(async () => { await bridge.close(); });
  await bridge.connect();
  const directoryStat = await fs.stat(dirs.owned);
  // Keep late completion entirely in the controlled promise/microtask queue.
  t.mock.method(fs, "stat", async () => directoryStat);
  const realpath = fs.realpath;
  let finish!: (value: string) => void;
  let entered!: () => void;
  const checking = new Promise<void>((resolve) => { entered = resolve; });
  const blocked = new Promise<string>((resolve) => { finish = resolve; });
  t.mock.method(fs, "realpath", (path: string) => {
    if (path === dirs.owned) { entered(); return blocked; }
    return realpath(path);
  });
  const pending = bridge.readTimeline("agent-example", { expectedCwd: dirs.owned });
  // Before implementation, INVALID_INPUT settles without ever resolving checking.
  const first = await Promise.race([checking.then(() => "checking"), pending.then(() => "finished")]);
  assert.equal(first, "checking");
  assert.deepEqual(await bridge.close(), { status: "ok" });
  assert.deepEqual(await pending, { status: "unavailable", code: "CLOSED" });
  finish(dirs.owned);
  // A completion marker after canonicalization establishes that late work ran.
  await new Promise<void>((resolve) => setImmediate(resolve));
  assert.deepEqual(f.queries, []);
});
