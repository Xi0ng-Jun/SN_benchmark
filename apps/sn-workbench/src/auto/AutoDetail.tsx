import React from 'react';
import { Text, View } from 'react-native';
import { Button, Copy, useTheme } from '../ui';
import { date, TaskSession, useResource } from '../contracts';
import { CandidateChanges, Proposal, short, signed } from '../tree/contracts';
import { Badge, Eyebrow, KeyValue, Mono, Section } from '../tree/parts';
import { DiffFile } from '../tree/NodeDetail';
import { agentsOf, iterationAnalystsOf } from '../tree/next';
import { agentStatusLabels } from '../agents/AgentPicker';
import { attemptLabels, attemptWhy, attributionLabels, AutoAttempt, AutoCard, AutoConfirmation, AutoNode, AutoRunView, AutoSegment, duration,
  nodeStateLabels, num, segmentVerdictLabels } from './contracts';
import { Marker } from './AutoRunPage';

// The right column of the automatic-run page: what the selection is and the numbers behind it.
// Read-only, like the page.

type AgentProps = { sessions: TaskSession[]; live: Record<string, { status: string | null }>; onOpenAgent: (sessionId: string) => void };

const CONFIG_LABELS: [string, string][] = [
  ['mde', 'MDE δ'], ['sigma_d', 'σ_d'], ['T', '触发 T'], ['ladder_c', 'Ladder c'], ['K_max', 'K_max'], ['n_fresh', 'n_fresh'],
  ['z_screen', 'z 筛选'], ['z_confirm', 'z 确认'], ['b_cards', '并行 b'], ['analyst_cards', '卡片数'], ['k_screen', '重跑 k'], ['k_smoke', '冒烟 k'],
  ['max_confirmations', '确认上限'], ['predicate_coverage_floor', '谓词下限'], ['max_consecutive_run_failures', '故障上限'],
];

function Heading({ eyebrow, title, children }: { eyebrow: string; title: string; children?: React.ReactNode }) {
  const theme = useTheme();
  return <View style={{ gap: 6, paddingBottom: 10 }}>
    <Eyebrow>{eyebrow}</Eyebrow>
    <Text selectable style={{ fontSize: 15, lineHeight: 22, fontWeight: '600', color: theme.text }}>{title}</Text>
    {children && <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6 }}>{children}</View>}
  </View>;
}

const Number_ = ({ value }: { value: string }) => <Mono size={12}>{value}</Mono>;

function AgentList({ title, ids, props }: { title: string; ids: TaskSession[]; props: AgentProps }) {
  if (!ids.length) return null;
  return <Section title={title} testID="auto-agents">
    {ids.map(s => <View key={s.id} style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
      <Copy small style={{ flex: 1 }}>{s.role === 'implementer' ? '实现' : '分析'} Agent · {short(s.agent_id)} · {agentStatusLabels[props.live[s.id]?.status ?? ''] ?? '状态未知'}</Copy>
      <Button label="查看" onPress={() => props.onOpenAgent(s.id)} testID={'auto-open-agent-' + s.id} />
    </View>)}
  </Section>;
}

function RunDetail({ view, agents }: { view: AutoRunView; agents: AgentProps }) {
  const { run, config } = view;
  const bounds = (config?.safeguard_bounds ?? {}) as Record<string, number>;
  return <View testID="auto-detail-run">
    <Heading eyebrow="自动运行" title={run.run_id ?? '—'} />
    <Section title="运行">
      <KeyValue label="操作者">{run.actor ?? '—'}</KeyValue>
      <KeyValue label="启动"><Copy small>{run.started_at ? date(run.started_at) : '—'}</Copy></KeyValue>
      <KeyValue label="协议哈希"><Mono size={11}>{short(run.protocol_sha256, 16)}</Mono></KeyValue>
    </Section>
    {config && <Section title="预注册参数" testID="auto-config">
      <Copy small muted>运行前冻结，运行中不能改。</Copy>
      {CONFIG_LABELS.filter(([key]) => config[key] !== undefined && config[key] !== null).map(([key, label]) =>
        <KeyValue key={key} label={label}><Number_ value={String(config[key])} /></KeyValue>)}
      {Object.entries(bounds).map(([metric, bound]) => <KeyValue key={metric} label="保护指标"><Mono size={11}>{metric} ≤ {bound}</Mono></KeyValue>)}
    </Section>}
    <AgentList title="分析 Agent" ids={iterationAnalystsOf(agents.sessions)} props={agents} />
    <Copy small muted>选中谱系里的段或候选，这里显示它的依据。</Copy>
  </View>;
}

function AttributionBlock({ c }: { c: AutoConfirmation }) {
  const a = c.attribution;
  if (!a || a.label === 'unattributed') return <Copy small muted>段端点卡片没有谓词，未归因。这不影响采纳。</Copy>;
  return <>
    {a.possibly_biased && <Copy small muted>切片和配对差来自同一次段起点运行，标签可能有偏，仅供参考。</Copy>}
    <KeyValue label="预测方向"><Copy small>{a.predicted_direction === 'increase' ? '上升' : '下降'}</Copy></KeyValue>
    <KeyValue label="行为发生率"><Number_ value={`${num(a.r_p, 2)} → ${num(a.r_c, 2)}`} /></KeyValue>
    <KeyValue label="切片内 Δ"><Number_ value={`${signed(a.delta_S)}（${a.slice_size ?? '—'} 题）`} /></KeyValue>
    <KeyValue label="切片外 Δ"><Number_ value={`${signed(a.delta_notS)}（${a.not_slice_size ?? '—'} 题）`} /></KeyValue>
    {!!a.n_not_computable && <KeyValue label="不可计算"><Number_ value={`${a.n_not_computable} 题`} /></KeyValue>}
  </>;
}

function SegmentDetail({ segment, onSelect }: { segment: AutoSegment; onSelect: (id: string) => void }) {
  const c = segment.confirmation;
  const status = segment.status === 'certified' ? '已确认' : segment.status === 'open' ? '暂定中' : '已回滚';
  const safeguards = Object.entries(c?.safeguards_end ?? {});
  return <View testID="auto-detail-segment">
    <Heading eyebrow={`段 ${segment.index}`} title={c ? segmentVerdictLabels[c.verdict] : status}>
      <Badge label={status} tone={segment.status === 'certified' ? 'success' : segment.status === 'open' ? 'primary' : undefined} />
      {c?.attribution && <Badge label={`归因：${attributionLabels[c.attribution.label]}`} tone={c.attribution.label === 'supported' ? 'success' : c.attribution.label === 'refuted' ? 'danger' : undefined} />}
    </Heading>
    {c ? <>
      <Section title="确认（新鲜批次）" testID="auto-confirmation">
        <KeyValue label="Δ_fresh"><Number_ value={`${signed(c.delta_fresh)} ± ${num(c.se_fresh)}`} /></KeyValue>
        <KeyValue label="区间"><Number_ value={`[${signed(c.lower)}, ${signed(c.upper)}]`} /></KeyValue>
        <KeyValue label="批次"><Number_ value={`第 ${(c.fresh_batch_index ?? 0) + 1} 批 · n = ${c.n_fresh ?? '—'} · k = ${c.k_fresh ?? '—'}`} /></KeyValue>
        {!!c.n_unpaired && <KeyValue label="未配对"><Number_ value={`${c.n_unpaired} 题`} /></KeyValue>}
        <KeyValue label="累积 Δ_val"><Number_ value={signed(c.cumulative_delta_val)} /></KeyValue>
        <KeyValue label="缩水"><Number_ value={signed(c.shrinkage)} /></KeyValue>
        <Copy small muted>缩水 = 验证池累积增益 − 新鲜批次增益，是选择偏差的直接读数。</Copy>
      </Section>
      <Section title="归因" testID="auto-attribution"><AttributionBlock c={c} /></Section>
      {(safeguards.length > 0 || c.cost_change !== null) && <Section title="保护指标与成本" defaultOpen={false}>
        {safeguards.map(([metric, value]) => <KeyValue key={metric} label={metric}><Number_ value={num(value)} /></KeyValue>)}
        {c.cost_change !== null && <KeyValue label="成本变化"><Number_ value={signed(c.cost_change, 1)} /></KeyValue>}
      </Section>}
    </> : <Copy small muted>还没有确认。累积 Δ_val 到 T 时用下一个新鲜批次确认整段。</Copy>}
    <Section title={`候选（${segment.nodes.length}）`}>
      {segment.nodes.map((n: AutoNode) => <View key={n.version_hash} style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
        <Marker state={n.state} size={8} />
        <Copy small style={{ flex: 1 }}>{n.title ?? short(n.version_hash)}</Copy>
        {n.attempt && <Button label="详情" onPress={() => onSelect('att:' + n.attempt)} />}
      </View>)}
    </Section>
  </View>;
}

function Changes({ candidateId }: { candidateId: string }) {
  const changes = useResource<CandidateChanges>(`/candidates/${candidateId}/changes`);
  return <Section title="代码差异" defaultOpen={false} testID="auto-changes">
    {changes.error ? <Copy small muted>{changes.error}</Copy> : !changes.data ? <Copy small muted>读取中…</Copy>
      : changes.data.files?.length ? changes.data.files.map(file => <DiffFile key={file.path} file={file} />) : <Copy small muted>没有差异记录</Copy>}
  </Section>;
}

function CardText({ proposal }: { proposal: Proposal | undefined }) {
  const theme = useTheme();
  if (!proposal) return null;
  const { card } = proposal;
  const block = (label: string, text: string | undefined) => text ? <View style={{ gap: 2 }}>
    <Text style={{ fontSize: 11, lineHeight: 16, color: theme.muted }}>{label}</Text>
    <Text selectable style={{ fontSize: 12, lineHeight: 19, color: theme.secondary }}>{text}</Text>
  </View> : null;
  return <Section title="假设" testID="auto-card">
    {block('组件', card.component)}{block('机制', card.mechanism)}{block('改动说明', card.implementer_brief)}{block('怎样算错', card.falsification)}
  </Section>;
}

function AttemptDetail({ attempt, node, proposal, agents }: { attempt: AutoAttempt; node: AutoNode | undefined; proposal: Proposal | undefined; agents: AgentProps }) {
  const timings = Object.entries(attempt.timings ?? {}).filter(([key]) => key !== 'total_s');
  const timingLabels: Record<string, string> = { implement_s: '实现', smoke_s: '冒烟', screen_s: '筛选', rebase_s: 'rebase' };
  const measured = attempt.delta_val !== null;
  return <View testID="auto-detail-attempt">
    <Heading eyebrow={`第 ${attempt.round_id} 轮 · 卡片 ${attempt.card_index + 1}`} title={attempt.title ?? '—'}>
      <Badge label={attempt.halted ? '未决' : attemptLabels[attempt.verdict]} tone={attempt.verdict === 'screen_pass' ? 'success' : undefined} testID="attempt-verdict" />
      {node && <Badge label={nodeStateLabels[node.state]} tone={node.state === 'certified' ? 'success' : node.state === 'provisional' ? 'primary' : undefined} />}
      {attempt.has_predicate ? <Badge label="有谓词" /> : <Badge label="无谓词" />}
    </Heading>
    <Section title="判定" testID="auto-screen">
      <Copy small>{attemptWhy(attempt)}</Copy>
      {measured && <>
        <KeyValue label="Δ_val"><Number_ value={`${signed(attempt.delta_val)} ± ${num(attempt.se_val)}`} /></KeyValue>
        <KeyValue label="下界"><Number_ value={`${signed(attempt.lower_val)}（须 > 0）`} /></KeyValue>
        <KeyValue label="分数"><Number_ value={`${num(attempt.score_val)}（须 > ${num(attempt.threshold)}）`} /></KeyValue>
        <KeyValue label="门槛"><Number_ value={`best ${num(attempt.best_before)} + m ${num(attempt.ladder_m)}`} /></KeyValue>
        <KeyValue label="验证池"><Number_ value={`${attempt.val_pool_size ?? '—'} 题${attempt.n_unpaired ? `，${attempt.n_unpaired} 题未配对` : ''}`} /></KeyValue>
      </>}
      {attempt.rebased_from && <KeyValue label="rebase"><Mono size={11}>来自 {short(attempt.rebased_from)}</Mono></KeyValue>}
      {attempt.reason && attempt.verdict !== 'screen_pass' && <Mono size={11} muted>{attempt.reason}</Mono>}
    </Section>
    <CardText proposal={proposal} />
    {timings.length > 0 && <Section title="耗时" defaultOpen={false}>
      {timings.map(([key, value]) => <KeyValue key={key} label={timingLabels[key] ?? key}><Copy small>{duration(value)}</Copy></KeyValue>)}
    </Section>}
    {attempt.candidate_id && <AgentList title="Agent" ids={agentsOf(attempt.candidate_id, agents.sessions)} props={agents} />}
    {attempt.candidate_id && <Changes key={attempt.candidate_id} candidateId={attempt.candidate_id} />}
  </View>;
}

function CardDetail({ card, proposal }: { card: AutoCard; proposal: Proposal | undefined }) {
  return <View testID="auto-detail-card">
    <Heading eyebrow={`卡片 ${card.index + 1}`} title={card.title ?? '—'}>
      <Badge label={card.status === 'queued' ? '排队' : '进行中'} tone={card.status === 'queued' ? undefined : 'running'} />
      {card.has_predicate ? <Badge label="有谓词" /> : <Badge label="无谓词" />}
    </Heading>
    <CardText proposal={proposal} />
  </View>;
}

export function AutoDetail({ view, selected, proposals, onSelect, ...agents }: AgentProps & {
  view: AutoRunView; selected: string | null; proposals: Record<string, Proposal>; onSelect: (id: string) => void;
}) {
  const attempts = [...view.rounds.flatMap(r => r.attempts), ...(view.open_round?.cards ?? []).flatMap(c => c.attempt ? [c.attempt] : [])];
  const nodes = view.segments.flatMap(s => s.nodes);
  const [kind, id] = selected ? [selected.slice(0, selected.indexOf(':')), selected.slice(selected.indexOf(':') + 1)] : ['', ''];
  if (kind === 'seg') {
    const segment = view.segments.find(s => String(s.index) === id);
    if (segment) return <SegmentDetail segment={segment} onSelect={onSelect} />;
  }
  if (kind === 'att') {
    const attempt = attempts.find(a => a.key === id);
    if (attempt) return <AttemptDetail attempt={attempt} node={nodes.find(n => n.version_hash === attempt.candidate)}
      proposal={attempt.card_ref ? proposals[attempt.card_ref] : undefined} agents={agents} />;
  }
  if (kind === 'card') {
    const card = view.open_round?.cards.find(c => String(c.index) === id);
    if (card) return <CardDetail card={card} proposal={card.ref ? proposals[card.ref] : undefined} />;
  }
  return <RunDetail view={view} agents={agents} />;
}
