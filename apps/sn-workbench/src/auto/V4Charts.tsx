import React from 'react';
import { View } from 'react-native';
import Svg, { Circle, Line, Path, Rect, Text as SvgText } from 'react-native-svg';
import { Copy, useTheme } from '../ui';
import { Section } from '../tree/parts';
import { duration, num } from './contracts';
import { labelTexts, V4Record, V4Round, V4RunView, V4Safeguards } from './v4contracts';
import { ForecastSummary } from './V4Calibration';

const needsReview = (r: V4Record) => r.flags?.includes('needs_human_review');
export function V4Overview({ view, onSelect }: { view: V4RunView; onSelect: (id: string | null) => void }) {
  const theme = useTheme();
  const records = view.rounds.flatMap(rd => rd.records.map(r => ({ r, round: rd.round })));
  const measured = records.filter(({ r }) => r.delta_val != null && r.se_val != null && r.se_val > 0);
  const lo = Math.min(-1, ...measured.map(({ r }) => r.delta_val! / r.se_val!)), hi = Math.max(2, ...measured.map(({ r }) => r.delta_val! / r.se_val!));
  const x = (i: number) => 55 + i * 480 / Math.max(1, measured.length - 1), y = (v: number) => 155 - (v - lo) / (hi - lo) * 130;
  let cumulative = 0;
  const nodes = view.lineage.map(n => ({ ...n, estimate: cumulative += n.delta_val ?? 0 }));
  const nlo = Math.min(0, ...nodes.map(n => n.estimate)), nhi = Math.max(.001, ...nodes.map(n => n.estimate));
  const nx = (i: number) => 55 + i * 480 / Math.max(1, nodes.length - 1), ny = (v: number) => 120 - (v - nlo) / (nhi - nlo) * 95;
  const path = nodes.map((n, i) => i ? `H ${nx(i)} V ${ny(n.estimate)}` : `M ${nx(i)} ${ny(n.estimate)}`).join(' ');
  const failures = records.filter(({ r }) => r.label === 'implementation_failed');
  const safeguardRows = records.filter(({ r }) => r.safeguards);
  const breached = safeguardRows.filter(({ r }) => r.safeguards?.ok === false);
  const warns = records.filter(({ r }) => needsReview(r));
  const outcomes = Object.entries(records.reduce<Record<string, number>>((out, { r }) => { const k = r.label ?? 'in_flight'; out[k] = (out[k] ?? 0) + 1; return out; }, {}));
  return <View style={{ gap: 14 }}>
    <Copy>验证集累计提升估计：{num(nodes.at(-1)?.estimate, 4)} · 采纳 {Math.max(0, nodes.length - 1)} 次</Copy>
    <Copy small muted>未经独立终评，不能视为泛化提升。累计值为各次采纳增量之和。</Copy>
    {warns.length > 0 && <Copy testID="v41-human-review" style={{ color: theme.warning }}>需人工查看：{warns.length} 个候选有刷分等审查标记；标记取消宽松门槛，不自动否决。</Copy>}
    {breached.length > safeguardRows.length / 2 && <Copy small style={{ color: theme.warning }}>超过一半已观测候选的保护指标越界，请检查产品退化与方向选择。</Copy>}
    {failures.length >= 2 && records.slice(-2).every(({ r }) => r.label === 'implementation_failed') && <Copy small style={{ color: theme.warning }}>最近连续两次实现失败；查看失败类别和 Agent 回执。</Copy>}
    <Section title="候选进展" testID="v41-candidate-chart">
      <Copy small muted>每点是候选首测 Δ／SE（横轴按轮次和卡片编号，纵轴以各候选自身 SE 归一化）。绿：采纳；红：失败／越界；蓝：其他。点击查看证据。</Copy>
      {!measured.length ? <Copy small muted>尚无带有效 SE 的候选</Copy> : <Svg width="100%" height={190} viewBox="0 0 600 190">
        {[0, .5, 1].map(v => <React.Fragment key={v}><Line x1={50} x2={540} y1={y(v)} y2={y(v)} stroke={v ? theme.warning : theme.lineStrong} strokeDasharray={v ? '4 3' : undefined} /></React.Fragment>)}
        <SvgText x={45} y={25} textAnchor="end" fontSize={12} fill={theme.muted}>{num(hi, 1)}</SvgText><SvgText x={45} y={155} textAnchor="end" fontSize={12} fill={theme.muted}>{num(lo, 1)}</SvgText>
        {measured.map(({ r, round }, i) => <React.Fragment key={`${round}-${r.card_index}`}>
          <Circle cx={x(i)} cy={y(r.delta_val! / r.se_val!)} r={6} fill={r.label === 'adopted' ? theme.success : ['run_failed', 'safeguard_breached', 'remeasure_failed'].includes(r.label ?? '') ? theme.danger : theme.primary}
            onPress={() => onSelect(`rec:${round}:${r.card_index}`)} />
          <SvgText x={x(i)} y={178} textAnchor="middle" fontSize={10} fill={theme.muted}>{i + 1}</SvgText>
        </React.Fragment>)}
      </Svg>}
      <Copy small muted>橙色虚线：0.5·SE 与 1·SE；大幅提升会使两条门槛线靠近。0.5·SE 仅适用于机制确认且无人工审查标记的候选；普通门槛为 1·SE。SE 为零的候选仍保留在轮次明细中。</Copy>
    </Section>
    <Section title="当前版本的累计验证估计" testID="v41-staircase">
      <Svg width="100%" height={160} viewBox="0 0 600 160">
        <Path d={path} fill="none" stroke={theme.success} strokeWidth={2} />
        {nodes.map((n, i) => <React.Fragment key={n.version}>
          <Circle cx={nx(i)} cy={ny(n.estimate)} r={5} fill={theme.success} onPress={() => onSelect('node:' + n.version)} />
          <SvgText x={nx(i)} y={ny(n.estimate) - 10} textAnchor="middle" fontSize={10} fill={theme.text}>{num(n.estimate, 3)}</SvgText>
          <SvgText x={nx(i)} y={145} textAnchor="middle" fontSize={10} fill={theme.muted}>{i === 0 ? '种子' : `采纳 ${i}`}</SvgText>
        </React.Fragment>)}
      </Svg>
    </Section>
    <Section title="候选去向" meta={`${records.length} 个候选；终态互斥计数`} testID="v41-funnel">
      {outcomes.map(([label, count]) => <View key={label} style={{ gap: 3 }}>
        <Copy small>{labelTexts[label as keyof typeof labelTexts] ?? '在途'} · {count}</Copy>
        <View style={{ height: 6, width: `${count / Math.max(1, records.length) * 100}%`, backgroundColor: theme.primary, borderRadius: 3 }} />
      </View>)}
    </Section>
    <ForecastSummary forecast={view.forecast} />
  </View>;
}

export function V4RoundChart({ round, onSelect }: { round: V4Round; onSelect: (id: string | null) => void }) {
  const theme = useTheme();
  const records = [...round.records].filter(r => r.delta_val != null).sort((a, b) => (a.direction ?? '').localeCompare(b.direction ?? ''));
  const values = records.flatMap(r => [r.delta_val! - (r.se_val ?? 0), r.delta_val! + (r.se_val ?? 0), r.qualification?.threshold ?? 0, r.remeasure?.delta_val ?? 0]);
  const low = Math.min(0, ...values), high = Math.max(.001, ...values), x = (v: number) => 120 + (v - low) / (high - low) * 400;
  if (!records.length) return null;
  return <View testID={`v41-intervals-${round.round}`} style={{ gap: 5 }}>
    <Copy small muted>按方向分组 · 线段为首测 Δ ± 1 SE（不是 95% 区间）；橙色短线为该卡门槛；空心圆为复测合并值。点击候选点查看详情。</Copy>
    <Svg width="100%" height={records.length * 42 + 30} viewBox={`0 0 600 ${records.length * 42 + 30}`}>
      <Line x1={x(0)} x2={x(0)} y1={5} y2={records.length * 42} stroke={theme.lineStrong} />
      {records.map((r, i) => <React.Fragment key={r.card_index}>
        <SvgText x={5} y={20 + i * 42} fontSize={10} fill={theme.muted}>{(r.direction ?? '未标方向').slice(0, 12)} #{r.card_index + 1}</SvgText>
        <Line x1={x(r.delta_val! - (r.se_val ?? 0))} x2={x(r.delta_val! + (r.se_val ?? 0))} y1={18 + i * 42} y2={18 + i * 42} stroke={theme.primary} strokeWidth={3} />
        <Circle cx={x(r.delta_val!)} cy={18 + i * 42} r={5} fill={theme.primary} onPress={() => onSelect(`rec:${round.round}:${r.card_index}`)} />
        {r.qualification?.threshold != null && <Line x1={x(r.qualification.threshold)} x2={x(r.qualification.threshold)} y1={8 + i * 42} y2={28 + i * 42} stroke={theme.warning} strokeWidth={2} />}
        {r.remeasure?.delta_val != null && <Circle cx={x(r.remeasure.delta_val)} cy={18 + i * 42} r={7} fill="none" stroke={r.remeasure.passed ? theme.success : theme.danger} strokeWidth={2} />}
      </React.Fragment>)}
      {[low, high].map((v, i) => <SvgText key={i} x={x(v)} y={records.length * 42 + 20} textAnchor="middle" fontSize={10} fill={theme.muted}>{num(v, 4)}</SvgText>)}
    </Svg>
    {records.map(r => <Copy small muted key={r.card_index}>#{r.card_index + 1} SE {num(r.se_val, 4)} = max(配对估计 {num(r.se_own, 4)}, 噪声下限 {num(r.se_floor, 4)}){r.remeasure ? ` · 复测${r.remeasure.passed ? '通过' : r.remeasure.passed === false ? '未通过' : r.remeasure.status}` : ''}</Copy>)}
  </View>;
}

export function V4Time({ view }: { view: V4RunView }) {
  const theme = useTheme();
  const entries = view.rounds.flatMap(rd => rd.records.map(r => ({ r, round: rd.round, start: Date.parse(r.started_at ?? ''), end: Date.parse(r.finished_at ?? view.run.updated_at ?? '') }))).filter(e => Number.isFinite(e.start) && Number.isFinite(e.end));
  const start = Math.min(...entries.map(e => e.start)), end = Math.max(...entries.map(e => e.end)), span = Math.max(1, end - start);
  const stages = view.time_account?.candidate_sums_s ?? {}, total = Object.values(stages).reduce((a, b) => a + b, 0);
  return <View style={{ gap: 14 }}>
    <Section title="候选时间线" testID="v41-gantt">
      <Copy small muted>横轴为距首个候选开始的分钟数；重叠条表示并行。空白表示无可用候选时间戳。</Copy>
      {!entries.length ? <Copy small muted>尚无可用时间戳</Copy> : <Svg width="100%" height={entries.length * 28 + 30} viewBox={`0 0 600 ${entries.length * 28 + 30}`}>
        {entries.map((e, i) => <React.Fragment key={`${e.round}-${e.r.card_index}`}>
          <SvgText x={5} y={20 + i * 28} fill={theme.muted} fontSize={10}>轮 {e.round} / #{e.r.card_index + 1}</SvgText>
          <Rect x={110 + (e.start - start) / span * 420} y={8 + i * 28} width={Math.max(2, (e.end - e.start) / span * 420)} height={16} fill={theme.primary} opacity={e.r.finished_at ? 1 : .45} />
        </React.Fragment>)}
        <SvgText x={110} y={entries.length * 28 + 22} fill={theme.muted} fontSize={10}>0</SvgText><SvgText x={530} y={entries.length * 28 + 22} textAnchor="end" fill={theme.muted} fontSize={10}>{num(span / 60000, 1)} 分钟</SvgText>
      </Svg>}
    </Section>
    <Section title="阶段耗时占比" meta="候选累计时间；并行时不等于总墙钟">
      {Object.entries(stages).map(([stage, seconds]) => <View key={stage} style={{ gap: 3 }}><Copy small>{stage} · {duration(seconds)}</Copy><View style={{ height: 7, width: `${total ? seconds / total * 100 : 0}%`, backgroundColor: theme.primary }} /></View>)}
      {!total && <Copy small muted>尚未记录阶段耗时</Copy>}
    </Section>
    <Section title="预测与实测">
      {view.rounds.map((rd, i) => { const previous = i ? view.rounds[i - 1].forecast : null; return <Copy small key={rd.round}>第 {rd.round} 轮：实测 {duration(rd.wall_s)} · 轮前预测 {previous ? `${duration(previous.per_round_s.low)}–${duration(previous.per_round_s.high)}` : '未保存轮前预测'}{previous && rd.wall_s != null && rd.wall_s > previous.per_round_s.high ? ' · 超出预测上界' : ''}</Copy>; })}
      <ForecastSummary forecast={view.forecast} />
      <Copy small muted>实际总费用：未汇总（不能用模拟费用冒充实际支出）。</Copy>
    </Section>
  </View>;
}

export function SafeguardBars({ safeguards }: { safeguards: V4Safeguards }) {
  const theme = useTheme();
  return <View style={{ gap: 10 }}>{safeguards.rows.map(r => {
    const max = Math.max(.001, r.reference ?? 0, r.subject ?? 0);
    return <View key={r.key} style={{ gap: 3 }}><Copy small>{r.key} · {r.breached === null ? '未观测' : r.breached ? '越界' : '未越界'}</Copy>
      {([['参考', r.reference], ['候选', r.subject]] as const).map(([label, v]) => <View key={label}><Copy small muted>{label} {num(v, 4)}</Copy>{v != null && <View style={{ height: 5, width: `${Math.max(0, v) / max * 100}%`, backgroundColor: label === '参考' ? theme.muted : r.breached ? theme.danger : theme.primary }} />}</View>)}
      <Copy small muted>损害 {num(r.harm, 4)} · 产品容忍 {num(r.tolerance, 4)} · SE {num(r.se, 4)} · 含噪声门槛 {num(r.threshold, 4)} · 涉及 {r.changed_tasks ?? '未知'} 题</Copy>
    </View>;
  })}</View>;
}
