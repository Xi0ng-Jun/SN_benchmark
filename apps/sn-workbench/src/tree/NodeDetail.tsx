import React, { useState } from 'react';
import { Pressable, ScrollView, Text, View } from 'react-native';
import { Button, Copy, fmt, MONO, useTheme } from '../ui';
import { useResource } from '../contracts';
import type { Actions } from './actions';
import type { ConfirmSpec } from './parts';
import { Badge, Delta, Eyebrow, KeyValue, Mono, Section } from './parts';
import type { Step } from './next';

export type NodeAgent = { sessionId: string; role: 'analyst' | 'implementer'; label: string; status: string | null };
import { ActionRequest, Candidate, CandidateChanges, CandidateComparisons, ChangedFile, ComparisonRecord, decisionLabels,
  IterationTree, Proposal, requestStatusLabels, short, stateLabels, verbLabels } from './contracts';

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
    <Copy small muted>记录 {short(record.id)} · 不可变；均值变好不等于不退化或显著</Copy>
  </View>;
}

function DiffFile({ file }: { file: ChangedFile }) {
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
  step, agents, onOpenAgent, onProposal, onAnalyst }: {
  tree: IterationTree; candidate: Candidate; labels: Record<string, string>; proposal: Proposal | undefined; requests: ActionRequest[];
  actions: Actions; onConfirm: (spec: ConfirmSpec) => void; onReadRun: (runId: string, execution?: string) => void; onImplementer: (c: Candidate, p: Proposal) => void; refreshKey: number;
  step: Step | null; agents: NodeAgent[]; onOpenAgent: (sessionId: string) => void; onProposal: (id: string) => void; onAnalyst: () => void;
}) {
  const theme = useTheme();
  const c = candidate;
  const isRoot = c.id === tree.root_candidate_id;
  const label = (id: string | null) => id ? labels[id] ?? short(id) : '—';
  const comparisons = useResource<CandidateComparisons>(!isRoot && c.reference ? `/candidates/${c.id}/comparisons` : null, refreshKey);
  const changes = useResource<CandidateChanges>(!isRoot && c.worktree?.status === 'ready' ? `/candidates/${c.id}/changes` : null, refreshKey);
  const parentRecord = comparisons.data?.parent;
  const rootRecord = comparisons.data?.root.status === 'same_as_parent' ? parentRecord : comparisons.data?.root;
  // Marked only where Δ父 and Δ根 differ; under the root they are the same record.
  const rootRegressed = c.parent_candidate_id !== tree.root_candidate_id ? rootRecord?.regressed ?? [] : [];
  const mine = requests.filter(r => r.target_id === c.id);
  const canCheck = Boolean(proposal && parentRecord?.admission?.status === 'admitted' && ['approved', 'implementing', 'verified'].includes(proposal.status));
  return <View testID="node-detail" style={{ gap: 4 }}>
    <View style={{ gap: 6, paddingBottom: 12 }}>
      <Eyebrow>{isRoot ? '根候选' : c.proposal_id ? '来自提议' : ({ manual: '手动派生', legacy: '遗留', 'legacy-manual': '遗留（人工关联）' } as Record<string, string>)[c.origin] ?? c.origin}</Eyebrow>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <Text style={{ fontSize: 18, fontWeight: '600', color: theme.text, letterSpacing: -0.2 }}>{label(c.id)}</Text>
        <Badge label={stateLabels[c.state] ?? c.state} tone={c.state === 'evaluated' ? 'success' : c.state === 'running' ? 'running' : c.state === 'failed' ? 'danger' : undefined} testID="node-state" />
        {c.decision && <Badge label={decisionLabels[c.decision.decision]} tone={c.decision.decision === 'adopted' ? 'primary' : undefined} />}
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
        {c.state === 'implementing' && proposal && !agents.length && <Button label="新建实现 Agent" primary={step?.action === 'implementer'} onPress={() => onImplementer(c, proposal)} testID="act-implementer" />}
        {c.state === 'implementing' && <Button label="冻结" primary={step?.action === 'freeze'} onPress={() => onConfirm(actions.freeze(c))} testID="act-freeze" />}
        {['frozen', 'failed', 'evaluated'].includes(c.state) && <Button label={c.attempts.length ? '再运行一次' : '运行'} primary={step?.action === 'run'}
          onPress={() => onConfirm(actions.run(c))} testID="act-run" />}
        {isRoot && ['frozen', 'failed'].includes(c.state) && <Button label="挂接已有结果"
          onPress={() => onConfirm(actions.adopt(c))} testID="act-adopt-runs" />}
        {canCheck && <Button label="核对预测" primary={step?.action === 'check'} onPress={() => onConfirm(actions.check(c))} testID="act-check" />}
        {['evaluated', 'failed'].includes(c.state) && !(step?.action === 'open-agent' && agents.some(a => a.sessionId === step.sessionId && a.role === 'analyst')) &&
          <Button label={agents.some(a => a.role === 'analyst') ? '打开分析 Agent' : '分析这个节点'} primary={step?.action === 'analyst'} onPress={onAnalyst} testID="act-analyst" />}
        {c.state === 'evaluated' && <Button label="派生子节点" onPress={() => onConfirm(actions.derive(c))} testID="act-derive" />}
        {c.state === 'evaluated' && !isRoot && <Button label="采纳" onPress={() => onConfirm(actions.decide(c, 'adopted'))} testID="act-adopt" />}
        {c.state === 'evaluated' && !isRoot && <Button label="放弃" onPress={() => onConfirm(actions.decide(c, 'rejected'))} testID="act-reject" />}
      </View>
    </View>
    {(agents.length > 0 || c.state === 'implementing') && <Section title="Agent" testID="section-agents" meta={<Text style={{ fontSize: 11, color: theme.muted }}>{agents.length ? `${agents.length} 个` : '无'}</Text>}>
      {agents.map(a => <View key={a.sessionId} testID={'node-agent-' + a.sessionId} style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
        <View style={{ width: 6, height: 6, borderRadius: 3, backgroundColor: a.status === 'running' || a.status === 'initializing' ? theme.primary : theme.lineStrong }} />
        <Text numberOfLines={1} style={{ flex: 1, fontSize: 12, color: theme.text }}>{a.label}</Text>
        <Text style={{ fontSize: 11, color: theme.muted }}>{a.status === 'running' || a.status === 'initializing' ? '正在工作' : a.status === 'idle' ? '空闲' : a.status ?? '状态未知'}</Text>
        <Button label="打开对话" onPress={() => onOpenAgent(a.sessionId)} testID={'open-agent-' + a.sessionId} />
      </View>)}
      {!agents.some(a => a.role === 'implementer') && c.state === 'implementing' && <Copy small muted>这个候选还没有实现 Agent。它只在该候选的 worktree 里工作，只拿到提议里给实现者的字段。</Copy>}
    </Section>}
    {mine.some(r => r.status === 'pending') && <Section title="待确认请求" testID="section-requests"
      meta={<Badge label={String(mine.filter(r => r.status === 'pending').length)} tone="warning" />}>
      {mine.filter(r => r.status === 'pending').map(r => <RequestRow key={r.id} request={r} label={label} actions={actions} onConfirm={onConfirm} />)}
    </Section>}
    {proposal && <Section title="假设与逐题预测" testID="section-proposal" meta={<Text style={{ fontSize: 11, color: theme.muted }}>{proposal.status}</Text>}>
      {!!proposal.card.mechanism && <Text style={{ fontSize: 12, lineHeight: 19, color: theme.secondary }}>{proposal.card.mechanism}</Text>}
      <Predictions proposal={proposal} />
      {proposal.conclusion && <Copy small>结论：{proposal.conclusion.effect} / {proposal.conclusion.cause} {proposal.conclusion.note}</Copy>}
    </Section>}
    {!isRoot && <Section title={`对父 ${label(c.parent_candidate_id)} 的翻转`} testID="section-parent"
      meta={parentRecord?.admission?.status === 'admitted' ? <Delta value={parentRecord.mean?.delta} /> : null}>
      {!c.reference ? <Copy small muted>本节点还没有通过准入的运行</Copy> : comparisons.error ? <Copy small style={{ color: theme.error }}>{comparisons.error}</Copy>
        : parentRecord ? <FlipTable record={parentRecord} rootRegressed={rootRegressed} testID="parent-flips" /> : <Copy small muted>读取中…</Copy>}
    </Section>}
    {!isRoot && comparisons.data && comparisons.data.root.status !== 'same_as_parent' && <Section title="相对根 C0" testID="section-root" defaultOpen={false}
      meta={rootRecord?.admission?.status === 'admitted' ? <Delta value={rootRecord.mean?.delta} /> : null}>
      {rootRecord && <FlipTable record={rootRecord} rootRegressed={rootRegressed} />}
    </Section>}
    {!isRoot && <Section title="代码差异" testID="section-diff" defaultOpen={false}
      meta={changes.data?.file_count !== undefined ? <Text style={{ fontSize: 11, color: theme.muted }}>{changes.data.file_count} 个文件</Text> : null}>
      {changes.error ? <Copy small style={{ color: theme.error }}>{changes.error}</Copy> : !changes.data ? <Copy small muted>{c.worktree?.status === 'ready' ? '读取中…' : 'worktree 未就绪'}</Copy>
        : <View style={{ gap: 6 }}>{!!changes.data.diff_stat && <Mono size={11} muted>{changes.data.diff_stat.trim()}</Mono>}
          {(changes.data.files ?? []).map(file => <DiffFile key={file.path} file={file} />)}</View>}
    </Section>}
    <Section title="运行与准入" testID="section-runs" meta={<Text style={{ fontSize: 11, color: theme.muted }}>{c.attempts.length + c.links.length} 次</Text>}>
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
      <Copy small muted>参考运行取第一次通过准入的 attempt，与分数无关；之后的运行作为重复测量。</Copy>
    </Section>
    {mine.some(r => r.status !== 'pending') && <Section title="请求记录" defaultOpen={false}>
      {mine.filter(r => r.status !== 'pending').map(r => <RequestRow key={r.id} request={r} label={label} actions={actions} onConfirm={onConfirm} />)}
    </Section>}
    {c.decision && <Section title="审批与结论">
      <Copy small>{decisionLabels[c.decision.decision]} · {c.decision.actor} · {new Date(c.decision.at).toLocaleString('zh-CN', { hour12: false })}</Copy>
      <Copy small muted>{c.decision.note}</Copy>
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
