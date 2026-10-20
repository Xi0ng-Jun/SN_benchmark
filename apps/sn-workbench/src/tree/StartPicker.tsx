import React, { useEffect, useState } from 'react';
import { Pressable, ScrollView, Text, TextInput, View } from 'react-native';
import { api, useResource } from '../contracts';
import { Button, Copy, hover, useTheme } from '../ui';
import { decisionLabels, short } from './contracts';
import { Badge, Mono } from './parts';

export type ProjectCommit = { commit: string; subject: string; date: string; refs: string[]; head: boolean };
export type Seed = { iteration_id: string; iteration_title: string; candidate_id: string; root: boolean;
  frozen_commit: string; note: string | null; decision: string | null };
export type Start = { commit: string; summary: string; seeded_from: { iteration_id: string; candidate_id: string } | null };

const when = (iso: string) => new Date(iso).toLocaleString('zh-CN', { hour12: false, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });

function Choice({ selected, onPress, children, testID }: { selected: boolean; onPress: () => void; children: React.ReactNode; testID?: string }) {
  const theme = useTheme();
  return <Pressable accessibilityRole="radio" accessibilityState={{ checked: selected }} onPress={onPress} testID={testID}
    style={hover(hovered => ({ flexDirection: 'row', alignItems: 'center', gap: 8, paddingVertical: 6, paddingHorizontal: 10, borderRadius: 8,
      backgroundColor: selected ? theme.selected : hovered ? theme.surface : 'transparent', borderLeftWidth: 2, borderLeftColor: selected ? theme.primary : 'transparent' }))}>
    {children}
  </Pressable>;
}

// The start commit is chosen from the trusted SN repository (HEAD, branches, tags,
// recent history) or from a frozen candidate to continue from; a short hash, branch
// or tag typed in is resolved by the server. What gets confirmed is the full hash.
export function StartPicker({ open, value, onChange }: { open: boolean; value: Start | null; onChange: (start: Start) => void }) {
  const theme = useTheme();
  const choices = useResource<{ commits: ProjectCommit[]; seeds: Seed[] }>(open ? '/project/commits' : null);
  const [query, setQuery] = useState('');
  const [resolving, setResolving] = useState(false);
  const [error, setError] = useState('');
  const fromCommit = (c: ProjectCommit): Start => ({ commit: c.commit, seeded_from: null,
    summary: [c.head ? '当前 HEAD' : null, c.refs.join(', ') || null, c.subject].filter(Boolean).join(' · ') });
  const fromSeed = (s: Seed): Start => ({ commit: s.frozen_commit, seeded_from: { iteration_id: s.iteration_id, candidate_id: s.candidate_id },
    summary: `从「${s.iteration_title}」的${s.root ? '根候选' : '候选 ' + short(s.candidate_id)}继续${s.decision ? '（' + decisionLabels[s.decision] + '）' : ''}` });
  useEffect(() => { if (!value && choices.data?.commits[0]) onChange(fromCommit(choices.data.commits[0])); }, [choices.data]);
  const resolve = async () => {
    if (!query.trim()) return;
    setResolving(true); setError('');
    try {
      const found = await api<{ commits: ProjectCommit[] }>(`/project/commits?q=${encodeURIComponent(query.trim())}`);
      onChange(fromCommit(found.commits[0])); setQuery('');
    } catch (e) { setError((e as Error).message); } finally { setResolving(false); }
  };
  return <View testID="start-picker" style={{ gap: 8 }}>
    <Text style={{ fontSize: 12, fontWeight: '500', color: theme.secondary }}>起点</Text>
    <View style={{ flexDirection: 'row', gap: 6 }}>
      <TextInput testID="start-query" value={query} onChangeText={setQuery} onSubmitEditing={resolve} placeholder="分支、tag 或短哈希，回车解析"
        placeholderTextColor={theme.muted} style={{ flex: 1, height: 34, borderWidth: 1, borderColor: theme.line, borderRadius: 8, paddingHorizontal: 10,
          color: theme.text, backgroundColor: theme.surface, fontSize: 13, outlineColor: theme.primary, outlineWidth: 1 } as object} />
      <Button label={resolving ? '解析中…' : '解析'} onPress={resolve} disabled={resolving || !query.trim()} testID="start-resolve" />
    </View>
    {!!error && <Copy small style={{ color: theme.error }}>{error}</Copy>}
    <ScrollView style={{ maxHeight: 220, borderWidth: 1, borderColor: theme.line, borderRadius: 8 }} contentContainerStyle={{ padding: 4 }}>
      {choices.error && <Copy small style={{ color: theme.error }}>{choices.error}</Copy>}
      {!choices.data && !choices.error && <Copy small muted>正在读取 SN 仓库…</Copy>}
      {!!choices.data?.seeds.length && <Text style={{ fontSize: 11, color: theme.muted, paddingHorizontal: 10, paddingTop: 6, paddingBottom: 2 }}>从已有候选继续（记录来源，分数不继承）</Text>}
      {choices.data?.seeds.map(s => <Choice key={s.candidate_id} selected={value?.seeded_from?.candidate_id === s.candidate_id} onPress={() => onChange(fromSeed(s))} testID={'start-seed-' + s.candidate_id}>
        <Mono size={12}>{short(s.frozen_commit, 7)}</Mono>
        <Text numberOfLines={1} style={{ flex: 1, fontSize: 12, color: theme.text }}>{s.iteration_title} · {s.root ? 'C0' : short(s.candidate_id)}{s.note ? ' · ' + s.note : ''}</Text>
        {s.decision && <Badge label={decisionLabels[s.decision]} tone={s.decision === 'adopted' ? 'primary' : undefined} />}
      </Choice>)}
      {!!choices.data?.commits.length && <Text style={{ fontSize: 11, color: theme.muted, paddingHorizontal: 10, paddingTop: 6, paddingBottom: 2 }}>SN 仓库</Text>}
      {choices.data?.commits.map(c => <Choice key={c.commit} selected={!value?.seeded_from && value?.commit === c.commit} onPress={() => onChange(fromCommit(c))} testID={'start-commit-' + c.commit.slice(0, 12)}>
        <Mono size={12}>{c.commit.slice(0, 7)}</Mono>
        {c.head && <Badge label="HEAD" tone="primary" />}
        {c.refs.slice(0, 2).map(r => <Badge key={r} label={r.replace('tag: ', '')} />)}
        <Text numberOfLines={1} style={{ flex: 1, fontSize: 12, color: theme.text }}>{c.subject}</Text>
        <Text style={{ fontSize: 11, color: theme.muted }}>{when(c.date)}</Text>
      </Choice>)}
    </ScrollView>
    {value && <View testID="start-selected" style={{ gap: 2 }}>
      <Mono size={12}>{value.commit}</Mono>
      <Copy small muted>{value.summary}</Copy>
    </View>}
  </View>;
}
