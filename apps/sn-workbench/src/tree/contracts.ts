// Shapes served by iterations_api / comparisons / action_requests (design §4, §8).

export type Admission = { status: 'admitted' | 'not_admitted' | 'pending'; reason: string | null };
export type Measurement = { attempt?: number; execution_id: string; task_id: string; execution_state: string | null;
  run_dir: string | null; admission: Admission | null; created_at: string; created_by?: string; job_id?: string };
export type Decision = { decision: 'adopted' | 'rejected' | 'undecided'; note: string; actor: string; at: string };
export type Counts = { better: number; worse: number; same: number; unpaired: number };
// One side of a candidate's Δ父 / Δ根 as the tree view projects it.
export type DeltaSummary = { kind: string; status: string; reason?: string | null; comparison_id?: string | null;
  metric?: string | null; counts?: Counts; mean_delta?: number | null; regressed?: string[] };
export type CandidateState = 'provisioning' | 'implementing' | 'frozen' | 'running' | 'evaluated' | 'failed' | 'legacy';
export type Candidate = {
  id: string; iteration_id: string; parent_candidate_id: string | null; parent_commit: string | null;
  proposal_id: string | null; origin: string; status: string; frozen_commit: string | null; note: string | null;
  created_by: string; created_at: string; decision: Decision | null; state: CandidateState;
  attempts: (Measurement & { attempt: number })[]; links: Measurement[];
  worktree: { status: string; branch: string; base_revision: string; path: string | null } | null;
  reference: { attempt: number | null; execution_id: string } | null;
  depth?: number; comparisons?: { parent: DeltaSummary; root: DeltaSummary };
  primary?: { metric: string | null; value: number | null; run_id?: string; reason?: string } | null;
};
export type ProposalRow = { id: string; status: string; title: string | null; parent_candidate_id: string | null; created_at: string };
export type Iteration = {
  id: string; task_id: string; origin: string; title: string; start_commit: string | null; selection_hash: string | null;
  plan_id: string | null; plan_revision: string | null; root_candidate_id: string | null; created_at: string;
  protocol: { mode?: string; request_revision?: string; input?: { input_id?: string; bundle_id?: string; suite?: string } } | null;
};
export type IterationTree = Iteration & { candidates: Candidate[]; proposals: ProposalRow[]; unassigned_executions: string[] };

export type ComparisonRow = { case_id: string; left: number | null; right: number | null; delta: number | null; flip: keyof Counts; partition: number };
export type ComparisonSideRef = { candidate_id: string; attempt: number | null; execution_id: string; runs: string[] };
export type ComparisonRecord = {
  id?: string; kind: string; status?: string; reason?: string | null; comparison_id?: string;
  metric?: string | null; subject?: ComparisonSideRef; reference?: ComparisonSideRef; reference_candidate_id?: string;
  admission?: Admission; counts?: Counts; rows?: ComparisonRow[]; regressed?: string[];
  mean?: { reference: number | null; subject: number | null; delta: number | null }; created_at?: string; stored?: boolean;
};
export type CandidateComparisons = { candidate_id: string; parent: ComparisonRecord; root: ComparisonRecord };

export type Prediction = { case_id: string; expect: string; guard?: boolean; note?: string };
export type ProposalCard = { title: string; component?: string; mechanism?: string; predictions: Prediction[];
  expected_benefit?: string; possible_harm?: string; falsification?: string; change_scope?: string[]; implementer_brief?: string;
  evidence?: { run_id: string; case_id: string; note?: string }[]; alternatives?: string[] };
export type Verification = { comparison_id?: string; metric: string; admission: string; held: number; decided: number; total: number;
  results: { case_id: string; expect: string; guard?: boolean; delta: number | null; held: boolean | null; reason?: string | null }[];
  leak?: { status: string } };
export type Proposal = { id: string; task_id: string; status: string; card: ProposalCard; author: string;
  verification: Verification | null; conclusion: { effect: string; cause: string; note: string } | null;
  parent_candidate_id: string | null; created_at: string };

export type ActionRequest = { id: string; task_id: string; verb: string; target_id: string | null; params: Record<string, unknown>;
  reason: string; requested_by: string; status: 'pending' | 'confirmed' | 'executed' | 'failed' | 'rejected' | 'withdrawn';
  decided_by: string | null; decided_at: string | null; decision_note: string | null; result: Record<string, unknown> | null; created_at: string };

export type ChangedFile = { path: string; kind: string; binary: boolean; private: boolean; diff: string | null; truncated: boolean };
export type CandidateChanges = { binding: unknown; current_head?: string; diff_stat?: string; files?: ChangedFile[]; file_count?: number; files_truncated?: boolean };

export const stateLabels: Record<CandidateState, string> = {
  provisioning: '准备中', implementing: '实现中', frozen: '已冻结', running: '运行中', evaluated: '已评测', failed: '未通过准入', legacy: '遗留',
};
export const decisionLabels: Record<string, string> = { adopted: '已采纳', rejected: '已放弃', undecided: '未决' };
export const verbLabels: Record<string, string> = {
  'node-derive': '派生', 'node-freeze': '冻结', 'node-run': '运行', 'node-adopt': '采纳', 'node-reject': '放弃', 'iteration-create': '新建迭代',
};
export const requestStatusLabels: Record<ActionRequest['status'], string> = {
  pending: '待确认', confirmed: '执行中', executed: '已执行', failed: '执行失败', rejected: '已驳回', withdrawn: '已撤回',
};
export const short = (id: string | null | undefined, n = 8) => id ? id.slice(0, n) : '—';
export const signed = (value: number | null | undefined, digits = 3) =>
  typeof value === 'number' && Number.isFinite(value) ? (value > 0 ? '+' : value < 0 ? '−' : '±') + Math.abs(value).toFixed(digits) : '—';
// Δ根 of a child of the root is its Δ父 record.
export const rootSide = (c: Candidate): DeltaSummary | undefined =>
  c.comparisons?.root.status === 'same_as_parent' ? c.comparisons.parent : c.comparisons?.root;
