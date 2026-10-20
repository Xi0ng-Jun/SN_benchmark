import React, { useState } from 'react';
import { Modal, Pressable, ScrollView, Text, TextInput, View } from 'react-native';
import { ChevronDown, ChevronRight } from 'lucide-react-native';
import { Button, Copy, MONO, useTheme } from '../ui';
import { signed } from './contracts';

// Small primitives for the iteration screens (Linear tokens, DESIGN.md).

export function Mono({ children, muted = false, size = 12, color }: { children: React.ReactNode; muted?: boolean; size?: number; color?: string }) {
  const theme = useTheme();
  return <Text selectable style={{ fontFamily: MONO, fontSize: size, lineHeight: size + 6, color: color ?? (muted ? theme.muted : theme.text),
    fontVariant: ['tabular-nums'] }}>{children}</Text>;
}

// A numeric delta: green when better, red when worse, never coloured when unknown.
export function Delta({ value, digits = 3, testID }: { value: number | null | undefined; digits?: number; testID?: string }) {
  const theme = useTheme();
  const color = typeof value !== 'number' || value === 0 ? theme.muted : value > 0 ? theme.success : theme.danger;
  return <Text testID={testID} style={{ fontFamily: MONO, fontSize: 12, lineHeight: 18, color, fontVariant: ['tabular-nums'] }}>{signed(value, digits)}</Text>;
}

export function Badge({ label, tone, testID }: { label: string; tone?: 'success' | 'danger' | 'warning' | 'running' | 'primary'; testID?: string }) {
  const theme = useTheme();
  return <View testID={testID} style={{ borderRadius: 999, paddingHorizontal: 8, height: 20, justifyContent: 'center',
    backgroundColor: theme.selected, alignSelf: 'flex-start' }}>
    <Text style={{ fontSize: 11, lineHeight: 14, fontWeight: '500', color: tone ? theme[tone] : theme.secondary }}>{label}</Text>
  </View>;
}

export function Eyebrow({ children }: { children: React.ReactNode }) {
  const theme = useTheme();
  return <Text style={{ fontSize: 11, lineHeight: 16, fontWeight: '500', letterSpacing: 0.4, color: theme.muted, textTransform: 'uppercase' }}>{children}</Text>;
}

// A collapsible section in the node detail column; sections stack, never page.
export function Section({ title, meta, defaultOpen = true, children, testID }: {
  title: string; meta?: React.ReactNode; defaultOpen?: boolean; children: React.ReactNode; testID?: string;
}) {
  const theme = useTheme();
  const [open, setOpen] = useState(defaultOpen);
  const Icon = open ? ChevronDown : ChevronRight;
  return <View testID={testID} style={{ borderTopWidth: 1, borderColor: theme.line }}>
    <Pressable accessibilityRole="button" accessibilityState={{ expanded: open }} onPress={() => setOpen(o => !o)}
      style={{ flexDirection: 'row', alignItems: 'center', gap: 6, paddingVertical: 10 }}>
      <Icon size={14} strokeWidth={1.75} color={theme.muted} />
      <Text style={{ flex: 1, fontSize: 13, fontWeight: '500', color: theme.text }}>{title}</Text>
      {meta}
    </Pressable>
    {open && <View style={{ paddingBottom: 14, gap: 8 }}>{children}</View>}
  </View>;
}

export function KeyValue({ label, children }: { label: string; children: React.ReactNode }) {
  const theme = useTheme();
  return <View style={{ flexDirection: 'row', gap: 12, alignItems: 'flex-start', minHeight: 22 }}>
    <Text style={{ width: 72, fontSize: 12, lineHeight: 18, color: theme.muted }}>{label}</Text>
    <View style={{ flex: 1, minWidth: 0 }}>{typeof children === 'string' ? <Copy small>{children}</Copy> : children}</View>
  </View>;
}

export type ConfirmSpec = {
  title: string; lines: { label: string; value: string }[]; confirmLabel: string; danger?: boolean;
  note?: { label: string; required: boolean; placeholder?: string };
  run: (note: string) => Promise<unknown>;
};

// Every write from the page passes through this dialog: the person sees what will
// run, with which parameters, before the server is asked to do it.
export function ConfirmDialog({ spec, onClose, onDone }: { spec: ConfirmSpec | null; onClose: () => void; onDone: (result: unknown) => void }) {
  const theme = useTheme();
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const close = () => { if (!busy) { setNote(''); setError(''); onClose(); } };
  if (!spec) return null;
  const blocked = busy || Boolean(spec.note?.required && !note.trim());
  const confirm = async () => {
    setBusy(true); setError('');
    try { const result = await spec.run(note.trim()); setNote(''); onDone(result); }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  return <Modal visible transparent animationType="none" onRequestClose={close}>
    <View style={{ flex: 1, backgroundColor: '#0009', alignItems: 'center', justifyContent: 'center', padding: 24 }}>
      <View testID="confirm-dialog" style={{ width: '100%', maxWidth: 520, maxHeight: '90%', backgroundColor: theme.raised, borderRadius: 12,
        borderWidth: 1, borderColor: theme.lineStrong, padding: 20, gap: 14 }}>
        <Text style={{ fontSize: 16, fontWeight: '600', color: theme.text }}>{spec.title}</Text>
        <ScrollView style={{ maxHeight: 360 }} contentContainerStyle={{ gap: 6 }}>
          {spec.lines.map(line => <KeyValue key={line.label} label={line.label}>{line.value}</KeyValue>)}
        </ScrollView>
        {spec.note && <TextInput testID="confirm-note" value={note} onChangeText={setNote} multiline placeholder={spec.note.placeholder}
          placeholderTextColor={theme.muted} accessibilityLabel={spec.note.label}
          style={{ borderWidth: 1, borderColor: theme.line, borderRadius: 8, padding: 10, minHeight: 64, color: theme.text,
            backgroundColor: theme.surface, fontSize: 13, textAlignVertical: 'top', outlineColor: theme.primary, outlineWidth: 1 } as object} />}
        {!!error && <Copy small style={{ color: theme.error }} testID="confirm-error">{error}</Copy>}
        <View style={{ flexDirection: 'row', justifyContent: 'flex-end', gap: 8 }}>
          <Button label="取消" onPress={close} disabled={busy} testID="confirm-cancel" />
          <Button label={busy ? '执行中…' : spec.confirmLabel} primary onPress={confirm} disabled={blocked} testID="confirm-run" />
        </View>
      </View>
    </View>
  </Modal>;
}
