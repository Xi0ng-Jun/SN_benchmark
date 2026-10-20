import React, { useEffect, useState } from 'react';
import { Pressable, ScrollView, Text, View } from 'react-native';
import { Button, Copy, fmt, hover, useTheme } from '../ui';
import { api } from '../contracts';
import type { CaseList } from '../runs/contracts';
import { caseStatusLabels } from '../runs/contracts';
import { Candidate, IterationTree, short } from './contracts';
import { Delta, Mono } from './parts';

// Rows are the iteration's frozen cases; columns the picked candidates and the root,
// each read from its reference run. Statuses other than success are shown as they
// are, never as zeros (design §7).
export function CompareMatrix({ tree, labels, picked, onClose, onReadCase }: {
  tree: IterationTree; labels: Record<string, string>; picked: string[]; onClose: () => void; onReadCase: (runId: string, caseId: string) => void;
}) {
  const theme = useTheme();
  const order = tree.candidates.map(c => c.id);
  const ids = [...new Set([tree.root_candidate_id, ...picked].filter((id): id is string => Boolean(id)))].sort((a, b) => order.indexOf(a) - order.indexOf(b));
  const columns = ids.map(id => tree.candidates.find(c => c.id === id)).filter((c): c is Candidate => Boolean(c?.primary?.run_id));
  const [lists, setLists] = useState<Record<string, CaseList | string>>({});
  const runKey = columns.map(c => c.primary?.run_id).join();
  useEffect(() => {
    const controller = new AbortController();
    void Promise.all(columns.map(async c => {
      const runId = c.primary!.run_id!;
      try { return [c.id, await api<CaseList>(`/run-views/runs/${encodeURIComponent(runId)}/cases`, undefined, controller.signal)] as const; }
      catch (e) { return [c.id, (e as Error).message] as const; }
    })).then(entries => { if (!controller.signal.aborted) setLists(Object.fromEntries(entries)); });
    return () => controller.abort();
  }, [runKey]);
  const rootId = tree.root_candidate_id;
  const rootList = rootId ? lists[rootId] : undefined;
  const metric = Object.values(lists).find((l): l is CaseList => typeof l !== 'string')?.metric ?? null;
  const caseIds = [...new Set(Object.values(lists).flatMap(l => typeof l === 'string' ? [] : l.cases.map(x => x.case_id)))];
  const cell = (c: Candidate, caseId: string) => {
    const list = lists[c.id];
    return typeof list === 'string' || !list ? undefined : list.cases.find(x => x.case_id === caseId);
  };
  return <View testID="compare-matrix" style={{ gap: 12 }}>
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
      <Text style={{ flex: 1, fontSize: 14, fontWeight: '600', color: theme.text }}>逐题比较 · {metric ?? '主指标'}</Text>
      <Button label="返回谱系" onPress={onClose} testID="matrix-close" />
    </View>
    <Copy small muted>每列取该候选的参考运行；括号内为相对根的差值。点格子读两端答案与证据。n 很小时差值不是成绩结论。</Copy>
    <ScrollView horizontal>
      <View style={{ borderWidth: 1, borderColor: theme.line, borderRadius: 12, overflow: 'hidden', minWidth: 160 + columns.length * 132 }}>
        <View style={{ flexDirection: 'row', backgroundColor: theme.surface, borderBottomWidth: 1, borderColor: theme.line }}>
          <Text style={{ width: 160, padding: 10, fontSize: 11, color: theme.muted }}>题</Text>
          {columns.map(c => <View key={c.id} style={{ width: 132, padding: 10, borderLeftWidth: 1, borderColor: theme.line }}>
            <Text style={{ fontSize: 13, fontWeight: '500', color: theme.text }}>{labels[c.id]}{c.id === rootId ? ' · 根' : ''}</Text>
            <Mono size={11} muted>{short(c.primary?.run_id, 10)}</Mono>
          </View>)}
        </View>
        {caseIds.map((caseId, index) => <View key={caseId} style={{ flexDirection: 'row', borderTopWidth: index ? 1 : 0, borderColor: theme.line }}>
          <View style={{ width: 160, padding: 10, justifyContent: 'center' }}><Mono size={12}>{caseId.split(':').pop()?.slice(0, 12)}</Mono></View>
          {columns.map(c => {
            const row = cell(c, caseId);
            const value = metric ? row?.official.metrics[metric] : undefined;
            const base = rootId && typeof rootList !== 'string' ? rootList?.cases.find(x => x.case_id === caseId)?.official.metrics[metric ?? ''] : undefined;
            const delta = c.id !== rootId && typeof value === 'number' && typeof base === 'number' ? value - base : null;
            return <Pressable key={c.id} onPress={() => row && onReadCase(c.primary!.run_id!, caseId)} testID={`matrix-cell-${c.id}-${caseId}`}
              style={hover(hovered => ({ width: 132, padding: 10, borderLeftWidth: 1, borderColor: theme.line, backgroundColor: hovered ? theme.surface : 'transparent' }))}>
              {!row ? <Text style={{ fontSize: 12, color: theme.muted }}>{typeof lists[c.id] === 'string' ? '读取失败' : '…'}</Text>
                : row.status !== 'success' ? <Text style={{ fontSize: 12, color: theme.warning }}>{caseStatusLabels[row.status] ?? row.status}</Text>
                  : <View style={{ flexDirection: 'row', gap: 6, alignItems: 'baseline' }}><Mono size={13}>{fmt(value)}</Mono>{delta !== null && <Delta value={delta} />}</View>}
            </Pressable>;
          })}
        </View>)}
        {!caseIds.length && <Copy small muted>{Object.keys(lists).length ? '没有可显示的题' : '读取中…'}</Copy>}
      </View>
    </ScrollView>
    {tree.candidates.filter(c => picked.includes(c.id) && !c.primary?.run_id).map(c => <Copy key={c.id} small muted>{labels[c.id]} 没有可用的参考运行，未列入。</Copy>)}
  </View>;
}
