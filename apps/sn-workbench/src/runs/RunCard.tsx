import React, { useState } from 'react';
import { Pressable, Text, View } from 'react-native';
import { ChevronDown, ChevronRight, FileDiff, IdCard, GitCompareArrows, ListChecks, Play } from 'lucide-react-native';
import { Button, fmt, hover, StatusDot, useTheme } from '../ui';
import { attentionRuleLabel, phaseLabels, RunView, runTone, short, stageLabels, TaskChanges, diffCounts } from './contracts';

function Progress({ done, total }: { done: number; total: number }) {
  const theme = useTheme();
  const ratio = total ? Math.min(1, done / total) : 0;
  return <View style={{ width: 72, height: 4, borderRadius: 2, backgroundColor: theme.selected, overflow: 'hidden' }}>
    <View style={{ width: `${ratio * 100}%`, height: 4, backgroundColor: theme.muted }} />
  </View>;
}

function Metric({ label, value, denominator, testID }: { label: string; value: string; denominator?: string; testID?: string }) {
  const theme = useTheme();
  return <View testID={testID} style={{ minWidth: 92, gap: 1 }}>
    <Text style={{ fontSize: 11, color: theme.muted }}>{label}</Text>
    <Text style={{ fontSize: 15, color: theme.text, fontVariant: ['tabular-nums'] }}>{value}
      {denominator && <Text style={{ fontSize: 11, color: theme.muted }}>{' / ' + denominator}</Text>}</Text>
  </View>;
}

const ratio = (r: { numerator: number | null; denominator: number | null; status: string } | null) =>
  !r ? '—' : r.status !== 'available' || r.denominator == null ? r.status === 'not_applicable' ? '不适用' : '不可用' : `${r.numerator}/${r.denominator}`;

/** Paseo tool-call card: one header line, details folded underneath. */
export function RunCard({ run, onCases, onFlips, onProvenance, defaultOpen = true }: {
  run: RunView; onCases: (needsRead: boolean) => void; onFlips: () => void; onProvenance: () => void; defaultOpen?: boolean;
}) {
  const theme = useTheme();
  const [open, setOpen] = useState(defaultOpen);
  const [hover, setHover] = useState(false);
  const official = run.official;
  const metrics = Object.entries(official.metrics ?? {});
  const stage = run.stage;
  const kinds = stage?.scheduled?.service_kind_counts;
  const Chevron = open ? ChevronDown : ChevronRight;
  return <View testID="run-card" style={{ borderWidth: 1, borderColor: theme.line, borderRadius: 10, overflow: 'hidden' }}>
    <Pressable accessibilityRole="button" accessibilityState={{ expanded: open }} accessibilityLabel={'run ' + run.title}
      onPress={() => setOpen(o => !o)} onHoverIn={() => setHover(true)} onHoverOut={() => setHover(false)}
      style={{ minHeight: 38, paddingHorizontal: 12, flexDirection: 'row', alignItems: 'center', gap: 8, backgroundColor: hover ? theme.surface : theme.bg }}>
      <Play size={14} strokeWidth={1.6} color={theme.muted} />
      <Text numberOfLines={1} style={{ fontSize: 13, color: theme.text, flexShrink: 1 }}>
        {(run.suite ?? '?').toUpperCase()} · {run.mode ?? '?'} <Text style={{ color: theme.muted }}>· {run.title}</Text></Text>
      <View style={{ flex: 1 }} />
      <StatusDot tone={runTone(run)} />
      <Text style={{ fontSize: 12, color: theme.muted }}>{phaseLabels[run.state.phase] ?? run.state.phase}</Text>
      <Text style={{ fontSize: 12, color: theme.muted, fontVariant: ['tabular-nums'] }}>{run.progress.outputs}/{run.progress.planned}</Text>
      <Progress done={run.progress.outputs} total={run.progress.planned} />
      <Chevron size={14} color={theme.muted} />
    </Pressable>
    {open && <View style={{ borderTopWidth: 1, borderColor: theme.line, padding: 12, gap: 12, backgroundColor: theme.surface }}>
      <View style={{ gap: 6 }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
          <Text style={{ fontSize: 11, color: theme.muted, width: 34 }}>官方</Text>
          {official.status === 'available' ? <Text style={{ fontSize: 11, color: theme.muted }}>
            {official.scope} · {official.comparison_category ?? '类别未声明'} · scores {short(official.scores_id)}</Text>
            : <Text style={{ fontSize: 11, color: theme.warning }}>{official.reason ?? official.status}</Text>}
        </View>
        {official.status === 'available' && <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 16, paddingLeft: 40 }}>
          {metrics.map(([name, value]) => <Metric key={name} label={name} value={fmt(value)} denominator={String(official.metric_denominators?.[name] ?? '?')} testID={'run-official-' + name} />)}
          {!!official.pending_metrics?.length && <Metric label="评分缺口" value={String(official.pending_metrics.length)} />}
        </View>}
      </View>
      <View style={{ gap: 6 }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
          <Text style={{ fontSize: 11, color: theme.muted, width: 34 }}>阶段</Text>
          <Text style={{ fontSize: 11, color: theme.muted }}>diagnostic · 从已保存输出离线计算</Text>
        </View>
        {stage ? <View testID="run-stage" style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 16, paddingLeft: 40 }}>
          {(['gold_evidence_recall', 'context_precision', 'citation_precision', 'refusal_rate'] as const).map(key =>
            <Metric key={key} label={stageLabels[key]} value={ratio(stage[key])} />)}
          <Metric label="scheduled 调用" value={String(stage.scheduled?.submission_count ?? '—')}
            denominator={kinds ? Object.entries(kinds).map(([k, n]) => `${k} ${n}`).join(' · ') : undefined} />
          <Metric label="问答耗时" value={stage.calls.ask_latency_seconds == null ? '—' : stage.calls.ask_latency_seconds.toFixed(1) + 's'} />
        </View> : <Text style={{ fontSize: 12, color: theme.muted, paddingLeft: 40 }}>阶段指标不可用{run.warnings.length ? '：' + run.warnings.join('；') : ''}</Text>}
      </View>
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: 4, marginLeft: -6 }}>
        <Button label={run.attention.count ? `待读 ${run.attention.count} 题` : '无待读题'} icon={ListChecks} primary={run.attention.count > 0}
          disabled={!run.attention.count} onPress={() => onCases(true)} testID="run-attention" />
        <Button label={`全部 ${run.progress.planned} 题`} onPress={() => onCases(false)} testID="run-all-cases" />
        <Button label="翻转" icon={GitCompareArrows} onPress={onFlips} testID="run-flips" />
        <Button label="身份" icon={IdCard} onPress={onProvenance} testID="run-provenance" />
        <Text style={{ fontSize: 11, color: theme.muted }} testID="run-attention-rule">待读：{attentionRuleLabel(run.attention.rule)}</Text>
        <View style={{ flex: 1 }} />
        <Text style={{ fontSize: 11, color: theme.muted }}>源码 {short(run.provenance.source.head_revision, 7)} · ref {short(run.provenance.source.sn_source_ref)}</Text>
      </View>
    </View>}
  </View>;
}

/** Slot for the SN change of this Task; full diff lives on the Changes page. */
export function DiffCard({ changes, onOpen }: { changes: TaskChanges | undefined; onOpen: () => void }) {
  const theme = useTheme();
  const read = changes?.read;
  const totals = (read?.files ?? []).reduce((sum, file) => { const c = diffCounts(file.diff); return { a: sum.a + c.additions, d: sum.d + c.deletions }; }, { a: 0, d: 0 });
  return <Pressable testID="diff-card" accessibilityRole="button" accessibilityLabel="打开更改页" onPress={onOpen} disabled={!read}
    style={hover(hovered => ({ minHeight: 38, paddingHorizontal: 12, borderWidth: 1, borderColor: theme.line, borderRadius: 10, borderStyle: read ? 'solid' : 'dashed',
      flexDirection: 'row', alignItems: 'center', gap: 8, backgroundColor: hovered ? theme.surface : theme.bg }))}>
    <FileDiff size={14} strokeWidth={1.6} color={theme.muted} />
    {!read ? <Text style={{ fontSize: 13, color: theme.muted }}>SN 改动 · 该任务未绑定 SN worktree</Text> : <>
      <Text numberOfLines={1} style={{ fontSize: 13, color: theme.text, flexShrink: 1 }}>SN 改动 <Text style={{ color: theme.muted }}>· {read.binding.branch ?? 'detached'} · base {short(read.binding.base_revision, 7)}</Text></Text>
      <View style={{ flex: 1 }} />
      {read.files.length ? <>
        <Text style={{ fontSize: 12, color: theme.muted }}>{read.file_count} 文件</Text>
        <Text style={{ fontSize: 12, color: theme.add }}>+{totals.a}</Text><Text style={{ fontSize: 12, color: theme.del }}>-{totals.d}</Text>
      </> : <Text style={{ fontSize: 12, color: theme.muted }}>相对 base 无改动</Text>}
      <ChevronRight size={14} color={theme.muted} />
    </>}
  </Pressable>;
}

/** One inline line for a human decision, with its action next to it. */
// tone 'decision' = waits for a human (amber bar); 'read' = cases to read (amber dot, same as the sidebar), not a decision.
export function DecisionRow({ text, actions, testID, tone = 'decision' }: { text: string; actions: { label: string; onPress: () => void; testID?: string; primary?: boolean }[];
  testID?: string; tone?: 'decision' | 'read' }) {
  const theme = useTheme();
  return <View testID={testID} style={{ minHeight: 36, flexDirection: 'row', alignItems: 'center', gap: 8, paddingLeft: 12, borderLeftWidth: 2, borderColor: tone === 'decision' ? theme.warning : theme.line }}>
    <StatusDot tone="warning" />
    <Text style={{ flex: 1, fontSize: 13, color: theme.text }}>{text}</Text>
    {actions.map(action => <Button key={action.label} label={action.label} primary={action.primary} onPress={action.onPress} testID={action.testID} />)}
  </View>;
}

export function TimelineLabel({ children }: { children: React.ReactNode }) {
  const theme = useTheme();
  return <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
    <Text style={{ fontSize: 11, color: theme.muted, letterSpacing: 0.3 }}>{children}</Text>
    <View style={{ flex: 1, height: 1, backgroundColor: theme.line }} />
  </View>;
}
