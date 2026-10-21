import type { AgentBridge, AgentBridgeConfig, BridgeResult, CreateAgentResult, SendAgentResult } from "./types.js";
import { InvalidInputError, parseRequest } from "./input.js";

export interface CliResponse {
  result: BridgeResult<Record<string, unknown>> | CreateAgentResult | SendAgentResult;
  release: BridgeResult | { status: "not_needed" };
}
export const MAX_INPUT_BYTES = 64 * 1024;
export const MAX_OUTPUT_BYTES = 4 * 1024 * 1024;

export function inputFailure(): CliResponse {
  return { result: { status: "unavailable", code: "INVALID_INPUT" }, release: { status: "not_needed" } };
}

export async function executeRequest(
  input: unknown, factory: (config: AgentBridgeConfig) => AgentBridge,
): Promise<CliResponse> {
  let bridge: AgentBridge | undefined;
  let result: CliResponse["result"] = { status: "unavailable", code: "INTERNAL_ERROR" };
  let release: CliResponse["release"] = { status: "not_needed" };
  let creation = false;
  let creationStarted = false;
  let send = false;
  let sendStarted = false;
  let plannedId: string | undefined;
  let messageId: string | undefined;
  try {
    // Recognizable mutation commands rejected before dispatch are unattempted.
    creation = (input as { command?: { op?: unknown } } | null)?.command?.op === "createAgent";
    send = (input as { command?: { op?: unknown } } | null)?.command?.op === "sendAgent";
    const request = parseRequest(input);
    if (request.command.op === "createAgent") plannedId = request.command.agentId;
    if (request.command.op === "sendAgent") { plannedId = request.command.agentId; messageId = request.command.messageId; }
    bridge = factory(request.config);
    const connection = await bridge.connect();
    result = (creation || send) && connection.status !== "ok"
      ? { status: "not_attempted", code: connection.status === "missing" ? "INTERNAL_ERROR" : connection.code, agentId: plannedId!,
          ...(send ? { messageId: messageId! } : {}) }
      : connection;
    if (connection.status === "ok") {
      const command = request.command;
      switch (command.op) {
        case "sendAgent": {
          const { op: _op, ...options } = command;
          sendStarted = true;
          result = await bridge.sendAgent(options);
          break;
        }
        case "createAgent": {
          const { op: _op, ...options } = command;
          creationStarted = true;
          result = await bridge.createAgent(options);
          break;
        }
        case "listProviders": result = await bridge.listProviders(command.cwd === undefined ? undefined : { cwd: command.cwd }); break;
        case "listModels": result = await bridge.listModels(command.provider, command.cwd === undefined ? undefined : { cwd: command.cwd }); break;
        case "listWorkspaces": {
          const { op: _op, ...options } = command;
          result = await bridge.listWorkspaces(options);
          break;
        }
        case "readAgent": result = await bridge.readAgent(command.agentId); break;
        case "archiveAgent": result = await bridge.archiveAgent(command.agentId); break;
        case "readTimeline": {
          const { op: _op, agentId: id, ...options } = command;
          result = await bridge.readTimeline(id, options);
          break;
        }
      }
    }
  } catch (error) {
    const code = error instanceof InvalidInputError ? "INVALID_INPUT" : "INTERNAL_ERROR";
    result = sendStarted
      ? { status: "unknown", code: "SEND_OUTCOME_UNKNOWN", cause: code, agentId: plannedId!, messageId: messageId! }
      : creationStarted
      ? { status: "unknown", code: "CREATE_OUTCOME_UNKNOWN", cause: code, agentId: plannedId! }
      : creation || send ? { status: "not_attempted", code, ...(plannedId === undefined ? {} : { agentId: plannedId }),
          ...(messageId === undefined ? {} : { messageId }) }
        : { status: "unavailable", code };
  } finally {
    if (bridge) {
      try { release = await bridge.close(); }
      catch { release = { status: "unavailable", code: "CLOSE_FAILED" }; }
    }
  }
  return { result, release };
}

/** A oversized response fails as a whole: no dropped entries or invented cursor. */
export function encodeResponse(response: CliResponse): string {
  try {
    const output = JSON.stringify(response) + "\n";
    if (Buffer.byteLength(output) <= MAX_OUTPUT_BYTES) return output;
    return encodingFailure(response, "RESPONSE_TOO_LARGE");
  } catch {
    // release contains only our fixed DTO; retain its real failure outcome.
    return encodingFailure(response, "INTERNAL_ERROR");
  }
}

function encodingFailure(response: CliResponse, code: "RESPONSE_TOO_LARGE" | "INTERNAL_ERROR"): string {
  const original = response.result;
  if (original.status === "acknowledged" || (original.status === "unknown" && original.code === "SEND_OUTCOME_UNKNOWN")) {
    const result: SendAgentResult = { status: "unknown", code: "SEND_OUTCOME_UNKNOWN", cause: code,
      agentId: original.agentId, messageId: original.messageId,
      ...(original.status === "acknowledged" || original.observed?.sdkResolved ? { observed: { sdkResolved: true } } : {}) };
    return JSON.stringify({ result, release: response.release }) + "\n";
  }
  const result = original.status === "created" || original.status === "unknown"
    ? { status: "unknown", code: "CREATE_OUTCOME_UNKNOWN", cause: code, agentId: original.agentId,
        ...(original.status === "created" ? { observed: { handleId: original.agentId } }
          : original.observed ? { observed: { handleId: original.observed.handleId } } : {}) }
    : original.status === "not_attempted"
      ? { status: "not_attempted", code, ...(original.agentId === undefined ? {} : { agentId: original.agentId }),
          ...("messageId" in original && original.messageId !== undefined ? { messageId: original.messageId } : {}) }
      : { status: "unavailable", code };
  return JSON.stringify({ result, release: response.release }) + "\n";
}
