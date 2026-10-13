import React from 'react';
import { View } from 'react-native';
import { Button, Copy, Input, s, useTheme } from '../ui';

// Local controlled Agent creation request form. The parent owns the task scope,
// the server-allowed profile list, selection, title, request key, intent id,
// every status/blocked reason, the receipt and checking state. Callbacks are
// synchronous notifications only: this file never fetches, awaits, generates
// ids/hashes/times/request keys, concatenates configuration, auto-selects a
// profile, clears input, retries or infers success. A positive read-back is
// only shown when the receipt is marked current and strictly binds this
// intent, the selected allowed profile, the original title and non-blank
// Agent/session ids; readiness and release are shown as observed facts and do
// not claim future idleness, completed sending, atomic creation, sandboxing or
// key ownership. Trim is used only for blankness gates and explanations.

export type AgentCreationProfile = {
  id: string;
  label: string;
  providerLabel: string;
  modelLabel: string;
  modeLabel: string | null;
  effortLabel: string | null;
};

export type AgentCreationStatus = 'idle' | 'saving' | 'not_attempted' | 'created' | 'unknown' | 'error';

export type AgentCreationReadiness = 'idle' | 'not_idle' | 'archived' | 'incomplete_snapshot';

export type AgentCreationRelease = {
  status: 'ok' | 'unavailable' | 'not_needed';
  code?: string;
};

export type AgentCreationReceipt = {
  current: boolean;
  intentId: string;
  profileId: string;
  submittedTitle: string;
  agentId: string;
  sessionId: string;
  ready: boolean;
  readiness: AgentCreationReadiness;
  agentStatus: string | null;
  release: AgentCreationRelease;
};

export type AgentCreationPanelProps = {
  compact: boolean;
  scopeLabel: string;
  profiles: readonly AgentCreationProfile[];
  selectedProfileId: string | null;
  onProfileSelect: (id: string) => void;
  title: string;
  onTitleChange: (value: string) => void;
  status: AgentCreationStatus;
  blockedReason: string | null;
  detail?: string;
  intentId: string | null;
  receipt: AgentCreationReceipt | null;
  checking: boolean;
  onCreate?: () => void;
  onCheck?: () => void;
  onCancel?: () => void;
};

function orUnspecified(value: string | null): string {
  return value !== null && value.trim() !== '' ? value : '未指定';
}

export function AgentCreationPanel({ compact, scopeLabel, profiles, selectedProfileId, onProfileSelect, title, onTitleChange, status, blockedReason, detail, intentId, receipt, checking, onCreate, onCheck, onCancel }: AgentCreationPanelProps) {
  const theme = useTheme();
  const gap = compact ? 8 : 12;
  const sectionStyle = { gap: 6, minWidth: 0, borderTopWidth: 1, borderColor: theme.line, paddingTop: gap };
  const monoWrap = [s.mono, { flexShrink: 1, minWidth: 0 }];
  const submitted = status === 'saving' || status === 'unknown';
  // In-flight creation, an unresolved receipt and a pending local read all
  // lock profile/title/repeat-create; closing the form stays a parent call.
  const locked = submitted || checking;
  const cancelLabel = locked ? '关闭表单' : '取消';
  const scopeReady = scopeLabel.trim().length > 0;
  // A parent selection only counts when it points at a listed allowed profile;
  // a missing or unknown id is never replaced by a first/last default.
  const selected = selectedProfileId === null ? null : profiles.find(profile => profile.id === selectedProfileId) ?? null;
  const profileReady = selectedProfileId !== null && selectedProfileId.trim().length > 0 && selected !== null;
  const intentReady = intentId !== null && intentId.trim().length > 0;
  const detailSuffix = detail !== undefined && detail !== '' ? `：${detail}` : '';
  // A non-null blocked reason always blocks, even when blank; a blank parent
  // reason gets a concrete generic explanation instead of being ignored.
  const blockedCreateReason = blockedReason === null ? null
    : blockedReason.trim().length === 0 ? '当前暂不能创建 Agent' : blockedReason;
  const canCreate = scopeReady && profileReady && onCreate !== undefined && blockedReason === null && !checking
    && (status === 'idle' || status === 'error' || status === 'not_attempted');
  // Checking is independent of creation and title legitimacy: it only reads
  // the parent-held local intent and never re-dispatches a creation.
  const checkStatusAllowed = status === 'unknown' || status === 'created' || status === 'not_attempted' || status === 'error';
  const canCheck = scopeReady && intentReady && onCheck !== undefined && !checking && checkStatusAllowed;

  let createDisabledReason: string | null = null;
  if (blockedCreateReason !== null) createDisabledReason = blockedCreateReason;
  else if (status === 'saving') createDisabledReason = '正在创建，等待本地记录';
  else if (status === 'unknown') createDisabledReason = '创建结果尚未确认，请核对本地创建记录';
  else if (status === 'created') createDisabledReason = '已有创建记录，不能重复创建';
  else if (checking) createDisabledReason = '正在核对本地创建记录，暂不能创建';
  else if (!scopeReady) createDisabledReason = '缺少任务范围，暂不能创建 Agent';
  else if (selectedProfileId === null) createDisabledReason = '请选择创建配置';
  else if (selected === null) createDisabledReason = '所选配置不在当前允许列表中，请重新选择';
  else if (selectedProfileId.trim().length === 0) createDisabledReason = '所选配置标识无效，暂不能创建 Agent';
  else if (onCreate === undefined) createDisabledReason = '创建尚未接入';
  const showCreateReason = !canCreate && createDisabledReason !== null && !submitted;

  let checkDisabledReason: string | null = null;
  if (onCheck === undefined) checkDisabledReason = '核对尚未接入';
  else if (!scopeReady) checkDisabledReason = '缺少任务范围，暂不能核对';
  else if (!intentReady) checkDisabledReason = '尚无本地创建意图，暂不能核对';
  else if (checking) checkDisabledReason = null;
  else if (status === 'saving') checkDisabledReason = '创建进行中，暂不能核对';
  else if (!checkStatusAllowed) checkDisabledReason = '当前状态暂不能核对';

  const statusText = status === 'saving' ? '正在创建并等待本地记录…'
    : status === 'not_attempted' ? '本次未调用创建，可在条件满足后显式发起新意图'
      : status === 'unknown' ? '创建结果尚未确认，请核对本地创建记录'
        : status === 'error' ? `创建失败${detailSuffix}` : null;
  const showDetail = detail !== undefined && detail !== '' && status !== 'error';
  // Positive read-back requires the parent's current receipt to bind this
  // intent, the selected allowed profile, the original title and non-blank
  // observed ids; content is compared as original values without trimming.
  const receiptMatches = status === 'created' && receipt !== null
    && receipt.current === true
    && intentReady && receipt.intentId === intentId
    && selected !== null && receipt.profileId === selected.id
    && receipt.submittedTitle === title
    && receipt.agentId.trim().length > 0 && receipt.sessionId.trim().length > 0;
  const readinessText = receipt === null ? null
    : receipt.ready === true && receipt.readiness === 'idle' ? '读回时空闲'
      : receipt.readiness === 'not_idle' ? '读回时非空闲'
        : receipt.readiness === 'archived' ? '读回时已归档'
          : receipt.readiness === 'incomplete_snapshot' ? '读回信息不完整'
            : '读回时空闲状态未确认';
  const releaseText = receipt === null ? null
    : receipt.release.status === 'unavailable' ? `连接清理尚未确认${receipt.release.code !== undefined && receipt.release.code !== '' ? `（${receipt.release.code}）` : ''}`
      : receipt.release.status === 'ok' ? '连接清理已完成'
        : '连接清理无需执行';

  return <View testID="agent-creation-panel" style={{ width: '100%', maxWidth: 820, alignSelf: 'center', minWidth: 0, gap }}>
    <View style={{ gap: 6, minWidth: 0 }}>
      <Copy small style={{ fontWeight: '600' }}>创建 Agent</Copy>
      <Copy small muted style={{ flexShrink: 1, minWidth: 0 }}>{`任务范围：${scopeReady ? scopeLabel : '未提供'}`}</Copy>
      <Copy small muted>仅选择服务器允许的配置；本表单不接收工作目录、Agent ID、SDK key 或环境配置</Copy>
    </View>

    <View style={sectionStyle}>
      <Copy small style={{ fontWeight: '600' }}>创建配置</Copy>
      {profiles.length === 0
        ? <Copy small muted>当前任务没有可选的创建配置</Copy>
        : profiles.map(profile => <View key={profile.id} style={{ gap: 4, minWidth: 0, paddingTop: 4 }}>
          <Button label={profile.label} selected={selectedProfileId === profile.id} disabled={locked}
            testID={`agent-creation-profile-${profile.id}`}
            accessibilityLabel={`选择创建配置 ${profile.label}`}
            onPress={() => onProfileSelect(profile.id)} />
          <Copy small muted style={{ flexShrink: 1, minWidth: 0 }}>{`${profile.providerLabel} · ${profile.modelLabel} · 模式：${orUnspecified(profile.modeLabel)} · 思考：${orUnspecified(profile.effortLabel)}`}</Copy>
          <Copy small muted style={monoWrap}>{`配置 ID：${profile.id}`}</Copy>
        </View>)}
    </View>

    <View style={sectionStyle}>
      <Copy small style={{ fontWeight: '600' }}>标题</Copy>
      <Input value={title} onChange={onTitleChange} label="Agent 标题，可选" placeholder="留空则不提交标题"
        testID="agent-creation-title" editable={!locked} />
      <Copy small muted>标题按原值提交；不合法时会阻止创建并说明原因</Copy>
    </View>

    <View style={sectionStyle}>
      <View style={s.row}>
        <Button label="创建空白 Agent" primary testID="agent-creation-create" disabled={!canCreate}
          accessibilityLabel={`创建空白 Agent：${selected === null ? '未选择配置' : selected.label}`}
          onPress={() => { if (canCreate) onCreate?.(); }} />
        <Button label={checking ? '核对中…' : '核对本地创建记录'} testID="agent-creation-check" disabled={!canCheck}
          accessibilityLabel="核对本地创建记录" onPress={() => { if (canCheck) onCheck?.(); }} />
        <Button label={cancelLabel} testID="agent-creation-cancel" disabled={onCancel === undefined}
          accessibilityLabel={`${cancelLabel}：创建 Agent 表单`} onPress={() => onCancel?.()} />
      </View>
      {intentReady && <Copy small muted testID="agent-creation-intent" style={monoWrap}>{`本次创建意图：${intentId}`}</Copy>}
      <Copy small muted>核对只读取本地创建记录，不会重新发起创建</Copy>
      {statusText !== null && <Copy small style={{ color: status === 'error' ? theme.error : theme.muted }}>{statusText}</Copy>}
      {showDetail && <Copy small muted style={{ flexShrink: 1, minWidth: 0 }}>{detail}</Copy>}
      {showCreateReason && <Copy small muted style={{ flexShrink: 1, minWidth: 0 }}>{createDisabledReason}</Copy>}
      {!canCheck && checkDisabledReason !== null && <Copy small muted style={{ flexShrink: 1, minWidth: 0 }}>{checkDisabledReason}</Copy>}
      {onCancel === undefined && <Copy small muted>关闭尚未接入</Copy>}
      {submitted && <Copy small muted>关闭不撤销已经提交的创建</Copy>}
    </View>

    <View style={sectionStyle}>
      <Copy small style={{ fontWeight: '600' }}>本地创建记录</Copy>
      <View testID="agent-creation-receipt" style={{ gap: 4, minWidth: 0 }}>
        {receiptMatches && receipt !== null
          ? <>
            <Copy small>已读回 Agent，并关联当前任务</Copy>
            <Copy small muted style={monoWrap}>{`Agent：${receipt.agentId}`}</Copy>
            <Copy small muted style={monoWrap}>{`会话：${receipt.sessionId}`}</Copy>
            <Copy small muted testID="agent-creation-receipt-ready">{`读回就绪：${receipt.ready === true ? '是' : '否'}`}</Copy>
            {receipt.agentStatus !== null && <Copy small muted style={{ flexShrink: 1, minWidth: 0 }}>{`Agent 状态：${receipt.agentStatus}`}</Copy>}
            {readinessText !== null && <Copy small muted>{readinessText}</Copy>}
            {releaseText !== null && <Copy small muted style={{ flexShrink: 1, minWidth: 0 }}>{releaseText}</Copy>}
            <Copy small muted>读回只表示该次观察，不代表未来空闲、发送完成或创建因果</Copy>
          </>
          : <Copy small muted>当前创建记录尚未核对</Copy>}
      </View>
    </View>
  </View>;
}
