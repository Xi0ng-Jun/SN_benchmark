import React, { useEffect, useRef, useState } from 'react';
import { Platform, Pressable, ScrollView, Text, View } from 'react-native';
import { ChevronDown, ChevronRight, Circle, Folder, ListTree, PanelLeftClose, Plus, Settings } from 'lucide-react-native';
import type { LucideIcon } from 'lucide-react-native';
import type { Workspace } from './contracts';
import type { TaskOverview } from './runs/contracts';
import { DotTone, Pill, StatusDot, useTheme } from './ui';

// Paseo: left-sidebar / explorer-sidebar-layout / HEADER_INNER_HEIGHT.
// These local shells host workbench content without Paseo's private stores.
export const CHROME_HEIGHT = 36;
const STORAGE_KEY = 'sn-workbench-shell-v1';
type Preferences = { left: number; right: number; leftOpen: boolean };
function readPreferences(): Preferences {
  const defaults = { left: 320, right: 320, leftOpen: true };
  if (Platform.OS !== 'web') return defaults;
  try {
    const value = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '{}');
    const width = (n: unknown, minimum: number) => typeof n === 'number' && Number.isFinite(n) ? Math.max(minimum, Math.min(600, n)) : 320;
    return { left: width(value?.left, 200), right: width(value?.right, 240), leftOpen: value?.leftOpen !== false };
  } catch { return defaults; }
}

export function useShellLayout(viewport: number) {
  const [preferences, setPreferences] = useState(readPreferences);
  const compact = viewport < 900;
  useEffect(() => {
    if (Platform.OS === 'web') try { localStorage.setItem(STORAGE_KEY, JSON.stringify(preferences)); } catch { /* Layout memory is optional. */ }
  }, [preferences]);
  const sizes = (rightOpen: boolean) => {
    const minimumLeft = preferences.leftOpen ? 200 : 0;
    const right = rightOpen ? Math.max(240, Math.min(preferences.right, viewport - minimumLeft - 400)) : 0;
    const left = preferences.leftOpen ? Math.max(200, Math.min(preferences.left, viewport - right - 400)) : 0;
    return { left, right };
  };
  return { compact, leftOpen: preferences.leftOpen, sizes,
    toggleLeft: () => setPreferences(p => ({ ...p, leftOpen: !p.leftOpen })),
    resize: (side: 'left' | 'right', value: number) => setPreferences(p => ({ ...p, [side]: Math.max(side === 'left' ? 200 : 240, Math.min(600, value)) })),
  };
}

export function IconButton({ label, icon: Icon, onPress, selected = false, testID }: {
  label: string; icon: LucideIcon; onPress: () => void; selected?: boolean; testID?: string;
}) {
  const theme = useTheme();
  const [hovered, setHovered] = useState(false);
  return <View onPointerEnter={() => setHovered(true)} onPointerLeave={() => setHovered(false)}>
    <Pressable accessibilityRole="button" accessibilityLabel={label} accessibilityState={{ selected }}
      testID={testID} onPress={onPress} {...(Platform.OS === 'web' ? { title: label } : {})}
      style={({ pressed }) => ({ width: 28, height: 28, alignItems: 'center', justifyContent: 'center', borderRadius: 6,
        backgroundColor: selected || hovered || pressed ? theme.selected : 'transparent' })}>
      <Icon size={16} strokeWidth={1.5} color={hovered || selected ? theme.text : theme.muted} />
    </Pressable>
  </View>;
}

function NavigationRow({ label, icon: Icon, selected = false, onPress, children, trailing, testID }: {
  label: string; icon?: LucideIcon; selected?: boolean; onPress: () => void; children?: React.ReactNode; trailing?: React.ReactNode; testID?: string;
}) {
  const theme = useTheme();
  const [hovered, setHovered] = useState(false);
  return <View style={{ flex: 1, minWidth: 0 }} onPointerEnter={() => setHovered(true)} onPointerLeave={() => setHovered(false)}>
    <Pressable accessibilityRole="button" accessibilityLabel={label} accessibilityState={{ selected }} onPress={onPress} testID={testID}
      {...(Platform.OS === 'web' ? { title: label } : {})}
      style={{ minHeight: 32, paddingVertical: 6, paddingHorizontal: 8, flexDirection: 'row', alignItems: 'center', gap: 8, borderRadius: 8,
        backgroundColor: selected ? theme.selected : hovered ? theme.surface : 'transparent' }}>
      {Icon && <Icon size={16} strokeWidth={1.5} color={selected || hovered ? theme.text : theme.muted} />}
      {children}
      <Text numberOfLines={1} style={{ flex: 1, minWidth: 0, fontSize: 14, lineHeight: 20, color: selected || hovered ? theme.text : theme.muted }}>{label}</Text>
      {trailing}
    </Pressable>
  </View>;
}

// Paseo sidebar-agent-state buckets: running blue, failed red, needs you amber (undecided
// records or finished runs with cases to read), idle grey.
const taskTone = (overview?: TaskOverview): DotTone => !overview ? null
  : overview.status === 'running' ? 'running' : overview.status === 'failed' ? 'danger' : overview.pending ? 'warning'
    : overview.status === 'attention' ? 'warning' : overview.status === 'unbound' ? null : 'idle';
const toneLabels: Record<string, string> = { running: '运行中', danger: '运行失败', warning: '有待读题或待决定', idle: '无待处理' };

export function WorkspaceSidebar({ width, compact, workspaces, taskId, overview = {}, onTask, onNewTask, onClose, onSettings, onRuns }: {
  width: number; compact: boolean; workspaces: Workspace[]; taskId: string | null; overview?: Record<string, TaskOverview>;
  onTask: (id: string) => void; onNewTask: () => void; onClose: () => void; onSettings: () => void; onRuns?: () => void;
}) {
  const theme = useTheme();
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  return <View testID="workspace-sidebar" style={{ width, height: '100%', backgroundColor: theme.sidebar, borderRightWidth: 1, borderColor: theme.line }}>
    <View style={{ height: compact ? 48 : CHROME_HEIGHT, paddingHorizontal: 12, flexDirection: 'row', alignItems: 'center', gap: 8 }}>
      <Text style={{ flex: 1, color: theme.text, fontSize: 14, fontWeight: '400' }}>Silicon Notebook</Text>
      <IconButton label="收起工作区侧栏" icon={PanelLeftClose} onPress={onClose} testID={compact ? 'close-sidebar' : 'toggle-left-sidebar'} />
    </View>
    <View style={{ padding: 8, gap: 2 }}>
      <NavigationRow label="新建任务" icon={Plus} onPress={onNewTask} testID="new-task" />
      {onRuns && <NavigationRow label="全局 run 目录" icon={ListTree} onPress={onRuns} testID="open-run-directory" />}
    </View>
    <ScrollView style={{ flex: 1, minHeight: 0 }} contentContainerStyle={{ paddingHorizontal: 8, paddingTop: 12 }}>
      <View style={{ height: 28, paddingHorizontal: 8, flexDirection: 'row', alignItems: 'center' }}>
        <Text style={{ flex: 1, fontSize: 12, color: theme.muted }}>Workspaces</Text>
        <IconButton label="建立任务" icon={Plus} onPress={onNewTask} />
      </View>
      {workspaces.map(w => <View key={w.id} style={{ marginBottom: 16 }}>
        <NavigationRow label={w.title} onPress={() => setCollapsed(p => ({ ...p, [w.id]: !p[w.id] }))}>
          {collapsed[w.id] ? <ChevronRight size={12} color={theme.muted} /> : <ChevronDown size={12} color={theme.muted} />}
          <Folder size={16} strokeWidth={1.5} color={theme.muted} />
        </NavigationRow>
        {!collapsed[w.id] && <View style={{ marginLeft: 20, borderLeftWidth: 1, borderColor: theme.line, paddingLeft: 4, gap: 2 }}>
          {w.tasks.map(t => <NavigationRow key={t.id} label={t.title} onPress={() => onTask(t.id)} selected={t.id === taskId} testID={'task-nav-' + t.id}
            trailing={overview[t.id]?.pending ? <Pill label={String(overview[t.id].pending)} tone="warning" testID={'task-pending-' + t.id} /> : null}>
            <View style={{ width: 8, alignItems: 'center' }} {...(Platform.OS === 'web' ? { title: toneLabels[taskTone(overview[t.id]) ?? ''] ?? '' } : {})}>{taskTone(overview[t.id]) ? <StatusDot tone={taskTone(overview[t.id])} />
              : <Circle size={6} color={theme.line} fill={theme.line} />}</View>
          </NavigationRow>)}
          {!w.tasks.length && <Text style={{ color: theme.muted, fontSize: 12, padding: 8 }}>尚无任务</Text>}
        </View>}
      </View>)}
    </ScrollView>
    <View style={{ height: 42, paddingHorizontal: 8, borderTopWidth: 1, borderColor: theme.line, flexDirection: 'row', alignItems: 'center', gap: 4 }}>
      <IconButton label="建立任务" icon={Plus} onPress={onNewTask} />
      <Text style={{ flex: 1, color: theme.muted, fontSize: 12 }}>本地工作台</Text>
      <IconButton label="工作区设置" icon={Settings} onPress={onSettings} testID="open-settings" />
    </View>
  </View>;
}

export function ResizeHandle({ side, width, onResize }: { side: 'left' | 'right'; width: number; onResize: (n: number) => void }) {
  const theme = useTheme();
  const drag = useRef<{ x: number; width: number } | null>(null);
  const [active, setActive] = useState(false);
  if (Platform.OS !== 'web') return null;
  return <div data-testid={'resize-' + side + '-sidebar'} role="separator" aria-label={side === 'left' ? '调整工作区侧栏宽度' : '调整材料侧栏宽度'}
    aria-orientation="vertical" aria-valuenow={Math.round(width)} aria-valuemin={side === 'left' ? 200 : 240} aria-valuemax={600} tabIndex={0}
    style={{ position: 'absolute', zIndex: 5, top: 0, bottom: 0, [side === 'left' ? 'right' : 'left']: 0, width: 6, cursor: 'col-resize', touchAction: 'none', background: active ? theme.line : 'transparent' }}
    onPointerEnter={() => setActive(true)} onPointerLeave={() => { if (!drag.current) setActive(false); }}
    onPointerDown={e => { if (e.button !== 0) return; e.preventDefault(); e.currentTarget.setPointerCapture(e.pointerId); drag.current = { x: e.clientX, width }; setActive(true); }}
    onPointerMove={e => { if (drag.current) onResize(drag.current.width + (side === 'left' ? 1 : -1) * (e.clientX - drag.current.x)); }}
    onPointerUp={e => { drag.current = null; e.currentTarget.releasePointerCapture(e.pointerId); setActive(false); }}
    onLostPointerCapture={() => { drag.current = null; setActive(false); }}
    onKeyDown={e => {
      if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') { e.preventDefault(); onResize(width + (e.key === 'ArrowRight' ? 1 : -1) * (side === 'left' ? 1 : -1) * 20); }
      if (e.key === 'Home') { e.preventDefault(); onResize(320); }
    }} />;
}
