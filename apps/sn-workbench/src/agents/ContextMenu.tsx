import React, { useEffect } from 'react';
import { ScrollView, Text, View } from 'react-native';
import { Button, Copy, Input, useTheme } from '../ui';
import type { ContextMode, ContextPreview } from './chatTypes';

export type ContextMenuProps = {
  mode: ContextMode;
  onModeChange: (mode: ContextMode) => void;
  options: readonly { id: string; label: string }[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  path: string;
  onPathChange: (value: string) => void;
  onPreview: () => void;
  preview: ContextPreview;
  addBlockedReason: string | null;
  onAdd: (snapshotId: string) => void;
  onClose: () => void;
};

/** Pick one task material, read exactly what the Agent will receive, then attach it. */
export function ContextMenu({ mode, onModeChange, options, selectedId, onSelect, path, onPathChange, onPreview, preview, addBlockedReason, onAdd, onClose }: ContextMenuProps) {
  const theme = useTheme();
  // A listed material is previewed as soon as it is chosen.
  useEffect(() => { if (mode === 'reference' && selectedId && options.some(o => o.id === selectedId)) onPreview(); }, [mode, selectedId]);
  const snapshot = preview.snapshot;
  const ready = preview.status === 'ready' && preview.current && snapshot !== null && snapshot.complete;
  const canAdd = ready && addBlockedReason === null;
  return <View testID="context-menu" style={{ borderWidth: 1, borderColor: theme.line, borderRadius: 12, backgroundColor: theme.bg, padding: 10, gap: 8 }}>
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 4, flexWrap: 'wrap' }}>
      <Button label="任务材料" selected={mode === 'reference'} onPress={() => onModeChange('reference')} />
      <Button label="仓库文件" selected={mode === 'path'} onPress={() => onModeChange('path')} />
      <View style={{ flex: 1 }} />
      <Button label="关闭" onPress={onClose} />
    </View>
    {mode === 'reference'
      ? <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 4 }}>
        {options.map(option => <Button key={option.id} label={option.label} selected={selectedId === option.id}
          onPress={() => onSelect(option.id)} accessibilityLabel={'选择材料 ' + option.label} />)}
        {!options.length && <Copy small muted>暂无可引用的任务材料</Copy>}
      </View>
      : <View style={{ gap: 6 }}>
        <Input label="评测仓库内相对路径" value={path} onChange={onPathChange} placeholder="例如 docs/failure-ledger.md" testID="context-path-input" />
        <View style={{ flexDirection: 'row' }}><Button label="读取" onPress={onPreview} disabled={!path.trim() || preview.status === 'loading'} testID="context-preview" /></View>
      </View>}
    {preview.status === 'loading' && <Copy small muted>正在读取…</Copy>}
    {preview.status === 'error' && <Copy small style={{ color: theme.error }}>{preview.detail ?? '读取失败'}</Copy>}
    {snapshot && <View style={{ gap: 4 }}>
      <Text style={{ fontSize: 12, fontWeight: '600', color: theme.text }}>{snapshot.title}</Text>
      <Text style={{ fontSize: 11, color: theme.muted }}>Agent 收到的就是下面这段文本（{snapshot.text.length} 字符），不会自动附带其他内容</Text>
      <ScrollView testID="context-preview-text" style={{ maxHeight: 180, borderWidth: 1, borderColor: theme.line, borderRadius: 8, padding: 8 }}>
        <Text selectable style={{ fontSize: 12, lineHeight: 18, color: theme.text, fontFamily: 'monospace' }}>{snapshot.text || '（空）'}</Text>
      </ScrollView>
    </View>}
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
      <Button label="附加到消息" primary disabled={!canAdd} testID="context-add"
        onPress={() => { if (canAdd && snapshot) { onAdd(snapshot.id); onClose(); } }} />
      {ready && addBlockedReason && <Copy small muted>{addBlockedReason}</Copy>}
    </View>
  </View>;
}
