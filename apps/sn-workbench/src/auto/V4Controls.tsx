import React, { useState } from 'react';
import { View } from 'react-native';
import { api } from '../contracts';
import { Button, Copy, useTheme } from '../ui';
import { V4RunView } from './v4contracts';
import { Confirmation, controlPath } from './v41control';
import { V4Confirmation } from './V4Confirmation';

export function V4Controls({ view, onRefresh }: { view: V4RunView; onRefresh: () => void }) {
  const theme = useTheme();
  const [preview, setPreview] = useState<Confirmation | null>(null), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const [requestedRetry, setRequestedRetry] = useState(false);
  if (!view.run.iteration_id) return null;
  const base = controlPath(view.run.iteration_id);
  const request = async (action: string, confirmation?: Confirmation) => {
    setBusy(true); setError('');
    const retry = confirmation ? requestedRetry : view.next_action === 'retry' && view.progress?.failure_kind !== 'agent_request';
    try {
      const result = await api<Confirmation>(base + '/run/' + action, { retry_failed: retry, ...(confirmation ? { confirmation_token: confirmation.confirmation_token } : {}) });
      if (confirmation) { setPreview(null); onRefresh(); } else { setRequestedRetry(retry); setPreview(result); }
    } catch (e) { setPreview(null); setError((e as Error).message); } finally { setBusy(false); }
  };
  return <View style={{ gap: 8 }}>
    {preview ? <V4Confirmation preview={preview} busy={busy} onConfirm={() => request(preview.action, preview)} onCancel={() => setPreview(null)} /> : <View style={{ flexDirection: 'row', gap: 8 }}>
      {view.run.alive ? <><Button label="暂停（可续跑）" disabled={busy} onPress={() => request('pause')} testID="v41-pause" /></>
        : view.progress && ['resume', 'retry', 'budget'].includes(view.next_action ?? '') ? <Button label="预览续跑" disabled={busy} onPress={() => request('resume')} testID="v41-resume" /> : null}
      <Button label="刷新状态" disabled={busy} onPress={onRefresh} />
      {view.run.alive && <><View style={{ flex: 1 }} /><Button label="终止运行（不可恢复）" danger disabled={busy} onPress={() => request('stop')} testID="v41-stop" /></>}
    </View>}
    {!!error && <Copy small style={{ color: theme.error }}>{error}</Copy>}
  </View>;
}
