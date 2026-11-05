// The projection served by GET /api/iterations/{id}/auto-run for a method-v4 run (optimizer/v4/run_view.py).
// Counts only, never case ids.

export type V4Status = 'running' | 'interrupted' | 'halted' | 'not_started';
export type V4Phase = 'calibrating' | 'analysing' | 'building' | 'deciding' | 'between_rounds' | 'ablating' | 'halted';
export type V4NextAction = 'resume' | 'retry' | 'budget' | 'test' | 'review' | 'no_test';
export type V4Stage = 'pending' | 'built' | 'repair' | 'rebase' | 'smoke_pass' | 'minibatch_pass' | 'validated' | 'error' | 'smoke_fail' | 'minibatch_fail' | 'run_failed' | 'preflight_skipped' | 'measured';
export type V4Label = 'preflight_skipped' | 'implementation_failed' | 'smoke_fail' | 'minibatch_fail' | 'run_failed' | 'below_threshold' | 'qualified_not_adopted' | 'adopted' | 'safeguard_breached' | 'pruned';
export type V4Tier = 'confirmed' | 'unconfirmed' | 'suspicious';

export type V4SafeguardRow = { key: string; direction: string | null; subject: number | null; reference: number | null;
  tolerance: number | null; harm: number | null; breached: boolean | null };
export type V4Safeguards = { ok: boolean | null; breached: string[]; unobserved: string[]; rows: V4SafeguardRow[] };
export type V4Verdict = { pair: string; kind: 'candidate' | 'control' | null; effect: boolean | null; changed_side: 'A' | 'B' | null;
  side_effect: boolean | null; gaming: boolean | null; note: string | null };
export type V4Mechanism = {
  tier: V4Tier | null;
  mechanical: { status: 'changed' | 'unchanged' | 'not_computable' | null; candidate_rate: number | null; parent_rate: number | null;
    aa_gap: number | null; direction: string | null } | null;
  reviewer: { conclusion: 'effective' | 'suspicious' | 'none' | null; trusted: boolean | null; reason: string | null; effects: number | null;
    wrong_side: number | null; control_effects: number | null; gaming: string[]; side_effects: number | null; verdicts: V4Verdict[] };
};
export type V4Cache = { executions: number; wall_s: number; cases: number; cache_hits: number; cache_misses: number; lock_waited_s: number } | null;
export type V4Record = {
  card_index: number; title: string | null; card_ref: string | null; stage: V4Stage; label: V4Label | null; reason: string | null;
  carried_to: number | null; parent: string | null; attempt: number | null; repaired: boolean | null; pruned: boolean | null; halted: string | null; unmeasured: string | null;
  delta_mb: number | null; se_mb: number | null; n_mb: number | null; delta_val: number | null; se_val: number | null;
  se_own: number | null; se_floor: number | null; se_method: string | null; n_val: number | null; dropped_val: number | null;
  diff_lines: number | null; touches_preparation: boolean | null; timings: Record<string, number> | null; started_at: string | null; finished_at: string | null;
  candidate: string | null; candidate_id: string | null; smoke: string | null; smoke_reason: string | null;
  safeguards: V4Safeguards | null; mechanism: V4Mechanism | null;
  preflight?: { decision: string; reason: string; summary: string;
    checks: { kind: string; target: string; observation: string }[] } | null;
  qualification: { qualified: boolean; coefficient: number; threshold: number | null; reasons: string[] } | null;
  cache: { minibatch: V4Cache; rest: V4Cache };
};
export type V4Decision = { qualified: number[]; best: number | null; adopted: string | null; adopted_id: string | null;
  merge: { of: number[]; adopt: boolean; skipped: string | null; delta_val: number | null; se_val: number | null; candidate_id: string | null } | null };
export type V4Round = { round: number; parent: string | null; parent_id: string | null; started_at: string | null; finished_at: string | null;
  wall_s: number | null; analysis_s: number | null; decision_s: number | null; cards: number | null; cards_with_expectation: number;
  minibatch_size: number; rest_size: number; diagnosis_s: number | null; diagnosis_prefetched: boolean | null;
  timeboxed: boolean; not_started: number; open: boolean; records: V4Record[];
  queued: { card_index: number; title: string | null; has_expectation: boolean }[]; decision: V4Decision | null };
export type V4Node = { index: number; version: string; candidate_id: string | null; kind: 'seed' | 'candidate' | 'merge'; round: number | null;
  parent: string | null; delta_val: number | null; se_val: number | null; tier: V4Tier | null; elapsed_s: number | null;
  titles: (string | null)[]; pruned: boolean };
export type V4Ablation = { status: 'running' | 'done' | 'skipped'; reason: string | null; pruned: number[]; accepted: boolean | null;
  check: { delta: number | null; se: number | null; n: number } | null; final_id: string | null; wall_s: number | null;
  budget: { needed_s: number; remaining_s: number; measurements: number } | null;
  units: { index: number; round: number; kind: string; status: string; delta: number | null; se: number | null; n: number | null;
    prune: boolean; candidate_id: string | null }[] };
export type V4Calibration = { k_val: number; k_val_rule: string; rerun_var: number; task_var: number | null; aa_sd: number | null;
  aa_se: number | null; aa_delta: number | null; constant_fraction: number | null; seed_mean: number | null;
  infrastructure_failure_rate: number | null; sizes: Record<string, number | null>; walls: Record<string, number>; wall_s: number | null };
export type V4Feasibility = { stage_seconds: Record<string, number>; per_candidate_seconds: number; per_candidate_cold?: number;
  per_candidate_warm?: number; cache_note: string; candidates_in_budget: number | null; rounds_in_budget: number | null;
  test_half_width_95: number | null; test_n: number | null; warnings: string[] };
export type V4EndpointPoint = { index: number; candidate_id: string; kind: string | null; round: number | null; score: number | null;
  delta_test: number | null; ci95: [number, number] | null; ci95_method: string; delta_val_cumulative: number | null;
  shrinkage: number | null; tier: V4Tier | null };
export type V4Endpoint = { status: string; metric: string | null; n: number | null; k: number | null; interval_method: string;
  delta_test: number | null; ci95: [number, number] | null; confirmed: boolean; conclusion: string; deploy: string | null;
  points: V4EndpointPoint[]; warnings: string[] };
export type V4Progress = { rounds_finished: number; round: number | null; adopted: number; candidates: number;
  stage_counts: Record<string, number>; no_adoption: number; no_adoption_limit: number | null; run_failures: number;
  run_failures_limit: number | null; elapsed_s: number | null; budget_s: number | null; stop_reason: string | null;
  key_generation: number; search_size: number; validation_size: number; test_size: number | null; families: number | null };
export type V4RunView = {
  schema_version: number; method: 'v4';
  run: { method: 'v4'; run_id: string | null; iteration_id: string | null; actor: string | null; config_sha256: string | null;
    created_at: string | null; test_set: boolean; status: V4Status; alive: boolean; halt_reason?: string | null;
    started_at?: string | null; updated_at?: string | null };
  phase: V4Phase | null; next_action: V4NextAction | null; progress: V4Progress | null;
  config: Record<string, unknown> | null; variants: Record<string, { value: unknown; default: unknown }>;
  calibration: V4Calibration | null; feasibility: V4Feasibility | null; lineage: V4Node[]; rounds: V4Round[];
  ablation: V4Ablation | null; time_account: { total_wall_s: number; calibration_s: number | null;
    candidate_sums_s: Record<string, number>; ablation_s: number | null } | null;
  endpoint: V4Endpoint | null; deviations: string[];
};

export const isV4 = (view: unknown): view is V4RunView => !!view && (view as { method?: string }).method === 'v4';

export const v4StatusLabels: Record<V4Status, string> = { running: '运行中', interrupted: '已中断', halted: '已停机', not_started: '未开始' };
export const v4PhaseLabels: Record<V4Phase, string> = {
  calibrating: '校准：种子 A/A', analysing: '分析 Agent 诊断失败', building: '实现、冒烟、小批、全验证集、机制检查',
  deciding: '轮末决定', between_rounds: '准备下一轮', ablating: '消融剪枝', halted: '已停机',
};
// None of these means the room for improvement is used up (method-v4 §3.5).
export const v4HaltLabels: Record<string, string> = {
  time_budget: '时间预算用完', no_adoption: '连续多轮没有采纳', max_rounds: '达到轮数上限',
  process_failure: '流程故障', budget_exhausted: '花费预算用尽',
};
export const v4ActionTexts: Record<V4NextAction, { title: string; detail: string }> = {
  resume: { title: '进程已退出，运行没有结束', detail: '用启动时的同一条 v4 run 命令续跑。已决定的候选不会重做。' },
  retry: { title: '流程故障，等你修复', detail: '查清原因后，在 v4 run 命令后加 --retry-failed 续跑。' },
  budget: { title: '花费预算用尽，等你决定', detail: '增加预算后续跑，或就此结束，进入测试集评测。' },
  test: { title: '下一步：测试集评测（只做一次）', detail: '先运行 v4 test 预览节点集；经用户同意后再加 --confirm --budget <文件>。' },
  review: { title: '测试集已评测', detail: '结论和部署节点见下方“终点”。测试集已作废，下次运行需要新的测试集。' },
  no_test: { title: '这次运行没有测试集', detail: '用 --no-test-set 启动的演练，不做终点评测。' },
};
export const stageLabels: Record<V4Stage, string> = {
  pending: '等待实现', built: '等待冒烟', repair: '等待修复', rebase: '等待适配新父版本', smoke_pass: '等待小批', minibatch_pass: '等待全验证集', validated: '等待审查', error: '出错', smoke_fail: '冒烟未过',
  minibatch_fail: '小批淘汰', run_failed: '运行失败', preflight_skipped: '检查后跳过', measured: '已测完',
};
export const labelTexts: Record<V4Label, string> = {
  preflight_skipped: '检查后跳过', implementation_failed: '实现未完成',
  smoke_fail: '冒烟未过', minibatch_fail: '小批淘汰', run_failed: '运行失败', below_threshold: '未达门槛', qualified_not_adopted: '合格未采纳',
  adopted: '已采纳', safeguard_breached: '保护指标越界', pruned: '消融中被剪除',
};
export const tierLabels: Record<V4Tier, string> = { confirmed: '已确认', unconfirmed: '未确认', suspicious: '可疑' };
export const reasonTexts: Record<string, string> = {
  not_measurable: '没有可配对的题', not_positive: 'Δ_val 不为正', below_threshold: 'Δ_val 未达门槛',
  mechanism_suspicious: '机制可疑', safeguard_breached: '保护指标越界', safeguard_unobserved: '保护指标未观测到',
};
export const kindLabels: Record<string, string> = { seed: '种子', candidate: '候选', merge: '合并', pruned: '剪枝后' };
