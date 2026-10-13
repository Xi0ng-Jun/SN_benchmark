import React from 'react';
import { ScrollView, View } from 'react-native';
import { Button, Copy, Input, s, useTheme } from '../ui';

// Local controlled records browser for already-saved model candidates and
// append-only human decisions. The parent owns the local list snapshot,
// selection, decision draft, actor/head labels, request key, base revision
// and every status or receipt. Callbacks are synchronous notifications only:
// this file never fetches, awaits, subscribes, generates ids/hashes/times,
// clears input, auto-selects a candidate, retries or infers a receipt or the
// actor head. Global list order and per-candidate decision order come from
// the parent; a candidate's saved decisions are never treated as the current
// actor's head. Receipt binding is only shown when the parent marks the
// receipt current and its candidate/revision identity, judgment, reason and
// actor label match the current selection and draft. Text/hash/scope are
// rendered exactly as given and trim is only
// used for blankness gates and explanations.

export type CandidateJudgment = 'confirmed' | 'rejected' | 'undecided';

export type CandidateRecordsListStatus = 'idle' | 'loading' | 'ready' | 'error';

export type CandidateDecisionStatus = 'idle' | 'saving' | 'saved' | 'error' | 'unknown';

export type CandidateRecordDecision = {
  id: string;
  actorLabel: string;
  judgment: CandidateJudgment;
  reason: string;
  savedAtLabel: string;
};

export type CandidateRecordSource = {
  scopeLabel: string;
  text: string;
  sha256: string;
};

export type CandidateRecord = {
  id: string;
  label: string;
  kindLabel: string;
  text: string;
  origin: string;
  selectedByLabel: string;
  savedAtLabel: string;
  source: CandidateRecordSource;
  decisions: readonly CandidateRecordDecision[];
};

export type CandidateDecisionReceipt = {
  candidateId: string;
  revisionId: string;
  actorLabel: string;
  judgment: CandidateJudgment;
  reason: string;
  savedAtLabel: string;
  current: boolean;
};

export type CandidateDecisionDraft = {
  candidateId: string | null;
  actorLabel: string;
  headLabel: string;
  judgment: CandidateJudgment | null;
  reason: string;
  status: CandidateDecisionStatus;
  blockedReason: string | null;
  detail?: string;
  receipt?: CandidateDecisionReceipt | null;
};

export type CandidateRecordsPanelProps = {
  compact: boolean;
  records: readonly CandidateRecord[];
  selectedCandidateId: string | null;
  onSelectCandidate: (id: string) => void;
  listStatus: CandidateRecordsListStatus;
  listCurrent: boolean;
  listDetail?: string;
  decision: CandidateDecisionDraft;
  onJudgmentChange: (judgment: CandidateJudgment) => void;
  onReasonChange: (value: string) => void;
  onSave?: () => void;
  onRefresh?: () => void;
  onCancel?: () => void;
};

const judgmentActions: readonly { judgment: CandidateJudgment; label: string }[] = [
  { judgment: 'confirmed', label: '确认该候选' },
  { judgment: 'rejected', label: '不支持该候选' },
  { judgment: 'undecided', label: '暂无法判断' },
];

// Fixed display labels: an undecided record is never worded as confirmed.
const judgmentLabels: Record<CandidateJudgment, string> = {
  confirmed: '人工确认该候选',
  rejected: '不支持该候选',
  undecided: '暂无法判断',
};

// Explicit three-value gate: an unexpected runtime value never enables saving
// or a positive receipt, even when it is not null.
function isCandidateJudgment(value: unknown): value is CandidateJudgment {
  return value === 'confirmed' || value === 'rejected' || value === 'undecided';
}

// Local full-text region for a single record field; it is deliberately not an
// outer wrapper around the panel, so the parent keeps its own page scrolling.
function FullText({ text, testID }: { text: string; testID: string }) {
  return <View testID={testID} style={{ minWidth: 0 }}>
    <ScrollView style={{ maxHeight: 240, minWidth: 0 }}><Copy>{text}</Copy></ScrollView>
  </View>;
}

export function CandidateRecordsPanel({ compact, records, selectedCandidateId, onSelectCandidate, listStatus, listCurrent, listDetail, decision, onJudgmentChange, onReasonChange, onSave, onRefresh, onCancel }: CandidateRecordsPanelProps) {
  const theme = useTheme();
  const gap = compact ? 8 : 12;
  const sectionStyle = { gap: 6, minWidth: 0, borderTopWidth: 1, borderColor: theme.line, paddingTop: gap };
  const locked = decision.status === 'saving' || decision.status === 'unknown';
  const cancelLabel = locked ? '关闭表单' : '取消';
  const receipt = decision.receipt ?? null;
  const selected = selectedCandidateId === null ? null : records.find(record => record.id === selectedCandidateId) ?? null;
  const selectionMissing = selectedCandidateId !== null && selected === null;
  const selectedName = selected === null ? '未选择候选' : selected.label;
  const listDetailSuffix = listDetail !== undefined && listDetail !== '' ? `：${listDetail}` : '';
  const decisionDetailSuffix = decision.detail !== undefined && decision.detail !== '' ? `：${decision.detail}` : '';
  const listReady = listStatus === 'ready' && listCurrent;
  // A non-null blocked reason always blocks, even when blank; a blank parent
  // reason gets a concrete generic explanation instead of being ignored.
  const blockedDecisionReason = decision.blockedReason === null ? null
    : decision.blockedReason.trim().length === 0 ? '当前暂不能保存人工判断' : decision.blockedReason;
  const canRefresh = onRefresh !== undefined && listStatus !== 'loading';
  const judgmentLegal = isCandidateJudgment(decision.judgment);
  // Missing or stale snapshots stay readable, but cannot anchor a new
  // decision; both the gate and the nearby explanation use the same checks.
  const canSave = listReady
    && selected !== null && decision.candidateId === selected.id
    && judgmentLegal && decision.reason.trim().length > 0
    && decision.blockedReason === null
    && onSave !== undefined
    && (decision.status === 'idle' || decision.status === 'error');

  let disabledReason: string | null = null;
  if (blockedDecisionReason !== null) disabledReason = blockedDecisionReason;
  else if (listStatus === 'error') disabledReason = '候选列表读取失败，暂不能保存人工判断';
  else if (listStatus !== 'ready') disabledReason = '候选列表尚未读取完成，暂不能保存人工判断';
  else if (!listCurrent) disabledReason = '候选列表不是当前快照，暂不能保存人工判断';
  else if (selectedCandidateId === null) disabledReason = '请选择候选';
  else if (selected === null) disabledReason = '所选候选不在当前列表中';
  else if (decision.candidateId !== selected.id) disabledReason = decision.candidateId === null ? '判断草稿尚未绑定候选' : '判断草稿与当前所选候选不一致';
  else if (!judgmentLegal) disabledReason = decision.judgment === null ? '请选择判断结论' : '判断结论值无效，暂不能保存人工判断';
  else if (decision.reason.trim().length === 0) disabledReason = '判断理由不能为空';
  else if (onSave === undefined) disabledReason = '保存尚未接入';

  const showDisabledReason = !canSave && disabledReason !== null && (decision.status === 'idle' || decision.status === 'error');
  // Only a receipt the parent marks current and that binds the current
  // selection, decision content and actor with a non-blank revision id may
  // display a positive save. Content and actor are compared as original
  // values, without trimming the reason or labels.
  const receiptMatches = receipt !== null && selected !== null
    && receipt.current === true
    && judgmentLegal
    && decision.candidateId === selected.id
    && receipt.candidateId === selected.id
    && receipt.candidateId === decision.candidateId
    && receipt.revisionId.trim().length > 0
    && receipt.judgment === decision.judgment
    && receipt.reason === decision.reason
    && receipt.actorLabel === decision.actorLabel;
  const receiptText = decision.status === 'saved'
    ? receiptMatches && receipt !== null
      ? `人工判断已保存：${receipt.actorLabel} · ${judgmentLabels[receipt.judgment]} · ${receipt.savedAtLabel} · 修订 ${receipt.revisionId}`
      : '保存回执未核对或不属于当前候选，不能显示为已保存'
    : decision.status === 'unknown'
      ? '尚无匹配当前候选的已核对回执，不能显示为已保存'
      : receipt !== null
        ? '已有保存回执，但父层状态不是已保存，不能显示为保存成功'
        : '尚无保存回执';
  const decisionStatusText = decision.status === 'saving' ? '正在保存人工判断…'
    : decision.status === 'error' ? `保存失败${decisionDetailSuffix}`
      : decision.status === 'unknown' ? `保存结果未知，请先核对，不自动重试${decisionDetailSuffix}` : null;
  const listStatusText = listStatus === 'idle' ? '尚未读取本地候选列表'
    : listStatus === 'loading' ? '正在读取候选列表…'
      : listStatus === 'error' ? `读取候选列表失败${listDetailSuffix}` : null;
  const listStale = records.length > 0 && (listStatus !== 'ready' || !listCurrent);
  const listEmpty = listStatus === 'ready' && listCurrent && records.length === 0;

  return <View testID="candidate-records-panel" style={{ width: '100%', maxWidth: 820, alignSelf: 'center', minWidth: 0, gap }}>
    <View style={{ gap: 6, minWidth: 0 }}>
      <View style={s.row}>
        <Copy small style={{ fontWeight: '600' }}>已保存候选</Copy>
        <Button label={listStatus === 'loading' ? '读取中…' : '刷新候选列表'} testID="candidate-records-refresh"
          accessibilityLabel="刷新候选列表" disabled={!canRefresh}
          onPress={() => { if (canRefresh) onRefresh?.(); }} />
        {onRefresh === undefined && <Copy small muted>刷新尚未接入</Copy>}
      </View>
      {listStatusText !== null && <Copy small muted>{listStatusText}</Copy>}
      {listStatus !== 'error' && listDetail !== undefined && listDetail !== '' && <Copy small muted style={{ flexShrink: 1, minWidth: 0 }}>{listDetail}</Copy>}
      {listStale && <Copy small muted>先前读取的候选快照</Copy>}
      {listEmpty && <Copy small muted>尚无已保存候选</Copy>}
      {!listEmpty && records.length > 0 && <View style={s.row}>{records.map(record => <View key={record.id} style={{ maxWidth: '100%', minWidth: 0 }}>
        <Button label={record.label} selected={selectedCandidateId === record.id} testID={`candidate-record-${record.id}`}
          accessibilityLabel={`选择候选 ${record.label}`}
          onPress={() => onSelectCandidate(record.id)} />
      </View>)}</View>}
    </View>

    {selectionMissing
      ? <View style={sectionStyle}><Copy small muted>所选候选不在当前列表中，请重新选择</Copy></View>
      : selected !== null && <>
        <View style={sectionStyle}>
          <View style={s.row}>
            <Copy small style={{ fontWeight: '600', flexShrink: 1, minWidth: 0 }}>{selected.label}</Copy>
            <Copy small muted>{selected.kindLabel}</Copy>
          </View>
          <Copy small muted>模型保存的候选原文，不可在此修改</Copy>
          <FullText text={selected.text} testID="candidate-record-text" />
          <Copy small muted style={{ flexShrink: 1, minWidth: 0 }}>{`记录来源：${selected.origin}`}</Copy>
          <Copy small muted style={{ flexShrink: 1, minWidth: 0 }}>{`选入人：${selected.selectedByLabel}`}</Copy>
          <Copy small muted style={{ flexShrink: 1, minWidth: 0 }}>{`保存时间：${selected.savedAtLabel}`}</Copy>
        </View>

        <View style={sectionStyle}>
          <Copy small style={{ fontWeight: '600' }}>保存时来源原文</Copy>
          <Copy small muted style={{ flexShrink: 1, minWidth: 0 }}>{`来源范围：${selected.source.scopeLabel}`}</Copy>
          <Copy small muted testID="candidate-record-source-hash" style={[s.mono, { flexShrink: 1, minWidth: 0 }]}>{`SHA-256：${selected.source.sha256}`}</Copy>
          <FullText text={selected.source.text} testID="candidate-record-source-text" />
          <Copy small muted>来源 Agent 是否仍存在不影响阅读与人工判断</Copy>
        </View>

        <View style={sectionStyle}>
          <Copy small style={{ fontWeight: '600' }}>{`已有人工判断（${selected.decisions.length}）`}</Copy>
          {selected.decisions.length === 0
            ? <Copy small muted>该候选尚无人工判断</Copy>
            : selected.decisions.map(item => <View key={item.id} style={{ gap: 4, minWidth: 0, borderTopWidth: 1, borderColor: theme.line, paddingTop: 8 }}>
              <View style={s.row}>
                <Copy small style={{ fontWeight: '600' }}>{judgmentLabels[item.judgment]}</Copy>
                <Copy small muted style={{ flexShrink: 1, minWidth: 0 }}>{`${item.actorLabel} · ${item.savedAtLabel}`}</Copy>
              </View>
              {item.reason !== '' && <Copy>{item.reason}</Copy>}
            </View>)}
        </View>
      </>}

    <View style={sectionStyle}>
      <Copy small style={{ fontWeight: '600' }}>人工判断</Copy>
      <Copy small muted>人工判断独立记录，不修改模型候选或官方分数</Copy>
      <Copy small muted style={{ flexShrink: 1, minWidth: 0 }}>{`当前操作人：${decision.actorLabel}`}</Copy>
      <Copy small muted style={{ flexShrink: 1, minWidth: 0 }}>{`当前修订基线：${decision.headLabel}`}</Copy>
      <View style={s.row}>
        {judgmentActions.map(action => <Button key={action.judgment} label={action.label} testID={`candidate-judgment-${action.judgment}`}
          selected={decision.judgment === action.judgment} disabled={locked}
          accessibilityLabel={`${selectedName}：${action.label}`}
          onPress={() => onJudgmentChange(action.judgment)} />)}
      </View>
      <Input value={decision.reason} onChange={onReasonChange} multiline label="判断理由" testID="candidate-decision-reason" editable={!locked} />
      <View style={s.row}>
        <Button label="保存人工判断" primary testID="candidate-decision-save" disabled={!canSave}
          accessibilityLabel={`保存人工判断：${selectedName}`}
          onPress={() => { if (canSave) onSave?.(); }} />
        <Button label={cancelLabel} testID="candidate-decision-cancel" disabled={onCancel === undefined}
          accessibilityLabel={`${cancelLabel}：${selectedName}`}
          onPress={() => onCancel?.()} />
      </View>
      {onCancel === undefined && <Copy small muted>取消尚未接入</Copy>}
      {decisionStatusText !== null && <Copy small style={{ color: decision.status === 'error' ? theme.error : theme.muted }}>{decisionStatusText}</Copy>}
      <Copy small testID="candidate-decision-receipt" style={{ color: receiptMatches && decision.status === 'saved' ? theme.text : theme.muted, flexShrink: 1, minWidth: 0 }}>{receiptText}</Copy>
      {showDisabledReason && <Copy small muted style={{ flexShrink: 1, minWidth: 0 }}>{disabledReason}</Copy>}
      {locked && <Copy small muted>切换或关闭不撤销已提交的保存</Copy>}
    </View>
  </View>;
}
