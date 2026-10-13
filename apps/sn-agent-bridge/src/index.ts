import { createPaseoClient } from "@getpaseo/client";
import { createBridge } from "./bridge.js";
import type { AgentBridge, AgentBridgeConfig } from "./types.js";

export type {
  AgentBridge, AgentBridgeConfig, BridgeErrorCode, BridgeResult,
  ModelEntry, ProviderEntry, ProviderId, WorkspaceEntry,
  AgentSnapshot, CapabilityName, TextFragment, TimelineEntry, TimelinePage, TimelineReadOptions,
  CreateAgentOptions, CreateAgentResult, CreatePrecheckCode, CreateUnknownCause,
  SendAgentOptions, SendAgentResult, SendPrecheckCode,
} from "./types.js";
export type { CliRequest, ReadCommand, BridgeCommand } from "./input.js";
export type { CliResponse } from "./cli-protocol.js";

export function createAgentBridge(config: AgentBridgeConfig): AgentBridge {
  return createBridge(config, createPaseoClient);
}
