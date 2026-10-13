import React from 'react';
import { ScrollView, View } from 'react-native';
import { Button, Copy, Input, s, useTheme } from '../ui';

// Local controlled candidate draft form: the parent owns the selected
// assistant source (including its locator identity and epoch),
// candidate kind, candidate text, every save status/blocked reason and all
// request bookkeeping. Callbacks are synchronous notifications only: this
// file never fetches, awaits, subscribes, generates ids/hashes/times, copies
// props or source text into an internal draft, clears input, retries or
// infers success from a status. Source label/scope/hash/text are rendered as
// given; trim is only used for blankness gates and explanations, and onSave
// never means human confirmation.

export type CandidateDraftKind = 'finding' | 'direction' | 'next_step';

export type CandidateDraftSourceKind = 'user' | 'assistant' | 'activity';

export type CandidateDraftSource = {
  entryId: string;
  label: string;
  scopeLabel: string;
  kind: CandidateDraftSourceKind;
  text: string | null;
  sha256: string;
  truncated: boolean;
  current: boolean;
  blockedReason: string | null;
};

export type CandidateDraftSaveStatus = 'idle' | 'saving' | 'saved' | 'error' | 'unknown';

export type CandidateDraftPanelProps = {
  compact: boolean;
  source: CandidateDraftSource | null;
  kind: CandidateDraftKind | null;
  onKindChange: (kind: CandidateDraftKind) => void;
  text: string;
  onTextChange: (value: string) => void;
  saveStatus: CandidateDraftSaveStatus;
  statusDetail?: string;
  saveBlockedReason: string | null;
  onSave?: () => void;
  onCancel: () => void;
  closeLabel?: string;
};

const kindOptions: readonly { kind: CandidateDraftKind; label: string }[] = [
  { kind: 'finding', label: '发现候选' },
  { kind: 'direction', label: '方向候选' },
  { kind: 'next_step', label: '下一步候选' },
];

const roleLabels: Record<CandidateDraftSourceKind, string> = {
  user: '用户',
  assistant: '助手',
  activity: '活动',
};

export function CandidateDraftPanel({ compact, source, kind, onKindChange, text, onTextChange, saveStatus, statusDetail, saveBlockedReason, onSave, onCancel, closeLabel }: CandidateDraftPanelProps) {
  const theme = useTheme();
  const gap = compact ? 8 : 12;
  const sectionStyle = { gap: 6, minWidth: 0, borderTopWidth: 1, borderColor: theme.line, paddingTop: gap };
  const locked = saveStatus === 'saving' || saveStatus === 'unknown';
  const cancelLabel = closeLabel ?? (locked ? '关闭表单' : '取消');
  const sourceName = source === null ? '未选择来源' : source.label;
  const sourceText = source === null ? null : source.text;
  const statusSuffix = statusDetail !== undefined && statusDetail !== '' ? `：${statusDetail}` : '';
  // A non-null blocked reason always blocks, even when the parent string is
  // blank; a blank reason gets a concrete generic explanation instead of
  // being treated as absent.
  const blockedSaveReason = saveBlockedReason === null ? null
    : saveBlockedReason.trim().length === 0 ? '当前暂不能保存候选' : saveBlockedReason;
  const blockedSourceReason = source !== null && source.blockedReason !== null
    ? (source.blockedReason.trim().length === 0 ? '来源不可引用，暂不能保存候选' : source.blockedReason)
    : null;
  // Trim is only used to decide blankness for gates and explanations; the
  // parent keeps and displays the original id/label/scope/hash/text values.
  const canSave = source !== null && source.kind === 'assistant'
    && sourceText !== null && sourceText.trim().length > 0
    && !source.truncated && source.current
    && source.entryId.trim().length > 0 && source.sha256.trim().length > 0 && source.scopeLabel.trim().length > 0
    && source.blockedReason === null
    && kind !== null && text.trim().length > 0
    && saveBlockedReason === null
    && onSave !== undefined
    && (saveStatus === 'idle' || saveStatus === 'error');

  let disabledReason: string | null = null;
  if (blockedSaveReason !== null) disabledReason = blockedSaveReason;
  else if (source === null) disabledReason = '尚未选择候选来源';
  else if (blockedSourceReason !== null) disabledReason = blockedSourceReason;
  else if (source.kind !== 'assistant') disabledReason = '仅助手消息可作为候选来源';
  else if (sourceText === null) disabledReason = '来源没有正文，暂不能保存候选';
  else if (sourceText.trim().length === 0) disabledReason = '来源没有可引用正文，暂不能保存候选';
  else if (source.truncated) disabledReason = '来源正文已截断，暂不能保存完整候选';
  else if (!source.current) disabledReason = '来源已失效，暂不能保存候选';
  else if (source.entryId.trim().length === 0) disabledReason = '来源缺少条目标识，暂不能保存候选';
  else if (source.sha256.trim().length === 0) disabledReason = '来源缺少内容 hash，暂不能保存候选';
  else if (source.scopeLabel.trim().length === 0) disabledReason = '来源缺少任务/会话/Agent/序号范围，暂不能保存候选';
  else if (kind === null) disabledReason = '请选择候选类别';
  else if (text.trim().length === 0) disabledReason = '候选内容不能为空';
  else if (onSave === undefined) disabledReason = '保存尚未接入';

  const saveStatusText = saveStatus === 'saving' ? '正在保存候选…'
    : saveStatus === 'saved' ? '候选已保存'
      : saveStatus === 'error' ? `保存失败${statusSuffix}`
        : saveStatus === 'unknown' ? `保存回执未知，暂不能再保存${statusSuffix}；请先核对保存结果` : null;
  // In-flight and unknown receipts already explain why saving is blocked; a
  // concrete gate reason is only shown while the parent reports idle/error.
  const showDisabledReason = !canSave && disabledReason !== null && (saveStatus === 'idle' || saveStatus === 'error');

  return <View testID="candidate-draft-panel" style={{ width: '100%', maxWidth: 820, alignSelf: 'center', minWidth: 0, gap }}>
    <View style={{ gap: 6, minWidth: 0 }}>
      <Copy small style={{ fontWeight: '600' }}>候选来源</Copy>
      {source === null
        ? <Copy small muted>尚未选择候选来源；请先在只读消息中选择助手条目</Copy>
        : <>
          <View style={s.row}>
            <Copy small style={{ fontWeight: '600', flexShrink: 1, minWidth: 0 }} testID="candidate-source-label">{source.label}</Copy>
            <Copy small muted>{roleLabels[source.kind]}</Copy>
          </View>
          {source.scopeLabel.trim().length === 0
            ? <Copy small muted>来源缺少任务/会话/Agent/序号范围</Copy>
            : <Copy small muted style={{ flexShrink: 1, minWidth: 0 }}>{`范围：${source.scopeLabel}`}</Copy>}
          <View testID="candidate-source-text" style={{ minWidth: 0 }}>
            {sourceText === null
              ? <Copy muted>来源没有正文</Copy>
              : sourceText.trim().length === 0
                ? <Copy muted>来源没有可引用正文</Copy>
                : <ScrollView style={{ maxHeight: 240, minWidth: 0 }}><Copy>{sourceText}</Copy></ScrollView>}
          </View>
          {source.truncated && <Copy small muted>正文已截断</Copy>}
          {!source.current && <Copy small muted>来源已失效</Copy>}
          {source.sha256.trim().length === 0
            ? <Copy small muted testID="candidate-source-hash">来源缺少内容 hash</Copy>
            : <Copy small muted testID="candidate-source-hash" style={[s.mono, { flexShrink: 1, minWidth: 0 }]}>{`SHA-256：${source.sha256}`}</Copy>}
          <Copy small muted>来源原文只读，下方候选文本单独编辑</Copy>
        </>}
    </View>

    <View style={sectionStyle}>
      <Copy small style={{ fontWeight: '600' }}>候选草稿</Copy>
      <View style={s.row}>
        {kindOptions.map(option => <Button key={option.kind} label={option.label} testID={`candidate-kind-${option.kind}`}
          selected={kind === option.kind} disabled={locked}
          accessibilityLabel={`${sourceName}：选择${option.label}`}
          onPress={() => onKindChange(option.kind)} />)}
      </View>
      <Input value={text} onChange={onTextChange} multiline label="候选内容" testID="candidate-text" editable={!locked} />
      <Copy small muted>模型内容仅保存为候选，尚未经过人工确认</Copy>
    </View>

    <View style={sectionStyle}>
      <View style={s.row}>
        <Button label="保存候选" primary testID="candidate-save" disabled={!canSave}
          accessibilityLabel={`保存候选：来源 ${sourceName}`}
          onPress={() => { if (canSave) onSave?.(); }} />
        <Button label={cancelLabel} testID="candidate-cancel"
          accessibilityLabel={`${cancelLabel}：来源 ${sourceName}`}
          onPress={() => onCancel()} />
      </View>
      {saveStatusText !== null && <Copy small style={{ color: saveStatus === 'error' ? theme.error : theme.muted }}>{saveStatusText}</Copy>}
      {showDisabledReason && <Copy small muted style={{ flexShrink: 1, minWidth: 0 }}>{disabledReason}</Copy>}
      {locked && <Copy small muted>关闭表单不撤销已经提交的保存</Copy>}
    </View>
  </View>;
}
