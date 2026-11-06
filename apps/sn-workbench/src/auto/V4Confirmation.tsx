import React from 'react';
import { View } from 'react-native';
import { Button, Copy, useTheme } from '../ui';
import { Confirmation, parameterLabels } from './v41control';
import { KeyValue, Section } from '../tree/parts';

export function V4Confirmation({ preview, busy, onConfirm, onCancel }: {
  preview: Confirmation; busy: boolean; onConfirm: () => void; onCancel: () => void;
}) {
  const theme = useTheme();
  const entries = Object.entries(preview.config ?? {}).flatMap(([key, value]) =>
    (key === 'adapter' || key === 'split') && value && typeof value === 'object'
      ? Object.entries(value).map(([nested, v]) => [key === 'adapter' ? `adapter.${nested}` : nested, v] as const)
      : [[key, value] as const]);
  return <View testID="v41-confirmation" style={{ padding: 14, gap: 10, borderWidth: 1, borderColor: theme.warning, borderRadius: 8 }}>
    <Copy>确认{({ calibrate: '开始校准', run: '冻结并开始运行', resume: '续跑', pause: '暂停', stop: '停止' } as Record<string, string>)[preview.action]}</Copy>
    {preview.incurs_cost && <>
      <Copy small>此操作会调用执行服务和 Agent，并产生费用。时间预算：{preview.budget_hours} 小时。</Copy>
      {preview.action === 'calibrate' && <Copy small>校准执行并发上限：{String(preview.config?.calibration_concurrency ?? 1)}。这是本次校准的上限，优化阶段并发在建议书中另行确定。</Copy>}
      <Copy small>测量费用预算：{preview.spend_budget ?? '未设置'}；估算不含 Agent 和已经完成的校准费用，不是整场运行的硬性金额上限。</Copy>
      <Copy small>终评授权：{preview.test_set_authorized ? '已登记；终评仍需单独确认' : '未授权'}。此按钮不会执行终评。</Copy>
      {preview.validation_size != null && <Copy small>搜索 {preview.search_size} 题 · 验证 {preview.validation_size} 题；确认后划分和配置冻结。</Copy>}
      <Section title="确认的配置" defaultOpen={false}>
        {entries.map(([key, value]) => <KeyValue key={key} label={parameterLabels[key] ?? key}>
          <Copy small>{JSON.stringify(value)}{preview.sources?.[key] ? ` · ${preview.sources[key]}` : ''}</Copy>
        </KeyValue>)}
      </Section>
    </>}
    {preview.action === 'pause' && <Copy small>暂停本地调度，保留远端工作，之后可续跑。</Copy>}
    {preview.action === 'stop' && <Copy small>取消在途工作并归档 Agent，结束此次运行。</Copy>}
    <Copy small muted>确认有效期 {Math.round(preview.expires_in_seconds / 60)} 分钟；配置变化后需要重新预览。</Copy>
    <View style={{ flexDirection: 'row', gap: 8 }}>
      <Button label="返回" onPress={onCancel} disabled={busy} />
      <Button label={busy ? '提交中…' : '确认执行'} onPress={onConfirm} disabled={busy} primary testID="v41-confirm" />
    </View>
  </View>;
}
