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

function fragment(value: string, bound = 8192): TextFragment {
  let end = Math.min(value.length, bound);
  // Avoid cutting a UTF-16 surrogate pair in half at the bound.
  if (end < value.length && end > 0 && /[\uD800-\uDBFF]/.test(value[end - 1]!)) end--;
  return { value: value.slice(0, end), truncated: end < value.length, originalLength: value.length, lengthUnit: "utf16" };
}

// Activity text (reasoning, tool detail, todo, errors) shares one page budget,
// spent newest entry first, so a page of long tool outputs stays far below the
// 4 MiB CLI limit instead of failing whole. A JSON-encoded UTF-16 unit costs at
// most 6 bytes, so shrinking to remaining/6 units always fits.
const ACTIVITY_BUDGET_BYTES = 2.5 * 1024 * 1024;
type Budget = { remaining: number };

function bounded(value: string, budget: Budget): TextFragment {
  let result = fragment(value);
  let cost = Buffer.byteLength(JSON.stringify(result.value));
  if (cost > budget.remaining) {
    result = fragment(value, Math.min(8192, Math.floor(budget.remaining / 6)));
    cost = Buffer.byteLength(JSON.stringify(result.value));
  }
  budget.remaining -= cost;
  return result;
}

function json(value: unknown): string {
  try { return JSON.stringify(value, null, 2) ?? "null"; } catch { return "[unserializable]"; }
}

const textOf = (value: unknown) => typeof value === "string" ? value : json(value);
const numberOf = (value: unknown) => typeof value === "number" && Number.isFinite(value) ? value : null;
const list = (value: unknown, line: (item: Record<string, unknown>) => string) => Array.isArray(value)
  ? value.map((item) => typeof item === "string" ? item : item && typeof item === "object" ? line(item) : json(item)).join("\n")
  : textOf(value);

type FieldKind = "text" | "number" | "boolean" | "json" | ((value: unknown) => string);
const detailFields: Record<string, Record<string, FieldKind>> = {
  shell: { command: "text", cwd: "text", output: "text", exitCode: "number" },
  read: { filePath: "text", content: "text", offset: "number", limit: "number" },
  edit: { filePath: "text", oldString: "text", newString: "text", unifiedDiff: "text" },
  write: { filePath: "text", content: "text" },
  search: { query: "text", toolName: "text", content: "text", mode: "text", truncated: "boolean",
    numFiles: "number", numMatches: "number",
    filePaths: (v) => list(v, json), webResults: (v) => list(v, (r) => `${textOf(r.title)} ${textOf(r.url)}`),
    annotations: (v) => list(v, json) },
  fetch: { url: "text", prompt: "text", result: "text", code: "number", codeText: "text" },
  worktree_setup: { worktreePath: "text", branchName: "text", log: "text" },
  sub_agent: { subAgentType: "text", description: "text", log: "text",
    actions: (v) => list(v, (a) => [a.toolName, a.summary].filter((x) => x !== undefined).map(textOf).join(": ")) },
  plain_text: { label: "text", text: "text" },
  plan: { text: "text" },
  unknown: { input: "json", output: "json" },
};

function projectDetail(detail: unknown, budget: Budget): TimelineEntry["item"]["detail"] {
  if (!detail || typeof detail !== "object") return null;
  const raw = detail as Record<string, unknown>;
  const known = typeof raw.type === "string" && Object.hasOwn(detailFields, raw.type);
  // A future detail type keeps its whole payload as unknown input.
  const kind = known ? raw.type as string : "unknown";
  const source = known ? raw : { input: raw };
  const fields: Record<string, TextFragment | number | boolean | null> = {};
  for (const [name, as] of Object.entries(detailFields[kind]!)) {
    const value = source[name];
    if (value === undefined) continue;
    fields[name] = as === "number" ? numberOf(value)
      : as === "boolean" ? (typeof value === "boolean" ? value : null)
        : value === null && as === "text" ? null
          : bounded(as === "text" ? textOf(value) : as === "json" ? json(value) : as(value), budget);
  }
  return { kind, fields };
}

function projectItem(item: SdkTimelinePage["entries"][number]["item"], budget: Budget): TimelineEntry["item"] {
  if (item.type === "user_message" || item.type === "assistant_message")
    return { type: item.type, text: fragment(item.text), title: null, status: null };
  if (item.type === "tool_call")
    return { type: item.type, text: null, title: fragment(item.name), status: item.status,
      detail: projectDetail(item.detail, budget),
      error: item.error === null || item.error === undefined ? null : bounded(textOf(item.error), budget) };
  const titles = { reasoning: "Reasoning", todo: "Tasks", error: "Error", notification: "Notification",
    compaction: "Compaction", plugin: "Plugin activity" };
  if (!Object.hasOwn(titles, item.type)) return {
    type: "activity", text: null, title: fragment("Activity"), status: null,
    sourceType: fragment(typeof item.type === "string" ? item.type : "unknown"),
  };
  const title = fragment(titles[item.type]);
  if (item.type === "reasoning") return { type: item.type, text: bounded(textOf(item.text), budget), title, status: null };
  if (item.type === "error") return { type: item.type, text: bounded(textOf(item.message), budget), title, status: null };
  if (item.type === "notification") return { type: item.type, text: bounded(textOf(item.message), budget), title,
    status: typeof item.level === "string" ? item.level : null };
  if (item.type === "todo") {
    const items = Array.isArray(item.items) ? item.items : [];
    return { type: item.type, text: null, title, status: null, itemsOmitted: Math.max(0, items.length - 100),
      items: items.slice(0, 100).map((task) => ({ text: bounded(textOf(task?.text), budget), completed: task?.completed === true,
        status: typeof task?.status === "string" ? task.status : null })) };
  }
  return { type: item.type, text: null, title, status: item.type === "compaction" ? item.status : null };
}

export function projectTimeline(page: SdkTimelinePage, id: string): TimelinePage {
  if (page.agentId !== id || (page.agent && page.agent.id !== id)) throw new ReadFault("IDENTITY_MISMATCH");
  if (page.error || !page.agent) throw new ReadFault("READ_FAILED");
  if (page.entries.length > 100) throw new ReadFault("RESPONSE_TOO_LARGE");
  const budget = { remaining: ACTIVITY_BUDGET_BYTES };
  const items: TimelineEntry["item"][] = [];
  for (let i = page.entries.length - 1; i >= 0; i--) items[i] = projectItem(page.entries[i]!.item, budget);
  return {
    agentId: page.agentId, direction: page.direction, projection: page.projection, epoch: page.epoch,
    reset: page.reset, staleCursor: page.staleCursor, gap: page.gap,
    window: { minSeq: page.window.minSeq, maxSeq: page.window.maxSeq, nextSeq: page.window.nextSeq },
    startCursor: page.startCursor ? { epoch: page.startCursor.epoch, seq: page.startCursor.seq } : null,
    endCursor: page.endCursor ? { epoch: page.endCursor.epoch, seq: page.endCursor.seq } : null,
    hasOlder: page.hasOlder, hasNewer: page.hasNewer, mergeWindow: page.mergeWindow ?? null,
    entries: page.entries.map((entry, i) => ({
      provider: entry.provider, timestamp: entry.timestamp, seqStart: entry.seqStart, seqEnd: entry.seqEnd,
      sourceSeqRanges: entry.sourceSeqRanges.map(({ startSeq, endSeq }) => ({ startSeq, endSeq })),
      collapsed: [...entry.collapsed], turnId: entry.turnId ?? null, item: items[i]!,
    })),
  };
}
