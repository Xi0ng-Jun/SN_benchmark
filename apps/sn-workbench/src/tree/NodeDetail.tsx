import React, { useState } from 'react';
import { Pressable, ScrollView, Text, View } from 'react-native';
import { Button, Copy, fmt, MONO, useTheme } from '../ui';
import { useResource } from '../contracts';
import type { Actions } from './actions';
import type { ConfirmSpec } from './parts';
import { Badge, Delta, Eyebrow, KeyValue, Mono, RemoveConfirm, Section } from './parts';
import { agentStatusLabels } from '../agents/AgentPicker';
import type { Step } from './next';

export type NodeAgent = { sessionId: string; role: 'analyst' | 'implementer' | 'reviewer'; label: string; status: string | null };
import { ActionRequest, adoptedUnattributed, Attribution, attributionLabels, Candidate, CandidateChanges, CandidateComparisons, ChangedFile,
  ComparisonRecord, decisionLabels, IterationTree, isV2, Proposal, requestStatusLabels, Safeguard, short, signed, stateLabels,
  V2Verdict, v2Stages, v2VerdictLabels, verbLabels } from './contracts';

const caseShort = (id: string) => id.split(':').pop()!.slice(0, 8);

function FlipTable({ record, rootRegressed, testID }: { record: ComparisonRecord; rootRegressed: string[]; testID?: string }) {
  const theme = useTheme();
  if (record.status === 'unavailable' || record.status === 'none') return <Copy small muted>{record.reason ?? '不可用'}</Copy>;
  if (record.admission?.status !== 'admitted') return <Copy small style={{ color: theme.warning }}>未通过配对准入：{record.admission?.reason ?? '—'}</Copy>;
  return <View testID={testID} style={{ gap: 2 }}>
    <View style={{ flexDirection: 'row', gap: 8, paddingBottom: 4 }}>
      <Text style={{ flex: 1, fontSize: 11, color: theme.muted }}>题（{record.metric}）</Text>
      <Text style={{ width: 56, textAlign: 'right', fontSize: 11, color: theme.muted }}>参考</Text>
      <Text style={{ width: 56, textAlign: 'right', fontSize: 11, color: theme.muted }}>本节点</Text>
      <Text style={{ width: 60, textAlign: 'right', fontSize: 11, color: theme.muted }}>Δ</Text>
    </View>
    {(record.rows ?? []).map(row => <View key={row.case_id} style={{ flexDirection: 'row', gap: 8, alignItems: 'center' }}>
      <View style={{ flex: 1, flexDirection: 'row', gap: 6, alignItems: 'center' }}>
        <Mono size={12}>{caseShort(row.case_id)}</Mono>
        {rootRegressed.includes(row.case_id) && <Badge label="低于根" tone="danger" />}
      </View>
      <View style={{ width: 56, alignItems: 'flex-end' }}><Mono size={12} muted>{fmt(row.left)}</Mono></View>
      <View style={{ width: 56, alignItems: 'flex-end' }}><Mono size={12}>{fmt(row.right)}</Mono></View>
      <View style={{ width: 60, alignItems: 'flex-end' }}><Delta value={row.delta} /></View>
    </View>)}
    <View style={{ flexDirection: 'row', gap: 8, borderTopWidth: 1, borderColor: theme.line, paddingTop: 4, marginTop: 2 }}>
      <Text style={{ flex: 1, fontSize: 11, color: theme.muted }}>均值 · ↑{record.counts?.better} ↓{record.counts?.worse} ={record.counts?.same}</Text>
      <View style={{ width: 56, alignItems: 'flex-end' }}><Mono size={12} muted>{fmt(record.mean?.reference)}</Mono></View>
      <View style={{ width: 56, alignItems: 'flex-end' }}><Mono size={12}>{fmt(record.mean?.subject)}</Mono></View>
      <View style={{ width: 60, alignItems: 'flex-end' }}><Delta value={record.mean?.delta} /></View>
    </View>
    <Copy small muted>记录 {short(record.id)}</Copy>
  </View>;
}

export function DiffFile({ file }: { file: ChangedFile }) {
  const theme = useTheme();
  const [open, setOpen] = useState(false);
  return <View style={{ borderWidth: 1, borderColor: theme.line, borderRadius: 8, overflow: 'hidden' }}>
    <Pressable onPress={() => setOpen(o => !o)} style={{ paddingHorizontal: 10, paddingVertical: 6, backgroundColor: theme.surface }}>
      <Mono size={12}>{(open ? '▾ ' : '▸ ') + file.path}</Mono>
    </Pressable>
    {open && <ScrollView horizontal style={{ maxHeight: 360 }}><View style={{ padding: 8 }}>
      {(file.diff ?? (file.binary ? '（二进制文件）' : file.private ? '（私有路径，不显示）' : '')).split('\n').map((line, i) =>
        <Text key={i} style={{ fontFamily: MONO, fontSize: 11, lineHeight: 16,
          color: line.startsWith('+') && !line.startsWith('+++') ? theme.add : line.startsWith('-') && !line.startsWith('---') ? theme.del : theme.muted,
          backgroundColor: line.startsWith('+') && !line.startsWith('+++') ? theme.addBg : line.startsWith('-') && !line.startsWith('---') ? theme.delBg : 'transparent' }}>{line || ' '}</Text>)}
      {file.truncated && <Copy small muted>（diff 已截断）</Copy>}
    </View></ScrollView>}
  </View>;
}

const num = (value: number | null | undefined, digits = 3) => typeof value === 'number' && Number.isFinite(value) ? value.toFixed(digits) : '—';
const verdictTone = (v: V2Verdict['verdict']) =>
  v === 'pass' || v === 'holds' ? 'success' as const : v === 'fail' || v === 'does_not_hold' ? 'danger' as const : 'warning' as const;

// One lcd-v2 stage: Δ, its SE and the one-sided bound the verdict reads (lower > 0 adopts or passes).
function V2Stage({ verdict, testID }: { verdict: V2Verdict; testID?: string }) {
  const theme = useTheme();
  const cleared = verdict.lower !== null ? verdict.lower > 0 : null;
  return <View testID={testID} style={{ gap: 2 }}>
    <KeyValue label="Δ"><View style={{ flexDirection: 'row', gap: 8, alignItems: 'center' }}><Delta value={verdict.delta} /><Mono size={12} muted>SE {num(verdict.se)}</Mono></View></KeyValue>
    <KeyValue label="置信下界"><Text style={{ fontSize: 12, color: cleared === null ? theme.muted : cleared ? theme.success : theme.warning }}>
      <Mono size={12} color={cleared === null ? theme.muted : cleared ? theme.success : theme.warning}>{signed(verdict.lower)}</Mono>{cleared === null ? '' : cleared ? '  > 0' : '  ≤ 0'}</Text></KeyValue>
    <KeyValue label="上界"><Mono size={12}>{signed(verdict.upper)}</Mono></KeyValue>
    <KeyValue label="样本"><Mono size={12}>n = {verdict.n ?? '—'} · k = {verdict.k ?? '—'} · z = {num(verdict.z, 2)}</Mono></KeyValue>
    {verdict.reasons.length > 0 && <Copy small muted>{verdict.reasons.join(' · ')}</Copy>}
  </View>;
}

function AttributionView({ attribution, unattributed }: { attribution: Attribution | null | undefined; unattributed: boolean }) {
  const theme = useTheme();
  return <View style={{ gap: 6 }}>
    {unattributed && <View testID="unattributed-warning" style={{ borderWidth: 1, borderColor: theme.danger, borderRadius: 8, padding: 10, gap: 2 }}>
      <Text style={{ fontSize: 13, fontWeight: '600', color: theme.danger }}>已采纳、未归因</Text>
      <Copy small>收益在新鲜批次上成立并已采纳，但机制标签不是“支持”：改进的原因未被证实，留待人工复核。</Copy>
    </View>}
    <Copy small muted>归因标签只解释改动为什么有效，不参与采纳判定。</Copy>
    {!attribution ? <Copy small muted>没有机制假设或尚未确认，无标签</Copy> : <View style={{ gap: 2 }}>
      <KeyValue label="操纵率"><Mono size={12}>父 {num(attribution.manipulation_rate_parent)} → 本 {num(attribution.manipulation_rate_candidate)} · 变化 {signed(attribution.manipulation_change)}{attribution.manipulated === null ? '' : attribution.manipulated ? ' · 已操纵' : ' · 未达阈值'}</Mono></KeyValue>
      <KeyValue label="Δ 切片内"><View style={{ flexDirection: 'row', gap: 8 }}><Delta value={attribution.delta_slice} /><Mono size={12} muted>{attribution.slice_size} 题（至少 {attribution.n_min}）</Mono></View></KeyValue>
      <KeyValue label="Δ 切片外"><View style={{ flexDirection: 'row', gap: 8 }}><Delta value={attribution.delta_outside} /><Mono size={12} muted>{attribution.outside_size} 题</Mono></View></KeyValue>
    </View>}
  </View>;
}

function SafeguardTable({ safeguards }: { safeguards: Safeguard[] }) {
  const theme = useTheme();
  const head = (text: string, width?: number) => <Text style={{ width, flex: width ? undefined : 1, textAlign: width ? 'right' : 'left', fontSize: 11, color: theme.muted }}>{text}</Text>;
  return <View testID="safeguard-table" style={{ gap: 2 }}>
    <View style={{ flexDirection: 'row', gap: 8, paddingBottom: 4 }}>
      {head('指标')}{head('参考', 56)}{head('本节点', 56)}{head('变化', 60)}{head('容差', 48)}{head('越界', 36)}
    </View>
    {safeguards.map(g => <View key={g.key} testID={'safeguard-' + g.key} style={{ flexDirection: 'row', gap: 8, alignItems: 'center' }}>
      <View style={{ flex: 1, minWidth: 0 }}><Mono size={12}>{g.key}</Mono><Text style={{ fontSize: 10, color: theme.muted }}>{g.direction === 'higher_is_better' ? '越高越好' : '越低越好'}</Text></View>
      <View style={{ width: 56, alignItems: 'flex-end' }}><Mono size={12} muted>{num(g.reference)}</Mono></View>
      <View style={{ width: 56, alignItems: 'flex-end' }}><Mono size={12}>{num(g.subject)}</Mono></View>
      <View style={{ width: 60, alignItems: 'flex-end' }}><Mono size={12}>{signed(g.change)}</Mono></View>
      <View style={{ width: 48, alignItems: 'flex-end' }}><Mono size={12} muted>{num(g.margin)}</Mono></View>
      <Text style={{ width: 36, textAlign: 'right', fontSize: 12, color: g.breached === null ? theme.warning : g.breached ? theme.danger : theme.success }}>
        {g.breached === null ? '未观测' : g.breached ? '越界' : '否'}</Text>
    </View>)}
  </View>;
}

export function RequestRow({ request, label, actions, onConfirm }: { request: ActionRequest; label: (id: string | null) => string; actions: Actions; onConfirm: (spec: ConfirmSpec) => void }) {
  const theme = useTheme();
  return <View testID={'request-' + request.id} style={{ borderWidth: 1, borderColor: request.status === 'pending' ? theme.warning : theme.line, borderRadius: 8, padding: 10, gap: 6 }}>
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
      <Text style={{ flex: 1, fontSize: 13, fontWeight: '500', color: theme.text }}>{verbLabels[request.verb] ?? request.verb} {request.target_id ? label(request.target_id) : ''}</Text>
      <Badge label={requestStatusLabels[request.status]} tone={request.status === 'pending' ? 'warning' : request.status === 'executed' ? 'success' : request.status === 'failed' ? 'danger' : undefined} />
    </View>
    <Copy small muted>{request.requested_by} · {new Date(request.created_at).toLocaleString('zh-CN', { hour12: false })} · 参数 {JSON.stringify(request.params)}</Copy>
    <Text numberOfLines={4} style={{ fontSize: 12, lineHeight: 18, color: theme.secondary }}>{request.reason}</Text>
    {request.decided_by && <Copy small muted>{request.decided_by}：{request.decision_note || '（无备注）'}{request.result?.error ? ' · 错误：' + String(request.result.error) : ''}</Copy>}
    {request.status === 'pending' && <View style={{ flexDirection: 'row', gap: 8 }}>
      <Button label="确认并执行" primary onPress={() => onConfirm(actions.confirmRequest(request))} testID={'request-confirm-' + request.id} />
      <Button label="驳回" onPress={() => onConfirm(actions.rejectRequest(request))} testID={'request-reject-' + request.id} />
    </View>}
  </View>;
}

function Predictions({ proposal }: { proposal: Proposal }) {
  const theme = useTheme();
  const results = Object.fromEntries((proposal.verification?.results ?? []).map(r => [r.case_id, r]));
  return <View style={{ gap: 4 }}>
    {proposal.card.predictions.map(p => {
      const r = results[p.case_id];
      return <View key={p.case_id} style={{ flexDirection: 'row', gap: 8, alignItems: 'center' }}>
        <Mono size={12}>{caseShort(p.case_id)}</Mono>
        <Text style={{ flex: 1, fontSize: 12, color: theme.secondary }}>{p.expect}{p.guard ? ' · 守护' : ''}</Text>
        {r && <Delta value={r.delta} />}
        <Text style={{ width: 36, textAlign: 'right', fontSize: 12, color: !r || r.held === null ? theme.muted : r.held ? theme.success : theme.danger }}>
          {!r ? '未核' : r.held === null ? '—' : r.held ? '兑现' : '未兑现'}</Text>
      </View>;
    })}
    {proposal.verification && <Copy small muted>核对 {proposal.verification.held}/{proposal.verification.decided} 兑现 · 依据比较记录 {short(proposal.verification.comparison_id)} · 泄漏扫描 {proposal.verification.leak?.status ?? '—'}</Copy>}
  </View>;
}

export function CandidateDetail({ tree, candidate, labels, proposal, requests, actions, onConfirm, onReadRun, onImplementer, refreshKey,
  step, agents, onOpenAgent, onProposal, onAnalyst, onNewAnalyst, onRemoveAgent }: {
  tree: IterationTree; candidate: Candidate; labels: Record<string, string>; proposal: Proposal | undefined; requests: ActionRequest[];
  actions: Actions; onConfirm: (spec: ConfirmSpec) => void; onReadRun: (runId: string, execution?: string) => void; onImplementer: (c: Candidate, p: Proposal) => void; refreshKey: number;
  step: Step | null; agents: NodeAgent[]; onOpenAgent: (sessionId: string) => void; onProposal: (id: string) => void; onAnalyst: () => void;
  onNewAnalyst: () => void; onRemoveAgent: (sessionId: string) => Promise<unknown>;
}) {
  const theme = useTheme();
  const c = candidate;
  const isRoot = c.id === tree.root_candidate_id;
  const label = (id: string | null) => id ? labels[id] ?? short(id) : '—';
  const comparisons = useResource<CandidateComparisons>(!isRoot && c.attempts.length ? `/candidates/${c.id}/comparisons` : null, refreshKey);
  const changes = useResource<CandidateChanges>(!isRoot && c.worktree?.status === 'ready' ? `/candidates/${c.id}/changes` : null, refreshKey);
  const parentRecord = comparisons.data?.parent;
  const rootRecord = comparisons.data?.root.status === 'same_as_parent' ? parentRecord : comparisons.data?.root;
  // Marked only where Δ父 and Δ根 differ; under the root they are the same record.
  const rootRegressed = c.parent_candidate_id !== tree.root_candidate_id ? rootRecord?.regressed ?? [] : [];
  const mine = requests.filter(r => r.target_id === c.id);
  // A node can have several Agents; new ones are offered whenever the state allows.
  const canAnalyse = ['evaluated', 'failed'].includes(c.state);
  const canImplement = c.state === 'implementing' && Boolean(proposal);
  const canCheck = Boolean(proposal && parentRecord?.id && ['approved', 'implementing', 'verified'].includes(proposal.status));
  const v2 = isV2(tree);
  const stages = v2Stages(c.decision);
  const guards = stages.confirm ?? stages.screen;
  const unattributed = adoptedUnattributed(tree, c);
  // Shared by both flows; lcd-v2 moves operations and per-case flips below the evidence.
  const agentsSection = (agents.length > 0 || canAnalyse || c.state === 'implementing') && <Section title="Agent" testID="section-agents" meta={<Text style={{ fontSize: 11, color: theme.muted }}>{agents.length ? `${agents.length} 个` : '无'}</Text>}>
      {agents.map(a => <View key={a.sessionId} testID={'node-agent-' + a.sessionId} style={{ flexDirection: 'row', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <View style={{ width: 6, height: 6, borderRadius: 3, backgroundColor: a.status === 'running' || a.status === 'initializing' ? theme.primary : theme.lineStrong }} />
        <Text numberOfLines={1} style={{ flex: 1, minWidth: 140, fontSize: 12, color: theme.text }}>{a.label}</Text>
        <Text style={{ fontSize: 11, color: theme.muted }}>{a.status === 'running' || a.status === 'initializing' ? '正在工作' : a.status ? agentStatusLabels[a.status] ?? a.status : '读取中…'}</Text>
        <Button label="打开对话" onPress={() => onOpenAgent(a.sessionId)} testID={'open-agent-' + a.sessionId} />
        <RemoveConfirm onRemove={() => onRemoveAgent(a.sessionId)} testID={'remove-agent-' + a.sessionId} />
      </View>)}
      {!agents.some(a => a.role === 'implementer') && c.state === 'implementing' && <Copy small muted>还没有实现 Agent</Copy>}
      {(canAnalyse || canImplement) && <View style={{ flexDirection: 'row', gap: 6, flexWrap: 'wrap' }}>
        {canAnalyse && <Button label="新建分析 Agent" onPress={onNewAnalyst} testID="agent-new-analyst" />}
        {canImplement && proposal && <Button label="新建实现 Agent" onPress={() => onImplementer(c, proposal)} testID="agent-new-implementer" />}
      </View>}
    </Section>;
  const runsSection = <Section title="运行" testID="section-runs" meta={<Text style={{ fontSize: 11, color: theme.muted }}>{c.attempts.length + c.links.length} 次</Text>}>
      {[...c.attempts, ...c.links].map(m => {
        const isReference = c.reference?.execution_id === m.execution_id;
        const runId = m.execution_id + '-1';
        return <View key={m.execution_id} testID={'attempt-' + m.execution_id} style={{ flexDirection: 'row', gap: 8, alignItems: 'center', paddingVertical: 3 }}>
          <Mono size={12}>{m.attempt ? `#${m.attempt}` : '链接'}</Mono>
          <View style={{ flex: 1, minWidth: 0 }}>
            <Text numberOfLines={2} style={{ fontSize: 12, color: m.admission?.status === 'admitted' ? theme.text : m.admission?.status === 'pending' ? theme.running : theme.warning }}>
              {m.execution_state ?? '未知'} · {m.admission?.status === 'admitted' ? '通过准入' : m.admission?.status === 'pending' ? '运行中' : '未准入：' + (m.admission?.reason ?? '—')}</Text>
            <Mono size={11} muted>{short(m.execution_id, 12)}{isReference ? ' · 参考运行' : ''}</Mono>
          </View>
          {m.execution_state === 'finished' && <Button label="读题" onPress={() => onReadRun(runId, (c.primary?.partitions ?? 1) > 1 ? m.execution_id : undefined)} testID={'read-run-' + m.execution_id} />}
        </View>;
      })}
      {!c.attempts.length && !c.links.length && <Copy small muted>还没有运行</Copy>}
    </Section>;
  const parentSection = !isRoot && <Section title={v2 ? `与 ${label(c.parent_candidate_id)} 的逐题比较` : `与 ${label(c.parent_candidate_id)} 的比较`} testID="section-parent" defaultOpen={!v2}
      meta={parentRecord?.admission?.status === 'admitted' ? <Delta value={parentRecord.mean?.delta} /> : null}>
      {!c.reference ? <Copy small muted>本节点还没有通过准入的运行</Copy> : comparisons.error ? <Copy small style={{ color: theme.error }}>{comparisons.error}</Copy>
        : parentRecord ? <FlipTable record={parentRecord} rootRegressed={rootRegressed} testID="parent-flips" /> : <Copy small muted>读取中…</Copy>}
    </Section>;
  const rootSection = !isRoot && comparisons.data && comparisons.data.root.status !== 'same_as_parent' && <Section title="与根 C0 的比较" testID="section-root" defaultOpen={false}
      meta={rootRecord?.admission?.status === 'admitted' ? <Delta value={rootRecord.mean?.delta} /> : null}>
      {rootRecord && <FlipTable record={rootRecord} rootRegressed={rootRegressed} />}
    </Section>;
  const decisionSection = c.decision && <Section title="决策">
      <Copy small>{decisionLabels[c.decision.decision]} · {c.decision.actor} · {new Date(c.decision.at).toLocaleString('zh-CN', { hour12: false })}</Copy>
      <Copy small muted>{c.decision.note}</Copy>
    </Section>;
  return <View testID="node-detail" style={{ gap: 4 }}>
    <View style={{ gap: 6, paddingBottom: 12 }}>
      <Eyebrow>{isRoot ? '根候选' : c.proposal_id ? '来自提议' : ({ manual: '手动派生', legacy: '遗留', 'legacy-manual': '遗留（人工关联）' } as Record<string, string>)[c.origin] ?? c.origin}</Eyebrow>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <Text style={{ fontSize: 18, fontWeight: '600', color: theme.text, letterSpacing: -0.2 }}>{label(c.id)}</Text>
        <Badge label={stateLabels[c.state] ?? c.state} tone={c.state === 'evaluated' ? 'success' : c.state === 'running' ? 'running' : c.state === 'failed' ? 'danger' : undefined} testID="node-state" />
        {c.decision && <Badge label={decisionLabels[c.decision.decision]} tone={c.decision.decision === 'adopted' ? 'primary' : undefined} />}
        {unattributed && <Badge label="已采纳、未归因" tone="danger" testID="node-unattributed" />}
      </View>
      {!!(proposal?.card.title ?? c.note) && <Copy small>{proposal?.card.title ?? c.note}</Copy>}
      <View style={{ gap: 2, paddingTop: 4 }}>
        <KeyValue label="父候选">{isRoot ? '—（迭代起点）' : `${label(c.parent_candidate_id)} · ${short(c.parent_commit, 10)}`}</KeyValue>
        <KeyValue label="冻结 commit"><Mono size={12}>{c.frozen_commit ?? '未冻结'}</Mono></KeyValue>
        <KeyValue label="worktree"><Mono size={11} muted>{c.worktree?.branch ?? '—'}</Mono></KeyValue>
        <KeyValue label="主指标"><Mono size={12}>{c.primary?.metric ? `${c.primary.metric} ${fmt(c.primary.value)}` : c.primary?.reason ?? '—'}</Mono></KeyValue>
      </View>
      {step && <View testID="next-step" style={{ flexDirection: 'row', alignItems: 'center', gap: 6, paddingTop: 8 }}>
        {step.who === 'you' && <View style={{ width: 6, height: 6, borderRadius: 3, backgroundColor: theme.warning }} />}
        <Text style={{ flex: 1, fontSize: 12, lineHeight: 18, color: step.who === 'you' ? theme.warning : theme.muted }}>
          {step.who === 'you' ? '下一步：' : step.who === 'agent' ? '等 Agent：' : '等运行：'}{step.text}</Text>
      </View>}
      <View style={{ flexDirection: 'row', gap: 6, flexWrap: 'wrap', paddingTop: 4 }}>
        {c.state === 'provisioning' && <Button label="重新准备" onPress={() => onConfirm(actions.provision(c))} testID="act-provision" />}
        {(step?.action === 'proposal' || step?.action === 'derive-proposal') && step.proposalId &&
          <Button label="看提议" primary onPress={() => onProposal(step.proposalId!)} testID="act-proposal" />}
        {step?.action === 'open-agent' && step.sessionId && <Button label={`打开${agents.find(a => a.sessionId === step.sessionId)?.role === 'analyst' ? '分析' : '实现'} Agent 对话`}
          primary={step.who === 'you'} onPress={() => onOpenAgent(step.sessionId!)} testID="act-open-agent" />}
        {canImplement && proposal && !agents.some(a => a.role === 'implementer') && <Button label="新建实现 Agent" primary={step?.action === 'implementer'} onPress={() => onImplementer(c, proposal)} testID="act-implementer" />}
        {c.state === 'implementing' && <Button label="冻结" primary={step?.action === 'freeze'} onPress={() => onConfirm(actions.freeze(c))} testID="act-freeze" />}
        {['frozen', 'failed', 'evaluated'].includes(c.state) && <Button label={c.attempts.length ? '再运行一次' : '运行'} primary={step?.action === 'run'}
          onPress={() => onConfirm(actions.run(c))} testID="act-run" />}
        {isRoot && ['frozen', 'failed'].includes(c.state) && <Button label="挂接已有结果"
          onPress={() => onConfirm(actions.adopt(c))} testID="act-adopt-runs" />}
        {canCheck && <Button label="核对预测" primary={step?.action === 'check'} onPress={() => onConfirm(actions.check(c))} testID="act-check" />}
        {canAnalyse && !(step?.action === 'open-agent' && agents.some(a => a.sessionId === step.sessionId && a.role === 'analyst')) &&
          <Button label={agents.some(a => a.role === 'analyst') ? '打开分析 Agent' : '分析这个节点'} primary={step?.action === 'analyst'} onPress={onAnalyst} testID="act-analyst" />}
        {c.state === 'evaluated' && <Button label="派生子节点" onPress={() => onConfirm(actions.derive(c))} testID="act-derive" />}
        {c.state === 'evaluated' && !isRoot && <Button label="采纳" onPress={() => onConfirm(actions.decide(c, 'adopted'))} testID="act-adopt" />}
        {c.state === 'evaluated' && !isRoot && <Button label="放弃" onPress={() => onConfirm(actions.decide(c, 'rejected'))} testID="act-reject" />}
      </View>
    </View>
    {/* lcd-v2：假设 → diff → 筛选 → 确认 → 归因标签 → 保护指标；lcd-v1 照旧展示门的 ρ / τ */}
    {mine.some(r => r.status === 'pending') && <Section title="待确认请求" testID="section-requests"
      meta={<Badge label={String(mine.filter(r => r.status === 'pending').length)} tone="warning" />}>
      {mine.filter(r => r.status === 'pending').map(r => <RequestRow key={r.id} request={r} label={label} actions={actions} onConfirm={onConfirm} />)}
    </Section>}
    {proposal && <Section title="假设" testID="section-proposal" meta={<Text style={{ fontSize: 11, color: theme.muted }}>{proposal.status}</Text>}>
      {!!proposal.card.mechanism && <Text style={{ fontSize: 12, lineHeight: 19, color: theme.secondary }}>{proposal.card.mechanism}</Text>}
      <Predictions proposal={proposal} />
      {proposal.conclusion && <Copy small>结论：{proposal.conclusion.effect} / {proposal.conclusion.cause} {proposal.conclusion.note}</Copy>}
    </Section>}
    {!isRoot && <Section title="代码差异" testID="section-diff" defaultOpen={c.state === 'implementing' || c.state === 'frozen'}
      meta={changes.data?.file_count !== undefined ? <Text style={{ fontSize: 11, color: theme.muted }}>{changes.data.file_count} 个文件</Text> : null}>
      {changes.error ? <Copy small style={{ color: theme.error }}>{changes.error}</Copy> : !changes.data ? <Copy small muted>{c.worktree?.status === 'ready' ? '读取中…' : 'worktree 未就绪'}</Copy>
        : <View style={{ gap: 6 }}>{!!changes.data.diff_stat && <Mono size={11} muted>{changes.data.diff_stat.trim()}</Mono>}
          {(changes.data.files ?? []).map(file => <DiffFile key={file.path} file={file} />)}</View>}
    </Section>}
    {v2 ? <>
    {/* ── 3. 筛选 ── 验证池上的配对 Δ；单侧下界 > 0 才进入确认 */}
    {!isRoot && <Section title="筛选（验证池）" testID="section-screen"
      meta={stages.screen ? <Badge label={v2VerdictLabels[stages.screen.verdict]} tone={verdictTone(stages.screen.verdict)} testID="screen-verdict" /> : null}>
      {stages.screen ? <V2Stage verdict={stages.screen} testID="screen-bounds" /> : <Copy small muted>还没有筛选结果</Copy>}
    </Section>}
    {/* ── 4. 确认 ── 新鲜批次决定采纳 */}
    {!isRoot && <Section title="确认（新鲜批次）" testID="section-confirm"
      meta={stages.confirm ? <Badge label={v2VerdictLabels[stages.confirm.verdict]} tone={verdictTone(stages.confirm.verdict)} testID="confirm-verdict" /> : null}>
      {stages.confirm ? <V2Stage verdict={stages.confirm} testID="confirm-bounds" />
        : <Copy small muted>{stages.screen && stages.screen.verdict !== 'pass' ? '未通过筛选，不进入确认' : '还没有确认结果'}</Copy>}
    </Section>}
    {/* ── 5. 归因标签 ── 只解释，不参与判定 */}
    {!isRoot && <Section title="归因标签" testID="section-attribution"
      meta={stages.confirm?.attribution ? <Badge label={attributionLabels[stages.confirm.attribution.label]}
        tone={stages.confirm.attribution.label === 'supported' ? 'success' : stages.confirm.attribution.label === 'refuted' ? 'danger' : undefined} testID="attribution-label" /> : null}>
      <AttributionView attribution={stages.confirm?.attribution} unattributed={unattributed} />
    </Section>}
    {/* ── 6. 保护指标 ── 任一越界即不可测 */}
    {!isRoot && <Section title="保护指标" testID="section-safeguards"
      meta={guards ? <Text style={{ fontSize: 11, color: theme.muted }}>{guards.stage === 'confirm' ? '新鲜批次' : '验证池'}</Text> : null}>
      {guards?.safeguards.length ? <SafeguardTable safeguards={guards.safeguards} /> : <Copy small muted>还没有保护指标观测</Copy>}
    </Section>}
    {decisionSection}
    {agentsSection}
    {runsSection}
    {parentSection}
    {rootSection}
    </> : <>
    {agentsSection}
    {runsSection}
    {parentSection}
    {rootSection}
    {!isRoot && parentRecord?.admission?.status === 'admitted' && (() => {
      const kBase = tree.protocol?.k_base ?? 2;
      const admittedCount = c.attempts.filter(a => a.admission?.status === 'admitted').length;
      const delta = parentRecord.validation?.delta ?? parentRecord.mean?.delta ?? null;
      const eta = parentRecord.validation?.eta ?? parentRecord.eta ?? null;
      const tau = tree.protocol?.gate_rule?.tau ?? 0.5;
      const passesDelta = delta !== null && eta !== null ? delta >= -eta : null;
      const passesKBase = admittedCount >= kBase;
      const recorded = c.decision?.gate_verdict;
      const GateVerdict = recorded && !('stage' in recorded) ? recorded : undefined;
      const GateStep = ({ ok, children }: { ok: boolean | null; children: React.ReactNode }) =>
        <View style={{ flexDirection: 'row', gap: 6, alignItems: 'center' }}>
          <Text style={{ width: 14, fontSize: 12, color: ok === null ? theme.muted : ok ? theme.success : theme.warning }}>{ok === null ? '·' : ok ? '✓' : '○'}</Text>
          <Mono size={12}>{children}</Mono>
        </View>;
      return <Section title="门" testID="section-gate-status"
        meta={GateVerdict ? <Badge label={GateVerdict.verdict} tone={GateVerdict.verdict === 'accept' ? 'success' : GateVerdict.verdict === 'reject' ? 'danger' : undefined} /> : null}>
        <GateStep ok={passesDelta}>Δ {signed(delta)} {eta !== null ? `≥ −${eta.toFixed(3)}` : '· η 未知'}</GateStep>
        <GateStep ok={passesKBase}>准入 {admittedCount}/{kBase}</GateStep>
        <GateStep ok={GateVerdict ? GateVerdict.rho !== null && GateVerdict.rho >= GateVerdict.tau : null}>ρ {GateVerdict?.rho !== null && GateVerdict?.rho !== undefined ? GateVerdict.rho.toFixed(3) : '?'} ≥ τ {tau.toFixed(2)}</GateStep>
        {GateVerdict?.reasons.length ? <Copy small muted>{GateVerdict.reasons.join(' · ')}</Copy> : null}
        {GateVerdict && <Section title="判定依据" defaultOpen={false}><Copy small>{JSON.stringify(GateVerdict, null, 2)}</Copy></Section>}
      </Section>;
    })()}
    {decisionSection}
    </>}
    {mine.some(r => r.status !== 'pending') && <Section title="请求记录" defaultOpen={false}>
      {mine.filter(r => r.status !== 'pending').map(r => <RequestRow key={r.id} request={r} label={label} actions={actions} onConfirm={onConfirm} />)}
    </Section>}
  </View>;
}

export function ProposalDetail({ proposal, labels, actions, onConfirm }: { proposal: Proposal; labels: Record<string, string>; actions: Actions; onConfirm: (spec: ConfirmSpec) => void }) {
  const theme = useTheme();
  const card = proposal.card;
  return <View testID="proposal-detail" style={{ gap: 10 }}>
    <Eyebrow>提议 · {proposal.status}</Eyebrow>
    <Text style={{ fontSize: 16, fontWeight: '600', color: theme.text }}>{card.title}</Text>
    <KeyValue label="挂在">{labels[proposal.parent_candidate_id ?? ''] ?? short(proposal.parent_candidate_id)}</KeyValue>
    <KeyValue label="作者">{proposal.author}</KeyValue>
    {!!card.component && <KeyValue label="组件">{card.component}</KeyValue>}
    <View style={{ flexDirection: 'row', gap: 6 }}>
      {proposal.status === 'draft' && <Button label="批准" primary onPress={() => onConfirm(actions.proposal(proposal, 'approve'))} testID="proposal-approve" />}
      {proposal.status === 'draft' && <Button label="驳回" onPress={() => onConfirm(actions.proposal(proposal, 'reject'))} testID="proposal-reject" />}
      {proposal.status === 'approved' && <Button label="派生候选" primary onPress={() => onConfirm(actions.deriveFromProposal(proposal))} testID="proposal-derive" />}
    </View>
    <Section title="机制"><Text style={{ fontSize: 12, lineHeight: 19, color: theme.secondary }}>{card.mechanism}</Text></Section>
    <Section title="逐题预测"><Predictions proposal={proposal} /></Section>
    <Section title="收益、风险与证伪" defaultOpen={false}>
      <KeyValue label="预期受益">{card.expected_benefit ?? '—'}</KeyValue>
      <KeyValue label="可能受害">{card.possible_harm ?? '—'}</KeyValue>
      <KeyValue label="证伪">{card.falsification ?? '—'}</KeyValue>
      <KeyValue label="改动范围">{(card.change_scope ?? []).join('、') || '—'}</KeyValue>
    </Section>
    <Section title="交给实现 Agent 的说明" defaultOpen={false}><Copy small>{card.implementer_brief ?? '—'}</Copy></Section>
  </View>;
}
