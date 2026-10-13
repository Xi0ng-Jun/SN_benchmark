import React from 'react';
import { View } from 'react-native';
import { Button, s } from '../ui';

// UI01 controlled view switcher for a task surface. The parent owns the active
// view and renders each view itself; this file keeps no selection, request or
// storage state and never picks a default. `disabled` disables the controls and
// guards the callback so a stale press cannot change the parent state. The
// Button's tab role exposes the selected state explicitly on web
// and the app's light/dark/narrow styling.

export type TaskView = 'work' | 'changes' | 'results' | 'plans' | 'records';

export type TaskViewNavProps = {
  view: TaskView;
  onSelect: (view: TaskView) => void;
  disabled?: boolean;
};

const views: readonly { id: TaskView; label: string }[] = [
  { id: 'work', label: '工作' },
  { id: 'changes', label: '更改' },
  { id: 'results', label: '结果' },
  { id: 'plans', label: '计划' },
  { id: 'records', label: '记录' },
];

export function TaskViewNav({ view, onSelect, disabled = false }: TaskViewNavProps) {
  const select = (next: TaskView) => {
    if (disabled) return;
    onSelect(next);
  };
  return <View accessibilityRole="tablist" accessibilityLabel="任务工作面" style={s.row}>
    {views.map(item => <Button key={item.id} label={item.label} accessibilityRole="tab" selected={view === item.id}
      disabled={disabled} onPress={() => select(item.id)} testID={'view-' + item.id} />)}
  </View>;
}
