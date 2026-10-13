import type { PaseoAgent } from "@getpaseo/client";
import type { AgentSnapshot, BridgeErrorCode, CapabilityName, SdkTimelinePage, TextFragment, TimelineEntry, TimelinePage } from "./types.js";

export class ReadFault extends Error {
  constructor(readonly code: BridgeErrorCode | "AGENT_NOT_FOUND") { super(code); }
}

const capabilityNames: CapabilityName[] = [
  "supportsStreaming", "supportsSessionPersistence", "supportsSessionListing", "supportsDynamicModes",
  "supportsMcpServers", "supportsReasoningStream", "supportsToolInvocations", "supportsRewindConversation",
  "supportsRewindFiles", "supportsRewindBoth",
];

export function exactAgent(agent: PaseoAgent | null, id: string): PaseoAgent {
  if (!agent) throw new ReadFault("AGENT_NOT_FOUND");
  if (agent.id !== id) throw new ReadFault("IDENTITY_MISMATCH");
  return agent;
}

export function projectAgent(agent: PaseoAgent): AgentSnapshot {
  const capabilities = Object.fromEntries(capabilityNames.map((name) => [
    name, typeof agent.capabilities[name] === "boolean" ? agent.capabilities[name] : null,
  ])) as AgentSnapshot["capabilities"];
  return {
    id: agent.id, cwd: agent.cwd,
    ...(agent.workspaceId === undefined ? {} : { workspaceId: agent.workspaceId }),
    ...(agent.archivedAt === undefined ? {} : { archivedAt: agent.archivedAt }),
    status: agent.status, title: agent.title, provider: agent.provider, model: agent.model,
    ...(agent.activeTurn === undefined ? {} : { activeTurn: agent.activeTurn
      ? { turnId: agent.activeTurn.turnId, startedAt: agent.activeTurn.startedAt } : null }),
    capabilities,
  };
}

function fragment(value: string): TextFragment {
  let end = Math.min(value.length, 8192);
  // Avoid cutting a UTF-16 surrogate pair in half at the bound.
  if (end < value.length && end > 0 && /[\uD800-\uDBFF]/.test(value[end - 1]!)) end--;
  return { value: value.slice(0, end), truncated: end < value.length, originalLength: value.length, lengthUnit: "utf16" };
}

function projectItem(item: SdkTimelinePage["entries"][number]["item"]): TimelineEntry["item"] {
  if (item.type === "user_message" || item.type === "assistant_message")
    return { type: item.type, text: fragment(item.text), title: null, status: null };
  if (item.type === "tool_call")
    return { type: item.type, text: null, title: fragment(item.name), status: item.status };
  const titles = { reasoning: "Reasoning", todo: "Tasks", error: "Error", notification: "Notification",
    compaction: "Compaction", plugin: "Plugin activity" };
  if (!Object.hasOwn(titles, item.type)) return {
    type: "activity", text: null, title: fragment("Activity"), status: null,
    sourceType: fragment(typeof item.type === "string" ? item.type : "unknown"),
  };
  return { type: item.type, text: null, title: fragment(titles[item.type]),
    status: item.type === "compaction" ? item.status : null };
}

export function projectTimeline(page: SdkTimelinePage, id: string): TimelinePage {
  if (page.agentId !== id || (page.agent && page.agent.id !== id)) throw new ReadFault("IDENTITY_MISMATCH");
  if (page.error || !page.agent) throw new ReadFault("READ_FAILED");
  if (page.entries.length > 100) throw new ReadFault("RESPONSE_TOO_LARGE");
  return {
    agentId: page.agentId, direction: page.direction, projection: page.projection, epoch: page.epoch,
    reset: page.reset, staleCursor: page.staleCursor, gap: page.gap,
    window: { minSeq: page.window.minSeq, maxSeq: page.window.maxSeq, nextSeq: page.window.nextSeq },
    startCursor: page.startCursor ? { epoch: page.startCursor.epoch, seq: page.startCursor.seq } : null,
    endCursor: page.endCursor ? { epoch: page.endCursor.epoch, seq: page.endCursor.seq } : null,
    hasOlder: page.hasOlder, hasNewer: page.hasNewer, mergeWindow: page.mergeWindow ?? null,
    entries: page.entries.map((entry) => ({
      provider: entry.provider, timestamp: entry.timestamp, seqStart: entry.seqStart, seqEnd: entry.seqEnd,
      sourceSeqRanges: entry.sourceSeqRanges.map(({ startSeq, endSeq }) => ({ startSeq, endSeq })),
      collapsed: [...entry.collapsed], turnId: entry.turnId ?? null, item: projectItem(entry.item),
    })),
  };
}
