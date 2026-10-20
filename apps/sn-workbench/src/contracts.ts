import { useEffect, useState } from 'react';

export type Metric = { value: number; denominator: number; kind: 'per-case' | 'batch-only' };
export type CaseRow = { case_id: string; question: string; task: string; status: string; metrics: Record<string, number> };
export type Result = {
  id: string; suite: string; scope: string; method: { name: string; kind: string; configuration: Record<string, unknown> };
  case_count: number; bundle_case_count: number; coverage: Record<string, number | boolean>;
  metrics: Record<string, Metric>; pending_metrics: string[]; cases?: CaseRow[];
  scores_id: string; submission_id: string; scorer_id: string; profile: string;
  source: Record<string, unknown>; source_hashes: Record<string, string>; original_at: string | null;
};
export type Review = {
  id: string; parent_id: string | null; reviewer: string; judgment: string; reason: string;
  evidence: string[]; result_id: string; case_id: string; saved_at: string;
};
export type History = { seq: number; kind: string; object_id: string; content: Record<string, unknown>; registered_at: string };
export type TaskSession = { id: string; task_id: string; agent_id: string; linked_by: string; linked_at: string;
  role?: 'analyst' | 'implementer' | null; hypothesis_id?: string | null; candidate_id?: string | null };
export type BridgeRelease = { status: 'ok' | 'unavailable' | 'not_needed'; code?: string };
export type ReadResult<T> = ({ status: 'ok' } & T) | { status: 'missing' | 'unavailable'; code: string };
export type ReadEnvelope<T> = { result: ReadResult<T>; release: BridgeRelease; observed_at: string };
export type AgentSnapshot = { id: string; title: string | null; status: string; provider: string; model: string | null };
export type TimelineCursor = { epoch: string; seq: number };
export type SourceLocator = { epoch: string; seq_start: number; seq_end: number;
  source_seq_ranges: { start_seq: number; end_seq: number }[]; turn_id: string | null; sha256: string };
export type TimelineEntry = { id: string; label: string; kind: 'user' | 'assistant' | 'activity'; text: string | null;
  truncated: boolean; activityType: string | null; candidateBlockedReason: string | null; source_locator: SourceLocator };
export type TimelinePage = { agentId: string; direction: 'tail' | 'before' | 'after'; projection: 'projected' | 'canonical';
  epoch: string; reset: boolean; staleCursor: boolean; gap: boolean; window: { minSeq: number; maxSeq: number; nextSeq: number };
  startCursor: TimelineCursor | null; endCursor: TimelineCursor | null; hasOlder: boolean; hasNewer: boolean;
  mergeWindow: boolean | null; entries: TimelineEntry[] };
export type Task = { id: string; title: string; goal: string; workspace_id: string; results: Result[]; sessions: TaskSession[]; history: History[] };
export type ContextReference = { kind: 'goal' } | { kind: 'file'; path: string }
  | { kind: 'result'; result_id: string } | { kind: 'case'; result_id: string; case_id: string }
  | { kind: 'run_case'; run_id: string; case_id: string } | { kind: 'task_runs' } | { kind: 'brief'; session_id: string };
export type ContextSnapshot = { reference: ContextReference; title: string; text: string; sha256: string; source_refs: Record<string, string> };
export type Workspace = { id: string; title: string; location: string; tasks: Pick<Task, 'id' | 'title' | 'goal'>[] };
export type Source = { id: string; title: string; description: string };
export type CreationProfile = { id: string; provider: string; model: string; modeId?: string; thinkingOptionId?: string };
export type WorkbenchCapabilities = { result_import?: boolean; reviews?: boolean; notes?: boolean; agent?: boolean;
  hypothesis_worktrees?: boolean;
  agent_create?: boolean; agent_read?: boolean; agent_send?: boolean; result_comparison?: boolean; plan_drafts?: boolean; execution?: boolean };

export type ComparisonReason = { code: string; message: string; detail: string };
export type ComparisonSide = { result_id: string; status: 'readable' | 'unavailable';
  summary: Omit<Result, 'original_at'> | null; read_reference: { kind: 'result'; result_id: string }; reason: ComparisonReason | null };
export type ComparisonReport = { report_id: string; suite: string; scope: string; case_count: number; case_ids: string[];
  bundle_id: string; profile: string; scorer_id: string; source: Record<string, unknown>;
  metric_denominators: Record<string, number>; metric_case_ids: Record<string, string[]>;
  common_metrics: string[]; paired_metrics: string[];
  methods: { method_id: string; method: Result['method']; metrics: Record<string, number>;
    metric_denominators: Record<string, number>; metric_case_ids: Record<string, string[]>;
    pending_metrics: string[]; batch_only_metrics: string[]; unshared_metrics: string[]; unavailable_metrics: string[] }[];
  comparisons: { direction: string; batch_differences: Record<string, number>;
    paired_mean_differences: Record<string, number>; uncertainty: Record<string, unknown>;
    paired: Record<string, unknown>[]; group_differences: Record<string, unknown>[] }[];
  method_differences: Record<string, unknown>; interpretation: string[] };
export type TaskComparison = { task_id: string; left_result_id: string; right_result_id: string;
  status: 'comparable' | 'not_comparable' | 'unavailable'; left: ComparisonSide; right: ComparisonSide;
  reason: ComparisonReason | null; report: ComparisonReport | null };
export type PlanInput = { input_id: string; adapter: 'notebook-bundle'; bundle_id: string; suite: string;
  source_revision: string; adaptation_revision: 'notebook-data-v3'; case_ids: string[];
  partitions: { partition_id: string; case_ids: string[] }[] };
export type PlanDraftCommand = { title: string; input_id: string; expected_bundle_id: string; case_ids: string[];
  mode: 'chunk' | 'reasoning'; plan_id: string | null; base_revision: string | null; request_key: string };
export type PlanDraftRecord = { plan_id: string; revision: string; parent_revision: string | null;
  task_id: string; workspace_id: string; actor: string; title: string; state: 'draft'; execution: false;
  content_hash: string; saved_at: string; content: { format: 'sn-workbench-evaluation-plan-draft-v1'; title: string;
    input: Omit<PlanInput, 'case_ids' | 'partitions'>;
    selection: { kind: 'explicit'; case_ids: string[]; partition_ids: string[]; selection_hash: string };
    execution_intent: { entrypoint: 'scripts/run_notebook_benchmarks.py'; mode: 'chunk' | 'reasoning';
      request_revision: 'notebook-request-v3'; model_services_ref: null; official_scorer_ref: null };
    source_intent: { kind: 'task-workspace-at-freeze'; workspace_id: string; binding: 'not_captured' } } };
export type PlanDraftReceipt = { request_key: string; record: PlanDraftRecord | null };
export type AgentCreationRelease = { status: 'ok' | 'unavailable' | 'not_needed'; code?: string };
export type AgentCreationResult = { status: string; code?: string; cause?: string; agentId?: string; ready?: boolean;
  readiness?: 'idle' | 'not_idle' | 'archived' | 'incomplete_snapshot';
  agent?: { id: string; title?: string | null; status?: string; provider: string; model?: string | null };
  observed?: { handleId: string } };
export type AgentCreationEnvelope = { result: AgentCreationResult; release: AgentCreationRelease };
export type AgentCreationRecord = { id: string; task_id: string; actor: string; profile_id: string; title: string | null;
  request_key: string; agent_id: string; status: string; prepared_at: string | null; started_at: string | null;
  finished_at: string | null; session_id: string | null; envelope: AgentCreationEnvelope | null };
export type AgentMessageRecord = { id: string; task_id: string; actor: string; session_id: string; agent_id: string;
  message_id: string; text: string; contexts: { reference: ContextReference; sha256: string }[];
  request_key: string; active_turn_behavior: 'interrupt' | 'steer'; body_format: string; input_sha256: string; body_sha256: string;
  status: 'prepared' | 'unknown' | 'acknowledged' | 'not_attempted'; prepared_at: string; started_at: string | null; finished_at: string | null;
  envelope: { result: { status: string; agentId?: string; messageId?: string; code?: string; cause?: string; observed?: { sdkResolved: true } };
    release: BridgeRelease } | null };
export type ExecutionWorktree = { id: string; title: string; path: string };
export type Bootstrap = { workspaces: Workspace[]; sources: Source[]; inputs?: Source[]; actor: string;
  execution_worktrees?: ExecutionWorktree[];
  capabilities?: WorkbenchCapabilities; creation_profiles?: CreationProfile[] };
export type Packet = {
  result_id: string; case_id: string; task: string; question: string; status: string;
  answer: string; answer_source: string; prediction: string; metrics: Record<string, number>; metric_states: Record<string, string>; references: unknown; gold: unknown;
  documents: { id: string; title: string; text_sha256: string; characters: number; excerpt: string; excerpt_end: number }[];
  evidence: { id: string; label: string; value: unknown; completeness: string }[];
  evidence_ids: string[]; unavailable: string[]; source_refs: Record<string, string>;
};

export async function api<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch('/api' + path, {
    method: body === undefined ? 'GET' : 'POST', credentials: 'same-origin', signal,
    headers: { 'Content-Type': 'application/json', 'X-Workbench': '1' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const value = await response.json();
  if (!response.ok) throw new ApiError(typeof value.error === 'string' ? value.error : `请求失败 (${response.status})`, response.status, value);
  return value;
}

export class ApiError extends Error {
  constructor(message: string, public readonly status: number, public readonly value: unknown) { super(message); }
}

export function requestKey(): string { return crypto.randomUUID(); }
export function literal(value: unknown): string { return typeof value === 'string' ? value : JSON.stringify(value, null, 2); }
export function date(value: string): string { return new Date(value).toLocaleString('zh-CN', { hour12: false }); }
export const judgmentLabels: Record<string, string> = { confirmed: '问题成立', rejected: '不支持该问题', undecided: '无法判断' };
export const statusLabels: Record<string, string> = { success: '已回答', no_answer: '空回答', error: '执行错误', missing: '缺失', clarification: '澄清' };

export function useResource<T>(path: string | null, refresh = 0) {
  const [state, setState] = useState<{ path: string | null; data?: T; error?: string }>({ path: null });
  useEffect(() => {
    if (!path) return;
    const controller = new AbortController();
    api<T>(path, undefined, controller.signal).then(data => {
      if (!controller.signal.aborted) setState({ path, data });
    }).catch(error => {
      if (!controller.signal.aborted) setState({ path, error: String(error.message) });
    });
    return () => controller.abort();
  }, [path, refresh]);
  return state.path === path ? state : { path };
}
