import React from 'react';
import { Modal, Pressable, ScrollView, useWindowDimensions, View } from 'react-native';
import { ArrowLeft, X } from 'lucide-react-native';
import type { CreationProfile, Source, WorkbenchCapabilities, Workspace } from '../contracts';
import { Block, Button, Copy, useTheme } from '../ui';

// UI01 read-only configuration surface. Every value shown comes from props;
// only the theme is a real change and it is reported through onThemeChange.
// This component performs no fetch, no storage write and no Task/Job/Agent
// call, and closing it only notifies the parent through onClose. `visible`,
// `theme` and all server data stay owned by the parent.

export type WorkspaceConfigurationProps = {
  visible: boolean;
  workspace: Workspace | undefined;
  actor: string;
  capabilities: WorkbenchCapabilities | undefined;
  profiles: readonly CreationProfile[];
  inputs: readonly Source[];
  theme: 'light' | 'dark';
  onThemeChange: (theme: 'light' | 'dark') => void;
  onClose: () => void;
};

type EntryKey = keyof WorkbenchCapabilities;

const workspaceEntries: readonly { key: EntryKey; label: string }[] = [
  { key: 'result_import', label: '导入结果' },
  { key: 'reviews', label: '人工判断' },
  { key: 'notes', label: '调查笔记' },
];
const agentEntries: readonly { key: EntryKey; label: string }[] = [
  { key: 'agent_create', label: '创建 Agent' },
  { key: 'agent_read', label: '读取 Agent 会话' },
  { key: 'agent_send', label: '发送 Agent 消息' },
];
const inputEntries: readonly { key: EntryKey; label: string }[] = [
  { key: 'result_comparison', label: '结果比较' },
  { key: 'plan_drafts', label: '评测草稿' },
  { key: 'execution', label: '执行评测' },
];

function orUnspecified(value: string | null | undefined): string {
  return value !== null && value !== undefined && value.trim() !== '' ? value : '未提供';
}

// true only claims a registered entry; it never claims health, connectivity or
// a finished run. false and undefined stay distinguishable.
function entryState(value: boolean | undefined): string {
  return value === true ? '已登记入口' : value === false ? '无入口' : '未提供';
}

const wrap = { flexShrink: 1, minWidth: 0 } as const;

function Fact({ label, value }: { label: string; value: string }) {
  return <View style={{ gap: 2, minWidth: 0 }}>
    <Copy small muted>{label}</Copy>
    <Copy small style={wrap}>{value}</Copy>
  </View>;
}

function EntryRow({ label, value }: { label: string; value: boolean | undefined }) {
  return <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 8, minWidth: 0 }}>
    <Copy small style={wrap}>{label}</Copy>
    <Copy small muted>{entryState(value)}</Copy>
  </View>;
}

function EntryList({ capabilities, entries }: {
  capabilities: WorkbenchCapabilities | undefined;
  entries: readonly { key: EntryKey; label: string }[];
}) {
  return <View style={{ gap: 6 }}>
    {entries.map(entry => <EntryRow key={entry.key} label={entry.label} value={capabilities?.[entry.key]} />)}
  </View>;
}

export function WorkspaceConfiguration({ visible, workspace, actor, capabilities, profiles, inputs, theme, onThemeChange, onClose }: WorkspaceConfigurationProps) {
  const colors = useTheme();
  const { width } = useWindowDimensions();
  const compact = width < 720;
  return <Modal visible={visible} transparent animationType="fade" onRequestClose={onClose}>
    <View style={{ flex: 1, backgroundColor: compact ? colors.bg : '#0007', alignItems: 'center',
      justifyContent: compact ? 'flex-start' : 'center', padding: compact ? 0 : 24 }}>
      {!compact && <Pressable accessibilityRole="button" accessibilityLabel="关闭设置" onPress={onClose}
        style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0 }} />}
      <View testID="workspace-settings" style={compact
        ? { flex: 1, width: '100%', backgroundColor: colors.bg }
        : { width: '100%', maxWidth: 720, maxHeight: '88%', backgroundColor: colors.bg,
            borderWidth: 1, borderColor: colors.line, borderRadius: 12, overflow: 'hidden' }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, flexShrink: 0,
          paddingHorizontal: 16, paddingVertical: 12, borderBottomWidth: 1, borderColor: colors.line }}>
          {compact
            ? <><Button label="返回" icon={ArrowLeft} onPress={onClose} testID="settings-close" />
                <Copy style={{ flex: 1, minWidth: 0, fontWeight: '600' }}>工作台设置</Copy></>
            : <><Copy style={{ flex: 1, minWidth: 0, fontWeight: '600' }}>工作台设置</Copy>
                <Button label="关闭" icon={X} onPress={onClose} testID="settings-close" /></>}
        </View>
        <ScrollView style={compact ? { flex: 1, minHeight: 0 } : { flexGrow: 0, flexShrink: 1, minHeight: 0 }}
          contentContainerStyle={{ padding: compact ? 18 : 24 }}>
          <Copy small muted>服务器配置只读；外观可在此调整</Copy>
          <Block title="外观">
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
              <Button label="浅色" selected={theme === 'light'} onPress={() => onThemeChange('light')} testID="settings-theme-light" />
              <Button label="深色" selected={theme === 'dark'} onPress={() => onThemeChange('dark')} testID="settings-theme-dark" />
            </View>
            <Copy small muted>主题选择来自当前设置并立即生效；其余内容只读</Copy>
          </Block>
          <Block title="Workspace">
            {workspace
              ? <View style={{ gap: 10 }}>
                  <Fact label="已登记 Workspace" value={orUnspecified(workspace.title)} />
                  <Fact label="location" value={orUnspecified(workspace.location)} />
                  <Copy small muted>location 为服务器登记位置，不表示本次评测的冻结源码</Copy>
                </View>
              : <Copy muted>未提供已登记 workspace</Copy>}
            <View style={{ gap: 6 }}>
              <EntryList capabilities={capabilities} entries={workspaceEntries} />
              <Copy small muted>入口只表示服务器已登记，不代表健康、在线或运行完成</Copy>
            </View>
          </Block>
          <Block title="Agent">
            <Fact label="当前操作者" value={orUnspecified(actor)} />
            <EntryList capabilities={capabilities} entries={agentEntries} />
            {profiles.length === 0
              ? <Copy muted>服务器未登记创建配置</Copy>
              : profiles.map(profile => <View key={profile.id} style={{ gap: 2, minWidth: 0 }}>
                  <Copy small style={wrap}>{profile.provider + ' · ' + profile.model}</Copy>
                  <Copy small muted style={wrap}>{`模式：${profile.modeId ?? '未提供'} · 思考：${profile.thinkingOptionId ?? '未提供'}`}</Copy>
                </View>)}
          </Block>
          <Block title="评测输入">
            <Copy small muted>真实 SN 执行输入尚未绑定</Copy>
            <EntryList capabilities={capabilities} entries={inputEntries} />
            {inputs.length === 0
              ? <Copy muted>尚未登记评测输入</Copy>
              : inputs.map(input => <View key={input.id} style={{ gap: 2, minWidth: 0 }}>
                  <Copy small style={wrap}>{input.title}</Copy>
                  <Copy small muted style={wrap}>{input.description}</Copy>
                </View>)}
          </Block>

        </ScrollView>
      </View>
    </View>
  </Modal>;
}
