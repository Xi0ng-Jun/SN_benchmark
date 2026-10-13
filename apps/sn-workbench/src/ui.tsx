import React, { createContext, useContext } from 'react';
import { Pressable, PressableStateCallbackType, StyleSheet, Text, TextInput, View, ViewStyle } from 'react-native';
import type { LucideIcon } from 'lucide-react-native';

// Geometry and Zinc palette follow Paseo's theme.ts; components are local,
// so they do not depend on Paseo's private stores or panel context.
export const themes = {
  light: { bg: '#ffffff', sidebar: '#f4f4f5', surface: '#fafafa', selected: '#e4e4e7', line: '#e4e4e7', text: '#1a1a1e', muted: '#71717a', accent: '#3f3f46', error: '#b91c1c',
    // Paseo statusDot* band and diff add/delete tones.
    running: '#268ae0', warning: '#b37824', danger: '#f12e2f', success: '#299f51', add: '#3e704a', del: '#9d433b',
    gold: '#fde68a', cited: '#bfdbfe', retrieved: '#ececee', addBg: '#ecfdf3', delBg: '#fef2f2' },
  dark: { bg: '#18181b', sidebar: '#131316', surface: '#1f1f22', selected: '#27272a', line: '#27272a', text: '#f4f4f5', muted: '#a1a1aa', accent: '#d4d4d8', error: '#fca5a5',
    running: '#5caaf6', warning: '#db932e', danger: '#f7796d', success: '#35c264', add: '#6cb17b', del: '#d8847b',
    gold: '#5c4a12', cited: '#1e3a5f', retrieved: '#2a2a2e', addBg: '#12261a', delBg: '#2b1515' },
};
export type Theme = typeof themes.light;
export const ThemeContext = createContext<Theme>(themes.light);
export const useTheme = () => useContext(ThemeContext);
export const s = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'center', gap: 8, flexWrap: 'wrap' },
  heading: { fontSize: 20, fontWeight: '600', lineHeight: 28 },
  section: { fontSize: 14, fontWeight: '600', lineHeight: 22 },
  text: { fontSize: 14, lineHeight: 23 },
  hint: { fontSize: 12, lineHeight: 20 },
  mono: { fontFamily: 'monospace', fontSize: 11, lineHeight: 18 },
  card: { borderWidth: 1, borderRadius: 8, padding: 20, gap: 14 },
});
export function Copy({ children, muted = false, small = false, style, testID }: { children: React.ReactNode; muted?: boolean; small?: boolean; style?: object; testID?: string }) {
  const theme = useTheme();
  return <Text selectable testID={testID} style={[small ? s.hint : s.text, { color: muted ? theme.muted : theme.text }, style]}>{children}</Text>;
}
export function Button({ label, accessibilityLabel, accessibilityRole = 'button', onPress, icon: Icon, selected = false, disabled = false, primary = false, testID }: {
  label: string; accessibilityLabel?: string; accessibilityRole?: 'button' | 'tab'; onPress: () => void; icon?: LucideIcon; selected?: boolean; disabled?: boolean; primary?: boolean; testID?: string;
}) {
  const theme = useTheme();
  const [hovered, setHovered] = React.useState(false);
  return <Pressable accessibilityRole={accessibilityRole} accessibilityLabel={accessibilityLabel ?? label} accessibilityState={{ disabled, selected }}
    aria-selected={accessibilityRole === 'tab' ? selected : undefined} testID={testID}
    disabled={disabled} onPress={onPress} onHoverIn={() => setHovered(true)} onHoverOut={() => setHovered(false)} style={({ pressed }) => ({
      flexDirection: 'row', alignItems: 'center', gap: 6, borderRadius: 6, paddingHorizontal: 10, paddingVertical: 7,
      backgroundColor: primary ? theme.text : selected || hovered || pressed ? theme.selected : 'transparent', opacity: disabled ? .45 : 1,
    })}>
    {Icon && <Icon size={15} strokeWidth={1.5} color={primary ? theme.bg : theme.muted} />}
    <Text style={{ color: primary ? theme.bg : theme.text, fontSize: 12, lineHeight: 18 }}>{label}</Text>
  </Pressable>;
}
export function Input({ value, onChange, placeholder, multiline = false, label, editable = true, testID }: {
  value: string; onChange: (v: string) => void; placeholder?: string; multiline?: boolean; label: string; editable?: boolean; testID?: string;
}) {
  const theme = useTheme();
  return <TextInput value={value} onChangeText={onChange} placeholder={placeholder} placeholderTextColor={theme.muted}
    accessibilityLabel={label} multiline={multiline} editable={editable} testID={testID}
    style={{ borderWidth: 1, borderColor: theme.line, borderRadius: 6, padding: 12, color: theme.text, backgroundColor: theme.bg,
      fontSize: 14, lineHeight: 22, minHeight: multiline ? 86 : 42, textAlignVertical: 'top' }} />;
}
export function Block({ title, children }: { title: string; children: React.ReactNode }) {
  const theme = useTheme();
  return <View style={{ gap: 10, marginBottom: 24 }}><Text style={[s.section, { color: theme.text }]}>{title}</Text>{children}</View>;
}
export function Fold({ title, value }: { title: string; value: unknown }) {
  const [open, setOpen] = React.useState(false);
  const theme = useTheme();
  return <View style={{ gap: 8, borderTopWidth: 1, borderColor: theme.line, paddingTop: 8 }}>
    <Button label={(open ? '▾ ' : '▸ ') + title} onPress={() => setOpen(!open)} />
    {open && <Copy small style={{ fontFamily: 'monospace' }}>{typeof value === 'string' ? value : JSON.stringify(value, null, 2)}</Copy>}
  </View>;
}

export type DotTone = 'running' | 'warning' | 'danger' | 'success' | 'idle' | null;
// Paseo agent-status-dot: 6px filled dot; idle shows the line colour, null shows nothing.
export function StatusDot({ tone, size = 6 }: { tone: DotTone; size?: number }) {
  const theme = useTheme();
  if (!tone) return null;
  const color = tone === 'idle' ? theme.line : theme[tone];
  return <View style={{ width: size, height: size, borderRadius: size, backgroundColor: color, flexShrink: 0 }} />;
}
export function Pill({ label, tone, testID }: { label: string; tone?: 'warning' | 'danger' | 'success' | 'running'; testID?: string }) {
  const theme = useTheme();
  return <View testID={testID} style={{ borderRadius: 999, paddingHorizontal: 7, height: 18, justifyContent: 'center', backgroundColor: theme.selected }}>
    <Text style={{ fontSize: 11, lineHeight: 14, color: tone ? theme[tone] : theme.muted, fontVariant: ['tabular-nums'] }}>{label}</Text>
  </View>;
}
export function fmt(value: number | null | undefined, digits = 3): string {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(digits) : '—';
}
// react-native-web adds `hovered` to Pressable state; RN's types do not declare it.
export const hover = (style: (hovered: boolean) => ViewStyle) =>
  (state: PressableStateCallbackType) => style(Boolean((state as PressableStateCallbackType & { hovered?: boolean }).hovered));
