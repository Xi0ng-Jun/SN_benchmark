import React, { useState } from 'react';
import { View } from 'react-native';
import { api, useResource } from '../contracts';
import { Badge, Section } from '../tree/parts';
import { Button, Copy, useTheme } from '../ui';
import { controlPath, parameterLabels } from './v41control';

type Source = { iteration_id: string; title?: string | null; ok: boolean; failed?: string[]; reason?: string;
  probe?: number; diagnosis?: number; remainder_tasks?: number };
type ImportPreview = { ok: boolean; checks: { check: string; ok: boolean }[]; plan_sha256: string; probe: number; diagnosis: number;
  remainder_tasks: number; links: number; source_iteration_id: string; confirmation_token?: string; expires_in_seconds?: number;
  proposal: { values: Record<string, unknown>; user_edited: string[]; advisor: string | null } };

/** Reuse another iteration's completed calibration (same seed, input and pool) instead of measuring again. */
export function CalibrationReuse({ iterationId, disabled, onImported }: { iterationId: string; disabled: boolean; onImported: () => void }) {
  const theme = useTheme(), base = controlPath(iterationId);
  const sources = useResource<{ sources: Source[] }>(base + '/calibration-sources');
  const [preview, setPreview] = useState<ImportPreview | null>(null), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const call = async (source: string, token?: string) => {
    setBusy(true); setError('');
    try {
      const result = await api<ImportPreview & { accepted?: boolean }>(base + '/import-calibration',
        { source_iteration_id: source, ...(token ? { confirmation_token: token } : {}) });
      if (token) { setPreview(null); onImported(); } else setPreview(result);
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  const list = sources.data?.sources ?? [];
  if (!list.length) return null;
  return <Section title="复用已有校准" testID="v41-calibration-reuse" defaultOpen>
    <Copy small muted>同一起点 commit、输入、题池、划分种子、k 与 Agent 设置的迭代可直接复用校准测量、验证集剩余题基线和建议书（含当时的修改），不再重测种子。资料准备缓存按内容自动复用。</Copy>
    {preview ? <View testID="v41-import-preview" style={{ gap: 6, padding: 10, borderWidth: 1, borderColor: theme.warning, borderRadius: 8 }}>
      <Copy small>来源迭代 {preview.source_iteration_id.slice(0, 8)} · P {preview.probe} 题 · D {preview.diagnosis} 题 · 剩余基线 {preview.remainder_tasks} 题 · 挂接执行 {preview.links} 个</Copy>
      {preview.checks.filter(c => !c.ok).map(c => <Copy key={c.check} small style={{ color: theme.error }}>不一致：{c.check}</Copy>)}
      {preview.ok && <Copy small muted>{preview.checks.length} 项身份核对全部通过。导入不调用模型、不产生费用；来源迭代只读。</Copy>}
      <Copy small muted>建议书将作为默认值带入（用户改过：{preview.proposal.user_edited.map(k => parameterLabels[k] ?? k).join('、') || '无'}；顾问：{preview.proposal.advisor ?? '无'}），导入后可在建议书中调整，再预览冻结。</Copy>
      <View style={{ flexDirection: 'row', gap: 8 }}>
        <Button label="返回" disabled={busy} onPress={() => setPreview(null)} />
        {preview.ok && preview.confirmation_token && <Button label={busy ? '导入中…' : '确认复用'} primary disabled={busy || disabled}
          onPress={() => call(preview.source_iteration_id, preview.confirmation_token)} testID="v41-import-confirm" />}
      </View>
    </View> : list.map(s => <View key={s.iteration_id} style={{ flexDirection: 'row', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
      <Badge label={s.ok ? '可复用' : '不兼容'} tone={s.ok ? 'success' : 'warning'} />
      <Copy small style={{ flex: 1 }}>{s.title ?? s.iteration_id.slice(0, 8)}{s.probe != null ? ` · P ${s.probe} / D ${s.diagnosis}` : ''}
        {!s.ok ? ` · ${s.reason ?? (s.failed ?? []).join('、')}` : ''}</Copy>
      <Button label="预览复用此校准" disabled={busy || disabled || !s.ok} onPress={() => call(s.iteration_id)} testID={'v41-import-' + s.iteration_id} />
    </View>)}
    {!!error && <Copy small style={{ color: theme.error }}>{error}</Copy>}
  </Section>;
}
