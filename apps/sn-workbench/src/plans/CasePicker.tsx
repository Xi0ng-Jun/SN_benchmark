import React, { useMemo, useState } from 'react';
import { Pressable, Text, TextInput, View } from 'react-native';
import { ChevronDown, ChevronRight, Shuffle, Square, SquareCheck, SquareMinus } from 'lucide-react-native';
import { Button, Fold, Input, s, useTheme } from '../ui';
import type { PlanInput } from '../contracts';

// Selection widget over the canonical case/partition catalogue. It only edits
// the controller's case-ID text (canonical order, one per line); validation,
// partition derivation and saving stay in usePlanDrafts and the server.

function hashSeed(seed: string): number {
  let h = 2166136261;
  for (let i = 0; i < seed.length; i++) { h ^= seed.charCodeAt(i); h = Math.imul(h, 16777619); }
  return h >>> 0;
}
function mulberry32(a: number) {
  return () => { a |= 0; a = a + 0x6D2B79F5 | 0; let t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; };
}
/** Deterministic: same seed + same canonical order => same sample. */
export function seededSample<T>(items: readonly T[], n: number, seed: string): T[] {
  const random = mulberry32(hashSeed(seed)), copy = [...items];
  for (let i = copy.length - 1; i > 0; i--) { const j = Math.floor(random() * (i + 1)); [copy[i], copy[j]] = [copy[j], copy[i]]; }
  return copy.slice(0, Math.max(0, Math.min(n, copy.length)));
}

const PAGE = 40;

export function CasePicker({ descriptor, value, onChange, disabled }: { descriptor: PlanInput; value: string; onChange: (text: string) => void; disabled: boolean }) {
  const theme = useTheme();
  const selected = useMemo(() => new Set(value.split(/\r?\n/).map(v => v.trim()).filter(Boolean)), [value]);
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [limit, setLimit] = useState(PAGE);
  const [unit, setUnit] = useState<'paper' | 'case'>('paper');
  const [count, setCount] = useState('20');
  const [seed, setSeed] = useState('20261013');
  const known = new Set(descriptor.case_ids);
  const unknown = [...selected].filter(id => !known.has(id));
  const commit = (next: Set<string>) => onChange(descriptor.case_ids.filter(id => next.has(id)).concat(unknown.filter(id => next.has(id))).join('\n'));
  const toggle = (ids: string[], on: boolean) => { const next = new Set(selected); ids.forEach(id => on ? next.add(id) : next.delete(id)); commit(next); };
  const q = query.trim().toLowerCase();
  const groups = descriptor.partitions.filter(p => !q || p.partition_id.toLowerCase().includes(q) || p.case_ids.some(id => id.toLowerCase().includes(q)));
  const papers = descriptor.partitions.filter(p => p.case_ids.some(id => selected.has(id))).length;
  const n = Math.max(0, Number.parseInt(count, 10) || 0);
  const sample = () => {
    const ids = unit === 'paper' ? seededSample(descriptor.partitions, n, seed).flatMap(p => p.case_ids) : seededSample(descriptor.case_ids, n, seed);
    commit(new Set(ids));
  };
  const box = (state: 'all' | 'some' | 'none') => state === 'all' ? SquareCheck : state === 'some' ? SquareMinus : Square;
  return <View testID="case-picker" style={{ gap: 10, minWidth: 0 }}>
    <View testID="case-picker-summary" style={{ flexDirection: 'row', alignItems: 'center', gap: 10 }}>
      <Text style={{ fontSize: 13, color: theme.text, fontVariant: ['tabular-nums'] }}>已选 <Text style={{ fontWeight: '600' }}>{selected.size}</Text> 题 · {papers} 篇论文（分区）</Text>
      <Text style={{ fontSize: 12, color: theme.muted }}>共 {descriptor.case_ids.length} 题 / {descriptor.partitions.length} 分区</Text>
      <View style={{ flex: 1 }} />
      <Button label="清空" disabled={disabled || !selected.size} onPress={() => commit(new Set())} testID="picker-clear" />
    </View>
    {unknown.length > 0 && <Text style={{ fontSize: 12, color: theme.warning }}>{unknown.length} 个已选 ID 不在当前输入中，保存会被拒绝</Text>}
    <View style={{ padding: 10, borderRadius: 8, borderWidth: 1, borderColor: theme.line, gap: 8 }}>
      <View style={s.row}>
        <Shuffle size={14} color={theme.muted} />
        <Text style={{ fontSize: 12, color: theme.text }}>随机抽样</Text>
        <Button label="按论文" selected={unit === 'paper'} onPress={() => setUnit('paper')} disabled={disabled} />
        <Button label="按题" selected={unit === 'case'} onPress={() => setUnit('case')} disabled={disabled} />
        <TextInput accessibilityLabel="抽样数量" value={count} onChangeText={setCount} editable={!disabled} inputMode="numeric" testID="picker-sample-count"
          style={{ width: 56, height: 28, borderWidth: 1, borderColor: theme.line, borderRadius: 6, paddingHorizontal: 8, color: theme.text, fontSize: 12 }} />
        <Text style={{ fontSize: 12, color: theme.muted }}>seed</Text>
        <TextInput accessibilityLabel="抽样 seed" value={seed} onChangeText={setSeed} editable={!disabled} testID="picker-seed"
          style={{ width: 96, height: 28, borderWidth: 1, borderColor: theme.line, borderRadius: 6, paddingHorizontal: 8, color: theme.text, fontSize: 12 }} />
        <Button label="抽样替换当前选择" primary disabled={disabled || !n || !seed} onPress={sample} testID="picker-sample" />
      </View>
      <Text style={{ fontSize: 11, color: theme.muted }}>按论文抽样会选中每篇论文的全部题；同 seed、同输入得到同一选题。</Text>
    </View>
    <Input label="搜索分区或案例 ID" value={query} onChange={v => { setQuery(v); setLimit(PAGE); }} placeholder="搜索分区或案例 ID" testID="picker-search" />
    <View style={{ borderWidth: 1, borderColor: theme.line, borderRadius: 8, overflow: 'hidden' }}>
      {groups.slice(0, limit).map(part => {
        const chosen = part.case_ids.filter(id => selected.has(id)).length;
        const state = chosen === 0 ? 'none' : chosen === part.case_ids.length ? 'all' : 'some';
        const Box = box(state), Chevron = open[part.partition_id] || q ? ChevronDown : ChevronRight;
        const visible = q && !part.partition_id.toLowerCase().includes(q) ? part.case_ids.filter(id => id.toLowerCase().includes(q)) : part.case_ids;
        return <View key={part.partition_id} style={{ borderBottomWidth: 1, borderColor: theme.line }}>
          <View style={{ minHeight: 32, flexDirection: 'row', alignItems: 'center', gap: 6, paddingHorizontal: 8, backgroundColor: chosen ? theme.surface : theme.bg }}>
            <Pressable accessibilityRole="checkbox" accessibilityState={{ checked: state === 'all' ? true : state === 'some' ? 'mixed' : false, disabled }}
              accessibilityLabel={'选择分区 ' + part.partition_id} disabled={disabled} onPress={() => toggle(part.case_ids, state !== 'all')} testID={'picker-partition-' + part.partition_id}>
              <Box size={15} strokeWidth={1.6} color={chosen ? theme.text : theme.muted} />
            </Pressable>
            <Pressable style={{ flex: 1, flexDirection: 'row', alignItems: 'center', gap: 6 }} onPress={() => setOpen(o => ({ ...o, [part.partition_id]: !o[part.partition_id] }))}>
              <Chevron size={12} color={theme.muted} />
              <Text numberOfLines={1} style={{ flex: 1, fontSize: 12, fontFamily: 'monospace', color: theme.text }}>{part.partition_id}</Text>
              <Text style={{ fontSize: 11, color: theme.muted, fontVariant: ['tabular-nums'] }}>{chosen}/{part.case_ids.length} 题</Text>
            </Pressable>
          </View>
          {(open[part.partition_id] || q) && visible.map(id => {
            const on = selected.has(id), Icon = on ? SquareCheck : Square;
            return <Pressable key={id} accessibilityRole="checkbox" accessibilityState={{ checked: on, disabled }} accessibilityLabel={'选择案例 ' + id}
              disabled={disabled} onPress={() => toggle([id], !on)} testID={'picker-case-' + id}
              style={{ minHeight: 28, flexDirection: 'row', alignItems: 'center', gap: 6, paddingLeft: 34, paddingRight: 8 }}>
              <Icon size={14} strokeWidth={1.6} color={on ? theme.text : theme.muted} />
              <Text numberOfLines={1} style={{ flex: 1, fontSize: 12, fontFamily: 'monospace', color: on ? theme.text : theme.muted }}>{id}</Text>
            </Pressable>;
          })}
        </View>;
      })}
      {!groups.length && <Text style={{ padding: 10, fontSize: 12, color: theme.muted }}>没有匹配的分区或案例</Text>}
    </View>
    {groups.length > limit && <Button label={`再显示 ${Math.min(PAGE, groups.length - limit)} 个分区（共 ${groups.length}）`} onPress={() => setLimit(l => l + PAGE)} />}
    <Fold title={`已选 ID 清单（${selected.size}）`} value={[...selected].join('\n') || '（空）'} />
  </View>;
}
