import React, { useState } from 'react';
import { Pressable, Text, View } from 'react-native';
import { Button, Copy, hover, StatusDot, useTheme } from '../ui';
import { CreationProfile, date, liveSessions, TaskSession, useResource } from '../contracts';
import { CandidateChanges, short } from '../tree/contracts';
import { Badge, Delta, Eyebrow, KeyValue, Mono, Section } from '../tree/parts';
import { DiffFile } from '../tree/NodeDetail';
import { agentStatusLabels } from '../agents/AgentPicker';
import { ago, duration, num } from './contracts';
import { kindLabels, labelTexts, reasonTexts, stageLabels, tierLabels, V4Node, V4Record, V4Round, V4RunView, V4Tier, v4ActionTexts,
  v4HaltLabels, v4PhaseLabels, v4StatusLabels } from './v4contracts';

import { NewAutoRunWizard } from './NewAutoRunWizard';
import { V4Calibration, ForecastSummary } from './V4Calibration';
import { V4Controls } from './V4Controls';
import { SafeguardBars, V4Overview, V4RoundChart, V4Time } from './V4Charts';

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
    {!!view.progress && !!view.startup_error && <Copy testID="v4-startup-error" small style={{ color: theme.error }}>{view.startup_error.message}。本次启动已退出，请修复后重新预览。</Copy>}
    {run.halt_reason === 'stopped' && <Copy testID="v4-stopped" small style={{ color: theme.error }}>
      此运行已被终止（取消在途执行、归档 Agent），不能续跑。已完成的校准与测量仍保留；继续实验请新建迭代并复用校准。</Copy>}
    {!!view.progress?.failure_detail && <View testID="v4-failure-detail" style={{ gap: 4 }}>
      {(() => {
        // "<role> <CODE> (<Type>): <reason> intent_id=<id>" (sn_adapter.refusal_reason)
        const m = /^(\w+) ([A-Z][A-Z_]+) \(/.exec(view.progress.failure_detail ?? '');
        return m ? <View style={{ flexDirection: 'row', gap: 6 }}><Badge label={m[1]} /><Badge label={m[2]} tone="danger" /></View> : null;
      })()}
      <Copy small style={{ color: theme.error }}>{view.progress.failure_detail}</Copy>
      {view.progress.failure_kind === 'agent_request' && <Copy small muted>Agent 请求失败；已完成的测量已保留。请先核查创建或消息回执，再续跑。</Copy>}
    </View>}
    {run.status === 'halted' && run.halt_reason !== 'process_failure' && run.halt_reason !== 'budget_exhausted' &&
      <Copy small muted>停机原因只说明这次运行为什么结束，不代表改进空间已经用尽。</Copy>}
  </View>;
}

function Progress({ view }: { view: V4RunView }) {
  const theme = useTheme();
  const p = view.progress;
  if (!p) return null;
  const used = p.budget_s ? Math.min(1, (p.elapsed_s ?? 0) / p.budget_s) : null;
  return <View testID="v4-progress" style={{ flexDirection: 'row', flexWrap: 'wrap', borderWidth: 1, borderColor: theme.line, borderRadius: 8 }}>
    <Tile label="时间" value={`${duration(p.elapsed_s)}`} sub={used !== null ? `预算 ${duration(p.budget_s)} · 已用 ${pct(used)}` : undefined} />
    <Tile label="轮" value={String(p.rounds_finished + (p.round && !view.rounds.find(r => r.round === p.round && !r.open) ? 1 : 0))}
      sub={`连续无采纳 ${p.no_adoption}（不是停机条件）`} />
    <Tile label="采纳" value={String(p.adopted)} sub={`候选 ${p.candidates} 个`} />
    <Tile label="划分" value={`${p.search_size} / ${p.validation_size} / ${p.test_size ?? '—'}`} sub={`搜索 / 验证 / 测试${p.families ? ` · ${p.families} 篇论文` : ''}`} />
    <Tile label="流程故障" value={`${p.run_failures}/${p.run_failures_limit ?? '—'}`} sub={p.key_generation ? `key 代 ${p.key_generation}` : undefined} />
  </View>;
}

function Lineage({ view, selected, onSelect }: { view: V4RunView; selected: string | null; onSelect: (id: string | null) => void }) {
  return <Section title="谱系" meta={`${view.lineage.length - 1} 次采纳`} testID="v4-lineage">
    {view.lineage.map(n => <Row key={n.version} testID={`v4-node-${n.index}`} selected={selected === 'node:' + n.version} onPress={() => onSelect('node:' + n.version)}>
      <Mono size={11} muted>{n.index}</Mono>
      <Badge label={kindLabels[n.kind] ?? n.kind} tone={n.kind === 'seed' || n.kind === 'explore' ? undefined : 'success'} />
      <Copy small style={{ flex: 1 }}>{n.kind === 'seed' ? '种子版本' : n.titles.filter(Boolean).join(' + ') || short(n.candidate_id ?? n.version)}</Copy>
      {n.tier && <Badge label={`机制${tierLabels[n.tier]}`} tone={tierTone(n.tier)} />}
      {n.pruned && <Badge label="已剪除" tone="warning" />}
      {n.final && view.lineage.some(x => x.kind === 'explore') && <Badge label="最终版本" tone="success" />}
      {n.kind !== 'seed' && <Delta value={n.delta_val} />}
      {n.round ? <Mono size={11} muted>第 {n.round} 轮</Mono> : null}
    </Row>)}
  </Section>;
}

function AnalystProgress({ round }: { round: V4Round }) {
  const a = round.analysis_attempts;
  const since = round.started_at ? Math.max(0, (Date.now() - Date.parse(round.started_at)) / 1000) : null;
  return <View testID={`v4-analyst-progress-${round.round}`} style={{ gap: 2 }}>
    <Copy small>分析 Agent 起草中{since !== null ? ` · 本轮已 ${duration(since)}` : ''}{round.analysis_prefetched ? ' · 上一轮测量期间已预先派发' : ''}</Copy>
    <Copy small muted>{a && a.total ? `草稿 ${a.total} 次：保存 ${a.saved} · 格式不合格 ${a.invalid_card} · 含考题文本被拒 ${a.leak_refused} · 其他拒绝 ${a.refused}`
      + (a.last_at ? ` · 最近一次 ${ago(a.last_at)}` : '') : '还没有提交草稿'}</Copy>
  </View>;
}

function draftSummary(round: V4Round): string {
  const a = round.analysis_drafts?.attempts;
  if (!a) return '';
  const parts = [['saved', '已保存'], ['invalid_card', '格式不合法'], ['duplicate', '重复'], ['leak_refused', '泄漏扫描拒绝'], ['refused', '被拒']]
    .filter(([k]) => (a as Record<string, number | undefined>)[k]).map(([k, label]) => `${label} ${(a as Record<string, number>)[k]}`);
  return ` 本轮草稿 ${a.total ?? 0} 次${parts.length ? '：' + parts.join('，') : ''}。`;
}

function RoundView({ round, selected, onSelect }: { round: V4Round; selected: string | null; onSelect: (id: string | null) => void }) {
  const d = round.decision;
  const meta = round.open ? '进行中' : d?.adopted ? (d.merge?.adopt ? '采纳合并候选' : '有采纳') : '无采纳';
  return <Section title={`第 ${round.round} 轮`} meta={`${meta} · 卡片 ${round.cards ?? '—'}`} testID={`v4-round-${round.round}`} defaultOpen={round.open || !!d?.adopted}>
    <Copy small muted>父版本 {short(round.parent_id ?? round.parent ?? '')} · 小批 {round.minibatch_size} 题 · 其余 {round.rest_size} 题
      {round.cards !== null ? ` · 带行为预期 ${round.cards_with_expectation}/${round.cards}` : ''}{round.wall_s ? ` · 用时 ${duration(round.wall_s)}` : ''}
      {round.diagnosis_s !== null ? ` · 诊断 ${round.diagnosis_prefetched ? '已预取' : duration(round.diagnosis_s)}` : ''}
      {round.not_started ? ` · ${round.not_started} 张卡${round.timeboxed ? '因轮时间盒' : ''}未开始` : ''}</Copy>
    {round.open && round.cards === null && <AnalystProgress round={round} />}
    {d?.explore?.chosen != null && <Copy small muted>本轮未达门槛，探索采纳 #{d.explore.chosen + 1}：不比父版本差超过 {d.explore.margin}，作为后续轮次的起点，不计为提升。</Copy>}
    {round.analysis_empty && <Copy small muted>分析没有产出卡片，计为一次流程故障。{draftSummary(round)}</Copy>}
    {!round.analysis_empty && round.analysis_drafts?.deduped ? <Copy small muted>{round.analysis_drafts.deduped} 张卡与已试过的卡重复，未进入流水线。</Copy> : null}
    <V4RoundChart round={round} onSelect={onSelect} />
    {round.records.map(r => <Row key={r.card_index} testID={`v4-record-${round.round}-${r.card_index}`} selected={selected === recordKey(round.round, r.card_index)}
      onPress={() => onSelect(recordKey(round.round, r.card_index))}>
      <Mono size={11} muted>#{r.card_index + 1}</Mono>
      <Copy small style={{ flex: 1 }}>{r.title ?? '—'}</Copy>
      {r.flags?.includes('needs_human_review') && <Badge label="需人工查看" tone="warning" />}
      {r.edits === 'analyst_edits' && <Badge label="直接改动" />}
      {r.edits?.startsWith('fallback') && <Badge label="改动回退给实现" tone="warning" />}
      {r.carry_from && <Badge label="上轮带入" />}
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

function Terminology() {
  const theme = useTheme();
  const terms = [
    ['Δ', '候选相对父版本的同题配对均分差；正值表示本次验证集得分更高。'],
    ['SE', '差值的标准误；图中 Δ ± SE 不是 95% 置信区间。'],
    ['噪声下限', '由种子独立重复测量估计；最终 SE 取配对估计与噪声下限中的较大值。'],
    ['机制档位', '机制确认可把采纳门槛从 1·SE 放宽到 0.5·SE；需人工审查时取消放宽。'],
    ['保护指标', '限制产品退化：损害需超过产品容忍与噪声余量，比例指标还需至少涉及两题。'],
    ['复测', '采纳前独立再测，用两次测量合并结果确认；基础设施重试不算独立复测。'],
  ];
  const [active, setActive] = useState<number | null>(null);
  return <View style={{ gap: 5 }}><View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 10 }}>
    {terms.map(([name], i) => <Pressable key={name} accessibilityRole="button" accessibilityLabel={name + '：' + terms[i][1]} onHoverIn={() => setActive(i)} onHoverOut={() => setActive(null)} onPress={() => setActive(active === i ? null : i)}><Copy small style={{ color: theme.primary }}>{name}</Copy></Pressable>)}
  </View>{active != null && <Copy small muted>{terms[active][1]}</Copy>}</View>;
}

export function V4RunPage({ view, selected, onSelect, onRefresh, profiles, sessions, live, onOpenAgent }: AgentProps & {
  view: V4RunView; compact: boolean; selected: string | null; onSelect: (id: string | null) => void;
  onRefresh: () => void; profiles?: CreationProfile[];
}) {
  const theme = useTheme();
  const [tab, setTab] = useState('overview');
  const [help, setHelp] = useState(() => { try { return localStorage.getItem('v41-page-help') !== 'seen'; } catch { return true; } });
  const tabs = [['overview', '概览'], ['rounds', '轮次'], ['calibration', '校准'], ['time', '时间与花费'], ['config', '配置与账本']];
  const running = liveSessions(sessions).filter(s => ['running', 'busy', 'starting'].includes(live[s.id]?.status ?? ''));
  return <View testID="auto-run" style={{ gap: 18 }}>
    <Status view={view} />
    <V4Controls view={view} onRefresh={onRefresh} />
    {!view.progress && view.run.iteration_id ? <NewAutoRunWizard iterationId={view.run.iteration_id} profiles={profiles} onStarted={onRefresh} /> : <>
      <Button label={help ? '收起阅读说明' : '怎么读这个页面'} onPress={() => { setHelp(v => !v); try { localStorage.setItem('v41-page-help', 'seen'); } catch {} }} />
      {help && <Copy small muted>Δ：候选相对父版本的配对均分差；SE：这个差值的不确定性，取配对估计与校准噪声下限中的较大值。机制确认可将门槛从 1·SE 放宽到 0.5·SE；保护指标限制产品退化；复测用独立尝试再确认采纳。图中提升均是验证集估计。</Copy>}
      <Terminology />
      <View style={{ flexDirection: 'row', gap: 5, flexWrap: 'wrap' }}>{tabs.map(([id, label]) => <Button key={id} accessibilityRole="tab" label={label} selected={tab === id} onPress={() => setTab(id)} testID={'v41-tab-' + id} />)}</View>
      {tab === 'overview' && <>
        <Progress view={view} /><V4Overview view={view} onSelect={onSelect} />
        {running.length > 0 && <Section title="在途 Agent">{running.map(s => <Button key={s.id} label={`${s.role ?? 'Agent'} · ${short(s.agent_id)}`} onPress={() => onOpenAgent(s.id)} />)}</Section>}
        <Lineage view={view} selected={selected} onSelect={onSelect} /><Endpoint view={view} /><Ablation view={view} />
      </>}
      {tab === 'rounds' && [...view.rounds].reverse().map(r => <RoundView key={r.round} round={r} selected={selected} onSelect={onSelect} />)}
      {tab === 'calibration' && <V4Calibration detail={view.calibration_detail} forecast={view.forecast} iterationId={view.run.iteration_id} />}
      {tab === 'time' && <V4Time view={view} />}
      {tab === 'config' && <RunDetail view={view} />}
    </>}
    <Text style={{ fontSize: 11, color: theme.muted }}>自动优化使用冻结配置。暂停保留远端工作；停止取消在途工作。终点评测需单独确认。</Text>
  </View>;
}

// ---------------------------------------------------------------- detail column

type AgentProps = { sessions: TaskSession[]; live: Record<string, { status: string | null }>; onOpenAgent: (sessionId: string) => void };
const roleText: Record<string, string> = { calibrator: '校准顾问', analyst: '分析', implementer: '实现', reviewer: '审查' };

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
    {record.flags?.includes('needs_human_review') && <Copy style={{ color: theme.warning }}>需人工查看：审查报告了潜在刷分，取消机制门槛放宽。</Copy>}
    <Section title="假设与实现者检查">
      <Copy small>{record.title ?? '没有记录假设描述'}</Copy>
      <Copy small muted>方向：{record.direction ?? '未标注'}{record.direction_inferred ? '（由旧标题推断）' : ''}</Copy>
      {record.preflight && <><Copy small>{record.preflight.summary} · {record.preflight.decision}</Copy>{record.preflight.checks.map((c, i) => <Copy small muted key={i}>{c.kind} · {c.target}：{c.observation}</Copy>)}</>}
      {record.failure_kind && <Copy small style={{ color: theme.error }}>实现失败类别：{record.failure_kind}</Copy>}
    </Section>
    <Section title="测量与复测">
      <KeyValue label="0 冒烟"><Copy small>{record.smoke ?? '—'}{record.smoke_reason ? `（${record.smoke_reason}）` : ''}</Copy></KeyValue>
      <KeyValue label="1 小批"><Mono>{record.delta_mb !== null ? `Δ ${num(record.delta_mb)} ± ${num(record.se_mb)}（n = ${record.n_mb}）` : '—'}</Mono></KeyValue>
      <KeyValue label="2 全验证集"><Mono>{record.delta_val !== null ? `Δ ${num(record.delta_val)} ± ${num(record.se_val)}（n = ${record.n_val}${record.dropped_val ? `，丢弃 ${record.dropped_val}` : ''}）` : '—'}</Mono></KeyValue>
      {record.se_val !== null && <KeyValue label="SE 来源"><Mono size={11}>自身 {num(record.se_own, 4)}（{record.se_method}）· A/A 下限 {num(record.se_floor, 4)}</Mono></KeyValue>}
      {record.remeasure && <KeyValue label="采纳前独立复测"><Copy small>{record.remeasure.passed === true ? '通过' : record.remeasure.passed === false ? '未通过' : record.remeasure.status} · 合并 Δ {num(record.remeasure.delta_val)} ± {num(record.remeasure.se_val)} · n = {record.remeasure.n_val ?? '—'}{record.remeasure.reason ? ` · ${record.remeasure.reason}` : ''}</Copy></KeyValue>}
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
      {m.reviewer.verdicts.length > 0 && <View testID="v41-review-matrix" style={{ gap: 3 }}>
        <View style={{ flexDirection: 'row', gap: 4 }}>{['配对', '生效', '改动边', '副作用', '刷分'].map(h => <Copy key={h} small muted style={{ flex: 1 }}>{h}</Copy>)}</View>
        {m.reviewer.verdicts.map(v => <View key={v.pair} style={{ gap: 2, paddingVertical: 4, borderTopWidth: 1, borderColor: theme.line }}>
          <View style={{ flexDirection: 'row', gap: 4 }}>
            <Copy small style={{ flex: 1 }}>{v.pair}{v.kind === 'control' ? '（对照）' : ''}</Copy>
            {[v.effect, v.changed_side, v.side_effect, v.gaming].map((value, i) => <Copy key={i} small style={{ flex: 1, color: i === 3 && value === true ? theme.warning : theme.text }}>{value === true ? '是' : value === false ? '否' : value ?? '未知'}</Copy>)}
          </View>
          {v.note && <Copy small muted>{v.note}</Copy>}
        </View>)}
      </View>}
    </Section>}
    {sg && <Section title="保护指标：产品容忍与噪声" defaultOpen={!sg.ok}><SafeguardBars safeguards={sg} /></Section>}
    <Section title="结论"><Copy small>{record.label ? labelTexts[record.label] : stageLabels[record.stage]}{record.reason ? `：${record.reason}` : ''}</Copy></Section>
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
        <KeyValue key={k} label={k}><Mono size={11}>{typeof v === 'object' ? JSON.stringify(v) : String(v)} · {view.parameter_sources?.[k] ?? '来源未记录'}</Mono></KeyValue>)}
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
