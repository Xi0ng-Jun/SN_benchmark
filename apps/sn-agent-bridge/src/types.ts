import type { PaseoAgent, PaseoAgentHandle, PaseoAgentProvider, PaseoAgentTimelineRefetchOptions, PaseoProviderSnapshotResult, PaseoWorkspace } from "@getpaseo/client";

/** Server-owned configuration. Never serialize this object into a browser response. */
export interface AgentBridgeConfig {
  url: string;
  password?: string;
  authHeader?: string;
  connectTimeoutMs?: number;
}

export type ProviderId = PaseoAgentProvider;
export type BridgeErrorCode = "INVALID_CONFIG" | "NOT_CONNECTED" | "CONNECTION_UNAVAILABLE"
  | "CONNECT_FAILED" | "READ_FAILED" | "CATALOG_UNAVAILABLE" | "CLOSED" | "CLOSE_FAILED"
  | "INVALID_INPUT" | "IDENTITY_MISMATCH" | "SCOPE_MISMATCH" | "RESPONSE_TOO_LARGE" | "INTERNAL_ERROR";
export type BridgeResult<T = Record<never, never>> = ({ status: "ok" } & T)
  | { status: "unavailable"; code: BridgeErrorCode }
  | { status: "missing"; code: "AGENT_NOT_FOUND" };

/** Frozen and authorized by the server; this whitelist does not authorize a Task. */
export interface CreateAgentOptions {
  workspaceId: string;
  expectedCwd: string;
  agentId: string;
  idempotencyKey: string;
  config: { provider: string; modeId?: string; thinkingOptionId?: string };
  title?: string;
}
export type CreatePrecheckCode = BridgeErrorCode | "WORKSPACE_NOT_FOUND" | "WORKSPACE_UNAVAILABLE" | "AGENT_ALREADY_EXISTS";
export type CreateUnknownCause = BridgeErrorCode | "CREATE_FAILED" | "AGENT_NOT_FOUND";
export type CreateAgentResult =
  | { status: "not_attempted"; code: CreatePrecheckCode; agentId?: string }
  | { status: "unknown"; code: "CREATE_OUTCOME_UNKNOWN"; cause: CreateUnknownCause; agentId: string;
      /** Only observations made before this result settles, never remote absence. */
      observed?: { handleId: string; agent?: AgentSnapshot } }
  | { status: "created"; agentId: string; agent: AgentSnapshot; ready: boolean;
      readiness: "idle" | "not_idle" | "archived" | "incomplete_snapshot" };

/** Server-owned frozen intent. No implicit active-turn behavior or extra SDK options. */
export interface SendAgentOptions {
  agentId: string;
  expectedCwd: string;
  text: string;
  messageId: string;
  activeTurnBehavior: "interrupt" | "steer";
}
export type SendPrecheckCode = BridgeErrorCode | "AGENT_NOT_FOUND" | "AGENT_ARCHIVED"
  | "INCOMPLETE_SNAPSHOT" | "PERMISSION_REQUIRED" | "AGENT_UNAVAILABLE";
export type SendAgentResult =
  | { status: "not_attempted"; code: SendPrecheckCode; agentId?: string; messageId?: string }
  | { status: "unknown"; code: "SEND_OUTCOME_UNKNOWN"; cause: BridgeErrorCode | "SEND_FAILED";
      agentId: string; messageId: string; observed?: { sdkResolved: true } }
  /** IDs identify the frozen intent, not fields validated from the SDK's discarded ACK. */
  | { status: "acknowledged"; agentId: string; messageId: string };

export type TimelineReadOptions = Pick<PaseoAgentTimelineRefetchOptions, "direction" | "cursor" | "limit" | "projection"> & {
  /** Server-owned Task workspace. Checked locally after refresh; never sent to SDK. */
  expectedCwd?: string;
};
export type SdkTimelinePage = Awaited<ReturnType<PaseoAgentHandle["timeline"]["refetch"]>>;
export type CapabilityName = "supportsStreaming" | "supportsSessionPersistence" | "supportsSessionListing"
  | "supportsDynamicModes" | "supportsMcpServers" | "supportsReasoningStream" | "supportsToolInvocations"
  | "supportsRewindConversation" | "supportsRewindFiles" | "supportsRewindBoth";
export type AgentSnapshot = Pick<PaseoAgent, "id" | "cwd" | "status" | "title" | "provider" | "model"> & {
  /** Absent SDK fields stay absent (unknown), rather than becoming confirmed null. */
  workspaceId?: string;
  archivedAt?: string | null;
  activeTurn?: { turnId: string; startedAt: string | null } | null;
  capabilities: Record<CapabilityName, boolean | null>;
};
export interface TextFragment {
  value: string;
  truncated: boolean;
  originalLength: number;
  lengthUnit: "utf16";
}
export interface TimelineEntry {
  provider: string;
  timestamp: string;
  seqStart: number;
  seqEnd: number;
  sourceSeqRanges: { startSeq: number; endSeq: number }[];
  collapsed: SdkTimelinePage["entries"][number]["collapsed"];
  turnId: string | null;
  item: {
    type: SdkTimelinePage["entries"][number]["item"]["type"] | "activity";
    sourceType?: TextFragment;
    text: TextFragment | null;
    title: TextFragment | null;
    status: string | null;
  };
}
export type TimelinePage = Pick<SdkTimelinePage,
  "agentId" | "direction" | "projection" | "epoch" | "reset" | "staleCursor" | "gap"
  | "window" | "startCursor" | "endCursor" | "hasOlder" | "hasNewer"
> & { entries: TimelineEntry[]; mergeWindow: boolean | null };

export interface ProviderEntry {
  id: ProviderId;
  status: PaseoProviderSnapshotResult["entries"][number]["status"];
  enabled: boolean;
  label: string | null;
  fetchedAt: string | null;
}

export interface ModelEntry {
  id: string;
  label: string;
  isDefault: boolean | null;
  isSelectable: boolean | null;
  contextWindowMaxTokens: number | null;
  thinkingOptions: { id: string; label: string }[] | null;
  defaultThinkingOptionId: string | null;
}

export interface WorkspaceEntry {
  id: string;
  projectId: string;
  name: string;
  status: PaseoWorkspace["status"];
  /** Server-side path; downstream must decide whether a browser needs it. */
  directory: string | null;
}

export interface AgentBridge {
  connect(): Promise<BridgeResult>;
  /** Terminal and idempotent: construct a new bridge to use it after close. */
  close(): Promise<BridgeResult>;
  /** One no-prompt invocation. Unknown must never trigger an automatic retry. */
  createAgent(options: CreateAgentOptions): Promise<CreateAgentResult>;
  /** Exactly one send; SDK resolution is acknowledgement, never execution completion. */
  sendAgent(options: SendAgentOptions): Promise<SendAgentResult>;
  readAgent(agentId: string): Promise<BridgeResult<{ agent: AgentSnapshot }>>;
  readTimeline(agentId: string, options?: TimelineReadOptions): Promise<BridgeResult<TimelinePage>>;
  listProviders(options?: { cwd?: string }): Promise<BridgeResult<{
    entries: ProviderEntry[]; generatedAt: string;
  }>>;
  listModels(provider: ProviderId, options?: { cwd?: string }): Promise<BridgeResult<{
    entries: ModelEntry[]; provider: ProviderId; fetchedAt: string;
  }>>;
  listWorkspaces(options?: { cursor?: string; limit?: number }): Promise<BridgeResult<{
    entries: WorkspaceEntry[];
    pageInfo: { nextCursor: string | null; prevCursor: string | null; hasMore: boolean };
  }>>;
}
