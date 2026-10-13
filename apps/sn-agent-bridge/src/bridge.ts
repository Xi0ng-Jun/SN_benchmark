import type { PaseoAgentHandle, PaseoClient, PaseoClientConfig, PaseoWorkspaceHandle, PaseoWorkspaceAgentCreateOptions, PaseoWorkspaceListOptions, PaseoWorkspaceListResult } from "@getpaseo/client";
import type { AgentBridge, AgentBridgeConfig, BridgeErrorCode, BridgeResult, CreateAgentOptions, CreateAgentResult, CreatePrecheckCode, SendAgentOptions, SendAgentResult, SendPrecheckCode } from "./types.js";
import { agentId, creationId, createOptions, InvalidInputError, sendOptions, timelineOptions } from "./input.js";
import { exactAgent, projectAgent, projectTimeline, ReadFault } from "./read-projections.js";
import fs from "node:fs/promises";
import { isAbsolute } from "node:path";

// Internal injection boundary for offline tests; not exported from the package root.
export type DirectoryClient = Pick<PaseoClient, "connect" | "close" | "getConnectionState"> & {
  agents: {
    ref(agentId: string): Pick<PaseoAgentHandle, "refresh" | "send"> & {
      timeline: Pick<PaseoAgentHandle["timeline"], "refetch">;
    };
  };
  providers: Pick<PaseoClient["providers"], "snapshot" | "listModels">;
  workspaces: {
    ref(id: string): Pick<PaseoWorkspaceHandle, "refresh"> & {
      agents: { create(options: PaseoWorkspaceAgentCreateOptions): Promise<Pick<PaseoAgentHandle, "id" | "refresh">> };
    };
    list(options?: PaseoWorkspaceListOptions & { subscribe?: never }): Promise<PaseoWorkspaceListResult>;
  };
};

const closedSignal = Symbol("closed");
const catalogUnavailable = Symbol("catalogUnavailable");
const silentLogger = { debug() {}, info() {}, warn() {}, error() {} };
const unavailable = (code: BridgeErrorCode): BridgeResult<never> => ({ status: "unavailable", code });

async function canonicalDirectory(path: string, code: "INVALID_INPUT" | "READ_FAILED"): Promise<string> {
  try {
    if (typeof path !== "string" || !isAbsolute(path) || path.includes("\0")) throw new Error();
    const canonical = await fs.realpath(path);
    if (!(await fs.stat(canonical)).isDirectory()) throw new Error();
    return canonical;
  } catch { throw new ReadFault(code); } // Never propagate filesystem errors or paths.
}

export function createBridge(
  config: AgentBridgeConfig,
  factory: (config: PaseoClientConfig) => DirectoryClient,
): AgentBridge {
  // Copy only supported server fields. Caller mutation and extra logger/reconnect keys
  // must not change this instance's credential or lifecycle behavior.
  const sdkConfig: PaseoClientConfig = {
    url: config.url,
    password: config.password,
    authHeader: config.authHeader,
    connectTimeoutMs: config.connectTimeoutMs ?? 10_000,
    reconnect: { enabled: false },
    logger: silentLogger,
  };
  let validConfig = false;
  try {
    const url = new URL(sdkConfig.url);
    validConfig = ["ws:", "wss:"].includes(url.protocol) && url.pathname.endsWith("/ws")
      && !url.username && !url.password
      && Number.isFinite(sdkConfig.connectTimeoutMs) && sdkConfig.connectTimeoutMs! > 0;
  } catch { /* Fixed error code; URL parsing errors can contain secrets. */ }

  let closed = false;
  let current: DirectoryClient | undefined;
  let connecting: Promise<BridgeResult> | undefined;
  let closing: Promise<BridgeResult> | undefined;
  // A rejected SDK close does not prove disposal. Keep both the resource and
  // the failure fact; this bridge cannot safely create another client afterward.
  let releaseFailed = false;
  const lifetime = new AbortController();
  const releases = new WeakMap<DirectoryClient, Promise<boolean>>();

  function release(client: DirectoryClient): Promise<boolean> {
    let pending = releases.get(client);
    if (!pending) {
      pending = Promise.resolve().then(() => client.close()).then(() => true, () => {
        releaseFailed = true;
        return false;
      });
      releases.set(client, pending);
    }
    return pending;
  }

  async function untilClosed<T>(operation: Promise<T>): Promise<T> {
    let onClose!: () => void;
    const canceled = new Promise<never>((_, reject) => {
      onClose = () => reject(closedSignal);
      lifetime.signal.addEventListener("abort", onClose, { once: true });
      if (closed) onClose();
    });
    try {
      const result = await Promise.race([operation, canceled]);
      if (closed) throw closedSignal;
      return result;
    } finally {
      lifetime.signal.removeEventListener("abort", onClose);
    }
  }

  function connect(): Promise<BridgeResult> {
    if (closed) return Promise.resolve(unavailable("CLOSED"));
    if (!validConfig) return Promise.resolve(unavailable("INVALID_CONFIG"));
    if (releaseFailed) return Promise.resolve(unavailable("CLOSE_FAILED"));
    if (connecting) return connecting;
    if (current?.getConnectionState().status === "connected") return Promise.resolve({ status: "ok" });
    // This is the final shared publication promise, with no return-await/finally
    // continuation after the check. open's success belongs to one SDK instance.
    connecting = open().then((result): BridgeResult => {
      connecting = undefined;
      if (closed) return unavailable("CLOSED");
      if (releaseFailed) return unavailable("CLOSE_FAILED");
      if (result.status !== "ok") return result;
      if (current !== result.client || result.client.getConnectionState().status !== "connected")
        return unavailable("CONNECTION_UNAVAILABLE");
      return { status: "ok" };
    });
    return connecting;
  }

  async function open(): Promise<BridgeResult<{ client: DirectoryClient }>> {
    let client: DirectoryClient | undefined;
    try {
      if (current) {
        const previous = current;
        if (!await untilClosed(release(previous))) return unavailable("CLOSE_FAILED");
        current = undefined;
      }
      if (closed) return unavailable("CLOSED");
      client = factory(sdkConfig);
      current = client;
      await untilClosed(client.connect());
      if (closed || current !== client) throw closedSignal;
      if (client.getConnectionState().status !== "connected") throw Error("not connected");
      return { status: "ok", client };
    } catch {
      const released = client ? await release(client) : true;
      if (released && current === client) current = undefined;
      return unavailable(closed ? "CLOSED" : released ? "CONNECT_FAILED" : "CLOSE_FAILED");
    }
  }

  function close(): Promise<BridgeResult> {
    if (closing) return closing;
    closed = true;
    lifetime.abort();
    const client = current;
    closing = (client ? release(client) : Promise.resolve(true))
      .then((released) => {
        if (released && current === client) current = undefined;
        return released && !releaseFailed ? { status: "ok" } : unavailable("CLOSE_FAILED");
      });
    return closing;
  }

  async function read<T>(operation: (client: DirectoryClient) => Promise<T>): Promise<BridgeResult<T>> {
    if (closed) return unavailable("CLOSED");
    if (releaseFailed) return unavailable("CLOSE_FAILED");
    const client = current;
    if (!client) return unavailable("NOT_CONNECTED");
    if (client.getConnectionState().status !== "connected") return unavailable("CONNECTION_UNAVAILABLE");
    try {
      const result = await untilClosed(operation(client));
      if (closed) return unavailable("CLOSED");
      if (releaseFailed) return unavailable("CLOSE_FAILED");
      // A disconnected/replaced client cannot publish a stale success.
      if (current !== client || client.getConnectionState().status !== "connected")
        return unavailable("CONNECTION_UNAVAILABLE");
      return { status: "ok", ...result };
    } catch (error) {
      if (closed) return unavailable("CLOSED");
      if (releaseFailed) return unavailable("CLOSE_FAILED");
      if (current !== client || client.getConnectionState().status !== "connected") return unavailable("CONNECTION_UNAVAILABLE");
      if (error instanceof InvalidInputError) return unavailable("INVALID_INPUT");
      if (error instanceof ReadFault) return error.code === "AGENT_NOT_FOUND"
        ? { status: "missing", code: "AGENT_NOT_FOUND" } : unavailable(error.code);
      return unavailable(error === catalogUnavailable ? "CATALOG_UNAVAILABLE" : "READ_FAILED");
    }
  }

  function lifecycleCode(client?: DirectoryClient): BridgeErrorCode | undefined {
    if (closed) return "CLOSED";
    if (releaseFailed) return "CLOSE_FAILED";
    if (!client) return "NOT_CONNECTED";
    if (current !== client || releases.has(client) || client.getConnectionState().status !== "connected")
      return "CONNECTION_UNAVAILABLE";
  }

  async function untilCreateSettled<T>(client: DirectoryClient, operation: Promise<T>): Promise<T> {
    let timer: ReturnType<typeof setInterval> | undefined;
    // 0.11.2 retains a creation promise on connection loss for recovery even
    // when reconnect is disabled. Its public root has only a state getter.
    // This local check belongs solely to this SDK create await, not an RPC or
    // request deadline; the losing operation remains handled by Promise.race.
    const disconnected = new Promise<never>((_, reject) => {
      timer = setInterval(() => {
        try {
          const code = lifecycleCode(client);
          if (code) reject(new ReadFault(code));
        } catch { reject(new ReadFault("CONNECTION_UNAVAILABLE")); }
      }, 100);
    });
    try {
      return await untilClosed(Promise.race([operation, disconnected]));
    } finally {
      if (timer !== undefined) clearInterval(timer);
    }
  }

  async function createAgent(input: CreateAgentOptions): Promise<CreateAgentResult> {
    let options: CreateAgentOptions;
    try { options = createOptions(input); }
    catch { return { status: "not_attempted", code: "INVALID_INPUT" }; }
    const client = current;
    let started = false;
    let observed: Extract<CreateAgentResult, { status: "unknown" }>["observed"];
    const assertLive = () => { const code = lifecycleCode(client); if (code) throw new ReadFault(code); };
    const notAttempted = (code: CreatePrecheckCode): CreateAgentResult => ({ status: "not_attempted", code, agentId: options.agentId });
    try {
      assertLive();
      const workspaceHandle = client!.workspaces.ref(options.workspaceId);
      const workspace = await untilClosed(workspaceHandle.refresh());
      assertLive();
      if (!workspace) return notAttempted("WORKSPACE_NOT_FOUND");
      if (workspace.id !== options.workspaceId) return notAttempted("IDENTITY_MISMATCH");
      // These states aggregate workspace activity, not provider readiness. A
      // known bucket plus no archive operation and a real directory is usable.
      if (!["needs_input", "failed", "running", "attention", "done"].includes(workspace.status)
        || workspace.archivingAt !== null || !workspace.workspaceDirectory)
        return notAttempted("WORKSPACE_UNAVAILABLE");
      const expected = await untilClosed(canonicalDirectory(options.expectedCwd, "INVALID_INPUT"));
      assertLive();
      const directory = await untilClosed(canonicalDirectory(workspace.workspaceDirectory, "READ_FAILED"));
      assertLive();
      if (directory !== expected) return notAttempted("SCOPE_MISMATCH");
      const existing = await untilClosed(client!.agents.ref(options.agentId).refresh());
      assertLive();
      if (existing !== null) {
        exactAgent(existing?.agent ?? null, options.agentId);
        return notAttempted("AGENT_ALREADY_EXISTS");
      }
      // No await between this final ownership check and the single invocation.
      assertLive();
      started = true;
      const creating = workspaceHandle.agents.create({ agentId: options.agentId,
        idempotencyKey: options.idempotencyKey, config: options.config,
        ...(options.title === undefined ? {} : { title: options.title }) });
      const handle = await untilCreateSettled(client!, creating.then((handle) => {
        // Retain only a bounded complete UUID, never arbitrary returned text.
        try { observed = { handleId: creationId(handle.id) }; } catch { /* no safe ID observed */ }
        return handle;
      }));
      assertLive();
      if (handle.id !== options.agentId) throw new ReadFault("IDENTITY_MISMATCH");
      const agent = exactAgent((await untilClosed(handle.refresh()))?.agent ?? null, options.agentId);
      assertLive();
      if (agent.workspaceId !== options.workspaceId) throw new ReadFault("IDENTITY_MISMATCH");
      const slash = options.config.provider.indexOf("/");
      if (agent.provider !== options.config.provider.slice(0, slash) || agent.model !== options.config.provider.slice(slash + 1))
        throw new ReadFault("IDENTITY_MISMATCH");
      const actual = await untilClosed(canonicalDirectory(agent.cwd, "READ_FAILED"));
      assertLive();
      if (actual !== expected) throw new ReadFault("SCOPE_MISMATCH");
      const snapshot = projectAgent(agent);
      observed = { handleId: options.agentId, agent: snapshot };
      const readiness = agent.archivedAt === undefined || agent.activeTurn === undefined || agent.status === undefined
        ? "incomplete_snapshot" : agent.archivedAt !== null ? "archived"
          : agent.status !== "idle" || agent.activeTurn !== null ? "not_idle" : "idle";
      // Do not claim effective mode/effort, or atomic future idleness.
      assertLive();
      return { status: "created", agentId: options.agentId, agent: snapshot, ready: readiness === "idle", readiness };
    } catch (error) {
      const code = lifecycleCode(client) ?? (error instanceof ReadFault ? error.code : "CREATE_FAILED");
      if (!started) return notAttempted(code === "CREATE_FAILED" || code === "AGENT_NOT_FOUND" ? "READ_FAILED" : code);
      return { status: "unknown", code: "CREATE_OUTCOME_UNKNOWN", cause: code, agentId: options.agentId,
        ...(observed === undefined ? {} : { observed }) };
    }
  }

  async function sendAgent(input: SendAgentOptions): Promise<SendAgentResult> {
    let options: SendAgentOptions;
    try { options = sendOptions(input); }
    catch { return { status: "not_attempted", code: "INVALID_INPUT" }; }
    const client = current;
    let started = false;
    let sdkResolved = false;
    let finished = false;
    const assertLive = () => { const code = lifecycleCode(client); if (code) throw new ReadFault(code); };
    const identities = { agentId: options.agentId, messageId: options.messageId };
    const notAttempted = (code: SendPrecheckCode): SendAgentResult => ({ status: "not_attempted", code, ...identities });
    try {
      assertLive();
      const handle = client!.agents.ref(options.agentId);
      const agent = exactAgent((await untilClosed(handle.refresh()))?.agent ?? null, options.agentId);
      assertLive();
      // These are observations, not an atomic authorization/idle lock. Copy the
      // path before filesystem IO; no creation binding or remote workspace is required.
      const actualCwd = agent.cwd;
      if (agent.archivedAt === undefined || agent.status === undefined || !Array.isArray(agent.pendingPermissions)
        || agent.activeTurn === undefined || (agent.activeTurn !== null &&
          (typeof agent.activeTurn !== "object" || typeof agent.activeTurn.turnId !== "string"
            || !agent.activeTurn.turnId.length || !(agent.activeTurn.startedAt === null || typeof agent.activeTurn.startedAt === "string"))))
        return notAttempted("INCOMPLETE_SNAPSHOT");
      if (agent.archivedAt !== null) return notAttempted("AGENT_ARCHIVED");
      if (agent.pendingPermissions.length || agent.attentionReason === "permission") return notAttempted("PERMISSION_REQUIRED");
      if (!["idle", "running"].includes(agent.status) || agent.providerUnavailable === true)
        return notAttempted("AGENT_UNAVAILABLE");
      const expected = await untilClosed(canonicalDirectory(options.expectedCwd, "INVALID_INPUT"));
      assertLive();
      const actual = await untilClosed(canonicalDirectory(actualCwd, "READ_FAILED"));
      assertLive();
      if (actual !== expected) return notAttempted("SCOPE_MISMATCH");
      // No await between the final lifecycle/instance check and this single call.
      assertLive();
      started = true;
      const sending = handle.send(options.text, { messageId: options.messageId, activeTurnBehavior: options.activeTurnBehavior });
      await untilClosed(sending.then(() => { if (!finished) sdkResolved = true; }));
      assertLive();
      return { status: "acknowledged", ...identities };
    } catch (error) {
      const lifecycle = lifecycleCode(client);
      if (!started) return notAttempted(lifecycle ?? (error instanceof ReadFault ? error.code : "READ_FAILED"));
      return { status: "unknown", code: "SEND_OUTCOME_UNKNOWN", cause: lifecycle ?? "SEND_FAILED", ...identities,
        ...(sdkResolved ? { observed: { sdkResolved: true } } : {}) };
    } finally { finished = true; }
  }

  return {
    connect,
    close,
    createAgent,
    sendAgent,
    readAgent: (id) => read(async (client) => {
      const handle = client.agents.ref(agentId(id));
      return { agent: projectAgent(exactAgent((await handle.refresh())?.agent ?? null, id)) };
    }),
    readTimeline: (id, options) => read(async (client) => {
      const { expectedCwd, ...params } = timelineOptions(options);
      const handle = client.agents.ref(agentId(id));
      const agent = exactAgent((await handle.refresh())?.agent ?? null, id);
      // refresh and refetch are separate reads; a late refresh must not start
      // another RPC after this instance has been closed or replaced.
      if (closed) throw closedSignal;
      if (releaseFailed) throw new ReadFault("CLOSE_FAILED");
      if (current !== client || client.getConnectionState().status !== "connected")
        throw new ReadFault("CONNECTION_UNAVAILABLE");
      if (expectedCwd !== undefined) {
        const expected = await canonicalDirectory(expectedCwd, "INVALID_INPUT");
        const actual = await canonicalDirectory(agent.cwd, "READ_FAILED");
        if (actual !== expected) throw new ReadFault("SCOPE_MISMATCH");
        // Local path IO added await boundaries. Closing/replacing this bridge
        // during the check must not start a subsequent SDK timeline read.
        if (closed) throw closedSignal;
        if (releaseFailed) throw new ReadFault("CLOSE_FAILED");
        if (current !== client || client.getConnectionState().status !== "connected")
          throw new ReadFault("CONNECTION_UNAVAILABLE");
      }
      return projectTimeline(await handle.timeline.refetch(params), id);
    }),
    listProviders: (options) => read(async (client) => {
      const result = await client.providers.snapshot(options);
      return {
        generatedAt: result.generatedAt,
        entries: result.entries.map((entry) => ({
          id: entry.provider, status: entry.status, enabled: entry.enabled,
          label: entry.label ?? null, fetchedAt: entry.fetchedAt ?? null,
        })),
      };
    }),
    listModels: (provider, options) => read(async (client) => {
      const result = await client.providers.listModels(provider, options);
      if (result.error || !Array.isArray(result.models)) throw catalogUnavailable;
      return {
        provider: result.provider, fetchedAt: result.fetchedAt,
        entries: result.models.map((model) => ({
          id: model.id, label: model.label,
          isDefault: model.isDefault ?? null, isSelectable: model.isSelectable ?? null,
          contextWindowMaxTokens: model.contextWindowMaxTokens ?? null,
          thinkingOptions: model.thinkingOptions?.map(({ id, label }) => ({ id, label })) ?? null,
          defaultThinkingOptionId: model.defaultThinkingOptionId ?? null,
        })),
      };
    }),
    listWorkspaces: (options) => read(async (client) => {
      const result = await client.workspaces.list({
        page: { limit: options?.limit ?? 50, cursor: options?.cursor },
      });
      return {
        pageInfo: {
          nextCursor: result.pageInfo.nextCursor,
          prevCursor: result.pageInfo.prevCursor,
          hasMore: result.pageInfo.hasMore,
        },
        entries: result.entries.map((entry) => ({
          id: entry.id, projectId: entry.projectId, name: entry.name,
          status: entry.status, directory: entry.workspaceDirectory ?? null,
        })),
      };
    }),
  };
}
