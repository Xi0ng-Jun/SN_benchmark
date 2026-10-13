// Read-only projections from rag_eval.workbench.run_views (sn-workbench-run-views-v1).
// The page never recomputes official scores; it only renders saved values.

export type Ratio = { value: number | null; numerator: number | null; denominator: number | null; status: string; reason: string | null } | null;
export type RunState = { phase: string; terminal: boolean; updated_at: number | null };
export type Official = { status: 'available' | 'missing' | 'mismatch' | 'not_registered'; reason?: string; suite?: string; scope?: string;
  metrics?: Record<string, number>; metric_denominators?: Record<string, number>; pending_metrics?: string[]; batch_only_metrics?: string[];
  coverage?: Record<string, number | boolean>; primary_metric?: string | null; scores_id?: string; scorer_id?: string; bundle_id?: string;
  submission_id?: string; comparison_category?: string | null };
export type Stage = { metric_role: 'diagnostic'; gold_evidence_recall: Ratio; context_precision: Ratio; citation_precision: Ratio; refusal_rate: Ratio;
  calls: { scheduled: number | null; provider_logged: number | null; total_tokens: number | null; ask_latency_seconds: number | null; history_exhaustive: false };
  scheduled: { status: string; submission_count: number | null; terminal_count: number | null; unattributed_call_count: number | null;
    service_kind_counts: Record<string, number> | null; journal_pair_state: string | null } | null };
export type Provenance = {
  source: { sn_source_ref: string | null; source_format: string | null; head_revision: string | null; head_tree: string | null; selected_roots: string[] | null;
    product_archive_sha256: string | null; modified_paths: string[] | null; added_paths: string[] | null; deleted_paths: string[] | null };
  configuration: Record<string, string | null>;
  features: { status: 'not_projected' | 'recorded'; values: string[] | null; reason: string };
  model_services: { status: string; fingerprint: string | null; strict_bindings: boolean | null;
    services: { service_id: string; kind: string; protocol: string; model: string; generation: string }[]; limits: Record<string, string> };
  runtime_dependencies: Record<string, string | null> };
export type RunCase = { case_id: string; status: string; question: string | null; group_id: string | null;
  official: { state: string; metrics: Record<string, number> }; stage: Record<string, number | null>; needs_read: boolean; delta?: number | null };
export type RunView = { id: string; title: string; run_dir: string; task_id?: string | null; suite: string | null; mode: string | null; partition_id: string | null;
  state: RunState; progress: { planned: number; outputs: number; runtime_scores: number; status_counts: Record<string, number> };
  official: Official; stage: Stage | null; attention: { rule: string; count: number; case_ids: string[] };
  provenance: Provenance; ledger: { present: boolean; row: Record<string, unknown> | null }; warnings: string[]; cases?: RunCase[];
  status?: 'unreadable'; reason?: string };
export type RunList = { runs: RunView[]; ledger_only: { run_dir: string; suite: string; mode: string; state: string; official_score: unknown }[] };
export type CaseList = { run_id: string; metric: string | null; against: string | null; total: number; cases: RunCase[] };
export type Segment = { text: string; marks: ('gold' | 'cited' | 'retrieved')[]; labels: string[] };
export type Paper = { document_id: string; title: string | null; characters: number; text_sha256: string | null; first_gold: number | null;
  paragraphs: { start: number; end: number; marked: boolean; segments: Segment[] }[]; unmatched: { kind: string; label: string; excerpt: string }[] };
export type CaseDetail = { run_id: string; case_id: string; status: string; needs_read: boolean; question: string | null; answer: string | null;
  answer_reason: unknown; official: { state: string; metrics: Record<string, number>; primary_metric: string | null };
  stage: Record<string, number | null>; references: string[] | null;
  gold: { annotations: { answer: string | null; type: string | null; evidence: string[] }[] } | null; gold_role: string;
  papers: Paper[]; citations: { key: string; object_id: string; object_type: string; name: string | null; document_id: string | null;
    rank: number | null; contains_gold: boolean | null; snippet: string }[];
  retrieved: { rank: number; key: string | null; document_id: string | null; cited: boolean; contains_gold: boolean; excerpt: string }[];
  usage: { call_count: number | null; total_tokens: number | null; latency_seconds: number | null; coverage: string | null };
  evidence_snapshot: string | null };
export type Flip = 'better' | 'worse' | 'same' | 'unpaired';
export type FlipTable = { kind: 'paired' | 'not_admitted'; admission: 'admitted' | 'not_admitted'; reasons?: string[]; same_run: boolean; left_run: string; right_run: string; metric: string | null;
  counts: Record<Flip, number>; rows: { case_id: string; left: number | null; right: number | null; delta: number | null; flip: Flip }[]; note: string };
export type TaskOverview = { task_id: string; status: 'unbound' | 'idle' | 'running' | 'failed' | 'attention' | 'done'; pending: number; pending_hypotheses?: number;
  worktree: { worktree_path: string; branch: string | null; base_revision: string; paseo_workspace_id: string | null } | null; runs: string[] };
export type WorktreeFile = { path: string; preexisting_dirty: boolean | null; changed_since_start: boolean | null; private: boolean;
  binary: boolean; kind: string; diff: string | null; truncated: boolean; untracked?: boolean };
export type Hypothesis = { id: string; title: string; path: string; status: string };
export type TaskChanges = { task_id: string; status: 'available' | 'unbound'; source?: 'task_worktree' | 'run_views_fallback';
  read: { binding: { task_id: string | null; paseo_workspace_id: string | null; worktree_path: string; branch: string | null; base_revision: string;
    start_snapshot: Record<string, unknown> | null; created_at: string | null };
    current_head: string; current_branch?: string | null; files: WorktreeFile[]; file_count: number; files_truncated: boolean; start_flags?: string;
    observed_at?: string } | null;
  runs: { id: string; title: string; suite: string; mode: string; state: RunState; official: Record<string, number> | null; attention: number;
    match: string; sn_source_ref: string | null; head_revision: string | null }[];
  hypotheses: { status: 'available' | 'not_available'; items: Hypothesis[]; reason: string };
  verification: { stage: 'R1'; executable: boolean; capture_note: string; worktree_path: string; commands: { dry_run: string; run: string };
    budget: { note: string; estimate: string; reference_runs: { id: string; suite: string; mode: string }[]; fields: string[] } } | null };

export const short = (value: string | null | undefined, n = 8) => value ? value.length > n + 2 ? value.slice(0, n) : value : '未知';
export const caseLabel = (id: string) => { const [suite, rest] = id.split(':'); return rest ? `${suite}:${rest.slice(0, 8)}` : id.slice(0, 14); };
export const phaseLabels: Record<string, string> = { finished: '已完成', finished_with_errors: '完成（含错误）', failed: '失败', interrupted: '中断',
  cancelled: '已取消', timeout: '超时', running: '运行中', unknown: '状态未知' };
export const caseStatusLabels: Record<string, string> = { success: '已回答', no_answer: '拒答', clarification: '澄清', error: '错误', missing: '缺失',
  pending: '待生成', not_applicable: '不适用' };
export const stageLabels: Record<string, string> = { gold_evidence_recall: '证据召回', context_precision: '上下文精度', citation_precision: '引用精度',
  refusal_rate: '拒答率' };
export function diffCounts(diff: string | null): { additions: number; deletions: number } {
  let additions = 0, deletions = 0;
  for (const line of (diff ?? '').split('\n')) {
    if (line.startsWith('+') && !line.startsWith('+++')) additions++;
    else if (line.startsWith('-') && !line.startsWith('---')) deletions++;
  }
  return { additions, deletions };
}
export function runTone(run: Pick<RunView, 'state' | 'attention'>): 'running' | 'danger' | 'warning' | 'idle' {
  if (!run.state.terminal) return 'running';
  if (run.state.phase === 'failed' || run.state.phase === 'interrupted') return 'danger';
  return run.attention.count ? 'warning' : 'idle';
}

// Attention rules arrive as configured expressions; show them in words.
export const attentionRuleLabel = (rule: string) => rule
  .replace(/status\s*!=\s*success/g, '状态不是成功')
  .replace(/official\s+answer_f1/g, '官方 Answer F1')
  .replace(/\s+or\s+/g, ' 或 ').replace(/\s+and\s+/g, ' 且 ');
