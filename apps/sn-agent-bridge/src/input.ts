import type { AgentBridgeConfig, CreateAgentOptions, SendAgentOptions, TimelineReadOptions } from "./types.js";
import { isAbsolute } from "node:path";

export class InvalidInputError extends Error {
  constructor() { super("Invalid input"); }
}

export function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new InvalidInputError();
  const prototype = Object.getPrototypeOf(value);
  if (prototype !== Object.prototype && prototype !== null) throw new InvalidInputError();
  return value as Record<string, unknown>;
}

export function onlyKeys(value: Record<string, unknown>, keys: readonly string[]): void {
  if (Object.keys(value).some((key) => !keys.includes(key))) throw new InvalidInputError();
}

export function string(value: unknown, max = 8192, allowEmpty = false): string {
  if (typeof value !== "string" || value.length > max || (!allowEmpty && (!value.length || value.trim() !== value)))
    throw new InvalidInputError();
  return value;
}

export function agentId(value: unknown): string { return string(value, 512); }

// A full RFC UUID, not the SDK's alias/short-ID lookup syntax.
export function creationId(value: unknown): string {
  const id = string(value, 36);
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(id)) throw new InvalidInputError();
  return id;
}

export function createOptions(value: unknown): CreateAgentOptions {
  const input = record(value);
  onlyKeys(input, ["workspaceId", "expectedCwd", "agentId", "idempotencyKey", "config", "title"]);
  const opaque = (value: unknown, max = 512) => {
    const result = string(value, max);
    if (result.includes("\0")) throw new InvalidInputError();
    return result;
  };
  const expectedCwd = string(input.expectedCwd, 8192, true);
  if (!expectedCwd.length || !isAbsolute(expectedCwd) || expectedCwd.includes("\0")) throw new InvalidInputError();
  const config = record(input.config);
  onlyKeys(config, ["provider", "modeId", "thinkingOptionId"]);
  const provider = opaque(config.provider);
  const slash = provider.indexOf("/");
  if (slash < 1 || slash === provider.length - 1) throw new InvalidInputError();
  opaque(provider.slice(0, slash)); opaque(provider.slice(slash + 1));
  return { workspaceId: opaque(input.workspaceId), expectedCwd, agentId: creationId(input.agentId),
    idempotencyKey: opaque(input.idempotencyKey), config: { provider,
      ...(config.modeId === undefined ? {} : { modeId: opaque(config.modeId) }),
      ...(config.thinkingOptionId === undefined ? {} : { thinkingOptionId: opaque(config.thinkingOptionId) }) },
    ...(input.title === undefined ? {} : { title: opaque(input.title, 200) }) };
}

export const MAX_SEND_TEXT_UTF16 = 16_384;
export const MAX_SEND_TEXT_UTF8 = 32 * 1024;

export function sendOptions(value: unknown): SendAgentOptions {
  const input = record(value);
  onlyKeys(input, ["agentId", "expectedCwd", "text", "messageId", "activeTurnBehavior"]);
  const expectedCwd = string(input.expectedCwd, 8192, true);
  if (!expectedCwd.length || !isAbsolute(expectedCwd) || expectedCwd.includes("\0")) throw new InvalidInputError();
  const text = string(input.text, MAX_SEND_TEXT_UTF16, true);
  if (!text.trim().length || text.includes("\0") || Buffer.byteLength(text, "utf8") > MAX_SEND_TEXT_UTF8)
    throw new InvalidInputError();
  if (input.activeTurnBehavior !== "interrupt" && input.activeTurnBehavior !== "steer") throw new InvalidInputError();
  // All fields are primitive copies, frozen against mutation while prechecks await.
  return { agentId: creationId(input.agentId), expectedCwd, text,
    messageId: creationId(input.messageId), activeTurnBehavior: input.activeTurnBehavior };
}

function limit(value: unknown): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 1 || value > 100)
    throw new InvalidInputError();
  return value;
}

export function timelineOptions(value?: unknown): TimelineReadOptions {
  const input = value === undefined ? {} : record(value);
  onlyKeys(input, ["direction", "cursor", "limit", "projection", "expectedCwd"]);
  const result: TimelineReadOptions = {
    limit: input.limit === undefined ? 50 : limit(input.limit),
    projection: "projected",
  };
  if (input.expectedCwd !== undefined) {
    // Filesystem names may end in whitespace; preserve the path verbatim.
    const path = string(input.expectedCwd, 8192, true);
    if (!path.length || !isAbsolute(path) || path.includes("\0")) throw new InvalidInputError();
    result.expectedCwd = path;
  }
  if (input.direction !== undefined) {
    if (!["tail", "before", "after"].includes(input.direction as string)) throw new InvalidInputError();
    result.direction = input.direction as TimelineReadOptions["direction"];
  }
  if (input.projection !== undefined) {
    if (input.projection !== "projected" && input.projection !== "canonical") throw new InvalidInputError();
    result.projection = input.projection;
  }
  if (input.cursor !== undefined) {
    const cursor = record(input.cursor);
    onlyKeys(cursor, ["epoch", "seq"]);
    if (typeof cursor.seq !== "number" || !Number.isSafeInteger(cursor.seq) || cursor.seq < 0)
      throw new InvalidInputError();
    result.cursor = { epoch: string(cursor.epoch, 512), seq: cursor.seq };
  }
  return result;
}

export type ReadCommand =
  | { op: "listProviders"; cwd?: string }
  | { op: "listModels"; provider: string; cwd?: string }
  | { op: "listWorkspaces"; cursor?: string; limit?: number }
  | { op: "readAgent"; agentId: string }
  | ({ op: "readTimeline"; agentId: string } & TimelineReadOptions);
export type BridgeCommand = ReadCommand | ({ op: "createAgent" } & CreateAgentOptions)
  | ({ op: "sendAgent" } & SendAgentOptions);
export interface CliRequest { config: AgentBridgeConfig; command: BridgeCommand }

export function parseRequest(value: unknown): CliRequest {
  const input = record(value);
  onlyKeys(input, ["config", "command"]);
  const conf = record(input.config);
  onlyKeys(conf, ["url", "password", "authHeader", "connectTimeoutMs"]);
  const config: AgentBridgeConfig = { url: string(conf.url) };
  if (conf.password !== undefined) config.password = string(conf.password, 8192, true);
  if (conf.authHeader !== undefined) config.authHeader = string(conf.authHeader, 8192, true);
  if (conf.connectTimeoutMs !== undefined) {
    if (typeof conf.connectTimeoutMs !== "number" || !Number.isSafeInteger(conf.connectTimeoutMs)
      || conf.connectTimeoutMs < 1 || conf.connectTimeoutMs > 120_000) throw new InvalidInputError();
    config.connectTimeoutMs = conf.connectTimeoutMs;
  }
  const command = record(input.command);
  switch (command.op) {
    case "sendAgent": {
      const { op: _op, ...options } = command;
      return { config, command: { op: "sendAgent", ...sendOptions(options) } };
    }
    case "createAgent": {
      const { op: _op, ...options } = command;
      return { config, command: { op: "createAgent", ...createOptions(options) } };
    }
    case "listProviders":
    case "listModels": {
      onlyKeys(command, command.op === "listModels" ? ["op", "provider", "cwd"] : ["op", "cwd"]);
      const cwd = command.cwd === undefined ? {} : { cwd: string(command.cwd) };
      return { config, command: command.op === "listModels"
        ? { op: command.op, provider: agentId(command.provider), ...cwd } : { op: command.op, ...cwd } };
    }
    case "listWorkspaces": {
      onlyKeys(command, ["op", "cursor", "limit"]);
      return { config, command: { op: command.op,
        ...(command.cursor === undefined ? {} : { cursor: string(command.cursor) }),
        ...(command.limit === undefined ? {} : { limit: limit(command.limit) }) } };
    }
    case "readAgent":
      onlyKeys(command, ["op", "agentId"]);
      return { config, command: { op: command.op, agentId: agentId(command.agentId) } };
    case "readTimeline": {
      onlyKeys(command, ["op", "agentId", "direction", "cursor", "limit", "projection", "expectedCwd"]);
      const { op: _op, agentId: id, ...options } = command;
      return { config, command: { op: "readTimeline", agentId: agentId(id), ...timelineOptions(options) } };
    }
    default: throw new InvalidInputError();
  }
}
