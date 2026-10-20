import React, { useEffect, useState } from 'react';
import { Modal, Pressable, ScrollView, Text, TextInput, View } from 'react-native';
import { useTheme } from '../ui';

export type PaletteItem = { id: string; group: string; label: string; hint?: string; run: () => void };

// Ctrl+K: navigation and structured actions (design §7). Write actions only open
// the confirm dialog; the palette itself never executes anything.
export function CommandPalette({ open, items, onClose }: { open: boolean; items: PaletteItem[]; onClose: () => void }) {
  const theme = useTheme();
  const [query, setQuery] = useState('');
  const [index, setIndex] = useState(0);
  useEffect(() => { if (open) { setQuery(''); setIndex(0); } }, [open]);
  const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const shown = items.filter(item => words.every(w => `${item.group} ${item.label} ${item.hint ?? ''}`.toLowerCase().includes(w)));
  const choose = (item: PaletteItem | undefined) => { if (!item) return; onClose(); item.run(); };
  if (!open) return null;
  let lastGroup = '';
  return <Modal visible transparent animationType="none" onRequestClose={onClose}>
    <Pressable accessibilityLabel="关闭命令面板" onPress={onClose} style={{ flex: 1, backgroundColor: '#0008', alignItems: 'center', paddingTop: '12%' }}>
      <Pressable testID="command-palette" onPress={() => undefined} style={{ width: '92%', maxWidth: 620, backgroundColor: theme.raised, borderRadius: 12,
        borderWidth: 1, borderColor: theme.lineStrong, overflow: 'hidden' }}>
        <TextInput testID="palette-input" autoFocus value={query} placeholder="跳到迭代或候选，或执行动作…" placeholderTextColor={theme.muted}
          onChangeText={text => { setQuery(text); setIndex(0); }}
          onKeyPress={event => {
            const key = (event.nativeEvent as { key: string }).key;
            if (key === 'ArrowDown') { event.preventDefault?.(); setIndex(i => Math.min(i + 1, shown.length - 1)); }
            if (key === 'ArrowUp') { event.preventDefault?.(); setIndex(i => Math.max(i - 1, 0)); }
            if (key === 'Enter') { event.preventDefault?.(); choose(shown[index]); }
            if (key === 'Escape') onClose();
          }}
          style={{ height: 48, paddingHorizontal: 16, fontSize: 15, color: theme.text, borderBottomWidth: 1, borderColor: theme.line, outlineWidth: 0 } as object} />
        <ScrollView style={{ maxHeight: 420 }} contentContainerStyle={{ padding: 6 }}>
          {shown.map((item, i) => {
            const heading = item.group !== lastGroup ? item.group : null;
            lastGroup = item.group;
            return <View key={item.id}>
              {heading && <Text style={{ fontSize: 11, fontWeight: '500', letterSpacing: 0.4, color: theme.muted, paddingHorizontal: 10, paddingTop: 8, paddingBottom: 4 }}>{heading}</Text>}
              <Pressable testID={'palette-item-' + item.id} onPress={() => choose(item)} onHoverIn={() => setIndex(i)}
                style={{ flexDirection: 'row', alignItems: 'center', gap: 8, height: 34, paddingHorizontal: 10, borderRadius: 8,
                  backgroundColor: i === index ? theme.selected : 'transparent' }}>
                <Text numberOfLines={1} style={{ flex: 1, fontSize: 13, color: theme.text }}>{item.label}</Text>
                {!!item.hint && <Text numberOfLines={1} style={{ fontSize: 11, color: theme.muted }}>{item.hint}</Text>}
              </Pressable>
            </View>;
          })}
          {!shown.length && <Text style={{ padding: 12, fontSize: 13, color: theme.muted }}>没有匹配的项</Text>}
        </ScrollView>
      </Pressable>
    </Pressable>
  </Modal>;
}
