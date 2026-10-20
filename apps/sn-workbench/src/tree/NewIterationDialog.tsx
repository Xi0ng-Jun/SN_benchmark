import React, { useState } from 'react';
import { Modal, ScrollView, Text, View } from 'react-native';
import { api, PlanInput, requestKey, Source, useResource } from '../contracts';
import { Button, Copy, Input, useTheme } from '../ui';
import { CasePicker } from '../plans/CasePicker';
import type { IterationTree } from './contracts';
import { Start, StartPicker } from './StartPicker';

// An iteration fixes its start commit, frozen case list and mode; changing the mode
// means a new iteration (design §11). Creating it provisions root C0 at that commit.
export function NewIterationDialog({ open, inputs, onClose, onCreated }: {
  open: boolean; inputs: Source[]; onClose: () => void; onCreated: (tree: IterationTree) => void;
}) {
  const theme = useTheme();
  const [title, setTitle] = useState('');
  const [start, setStart] = useState<Start | null>(null);
  const [inputId, setInputId] = useState<string | null>(null);
  const [cases, setCases] = useState('');
  const [mode, setMode] = useState<'chunk' | 'reasoning'>('chunk');
  const [key, setKey] = useState(requestKey);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const descriptor = useResource<PlanInput>(open && inputId ? `/inputs/${encodeURIComponent(inputId)}` : null);
  const caseIds = cases.split(/\r?\n/).map(v => v.trim()).filter(Boolean);
  const edit = <T,>(set: (v: T) => void) => (v: T) => { set(v); setKey(requestKey()); };
  const ready = title.trim() && start && descriptor.data && caseIds.length && !busy;
  const create = async () => {
    if (!descriptor.data || !start) return;
    setBusy(true); setError('');
    try {
      const tree = await api<IterationTree>('/iterations', { title: title.trim(), start_commit: start.commit, input_id: descriptor.data.input_id,
        expected_bundle_id: descriptor.data.bundle_id, case_ids: caseIds, mode, request_key: key, seeded_from: start.seeded_from });
      setTitle(''); setStart(null); setCases(''); setKey(requestKey()); onCreated(tree);
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  return <Modal visible={open} transparent animationType="none" onRequestClose={() => !busy && onClose()}>
    <View style={{ flex: 1, backgroundColor: '#0009', alignItems: 'center', justifyContent: 'center', padding: 24 }}>
      <View testID="new-iteration-dialog" style={{ width: '100%', maxWidth: 640, maxHeight: '92%', backgroundColor: theme.raised, borderRadius: 12,
        borderWidth: 1, borderColor: theme.lineStrong, padding: 20, gap: 12 }}>
        <Text style={{ fontSize: 16, fontWeight: '600', color: theme.text }}>新建迭代</Text>
        <Copy small muted>迭代固定起点 commit、冻结题单与 mode；建好后从起点准备根候选 C0。换 mode 要另开迭代。</Copy>
        <ScrollView style={{ flexGrow: 0 }} contentContainerStyle={{ gap: 10 }}>
          <Input label="标题" value={title} onChange={edit(setTitle)} placeholder="例如：QASPER 固定三题 · chunk" testID="iteration-title" />
          <StartPicker open={open} value={start} onChange={edit(setStart)} />
          <View style={{ flexDirection: 'row', gap: 6, flexWrap: 'wrap' }}>
            {inputs.map(input => <Button key={input.id} label={input.title} selected={inputId === input.id}
              onPress={() => { setInputId(input.id); setCases(''); setKey(requestKey()); }} testID={'iteration-input-' + input.id} />)}
            {!inputs.length && <Copy small muted>服务器没有登记评测输入</Copy>}
          </View>
          <View style={{ flexDirection: 'row', gap: 6 }}>
            {(['chunk', 'reasoning'] as const).map(m => <Button key={m} label={m} selected={mode === m} onPress={() => { setMode(m); setKey(requestKey()); }} testID={'iteration-mode-' + m} />)}
          </View>
          {descriptor.error && <Copy small style={{ color: theme.error }}>{descriptor.error}</Copy>}
          {descriptor.data && <CasePicker descriptor={descriptor.data} value={cases} onChange={edit(setCases)} disabled={busy} />}
        </ScrollView>
        {!!error && <Copy small style={{ color: theme.error }}>{error}</Copy>}
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
          <Text style={{ flex: 1, fontSize: 12, color: theme.muted }}>{caseIds.length} 题</Text>
          <Button label="取消" onPress={onClose} disabled={busy} />
          <Button label={busy ? '建立中…' : '建立迭代'} primary onPress={create} disabled={!ready} testID="iteration-create" />
        </View>
      </View>
    </View>
  </Modal>;
}
