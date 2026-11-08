import { ApiError } from '../contracts';

export type Interval = { low: number; mid: number; high: number };
export type Forecast = {
  sim_id: string; per_round_s: Interval; candidates: Interval; rounds: Interval;
  resolution: { n_val: number; se: Interval; detectable: Interval; rule: string };
  spend: { tokens: Interval | null; cost?: Interval; scope: string; agent_cost_included: boolean };
  bottleneck: string; stages: Record<string, number>; bottleneck_shares_s: Record<string, number>;
  assumptions: { name: string; value: unknown; source: string }[];
  params: Record<string, unknown>;
};
export type StartupError = { code: string; message: string; command: string; at: number };
export type CalibrationDetail = {
  startup_error?: StartupError | null;
  execution_timeout_seconds?: number; recovery_execution_timeout_seconds?: number | null;
  complete: boolean; alive?: boolean; elapsed_s?: number | null; phase?: string; launch_inputs?: Record<string, unknown>; inputs?: Record<string, unknown>;
  probe_size?: number; diagnosis_size?: number; completed_stages?: string[];
  calibration_concurrency?: number; probe_completed_k?: number; diagnosis_completed_cases?: number;
  measurement_active_wall_s?: number | null; cache_lock_wait_max_s?: number | null;
  summary?: { n_probe: number; n_diagnosis: number; k: number; pool_size: number; test_size: number;
    rerun_var: number; constant_fraction: number; infra_fail_rate: number; tokens_per_task: number | null;
    cold_wall_s: number | null; warm_wall_s: number | null; exec_overhead_s: number; per_task_s: number;
    p95_exec_s: number; safeguard_var: Record<string, number>; remaining_paper_sizes: number[] | null };
  assumptions?: string[]; heatmap?: { row: number; scores: (number | null)[]; mean: number }[];
  attempt_means?: (number | null)[]; aa_differences?: number[]; aa_se?: number | null;
};
export type ProposalItem = { value: unknown; source: string; reason: string; confidence: string; rule_value: unknown; agent_value?: unknown;
  range: { type: string; min?: number; max?: number }; sim_id: string | null };
export type ProposalView = { proposal: { items: Record<string, ProposalItem>; inputs: Record<string, unknown>;
  notes: { advisor?: { status: string; message?: string; reason?: string } } }; forecast: Forecast; frozen: boolean };
export type Confirmation = { confirmation_token: string; action: string; incurs_cost: boolean; expires_in_seconds: number;
  execution_timeout_seconds?: number; recovery_execution_timeout_seconds?: number | null;
  agent_profiles?: Record<string, string>;
  config_sha256: string; config?: Record<string, unknown>; sources?: Record<string, string>; budget_hours?: number;
  test_set_authorized?: boolean; spend_budget?: number | null; spend_scope?: string; search_size?: number;
  validation_size?: number; forecast?: Forecast; behavior?: string };
export const controlPath = (id: string) => `/iterations/${encodeURIComponent(id)}/v4`;
export async function patchProposal(id: string, key: string, value: unknown): Promise<ProposalView> {
  const response = await fetch('/api' + controlPath(id) + '/proposal', { method: 'PATCH', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', 'X-Workbench': '1' }, body: JSON.stringify({ key, value }) });
  const result = await response.json();
  if (!response.ok) throw new ApiError(typeof result.error === 'string' ? result.error : `修改失败 (${response.status})`, response.status, result);
  return result;
}
export const parameterLabels: Record<string, string> = {
  'adapter.analyst_profile': '分析 Agent 模型配置', 'adapter.implementer_profile': '实现 Agent 模型配置',
  'adapter.reviewer_profile': '审查 Agent 模型配置', 'adapter.calibrator_profile': '校准顾问模型配置',
  calibration_concurrency: '校准执行并发',
  calibration_k: '校准遍数', validation_size: '验证集题数', diagnose_size: '诊断题数', b: '每轮候选数',
  cards_per_analysis: '每轮分析卡数', max_concurrent_executions: '执行并发', remeasure: '采纳前复测',
  smoke: '冒烟检查', smoke_size: '冒烟题数', minibatch: '小批筛选', minibatch_fraction: '小批比例', minibatch_min: '小批最少题数',
  safeguard_z: '保护指标噪声系数', safeguards: '产品容忍量', 'adapter.agent_timeout': '实现 Agent 时限（秒）',
  'adapter.execution_timeout_seconds': '执行时限（秒）', 'adapter.infra_retry': '基础设施重试次数',
};
export const simulationKey = (key: string) => key === 'adapter.agent_timeout' ? 'agent_timeout'
  : ['validation_size', 'calibration_k', 'smoke', 'smoke_size', 'minibatch', 'minibatch_fraction', 'minibatch_min',
    'b', 'cards_per_analysis', 'max_concurrent_executions', 'remeasure'].includes(key) ? key : null;
