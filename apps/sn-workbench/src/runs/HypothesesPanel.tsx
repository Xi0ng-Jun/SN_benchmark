import React, { useState } from 'react';
import { Text, View } from 'react-native';
import { Lightbulb } from 'lucide-react-native';
import { api } from '../contracts';
import { useResource } from '../materials';
import { Button, Copy, fmt, Input, Pill, s, useTheme } from '../ui';

export type HypothesisPrediction = { case_id: string; expect: string; metric: string | null; note: string | null; guard: boolean };
export type HypothesisCard = {
  id: string; task_id: string; status: string; author: string; created_at: string;
  card: { title: string; component: string; mechanism: string; falsification: string; implementer_brief: string;
    expected_benefit?: string; possible_harm?: string; alternatives: string[]; change_scope: string[];
    evidence: { run_id: string; case_id: string; note?: string }[]; predictions: HypothesisPrediction[] };
  decision: { decision: string; actor: string; note: string } | null;
  verification: { baseline_run: string; candidate_run: string; metric: string; held: number; decided: number; total: number;
    leak: { status: string; findings: { kind: string; case_id?: string; source: string }[] } | null;
    results: { case_id: string; expect: string; guard: boolean; delta: number | null; left: number | null; right: number | null; held: boolean | null; reason: string | null }[] } | null;
  conclusion: { effect: string; cause: string; note: string } | null;
  worktree?: { status: string; branch: string; base_revision: string; detail: string | null;
    binding: { worktree_path: string; paseo_workspace_id: string } | null } | null;
};

const statusLabels: Record<string, string> = {
  draft: '草稿·待你批准', approved: '已批准·待实现', rejected: '已驳回', implementing: '实现中', verified: '已核对预测·待结论', concluded: '已结论',
};
const expectLabels: Record<string, string> = { improve: '变好', unchanged: '不变', not_worse: '不变差', worse: '变差' };
const effectLabels: Record<string, string> = { supported: '支持改善', not_observed: '未观察到改善', regression_or_tradeoff: '退化或取舍', insufficient: '证据不足' };
const causeLabels: Record<string, string> = { supported: '原因得到支持', refuted: '原因被反证', undecided: '原因未决' };
const short = (id: string) => id.split(':').pop()!.slice(0, 8);

export type HypothesesPanelProps = {
  taskId: string;
  runs: { id: string; title: string }[];
  profiles: { id: string; label: string }[];
  implementers: Record<string, string>;   // hypothesis id -> agent label
  canCreateImplementer: string | null;    // null when allowed, else the reason
  onStartImplementer: (hypothesisId: string, profileId: string) => void;
  onOpenCase: (runId: string, caseId: string) => void;
  refreshKey: number;
  canCreateWorktree?: boolean;
  activeWorktree?: string;
  onWorktreeChanged?: () => void;
};

/** Cards drafted by the analyst Agent (or a person); a person approves, starts the implementer and concludes. */
export function HypothesesPanel({ taskId, runs, profiles, implementers, canCreateImplementer, onStartImplementer, onOpenCase, refreshKey,
  canCreateWorktree = false, activeWorktree, onWorktreeChanged }: HypothesesPanelProps) {
  const theme = useTheme();
  const [tick, setTick] = useState(0);
  const cards = useResource<HypothesisCard[]>(`/tasks/${encodeURIComponent(taskId)}/hypotheses`, refreshKey + tick);
  if (cards.error) return <Copy small style={{ color: theme.error }}>{cards.error}</Copy>;
  if (!cards.data) return <Copy small muted>正在读取假设卡…</Copy>;
  if (!cards.data.length) return <View testID="hypotheses-empty" style={{ padding: 12, borderWidth: 1, borderStyle: 'dashed', borderColor: theme.line, borderRadius: 8, gap: 4 }}>
    <Text style={{ fontSize: 13, color: theme.text }}>还没有假设卡</Text>
    <Text style={{ fontSize: 12, lineHeight: 18, color: theme.muted }}>在“工作”页让分析 Agent 读错题并提交假设卡草稿（它用 sn-eval hypothesis-draft）。草稿出现在这里，由你批准后才会新建实现 Agent。</Text>
  </View>;
  return <View testID="hypotheses" style={{ gap: 12 }}>
    {cards.data.map(card => <Card key={card.id} card={card} runs={runs} profiles={profiles} implementer={implementers[card.id]}
      canCreateImplementer={canCreateImplementer} onStartImplementer={onStartImplementer} onOpenCase={onOpenCase}
      canCreateWorktree={canCreateWorktree} activeWorktree={activeWorktree} onWorktreeChanged={onWorktreeChanged}
      taskId={taskId} onChanged={() => setTick(n => n + 1)} />)}
  </View>;
}

function Card({ card, taskId, runs, profiles, implementer, canCreateImplementer, onStartImplementer, onOpenCase, onChanged,
  canCreateWorktree, activeWorktree, onWorktreeChanged }: {
  card: HypothesisCard; taskId: string; runs: { id: string; title: string }[]; profiles: { id: string; label: string }[]; implementer?: string;
  canCreateImplementer: string | null; onStartImplementer: (id: string, profile: string) => void; onOpenCase: (runId: string, caseId: string) => void; onChanged: () => void;
  canCreateWorktree: boolean; activeWorktree?: string; onWorktreeChanged?: () => void;
}) {
  const theme = useTheme();
  const c = card.card;
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [baseline, setBaseline] = useState<string | null>(null);
  const [candidate, setCandidate] = useState<string | null>(null);
  const [effect, setEffect] = useState<string | null>(null);
  const [cause, setCause] = useState<string | null>(null);
  const call = async (path: string, body: unknown) => {
    setBusy(true); setError(null);
    try { await api(`/tasks/${encodeURIComponent(taskId)}/hypotheses/${card.id}/${path}`, body); onChanged();
      if (path.startsWith('worktree')) onWorktreeChanged?.(); }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  const v = card.verification;
  const results = new Map((v?.results ?? []).map(r => [r.case_id, r]));
  return <View testID="hypothesis-card" style={{ borderWidth: 1, borderColor: theme.line, borderRadius: 10, padding: 14, gap: 10 }}>
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
      <Lightbulb size={14} strokeWidth={1.6} color={theme.warning} />
      <Text style={{ fontSize: 14, fontWeight: '600', color: theme.text, flexShrink: 1 }}>{c.title}</Text>
      <Pill label={statusLabels[card.status] ?? card.status} tone={card.status === 'draft' || card.status === 'verified' ? 'warning' : undefined} />
      <Text style={{ fontSize: 11, color: theme.muted }}>{card.author.startsWith('agent:') ? '分析 Agent 起草' : '人工起草'}</Text>
    </View>
    <Field label="组件" value={c.component} />
    <Field label="机制" value={c.mechanism} />
    {!!c.alternatives.length && <Field label="替代解释" value={c.alternatives.join('；')} />}
    {c.expected_benefit && <Field label="预期受益" value={c.expected_benefit} />}
    {c.possible_harm && <Field label="可能受害" value={c.possible_harm} />}
    <Field label="否定条件" value={c.falsification} />
    {!!c.evidence.length && <View style={{ gap: 2 }}>
      <Text style={{ fontSize: 11, color: theme.muted }}>证据</Text>
      {c.evidence.map((e, i) => <View key={i} style={s.row}>
        <Button label={`${short(e.case_id)} · ${e.run_id.slice(0, 8)}`} onPress={() => onOpenCase(e.run_id, e.case_id)} />
        {e.note && <Text style={{ fontSize: 12, color: theme.text, flexShrink: 1 }}>{e.note}</Text>}
      </View>)}
    </View>}
    <View style={{ gap: 2 }}>
      <Text style={{ fontSize: 11, color: theme.muted }}>逐题预测（验证时逐条核对，不看均分）</Text>
      {c.predictions.map(p => { const r = results.get(p.case_id); return <View key={p.case_id} testID="hypothesis-prediction" style={{ flexDirection: 'row', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <Text style={{ fontSize: 12, color: theme.text, fontVariant: ['tabular-nums'] }}>{short(p.case_id)}</Text>
        <Pill label={(p.guard ? '守护 · ' : '') + (expectLabels[p.expect] ?? p.expect)} />
        {p.note && <Text style={{ fontSize: 12, color: theme.muted, flexShrink: 1 }}>{p.note}</Text>}
        {r && <Text style={{ fontSize: 12, color: r.held === true ? theme.success : r.held === false ? theme.danger : theme.muted }}>
          {r.held === null ? `无法判定：${r.reason}` : `${r.held ? '兑现' : '未兑现'} · ${fmt(r.left)} → ${fmt(r.right)}`}</Text>}
      </View>; })}
    </View>
    <View style={{ gap: 2 }}>
      <Text style={{ fontSize: 11, color: theme.muted }}>交给实现 Agent 的说明（只有这段和组件、机制、改动范围会给它）</Text>
      <Text selectable style={{ fontSize: 13, lineHeight: 20, color: theme.text }}>{c.implementer_brief}</Text>
      {!!c.change_scope.length && <Text style={{ fontSize: 12, color: theme.muted, fontFamily: 'monospace' }}>{c.change_scope.join('  ')}</Text>}
    </View>
    {card.decision?.note ? <Field label={card.decision.decision === 'approve' ? '批准备注' : '驳回理由'} value={card.decision.note} /> : null}

    {card.status === 'draft' && <View style={{ gap: 6 }}>
      <Input label="备注（可选）" value={note} onChange={setNote} placeholder="批准或驳回的理由" testID="hypothesis-note" />
      <View style={s.row}>
        <Button label="批准" primary disabled={busy} onPress={() => call('decision', { decision: 'approve', note })} testID="hypothesis-approve" />
        <Button label="驳回" disabled={busy} onPress={() => call('decision', { decision: 'reject', note })} testID="hypothesis-reject" />
      </View>
    </View>}

    {(card.status === 'approved' || card.status === 'implementing') && <View style={{ gap: 6 }}>
      {card.worktree?.status === 'ready' ? <View style={{ gap: 4 }}>
        <Copy small>{`独立改动：${card.worktree.branch} · 起点 ${card.worktree.base_revision.slice(0, 8)}`}</Copy>
        {activeWorktree === card.worktree.binding?.worktree_path ? <Copy small muted>当前更改页已选中此改动</Copy>
          : <Button label="设为当前改动" disabled={busy} onPress={() => call('worktree/activate', {})} testID="hypothesis-worktree-activate" />}
      </View> : canCreateWorktree && !implementer ? <View style={{ gap: 4 }}>
        <Copy small muted>{card.worktree ? '创建结果未确认；核对已有工作空间，不重复创建。' : '从 SN 当前提交新建独立 worktree，保留现有改动与运行。'}</Copy>
        <Button label={card.worktree ? '核对工作空间' : '新建独立 worktree'} disabled={busy}
          onPress={() => call('worktree', {})} testID="hypothesis-worktree-create" />
      </View> : null}
      {implementer ? <Copy small muted>{`实现 Agent：${implementer}（在“工作”页对话）`}</Copy>
        : canCreateImplementer ? <Copy small muted>{canCreateImplementer}</Copy>
          : <View style={s.row}>
            <Text style={{ fontSize: 12, color: theme.muted }}>新建实现 Agent：</Text>
            {profiles.map(p => <Button key={p.id} label={p.label} onPress={() => onStartImplementer(card.id, p.id)} testID={'hypothesis-implementer-' + p.id} />)}
          </View>}
    </View>}

    {(card.status === 'implementing' || card.status === 'approved' || card.status === 'verified') && <View style={{ gap: 6 }}>
      <Text style={{ fontSize: 11, color: theme.muted }}>核对预测：选基线 run 和改动后的 run（同一计划、同题）</Text>
      <View style={s.row}><Text style={{ fontSize: 12, color: theme.muted }}>基线</Text>
        {runs.map(r => <Button key={r.id} label={r.title} selected={baseline === r.id} onPress={() => setBaseline(r.id)} />)}</View>
      <View style={s.row}><Text style={{ fontSize: 12, color: theme.muted }}>改动后</Text>
        {runs.map(r => <Button key={r.id} label={r.title} selected={candidate === r.id} onPress={() => setCandidate(r.id)} />)}</View>
      <View style={s.row}><Button label="核对预测" disabled={busy || !baseline || !candidate || baseline === candidate}
        onPress={() => call('check', { baseline_run: baseline, candidate_run: candidate })} testID="hypothesis-check" /></View>
    </View>}

    {v && <View testID="hypothesis-verification" style={{ gap: 2 }}>
      <Text style={{ fontSize: 13, color: theme.text }}>{`预测兑现 ${v.held} / ${v.decided}（共 ${v.total} 条，${v.metric}）`}</Text>
      {v.leak && <Text style={{ fontSize: 12, color: v.leak.status === 'clean' ? theme.muted : theme.danger }}>
        {v.leak.status === 'clean' ? '泄漏扫描：diff 未发现题面或 gold 重叠' : `泄漏扫描：${v.leak.findings.length} 处与题面/gold 重叠，需人工检查`}</Text>}
    </View>}

    {card.status === 'verified' && <View style={{ gap: 6 }}>
      <View style={s.row}><Text style={{ fontSize: 12, color: theme.muted }}>效果</Text>
        {Object.entries(effectLabels).map(([k, l]) => <Button key={k} label={l} selected={effect === k} onPress={() => setEffect(k)} />)}</View>
      <View style={s.row}><Text style={{ fontSize: 12, color: theme.muted }}>原因</Text>
        {Object.entries(causeLabels).map(([k, l]) => <Button key={k} label={l} selected={cause === k} onPress={() => setCause(k)} />)}</View>
      <Input label="结论说明" value={note} onChange={setNote} placeholder="依据哪些翻转、哪些预测没兑现" multiline />
      <View style={s.row}><Button label="保存结论" primary disabled={busy || !effect || !cause}
        onPress={() => call('conclusion', { effect, cause, note })} testID="hypothesis-conclude" /></View>
    </View>}
    {card.conclusion && <Text style={{ fontSize: 13, color: theme.text }}>
      {`结论：${effectLabels[card.conclusion.effect] ?? card.conclusion.effect}；${causeLabels[card.conclusion.cause] ?? card.conclusion.cause}${card.conclusion.note ? '。' + card.conclusion.note : ''}`}</Text>}
    {error && <Copy small style={{ color: theme.error }}>{error}</Copy>}
  </View>;
}

function Field({ label, value }: { label: string; value: string }) {
  const theme = useTheme();
  return <View style={{ flexDirection: 'row', gap: 10 }}>
    <Text style={{ width: 64, fontSize: 12, color: theme.muted }}>{label}</Text>
    <Text selectable style={{ flex: 1, fontSize: 13, lineHeight: 20, color: theme.text }}>{value}</Text>
  </View>;
}
