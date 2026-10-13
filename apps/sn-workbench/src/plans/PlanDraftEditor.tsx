import React, { useState } from 'react';
import { View } from 'react-native';
import { Button, Copy, Fold, Input, s, useTheme } from '../ui';
import type { PlanDraftController } from './usePlanDrafts';
import { CasePicker } from './CasePicker';

export type PlanDraftEditorProps = { controller: PlanDraftController };

/** A single controlled body. All transport, bindings and synchronous locks
 * belong to the controller; this component creates no second editable draft. */
export function PlanDraftEditor({ controller: c }: PlanDraftEditorProps) {
  const theme = useTheme();
  const [paste, setPaste] = useState(false);
  const status = c.status === 'saved' ? '已保存草稿'
    : c.status === 'saving' ? '正在保存草稿，原命令已冻结'
      : c.status === 'checking' ? '正在核对上次保存是否成功'
        : c.status === 'unknown' ? '上次保存结果未知，可以核对或用同一内容重试'
          : c.status === 'rejected' ? '本次命令被拒绝，正文可修改' : '草稿编辑中，尚未保存本次修改';
  return <View testID="plans-panel" style={[s.card, { borderColor: theme.line, backgroundColor: theme.surface, minWidth: 0 }]}>
    <View style={s.row}>
      <Copy style={{ fontWeight: '600', flex: 1 }}>评测计划草稿</Copy>
      <Button label="新建草稿" testID="plan-new" disabled={c.locked} onPress={c.newDraft} />
    </View>
    <Copy testID="plan-status">{status}</Copy>
    <Copy small muted>{c.binding.kind === 'new' ? '新计划。'
      : c.binding.kind === 'fixed' ? '正在基于一个旧版本修改：保存时若已有更新版本会提示冲突。'
        : '正在继续编辑最新版本。'}{c.binding.planId !== null ? ` 计划 ${c.binding.planId.slice(0, 8)}` : ''}</Copy>
    <Copy small muted>这里只定“考哪些题、用哪种模式”。源码、模型和评分器在“更改”页运行前才固定，运行状态也在“更改”页看。</Copy>
    <Copy small>标题</Copy>
    <Input testID="plan-title" label="草稿标题" value={c.draft.title} editable={!c.locked} onChange={c.setTitle} placeholder="例如：QASPER 20 篇基线" />
    <Copy small>题库（选一个）</Copy>
    {c.inputs.length === 0 ? <Copy small muted>服务器没有登记可用题库。</Copy> : <View style={s.row}>
      {c.inputs.map(input => <Button key={input.id} testID={'input-' + input.id} label={input.title}
        selected={c.draft.inputId === input.id} disabled={c.locked} onPress={() => c.selectInput(input.id)} />)}
    </View>}
    {c.draft.inputId === null && <Copy small muted>尚未选择题库。</Copy>}
    {c.inputLoading && <Copy small muted>正在读取题库…</Copy>}
    {c.descriptor && <>
      <Copy small muted>{c.descriptor.suite} · {c.descriptor.case_ids.length} 题 / {c.descriptor.partitions.length} 分区</Copy>
      <Fold title="题库身份" value={{ input_id: c.descriptor.input_id, adapter: c.descriptor.adapter,
        bundle_id: c.descriptor.bundle_id, suite: c.descriptor.suite, source_revision: c.descriptor.source_revision, adaptation_revision: c.descriptor.adaptation_revision }} />
    </>}
    <View style={s.row}>
      <Copy small style={{ flex: 1 }}>选题</Copy>
      <Button label={paste ? '回到选题器' : '粘贴 ID 列表'} onPress={() => setPaste(v => !v)} testID="plan-paste-toggle" />
    </View>
    {c.descriptor && !paste ? <CasePicker descriptor={c.descriptor} value={c.draft.caseIdsText} onChange={c.setCaseIdsText} disabled={c.locked} />
      : <Input testID="plan-case-ids" label="显式 case IDs，一行一个" multiline editable={!c.locked}
        value={c.draft.caseIdsText} onChange={c.setCaseIdsText} placeholder={c.descriptor ? 'case-id-1\ncase-id-2' : '先选择题库再用选题器，或直接粘贴 case IDs'} />}
    <View style={s.row}>
      <Copy small>Notebook 模式</Copy>
      <Button label="chunk" selected={c.draft.mode === 'chunk'} disabled={c.locked} onPress={() => c.setMode('chunk')} />
      <Button label="reasoning" selected={c.draft.mode === 'reasoning'} disabled={c.locked} onPress={() => c.setMode('reasoning')} />
    </View>
    {c.error && <Copy small style={{ color: theme.error }}>{c.error}</Copy>}
    {!c.error && !c.locked && c.status !== 'saved' && c.blockedReason && <Copy small muted>{c.blockedReason}</Copy>}
    <View style={s.row}>
      <Button testID="plan-save" label="保存草稿" primary disabled={!c.canSave} onPress={() => { void c.save(); }} />
      {c.canRecover && <Button testID="plan-check" label="核对上次保存" onPress={() => { void c.checkReceipt(); }} />}
      {c.canRecover && <Button testID="plan-retry" label="用同一内容重试" onPress={() => { void c.retrySave(); }} />}
    </View>
    {c.canRecover && <Copy small muted>重试使用同一请求标识，服务器不会因此保存两份。</Copy>}
  </View>;
}
