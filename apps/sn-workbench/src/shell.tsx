import React, { useEffect, useRef, useState } from 'react';
import { Platform, Pressable, View } from 'react-native';
import type { LucideIcon } from 'lucide-react-native';
import { useTheme } from './ui';

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
