import React, { useState } from 'react';
import { Pressable, Text, View } from 'react-native';
import Svg, { Circle, Line, Path, Text as SvgText } from 'react-native-svg';
import { MONO, useTheme } from '../ui';
import { short, signed } from '../tree/contracts';
import { CurvePoint, nodeStateLabels, num, SealedCurveView } from './contracts';

// Round x sealed score of the whole lineage (method-v3 §6), from optimizer/curve_view.py.
// The main path is the solid line; markers are the page's Marker: solid = certified,
// hollow = provisional, dashed = rolled back (joined to its parent by a dashed segment).
// Each whisker is the multiplicity-corrected interval of Δ vs the seed, drawn from the
// seed line, so "does it clear the seed" reads directly.

const HEIGHT = 230;
const PAD = { top: 22, right: 14, bottom: 40, left: 46 };
const R = 5;
const HIT = 28;
const TIP_WIDTH = 236;

export const curveSelection = (p: CurvePoint) => p.attempt ? 'att:' + p.attempt : 'node:' + p.version_hash;

function niceStep(span: number, target: number) {
  const raw = span / Math.max(1, target);
  const mag = 10 ** Math.floor(Math.log10(raw || 1));
  return [1, 2, 2.5, 5, 10].map(s => s * mag).find(s => s >= raw) ?? raw;
}

function layout(curve: SealedCurveView, width: number) {
  const plotW = Math.max(120, width - PAD.left - PAD.right);
  const plotH = HEIGHT - PAD.top - PAD.bottom;
  const values = curve.points.flatMap(p => [p.score, p.y_lower, p.y_upper]).filter((v): v is number => typeof v === 'number');
  const lo0 = values.length ? Math.min(...values) : 0, hi0 = values.length ? Math.max(...values) : 1;
  const pad = Math.max((hi0 - lo0) * 0.1, 0.005);
  const lo = lo0 - pad, hi = hi0 + pad;
  const step = niceStep(hi - lo, plotH < 160 ? 3 : 4);
  const yTicks: number[] = [];
  for (let t = Math.ceil(lo / step) * step; t <= hi + 1e-9; t += step) yTicks.push(Number(t.toFixed(6)));
  const rounds = curve.max_round;
  const band = plotW / (rounds + 1);
  const x0 = (round: number) => PAD.left + (round + 0.5) * band;
  const y = (v: number) => PAD.top + (hi - v) / (hi - lo) * plotH;
  const every = Math.max(1, Math.ceil((rounds + 1) / Math.max(1, Math.floor(plotW / 28))));
  const xTicks = Array.from({ length: rounds + 1 }, (_, r) => r).filter(r => r % every === 0);
  // Several nodes of one round sit side by side, in append order.
  const dx = (p: CurvePoint) => Math.min(18, band / (p.slots + 1));
  const x = (p: CurvePoint) => x0(p.round) + (p.slot - (p.slots - 1) / 2) * dx(p);
  // Hit targets of neighbours split the gap between them instead of covering each other.
  const hitWidth = (p: CurvePoint) => p.slots > 1 ? Math.min(HIT, dx(p)) : HIT;
  return { plotW, plotH, x, y, yTicks, xTicks, step, hitWidth };
}

function Tip({ point, left, top, metric }: { point: CurvePoint; left: number; top: number; metric: string }) {
  const theme = useTheme();
  const seed = point.index === 0;
  const verdict = point.holds_holm === null || point.holds_holm === undefined ? '—' : point.holds_holm ? '成立' : '不成立';
  return <View testID="sealed-tip" pointerEvents="none" style={{ position: 'absolute', left, top, width: TIP_WIDTH, gap: 2, paddingHorizontal: 10, paddingVertical: 8,
    borderRadius: 8, borderWidth: 1, borderColor: theme.lineStrong, backgroundColor: theme.raised, zIndex: 2 }}>
    <Text style={{ fontFamily: MONO, fontSize: 13, lineHeight: 18, fontWeight: '600', color: theme.text, fontVariant: ['tabular-nums'] }}>
      {num(point.score)} <Text style={{ fontWeight: '400', fontSize: 11, color: theme.muted }}>{metric}</Text>
    </Text>
    {!seed && <Text style={{ fontFamily: MONO, fontSize: 11, lineHeight: 16, color: theme.secondary, fontVariant: ['tabular-nums'] }}>
      Δ种子 {signed(point.delta_vs_seed)} [{signed(point.lower_adj)}, {signed(point.upper_adj)}]</Text>}
    {!seed && <Text style={{ fontFamily: MONO, fontSize: 11, lineHeight: 16, color: theme.secondary, fontVariant: ['tabular-nums'] }}>p_holm {num(point.p_holm)} · {verdict}</Text>}
    <Text numberOfLines={2} style={{ fontSize: 12, lineHeight: 17, color: theme.text, paddingTop: 2 }}>{seed ? '种子' : point.title ?? short(point.candidate_id ?? point.version_hash)}</Text>
    <Text style={{ fontSize: 11, lineHeight: 16, color: theme.muted }}>
      {seed ? '第 0 轮' : `第 ${point.round} 轮${point.round_inferred ? '（推定）' : ''}`} · {seed ? '参照' : point.state ? nodeStateLabels[point.state] : '—'}{point.on_path || seed ? '' : ' · 不在主路径'}
    </Text>
  </View>;
}

export function SealedChart({ curve, selected, onSelect }: { curve: SealedCurveView; selected: string | null; onSelect: (id: string | null) => void }) {
  const theme = useTheme();
  const [width, setWidth] = useState(0);
  const [hovered, setHovered] = useState<number | null>(null);
  const L = layout(curve, width || 600);
  const byIndex = new Map(curve.points.map(p => [p.index, p]));
  const measured = (p: CurvePoint | undefined): p is CurvePoint & { score: number } => !!p && typeof p.score === 'number';
  const tone = (p: CurvePoint) => p.index === 0 ? theme.muted : p.state === 'certified' ? theme.success : p.state === 'provisional' ? theme.primary : theme.muted;
  const path = curve.path.map(i => byIndex.get(i)).filter(measured);
  const branches = curve.points.filter(p => !p.on_path && p.parent_index !== null && measured(p) && measured(byIndex.get(p.parent_index!)));
  const metric = curve.metric ?? 'score';
  const seedY = typeof curve.seed_score === 'number' ? L.y(curve.seed_score) : null;
  const hot = hovered !== null ? byIndex.get(hovered) : undefined;
  return <View testID="sealed-chart" onLayout={e => setWidth(e.nativeEvent.layout.width)} style={{ height: HEIGHT, alignSelf: 'stretch' }}
    {...({ 'aria-label': `封存集 ${metric} 随轮次的变化，${curve.points.length} 个节点` } as object)}>
    {width > 0 && <Svg width={width} height={HEIGHT}>
      {L.yTicks.map(t => <React.Fragment key={'y' + t}>
        <Line x1={PAD.left} x2={PAD.left + L.plotW} y1={L.y(t)} y2={L.y(t)} stroke={theme.line} strokeWidth={1} />
        <SvgText x={PAD.left - 8} y={L.y(t) + 3.5} fontSize={10} fontFamily={MONO} fill={theme.muted} textAnchor="end">{t.toFixed(L.step < 0.01 ? 3 : 2)}</SvgText>
      </React.Fragment>)}
      <Line x1={PAD.left} x2={PAD.left + L.plotW} y1={PAD.top + L.plotH} y2={PAD.top + L.plotH} stroke={theme.lineStrong} strokeWidth={1} />
      {L.xTicks.map(r => <SvgText key={'x' + r} x={PAD.left + (r + 0.5) * L.plotW / (curve.max_round + 1)} y={PAD.top + L.plotH + 15} fontSize={10}
        fontFamily={MONO} fill={theme.muted} textAnchor="middle">{r}</SvgText>)}
      <SvgText x={PAD.left + L.plotW / 2} y={HEIGHT - 6} fontSize={11} fill={theme.muted} textAnchor="middle">轮（0 = 种子）</SvgText>
      <SvgText x={PAD.left - 8} y={PAD.top - 10} fontSize={11} fill={theme.muted} textAnchor="start">{`封存集 ${metric}`}</SvgText>
      {seedY !== null && <>
        <Line x1={PAD.left} x2={PAD.left + L.plotW} y1={seedY} y2={seedY} stroke={theme.lineStrong} strokeWidth={1} />
      </>}
      {curve.points.filter(p => p.index > 0 && measured(p) && p.y_lower !== null && p.y_upper !== null).map(p => {
        const cx = L.x(p), top = L.y(p.y_upper!), bottom = L.y(p.y_lower!);
        return <Path key={'w' + p.index} d={`M${cx} ${top}V${bottom}M${cx - 3} ${top}H${cx + 3}M${cx - 3} ${bottom}H${cx + 3}`}
          stroke={tone(p)} strokeOpacity={0.5} strokeWidth={1.5} fill="none" />;
      })}
      {path.length > 1 && <Path testID="sealed-path" d={path.map((p, i) => `${i ? 'L' : 'M'}${L.x(p)} ${L.y(p.score)}`).join('')}
        stroke={theme.secondary} strokeWidth={2} fill="none" strokeLinejoin="round" strokeLinecap="round" />}
      {branches.map(p => {
        const parent = byIndex.get(p.parent_index!)!;
        return <Line key={'b' + p.index} x1={L.x(parent)} y1={L.y(parent.score!)} x2={L.x(p)} y2={L.y(p.score!)} stroke={theme.muted} strokeWidth={1.5}
          strokeDasharray={p.state === 'rolled_back' ? '4 3' : undefined} />;
      })}
      {curve.points.filter(measured).map(p => {
        const cx = L.x(p), cy = L.y(p.score), color = tone(p);
        const isSelected = selected === curveSelection(p);
        const filled = p.index === 0 || p.state === 'certified';
        return <React.Fragment key={'p' + p.index}>
          {(isSelected || hovered === p.index) && <Circle cx={cx} cy={cy} r={R + 4} fill="none" stroke={isSelected ? theme.primary : theme.lineStrong} strokeWidth={1.5} />}
          <Circle cx={cx} cy={cy} r={R + 2} fill={theme.bg} />
          <Circle testID={`sealed-marker-${p.index}`} cx={cx} cy={cy} r={R} fill={filled ? color : theme.bg} stroke={color} strokeWidth={1.5}
            strokeDasharray={p.state === 'rolled_back' && p.index > 0 ? '2.5 2' : undefined} />
        </React.Fragment>;
      })}
    </Svg>}
    {width > 0 && seedY !== null && <Text pointerEvents="none" style={{ position: 'absolute', left: PAD.left + 4, top: seedY + 2, fontSize: 10, lineHeight: 13, color: theme.muted }}>种子</Text>}
    {width > 0 && curve.points.filter(measured).map(p => <Pressable key={'h' + p.index} testID={`sealed-point-${p.index}`} accessibilityRole="button"
      accessibilityLabel={`${p.index === 0 ? '种子' : p.title ?? short(p.candidate_id)}：${num(p.score)}`} accessibilityState={{ selected: selected === curveSelection(p) }}
      onHoverIn={() => setHovered(p.index)} onHoverOut={() => setHovered(h => h === p.index ? null : h)}
      onFocus={() => setHovered(p.index)} onBlur={() => setHovered(h => h === p.index ? null : h)}
      onPress={() => onSelect(curveSelection(p))}
      style={{ position: 'absolute', left: L.x(p) - L.hitWidth(p) / 2, top: L.y(p.score) - HIT / 2, width: L.hitWidth(p), height: HIT, borderRadius: HIT / 2 }} />)}
    {width > 0 && measured(hot) && <Tip point={hot} metric={metric}
      left={Math.max(0, Math.min(width - TIP_WIDTH, L.x(hot) + 12 + TIP_WIDTH > width ? L.x(hot) - 12 - TIP_WIDTH : L.x(hot) + 12))}
      top={Math.max(0, Math.min(HEIGHT - 110, L.y(hot.score) - 30))} />}
  </View>;
}

export function SealedLegend() {
  const theme = useTheme();
  const item = (key: string, glyph: React.ReactNode, label: string) => <View key={key} style={{ flexDirection: 'row', alignItems: 'center', gap: 5 }}>
    {glyph}<Text style={{ fontSize: 11, lineHeight: 16, color: theme.muted }}>{label}</Text>
  </View>;
  const dot = (fill: string, stroke: string, dash?: string) => <Svg width={12} height={12}><Circle cx={6} cy={6} r={4.5} fill={fill} stroke={stroke} strokeWidth={1.5} strokeDasharray={dash} /></Svg>;
  return <View testID="sealed-legend" style={{ flexDirection: 'row', flexWrap: 'wrap', columnGap: 14, rowGap: 4, paddingLeft: 4 }}>
    {item('c', dot(theme.success, theme.success), '已确认')}
    {item('p', dot(theme.bg, theme.primary), '暂定')}
    {item('r', dot(theme.bg, theme.muted, '2.5 2'), '已回滚')}
    {item('w', <Svg width={12} height={14}><Path d="M6 2V12M3 2H9M3 12H9" stroke={theme.muted} strokeWidth={1.5} fill="none" /></Svg>, '多重校正区间（相对种子）')}
    {item('s', <Svg width={14} height={12}><Line x1={0} x2={14} y1={6} y2={6} stroke={theme.lineStrong} strokeWidth={1.5} /></Svg>, '种子分数')}
  </View>;
}
