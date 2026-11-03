import { date } from '../contracts';

// The projection served by GET /api/iterations/{id}/auto-run (optimizer/run_view.py).
// One pre-registered safeguard on one pair (SN: the iteration's v2 safeguards, v2's rule).
export type SafeguardRow = { key: string; direction?: 'higher_is_better' | 'lower_is_better'; margin: number;
  reference?: number | null; subject: number | null; change?: number | null; breached: boolean | null };
// v3 infrastructure count: an attempt that died before publishing per-case results counts its planned cases as failed.
export type InfraAttempt = { execution_id: string; scope: string | null; state: string; total: number; failed: number;
  unobserved: boolean; counted_from_plan: boolean; reason?: string };
export type InfraSide = { total: number; failed: number; unobserved: number; attempts: InfraAttempt[] };
export type SafeguardCheck = { source: string; ok: boolean; reasons: string[]; checks: SafeguardRow[];
  infrastructure?: { subject: InfraSide; reference: InfraSide } };
export type SafeguardsInForce = { source: string; specs?: { key: string; direction: string; margin: number }[]; bounds?: Record<string, number>;
  infrastructure_rule?: string };
export type AttemptVerdict = 'screen_pass' | 'screen_fail' | 'smoke_fail' | 'unmeasurable' | 'stale_parent' | 'error';
export type AutoAttempt = {
  key: string; round_id: number; card_index: number; card_ref: string | null; title: string | null; verdict: AttemptVerdict; reason: string | null;
  parent: string | null; candidate: string | null; candidate_id: string | null; rebased_from: string | null; smoke_verdict: string | null;
  delta_val: number | null; se_val: number | null; lower_val: number | null; score_val: number | null; best_before: number | null;
  threshold: number | null; ladder_m: number | null; val_pool_size: number | null; n_unpaired: number | null; screen_evaluations: number | null;
  timings: Record<string, number> | null; has_predicate: boolean; resumed: boolean; halted: boolean;
  safeguards: SafeguardCheck | null;
};
export type AutoAttribution = { label: 'supported' | 'refuted' | 'insufficient' | 'unattributed'; possibly_biased?: boolean; reason?: string | null;
  predicted_direction?: string; r_p?: number | null; r_c?: number | null; delta_S?: number | null; delta_notS?: number | null;
  slice_size?: number; not_slice_size?: number; n_not_computable?: number; n_min?: number };
export type ConfirmationVerdict = 'confirmed' | 'rejected' | 'insufficient' | 'unmeasurable';
export type AutoConfirmation = {
  round_id: number; verdict: ConfirmationVerdict; delta_fresh: number | null; se_fresh: number | null; lower: number | null; upper: number | null;
  n_fresh: number | null; n_unpaired: number | null; k_fresh: number | null; fresh_batch_index: number | null; segment_steps: number | null;
  cumulative_delta_val: number | null; shrinkage: number | null; safeguards_end: SafeguardCheck | null; cost_start: number | null; cost_end: number | null;
  cost_change: number | null; attribution: AutoAttribution | null; val_pool_size: number | null; ladder_m: number | null; best_score: number | null;
};
export type NodeState = 'certified' | 'provisional' | 'rolled_back';
export type AutoNode = { version_hash: string; candidate_id: string | null; state: NodeState; card_ref: string | null; created_at: string | null;
  agent_cost: number | null; has_predicate: boolean; title: string | null; attempt: string | null; delta_val: number | null };
export type AutoSegment = { index: number; status: 'certified' | 'rolled_back' | 'open'; start: string | null; end: string | null; nodes: AutoNode[]; confirmation: AutoConfirmation | null };
export type Phase = 'baseline' | 'analysing' | 'building' | 'confirming' | 'refreshing_best' | 'between_rounds';
export type AutoProgress = {
  phase: Phase; round: number; rounds_finished: number; milestones_certified: number; fresh_used: number; fresh_total: number;
  confirmations_used: number; max_confirmations: number | null; no_screen_streak: number; no_screen_limit: number; rejected_streak: number; rejected_limit: number;
  run_failures: number; run_failures_limit: number | null; cumulative_delta_val: number | null; T: number | null; ladder_m: number | null; best_score: number | null;
  key_generation: number; search_size: number; validation_size: number; validation_initial: number;
};
export type AutoCard = { index: number; ref: string | null; title: string | null; has_predicate: boolean; parent: string | null;
  status: 'inflight' | 'decided' | 'submitted' | 'queued'; attempt?: AutoAttempt };
export type AutoOpenRound = { round_id: number; started_at: string | null; phase: Phase; cards_generated: number; smoke_passed: number; screen_passed: number;
  milestone_triggered: boolean; confirmation_verdict: ConfirmationVerdict | null; halt_reason: string | null; cards: AutoCard[] };
export type AutoRound = { round_id: number; started_at: string | null; finished_at: string | null; cards_generated: number; smoke_passed: number; screen_passed: number;
  milestone_triggered: boolean; confirmation_verdict: ConfirmationVerdict | null; rolled_back: boolean; attempts: AutoAttempt[] };
export type AutoLedger = {
  utility: { n_certified_milestones?: number };
  selection: { n_evaluated_on_validation_pool?: number; ladder_blocked?: number; provisional_adopted?: number; provisional_rolled_back?: number;
    share_provisional_rolled_back?: number | null; verdict_counts?: Record<string, number> };
  mechanism: { n_certified_milestones?: number; n_certified_supported?: number; n_certified_supported_possibly_biased?: number;
    predicate_attachment?: { cards_generated: number; cards_with_predicate: number; rate: number | null; floor: number; meets_floor: boolean } };
  integrity: { n_attempts_recorded?: number; n_halted_attempts?: number; n_aborted_attempts_retried?: number; cards_generated?: number; cards_not_attempted?: number; n_confirmation_attempts?: number };
};
export type SealedPoint = { index?: number; candidate_id: string; state?: NodeState; style?: 'solid' | 'hollow' | 'dashed'; status?: string; score?: number | null; se?: number | null;
  delta_vs_seed?: number | null; lower_adj?: number | null; upper_adj?: number | null; p_holm?: number | null; holds_holm?: boolean | null };
// Round x sealed score (optimizer/curve_view.py). y_lower/y_upper = seed score + the corrected bounds on Δ vs the seed.
export type CurvePoint = { index: number; version_hash: string; candidate_id: string | null; state: NodeState | null; style?: string | null;
  status?: string | null; score: number | null; delta_vs_seed: number | null; lower_adj: number | null; upper_adj: number | null;
  p_holm: number | null; holds_holm: boolean | null; parent_hash: string | null; parent_index: number | null; round: number; round_inferred: boolean;
  on_path: boolean; slot: number; slots: number; y_lower: number | null; y_upper: number | null; attempt: string | null; title: string | null };
export type SealedCurveView = { metric: string | null; n: number | null; k: number | null; m?: number | null; correction: string | null;
  seed_score: number | null; max_round: number; path: number[]; points: CurvePoint[] };
export type SealedCurve = { status: string; metric?: string | null; n?: number | null; k?: number; correction?: string; points: SealedPoint[];
  curve?: SealedCurveView | null };
export type RunStatus = 'running' | 'interrupted' | 'halted' | 'not_started';
export type NextAction = 'resume' | 'retry' | 'budget' | 'sealed' | 'review';
export type AutoRunView = {
  run: { run_id: string | null; iteration_id: string | null; actor: string | null; protocol_sha256: string | null; created_at: string | null;
    status: RunStatus; alive: boolean; halt_reason?: string | null; started_at?: string | null; updated_at?: string | null };
  next_action: NextAction | null; progress: AutoProgress | null; config: Record<string, unknown> | null;
  safeguards: SafeguardsInForce | null;
  seed: { version_hash: string; candidate_id: string | null; state: NodeState } | null;
  segments: AutoSegment[]; open_round: AutoOpenRound | null; rounds: AutoRound[]; ledger: AutoLedger | null; sealed: SealedCurve | null;
};

export const statusLabels: Record<RunStatus, string> = { running: '运行中', interrupted: '已中断', halted: '已停机', not_started: '未开始' };
export const phaseLabels: Record<Phase, string> = {
  baseline: '测量基线', analysing: '分析 Agent 诊断失败', building: '实现、冒烟、筛选候选', confirming: '在新鲜批次上确认里程碑',
  refreshing_best: '在扩大后的验证池上重测 best', between_rounds: '准备下一轮',
};
// None of these means the room for improvement is used up (method-v3 §2.5).
export const haltLabels: Record<string, string> = {
  fresh_exhausted: '新鲜批次用完', no_improvement: '连续两次里程碑确认不成立', no_candidates: '连续两轮没有候选通过筛选',
  max_confirmations: '达到确认次数上限', max_rounds: '达到轮数上限', budget_exhausted: '预算用尽', process_failure: '流程故障',
};
export const actionTexts: Record<NextAction, { title: string; detail: string }> = {
  resume: { title: '进程已退出，运行没有结束', detail: '用启动时的同一条命令续跑。已决定的卡片不会重做，也不会重复花费新鲜批次。' },
  retry: { title: '流程故障，等你修复', detail: '查清原因后，在启动命令后加 --retry-failed 续跑。' },
  budget: { title: '预算用尽，等你决定', detail: '增加预算后用同一条命令续跑，或就此结束，进入封存集评测。' },
  sealed: { title: '下一步：封存集评测', detail: '用命令行 sealed 子命令评测整条谱系。封存集只能用一次，先不带 --confirm 看一遍节点集。' },
  review: { title: '下一步：复核并选定部署节点', detail: '复核已确认但未归因的改动。方法主张只挂在已确认节点上。' },
};
export const segmentVerdictLabels: Record<ConfirmationVerdict, string> = { confirmed: '确认成立', rejected: '确认不成立', insufficient: '证据不足', unmeasurable: '不可测' };
export const attemptLabels: Record<AttemptVerdict, string> = {
  screen_pass: '暂定采纳', screen_fail: '筛选未过', smoke_fail: '冒烟未过', unmeasurable: '不可测', stale_parent: '父节点已变', error: '出错',
};
export const attributionLabels: Record<AutoAttribution['label'], string> = { supported: '支持', refuted: '反驳', insufficient: '证据不足', unattributed: '未归因' };
export const nodeStateLabels: Record<NodeState, string> = { certified: '已确认', provisional: '暂定', rolled_back: '已回滚' };

// Why an attempt ended where it did, in the terms of the screening rule.
export function attemptWhy(a: AutoAttempt): string {
  if (a.halted) return '停机时在途，续跑后重做';
  if (a.verdict === 'screen_fail') return a.lower_val !== null && a.lower_val > 0 ? '分数未超过 best + m' : '下界未过零';
  if (a.verdict === 'smoke_fail') return a.has_predicate ? '行为没有按预测变化' : '候选没有跑通';
  if (a.verdict === 'screen_pass') return a.smoke_verdict === 'pass' && a.has_predicate ? '通过筛选' : '通过筛选（行为未检查）';
  if (a.verdict === 'stale_parent') return '父节点已变，rebase 冲突';
  if (a.verdict === 'unmeasurable') {
    const reasons = a.safeguards && !a.safeguards.ok ? a.safeguards.reasons : [];
    if (reasons.length) return reasons.map(r => (r.startsWith('safeguard_breached:') ? '保护指标越界：' : '保护指标未观测到：') + r.slice(r.indexOf(':') + 1)).join('；');
    return '可配对的题太少';
  }
  return a.reason ?? '—';
}

export function ago(iso: string | null | undefined): string {
  if (!iso) return '—';
  const seconds = (Date.now() - Date.parse(iso)) / 1000;
  return seconds < 90 ? '刚刚' : seconds < 5400 ? `${Math.round(seconds / 60)} 分钟前` : seconds < 172800 ? `${Math.round(seconds / 3600)} 小时前` : date(iso);
}
export const num = (value: number | null | undefined, digits = 3) => typeof value === 'number' && Number.isFinite(value) ? value.toFixed(digits) : '—';
export const duration = (seconds: number | null | undefined) => typeof seconds !== 'number' ? '—' : seconds < 90 ? `${Math.round(seconds)} 秒` : `${Math.round(seconds / 60)} 分钟`;
