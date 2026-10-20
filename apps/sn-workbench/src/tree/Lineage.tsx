import React from 'react';
import { Pressable, Text, View } from 'react-native';
import { Check } from 'lucide-react-native';
import { Button, fmt, hover, useTheme } from '../ui';
import { Candidate, decisionLabels, DeltaSummary, IterationTree, ProposalRow, rootSide, short, stateLabels } from './contracts';
import { Delta, Mono } from './parts';

// Tree order by default (parent before children, siblings by derivation time);
// ranking by Δ根 is a flat view and never reorders the tree (design §7, §11).
type Row = { kind: 'candidate'; candidate: Candidate; guides: boolean[]; last: boolean }
  | { kind: 'proposal'; proposal: ProposalRow; guides: boolean[]; last: boolean };

function treeRows(tree: IterationTree, showProposals: boolean): Row[] {
  const children: Record<string, Candidate[]> = {};
  for (const c of tree.candidates) (children[c.parent_candidate_id ?? ''] ??= []).push(c);
  const proposals: Record<string, ProposalRow[]> = {};
  if (showProposals) for (const p of tree.proposals)
    if (p.status !== 'implementing' && !tree.candidates.some(c => c.proposal_id === p.id)) (proposals[p.parent_candidate_id ?? ''] ??= []).push(p);
  const rows: Row[] = [];
  const visit = (c: Candidate, guides: boolean[], last: boolean) => {
    rows.push({ kind: 'candidate', candidate: c, guides, last });
    const kids: ({ c: Candidate } | { p: ProposalRow })[] = [...(children[c.id] ?? []).map(k => ({ c: k })), ...(proposals[c.id] ?? []).map(p => ({ p }))];
    kids.forEach((kid, i) => {
      const isLast = i === kids.length - 1;
      const next = guides.length || c.parent_candidate_id ? [...guides, !last] : [];
      if ('c' in kid) visit(kid.c, next, isLast);
      else rows.push({ kind: 'proposal', proposal: kid.p, guides: next, last: isLast });
    });
  };
  const known = new Set(tree.candidates.map(c => c.id));
  tree.candidates.filter(c => !c.parent_candidate_id || !known.has(c.parent_candidate_id)).forEach((c, i, all) => visit(c, [], i === all.length - 1));
  return rows;
}

function Rails({ guides, last, depth }: { guides: boolean[]; last: boolean; depth: number }) {
  const theme = useTheme();
  if (depth === 0) return null;
  const cells = guides;
  return <View style={{ flexDirection: 'row', alignSelf: 'stretch' }}>
    {cells.map((line, i) => <View key={i} style={{ width: 16, alignItems: 'center' }}>{line && <View style={{ width: 1, flex: 1, backgroundColor: theme.lineStrong }} />}</View>)}
    <View style={{ width: 16 }}>
      <View style={{ position: 'absolute', left: 7.5, top: 0, height: last ? '50%' : '100%', width: 1, backgroundColor: theme.lineStrong }} />
      <View style={{ position: 'absolute', left: 7.5, top: '50%', width: 8, height: 1, backgroundColor: theme.lineStrong }} />
    </View>
  </View>;
}

function stateTone(c: Candidate, theme: ReturnType<typeof useTheme>) {
  return c.state === 'running' ? theme.running : c.state === 'failed' ? theme.danger : c.state === 'evaluated' ? theme.text
    : c.state === 'implementing' || c.state === 'provisioning' ? theme.warning : theme.muted;
}

const COLS = { state: 104, metric: 64, delta: 68, flips: 76, pick: 28 };

function DeltaCell({ side, isRoot }: { side: DeltaSummary | undefined; isRoot: boolean }) {
  const theme = useTheme();
  if (isRoot) return <Text style={{ width: COLS.delta, textAlign: 'right', fontSize: 11, color: theme.muted }}>基线</Text>;
  return <View style={{ width: COLS.delta, alignItems: 'flex-end' }} {...({ title: side?.status === 'admitted' ? '' : side?.reason ?? '' } as object)}>
    {side?.status === 'admitted' ? <Delta value={side.mean_delta} /> : <Text style={{ fontSize: 11, color: theme.muted }}>{side?.status === 'not_admitted' ? '不可比' : '—'}</Text>}
  </View>;
}

export function Lineage({ tree, labels, titles, selectedId, onSelect, onSelectProposal, rank, onRank, picked, onPick, onCompare, compact }: {
  tree: IterationTree; labels: Record<string, string>; titles: Record<string, string>; selectedId: string | null;
  onSelect: (id: string) => void; onSelectProposal: (id: string) => void; rank: boolean; onRank: (rank: boolean) => void;
  picked: string[]; onPick: (id: string) => void; onCompare: () => void; compact: boolean;
}) {
  const theme = useTheme();
  const rootId = tree.root_candidate_id;
  const rankValue = (c: Candidate) => c.id === rootId ? 0 : rootSide(c)?.status === 'admitted' ? rootSide(c)?.mean_delta ?? null : null;
  const rows: Row[] = rank
    ? tree.candidates.slice().sort((a, b) => (rankValue(a) === null ? 1 : 0) - (rankValue(b) === null ? 1 : 0) || (rankValue(b) ?? 0) - (rankValue(a) ?? 0))
      .map(candidate => ({ kind: 'candidate' as const, candidate, guides: [], last: true }))
    : treeRows(tree, true);
  return <View testID="lineage" style={{ gap: 12 }}>
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
      <View accessibilityRole="tablist" style={{ flexDirection: 'row', gap: 2, padding: 2, borderRadius: 999, backgroundColor: theme.surface, borderWidth: 1, borderColor: theme.line }}>
        {[{ id: false, label: '谱系' }, { id: true, label: '按 Δ根 排行' }].map(item => <Pressable key={String(item.id)} accessibilityRole="tab"
          accessibilityState={{ selected: rank === item.id }} onPress={() => onRank(item.id)} testID={item.id ? 'lineage-rank' : 'lineage-tree'}
          style={{ paddingHorizontal: 12, height: 24, borderRadius: 999, justifyContent: 'center', backgroundColor: rank === item.id ? theme.selected : 'transparent' }}>
          <Text style={{ fontSize: 12, fontWeight: '500', color: rank === item.id ? theme.text : theme.muted }}>{item.label}</Text>
        </Pressable>)}
      </View>
      <View style={{ flex: 1 }} />
      <Button label={picked.length ? `比较所选（${picked.length}）` : '勾选候选以比较'} onPress={onCompare} disabled={!picked.length} testID="compare-picked" />
    </View>
    <View style={{ borderWidth: 1, borderColor: theme.line, borderRadius: 12, overflow: 'hidden' }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', height: 30, paddingHorizontal: 12, gap: 8, backgroundColor: theme.surface, borderBottomWidth: 1, borderColor: theme.line }}>
        <Text style={{ flex: 1, fontSize: 11, color: theme.muted }}>{rank ? '排行（平铺，不改谱系）' : '候选'}</Text>
        <Text style={{ width: COLS.state, fontSize: 11, color: theme.muted }}>状态</Text>
        <Text style={{ width: COLS.metric, textAlign: 'right', fontSize: 11, color: theme.muted }}>{tree.candidates.find(c => c.primary?.metric)?.primary?.metric ?? '主指标'}</Text>
        <Text style={{ width: COLS.delta, textAlign: 'right', fontSize: 11, color: theme.muted }}>Δ父</Text>
        {!compact && <Text style={{ width: COLS.delta, textAlign: 'right', fontSize: 11, color: theme.muted }}>Δ根</Text>}
        {!compact && <Text style={{ width: COLS.flips, textAlign: 'right', fontSize: 11, color: theme.muted }}>对父 ↑↓=</Text>}
        <View style={{ width: COLS.pick }} />
      </View>
      {rows.map((row, index) => {
        if (row.kind === 'proposal') {
          const p = row.proposal;
          return <Pressable key={'p' + p.id} onPress={() => onSelectProposal(p.id)} testID={'lineage-proposal-' + p.id}
            style={hover(hovered => ({ flexDirection: 'row', alignItems: 'center', minHeight: 34, paddingHorizontal: 12, gap: 8,
              backgroundColor: selectedId === p.id ? theme.selected : hovered ? theme.surface : 'transparent', borderTopWidth: index ? 1 : 0, borderColor: theme.line }))}>
            <View style={{ flex: 1, flexDirection: 'row', alignItems: 'center', minWidth: 0, alignSelf: 'stretch' }}>
              <Rails guides={row.guides} last={row.last} depth={row.guides.length + 1} />
              <View style={{ width: 10, height: 10, borderRadius: 5, borderWidth: 1, borderStyle: 'dashed', borderColor: theme.muted, marginHorizontal: 4 }} />
              <Text numberOfLines={1} style={{ flex: 1, fontSize: 13, color: theme.muted, fontStyle: 'italic' }}>提议 · {p.title}</Text>
            </View>
            <Text style={{ width: COLS.state, fontSize: 12, color: p.status === 'draft' ? theme.warning : theme.muted }}>
              {p.status === 'draft' ? '待批准' : p.status === 'approved' ? '已批准·未派生' : p.status === 'rejected' ? '已驳回' : p.status}</Text>
            <View style={{ width: COLS.metric + COLS.delta + (compact ? 0 : COLS.delta + COLS.flips) + COLS.pick + (compact ? 16 : 32) }} />
          </Pressable>;
        }
        const c = row.candidate;
        const isRoot = c.id === rootId;
        const parent = c.comparisons?.parent;
        const selected = selectedId === c.id;
        return <Pressable key={c.id} onPress={() => onSelect(c.id)} testID={'lineage-row-' + c.id} accessibilityState={{ selected }}
          style={hover(hovered => ({ flexDirection: 'row', alignItems: 'center', minHeight: 40, paddingHorizontal: 12, gap: 8,
            backgroundColor: selected ? theme.selected : hovered ? theme.surface : 'transparent', borderTopWidth: index ? 1 : 0, borderColor: theme.line,
            borderLeftWidth: 2, borderLeftColor: selected ? theme.primary : 'transparent' }))}>
          <View style={{ flex: 1, flexDirection: 'row', alignItems: 'center', minWidth: 0, alignSelf: 'stretch' }}>
            {rank ? <Mono muted size={11}>{String(index + 1).padStart(2, ' ')}  </Mono> : <Rails guides={row.guides} last={row.last} depth={c.depth ?? 0} />}
            <View style={{ width: 10, height: 10, borderRadius: 5, marginHorizontal: 4, backgroundColor: c.decision?.decision === 'adopted' ? theme.primary : stateTone(c, theme),
              opacity: c.decision?.decision === 'rejected' ? .35 : 1 }} />
            <View style={{ flex: 1, minWidth: 0, paddingVertical: 4 }}>
              <Text numberOfLines={1} style={{ fontSize: 13, fontWeight: '500', color: theme.text, textDecorationLine: c.decision?.decision === 'rejected' ? 'line-through' : 'none' }}>
                {labels[c.id] ?? short(c.id)}<Text style={{ fontWeight: '400', color: theme.muted }}>{titles[c.id] ? '  ' + titles[c.id] : ''}</Text></Text>
            </View>
          </View>
          <Text numberOfLines={1} style={{ width: COLS.state, fontSize: 12, color: stateTone(c, theme) }}>
            {stateLabels[c.state] ?? c.state}{c.decision ? ' · ' + decisionLabels[c.decision.decision] : ''}</Text>
          <View style={{ width: COLS.metric, alignItems: 'flex-end' }}><Mono size={12}>{fmt(c.primary?.value)}</Mono></View>
          <DeltaCell side={parent} isRoot={isRoot} />
          {!compact && <DeltaCell side={rootSide(c)} isRoot={isRoot} />}
          {!compact && <View style={{ width: COLS.flips, alignItems: 'flex-end' }}>
            {parent?.status === 'admitted' && parent.counts ? <Text style={{ fontFamily: 'ui-monospace, monospace', fontSize: 11, color: theme.muted }}>
              <Text style={{ color: theme.success }}>↑{parent.counts.better}</Text> <Text style={{ color: theme.danger }}>↓{parent.counts.worse}</Text> ={parent.counts.same}</Text>
              : <Text style={{ fontSize: 11, color: theme.muted }}>{isRoot ? '' : '—'}</Text>}
          </View>}
          <Pressable accessibilityRole="checkbox" accessibilityLabel={`比较 ${labels[c.id]}`} accessibilityState={{ checked: picked.includes(c.id) }}
            onPress={() => onPick(c.id)} disabled={!c.reference} testID={'lineage-pick-' + c.id}
            style={{ width: COLS.pick, alignItems: 'center', opacity: c.reference ? 1 : .3 }}>
            <View style={{ width: 16, height: 16, borderRadius: 4, borderWidth: 1, borderColor: picked.includes(c.id) ? theme.primary : theme.lineStrong,
              backgroundColor: picked.includes(c.id) ? theme.primary : 'transparent', alignItems: 'center', justifyContent: 'center' }}>
              {picked.includes(c.id) && <Check size={12} strokeWidth={2.5} color={theme.onPrimary} />}
            </View>
          </Pressable>
        </Pressable>;
      })}
    </View>
  </View>;
}
