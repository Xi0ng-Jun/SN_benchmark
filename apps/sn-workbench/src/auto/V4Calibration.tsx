import React, { useEffect, useState } from 'react';
import { ScrollView, View } from 'react-native';
import Svg, { Circle, Line, Polyline, Rect, Text as SvgText } from 'react-native-svg';
import { api } from '../contracts';
import { Copy, useTheme } from '../ui';
import { KeyValue, Section } from '../tree/parts';
import { duration, num } from './contracts';
import { CalibrationDetail, controlPath, Forecast, Interval } from './v41control';

export const intervalText = (v: Interval | null | undefined, digits = 2) => v ? `${num(v.low, digits)}–${num(v.high, digits)}` : '未知';
export function ForecastSummary({ forecast }: { forecast?: Forecast | null }) {
  if (!forecast) return <Copy small muted>预测尚未生成</Copy>;
  return <View testID="v41-forecast" style={{ gap: 4 }}>
    <Copy small>预计 {intervalText(forecast.candidates, 1)} 个候选 · {intervalText(forecast.rounds, 1)} 轮 · 每轮 {duration(forecast.per_round_s.low)}–{duration(forecast.per_round_s.high)}</Copy>
    <Copy small>可分辨提升 {intervalText(forecast.resolution.detectable, 4)} · 瓶颈 {forecast.bottleneck}</Copy>
    <Copy small>测量费用估计：{intervalText(forecast.spend.cost)}；token {intervalText(forecast.spend.tokens, 0)}</Copy>
    <Copy small muted>预测区间来自实测校准与历史／假设，不是保证。费用仅含后续测量，不含既有校准及 Agent；未记录用量时显示未知。</Copy>
  </View>;
}

// Small sequential sweep over achievable whole-paper sizes. Never fire a request per slider pixel.
export function Tradeoff({ iterationId, calibration, forecast }: { iterationId: string; calibration?: CalibrationDetail | null; forecast?: Forecast | null }) {
  const theme = useTheme();
  const [points, setPoints] = useState<Forecast[]>([]), [error, setError] = useState('');
  useEffect(() => {
    setPoints([]); setError('');
    const summary = calibration?.summary;
    if (!summary || !forecast || !summary.remaining_paper_sizes) return;
    const controller = new AbortController();
    const timer = setTimeout(async () => {
      try {
        let reachable = new Set([0]);
        for (const size of summary.remaining_paper_sizes!) for (const n of [...reachable]) reachable.add(n + size);
        const sizes = [...reachable].map(n => n + summary.n_probe).sort((a, b) => a - b);
        const chosen = [...new Set([sizes[0], sizes[Math.floor((sizes.length - 1) / 2)], sizes[sizes.length - 1], forecast.resolution.n_val])].sort((a, b) => a - b);
        const result: Forecast[] = [];
        for (const validation_size of chosen) {
          const value = await api<Forecast>(controlPath(iterationId) + '/simulate', { params: { ...forecast.params, validation_size } }, controller.signal);
          result.push(value);
        }
        if (!controller.signal.aborted) setPoints(result);
      } catch (e) { if (!controller.signal.aborted) setError((e as Error).message); }
    }, 600);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [iterationId, calibration, forecast?.sim_id]);
  if (!forecast) return null;
  const min = points[0]?.resolution.n_val ?? 0, max = points.at(-1)?.resolution.n_val ?? 1;
  const rate = (p: Forecast) => p.candidates.mid / Number(p.params.time_budget_hours);
  const maxRate = Math.max(1, ...points.map(rate)), maxResolution = Math.max(.001, ...points.map(p => p.resolution.detectable.mid));
  const x = (p: Forecast) => 50 + (p.resolution.n_val - min) / Math.max(1, max - min) * 480;
  return <Section title="验证题数的权衡" testID="v41-tradeoff">
    <Copy small muted>蓝：候选／小时（左轴）；橙：可分辨提升（右轴）。仅采样可实现的整篇论文划分；圆环为当前选择。</Copy>
    {error ? <Copy small style={{ color: theme.error }}>{error}</Copy> : !points.length ? <Copy small muted>正在计算…</Copy> : <Svg width="100%" height={180} viewBox="0 0 600 180" accessibilityLabel="验证集题数与速度、分辨率权衡">
      <Line x1={50} x2={530} y1={140} y2={140} stroke={theme.lineStrong} />
      {[0, .5, 1].map(t => <React.Fragment key={t}>
        <SvgText x={45} y={144 - t * 115} textAnchor="end" fontSize={10} fill={theme.primary}>{num(t * maxRate, 1)}</SvgText>
        <SvgText x={538} y={144 - t * 115} fontSize={10} fill={theme.warning}>{num(t * maxResolution, 3)}</SvgText>
      </React.Fragment>)}
      <Polyline points={points.map(p => `${x(p)},${140 - rate(p) / maxRate * 115}`).join(' ')} fill="none" stroke={theme.primary} strokeWidth={2} />
      <Polyline points={points.map(p => `${x(p)},${140 - p.resolution.detectable.mid / maxResolution * 115}`).join(' ')} fill="none" stroke={theme.warning} strokeWidth={2} />
      {points.map(p => <React.Fragment key={p.sim_id}>
        <Circle cx={x(p)} cy={140 - rate(p) / maxRate * 115} r={p.resolution.n_val === forecast.resolution.n_val ? 6 : 3} fill={theme.raised} stroke={theme.primary} strokeWidth={2} />
        <SvgText x={x(p)} y={162} textAnchor="middle" fontSize={10} fill={theme.muted}>{p.resolution.n_val}</SvgText>
      </React.Fragment>)}
      <SvgText x={290} y={178} textAnchor="middle" fontSize={10} fill={theme.muted}>验证集题数</SvgText>
    </Svg>}
  </Section>;
}

export function V4Calibration({ detail, forecast, iterationId }: { detail?: CalibrationDetail | null; forecast?: Forecast | null; iterationId?: string | null }) {
  const theme = useTheme();
  const [cell, setCell] = useState<string | null>(null);
  const [heatWidth, setHeatWidth] = useState(560);
  if (!detail?.complete || !detail.summary) return <Copy small muted>校准尚未完成；完成后显示匿名测量与噪声。</Copy>;
  const s = detail.summary, rows = detail.heatmap ?? [], means = detail.attempt_means ?? [], diffs = detail.aa_differences ?? [];
  const scores = rows.flatMap(r => r.scores).filter((v): v is number => v != null), low = Math.min(0, ...scores), high = Math.max(1, ...scores);
  const half = Math.max(.001, ...diffs.map(Math.abs), detail.aa_se ?? 0);
  const bins = Array.from({ length: 16 }, () => 0);
  diffs.forEach(d => { bins[Math.min(15, Math.floor((d + half) / (2 * half) * 16))]++; });
  const count = Math.max(1, ...bins);
  return <View testID="v4-calibration" style={{ gap: 14 }}>
    <Copy>校准：P {s.n_probe} 题 × {s.k} 遍，D {s.n_diagnosis} 题 × 1 遍</Copy>
    <Section title="匿名题 × 尝试" meta="按题均分排序">
      <Copy small muted>颜色越深分数越高（{num(low)}–{num(high)}）；点击格子查看分数。灰色表示该遍缺分；匿名行号不对应题号。</Copy>
      <View onLayout={e => setHeatWidth(e.nativeEvent.layout.width)}><ScrollView style={{ maxHeight: 280 }}>
        <Svg width={heatWidth} height={Math.max(60, rows.length * 18 + 24)} viewBox={`0 0 ${heatWidth} ${Math.max(60, rows.length * 18 + 24)}`}>
          {Array.from({ length: s.k }, (_, i) => <SvgText key={i} x={55 + i * (heatWidth - 65) / s.k} y={14} fill={theme.muted} fontSize={11}>第 {i + 1} 遍</SvgText>)}
          {rows.map((r, i) => <React.Fragment key={r.row}>
            <SvgText x={38} y={36 + i * 18} textAnchor="end" fill={theme.muted} fontSize={10}>{r.row}</SvgText>
            {r.scores.map((score, j) => <Rect key={j} x={55 + j * (heatWidth - 65) / s.k} y={24 + i * 18} width={Math.max(1, (heatWidth - 65) / s.k - 5)} height={15}
              fill={score == null ? theme.lineStrong : theme.primary} opacity={score == null ? 1 : .15 + .85 * (score - low) / (high - low)} onPress={() => setCell(`匿名行 ${r.row} · 第 ${j + 1} 遍：${num(score, 4)}`)} />)}
          </React.Fragment>)}
        </Svg>
      </ScrollView></View>
      <Copy small>{cell ?? '选择一个格子查看数值'}</Copy>
    </Section>
    <Section title="每遍均分">
      <Svg width="100%" height={100} viewBox="0 0 560 100">
        <Line x1={50} x2={530} y1={65} y2={65} stroke={theme.lineStrong} />
        {means.map((value, i) => value == null ? null : <React.Fragment key={i}>
          <Circle cx={70 + i * 95} cy={65 - (value - low) / (high - low) * 45} r={5} fill={theme.primary} />
          <SvgText x={70 + i * 95} y={83} textAnchor="middle" fill={theme.muted} fontSize={10}>{i + 1}: {num(value, 3)}</SvgText>
        </React.Fragment>)}
      </Svg>
    </Section>
    <Section title="A/A 差分布">
      <Copy small muted>每题首遍减其余遍均值；SE {num(detail.aa_se, 4)}。虚线为 ±SE，用于理解噪声，不是候选提升。</Copy>
      <Svg width="100%" height={145} viewBox="0 0 560 145">
        {bins.map((v, i) => <Rect key={i} x={50 + i * 30} y={110 - v / count * 85} width={27} height={v / count * 85} fill={theme.primary} />)}
        <Line x1={290} x2={290} y1={20} y2={110} stroke={theme.text} />
        {[-1, 1].map(sign => <Line key={sign} x1={290 + sign * (detail.aa_se ?? 0) / half * 240} x2={290 + sign * (detail.aa_se ?? 0) / half * 240} y1={20} y2={110} stroke={theme.warning} strokeDasharray="4 3" />)}
        {[-1, 0, 1].map(v => <SvgText key={v} x={290 + v * 240} y={132} textAnchor="middle" fontSize={10} fill={theme.muted}>{num(v * half, 3)}</SvgText>)}
      </Svg>
    </Section>
    <Section title="保护指标噪声">
      <Copy small muted>以下为 A/A 方差推算的 2·SE 噪声余量；实际越界还需超过产品容忍量，比例指标至少涉及两题。token 比值采用对数尺度。</Copy>
      {Object.entries(s.safeguard_var).map(([key, variance]) => <KeyValue key={key} label={key}><Copy small>方差 {num(variance, 5)} · 噪声余量 {num(2 * Math.sqrt(2 * variance / (forecast?.resolution.n_val ?? s.n_probe)), 4)}</Copy></KeyValue>)}
      {!Object.keys(s.safeguard_var).length && <Copy small muted>没有可用的保护指标 A/A 观测</Copy>}
    </Section>
    <Section title="耗时与测量依据">
      <KeyValue label="冷 / 热耗时"><Copy small>{duration(s.cold_wall_s)} / {duration(s.warm_wall_s)}（按缓存回执区分，缺回执时未知）</Copy></KeyValue>
      <KeyValue label="固定开销 / 每题"><Copy small>{duration(s.exec_overhead_s)} / {duration(s.per_task_s)}</Copy></KeyValue>
      <KeyValue label="p95 / 基础设施失败率"><Copy small>{duration(s.p95_exec_s)} / {num(s.infra_fail_rate * 100, 1)}%</Copy></KeyValue>
      {(detail.assumptions ?? []).map(a => <Copy small muted key={a}>{a}</Copy>)}
    </Section>
    {iterationId && <Tradeoff iterationId={iterationId} calibration={detail} forecast={forecast} />}
  </View>;
}
