import React from 'react';
import { Pressable, ScrollView, Text, View } from 'react-native';
import { Command, GitBranch, Inbox, PanelLeftClose, Plus, Settings } from 'lucide-react-native';
import { hover, StatusDot, useTheme } from '../ui';
import type { DotTone } from '../ui';
import { CHROME_HEIGHT, IconButton } from '../shell';
import { ActionRequest, Iteration, ProposalRow, verbLabels } from './contracts';

export type IterationStatus = { tone: DotTone; label: string };

function Row({ label, detail, selected = false, onPress, leading, testID }: {
  label: string; detail?: string; selected?: boolean; onPress: () => void; leading?: React.ReactNode; testID?: string;
}) {
  const theme = useTheme();
  return <Pressable accessibilityRole="button" accessibilityLabel={label} accessibilityState={{ selected }} onPress={onPress} testID={testID}
    style={hover(hovered => ({ minHeight: 30, paddingVertical: 5, paddingHorizontal: 8, borderRadius: 8, flexDirection: 'row', alignItems: 'center', gap: 8,
      backgroundColor: selected ? theme.selected : hovered ? theme.surface : 'transparent' }))}>
    {leading}
    <View style={{ flex: 1, minWidth: 0 }}>
      <Text numberOfLines={1} style={{ fontSize: 13, lineHeight: 18, color: selected ? theme.text : theme.secondary }}>{label}</Text>
      {!!detail && <Text numberOfLines={1} style={{ fontSize: 11, lineHeight: 15, color: theme.muted }}>{detail}</Text>}
    </View>
  </Pressable>;
}

function Heading({ children, trailing }: { children: React.ReactNode; trailing?: React.ReactNode }) {
  const theme = useTheme();
  return <View style={{ height: 28, paddingHorizontal: 8, marginTop: 14, flexDirection: 'row', alignItems: 'center' }}>
    <Text style={{ flex: 1, fontSize: 11, fontWeight: '500', letterSpacing: 0.4, color: theme.muted }}>{children}</Text>{trailing}
  </View>;
}

export function IterationSidebar({ width, compact, project, iterations, statuses, iterationId, requests, proposals, label,
  onIteration, onRequest, onProposal, onNew, onPalette, onSettings, onClose }: {
  width: number; compact: boolean; project: string; iterations: Iteration[]; statuses: Record<string, IterationStatus>;
  iterationId: string | null; requests: ActionRequest[]; proposals: ProposalRow[]; label: (candidateId: string | null) => string;
  onIteration: (id: string) => void; onRequest: (request: ActionRequest) => void; onProposal: (proposal: ProposalRow) => void;
  onNew: () => void; onPalette: () => void; onSettings: () => void; onClose: () => void;
}) {
  const theme = useTheme();
  const pending = requests.filter(r => r.status === 'pending');
  const drafts = proposals.filter(p => p.status === 'draft');
  return <View testID="iteration-sidebar" style={{ width, height: '100%', backgroundColor: theme.sidebar, borderRightWidth: 1, borderColor: theme.line }}>
    <View style={{ height: compact ? 48 : CHROME_HEIGHT + 8, paddingHorizontal: 12, flexDirection: 'row', alignItems: 'center', gap: 8 }}>
      <Text style={{ flex: 1, color: theme.text, fontSize: 13, fontWeight: '600' }}>Silicon Notebook</Text>
      <IconButton label="命令面板（Ctrl+K）" icon={Command} onPress={onPalette} testID="open-palette" />
      <IconButton label="收起侧栏" icon={PanelLeftClose} onPress={onClose} testID={compact ? 'close-sidebar' : 'toggle-left-sidebar'} />
    </View>
    <ScrollView style={{ flex: 1, minHeight: 0 }} contentContainerStyle={{ paddingHorizontal: 8, paddingBottom: 16 }}>
      <Row label="新建迭代" onPress={onNew} leading={<Plus size={14} strokeWidth={1.75} color={theme.muted} />} testID="new-iteration" />
      <Heading>{project}</Heading>
      {iterations.map(it => <Row key={it.id} label={it.title} selected={it.id === iterationId} onPress={() => onIteration(it.id)}
        detail={[it.protocol?.mode, statuses[it.id]?.label].filter(Boolean).join(' · ')} testID={'iteration-nav-' + it.id}
        leading={<View style={{ width: 14, alignItems: 'center' }}>{statuses[it.id]?.tone
          ? <StatusDot tone={statuses[it.id].tone} /> : <GitBranch size={13} strokeWidth={1.75} color={theme.muted} />}</View>} />)}
      {!iterations.length && <Text style={{ color: theme.muted, fontSize: 12, padding: 8 }}>还没有迭代。新建一个：选起点 commit 和冻结题单。</Text>}
      <Heading trailing={pending.length + drafts.length ? <Text testID="pending-count" style={{ fontSize: 11, color: theme.warning, fontVariant: ['tabular-nums'] }}>{pending.length + drafts.length}</Text> : null}>待处理</Heading>
      {pending.map(r => <Row key={r.id} testID={'pending-request-' + r.id} onPress={() => onRequest(r)}
        label={`${verbLabels[r.verb] ?? r.verb} ${r.target_id ? label(r.target_id) : ''}`.trim()} detail={`请求 · ${r.requested_by}`}
        leading={<Inbox size={14} strokeWidth={1.75} color={theme.warning} />} />)}
      {drafts.map(p => <Row key={p.id} testID={'pending-proposal-' + p.id} onPress={() => onProposal(p)}
        label={p.title ?? '提议'} detail={`提议待批 · 挂在 ${label(p.parent_candidate_id)}`}
        leading={<View style={{ width: 14, height: 14, borderRadius: 7, borderWidth: 1, borderStyle: 'dashed', borderColor: theme.warning }} />} />)}
      {!pending.length && !drafts.length && <Text style={{ color: theme.muted, fontSize: 12, paddingHorizontal: 8, paddingVertical: 4 }}>没有等你处理的事</Text>}
    </ScrollView>
    <View style={{ height: 40, paddingHorizontal: 8, borderTopWidth: 1, borderColor: theme.line, flexDirection: 'row', alignItems: 'center', gap: 4 }}>
      <Text style={{ flex: 1, color: theme.muted, fontSize: 12, paddingLeft: 4 }}>本地工作台</Text>
      <IconButton label="工作区设置" icon={Settings} onPress={onSettings} testID="open-settings" />
    </View>
  </View>;
}
