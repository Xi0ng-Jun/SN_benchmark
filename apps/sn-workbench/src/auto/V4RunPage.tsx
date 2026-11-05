import React from 'react';
import { Pressable, Text, View } from 'react-native';
import { Button, Copy, hover, StatusDot, useTheme } from '../ui';
import { date, liveSessions, TaskSession, useResource } from '../contracts';
import { CandidateChanges, short } from '../tree/contracts';
import { Badge, Delta, Eyebrow, KeyValue, Mono, Section } from '../tree/parts';
import { DiffFile } from '../tree/NodeDetail';
import { agentStatusLabels } from '../agents/AgentPicker';
import { ago, duration, num } from './contracts';
import { kindLabels, labelTexts, reasonTexts, stageLabels, tierLabels, V4Node, V4Record, V4Round, V4RunView, V4Tier, v4ActionTexts,
  v4HaltLabels, v4PhaseLabels, v4StatusLabels } from './v4contracts';

// The method-v4 automatic run, read top to bottom: status, budget, calibration, lineage, rounds
// (each candidate through the cascade, then the round-end decision), ablation, endpoint. Nothing here writes.

const tierTone = (tier: V4Tier | null | undefined) => tier === 'confirmed' ? 'success' as const : tier === 'suspicious' ? 'danger' as const : undefined;
const labelTone = (r: V4Record) => r.label === 'adopted' ? 'success' as const : r.label === 'qualified_not_adopted' ? 'primary' as const
  : r.stage === 'error' || r.stage === 'run_failed' || r.label === 'safeguard_breached' ? 'danger' as const
  : ['pending', 'built', 'repair', 'rebase', 'smoke_pass', 'minibatch_pass', 'validated'].includes(r.stage) ? 'running' as const : undefined;
const recordKey = (round: number, card: number) => `rec:${round}:${card}`;
const pct = (value: number | null | undefined) => typeof value === 'number' ? `${Math.round(value * 100)}%` : '—';

function Row({ selected, onPress, testID, children }: { selected?: boolean; onPress: () => void; testID?: string; children: React.ReactNode }) {
  const theme = useTheme();
  return <Pressable testID={testID} onPress={onPress} accessibilityRole="button"
    style={hover(hovered => ({ flexDirection: 'row', alignItems: 'center', gap: 8, paddingVertical: 6, paddingHorizontal: 8, borderRadius: 6,
      backgroundColor: selected ? theme.selected : hovered ? theme.surface : 'transparent' }))}>{children}</Pressable>;
}

function Tile({ label, value, sub }: { label: string; value: string; sub?: string }) {
  const theme = useTheme();
  return <View style={{ flexGrow: 1, flexBasis: 130, minWidth: 124, gap: 4, paddingVertical: 12, paddingHorizontal: 12 }}>
    <Text style={{ fontSize: 12, lineHeight: 16, color: theme.muted }}>{label}</Text>
    <Text style={{ fontSize: 20, lineHeight: 26, fontWeight: '600', color: theme.text }}>{value}</Text>
    {!!sub && <Text style={{ fontSize: 11, lineHeight: 15, color: theme.muted }}>{sub}</Text>}
  </View>;
}

function Status({ view }: { view: V4RunView }) {
  const theme = useTheme();
  const { run, next_action: action } = view;
  const headline = run.status === 'running' ? (view.phase ? v4PhaseLabels[view.phase] : '正在启动')
    : run.status === 'halted' ? v4HaltLabels[run.halt_reason ?? ''] ?? run.halt_reason ?? '已停机'
    : run.status === 'interrupted' ? `停在第 ${view.progress?.round ?? view.progress?.rounds_finished ?? 0} 轮` : '已登记，还没有写入状态';
  const tone = run.status === 'running' ? 'running' as const : action === 'resume' || action === 'retry' || action === 'budget' ? 'warning' as const : 'idle' as const;
  return <View testID="auto-status" style={{ gap: 10 }}>
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
      <StatusDot tone={tone} size={8} />
      <Text testID="auto-status-label" style={{ fontSize: 16, lineHeight: 22, fontWeight: '600', color: theme.text }}>{v4StatusLabels[run.status]}</Text>
      <Badge label="method-v4" />
      <Text style={{ fontSize: 14, lineHeight: 22, color: theme.secondary, flexShrink: 1 }}>{headline}</Text>
      <View style={{ flex: 1 }} />
      <Text style={{ fontSize: 12, color: theme.muted }}>更新于 {ago(run.updated_at)}</Text>
    </View>
    {action && <View testID="auto-next-action" style={{ borderWidth: 1, borderColor: theme.line, borderRadius: 8, backgroundColor: theme.surface, paddingHorizontal: 12, paddingVertical: 10, gap: 2 }}>
      <Text style={{ fontSize: 13, lineHeight: 20, fontWeight: '500', color: ['resume', 'retry', 'budget'].includes(action) ? theme.warning : theme.text }}>{v4ActionTexts[action].title}</Text>
      <Copy small muted>{v4ActionTexts[action].detail}</Copy>
    </View>}
    {run.status === 'halted' && run.halt_reason !== 'process_failure' && run.halt_reason !== 'budget_exhausted' &&
      <Copy small muted>停机原因只说明这次运行为什么结束，不代表改进空间已经用尽。</Copy>}
  </View>;
}

function Progress({ view }: { view: V4RunView }) {
  const p = view.progress;
  if (!p) return null;
  const used = p.budget_s ? Math.min(1, (p.elapsed_s ?? 0) / p.budget_s) : null;
  return <View testID="v4-progress" style={{ flexDirection: 'row', flexWrap: 'wrap', borderWidth: 1, borderColor: useTheme().line, borderRadius: 8 }}>
    <Tile label="时间" value={`${duration(p.elapsed_s)}`} sub={used !== null ? `预算 ${duration(p.budget_s)} · 已用 ${pct(used)}` : undefined} />
    <Tile label="轮" value={String(p.rounds_finished + (p.round && !view.rounds.find(r => r.round === p.round && !r.open) ? 1 : 0))}
      sub={`连续无采纳 ${p.no_adoption}/${p.no_adoption_limit ?? '—'}`} />
    <Tile label="采纳" value={String(p.adopted)} sub={`候选 ${p.candidates} 个`} />
    <Tile label="划分" value={`${p.search_size} / ${p.validation_size} / ${p.test_size ?? '—'}`} sub={`搜索 / 验证 / 测试${p.families ? ` · ${p.families} 篇论文` : ''}`} />
    <Tile label="流程故障" value={`${p.run_failures}/${p.run_failures_limit ?? '—'}`} sub={p.key_generation ? `key 代 ${p.key_generation}` : undefined} />
  </View>;
}

function Calibration({ view }: { view: V4RunView }) {
  const cal = view.calibration, feas = view.feasibility;
  if (!cal) return null;
  return <Section title="校准与可行性" meta={`k_val = ${cal.k_val}`} testID="v4-calibration" defaultOpen={false}>
    <KeyValue label="k_val"><Copy small>{cal.k_val}（{cal.k_val_rule}）</Copy></KeyValue>
    <KeyValue label="重跑方差 / 任务方差"><Mono>{num(cal.rerun_var, 4)} / {num(cal.task_var, 4)}</Mono></KeyValue>
    <KeyValue label="A/A 配对差"><Mono>Δ {num(cal.aa_delta, 4)} · SD {num(cal.aa_sd, 4)} · SE {num(cal.aa_se, 4)}</Mono></KeyValue>
    <KeyValue label="恒定题比例"><Mono>{pct(cal.constant_fraction)}</Mono></KeyValue>
    <KeyValue label="种子验证集均分"><Mono>{num(cal.seed_mean)}</Mono></KeyValue>
    <KeyValue label="基础设施失败率"><Mono>{pct(cal.infrastructure_failure_rate)}</Mono></KeyValue>
    {feas && <>
      <KeyValue label="单个候选耗时"><Copy small>{Object.entries(feas.stage_seconds).map(([k, v]) => `${k} ${duration(v)}`).join('，')}</Copy></KeyValue>
      {feas.per_candidate_cold !== undefined && <KeyValue label="冷 / 热缓存"><Copy small>{duration(feas.per_candidate_cold)} / {duration(feas.per_candidate_warm)}（{feas.cache_note}）</Copy></KeyValue>}
      <KeyValue label="预算内预计"><Copy small>约 {feas.candidates_in_budget ?? '—'} 个候选、{feas.rounds_in_budget ?? '—'} 轮（上界）</Copy></KeyValue>
      <KeyValue label="测试集分辨率"><Copy small>95% 半宽约 {num(feas.test_half_width_95)}（n = {feas.test_n ?? '—'}）</Copy></KeyValue>
      {feas.warnings.map(w => <Copy key={w} small style={{ color: useTheme().warning }}>{w}</Copy>)}
    </>}
  </Section>;
}

function Lineage({ view, selected, onSelect }: { view: V4RunView; selected: string | null; onSelect: (id: string | null) => void }) {
  return <Section title="谱系" meta={`${view.lineage.length - 1} 次采纳`} testID="v4-lineage">
    {view.lineage.map(n => <Row key={n.version} testID={`v4-node-${n.index}`} selected={selected === 'node:' + n.version} onPress={() => onSelect('node:' + n.version)}>
      <Mono size={11} muted>{n.index}</Mono>
      <Badge label={kindLabels[n.kind] ?? n.kind} tone={n.kind === 'seed' ? undefined : 'success'} />
      <Copy small style={{ flex: 1 }}>{n.kind === 'seed' ? '种子版本' : n.titles.filter(Boolean).join(' + ') || short(n.candidate_id ?? n.version)}</Copy>
      {n.tier && <Badge label={`机制${tierLabels[n.tier]}`} tone={tierTone(n.tier)} />}
      {n.pruned && <Badge label="已剪除" tone="warning" />}
      {n.kind !== 'seed' && <Delta value={n.delta_val} />}
      {n.round ? <Mono size={11} muted>第 {n.round} 轮</Mono> : null}
    </Row>)}
  </Section>;
}

function RoundView({ round, selected, onSelect }: { round: V4Round; selected: string | null; onSelect: (id: string | null) => void }) {
  const d = round.decision;
  const meta = round.open ? '进行中' : d?.adopted ? (d.merge?.adopt ? '采纳合并候选' : '有采纳') : '无采纳';
  return <Section title={`第 ${round.round} 轮`} meta={`${meta} · 卡片 ${round.cards ?? '—'}`} testID={`v4-round-${round.round}`} defaultOpen={round.open || !!d?.adopted}>
    <Copy small muted>父版本 {short(round.parent_id ?? round.parent ?? '')} · 小批 {round.minibatch_size} 题 · 其余 {round.rest_size} 题
      {round.cards !== null ? ` · 带行为预期 ${round.cards_with_expectation}/${round.cards}` : ''}{round.wall_s ? ` · 用时 ${duration(round.wall_s)}` : ''}
      {round.diagnosis_s !== null ? ` · 诊断 ${round.diagnosis_prefetched ? '已预取' : duration(round.diagnosis_s)}` : ''}
      {round.not_started ? ` · ${round.not_started} 张卡${round.timeboxed ? '因轮时间盒' : ''}未开始` : ''}</Copy>
    {round.records.map(r => <Row key={r.card_index} testID={`v4-record-${round.round}-${r.card_index}`} selected={selected === recordKey(round.round, r.card_index)}
      onPress={() => onSelect(recordKey(round.round, r.card_index))}>
      <Mono size={11} muted>#{r.card_index + 1}</Mono>
      <Copy small style={{ flex: 1 }}>{r.title ?? '—'}</Copy>
      {r.mechanism?.tier && <Badge label={`机制${tierLabels[r.mechanism.tier]}`} tone={tierTone(r.mechanism.tier)} />}
      <Mono size={11}>{r.delta_val !== null ? `Δval ${num(r.delta_val)} ± ${num(r.se_val)}` : r.delta_mb !== null ? `Δmb ${num(r.delta_mb)}` : ''}</Mono>
      <Badge label={r.carried_to ? `转入第 ${r.carried_to} 轮` : r.halted ? '停机时在途' : r.label ? labelTexts[r.label] : stageLabels[r.stage]} tone={r.halted ? 'warning' : labelTone(r)} />
    </Row>)}
    {round.queued.map(q => <Row key={'q' + q.card_index} onPress={() => onSelect(null)}>
      <Mono size={11} muted>#{q.card_index + 1}</Mono><Copy small muted style={{ flex: 1 }}>{q.title ?? '—'}</Copy><Badge label="排队" />
    </Row>)}
    {d && <View testID={`v4-decision-${round.round}`} style={{ gap: 2, paddingTop: 6 }}>
      <Copy small>轮末：合格 {d.qualified.length} 个{d.best !== null ? `，最好 #${d.best + 1}` : ''}{d.adopted ? `，采纳 ${short(d.adopted_id ?? d.adopted)}` : '，父版本不变'}</Copy>
      {d.merge && <Copy small muted>合并 #{d.merge.of.map(i => i + 1).join(' + #')}：{d.merge.skipped ? `跳过（${d.merge.skipped}）`
        : `Δval ${num(d.merge.delta_val)} ± ${num(d.merge.se_val)}，${d.merge.adopt ? '高于最好单个候选，采纳' : '不高于最好单个候选，不采纳'}`}</Copy>}
    </View>}
  </Section>;
}

function Ablation({ view }: { view: V4RunView }) {
  const ab = view.ablation;
  if (!ab) return null;
  const meta = ab.status === 'skipped' ? `跳过：${ab.reason}` : ab.status === 'running' ? '进行中' : ab.pruned.length ? `剪除 ${ab.pruned.length} 笔` : '无剪除';
  return <Section title="消融剪枝" meta={meta} testID="v4-ablation" defaultOpen={ab.status !== 'skipped'}>
    {ab.units.map(u => <KeyValue key={u.index} label={`去掉第 ${u.index} 笔（第 ${u.round} 轮）`}>
      <Copy small>{u.status === 'conflict' ? 'rebase 冲突，保留' : `Δ ${num(u.delta)} ± ${num(u.se)} → ${u.prune ? '不起作用，剪除' : '起作用，保留'}`}</Copy>
    </KeyValue>)}
    {ab.status === 'done' && ab.pruned.length > 0 && <Copy small>{ab.accepted ? `剪枝后版本复测 Δ ${num(ab.check?.delta)} ± ${num(ab.check?.se)}，作为终点的最终版本 ${short(ab.final_id ?? '')}`
      : `剪枝未采用：${ab.reason}`}</Copy>}
    <Copy small muted>消融在验证集上做，说明的是分数上的贡献；测试集保持干净。</Copy>
  </Section>;
}

function Endpoint({ view }: { view: V4RunView }) {
  const theme = useTheme();
  const ep = view.endpoint;
  if (!ep) return null;
  return <Section title="终点：测试集（只评一次）" meta={ep.conclusion === 'the test set confirms an improvement' ? '确认提升' : '未能确认提升'} testID="v4-endpoint">
    <Text style={{ fontSize: 15, fontWeight: '600', color: ep.confirmed ? theme.success : theme.text }}>
      Δ_test {num(ep.delta_test)}，95% 区间 [{num(ep.ci95?.[0])}, {num(ep.ci95?.[1])}]</Text>
    <Copy small muted>{ep.metric} · n = {ep.n} · k = {ep.k} · {ep.interval_method}</Copy>
    {ep.points.map(p => <KeyValue key={p.index} label={`${p.index}. ${kindLabels[p.kind ?? ''] ?? p.kind ?? ''} ${short(p.candidate_id)}`}>
      <Mono size={11}>分数 {num(p.score)}{p.index ? ` · Δ ${num(p.delta_test)} · 验证集累计 ${num(p.delta_val_cumulative)} · 缩水 ${num(p.shrinkage)}` : ''}</Mono>
    </KeyValue>)}
    <Copy small>部署：{short(ep.deploy ?? '')}。{ep.confirmed ? '' : '测试集未能确认提升；谱系、各改动的 diff 和合格候选仍然交付，供人判断。'}</Copy>
    {ep.warnings.map(w => <Copy key={w} small style={{ color: theme.warning }}>{w}</Copy>)}
  </Section>;
}

export function V4RunPage({ view, selected, onSelect }: { view: V4RunView; compact: boolean; selected: string | null; onSelect: (id: string | null) => void }) {
  const theme = useTheme();
  const rounds = [...view.rounds].reverse();
  return <View testID="auto-run" style={{ gap: 24 }}>
    <Status view={view} />
    <Progress view={view} />
    <Endpoint view={view} />
    <Calibration view={view} />
    {view.progress && <Lineage view={view} selected={selected} onSelect={onSelect} />}
    {rounds.map(r => <RoundView key={r.round} round={r} selected={selected} onSelect={onSelect} />)}
    <Ablation view={view} />
    <Text style={{ fontSize: 11, lineHeight: 16, color: theme.muted }}>这个迭代由 method-v4 自动运行驱动，手动操作已锁定。续跑和测试集评测用命令行 v4 run / v4 test。</Text>
  </View>;
}

// ---------------------------------------------------------------- detail column

type AgentProps = { sessions: TaskSession[]; live: Record<string, { status: string | null }>; onOpenAgent: (sessionId: string) => void };
const roleText: Record<string, string> = { analyst: '分析', implementer: '实现', reviewer: '审查' };

function Agents({ candidateId, agents }: { candidateId: string | null; agents: AgentProps }) {
  const own = candidateId ? liveSessions(agents.sessions).filter(s => s.candidate_id === candidateId) : [];
  if (!own.length) return null;
  return <Section title="Agent" testID="v4-agents">
    {own.map(s => <View key={s.id} style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
      <Copy small style={{ flex: 1 }}>{roleText[s.role ?? 'analyst'] ?? s.role} Agent · {short(s.agent_id)} · {agentStatusLabels[agents.live[s.id]?.status ?? ''] ?? '状态未知'}</Copy>
      <Button label="查看" onPress={() => agents.onOpenAgent(s.id)} testID={'v4-open-agent-' + s.id} />
    </View>)}
  </Section>;
}

function Changes({ candidateId }: { candidateId: string }) {
  const changes = useResource<CandidateChanges>(`/candidates/${candidateId}/changes`);
  return <Section title="改动" defaultOpen={false}>
    {!changes.data ? <Copy small muted>{changes.error ?? '正在读取…'}</Copy>
      : changes.data.files?.length ? changes.data.files.map(file => <DiffFile key={file.path} file={file} />) : <Copy small muted>没有差异记录</Copy>}
  </Section>;
}

function RecordDetail({ view, round, record, agents }: { view: V4RunView; round: V4Round; record: V4Record; agents: AgentProps }) {
  const theme = useTheme();
  const m = record.mechanism, q = record.qualification, sg = record.safeguards;
  return <View testID="v4-detail-record">
    <View style={{ gap: 6, paddingBottom: 10 }}>
      <Eyebrow>第 {round.round} 轮 · 卡片 #{record.card_index + 1}</Eyebrow>
      <Text selectable style={{ fontSize: 15, lineHeight: 22, fontWeight: '600', color: theme.text }}>{record.title ?? '—'}</Text>
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6 }}>
        <Badge label={record.label ? labelTexts[record.label] : stageLabels[record.stage]} tone={labelTone(record)} />
        {m?.tier && <Badge label={`机制${tierLabels[m.tier]}`} tone={tierTone(m.tier)} />}
        {record.repaired && <Badge label="修复后" />}
        {record.pruned && <Badge label="消融中被剪除" tone="warning" />}
      </View>
    </View>
    <Section title="级联">
      <KeyValue label="0 冒烟"><Copy small>{record.smoke ?? '—'}{record.smoke_reason ? `（${record.smoke_reason}）` : ''}</Copy></KeyValue>
      <KeyValue label="1 小批"><Mono>{record.delta_mb !== null ? `Δ ${num(record.delta_mb)} ± ${num(record.se_mb)}（n = ${record.n_mb}）` : '—'}</Mono></KeyValue>
      <KeyValue label="2 全验证集"><Mono>{record.delta_val !== null ? `Δ ${num(record.delta_val)} ± ${num(record.se_val)}（n = ${record.n_val}${record.dropped_val ? `，丢弃 ${record.dropped_val}` : ''}）` : '—'}</Mono></KeyValue>
      {record.se_val !== null && <KeyValue label="SE 来源"><Mono size={11}>自身 {num(record.se_own, 4)}（{record.se_method}）· A/A 下限 {num(record.se_floor, 4)}</Mono></KeyValue>}
      {q && <KeyValue label="采纳门槛"><Copy small>{q.coefficient}·SE = {num(q.threshold)} → {q.qualified ? '合格' : q.reasons.map(r => reasonTexts[r] ?? r).join('；')}</Copy></KeyValue>}
      {record.reason && !q && <KeyValue label="原因"><Copy small>{record.reason}</Copy></KeyValue>}
      {record.halted && <Copy small style={{ color: theme.warning }}>停机时在途：{record.halted}。续跑后重做这一阶段。</Copy>}
    </Section>
    {record.preflight && <Section title="实现前检查（实现者报告）">
      <KeyValue label="决定"><Copy small>{record.preflight.decision === 'skip' ? '跳过本卡' : '继续实现'} · {({ supported: '假设得到支持', refuted: '假设不成立', already_satisfied: '现有代码已满足', inconclusive: '尚无法判断' } as Record<string, string>)[record.preflight.reason] ?? record.preflight.reason}</Copy></KeyValue>
      <Copy small>{record.preflight.summary}</Copy>
      {record.preflight.checks.map((check, index) => <KeyValue key={index} label={check.target}>
        <Copy small>{check.observation}</Copy>
      </KeyValue>)}
    </Section>}
    {m && m.tier && <Section title="3 机制检查" meta={tierLabels[m.tier]} testID="v4-mechanism">
      <KeyValue label="机械检查"><Copy small>{!m.mechanical || m.mechanical.status === null ? '卡片没有行为预期'
        : `${m.mechanical.status}：父 ${pct(m.mechanical.parent_rate)} → 候选 ${pct(m.mechanical.candidate_rate)}（预测 ${m.mechanical.direction}，A/A 差距 ${pct(m.mechanical.aa_gap)}）`}</Copy></KeyValue>
      <KeyValue label="审查者"><Copy small>{m.reviewer.conclusion ?? '—'}{m.reviewer.trusted === false ? '（对照对判定失败，结论作废）' : ''}{m.reviewer.reason ? `；${m.reviewer.reason}` : ''}</Copy></KeyValue>
      {m.reviewer.verdicts.length > 0 && <Copy small muted>生效 {m.reviewer.effects ?? 0}，判错边 {m.reviewer.wrong_side ?? 0}，对照判生效 {m.reviewer.control_effects ?? 0}，副作用 {m.reviewer.side_effects ?? 0}，刷分 {m.reviewer.gaming.length}</Copy>}
      {m.reviewer.verdicts.map(v => <KeyValue key={v.pair} label={`${v.pair}${v.kind === 'control' ? '（对照）' : ''}`}>
        <Copy small>{v.effect ? `生效（${v.changed_side ?? '?'} 边）` : '未见变化'}{v.side_effect ? ' · 副作用' : ''}{v.gaming ? ' · 刷分' : ''}{v.note ? ` · ${v.note}` : ''}</Copy>
      </KeyValue>)}
    </Section>}
    {sg && <Section title="保护指标（点估计 vs 容忍量）" defaultOpen={!sg.ok}>
      {sg.rows.map(r => <KeyValue key={r.key} label={r.key}>
        <Mono size={11} color={r.breached ? theme.danger : undefined}>{num(r.reference)} → {num(r.subject)} · 损害 {num(r.harm)} / 容忍 {num(r.tolerance)}{r.breached === null ? ' · 未观测' : r.breached ? ' · 越界' : ''}</Mono>
      </KeyValue>)}
    </Section>}
    <Section title="耗时" defaultOpen={false}>
      {Object.entries(record.timings ?? {}).map(([k, v]) => <KeyValue key={k} label={k}><Copy small>{duration(v)}</Copy></KeyValue>)}
      {(['minibatch', 'rest'] as const).map(k => record.cache[k] && <KeyValue key={k} label={`${k} 准备缓存`}>
        <Copy small>命中 {record.cache[k]!.cache_hits} · 未命中 {record.cache[k]!.cache_misses}</Copy></KeyValue>)}
      {record.diff_lines !== null && <KeyValue label="diff 行数"><Mono>{record.diff_lines}</Mono></KeyValue>}
      {record.touches_preparation !== null && <KeyValue label="改动准备阶段代码"><Copy small>{record.touches_preparation ? '涉及准备代码（可能需要重新准备）' : '否'}</Copy></KeyValue>}
    </Section>
    <Agents candidateId={record.candidate_id} agents={agents} />
    {record.candidate_id && <Changes candidateId={record.candidate_id} />}
  </View>;
}

function NodeDetail({ node, agents }: { node: V4Node; agents: AgentProps }) {
  const theme = useTheme();
  return <View testID="v4-detail-node">
    <View style={{ gap: 6, paddingBottom: 10 }}>
      <Eyebrow>谱系节点 {node.index}</Eyebrow>
      <Text selectable style={{ fontSize: 15, lineHeight: 22, fontWeight: '600', color: theme.text }}>{node.kind === 'seed' ? '种子版本' : node.titles.filter(Boolean).join(' + ')}</Text>
    </View>
    <Section title="节点">
      <KeyValue label="类型"><Copy small>{kindLabels[node.kind] ?? node.kind}</Copy></KeyValue>
      <KeyValue label="候选"><Mono size={11}>{node.candidate_id ?? '—'}</Mono></KeyValue>
      {node.round ? <KeyValue label="轮"><Copy small>第 {node.round} 轮</Copy></KeyValue> : null}
      {node.kind !== 'seed' && <KeyValue label="Δ_val"><Mono>{num(node.delta_val)} ± {num(node.se_val)}</Mono></KeyValue>}
      {node.tier && <KeyValue label="机制"><Copy small>{tierLabels[node.tier]}</Copy></KeyValue>}
      {node.elapsed_s !== null && <KeyValue label="采纳时累计用时"><Copy small>{duration(node.elapsed_s)}</Copy></KeyValue>}
      {node.pruned && <Copy small style={{ color: theme.warning }}>这笔改动在消融中被剪除，不在终点的最终版本里。</Copy>}
    </Section>
    <Agents candidateId={node.candidate_id} agents={agents} />
    {node.candidate_id && node.kind !== 'seed' && <Changes candidateId={node.candidate_id} />}
  </View>;
}

function RunDetail({ view }: { view: V4RunView }) {
  const theme = useTheme();
  const { run, config } = view;
  return <View testID="v4-detail-run">
    <View style={{ gap: 6, paddingBottom: 10 }}>
      <Eyebrow>自动运行 · method-v4</Eyebrow>
      <Text selectable style={{ fontSize: 15, lineHeight: 22, fontWeight: '600', color: theme.text }}>{run.run_id ?? '—'}</Text>
    </View>
    <Section title="运行">
      <KeyValue label="操作者">{run.actor ?? '—'}</KeyValue>
      <KeyValue label="启动"><Copy small>{run.started_at ? date(run.started_at) : '—'}</Copy></KeyValue>
      <KeyValue label="配置哈希"><Mono size={11}>{short(run.config_sha256, 16)}</Mono></KeyValue>
      <KeyValue label="测试集"><Copy small>{run.test_set ? '有（只评一次）' : '无（演练）'}</Copy></KeyValue>
    </Section>
    {config && <Section title="预注册配置" testID="v4-config">
      {Object.entries(config).filter(([, v]) => v !== null && v !== undefined).map(([k, v]) =>
        <KeyValue key={k} label={k}><Mono size={11}>{typeof v === 'object' ? JSON.stringify(v) : String(v)}</Mono></KeyValue>)}
    </Section>}
    {Object.keys(view.variants).length > 0 && <Section title="方法变体（改过的方法常数）">
      {Object.entries(view.variants).map(([k, v]) => <KeyValue key={k} label={k}><Mono size={11}>{JSON.stringify(v.value)}（默认 {JSON.stringify(v.default)}）</Mono></KeyValue>)}
    </Section>}
    {view.time_account && <Section title="时间账" defaultOpen={false}>
      <KeyValue label="总墙钟"><Copy small>{duration(view.time_account.total_wall_s)}</Copy></KeyValue>
      <KeyValue label="校准"><Copy small>{duration(view.time_account.calibration_s)}</Copy></KeyValue>
      {Object.entries(view.time_account.candidate_sums_s).map(([k, v]) => <KeyValue key={k} label={`候选合计 ${k}`}><Copy small>{duration(v)}</Copy></KeyValue>)}
      {view.time_account.ablation_s !== null && <KeyValue label="消融"><Copy small>{duration(view.time_account.ablation_s)}</Copy></KeyValue>}
    </Section>}
    {view.deviations.length > 0 && <Section title="与方法文档的偏离" defaultOpen={false}>
      {view.deviations.map(d => <Copy key={d} small>· {d}</Copy>)}
    </Section>}
  </View>;
}

export function V4Detail({ view, selected, ...agents }: AgentProps & { view: V4RunView; selected: string | null }) {
  if (selected?.startsWith('rec:')) {
    const [, r, c] = selected.split(':');
    const round = view.rounds.find(x => String(x.round) === r);
    const record = round?.records.find(x => String(x.card_index) === c);
    if (round && record) return <RecordDetail view={view} round={round} record={record} agents={agents} />;
  }
  if (selected?.startsWith('node:')) {
    const node = view.lineage.find(n => n.version === selected.slice(5));
    if (node) return <NodeDetail node={node} agents={agents} />;
  }
  return <RunDetail view={view} />;
}
