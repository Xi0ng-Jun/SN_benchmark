import React from 'react';
import { Platform, Pressable, Text, TextInput, View } from 'react-native';
import { ArrowUp, Paperclip, X } from 'lucide-react-native';
import { hover, useTheme } from '../ui';

export type ComposerAttachment = { id: string; label: string };
export type ComposerStatus = { text: string; tone: 'muted' | 'error'; action?: { label: string; onPress: () => void } };

export type ChatComposerProps = {
  compact: boolean;
  value: string;
  onChange: (value: string) => void;
  editable: boolean;
  canSend: boolean;
  onSend: () => void;
  placeholder: string;
  attachments: readonly ComposerAttachment[];
  onOpenAttachment: (id: string) => void;
  onRemoveAttachment: (id: string) => void;
  attachOpen: boolean;
  onToggleAttach: () => void;
  attachEnabled: boolean;
  /** Popover rendered above the input (attachments or Agent menu). */
  popover: React.ReactNode;
  agentButton: React.ReactNode;
  status: ComposerStatus | null;
};

/** Paseo-style input: one box with attachments, Agent/model choice and send. */
export function ChatComposer({ compact, value, onChange, editable, canSend, onSend, placeholder, attachments, onOpenAttachment,
  onRemoveAttachment, attachOpen, onToggleAttach, attachEnabled, popover, agentButton, status }: ChatComposerProps) {
  const theme = useTheme();
  const send = () => { if (canSend) onSend(); };
  return <View testID="chat-composer" style={{ paddingHorizontal: compact ? 12 : 24, paddingBottom: compact ? 12 : 18, paddingTop: 8 }}>
    <View style={{ width: '100%', maxWidth: 820, alignSelf: 'center', gap: 6 }}>
      {popover}
      {status && <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, flexWrap: 'wrap', paddingHorizontal: 4 }}>
        <Text testID="chat-status" style={{ flexShrink: 1, fontSize: 12, lineHeight: 18, color: status.tone === 'error' ? theme.error : theme.muted }}>{status.text}</Text>
        {status.action && <Pressable accessibilityRole="button" onPress={status.action.onPress} testID="chat-status-action">
          <Text style={{ fontSize: 12, color: theme.text, textDecorationLine: 'underline' }}>{status.action.label}</Text></Pressable>}
      </View>}
      <View style={{ borderWidth: 1, borderColor: theme.line, borderRadius: 14, backgroundColor: theme.bg, padding: 8, gap: 6 }}>
        {attachments.length > 0 && <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6 }}>
          {attachments.map(item => <View key={item.id} style={{ flexDirection: 'row', alignItems: 'center', borderRadius: 8, backgroundColor: theme.surface,
            borderWidth: 1, borderColor: theme.line, maxWidth: '100%' }}>
            <Pressable accessibilityRole="button" accessibilityLabel={'查看材料 ' + item.label} onPress={() => onOpenAttachment(item.id)}
              style={{ paddingLeft: 8, paddingVertical: 4, flexShrink: 1 }}>
              <Text numberOfLines={1} style={{ fontSize: 12, color: theme.text }}>{item.label}</Text>
            </Pressable>
            <Pressable accessibilityRole="button" accessibilityLabel={'移除材料 ' + item.label} disabled={!editable}
              onPress={() => onRemoveAttachment(item.id)} style={{ padding: 6 }}><X size={12} color={theme.muted} /></Pressable>
          </View>)}
        </View>}
        <TextInput value={value} onChangeText={onChange} editable={editable} multiline placeholder={placeholder}
          placeholderTextColor={theme.muted} accessibilityLabel="Agent 消息" testID="message-input"
          onKeyPress={event => {
            const native = event.nativeEvent as { key: string; shiftKey?: boolean; isComposing?: boolean };
            if (Platform.OS === 'web' && native.key === 'Enter' && !native.shiftKey && !native.isComposing) {
              (event as unknown as { preventDefault: () => void }).preventDefault(); send();
            }
          }}
          style={{ minHeight: 44, maxHeight: 220, paddingHorizontal: 6, paddingVertical: 4, fontSize: 14, lineHeight: 22, color: theme.text,
            textAlignVertical: 'top', ...(Platform.OS === 'web' ? { outlineStyle: 'none' } as object : {}) }} />
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 4 }}>
          <Pressable accessibilityRole="button" accessibilityLabel="引用材料" accessibilityState={{ expanded: attachOpen, disabled: !attachEnabled }}
            disabled={!attachEnabled} onPress={onToggleAttach} testID="message-context-toggle"
            style={hover(hovered => ({ flexDirection: 'row', alignItems: 'center', gap: 4, borderRadius: 8, paddingHorizontal: 8, height: 28,
              opacity: attachEnabled ? 1 : .45, backgroundColor: attachOpen || hovered ? theme.selected : 'transparent' }))}>
            <Paperclip size={14} strokeWidth={1.6} color={theme.muted} />
            <Text style={{ fontSize: 12, color: theme.muted }}>材料</Text>
          </Pressable>
          {agentButton}
          <View style={{ flex: 1 }} />
          <Pressable accessibilityRole="button" accessibilityLabel="发送消息" accessibilityState={{ disabled: !canSend }} disabled={!canSend}
            onPress={send} testID="send-message"
            style={{ width: 30, height: 30, borderRadius: 15, alignItems: 'center', justifyContent: 'center',
              backgroundColor: canSend ? theme.text : theme.selected }}>
            <ArrowUp size={16} strokeWidth={2} color={canSend ? theme.bg : theme.muted} />
          </Pressable>
        </View>
      </View>
    </View>
  </View>;
}
