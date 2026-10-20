import React from 'react';
import { Pressable, ScrollView, Text, View } from 'react-native';
import { Bot, Check, ChevronDown, Plus } from 'lucide-react-native';
import { Button, Copy, hover, Input, StatusDot, useTheme } from '../ui';
import type { DotTone } from '../ui';

export type AgentOption = { id: string; label: string; detail: string; selected: boolean };
export type ProfileOption = { id: string; label: string; detail: string };

export const agentStatusLabels: Record<string, string> = {
  idle: '空闲', running: '运行中', initializing: '启动中', error: '出错', closed: '已关闭', archived: '已归档',
};
export const agentTone = (status?: string): DotTone => status === 'running' || status === 'initializing' ? 'running'
  : status === 'error' ? 'danger' : status === 'idle' ? 'success' : 'idle';

/** Composer button showing the current Agent and model. */
export function AgentButton({ label, status, open, onPress }: { label: string; status?: string; open: boolean; onPress: () => void }) {
  const theme = useTheme();
  return <Pressable accessibilityRole="button" accessibilityLabel={'选择 Agent：' + label} accessibilityState={{ expanded: open }}
    onPress={onPress} testID="agent-picker-toggle"
    style={hover(hovered => ({ flexDirection: 'row', alignItems: 'center', gap: 6, borderRadius: 8, paddingHorizontal: 8, height: 28,
      maxWidth: 360, backgroundColor: open || hovered ? theme.selected : 'transparent' }))}>
    <Bot size={14} strokeWidth={1.6} color={theme.muted} />
    <Text numberOfLines={1} style={{ flexShrink: 1, fontSize: 12, color: theme.text }}>{label}</Text>
    {status && <StatusDot tone={agentTone(status)} />}
    {status && <Text style={{ fontSize: 12, color: theme.muted }}>{agentStatusLabels[status] ?? status}</Text>}
    <ChevronDown size={12} color={theme.muted} />
  </Pressable>;
}

function Row({ label, detail, selected, icon, onPress, disabled, testID }: {
  label: string; detail: string; selected?: boolean; icon?: React.ReactNode; onPress: () => void; disabled?: boolean; testID?: string;
}) {
  const theme = useTheme();
  return <Pressable accessibilityRole="button" accessibilityState={{ selected, disabled }} accessibilityLabel={label} disabled={disabled}
    onPress={onPress} testID={testID}
    style={hover(hovered => ({ flexDirection: 'row', alignItems: 'center', gap: 8, borderRadius: 8, paddingHorizontal: 10, paddingVertical: 7,
      opacity: disabled ? .45 : 1, backgroundColor: hovered ? theme.surface : 'transparent' }))}>
    <View style={{ width: 14, alignItems: 'center' }}>{icon}</View>
    <View style={{ flex: 1, minWidth: 0 }}>
      <Text numberOfLines={1} style={{ fontSize: 13, color: theme.text }}>{label}</Text>
      <Text numberOfLines={1} style={{ fontSize: 11, color: theme.muted }}>{detail}</Text>
    </View>
  </Pressable>;
}

export type AgentMenuProps = {
  agents: readonly AgentOption[];
  onSelect: (id: string) => void;
  profiles: readonly ProfileOption[];
  createEnabled: boolean;
  createReason: string | null;
  onCreate: (profileId: string) => void;
  creation: { text: string; tone: 'muted' | 'error'; action?: { label: string; onPress: () => void } } | null;
  link: { value: string; onChange: (value: string) => void; onSubmit: () => void; statusText: string | null };
  /** Where a new Agent works: the Task's SN worktree when bound, else the workspace. */
  cwdLabel: string;
  /** The space the drawer leaves for the menu; the Agent list scrolls inside it. */
  maxHeight?: number;
  onClose: () => void;
};

/** Agent list for this Task plus "new Agent" per server-allowed model profile. */
export function AgentMenu({ agents, onSelect, profiles, createEnabled, createReason, onCreate, creation, link, cwdLabel, maxHeight = 420, onClose }: AgentMenuProps) {
  const theme = useTheme();
  const [linkOpen, setLinkOpen] = React.useState(false);
  return <View testID="agent-menu" style={{ borderWidth: 1, borderColor: theme.line, borderRadius: 12, backgroundColor: theme.bg, padding: 6, gap: 2,
    maxHeight, shadowColor: '#000', shadowOpacity: .08, shadowRadius: 12 }}>
    <Text style={{ fontSize: 11, color: theme.muted, paddingHorizontal: 10, paddingTop: 4 }}>此任务的 Agent</Text>
    {/* The list scrolls so a long history never pushes the create rows out of the menu. */}
    <ScrollView style={{ flexShrink: 1 }} contentContainerStyle={{ gap: 2 }}>
      {agents.map(agent => <Row key={agent.id} label={agent.label} detail={agent.detail} selected={agent.selected}
        icon={agent.selected ? <Check size={13} color={theme.text} /> : null} testID={'agent-option-' + agent.id}
        onPress={() => { onSelect(agent.id); onClose(); }} />)}
    </ScrollView>
    {!agents.length && <Copy small muted style={{ paddingHorizontal: 10 }}>还没有 Agent，从下面选一个模型新建</Copy>}
    <View style={{ height: 1, backgroundColor: theme.line, marginVertical: 4 }} />
    <Text style={{ fontSize: 11, color: theme.muted, paddingHorizontal: 10 }}>{cwdLabel}</Text>
    {profiles.map(profile => <Row key={profile.id} label={profile.label} detail={profile.detail} disabled={!createEnabled}
      icon={<Plus size={13} color={theme.muted} />} testID={'agent-create-' + profile.id} onPress={() => onCreate(profile.id)} />)}
    {!profiles.length && <Copy small muted style={{ paddingHorizontal: 10 }}>服务器未配置可用的模型</Copy>}
    {createReason && <Copy small muted style={{ paddingHorizontal: 10 }}>{createReason}</Copy>}
    {creation && <View style={{ flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: 6, paddingHorizontal: 10 }}>
      <Text testID="agent-creation-status" style={{ fontSize: 12, color: creation.tone === 'error' ? theme.error : theme.muted }}>{creation.text}</Text>
      {creation.action && <Button label={creation.action.label} onPress={creation.action.onPress} />}
    </View>}
    <View style={{ height: 1, backgroundColor: theme.line, marginVertical: 4 }} />
    {linkOpen ? <View style={{ gap: 6, padding: 6 }}>
      <Input label="已有 Agent ID" value={link.value} onChange={link.onChange} placeholder="Paseo Agent ID" testID="session-agent-id-input" />
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
        <Button label="关联" onPress={link.onSubmit} disabled={!link.value.trim()} testID="session-link" />
        <Button label="取消" onPress={() => setLinkOpen(false)} />
        {link.statusText && <Copy small muted>{link.statusText}</Copy>}
      </View>
    </View> : <Button label="关联 Paseo 中已有的 Agent…" onPress={() => setLinkOpen(true)} testID="agent-link-open" />}
  </View>;
}
