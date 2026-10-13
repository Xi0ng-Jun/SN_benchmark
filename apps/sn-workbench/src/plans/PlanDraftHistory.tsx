import React from 'react';
import { View } from 'react-native';
import type { PlanDraftRecord } from '../contracts';
import { Block, Button, Copy, Fold, s, useTheme } from '../ui';

// Controlled read-only plan draft history. The parent owns the fixed
// revision/head list, the fixed detail selection, loading/error and every
// callback result. This file never fetches, stores, schedules or copies
// records into local state, never infers an identity from a title or from
// recency, and never presents a saved draft as executable: selecting a row
// always sends the exact plan_id and revision pair. Selection is only marked
// when both ids match exactly. The type carries no raw/gold or private path
// fields, so none can be rendered here. Row buttons use a short stable
// version label because the shared Button has no local label width constraint;
// the full title renders in a constrained wrapping Copy so long atomic titles
// stay fully visible without widening the row.

export type PlanDraftHistoryProps = {
  records: PlanDraftRecord[];
  selected: PlanDraftRecord | null;
  onSelect: (planId: string, revision: string) => void;
  disabled?: boolean;
  loading?: boolean;
  error?: string | null;
};

function shortId(id: string): string {
  return id.length > 8 ? id.slice(0, 8) : id;
}

function orUnspecified(value: string | null | undefined): string {
  return value !== null && value !== undefined && value.trim() !== '' ? value : '未提供';
}

function savedAtText(value: string): string {
  const time = new Date(value);
  return Number.isNaN(time.getTime()) ? value : time.toLocaleString('zh-CN', { hour12: false });
}

// Null references are reported with their own real meaning, not collapsed into
// one "unknown": a source/model binding was never captured, a scorer was never
// chosen. Non-null values are shown as stored rather than interpreted.
function referenceText(value: unknown, missing: string): string {
  return value === null || value === undefined ? missing
    : typeof value === 'string' ? value : JSON.stringify(value);
}

export function PlanDraftHistory({ records, selected, onSelect, disabled = false, loading = false, error = null }: PlanDraftHistoryProps) {
  const theme = useTheme();
  const monoWrap = [s.mono, { flexShrink: 1, minWidth: 0 }];
  const errorText = error === null ? null : error.trim().length > 0 ? error : '未提供错误详情';
  const isEmpty = records.length === 0;
  // Exact pair only: a shared revision or a matching title is never enough to
  // mark a row as the fixed detail the parent holds.
  const matchesSelected = (record: PlanDraftRecord) => selected !== null
    && record.plan_id === selected.plan_id && record.revision === selected.revision;

  return <View testID="plan-history" style={{ width: '100%', maxWidth: 820, alignSelf: 'center', minWidth: 0, gap: 12 }}>
    <View style={{ gap: 4, minWidth: 0 }}>
      <Copy small style={{ fontWeight: '600' }}>计划草稿历史</Copy>
    </View>

    {errorText !== null && <View testID="plan-history-error" accessibilityRole="alert" style={{ gap: 2, minWidth: 0 }}>
      <Copy small style={{ color: theme.error, flexShrink: 1, minWidth: 0 }}>{`读取计划草稿失败：${errorText}`}</Copy>
    </View>}
    {loading && <Copy small muted>正在读取计划草稿…</Copy>}
    {!loading && errorText === null && isEmpty && <View testID="plan-history-empty" style={{ gap: 2, minWidth: 0 }}>
      <Copy small muted style={{ flexShrink: 1, minWidth: 0 }}>当前任务还没有已保存的计划草稿。</Copy>
    </View>}

    {!isEmpty && <View style={{ gap: 10, minWidth: 0 }}>
      {records.map(record => {
        const selection = record.content.selection;
        const intent = record.content.execution_intent;
        const current = matchesSelected(record);
        const parentText = record.parent_revision !== null ? `，父版本 ${shortId(record.parent_revision)}` : '';
        return <View key={`${record.plan_id}-${record.revision}`} style={{ gap: 4, minWidth: 0, borderWidth: 1, borderColor: theme.line, borderRadius: 8, padding: 12 }}>
          <Copy small style={{ fontWeight: '600', flexShrink: 1, minWidth: 0 }}>{orUnspecified(record.title)}</Copy>
          <Button label={`查看此版本 · ${shortId(record.revision)}`} selected={current} disabled={disabled}
            testID={`plan-record-${record.revision}`}
            accessibilityLabel={`选择计划草稿：${orUnspecified(record.title)}，套件 ${orUnspecified(record.content.input.suite)}，模式 ${intent.mode}，计划 ${shortId(record.plan_id)}，版本 ${shortId(record.revision)}${parentText}`}
            onPress={() => { if (!disabled) onSelect(record.plan_id, record.revision); }} />
          <Copy small muted style={{ flexShrink: 1, minWidth: 0 }}>{`套件：${orUnspecified(record.content.input.suite)} · 选题数：${String(selection.case_ids.length)} 题 · 模式：${intent.mode}`}</Copy>
          <Copy small muted style={{ flexShrink: 1, minWidth: 0 }}>{`保存时间：${savedAtText(record.saved_at)}`}</Copy>
          <Copy small muted style={{ flexShrink: 1, minWidth: 0 }}>{`计划 ${shortId(record.plan_id)} · 版本 ${shortId(record.revision)}${parentText}`}</Copy>
          <Copy small muted style={{ flexShrink: 1, minWidth: 0 }}>{current ? '当前查看 · 草稿' : '草稿'}</Copy>
        </View>;
      })}
    </View>}

    {selected !== null && <Block title="固定版本详情">
      <View testID="plan-fixed-detail" style={{ gap: 6, minWidth: 0 }}>
        <Copy small style={{ fontWeight: '600', flexShrink: 1, minWidth: 0 }}>{orUnspecified(selected.title)}</Copy>
        <Copy small muted style={monoWrap}>{`计划 ID：${selected.plan_id}`}</Copy>
        <Copy small muted style={monoWrap}>{`完整 revision：${selected.revision}`}</Copy>
        <Copy small muted style={monoWrap}>{`内容哈希 content_hash：${selected.content_hash}`}</Copy>
        <Copy small muted style={monoWrap}>{`父版本：${selected.parent_revision === null ? '无（首个版本）' : selected.parent_revision}`}</Copy>
        <Copy small muted style={{ flexShrink: 1, minWidth: 0 }}>{`套件：${orUnspecified(selected.content.input.suite)} · 选题数：${String(selected.content.selection.case_ids.length)} 题 · 模式：${selected.content.execution_intent.mode} · 适配：${selected.content.input.adaptation_revision}`}</Copy>
        <Copy small muted style={monoWrap}>{`canonical bundle 身份：${selected.content.input.bundle_id}`}</Copy>
        <Copy small muted style={monoWrap}>{`输入 ID：${selected.content.input.input_id} · 适配器：${selected.content.input.adapter}`}</Copy>
        <Copy small muted style={monoWrap}>{`来源版本（sha256 身份，非原文）：${selected.content.input.source_revision}`}</Copy>
        <Copy small muted style={{ flexShrink: 1, minWidth: 0 }}>{`执行意图：入口 ${selected.content.execution_intent.entrypoint} · 模式 ${selected.content.execution_intent.mode} · 请求 ${selected.content.execution_intent.request_revision}（Notebook 意图，不代表已执行）`}</Copy>
        <Copy small muted style={{ flexShrink: 1, minWidth: 0 }}>{`模型服务：${referenceText(selected.content.execution_intent.model_services_ref, '未捕获')} · 官方评分器：${referenceText(selected.content.execution_intent.official_scorer_ref, '未选择')}`}</Copy>
        <Copy small muted style={{ flexShrink: 1, minWidth: 0 }}>{`源码绑定：${selected.content.source_intent.binding === 'not_captured' ? '未捕获（not_captured）' : selected.content.source_intent.binding} · 工作区：${selected.content.source_intent.workspace_id}`}</Copy>
        <Copy small muted style={{ flexShrink: 1, minWidth: 0 }}>{`状态：${selected.state} · execution=${String(selected.execution)}（计划草稿不直接执行；实际 Job 状态见“更改”页）`}</Copy>
        <Copy small muted style={monoWrap}>{`选题指纹 selection_hash：${selected.content.selection.selection_hash}`}</Copy>
        <Fold title="题单（显式 case_ids）" value={selected.content.selection.case_ids} />
        <Fold title="派生分区（partition_ids）" value={selected.content.selection.partition_ids} />
      </View>
    </Block>}
  </View>;
}
