import React, { useEffect, useRef, useState } from 'react';
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

// The ledger is rebuilt from every execution of the iteration; the page's 4 s
// poll re-reads it at most every 30 s.
const LEDGER_REFRESH_MS = 30000;

export function LedgerPanel({ iterationId, refresh }: { iterationId: string; refresh: number }) {
  const [slow, setSlow] = useState(0);
  const last = useRef(Date.now());
  useEffect(() => {
    if (Date.now() - last.current < LEDGER_REFRESH_MS) return;
    last.current = Date.now();
    setSlow(n => n + 1);
  }, [refresh]);
  const state = useResource<Ledger>(`/iterations/${iterationId}/ledger`, slow);
  const data = state.data;
  const budget = data?.sampling_ledger.budget_usage;
  return <Section title="账本" defaultOpen={true} testID="iteration-ledger">
    {state.error ? <Copy small>{state.error}</Copy> : !data ? <Copy small muted>读取中…</Copy> : <View style={{ gap: 6 }}>
      {budget?.policy && <View style={{ gap: 2 }}>
        <Copy small>题次 {num(budget.case_runs_used)} / {budget.policy.max_case_runs ?? '∞'} · 每候选 ≤ {budget.policy.max_runs_per_candidate}</Copy>
        {budget.policy.limit_cny !== undefined && <Copy small>CNY {num(budget.charged_cny)} / {num(budget.policy.limit_cny)}{budget.actual_cny !== null ? ` · 实测 ${num(budget.actual_cny)}` : ''}</Copy>}
      </View>}
      <Copy small>η = {num(data.sampling_ledger.S_B)} · τ = {num(data.structure_ledger.contracts[0]?.rho ?? null)} · k = {data.sampling_ledger.k}</Copy>
      <Copy small>{data.structure_ledger.wishlist.length} 个开放簇 · audit {data.structure_ledger.audit_count}</Copy>
      {data.overfit_ledger.accepted.length > 0 && <Section title="已通过门" defaultOpen={false}>
        {data.overfit_ledger.accepted.map(row => <View key={row.candidate_id} style={{ gap: 2 }}>
          <Mono size={11}>{row.candidate_id.slice(0, 8)} · validation Δ {num(row.validation.delta)}</Mono>
          {row.fresh.map((b, i) => <Copy small key={`${b.batch_id}-${i}`}>{b.batch_id}：Δ {num(b.validation_delta)} / fresh {num(b.delta)} / 偏差 {num(b.observed_bias)}{b.reason ? ` (${b.reason})` : ''}</Copy>)}
        </View>)}
      </Section>}
      <Section title="采样 / 过拟合 / 结构" defaultOpen={false}>
        <Copy small>k = {data.sampling_ledger.k} · verifier = {data.sampling_ledger.verifier} · S(B) = {num(data.sampling_ledger.S_B)}</Copy>
        <Copy small>n = {num(data.overfit_ledger.n)} · n_eff = {num(data.overfit_ledger.n_eff)} · K = {num(data.overfit_ledger.K)} · η = {num(data.overfit_ledger.eta)} · 期望偏差 = {num(data.overfit_ledger.expected_bias)}</Copy>
        <Copy small muted>validation 池：{pool(data.overfit_ledger.pool)}</Copy>
        {data.structure_ledger.clusters.map(c => <Copy small key={c.cluster_id}>{c.cluster_id} · {c.status} · q/m/v = {num(c.q)}/{num(c.m)}/{num(c.v)}</Copy>)}
        {data.structure_ledger.contracts.map((c, i) => <Copy small key={`${c.candidate_id}-${i}`}>合同 {c.candidate_id?.slice(0, 8) ?? '未派生'} · ρ {num(c.rho)} · {c.verdict ?? '未判定'}</Copy>)}
        <Copy small>sealed：{data.sealed_score ? `${data.sealed_score.status} · ${data.sealed_score.metric ?? '—'} · n=${num(data.sealed_score.n)} · ${num(data.sealed_score.mean)}` : '未调用'}</Copy>
      </Section>
    </View>}
  </Section>;
}
