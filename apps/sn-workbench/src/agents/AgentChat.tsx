import React, { useState } from 'react';
import { Pressable, ScrollView, Text, View } from 'react-native';
import { Bell, Bot, Brain, ChevronDown, ChevronRight, CircleAlert, Eye, FilePlus, Globe, ListChecks, NotebookPen, Pencil, Search,
  Shrink, Square, SquareCheck, SquareTerminal, Wrench } from 'lucide-react-native';
import type { LucideIcon } from 'lucide-react-native';
import { Button, Copy, s, useTheme } from '../ui';
import type { TimelineText } from '../contracts';
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

type Field = TimelineText | number | boolean | null | undefined;
const isText = (value: Field): value is TimelineText => typeof value === 'object' && value !== null;
const firstLine = (text: string) => text.split('\n').find(line => line.trim())?.trim() ?? '';
const truncNote = (value: { truncated: boolean; originalLength?: number }) =>
  value.truncated ? `已截断${value.originalLength ? `，原长 ${value.originalLength} 字符` : ''}` : null;

const toolIcons: Record<string, LucideIcon> = {
  shell: SquareTerminal, worktree_setup: SquareTerminal, read: Eye, edit: Pencil, write: FilePlus, search: Search,
  fetch: Globe, sub_agent: Bot, plan: NotebookPen,
};
const fieldLabels: Record<string, string> = {
  command: '命令', cwd: '目录', output: '输出', exitCode: '退出码', filePath: '文件', content: '内容', offset: '起始行',
  limit: '行数', oldString: '替换前', newString: '替换后', unifiedDiff: '改动', query: '查询', toolName: '工具', mode: '模式',
  truncated: '结果已截断', numFiles: '文件数', numMatches: '匹配数', filePaths: '文件列表', webResults: '网页结果',
  annotations: '注释', url: '地址', prompt: '提示', result: '结果', code: '状态码', codeText: '状态', worktreePath: '工作树',
  branchName: '分支', log: '日志', subAgentType: '子 Agent', description: '描述', actions: '动作', label: '标签', text: '正文',
  input: '输入',
};

// One-line summary like Paseo: `$ command`, the file path, query or URL.
function toolSummary(entry: SessionEntry): string {
  const fields = entry.detail?.fields ?? {};
  const text = (name: string) => { const value = fields[name]; return isText(value) ? value.text : ''; };
  switch (entry.detail?.kind) {
    case 'shell': return text('command') && '$ ' + firstLine(text('command'));
    case 'read': case 'write': case 'edit': return text('filePath');
    case 'search': return text('query');
    case 'fetch': return text('url');
    case 'sub_agent': return text('description') || text('subAgentType');
    case 'plain_text': return text('label') || firstLine(text('text'));
    case 'plan': return firstLine(text('text'));
    case 'worktree_setup': return text('branchName');
    default: return '';
  }
}

// Monospace output capped at ~300px, scrolling inside like Paseo's tool panes.
function CodeBlock({ value, color, diff = false }: { value: TimelineText; color?: string; diff?: boolean }) {
  const theme = useTheme();
  const note = truncNote(value);
  const lineColor = (line: string) => !diff ? color ?? theme.secondary
    : line.startsWith('+') && !line.startsWith('+++') ? theme.add : line.startsWith('-') && !line.startsWith('---') ? theme.del
      : line.startsWith('@@') ? theme.primary : theme.secondary;
  return <View style={{ gap: 2 }}>
    <ScrollView nestedScrollEnabled style={{ maxHeight: 300, borderWidth: 1, borderColor: theme.line, borderRadius: 6,
      backgroundColor: theme.surface }} contentContainerStyle={{ padding: 8 }}>
      {diff ? value.text.split('\n').map((line, i) => <Text key={i} selectable style={[s.mono, { color: lineColor(line) }]}>{line || ' '}</Text>)
        : <Text selectable style={[s.mono, { color: lineColor('') }]}>{value.text || ' '}</Text>}
    </ScrollView>
    {note && <Text style={{ fontSize: 11, color: theme.muted }}>{note}</Text>}
  </View>;
}

function ToolBody({ entry }: { entry: SessionEntry }) {
  const theme = useTheme();
  const fields = Object.entries(entry.detail?.fields ?? {}).filter(([, value]) => value !== null);
  return <View style={{ gap: 6 }}>
    {fields.map(([name, value]) => {
      const label = fieldLabels[name] ?? name;
      // Short single-line values sit inline; anything longer gets a scrolling block.
      if (!isText(value) || (!value.text.includes('\n') && value.text.length < 200 && !value.truncated))
        return <Text key={name} selectable style={{ fontSize: 12, lineHeight: 18, color: theme.muted }}>{label + '  '}
          <Text style={[s.mono, { color: theme.secondary }]}>
            {isText(value) ? (name === 'command' ? '$ ' : '') + value.text : typeof value === 'boolean' ? (value ? '是' : '否') : String(value)}</Text>
        </Text>;
      return <View key={name} style={{ gap: 2 }}>
        <Text style={{ fontSize: 11, color: theme.muted }}>{label}</Text>
        <CodeBlock value={value} diff={name === 'unifiedDiff'} />
      </View>;
    })}
    {entry.error && <View style={{ gap: 2 }}>
      <Text style={{ fontSize: 11, color: theme.error }}>错误</Text>
      <CodeBlock value={entry.error} color={theme.error} />
    </View>}
    {!fields.length && !entry.error && <Text style={{ fontSize: 12, color: theme.muted }}>没有可显示的详情</Text>}
  </View>;
}

const toolStatus: Record<string, { label: string; tone: 'running' | 'error' | 'muted' }> = {
  running: { label: '运行中…', tone: 'running' }, failed: { label: '失败', tone: 'error' }, canceled: { label: '已取消', tone: 'muted' },
};

// A row header: icon, name, one-line summary, status; expands to `body`.
function Row({ icon: Icon, name, summary, mono = false, status, color, body, testID }: {
  icon: LucideIcon; name: string; summary?: string; mono?: boolean; status?: { label: string; color: string } | null;
  color?: string; body?: React.ReactNode; testID?: string;
}) {
  const theme = useTheme();
  const [open, setOpen] = useState(false);
  const Chevron = open ? ChevronDown : ChevronRight;
  const head = <>
    <Icon size={12} strokeWidth={1.6} color={color ?? theme.muted} />
    <Text style={{ flexShrink: 0, fontSize: 12, fontWeight: '500', color: color ?? theme.secondary }}>{name}</Text>
    {summary ? <Text numberOfLines={1} style={[mono ? s.mono : { fontSize: 12 }, { flexShrink: 1, color: theme.muted }]}>{summary}</Text> : null}
    {status && <Text style={{ fontSize: 12, color: status.color }}>{status.label}</Text>}
    {body ? <Chevron size={12} color={theme.muted} /> : null}
  </>;
  const rowStyle = { flexDirection: 'row' as const, alignItems: 'center' as const, gap: 6, paddingVertical: 2 };
  return <View testID={testID} style={{ gap: 4 }}>
    {body ? <Pressable accessibilityRole="button" accessibilityState={{ expanded: open }} onPress={() => setOpen(o => !o)} style={rowStyle}>
      {head}</Pressable> : <View style={rowStyle}>{head}</View>}
    {open && body ? <View style={{ paddingLeft: 18 }}>{body}</View> : null}
  </View>;
}

function ActivityRow({ entry }: { entry: SessionEntry }) {
  const theme = useTheme();
  const type = entry.activityType ?? '';
  if (type === 'tool_call') {
    const status = entry.status ? toolStatus[entry.status] : undefined;
    const tone = status && { running: theme.running, error: theme.error, muted: theme.muted }[status.tone];
    return <View style={{ gap: 2 }}>
      <Row testID="chat-tool" icon={toolIcons[entry.detail?.kind ?? ''] ?? Wrench} name={entry.title ?? '工具'} mono
        summary={toolSummary(entry)} status={status && tone ? { label: status.label, color: tone } : null}
        body={<ToolBody entry={entry} />} />
      {entry.status === 'failed' && entry.error && <Text numberOfLines={2} selectable
        style={{ paddingLeft: 18, fontSize: 12, lineHeight: 18, color: theme.error }}>{firstLine(entry.error.text)}</Text>}
    </View>;
  }
  if (type === 'reasoning') {
    const note = truncNote(entry);
    return <Row testID="chat-reasoning" icon={Brain} name="思考" summary={firstLine(entry.text ?? '')}
      body={entry.text ? <View style={{ gap: 2 }}>
        <Text selectable style={{ fontSize: 12, lineHeight: 19, color: theme.muted }}>{entry.text}</Text>
        {note && <Text style={{ fontSize: 11, color: theme.muted }}>{note}</Text>}
      </View> : undefined} />;
  }
  if (type === 'todo') {
    const items = entry.items ?? [];
    return <View testID="chat-todo" style={{ gap: 2 }}>
      <Row icon={ListChecks} name="任务清单" summary={`完成 ${items.filter(i => i.completed).length}/${items.length}`} />
      <View style={{ paddingLeft: 18, gap: 2 }}>
        {items.map((item, i) => {
          const Check = item.completed ? SquareCheck : Square;
          const active = !item.completed && item.status === 'in_progress';
          return <View key={i} style={{ flexDirection: 'row', alignItems: 'flex-start', gap: 6 }}>
            <View style={{ paddingTop: 3 }}><Check size={12} color={active ? theme.running : theme.muted} /></View>
            <Text selectable style={{ flexShrink: 1, fontSize: 12, lineHeight: 18, color: active ? theme.running : item.completed ? theme.muted : theme.secondary,
              textDecorationLine: item.completed ? 'line-through' : 'none' }}>{item.text?.text ?? ''}</Text>
          </View>;
        })}
        {entry.itemsOmitted ? <Text style={{ fontSize: 11, color: theme.muted }}>{`另有 ${entry.itemsOmitted} 项未显示`}</Text> : null}
      </View>
    </View>;
  }
  if (type === 'error' || type === 'notification') {
    const color = type === 'error' || entry.status === 'error' ? theme.error : entry.status === 'warning' ? theme.warning : theme.muted;
    const Icon = type === 'error' ? CircleAlert : Bell;
    return <View testID={`chat-${type}`} style={{ flexDirection: 'row', alignItems: 'flex-start', gap: 6 }}>
      <View style={{ paddingTop: 3 }}><Icon size={12} strokeWidth={1.6} color={color} /></View>
      <Text selectable style={{ flexShrink: 1, fontSize: 12, lineHeight: 18, color }}>
        {(entry.text ?? (type === 'error' ? '出错' : '通知')) + (entry.truncated ? '（已截断）' : '')}</Text>
    </View>;
  }
  if (type === 'compaction')
    return <Row icon={Shrink} name="上下文压缩"
      summary={entry.status === 'loading' ? '进行中…' : entry.status === 'completed' ? '完成' : undefined} />;
  return <Row icon={Wrench} name={type === 'plugin' ? '插件活动' : type && type !== 'activity' ? type : '活动'} />;
}

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

// Consecutive activity stays together, one row per item, like Paseo.
function ActivityGroup({ entries }: { entries: SessionEntry[] }) {
  return <View style={{ gap: 4 }}>{entries.map(entry => <ActivityRow key={entry.id} entry={entry} />)}</View>;
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

/** Conversation with the selected Agent: user bubbles, assistant text, the activity trajectory. */
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
