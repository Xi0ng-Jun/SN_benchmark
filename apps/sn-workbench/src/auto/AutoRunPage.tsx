import React, { useState } from 'react';
import { Pressable, Text, View } from 'react-native';
import { Copy, hover, StatusDot, useTheme } from '../ui';
import { signed, short } from '../tree/contracts';
import { Badge, Delta, Eyebrow, Mono, Section } from '../tree/parts';
import { actionTexts, ago, AttemptVerdict, attemptLabels, attemptWhy, attributionLabels, AutoAttempt, AutoCard, AutoConfirmation, AutoLedger, AutoNode, AutoOpenRound,
  AutoProgress, AutoRound, AutoRunView, AutoSegment, haltLabels, NodeState, num, Phase, phaseLabels, SealedCurve, SealedPoint, segmentVerdictLabels, statusLabels } from './contracts';
import { curveSelection, SealedChart, SealedLegend } from './SealedChart';

// The automatic run, read top to bottom: how is it now, how far can it still go,
// what did it get, what is it doing, what can be claimed. Nothing here writes.
// Exception: sealed evaluation trigger is a manual action available in the UI.

const PLOT_WIDTH = 200;
const NUMBERS_WIDTH = 172;

// One marker across the page, the same as the sealed curve (method-v3 §6):
// solid = certified, hollow = provisional, dashed = rolled back.
export function Marker({ state, size = 10 }: { state: NodeState | 'seed'; size?: number }) {
  const theme = useTheme();
  const base = { width: size, height: size, borderRadius: size, borderWidth: 1.5 };
  if (state === 'rolled_back') return <View style={{ ...base, borderStyle: 'dashed', borderColor: theme.muted, backgroundColor: theme.bg }} />;
  if (state === 'provisional') return <View style={{ ...base, borderColor: theme.primary, backgroundColor: theme.bg }} />;
  const fill = state === 'seed' ? theme.muted : theme.success;
  return <View style={{ ...base, borderColor: fill, backgroundColor: fill }} />;
}

function StatusBand({ view, onTriggerSealed }: { view: AutoRunView; onTriggerSealed?: () => void }) {
  const theme = useTheme();
  const { run, progress, next_action: action } = view;
  const headline = run.status === 'running' ? (progress ? `第 ${progress.round} 轮 · ${phaseLabels[progress.phase]}` : '正在启动')
    : run.status === 'halted' ? haltLabels[run.halt_reason ?? ''] ?? run.halt_reason ?? '已停机'
    : run.status === 'interrupted' ? `停在第 ${progress?.round ?? 0} 轮` : '已登记，还没有写入状态';
  const tone = run.status === 'running' ? 'running' as const : action === 'resume' || action === 'retry' || action === 'budget' ? 'warning' as const : 'idle' as const;
  const canTriggerSealed = action === 'sealed' && onTriggerSealed;
  return <View testID="auto-status" style={{ gap: 10 }}>
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
      <StatusDot tone={tone} size={8} />
      <Text testID="auto-status-label" style={{ fontSize: 16, lineHeight: 22, fontWeight: '600', color: theme.text }}>{statusLabels[run.status]}</Text>
      <Text style={{ fontSize: 14, lineHeight: 22, color: theme.secondary, flexShrink: 1 }}>{headline}</Text>
      <View style={{ flex: 1 }} />
      <Text style={{ fontSize: 12, color: theme.muted }}>更新于 {ago(run.updated_at)}</Text>
    </View>
    {action && <View testID="auto-next-action" style={{ borderWidth: 1, borderColor: theme.line, borderRadius: 8, backgroundColor: theme.surface, paddingHorizontal: 12, paddingVertical: 10, gap: 2 }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
        <View style={{ flex: 1, gap: 2 }}>
          <Text style={{ fontSize: 13, lineHeight: 20, fontWeight: '500', color: action === 'sealed' || action === 'review' ? theme.text : theme.warning }}>{actionTexts[action].title}</Text>
          <Copy small muted>{actionTexts[action].detail}</Copy>
        </View>
        {canTriggerSealed && <Pressable onPress={onTriggerSealed} testID="trigger-sealed-btn"
          style={hover(hovered => ({ paddingHorizontal: 16, paddingVertical: 8, borderRadius: 6, backgroundColor: hovered ? theme.primaryHover : theme.primary }))}
          accessibilityRole="button" accessibilityLabel="触发封存集评测">
          <Text style={{ fontSize: 13, lineHeight: 20, fontWeight: '500', color: theme.bg }}>评测封存集</Text>
        </Pressable>}
      </View>
    </View>}
    {run.status === 'halted' && run.halt_reason !== 'process_failure' && run.halt_reason !== 'budget_exhausted' &&
      <Copy small muted>停机原因只说明这次运行为什么结束，不代表改进空间已经用尽。</Copy>}
  </View>;
}

function Meter({ ratio, width }: { ratio: number; width?: number }) {
  const theme = useTheme();
  const filled = Math.max(0, Math.min(1, ratio));
  return <View style={{ height: 4, borderRadius: 2, backgroundColor: theme.selected, overflow: 'hidden', width: width as number }}>
    <View style={{ height: 4, width: `${filled * 100}%`, borderRadius: 2, backgroundColor: theme.primary }} />
  </View>;
}

function Tile({ label, value, sub, children, testID }: { label: string; value?: string; sub?: string; children?: React.ReactNode; testID?: string }) {
  const theme = useTheme();
  return <View testID={testID} style={{ flexGrow: 1, flexBasis: 130, minWidth: 124, gap: 4, paddingVertical: 12, paddingHorizontal: 12 }}>
    <Text style={{ fontSize: 12, lineHeight: 16, color: theme.muted }}>{label}</Text>
    {value !== undefined && <Text style={{ fontSize: 20, lineHeight: 26, fontWeight: '600', color: theme.text }}>{value}</Text>}
    {children}
    {!!sub && <Text style={{ fontSize: 11, lineHeight: 15, color: theme.muted }}>{sub}</Text>}
  </View>;
}

function Streak({ label, value, limit }: { label: string; value: number; limit: number | null }) {
  const theme = useTheme();
  const close = limit !== null && value > 0 && value >= limit - 1;
  return <View style={{ flexDirection: 'row', justifyContent: 'space-between', gap: 8, maxWidth: 180 }}>
    <Text style={{ fontSize: 12, lineHeight: 18, color: theme.secondary }}>{label}</Text>
    <Mono size={12} color={close ? theme.warning : theme.text}>{value}/{limit ?? '—'}</Mono>
  </View>;
}

function Progress({ progress }: { progress: AutoProgress }) {
  const theme = useTheme();
  const cumulative = progress.cumulative_delta_val ?? 0;
  const left = Math.max(0, progress.fresh_total - progress.fresh_used);
  return <View testID="auto-progress" style={{ flexDirection: 'row', flexWrap: 'wrap', borderWidth: 1, borderColor: theme.line, borderRadius: 12 }}>
    <Tile label="已确认里程碑" value={String(progress.milestones_certified)} sub={`已确认 ${progress.confirmations_used} 次，上限 ${progress.max_confirmations ?? '—'}`} testID="kpi-milestones" />
    <Tile label="新鲜批次" value={`${progress.fresh_used} / ${progress.fresh_total}`} sub={`还剩 ${left} 批，用后并入验证池`} testID="kpi-fresh">
      <View style={{ flexDirection: 'row', gap: 3 }}>{Array.from({ length: progress.fresh_total }, (_, i) =>
        <View key={i} style={{ flex: 1, maxWidth: 28, height: 4, borderRadius: 2, backgroundColor: i < progress.fresh_used ? theme.primary : theme.selected }} />)}</View>
    </Tile>
    <Tile label="本段累积 Δ_val" value={signed(cumulative)} sub={`到 T = ${num(progress.T)} 时触发确认`} testID="kpi-cumulative">
      <Meter ratio={progress.T ? cumulative / progress.T : 0} />
    </Tile>
    <Tile label="验证池" value={String(progress.validation_size)} sub={`初始 ${progress.validation_initial} · m ${num(progress.ladder_m)} · best ${num(progress.best_score)}`} testID="kpi-pool" />
    <Tile label="距离停机" testID="kpi-halt">
      <Streak label="连续无过筛" value={progress.no_screen_streak} limit={progress.no_screen_limit} />
      <Streak label="连续未确认" value={progress.rejected_streak} limit={progress.rejected_limit} />
    </Tile>
  </View>;
}

// Δ with its interval on the scale all segments share, so "does it cross zero" reads at a glance.
export function IntervalPlot({ lower, upper, center, scale, tone, width = PLOT_WIDTH, title, marker }: {
  lower: number | null; upper: number | null; center: number | null; scale: number; tone: string; width?: number; title?: string; marker?: React.ReactNode;
}) {
  const theme = useTheme();
  const at = (value: number) => Math.max(0, Math.min(width, width * (value + scale) / (2 * scale)));
  return <View {...({ title } as object)} style={{ width, height: 18, justifyContent: 'center' }}>
    <View style={{ position: 'absolute', left: at(0), top: 0, bottom: 0, width: 1, backgroundColor: theme.lineStrong }} />
    {lower !== null && upper !== null && <View style={{ position: 'absolute', left: at(lower), width: Math.max(2, at(upper) - at(lower)), height: 2, borderRadius: 1, backgroundColor: tone }} />}
    {center !== null && (marker
      ? <View style={{ position: 'absolute', left: at(center) - 6, width: 12, height: 12, alignItems: 'center', justifyContent: 'center' }}>{marker}</View>
      : <View style={{ position: 'absolute', left: at(center) - 6, width: 12, height: 12, borderRadius: 6, borderWidth: 2, borderColor: theme.bg, backgroundColor: tone }} />)}
  </View>;
}

// The trunk is the path that stands. A rolled-back segment leaves it at its header
// ('head') and runs dashed beside it down to its last node ('last').
type Branch = 'head' | 'mid' | 'last';
const RAIL = 21;
function Rail({ trunk, marker, branch }: { trunk: 'start' | 'through' | 'end' | 'none'; marker?: React.ReactNode; branch?: Branch }) {
  const theme = useTheme();
  const line = trunk === 'none' ? null : { position: 'absolute' as const, left: 10, width: 1, backgroundColor: theme.lineStrong,
    top: trunk === 'start' ? '50%' as const : 0, bottom: trunk === 'end' ? '50%' as const : 0 };
  return <View style={{ flexDirection: 'row', alignSelf: 'stretch', flexShrink: 0 }}>
    <View style={{ width: RAIL, alignItems: 'center', justifyContent: 'center' }}>
      {line && <View style={line} />}
      {!branch && marker}
    </View>
    {branch && <View style={{ width: RAIL, alignItems: 'center', justifyContent: 'center' }}>
      <View testID="rail-branch" style={{ position: 'absolute', left: 10, width: 0, borderLeftWidth: 1, borderStyle: 'dashed', borderColor: theme.muted,
        top: branch === 'head' ? '50%' : 0, bottom: branch === 'last' ? '50%' : 0 }} />
      {branch === 'head' && <View style={{ position: 'absolute', left: -RAIL + 10, width: RAIL, top: '50%', height: 0, borderTopWidth: 1, borderStyle: 'dashed', borderColor: theme.muted }} />}
      {marker}
    </View>}
  </View>;
}

function RowPress({ selected, onPress, testID, children }: { selected?: boolean; onPress: () => void; testID?: string; children: React.ReactNode }) {
  const theme = useTheme();
  return <Pressable onPress={onPress} testID={testID} accessibilityRole="button" accessibilityState={{ selected }}
    style={hover(hovered => ({ flexDirection: 'row', alignItems: 'center', gap: 8, minHeight: 32, paddingRight: 8, borderRadius: 6,
      backgroundColor: selected ? theme.selected : hovered ? theme.surface : 'transparent' }))}>{children}</Pressable>;
}

const segmentTone = (c: AutoConfirmation, theme: ReturnType<typeof useTheme>) =>
  c.verdict === 'confirmed' ? theme.success : c.verdict === 'rejected' ? theme.danger : theme.warning;

function SegmentHeader({ segment, trunk, scale, narrow, progress, selected, onPress }: {
  segment: AutoSegment; trunk: 'through' | 'end' | 'none'; scale: number; narrow: boolean; progress: AutoProgress | null; selected: boolean; onPress: () => void;
}) {
  const theme = useTheme();
  const c = segment.confirmation;
  const rolled = segment.status === 'rolled_back';
  const attribution = c?.attribution;
  const statusLabel = segment.status === 'certified' ? '已确认' : segment.status === 'open' ? '暂定中' : c ? `已回滚 · ${segmentVerdictLabels[c.verdict]}` : '已回滚';
  const open = segment.status === 'open' && progress;
  return <RowPress selected={selected} onPress={onPress} testID={`segment-${segment.index}`}>
    <Rail trunk={trunk} branch={rolled ? (segment.nodes.length ? 'head' : undefined) : undefined} />
    <View style={{ flex: 1, minWidth: 0, flexDirection: narrow ? 'column' : 'row', alignItems: narrow ? 'flex-start' : 'center', gap: narrow ? 2 : 8, paddingVertical: narrow ? 6 : 0 }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, flexShrink: 0 }}>
        <Text style={{ fontSize: 13, lineHeight: 20, fontWeight: '500', color: rolled ? theme.muted : theme.text, flexShrink: 0 }}>段 {segment.index}</Text>
        <Badge label={statusLabel} tone={segment.status === 'certified' ? 'success' : segment.status === 'open' ? 'primary' : undefined} testID={`segment-${segment.index}-status`} />
        {!narrow && segment.status === 'certified' && attribution && <Badge label={`归因：${attributionLabels[attribution.label]}${attribution.possibly_biased ? '（可能有偏）' : ''}`}
          tone={attribution.label === 'supported' ? 'success' : attribution.label === 'refuted' ? 'danger' : undefined} />}
      </View>
      {!narrow && <View style={{ flex: 1 }} />}
      {c && !narrow && <IntervalPlot lower={c.lower} upper={c.upper} center={c.delta_fresh} scale={scale} tone={segmentTone(c, theme)}
        title={`Δ_fresh ${num(c.delta_fresh)}，区间 [${num(c.lower)}, ${num(c.upper)}]，n = ${c.n_fresh ?? '—'}`} />}
      {c && <View style={{ width: narrow ? undefined : NUMBERS_WIDTH, alignItems: 'flex-end' }}>
        <Mono size={11} muted={rolled}>{signed(c.delta_fresh)} [{signed(c.lower)}, {signed(c.upper)}]</Mono>
      </View>}
      {open && !narrow && <View style={{ width: PLOT_WIDTH }}><Meter ratio={progress.T ? (progress.cumulative_delta_val ?? 0) / progress.T : 0} /></View>}
      {open && <View style={{ width: narrow ? undefined : NUMBERS_WIDTH, alignItems: 'flex-end' }}>
        <Mono size={11} muted>累积 {num(progress.cumulative_delta_val)} / T {num(progress.T)}</Mono>
      </View>}
    </View>
  </RowPress>;
}

function NodeRow({ node, trunk, branch, selected, onPress }: { node: AutoNode; trunk: 'through' | 'end' | 'none'; branch?: Branch; selected: boolean; onPress: () => void }) {
  const theme = useTheme();
  return <RowPress selected={selected} onPress={onPress} testID={`lineage-node-${node.version_hash}`}>
    <Rail trunk={trunk} branch={branch} marker={<Marker state={node.state} />} />
    <Text numberOfLines={1} style={{ flex: 1, fontSize: 13, lineHeight: 20, color: node.state === 'rolled_back' ? theme.muted : theme.secondary }}>{node.title ?? short(node.version_hash)}</Text>
    <Mono size={11} muted>{short(node.candidate_id ?? node.version_hash)}</Mono>
    <View style={{ width: 60, alignItems: 'flex-end' }}><Delta value={node.delta_val} /></View>
  </RowPress>;
}

function Lineage({ view, compact, selected, onSelect }: { view: AutoRunView; compact: boolean; selected: string | null; onSelect: (id: string | null) => void }) {
  const theme = useTheme();
  const { segments, seed, progress } = view;
  const bounds = segments.flatMap(s => s.confirmation ? [s.confirmation.lower, s.confirmation.upper] : []).filter((x): x is number => typeof x === 'number');
  const scale = Math.max(0.01, ...bounds.map(Math.abs)) * 1.15;
  // The trunk runs to the last node that stands, or to the header of a later rolled-back segment leaving it.
  const flat = segments.flatMap(s => [{ s, n: null as AutoNode | null, last: false }, ...s.nodes.map((n, i) => ({ s, n, last: i === s.nodes.length - 1 }))]);
  const lastMain = flat.reduce((last, row, i) => (row.n ? row.s.status !== 'rolled_back' : row.s.status === 'rolled_back') ? i : last, -1);
  const trunkAt = (i: number): 'through' | 'end' | 'none' => i < lastMain ? 'through' : i === lastMain ? 'end' : 'none';
  return <View testID="auto-lineage">
    <View style={{ flexDirection: 'row', alignItems: 'center', height: 24, paddingRight: 8 }}>
      <Eyebrow>谱系</Eyebrow>
      <View style={{ flex: 1 }} />
      {!compact && bounds.length > 0 && <View style={{ flexDirection: 'row', gap: 8 }}>
        <View style={{ width: PLOT_WIDTH, flexDirection: 'row', justifyContent: 'space-between' }}>
          <Mono size={10} muted>{signed(-scale, 2)}</Mono><Mono size={10} muted>0</Mono><Mono size={10} muted>{signed(scale, 2)}</Mono>
        </View>
        <View style={{ width: NUMBERS_WIDTH, alignItems: 'flex-end' }}><Text style={{ fontSize: 11, color: theme.muted }}>Δ_fresh [区间]</Text></View>
      </View>}
    </View>
    {seed && <RowPress onPress={() => onSelect(null)} testID="lineage-seed">
      <Rail trunk={lastMain >= 0 ? 'start' : 'none'} marker={<Marker state="seed" />} />
      <Text style={{ flex: 1, fontSize: 13, lineHeight: 20, color: theme.secondary }}>种子</Text>
      <Mono size={11} muted>{short(seed.candidate_id ?? seed.version_hash)}</Mono>
      <View style={{ width: 60 }} />
    </RowPress>}
    {flat.map((row, i) => row.n
      ? <NodeRow key={row.n.version_hash} node={row.n} trunk={trunkAt(i)} branch={row.s.status === 'rolled_back' ? (row.last ? 'last' : 'mid') : undefined}
        selected={selected === (row.n.attempt ? 'att:' + row.n.attempt : 'node:' + row.n.version_hash)}
        onPress={() => onSelect(row.n!.attempt ? 'att:' + row.n!.attempt : 'node:' + row.n!.version_hash)} />
      : <SegmentHeader key={'seg' + row.s.index} segment={row.s} trunk={trunkAt(i)} scale={scale} narrow={compact} progress={progress}
        selected={selected === 'seg:' + row.s.index} onPress={() => onSelect('seg:' + row.s.index)} />)}
    {!segments.length && <Copy small muted>还没有候选通过筛选。</Copy>}
    {segments.length > 0 && <View testID="lineage-legend" style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 14, paddingTop: 8, paddingLeft: 5 }}>
      {(['certified', 'provisional', 'rolled_back'] as const).map(state => <View key={state} style={{ flexDirection: 'row', alignItems: 'center', gap: 5 }}>
        <Marker state={state} size={8} /><Text style={{ fontSize: 11, color: theme.muted }}>{{ certified: '已确认', provisional: '暂定', rolled_back: '已回滚' }[state]}</Text>
      </View>)}
    </View>}
  </View>;
}

const RESULT_WIDE = 150;
const RESULT_NARROW = 110;
const verdictTone = (verdict: AttemptVerdict) => verdict === 'screen_pass' ? 'success' as const : verdict === 'error' || verdict === 'unmeasurable' ? 'warning' as const : 'idle' as const;

function AttemptHead({ compact }: { compact: boolean }) {
  const theme = useTheme();
  const cell = { fontSize: 11, lineHeight: 16, color: theme.muted };
  return <View style={{ flexDirection: 'row', gap: 8, paddingHorizontal: 8, paddingBottom: 2 }}>
    <Text style={[cell, { flex: 1 }]}>卡片</Text>
    <Text style={[cell, { width: compact ? RESULT_NARROW : RESULT_WIDE }]}>结果</Text>
    <Text style={[cell, { width: 60, textAlign: 'right' }]}>Δ_val</Text>
    {!compact && <Text style={[cell, { width: 60, textAlign: 'right' }]}>下界</Text>}
    {!compact && <Text style={[cell, { width: 112, textAlign: 'right' }]}>分数 / 门槛</Text>}
  </View>;
}

function AttemptRow({ attempt, compact, selected, onPress }: { attempt: AutoAttempt; compact: boolean; selected: boolean; onPress: () => void }) {
  const theme = useTheme();
  const passed = attempt.verdict === 'screen_pass';
  return <RowPress selected={selected} onPress={onPress} testID={`attempt-${attempt.key}`}>
    <View style={{ flex: 1, minWidth: 0, flexDirection: 'row', alignItems: 'center', gap: 8, paddingLeft: 8 }}>
      <StatusDot tone={verdictTone(attempt.verdict)} />
      <Text numberOfLines={1} style={{ flex: 1, fontSize: 13, lineHeight: 20, color: passed ? theme.text : theme.secondary }}>{attempt.title ?? `卡片 ${attempt.card_index + 1}`}</Text>
    </View>
    <View style={{ width: compact ? RESULT_NARROW : RESULT_WIDE }} {...({ title: attemptWhy(attempt) } as object)}>
      <Text numberOfLines={1} style={{ fontSize: 12, lineHeight: 18, color: passed ? theme.text : theme.secondary }}>{attempt.halted ? '未决' : attemptLabels[attempt.verdict]}
        {!passed && <Text style={{ color: theme.muted }}> · {attemptWhy(attempt)}</Text>}</Text>
    </View>
    <View style={{ width: 60, alignItems: 'flex-end' }}><Delta value={attempt.delta_val} /></View>
    {!compact && <View style={{ width: 60, alignItems: 'flex-end' }}><Mono size={12} muted>{signed(attempt.lower_val)}</Mono></View>}
    {!compact && <View style={{ width: 112, alignItems: 'flex-end' }}><Mono size={12} muted>{num(attempt.score_val)} / {num(attempt.threshold)}</Mono></View>}
  </RowPress>;
}

const PHASES: { phase: Phase[]; label: string }[] = [
  { phase: ['analysing'], label: '分析' }, { phase: ['building'], label: '实现 · 冒烟 · 筛选' }, { phase: ['confirming', 'refreshing_best'], label: '里程碑确认' },
];

function Stepper({ phase, alive }: { phase: Phase; alive: boolean }) {
  const theme = useTheme();
  const at = PHASES.findIndex(p => p.phase.includes(phase));
  return <View testID="round-stepper" style={{ flexDirection: 'row', alignItems: 'center', flexWrap: 'wrap', gap: 6 }}>
    {PHASES.map((p, i) => <React.Fragment key={p.label}>
      {i > 0 && <View style={{ width: 14, height: 1, backgroundColor: theme.lineStrong }} />}
      {i === at && alive && <StatusDot tone="running" />}
      <Text style={{ fontSize: 12, lineHeight: 18, fontWeight: i === at ? '500' : '400', color: i === at ? theme.text : theme.muted }}>{p.label}</Text>
    </React.Fragment>)}
  </View>;
}

function CardRow({ card, compact, selected, onPress }: { card: AutoCard; compact: boolean; selected: boolean; onPress: () => void }) {
  const theme = useTheme();
  const inflight = card.status === 'inflight' || card.status === 'submitted';
  return <RowPress selected={selected} onPress={onPress} testID={`card-${card.index}`}>
    <View style={{ flex: 1, minWidth: 0, flexDirection: 'row', alignItems: 'center', gap: 8, paddingLeft: 8 }}>
      <StatusDot tone={inflight ? 'running' : 'idle'} />
      <Text numberOfLines={1} style={{ flex: 1, fontSize: 13, lineHeight: 20, color: inflight ? theme.text : theme.muted }}>{card.title ?? `卡片 ${card.index + 1}`}</Text>
    </View>
    <Text style={{ width: compact ? RESULT_NARROW : RESULT_WIDE, fontSize: 12, lineHeight: 18, color: theme.muted }}>{inflight ? '进行中' : '排队'}</Text>
    <View style={{ width: 60 }} />
    {!compact && <View style={{ width: 60 }} />}
    {!compact && <View style={{ width: 112 }} />}
  </RowPress>;
}

function OpenRound({ round, alive, compact, selected, onSelect }: { round: AutoOpenRound; alive: boolean; compact: boolean; selected: string | null; onSelect: (id: string) => void }) {
  const theme = useTheme();
  return <View testID="auto-open-round" style={{ gap: 8 }}>
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12, flexWrap: 'wrap', minHeight: 24 }}>
      <Eyebrow>第 {round.round_id} 轮</Eyebrow>
      <Stepper phase={round.phase} alive={alive} />
      <View style={{ flex: 1 }} />
      <Text style={{ fontSize: 12, color: theme.muted }}>开始于 {ago(round.started_at)}</Text>
    </View>
    {round.cards.length > 0 ? <View style={{ gap: 2 }}>
      <AttemptHead compact={compact} />
      {round.cards.map(card => card.attempt
        ? <AttemptRow key={card.index} attempt={card.attempt} compact={compact} selected={selected === 'att:' + card.attempt.key} onPress={() => onSelect('att:' + card.attempt!.key)} />
        : <CardRow key={card.index} card={card} compact={compact} selected={selected === 'card:' + card.index} onPress={() => onSelect('card:' + card.index)} />)}
    </View> : <Copy small muted>{round.phase === 'analysing' ? '等分析 Agent 交卡片。' : '这一轮没有卡片。'}</Copy>}
  </View>;
}

function roundSummary(r: AutoRound, compact: boolean) {
  if (compact) return `过筛 ${r.screen_passed}/${r.cards_generated}`;
  const parts = [`${r.cards_generated} 张卡片`, `冒烟过 ${r.smoke_passed}`, `过筛 ${r.screen_passed}`];
  if (r.confirmation_verdict) parts.push(`里程碑：${segmentVerdictLabels[r.confirmation_verdict]}`);
  return parts.join(' · ');
}

function Rounds({ rounds, compact, selected, onSelect, openLatest }: { rounds: AutoRound[]; compact: boolean; selected: string | null; onSelect: (id: string) => void; openLatest: boolean }) {
  const theme = useTheme();
  if (!rounds.length) return null;
  const ordered = [...rounds].reverse();
  return <View testID="auto-rounds">
    <View style={{ height: 24, justifyContent: 'center' }}><Eyebrow>已结束的轮</Eyebrow></View>
    {ordered.map((r, i) => <Section key={r.round_id} title={`第 ${r.round_id} 轮`} defaultOpen={openLatest && i === 0} testID={`round-${r.round_id}`}
      meta={<Text numberOfLines={1} style={{ fontSize: 12, color: theme.muted }}>{roundSummary(r, compact)}</Text>}>
      {r.attempts.length > 0 ? <View style={{ gap: 2 }}>
        <AttemptHead compact={compact} />
        {r.attempts.map(a => <AttemptRow key={a.key} attempt={a} compact={compact} selected={selected === 'att:' + a.key} onPress={() => onSelect('att:' + a.key)} />)}
      </View> : <Copy small muted>这一轮没有进入流水线的卡片。</Copy>}
    </Section>)}
  </View>;
}

function Fact({ label, value, warn }: { label: string; value: string; warn?: boolean }) {
  const theme = useTheme();
  return <View style={{ flexDirection: 'row', justifyContent: 'space-between', gap: 8 }}>
    <Text style={{ flexShrink: 1, fontSize: 12, lineHeight: 18, color: theme.muted }}>{label}</Text>
    <Mono size={12} color={warn ? theme.warning : theme.text}>{value}</Mono>
  </View>;
}

function LedgerColumn({ title, children }: { title: string; children: React.ReactNode }) {
  const theme = useTheme();
  return <View style={{ flexGrow: 1, flexBasis: 160, minWidth: 150, gap: 4, padding: 12 }}>
    <Text style={{ fontSize: 13, lineHeight: 20, fontWeight: '500', paddingBottom: 2, color: theme.text }}>{title}</Text>
    {children}
  </View>;
}

function Ledger({ ledger }: { ledger: AutoLedger }) {
  const theme = useTheme();
  const { utility, selection, mechanism, integrity } = ledger;
  const predicate = mechanism.predicate_attachment;
  const share = selection.share_provisional_rolled_back;
  return <View testID="auto-ledger" style={{ gap: 8 }}>
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10, minHeight: 24 }}>
      <Eyebrow>证据账本</Eyebrow>
      <Text style={{ fontSize: 12, color: theme.muted }}>各栏并列，不相加</Text>
    </View>
    <View style={{ flexDirection: 'row', flexWrap: 'wrap', borderWidth: 1, borderColor: theme.line, borderRadius: 12 }}>
      <LedgerColumn title="收益">
        <Fact label="已确认里程碑" value={String(utility.n_certified_milestones ?? 0)} />
      </LedgerColumn>
      <LedgerColumn title="选择">
        <Fact label="验证池上评过" value={String(selection.n_evaluated_on_validation_pool ?? 0)} />
        <Fact label="被 Ladder 挡下" value={String(selection.ladder_blocked ?? 0)} />
        <Fact label="暂定后回滚" value={`${selection.provisional_rolled_back ?? 0}/${selection.provisional_adopted ?? 0}${typeof share === 'number' ? ` (${Math.round(share * 100)}%)` : ''}`} />
      </LedgerColumn>
      <LedgerColumn title="机制">
        <Fact label="已确认且归因支持" value={`${mechanism.n_certified_supported ?? 0}/${mechanism.n_certified_milestones ?? 0}`} />
        <Fact label="其中可能有偏" value={String(mechanism.n_certified_supported_possibly_biased ?? 0)} />
        {predicate && <Fact label={`谓词附带率（下限 ${Math.round(predicate.floor * 100)}%）`} value={predicate.rate === null ? '—' : `${Math.round(predicate.rate * 100)}%`} warn={!predicate.meets_floor && predicate.rate !== null} />}
      </LedgerColumn>
      <LedgerColumn title="完整性">
        <Fact label="记录的尝试" value={String(integrity.n_attempts_recorded ?? 0)} />
        <Fact label="停机时在途" value={String(integrity.n_halted_attempts ?? 0)} />
        <Fact label="重试过的中止" value={String(integrity.n_aborted_attempts_retried ?? 0)} />
        <Fact label="未尝试的卡片" value={String(integrity.cards_not_attempted ?? 0)} />
      </LedgerColumn>
    </View>
  </View>;
}

// The sealed result: the round x score curve, then the same numbers per node (the chart's table view).
function Sealed({ sealed, compact, selected, onSelect }: { sealed: SealedCurve; compact: boolean; selected: string | null; onSelect: (id: string | null) => void }) {
  const theme = useTheme();
  const curve = sealed.curve;
  const points = curve?.points;
  return <View testID="auto-sealed" style={{ gap: 8 }}>
    <View style={{ flexDirection: 'row', alignItems: 'center', columnGap: 10, rowGap: 2, minHeight: 24, flexWrap: 'wrap' }}>
      <Eyebrow>封存集</Eyebrow>
      <Text testID="sealed-axis-note" style={{ fontSize: 12, color: theme.muted, flexShrink: 1 }}>{sealed.metric ?? '—'} · n = {sealed.n ?? '—'} · k = {sealed.k ?? '—'} · 区间已做多重校正</Text>
    </View>
    {curve && <SealedChart curve={curve} selected={selected} onSelect={onSelect} />}
    {curve && <SealedLegend />}
    <Section title="逐节点数值" defaultOpen={!curve} testID="sealed-table">
      {(points ?? sealed.points.map((p, i) => ({ ...p, index: i } as SealedPoint & { index: number }))).map((p, i) => {
        const cp = points ? points[i] : null;
        const id = cp ? curveSelection(cp) : null;
        return <RowPress key={i} selected={!!id && selected === id} onPress={() => id && onSelect(id)} testID={`sealed-row-${i}`}>
          <View style={{ width: 20, alignItems: 'center' }}><Marker state={i === 0 ? 'seed' : p.state ?? 'provisional'} /></View>
          <Text numberOfLines={1} style={{ flex: 1, minWidth: 0, fontSize: 13, lineHeight: 20, color: p.state === 'rolled_back' ? theme.muted : theme.secondary }}>
            {i === 0 ? '种子' : cp?.title ?? short(p.candidate_id)}</Text>
          {cp && <Mono size={11} muted>第 {cp.round} 轮</Mono>}
          <View style={{ width: 44, alignItems: 'flex-end' }}><Mono size={12}>{num(p.score)}</Mono></View>
          <View style={{ width: compact ? 64 : NUMBERS_WIDTH, alignItems: 'flex-end' }}>
            <Mono size={11} muted>{i === 0 ? '参照' : compact ? signed(p.delta_vs_seed) : `${signed(p.delta_vs_seed)} [${signed(p.lower_adj)}, ${signed(p.upper_adj)}]`}</Mono>
          </View>
          {!compact && <View style={{ width: 52, alignItems: 'flex-end' }}>
            <Text style={{ fontSize: 11, lineHeight: 16, color: theme.muted }}>{i === 0 ? '' : p.holds_holm === null || p.holds_holm === undefined ? '—' : p.holds_holm ? '成立' : '不成立'}</Text>
          </View>}
        </RowPress>;
      })}
    </Section>
  </View>;
}

// Below this the interval plots and the extra columns do not fit. The column width
// decides, not the screen: side panels take most of a mid-size window.
const FULL_WIDTH = 680;

export function AutoRunPage({ view, compact: narrowScreen, selected, onSelect, onTriggerSealed }: {
  view: AutoRunView; compact: boolean; selected: string | null; onSelect: (id: string | null) => void; onTriggerSealed?: () => void;
}) {
  const theme = useTheme();
  const [width, setWidth] = useState(FULL_WIDTH);
  const compact = narrowScreen || width < FULL_WIDTH;
  return <View testID="auto-run" onLayout={event => setWidth(event.nativeEvent.layout.width)} style={{ gap: 24 }}>
    <StatusBand view={view} onTriggerSealed={onTriggerSealed} />
    {view.progress && <Progress progress={view.progress} />}
    {view.sealed && <Sealed sealed={view.sealed} compact={compact} selected={selected} onSelect={onSelect} />}
    {view.progress && <Lineage view={view} compact={compact} selected={selected} onSelect={onSelect} />}
    {view.open_round && <OpenRound round={view.open_round} alive={view.run.alive} compact={compact} selected={selected} onSelect={onSelect} />}
    <Rounds rounds={view.rounds} compact={compact} selected={selected} onSelect={onSelect} openLatest={!view.open_round} />
    {view.ledger && <Ledger ledger={view.ledger} />}
    <Text style={{ fontSize: 11, lineHeight: 16, color: theme.muted }}>这个迭代由机器驱动，手动操作已锁定。启动、续跑和封存集评测可通过界面触发。</Text>
  </View>;
}
