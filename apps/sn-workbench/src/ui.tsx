import React, { createContext, useContext } from 'react';
import { Pressable, PressableStateCallbackType, StyleSheet, Text, TextInput, View, ViewStyle } from 'react-native';
import type { LucideIcon } from 'lucide-react-native';

// Linear tokens (apps/sn-workbench/DESIGN.md): surface ladder + hairlines, one lavender accent.
// Semantic colours are reserved for status and numeric deltas.
export const themes = {
  light: { bg: '#ffffff', sidebar: '#f5f6f6', surface: '#f6f7f7', raised: '#ffffff', selected: '#ebecee', line: '#e6e6e8', lineStrong: '#d4d5d8',
    text: '#1b1b1f', secondary: '#3c3f46', muted: '#6b6f76', accent: '#3c3f46', primary: '#5e6ad2', primaryHover: '#828fff', onPrimary: '#ffffff', error: '#c4282d',
    running: '#5e6ad2', warning: '#b7791f', danger: '#e5484d', success: '#27a644', add: '#2f7a45', del: '#b4383c',
    gold: '#fde68a', cited: '#c7d2fe', retrieved: '#ececee', addBg: '#ecfdf3', delBg: '#fdf0f0' },
  dark: { bg: '#0f1011', sidebar: '#010102', surface: '#141516', raised: '#18191a', selected: '#202125', line: '#23252a', lineStrong: '#34343a',
    text: '#f7f8f8', secondary: '#d0d6e0', muted: '#8a8f98', accent: '#d0d6e0', primary: '#5e6ad2', primaryHover: '#828fff', onPrimary: '#ffffff', error: '#ff8a8a',
    running: '#828fff', warning: '#d9a33a', danger: '#ff6369', success: '#3dbf5c', add: '#6cb17b', del: '#e0807a',
    gold: '#5c4a12', cited: '#262c5c', retrieved: '#1c1d20', addBg: '#10251a', delBg: '#2b1517' },
};
export const MONO = 'ui-monospace, "SF Mono", "JetBrains Mono", Menlo, monospace';
export type Theme = typeof themes.light;
export const ThemeContext = createContext<Theme>(themes.light);
export const useTheme = () => useContext(ThemeContext);
export const s = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'center', gap: 8, flexWrap: 'wrap' },
  heading: { fontSize: 20, fontWeight: '600', lineHeight: 28 },
  section: { fontSize: 14, fontWeight: '600', lineHeight: 22 },
  text: { fontSize: 14, lineHeight: 23 },
  hint: { fontSize: 12, lineHeight: 20 },
  mono: { fontFamily: MONO, fontSize: 12, lineHeight: 18 },
  card: { borderWidth: 1, borderRadius: 12, padding: 20, gap: 14 },
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
      flexDirection: 'row', alignItems: 'center', gap: 6, borderRadius: 8, paddingHorizontal: 12, paddingVertical: 6,
      backgroundColor: primary ? (hovered || pressed ? theme.primaryHover : theme.primary) : selected || hovered || pressed ? theme.selected : 'transparent',
      borderWidth: 1, borderColor: primary ? 'transparent' : selected ? theme.lineStrong : 'transparent', opacity: disabled ? .45 : 1,
    })}>
    {Icon && <Icon size={14} strokeWidth={1.75} color={primary ? theme.onPrimary : theme.muted} />}
    <Text style={{ color: primary ? theme.onPrimary : theme.text, fontSize: 13, fontWeight: '500', lineHeight: 18 }}>{label}</Text>
  </Pressable>;
}
export function Input({ value, onChange, placeholder, multiline = false, label, editable = true, testID }: {
  value: string; onChange: (v: string) => void; placeholder?: string; multiline?: boolean; label: string; editable?: boolean; testID?: string;
}) {
  const theme = useTheme();
  return <TextInput value={value} onChangeText={onChange} placeholder={placeholder} placeholderTextColor={theme.muted}
    accessibilityLabel={label} multiline={multiline} editable={editable} testID={testID}
    style={{ borderWidth: 1, borderColor: theme.line, borderRadius: 8, paddingVertical: 8, paddingHorizontal: 12, color: theme.text, backgroundColor: theme.surface,
      fontSize: 14, lineHeight: 22, minHeight: multiline ? 86 : 36, textAlignVertical: 'top' }} />;
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
    {open && <Copy small style={{ fontFamily: MONO }}>{typeof value === 'string' ? value : JSON.stringify(value, null, 2)}</Copy>}
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
