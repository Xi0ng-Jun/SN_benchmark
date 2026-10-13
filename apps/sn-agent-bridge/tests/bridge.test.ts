import assert from "node:assert/strict";
import test from "node:test";
import type { PaseoClientConfig, PaseoProviderModelsResult, PaseoProviderSnapshotResult } from "@getpaseo/client";
import { createBridge, type DirectoryClient } from "../dist/bridge.js";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function fixture() {
  let connected = false;
  let disposed = false;
  let closeCalls = 0;
  let snapshot: PaseoProviderSnapshotResult = { entries: [], generatedAt: "2026-10-11T00:00:00Z", requestId: "r1" };
  let models: PaseoProviderModelsResult = { provider: "codex", models: [], fetchedAt: "2026-10-11T00:00:00Z", requestId: "r2" };
  const client: DirectoryClient = {
    agents: { ref: () => { throw Error("unexpected agent read"); } },
    connect: async () => { if (disposed) throw Error("disposed"); connected = true; },
    close: async () => { closeCalls++; disposed = true; connected = false; },
    getConnectionState: () => ({ status: disposed ? "disposed" : connected ? "connected" : "idle" }),
    providers: { snapshot: async () => snapshot, listModels: async () => models },
    workspaces: { ref: () => { throw Error("unexpected workspace read"); }, list: async (options) => {
      assert.equal(options?.subscribe, undefined);
      assert.deepEqual(options?.page, { cursor: "page-2", limit: 10 });
      return { entries: [], requestId: "r3", pageInfo: { nextCursor: "page-3", prevCursor: "page-1", hasMore: true } };
    } },
  };
  return { client, setSnapshot: (value: PaseoProviderSnapshotResult) => { snapshot = value; },
    setModels: (value: PaseoProviderModelsResult) => { models = value; },
    disconnect: () => { connected = false; }, isDisposed: () => disposed, closeCalls: () => closeCalls };
}

const config = { url: "ws://127.0.0.1:6767/ws", password: "test-secret" };

test("unconnected reads differ from successful empty directories; workspace pagination survives", async () => {
  const f = fixture();
  const bridge = createBridge(config, () => f.client);
  assert.deepEqual(await bridge.listProviders(), { status: "unavailable", code: "NOT_CONNECTED" });
  assert.deepEqual(await bridge.connect(), { status: "ok" });
  assert.deepEqual(await bridge.listProviders(), { status: "ok", entries: [], generatedAt: "2026-10-11T00:00:00Z" });
  assert.deepEqual(await bridge.listModels("codex"), { status: "ok", entries: [], provider: "codex", fetchedAt: "2026-10-11T00:00:00Z" });
  assert.deepEqual(await bridge.listWorkspaces({ cursor: "page-2", limit: 10 }), {
    status: "ok", entries: [], pageInfo: { nextCursor: "page-3", prevCursor: "page-1", hasMore: true },
  });
  await bridge.close();
  assert.equal(f.isDisposed(), true);
});

test("failed connect releases its resource, redacts exceptions, and permits an explicit retry", async () => {
  const bad = fixture();
  bad.client.connect = async () => { throw Error("password=test-secret private-host"); };
  const good = fixture();
  const clients = [bad.client, good.client];
  const bridge = createBridge(config, () => clients.shift()!);
  assert.deepEqual(await bridge.connect(), { status: "unavailable", code: "CONNECT_FAILED" });
  assert.equal(bad.isDisposed(), true);
  assert.deepEqual(await bridge.connect(), { status: "ok" });
  await bridge.close();
  assert.equal(good.isDisposed(), true);
});

test("SDK options disable retries and raw logging; provider errors stay out of the projection", async () => {
  const f = fixture();
  let sdkConfig!: PaseoClientConfig;
  const bridge = createBridge(config, (options) => { sdkConfig = options; return f.client; });
  await bridge.connect();
  assert.equal(sdkConfig.password, "test-secret");
  assert.deepEqual(sdkConfig.reconnect, { enabled: false });
  assert.ok(sdkConfig.logger);
  f.setSnapshot({ generatedAt: "2026-10-11T00:00:00Z", requestId: "r1", entries: [
    { provider: "codex", status: "error", enabled: true, error: "test-secret private-host", label: "Codex" },
    { provider: "pi", status: "loading", enabled: true },
  ] });
  const result = await bridge.listProviders();
  assert.deepEqual(result, { status: "ok", generatedAt: "2026-10-11T00:00:00Z", entries: [
    { id: "codex", status: "error", enabled: true, label: "Codex", fetchedAt: null },
    { id: "pi", status: "loading", enabled: true, label: null, fetchedAt: null },
  ] });
  assert.equal(JSON.stringify(result).includes("test-secret"), false);
  await bridge.close();
});

test("model error and absent models are failures; read exceptions are sanitized", async () => {
  const f = fixture();
  const bridge = createBridge(config, () => f.client);
  await bridge.connect();
  f.setModels({ provider: "codex", error: "test-secret", fetchedAt: "now", requestId: "r2" });
  assert.deepEqual(await bridge.listModels("codex"), { status: "unavailable", code: "CATALOG_UNAVAILABLE" });
  f.setModels({ provider: "codex", fetchedAt: "now", requestId: "r2" });
  assert.deepEqual(await bridge.listModels("codex"), { status: "unavailable", code: "CATALOG_UNAVAILABLE" });
  f.client.providers.snapshot = async () => { throw Error("test-secret"); };
  assert.deepEqual(await bridge.listProviders(), { status: "unavailable", code: "READ_FAILED" });
  f.disconnect();
  assert.deepEqual(await bridge.listProviders(), { status: "unavailable", code: "CONNECTION_UNAVAILABLE" });
  await bridge.close();
});

test("close cancels a pending handshake even when the SDK promise never settles", async () => {
  const f = fixture();
  const handshake = deferred<void>();
  f.client.connect = () => handshake.promise;
  const bridge = createBridge(config, () => f.client);
  const first = bridge.connect();
  const second = bridge.connect();
  assert.deepEqual(await bridge.close(), { status: "ok" });
  assert.deepEqual(await first, { status: "unavailable", code: "CLOSED" });
  assert.deepEqual(await second, { status: "unavailable", code: "CLOSED" });
  assert.deepEqual(await bridge.close(), { status: "ok" });
  assert.equal(f.closeCalls(), 1);
  assert.equal(f.isDisposed(), true);
  assert.deepEqual(await bridge.connect(), { status: "unavailable", code: "CLOSED" });
});

test("closing an in-flight read settles promptly and drops its late result", async () => {
  const f = fixture();
  const pending = deferred<PaseoProviderSnapshotResult>();
  f.client.providers.snapshot = () => pending.promise;
  const bridge = createBridge(config, () => f.client);
  await bridge.connect();
  const read = bridge.listProviders();
  await bridge.close();
  assert.deepEqual(await read, { status: "unavailable", code: "CLOSED" });
  pending.resolve({ entries: [], generatedAt: "late", requestId: "r" });
  assert.deepEqual(await bridge.listProviders(), { status: "unavailable", code: "CLOSED" });
});

test("failed release reports a fixed code instead of hiding cleanup failure", async () => {
  const f = fixture();
  f.client.close = async () => { throw Error("test-secret"); };
  const bridge = createBridge(config, () => f.client);
  await bridge.connect();
  assert.deepEqual(await bridge.close(), { status: "unavailable", code: "CLOSE_FAILED" });
  assert.deepEqual(await bridge.close(), { status: "unavailable", code: "CLOSE_FAILED" });
});

test("model selection capabilities are projected without arbitrary provider metadata", async () => {
  const f = fixture();
  let observedProvider: unknown;
  let observedOptions: unknown;
  f.client.providers.listModels = async (provider, options) => {
    observedProvider = provider;
    observedOptions = options;
    return { provider: "codex", fetchedAt: "now", requestId: "r2", models: [{
      provider: "codex", id: "example/model", label: "Example", isDefault: false,
      isSelectable: true, contextWindowMaxTokens: 1000,
      thinkingOptions: [{ id: "high", label: "High", metadata: { token: "test-secret" } }],
      defaultThinkingOptionId: "high", metadata: { token: "test-secret" },
    }] };
  };
  const bridge = createBridge(config, () => f.client);
  await bridge.connect();
  const result = await bridge.listModels("codex", { cwd: "/server/repo" });
  assert.equal(observedProvider, "codex");
  assert.deepEqual(observedOptions, { cwd: "/server/repo" });
  assert.deepEqual(result, { status: "ok", provider: "codex", fetchedAt: "now", entries: [{
    id: "example/model", label: "Example", isDefault: false, isSelectable: true,
    contextWindowMaxTokens: 1000, thinkingOptions: [{ id: "high", label: "High" }],
    defaultThinkingOptionId: "high",
  }] });
  await bridge.close();
});

test("workspace projection keeps identity and default pagination without private runtime details", async () => {
  const f = fixture();
  f.client.workspaces.list = async (options) => {
    assert.equal(options?.subscribe, undefined);
    assert.deepEqual(options?.page, { limit: 50, cursor: undefined });
    return { requestId: "r3", entries: [{
      id: "wks-example", projectId: "prj-example", projectDisplayName: "Example",
      projectRootPath: "/server/repo", workspaceDirectory: "/server/repo/worktree",
      projectKind: "git", workspaceKind: "worktree", name: "Review", status: "running",
      archivingAt: null, statusEnteredAt: null, activityAt: null, scripts: [],
      gitRuntime: null, githubRuntime: { error: { message: "test-secret" } },
    }], pageInfo: { nextCursor: null, prevCursor: null, hasMore: false } };
  };
  const bridge = createBridge(config, () => f.client);
  await bridge.connect();
  assert.deepEqual(await bridge.listWorkspaces(), { status: "ok", entries: [{
    id: "wks-example", projectId: "prj-example", name: "Review", status: "running",
    directory: "/server/repo/worktree",
  }], pageInfo: { nextCursor: null, prevCursor: null, hasMore: false } });
  await bridge.close();
});

test("reconnect disposes the disconnected instance and drops its pending directory response", async () => {
  const first = fixture();
  const second = fixture();
  const clients = [first.client, second.client];
  const pending = deferred<PaseoProviderSnapshotResult>();
  first.client.providers.snapshot = () => pending.promise;
  const bridge = createBridge(config, () => clients.shift()!);
  await bridge.connect();
  const read = bridge.listProviders();
  first.disconnect();
  assert.deepEqual(await bridge.connect(), { status: "ok" });
  assert.equal(first.isDisposed(), true);
  pending.resolve({ entries: [], generatedAt: "old", requestId: "r1" });
  assert.deepEqual(await read, { status: "unavailable", code: "CONNECTION_UNAVAILABLE" });
  assert.equal((await bridge.listProviders()).status, "ok");
  await bridge.close();
  assert.equal(second.isDisposed(), true);
});

test("invalid connection configuration never reaches the SDK factory", async () => {
  for (const badConfig of [
    { url: "http://host/ws" }, { url: "ws://user:secret@host/ws" },
    { url: "ws://host/api" }, { url: "not a URL" },
    { url: config.url, connectTimeoutMs: 0 },
  ]) {
    const bridge = createBridge(badConfig, () => { assert.fail("factory called"); });
    assert.deepEqual(await bridge.connect(), { status: "unavailable", code: "INVALID_CONFIG" });
    assert.deepEqual(await bridge.close(), { status: "ok" });
  }
});

test("unconfirmed release after a rejected handshake blocks retry and remains a close failure", async () => {
  const resource = fixture();
  let creations = 0;
  resource.client.connect = async () => { throw Error("handshake rejected"); };
  resource.client.close = async () => { throw Error("release rejected"); };
  // A failed release gives no permission to trust a still-connected SDK state.
  resource.client.getConnectionState = () => ({ status: "connected" });
  const bridge = createBridge(config, () => { creations++; return resource.client; });
  assert.deepEqual(await bridge.connect(), { status: "unavailable", code: "CLOSE_FAILED" });
  assert.deepEqual(await bridge.listProviders(), { status: "unavailable", code: "CLOSE_FAILED" });
  // Both results must preserve the original unconfirmed resource; neither may
  // report success or construct another client while the old one is unresolved.
  const retry = await bridge.connect();
  const closing = await bridge.close();
  assert.deepEqual({ creations, retry, closing }, {
    creations: 1,
    retry: { status: "unavailable", code: "CLOSE_FAILED" },
    closing: { status: "unavailable", code: "CLOSE_FAILED" },
  });
  assert.deepEqual(await bridge.close(), { status: "unavailable", code: "CLOSE_FAILED" });
});

for (const boundary of ["open", "outer connect"] as const) {
  test(`a close microtask before ${boundary} continuation cannot publish connected`, async () => {
    const f = fixture();
    const handshake = deferred<void>();
    const order: string[] = [];
    f.client.connect = () => handshake.promise;
    f.client.getConnectionState = () => {
      order.push(f.isDisposed() ? "sdk.disposed" : "sdk.connected");
      return { status: f.isDisposed() ? "disposed" : "connected" };
    };
    const release = f.client.close;
    f.client.close = async () => { order.push("sdk.close"); await release(); };
    const bridge = createBridge(config, () => f.client);
    const closed = deferred<Awaited<ReturnType<typeof bridge.close>>>();
    const connecting = bridge.connect();
    handshake.resolve();
    // Promise.race reaction -> untilClosed continuation -> open continuation.
    // Two explicit queueMicrotask steps place close after untilClosed has checked
    // its flag and before open resumes. A third step places it after open's
    // success but before outer connect resumes. SDK release is still queued.
    queueMicrotask(() => {
      queueMicrotask(() => {
        const closeAtBoundary = () => {
          order.push("bridge.close");
          const closing = bridge.close();
          assert.equal(f.isDisposed(), false, "SDK release must still be queued");
          closing.then(closed.resolve, closed.reject);
        };
        if (boundary === "open") closeAtBoundary();
        else queueMicrotask(closeAtBoundary);
      });
    });
    const result = await connecting;
    assert.deepEqual(await closed.promise, { status: "ok" });
    assert.equal(order[0], boundary === "open" ? "bridge.close" : "sdk.connected");
    assert.ok(order.includes("sdk.close"));
    assert.deepEqual(result, { status: "unavailable", code: "CLOSED" }, order.join(" -> "));
  });
}

test("retaining a releasing instance cannot let a read publish success after close", async () => {
  const f = fixture();
  const response = deferred<PaseoProviderSnapshotResult>();
  f.client.providers.snapshot = () => response.promise;
  const bridge = createBridge(config, () => f.client);
  await bridge.connect();
  const closing = deferred<Awaited<ReturnType<typeof bridge.close>>>();
  const reading = bridge.listProviders();
  response.resolve({ entries: [], generatedAt: "late", requestId: "r" });
  // Projection continuation -> Promise.race -> untilClosed -> read publication.
  // close runs after untilClosed and before read, while the SDK is connected.
  queueMicrotask(() => queueMicrotask(() => queueMicrotask(() => {
    const result = bridge.close();
    assert.equal(f.isDisposed(), false);
    result.then(closing.resolve, closing.reject);
  })));
  const result = await reading;
  assert.deepEqual(await closing.promise, { status: "ok" });
  assert.deepEqual(result, { status: "unavailable", code: "CLOSED" });
});

test("a pending read rejection after a failed reconnect release prioritizes CLOSE_FAILED", async () => {
  const f = fixture();
  const response = deferred<PaseoProviderSnapshotResult>();
  f.client.providers.snapshot = () => response.promise;
  f.client.close = async () => { throw Error("release rejected"); };
  const bridge = createBridge(config, () => f.client);
  await bridge.connect();
  const reading = bridge.listProviders();
  f.disconnect();
  assert.deepEqual(await bridge.connect(), { status: "unavailable", code: "CLOSE_FAILED" });
  response.reject(Error("private read failure"));
  const result = await reading;
  assert.deepEqual(await bridge.close(), { status: "unavailable", code: "CLOSE_FAILED" });
  assert.deepEqual(result, { status: "unavailable", code: "CLOSE_FAILED" });
});
