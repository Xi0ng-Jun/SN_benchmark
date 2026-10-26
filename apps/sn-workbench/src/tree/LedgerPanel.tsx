import React from 'react';
import { View } from 'react-native';
import { useResource } from '../contracts';
import { Copy } from '../ui';
import { Mono, Section } from './parts';

type Pool = { batch_id: string; selection_hash: string | null; n: number };
type BudgetUsage = {
  policy: { max_case_runs?: number; limit_cny?: number; max_runs_per_candidate: number } | null;
  case_runs_used: number; charged_cny: number; actual_cny: number | null;
};
type Ledger = {
  sampling_ledger: { k: number; verifier: string; seed_search_mean: number | null; S_B: number | null; budget_usage?: BudgetUsage | null };
  overfit_ledger: { n: number | null; n_eff: number | null; K: number | null; eta: number | null;
    expected_bias: number | null; pool: Pool[] | null; accepted: Array<{ candidate_id: string;
      validation: { delta: number | null; pool: Pool[] | null }; fresh: Array<{ batch_id: string;
        delta: number | null; validation_delta: number | null; observed_bias: number | null; reason?: string }> }> };
  structure_ledger: { audit_count: number; clusters: Array<{ cluster_id: string; status: string; q: number | null; m: number | null; v: number | null }>;
    contracts: Array<{ candidate_id: string | null; rho: number | null; verdict: string | null }>; wishlist: Array<{ cluster_id: string }> };
  sealed_score: { status: string; metric: string | null; n: number | null; mean: number | null } | null;
};
const num = (n: number | null | undefined) => typeof n === 'number' ? Number(n.toFixed(4)).toString() : '—';
const pool = (p: Pool[] | null) => p?.map(b => `${b.batch_id} (${b.n}, ${b.selection_hash?.slice(0, 8) ?? '—'})`).join(' + ') ?? '未知';

export function LedgerPanel({ iterationId, refresh }: { iterationId: string; refresh: number }) {
  const state = useResource<Ledger>(`/iterations/${iterationId}/ledger`, refresh);
  const data = state.data;
  const budget = data?.sampling_ledger.budget_usage;
  return <Section title="三笔账" defaultOpen={false} testID="iteration-ledger">
    {state.error ? <Copy small>{state.error}</Copy> : !data ? <Copy small muted>正在读取账本…</Copy> : <View style={{ gap: 10 }}>
      <Copy small>采样：k = {data.sampling_ledger.k}；verifier = {data.sampling_ledger.verifier}；种子 search 均值 / S(B) = {num(data.sampling_ledger.S_B)}</Copy>
      {budget?.policy && <Copy small>预算：题次 {num(budget.case_runs_used)} / {budget.policy.max_case_runs ?? '未设上限'}
        {budget.policy.limit_cny !== undefined ? `；CNY 占用 ${num(budget.charged_cny)} / ${num(budget.policy.limit_cny)}（实测 ${num(budget.actual_cny)}）` : ''}
        {`；每候选最多 ${budget.policy.max_runs_per_candidate} 次运行`}</Copy>}
      <Copy small>过拟合：n = {num(data.overfit_ledger.n)}；n_eff = {num(data.overfit_ledger.n_eff)}；K = {num(data.overfit_ledger.K)}；η = {num(data.overfit_ledger.eta)}；期望偏差 = {num(data.overfit_ledger.expected_bias)}</Copy>
      <Copy small muted>validation 池：{pool(data.overfit_ledger.pool)}</Copy>
      {data.overfit_ledger.accepted.map(row => <View key={row.candidate_id} style={{ gap: 4 }}>
        <Mono size={11}>{row.candidate_id.slice(0, 8)} · validation Δ {num(row.validation.delta)} · {pool(row.validation.pool)}</Mono>
        {row.fresh.map((b, i) => <Copy small key={`${b.batch_id}-${i}`}>{b.batch_id}：validation Δ {num(b.validation_delta)} / fresh Δ {num(b.delta)} / 配对偏差 {num(b.observed_bias)}{b.reason ? ` (${b.reason})` : ''}</Copy>)}
      </View>)}
      <Copy small>结构：audit {data.structure_ledger.audit_count}；愿望清单 {data.structure_ledger.wishlist.length} 个 open 簇</Copy>
      {data.structure_ledger.clusters.map(c => <Copy small key={c.cluster_id}>{c.cluster_id} · {c.status} · q/m/v = {num(c.q)} / {num(c.m)} / {num(c.v)}</Copy>)}
      {data.structure_ledger.contracts.map((c, i) => <Copy small key={`${c.candidate_id}-${i}`}>合同 {c.candidate_id?.slice(0, 8) ?? '未派生'} · ρ {num(c.rho)} · {c.verdict ?? '未判定'}</Copy>)}
      <Copy small>sealed：{data.sealed_score ? `${data.sealed_score.status} · ${data.sealed_score.metric ?? '—'} · n=${num(data.sealed_score.n)} · ${num(data.sealed_score.mean)}` : '未调用'}。整个研究只此一次。</Copy>
    </View>}
  </Section>;
}
