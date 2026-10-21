import type { PaseoAgent } from "@getpaseo/client";
import type { DirectoryClient } from "../dist/bridge.js";
import type { SdkTimelinePage, TimelineReadOptions } from "../dist/types.js";

export function readFixture() {
  let connected = false;
  let released = false;
  const snapshot: PaseoAgent = {
    id: "agent-example", cwd: "/example/repo", workspaceId: "workspace-example",
    archivedAt: null, status: "idle", title: "Example investigation", provider: "example", model: "model-example",
    createdAt: "2026-10-11T00:00:00Z", updatedAt: "2026-10-11T00:00:00Z", lastUserMessageAt: null,
    activeTurn: { turnId: "turn-example", startedAt: null },
    capabilities: { supportsStreaming: true, supportsSessionPersistence: true, supportsDynamicModes: false,
      supportsMcpServers: false, supportsReasoningStream: true, supportsToolInvocations: true },
    currentModeId: null, availableModes: [], pendingPermissions: [], persistence: null, labels: {},
  };
  Object.assign(snapshot, { config: { env: { SECRET: "private-sentinel" } }, lastError: "private-sentinel" });
  const data: { agent: PaseoAgent | null; page: SdkTimelinePage } = {
    agent: snapshot,
    page: { requestId: "request-example", agentId: "agent-example", agent: snapshot,
      direction: "before", projection: "projected", epoch: "epoch-example", reset: false, staleCursor: false, gap: false,
      window: { minSeq: 10, maxSeq: 90, nextSeq: 91 }, startCursor: null, endCursor: null,
      hasOlder: false, hasNewer: false, entries: [], error: null },
  };
  const references: string[] = [];
  const archived: string[] = [];
  const queries: { id: string; options?: TimelineReadOptions }[] = [];
  const handle: ReturnType<DirectoryClient["agents"]["ref"]> = {
    refresh: async () => data.agent ? { agent: data.agent, project: null } : null,
    send: async () => { throw Error("unexpected send"); },
    archive: async () => { archived.push("agent-example"); data.agent = data.agent && { ...data.agent, archivedAt: "2026-10-21T00:00:00Z" }; return { archivedAt: "2026-10-21T00:00:00Z" }; },
    timeline: { refetch: async (options) => { queries.push({ id: "agent-example", options }); return data.page; } },
  };
  const client: DirectoryClient = {
    connect: async () => { connected = true; },
    close: async () => { released = true; connected = false; },
    getConnectionState: () => ({ status: released ? "disposed" : connected ? "connected" : "idle" }),
    agents: { ref: (id) => { references.push(id); return handle; } },
    providers: { snapshot: async () => ({ entries: [], generatedAt: "now", requestId: "r" }),
      listModels: async (provider) => ({ provider, models: [], fetchedAt: "now", requestId: "r" }) },
    workspaces: { ref: () => { throw Error("unexpected workspace read"); },
      list: async () => ({ entries: [], requestId: "r", pageInfo: { nextCursor: null, prevCursor: null, hasMore: false } }) },
  };
  return { data, client, handle, references, queries, archived, released: () => released };
}
