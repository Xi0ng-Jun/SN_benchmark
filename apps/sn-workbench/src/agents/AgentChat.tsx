import React, { useState } from 'react';
import { Pressable, Text, View } from 'react-native';
import { ChevronDown, ChevronRight, Wrench } from 'lucide-react-native';
import { Button, Copy, useTheme } from '../ui';
import type { SessionEntry, SessionPage, SessionRemote } from './chatTypes';

export type CandidateKind = 'finding' | 'direction' | 'next_step';
// `before`: entry ids already on the page when it was sent, so the bubble sits
// before the reply even when the echoed user entry is outside the read page.
export type Outgoing = { text: string; attachments: number; state: 'sending' | 'sent'; before: readonly string[] };

const candidateActions: readonly { kind: CandidateKind; label: string }[] = [
  { kind: 'finding', label: '发现' }, { kind: 'direction', label: '方向' }, { kind: 'next_step', label: '下一步' },
];

// Workbench messages reach the Agent as {"message", "context": [...]} JSON.
// Show the typed text and the attached titles instead of the raw body.
function userText(text: string | null): { text: string; attached: string[] } {
  if (text) try {
    const body = JSON.parse(text);
    if (body && typeof body.message === 'string' && Array.isArray(body.context))
      return { text: body.message, attached: body.context.map((item: { title?: unknown }) => typeof item?.title === 'string' ? item.title : '材料') };
  } catch {
    // Long bodies arrive truncated by the bridge: recover the typed text and titles.
    const head = /^\{"message":"((?:[^"\\]|\\.)*)","context":\[/.exec(text);
    if (head) {
      const titles = [...text.matchAll(/"title":"((?:[^"\\]|\\.)*)"/g)].map(m => JSON.parse(`"${m[1]}"`) as string);
      return { text: JSON.parse(`"${head[1]}"`) as string, attached: titles.length ? titles : ['材料（正文已截断）'] };
    }
  }
  return { text: text ?? '', attached: [] };
}

// The bridge projects only a tool name or a fixed English title per activity
// (no arguments or error text), so these labels are all the page can show.
const fixedActivities: Record<string, string> = {
  reasoning: '思考', todo: '任务清单', error: '出错（详情只在 Paseo 中可见）', notification: '通知',
  compaction: '上下文压缩', plugin: '插件活动',
};
const activityLabel = (entry: SessionEntry) => {
  const type = entry.activityType ?? '';
  if (fixedActivities[type]) return fixedActivities[type];
  if (type === 'tool_call') return '工具 · ' + (entry.text ?? '未知');
  return [type && type !== 'activity' ? type : null, entry.text].filter(Boolean).join(' · ') || '活动';
};

type Group = { kind: 'message'; entry: SessionEntry } | { kind: 'activity'; id: string; entries: SessionEntry[] };
function group(entries: readonly SessionEntry[]): Group[] {
  const groups: Group[] = [];
  for (const entry of entries) {
    const last = groups[groups.length - 1];
    if (entry.kind === 'activity' && last?.kind === 'activity') last.entries.push(entry);
    else groups.push(entry.kind === 'activity' ? { kind: 'activity', id: entry.id, entries: [entry] } : { kind: 'message', entry });
  }
  return groups;
}

// Tool calls and other activity collapse to one line per run, like Paseo.
function ActivityGroup({ entries }: { entries: SessionEntry[] }) {
  const theme = useTheme();
  const [open, setOpen] = useState(false);
  const Chevron = open ? ChevronDown : ChevronRight;
  const last = entries[entries.length - 1];
  return <View style={{ gap: 4 }}>
    <Pressable accessibilityRole="button" accessibilityState={{ expanded: open }} onPress={() => setOpen(o => !o)}
      style={{ flexDirection: 'row', alignItems: 'center', gap: 6, paddingVertical: 2 }}>
      <Wrench size={12} strokeWidth={1.6} color={theme.muted} />
      <Text numberOfLines={1} style={{ flexShrink: 1, fontSize: 12, color: theme.muted }}>
        {entries.length > 1 ? `${entries.length} 项活动 · 最近：` : ''}{activityLabel(last)}</Text>
      <Chevron size={12} color={theme.muted} />
    </Pressable>
    {open && <View style={{ paddingLeft: 18, gap: 2 }}>
      {entries.map(entry => <Text key={entry.id} selectable style={{ fontSize: 12, lineHeight: 18, color: theme.muted }}>
        {activityLabel(entry)}</Text>)}
    </View>}
  </View>;
}

function UserBubble({ text, note, testID }: { text: string; note?: string; testID?: string }) {
  const theme = useTheme();
  return <View testID={testID} style={{ alignSelf: 'flex-end', maxWidth: '85%', gap: 2, alignItems: 'flex-end' }}>
    <View style={{ backgroundColor: theme.selected, borderRadius: 12, paddingHorizontal: 14, paddingVertical: 9 }}>
      <Text selectable style={{ fontSize: 14, lineHeight: 22, color: theme.text }}>{text}</Text>
    </View>
    {note && <Text style={{ fontSize: 11, color: theme.muted }}>{note}</Text>}
  </View>;
}

function AssistantMessage({ entry, candidateReason, onCandidate }: {
  entry: SessionEntry; candidateReason: string | null; onCandidate?: (kind: CandidateKind) => void;
}) {
  const theme = useTheme();
  const [open, setOpen] = useState(false);
  const [hovered, setHovered] = useState(false);
  return <View testID="chat-assistant" style={{ gap: 4 }} onPointerEnter={() => setHovered(true)} onPointerLeave={() => setHovered(false)}>
    {entry.text !== null ? <Copy>{entry.text}</Copy> : <Copy muted>此条目没有可显示正文</Copy>}
    {entry.truncated && <Copy small muted>正文已截断</Copy>}
    {onCandidate && <View style={{ flexDirection: 'row', alignItems: 'center', gap: 2, marginLeft: -8, flexWrap: 'wrap', minHeight: 32,
      opacity: hovered || open ? 1 : 0 }}>
      <Button label={open ? '收起' : '记下'} onPress={() => setOpen(o => !o)} testID={`chat-candidate-${entry.id}`} />
      {open && candidateActions.map(action => <Button key={action.kind} label={action.label} disabled={candidateReason !== null}
        accessibilityLabel={`记为${action.label}`} onPress={() => { if (candidateReason === null) onCandidate(action.kind); }} />)}
      {open && candidateReason !== null && <Text style={{ fontSize: 11, color: theme.muted }}>{candidateReason}</Text>}
    </View>}
  </View>;
}

export type AgentChatProps = {
  hasSession: boolean;
  remote: SessionRemote;
  page: SessionPage;
  viewingTail: boolean;
  outgoing: Outgoing | null;
  running: boolean;
  onOlder?: () => void;
  onLatest?: () => void;
  candidateReason: (entry: SessionEntry) => string | null;
  onCandidate?: (entryId: string, kind: CandidateKind) => void;
  empty: React.ReactNode;
};

/** Conversation with the selected Agent: user bubbles, assistant text, folded activity. */
export function AgentChat({ hasSession, remote, page, viewingTail, outgoing, running, onOlder, onLatest, candidateReason, onCandidate, empty }: AgentChatProps) {
  const theme = useTheme();
  if (!hasSession) return <View testID="chat-empty">{empty}</View>;
  // The sent text stays visible until the read page contains it.
  const echoed = outgoing && page.entries.some(entry => entry.kind === 'user' && userText(entry.text).text === outgoing.text);
  const unavailable = remote.status === 'missing' || remote.status === 'unavailable';
  return <View testID="agent-chat" style={{ gap: 16 }}>
    {(page.hasOlder || !viewingTail) && <View style={{ flexDirection: 'row', justifyContent: 'center', gap: 8 }}>
      {page.hasOlder && onOlder && <Button label="更早的消息" onPress={onOlder} testID="chat-older" />}
      {!viewingTail && onLatest && <Button label="回到最新" onPress={onLatest} testID="chat-latest" />}
    </View>}
    {page.status === 'loading' && !page.entries.length && <Copy small muted>正在读取对话…</Copy>}
    {page.status === 'error' && <Copy small style={{ color: theme.error }}>{`对话读取失败：${page.detail ?? '未知原因'}`}</Copy>}
    {unavailable && <Copy small style={{ color: theme.error }} testID="chat-agent-unavailable">
      {remote.status === 'missing' ? 'Paseo 中找不到这个 Agent，可能已删除；请新建或换一个 Agent'
        : `Agent 暂不可用：${remote.detail ?? '未知原因'}`}</Copy>}
    {page.status === 'ready' && !page.entries.length && !outgoing && <Copy small muted>还没有对话。在下方输入第一条消息。</Copy>}
    {(() => {
      const known = new Set(outgoing?.before ?? []);
      const firstNew = outgoing && !echoed ? page.entries.findIndex(entry => !known.has(entry.id)) : -1;
      const pending = outgoing && !echoed ? <UserBubble key="outgoing" text={outgoing.text} testID="chat-outgoing"
        note={(outgoing.state === 'sending' ? '发送中…' : '已送达 Agent') + (outgoing.attachments ? ` · 附 ${outgoing.attachments} 份材料` : '')} /> : null;
      const items = group(page.entries.slice(0, firstNew < 0 ? undefined : firstNew)).map(render);
      return <>{items}{pending}{firstNew >= 0 && group(page.entries.slice(firstNew)).map(render)}</>;
    })()}
    {running && viewingTail && <Copy small muted testID="chat-running">Agent 正在处理…</Copy>}
  </View>;

  function render(item: Group) {
    return item.kind === 'activity'
      ? <ActivityGroup key={item.id} entries={item.entries} />
      : item.entry.kind === 'user'
        ? (({ text, attached }) => <UserBubble key={item.entry.id} text={text} testID="chat-user"
          note={attached.length ? '附材料：' + attached.join('、') : undefined} />)(userText(item.entry.text))
        : <AssistantMessage key={item.entry.id} entry={item.entry} candidateReason={candidateReason(item.entry)}
          onCandidate={onCandidate ? kind => onCandidate(item.entry.id, kind) : undefined} />;
  }
}
