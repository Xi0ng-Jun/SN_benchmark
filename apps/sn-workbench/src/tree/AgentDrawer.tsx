import React, { useEffect, useRef, useState } from 'react';
import { Platform, ScrollView, Text, View } from 'react-native';
import { ChevronDown, ChevronUp, Lightbulb, Maximize2, Minimize2 } from 'lucide-react-native';
import { AgentMessageRecord, Bootstrap, date, liveSessions, Task, TaskSession, useResource } from '../contracts';
import { Button, Copy, useTheme } from '../ui';
import { IconButton } from '../shell';
import { useTaskSessions } from '../agents/useTaskSessions';
import { useMessageContext } from '../agents/useMessageContext';
import { useAgentMessages, MessageDraftPort } from '../agents/useAgentMessages';
import { useCandidates } from '../agents/useCandidates';
import { CandidateDraftPanel } from '../agents/CandidateDraftPanel';
import { CandidateRecordsPanel } from '../agents/CandidateRecordsPanel';
import { useAgentCreations } from '../agents/useAgentCreations';
import { AgentChat, Outgoing } from '../agents/AgentChat';
import { ChatComposer, ComposerStatus } from '../agents/ChatComposer';
import { AgentButton, AgentMenu } from '../agents/AgentPicker';
import { ContextMenu } from '../agents/ContextMenu';
import type { SessionEntry } from '../agents/chatTypes';
import { RemoveConfirm } from './parts';
import { ITERATION_MIN_EVALUATED } from './next';

// A new Agent's binding: an analyst reads one node or (scope 'iteration') the whole
// tree, an implementer changes one candidate.
export type AgentTarget = { role: 'analyst' | 'implementer'; candidateId?: string; hypothesisId?: string; scope?: 'iteration'; label: string };
// The candidate selected in the main view: what the menu's "new" creates for.
export type NodeContext = { candidateId: string; label: string; state: string; hypothesisId?: string };

const roleLabels: Record<string, string> = { analyst: '分析', implementer: '实现' };
// Each Agent's header states what it can see (design §7). The isolation is
// information not sent plus a leak scan, not a filesystem sandbox.
const scopes: Record<string, string> = {
  analyst: '可见：题面 · gold · 成绩 · 运行；对 SN 只读',
  implementer: '可见：已批准提议 · 候选 worktree 代码；不下发题面、gold、成绩（非沙箱）',
};
function plainTitle(title: string | null): string | null {
  if (!title) return null;
  try { const body = JSON.parse(title); if (typeof body?.message === 'string') return body.message.slice(0, 40); } catch { /* plain title */ }
  return title.startsWith('{"message"') ? null : title.slice(0, 40);
}

// The conversation machinery is the pre-tree workbench's, moved here unchanged:
// sessions, message intents, attachments (brief and run summary auto-attached),
// Agent creation, and findings (发现) from assistant messages.
const SIZE_KEY = 'sn-workbench-drawer-v1';
const readSize = () => { try { const n = Number(localStorage.getItem(SIZE_KEY)); return Number.isFinite(n) && n >= 200 ? n : 380; } catch { return 380; } };

// Drag the top edge to resize; the height is remembered. Web only, like the side handles.
function TopHandle({ height, onResize }: { height: number; onResize: (n: number) => void }) {
  const theme = useTheme();
  const drag = useRef<{ y: number; height: number } | null>(null);
  const [active, setActive] = useState(false);
  if (Platform.OS !== 'web') return null;
  return <div data-testid="resize-drawer" role="separator" aria-label="调整 Agent 抽屉高度" aria-orientation="horizontal" tabIndex={0}
    style={{ position: 'absolute', zIndex: 5, left: 0, right: 0, top: -3, height: 6, cursor: 'row-resize', touchAction: 'none', background: active ? theme.lineStrong : 'transparent' }}
    onPointerEnter={() => setActive(true)} onPointerLeave={() => { if (!drag.current) setActive(false); }}
    onPointerDown={e => { if (e.button !== 0) return; e.preventDefault(); e.currentTarget.setPointerCapture(e.pointerId); drag.current = { y: e.clientY, height }; setActive(true); }}
    onPointerMove={e => { if (drag.current) onResize(drag.current.height - (e.clientY - drag.current.y)); }}
    onPointerUp={e => { drag.current = null; e.currentTarget.releasePointerCapture(e.pointerId); setActive(false); }}
    onKeyDown={e => { if (e.key === 'ArrowUp' || e.key === 'ArrowDown') { e.preventDefault(); onResize(height + (e.key === 'ArrowUp' ? 40 : -40)); } }} />;
}

export function AgentDrawer({ task, boot, compact, open, available, onToggle, hasRuns, target, onTargetDone, onTaskChanged, labels, focus,
  node, evaluated, onRemoveAgent }: {
  task: Task | undefined; boot: Bootstrap; compact: boolean; open: boolean; available: number; onToggle: () => void; hasRuns: boolean;
  target: AgentTarget | null; onTargetDone: () => void; onTaskChanged: () => void;
  labels: Record<string, string>; focus: { sessionId: string; nonce: number } | null;
  node: NodeContext | null; evaluated: number; onRemoveAgent: (sessionId: string) => Promise<unknown>;
}) {
  const theme = useTheme();
  const [size, setSize] = useState(readSize);
  const [maximized, setMaximized] = useState(false);
  useEffect(() => { try { localStorage.setItem(SIZE_KEY, String(Math.round(size))); } catch { /* optional */ } }, [size]);
  const height = maximized ? available : Math.max(200, Math.min(size, available - 80));
  const taskRef = useRef(task?.id);
  taskRef.current = task?.id;
  const changed = (id: string) => { if (taskRef.current === id) onTaskChanged(); };
  const [menu, setMenu] = useState<'none' | 'agent' | 'context'>('none');
  const [findingsOpen, setFindingsOpen] = useState(false);
  const [candidateOpen, setCandidateOpen] = useState(false);
  const [outgoing, setOutgoing] = useState<(Outgoing & { sessionId: string }) | null>(null);
  const [chatError, setChatError] = useState<{ sessionId: string; text: string } | null>(null);
  const scrollRef = useRef<ScrollView>(null);
  const stickToBottom = useRef(true);
  const sessions = useTaskSessions(task, changed, { pollPaused: candidateOpen });
  const session = sessions.session;
  const messagePort = useRef<MessageDraftPort | null>(null);
  const messages = useAgentMessages(task, session, boot.actor, sessions.generation, boot.capabilities?.agent_send === true,
    sessions.remote.status === 'available' && sessions.remote.agentStatusLabel !== 'archived', messagePort);
  const contexts = useMessageContext(task, session, sessions.generation, messages.draftId, null, messages.edit, null, hasRuns, session?.role === 'implementer');
  messagePort.current = { read: contexts.readDraft, clear: contexts.clearDraft };
  const candidates = useCandidates(task, boot.actor, sessions, changed);
  const creations = useAgentCreations(task, boot.actor, compact, boot.capabilities?.agent_create === true, boot.creation_profiles ?? [], changed);

  // A creation target carries its own model buttons; the Agent menu stays closed.
  useEffect(() => { if (target) setMenu('none'); }, [target?.candidateId, target?.role, target?.scope]);
  useEffect(() => {
    if (focus && focus.sessionId !== session?.id && task?.sessions.some(s => s.id === focus.sessionId)) {
      contexts.invalidate(); candidates.invalidateSource(); sessions.selectSession(focus.sessionId); setChatError(null);
    }
  }, [focus?.nonce]);
  // What an Agent works on: an implementer changes one candidate, an analyst reads one node
  // or, explicitly, the whole iteration; an analyst with neither is the earlier unbound kind.
  const binding = (saved: TaskSession | undefined) => !saved ? ''
    : saved.candidate_id ? `候选 ${labels[saved.candidate_id] ?? saved.candidate_id.slice(0, 8)}`
    : saved.scope === 'iteration' ? '迭代'
    : saved.role === 'implementer' ? '候选 ?' : '未绑定（旧）';
  // Menu groups: the iteration first, then candidates in tree order, then legacy sessions.
  const nodeOrder = Object.keys(labels);
  const groupOf = (saved: TaskSession) => saved.candidate_id ? binding(saved) : saved.scope === 'iteration' ? '本迭代' : '历史（未绑定）';
  const groupRank = (saved: TaskSession) => saved.candidate_id ? 1 + (nodeOrder.indexOf(saved.candidate_id) + 1 || nodeOrder.length + 1)
    : saved.scope === 'iteration' ? 0 : 1e6;
  const order = (saved: { role?: string | null }) => saved.role === 'implementer' ? 1 : 0;
  const sessionOptions = liveSessions(task?.sessions ?? []).sort((a, b) => groupRank(a) - groupRank(b)
    || groupOf(a).localeCompare(groupOf(b)) || order(a) - order(b) || b.linked_at.localeCompare(a.linked_at)).map(saved => {
    const created = creations.records.find(record => record.agent_id === saved.agent_id && record.status === 'created');
    const agent = created?.envelope?.result.agent;
    const live = saved.id === session?.id && sessions.remote.status === 'available' ? sessions.remote : null;
    const provider = live?.agentProvider ?? agent?.provider;
    const model = live?.agentModel ?? agent?.model;
    const title = plainTitle(live?.agentTitle ?? agent?.title ?? null);
    const label = [saved.role ? roleLabels[saved.role] : null, binding(saved), provider ? [provider, model].filter(Boolean).join(' · ') : 'Agent ' + saved.agent_id.slice(0, 8)].filter(Boolean).join(' · ');
    return { id: saved.id, label, selected: saved.id === session?.id, group: groupOf(saved),
      detail: [title, `${date(saved.linked_at)} 关联`, saved.agent_id.slice(0, 8)].filter(Boolean).join(' · ') };
  });
  const currentAgent = sessionOptions.find(option => option.selected);
  const pageCurrent = sessions.page.status === 'ready' && sessions.page.freshness === 'current' && sessions.page.pendingAction === 'none';
  const candidateReason = (entry: SessionEntry): string | null => entry.candidateBlockedReason
    ?? (entry.truncated ? '正文已截断，不能作为发现来源' : !entry.text?.trim() ? '此条目没有可引用正文'
      : !pageCurrent || sessions.remote.status !== 'available' ? '对话正在刷新，稍后再试' : null);
  const messageText = contexts.message.text;
  const sending = useRef<{ taskId: string; draftId: string; text: string } | null>(null);
  const refill = useRef<{ taskId: string; fromDraft: string; text: string } | null>(null);
  const [sentTick, setSentTick] = useState(0);
  const send = () => {
    if (!task || !session) return;
    sending.current = { taskId: task.id, draftId: messages.draftId, text: messageText };
    setChatError(null); stickToBottom.current = true;
    setOutgoing({ sessionId: session.id, text: messageText, attachments: contexts.message.attachments.length, state: 'sending',
      before: sessions.page.entries.map(entry => entry.id) });
    messages.send();
  };
  useEffect(() => {
    const mine = sending.current && sending.current.taskId === task?.id && sending.current.draftId === messages.draftId ? sending.current : null;
    if (messages.status === 'accepted' && messages.canNewDraft) {
      if (mine) { setOutgoing(prev => prev ? { ...prev, state: 'sent' } : prev); sessions.nudge(); sending.current = null; setSentTick(n => n + 1); }
      messages.newDraft();
    } else if (messages.status === 'error' && messages.canNewDraft) {
      if (mine && task && session) {
        setChatError({ sessionId: session.id, text: '消息没有发出：' + (messages.detail ?? '服务端拒绝') });
        setOutgoing(null); refill.current = { taskId: task.id, fromDraft: messages.draftId, text: mine.text }; sending.current = null;
      }
      messages.newDraft();
    }
  }, [messages.status, messages.draftId, messages.canNewDraft]);
  useEffect(() => {
    const pending = refill.current;
    if (pending && pending.taskId === task?.id && pending.fromDraft !== messages.draftId && messages.status === 'idle' && !messages.editingLocked) {
      refill.current = null; contexts.message.onChange(pending.text);
    }
  }, [messages.draftId, messages.status, messages.editingLocked]);
  // Each new draft starts with what this session has not received yet: its role
  // brief and (not for implementers) the Task's run summary — visible, removable.
  const sentRecords = useResource<AgentMessageRecord[]>(task && session ? `/tasks/${task.id}/agent-messages` : null, sentTick);
  const [recordsAt, setRecordsAt] = useState(-1);
  useEffect(() => { if (sentRecords.data) setRecordsAt(sentTick); }, [sentRecords.data]);
  const autoTried = useRef(new Set<string>());
  useEffect(() => {
    if (!session || !sentRecords.data || recordsAt !== sentTick || messages.editingLocked || messages.status !== 'idle') return;
    const delivered = sentRecords.data.filter(r => r.session_id === session.id && r.status === 'acknowledged').flatMap(r => r.contexts);
    const lastSha = (kind: string) => delivered.filter(c => c.reference.kind === kind).at(-1)?.sha256 ?? null;
    const wanted = [
      { reference: { kind: 'brief' as const, session_id: session.id }, skipSha: lastSha('brief') },
      // A node-bound analyst's brief already carries its node's runs and path; an iteration
      // analyst gets a fresh tree summary; a legacy unbound one the Task's runs.
      ...(session.scope === 'iteration' ? [{ reference: { kind: 'iteration_tree' as const }, skipSha: lastSha('iteration_tree') }]
        : session.role !== 'implementer' && !session.candidate_id && hasRuns ? [{ reference: { kind: 'task_runs' as const }, skipSha: lastSha('task_runs') }] : []),
    ].filter(({ reference }) => {
      const key = `${messages.draftId}:${reference.kind}`;
      if (autoTried.current.has(key)) return false;
      autoTried.current.add(key);
      return true;
    });
    if (wanted.length) contexts.autoAttach(wanted);
  }, [session?.id, messages.draftId, messages.editingLocked, messages.status, Boolean(sentRecords.data), recordsAt, sentTick, hasRuns]);
  const shownOutgoing = outgoing && outgoing.sessionId === session?.id ? outgoing : null;
  const shownError = chatError && chatError.sessionId === session?.id ? chatError.text : null;
  // The first message must carry the role brief: wait for the automatic attachments.
  const attaching = Boolean(session) && ((!sentRecords.data && !sentRecords.error) || contexts.autoAttaching);
  const composerStatus: ComposerStatus | null = !task || !session ? null
    : messages.status === 'unknown' ? { text: '上一条消息的发送结果未确认，确认前不能再发。' + (messages.detail ? `（${messages.detail}）` : ''), tone: 'error',
      action: messages.checking ? undefined : { label: '核对', onPress: messages.check } }
    : shownError ? { text: shownError, tone: 'error' }
    : sessions.remote.status === 'loading' && !sessions.remote.agentStatusLabel ? { text: '正在连接 Agent…', tone: 'muted' }
    : sessions.remote.status === 'missing' || sessions.remote.status === 'unavailable' ? null
    : messages.blockedReason && !/正在取得消息写入权|当前意图已冻结/.test(messages.blockedReason) ? { text: messages.blockedReason, tone: 'muted' }
    : sessions.running ? { text: 'Agent 正在运行：现在发送会打断当前这一轮', tone: 'muted' }
    : attaching ? { text: '正在附上工作说明与材料…', tone: 'muted' } : null;
  const canSend = Boolean(session) && !attaching && messages.blockedReason === null && messages.status === 'idle' && messageText.trim().length > 0;
  // The menu creates for the current context: the pending target, else the selected node,
  // else the iteration. Nothing creates an unbound analyst any more.
  const nodeTarget: AgentTarget | null = !node ? null
    : ['evaluated', 'failed'].includes(node.state) ? { role: 'analyst', candidateId: node.candidateId, label: node.label }
    : node.state === 'implementing' && node.hypothesisId ? { role: 'implementer', candidateId: node.candidateId, hypothesisId: node.hypothesisId, label: node.label }
    : null;
  const createFor: AgentTarget | null = target ?? (node ? nodeTarget
    : evaluated >= ITERATION_MIN_EVALUATED ? { role: 'analyst', scope: 'iteration', label: '迭代' } : null);
  const contextReason = createFor ? null
    : node ? `${node.label} 现在不能新建 Agent：分析要先评测完，实现要在实现中且有提议`
    : '先在谱系里选一个候选';
  const createAgent = (profileId: string) => {
    if (!createFor) return;
    if (creations.canNewIntent) creations.newIntent();
    const name = `${roleLabels[createFor.role]} · ${createFor.scope === 'iteration' ? '迭代' : '候选 ' + createFor.label} · ${task?.title ?? ''}`;
    creations.setTarget(createFor.role, createFor.hypothesisId, createFor.candidateId, createFor.scope,
      name.replace(/\s+/g, ' ').trim().slice(0, 80).trim());
    creations.panel?.onProfileSelect(profileId);
    creations.panel?.onCreate?.();
  };
  const createEnabled = creations.available && creations.status !== 'saving' && creations.status !== 'unknown' && !creations.checking
    && (creations.panel?.blockedReason == null || creations.canNewIntent);
  const createReason = !creations.available ? '服务器未开放 Agent 创建' : creations.panel?.blockedReason && !creations.canNewIntent ? creations.panel.blockedReason : null;
  const removeCurrent = async (id: string) => {
    await onRemoveAgent(id);
    if (id === session?.id) { contexts.invalidate(); candidates.invalidateSource(); setChatError(null); setMenu('none'); }
  };
  const adopted = useRef(new Set<string>());
  const createdSession = creations.panel?.receipt?.sessionId;
  useEffect(() => {
    if (!createdSession || adopted.current.has(createdSession) || !task?.sessions.some(saved => saved.id === createdSession)) return;
    adopted.current.add(createdSession); contexts.invalidate(); candidates.invalidateSource(); sessions.selectSession(createdSession); setMenu('none');
    if (target) onTargetDone();
  }, [createdSession, task?.sessions.length]);
  const creationStatus = creations.status === 'saving' ? { text: '正在新建 Agent…', tone: 'muted' as const }
    : creations.status === 'unknown' ? { text: '新建结果尚未确认' + (creations.panel?.detail ? `：${creations.panel.detail}` : ''), tone: 'error' as const,
      action: creations.panel?.onCheck ? { label: creations.checking ? '核对中…' : '核对', onPress: creations.panel.onCheck } : undefined }
    : creations.status === 'error' || creations.status === 'not_attempted' ? { text: '新建失败' + (creations.panel?.detail ? `：${creations.panel.detail}` : ''), tone: 'error' as const }
    : null;
  const role = session?.role ?? 'analyst';
  const Chevron = open ? ChevronDown : ChevronUp;
  return <View testID="agent-drawer" style={{ position: 'relative', height: open ? height : 38, borderTopWidth: 1, borderColor: theme.line, backgroundColor: theme.bg }}>
    {open && !maximized && <TopHandle height={height} onResize={n => setSize(Math.max(200, Math.min(n, available - 80)))} />}
    <View style={{ height: 38, paddingHorizontal: 10, flexDirection: 'row', alignItems: 'center', gap: 8, borderBottomWidth: open ? 1 : 0, borderColor: theme.line }}>
      <IconButton label={open ? '收起 Agent 抽屉' : '展开 Agent 抽屉'} icon={Chevron} onPress={onToggle} testID="toggle-drawer" />
      <Text testID="agent-binding" numberOfLines={1} style={{ fontSize: 13, fontWeight: '500', color: theme.text }}>
        {session ? `${roleLabels[role]} · ${binding(task?.sessions.find(s => s.id === session.id))}` : 'Agent'}</Text>
      {session && <Text testID="agent-scope" numberOfLines={1} style={{ flexShrink: 1, fontSize: 11, color: theme.muted }}>{scopes[role]}</Text>}
      <View style={{ flex: 1 }} />
      {open && session && <RemoveConfirm key={session.id} onRemove={() => removeCurrent(session.id)} testID="drawer-remove-agent" />}
      {open && candidates.recordsPanel && <IconButton label="发现记录" icon={Lightbulb} selected={findingsOpen} onPress={() => setFindingsOpen(v => !v)} testID="toggle-findings" />}
      {open && <IconButton label={maximized ? '还原抽屉' : '最大化对话'} icon={maximized ? Minimize2 : Maximize2} selected={maximized} onPress={() => setMaximized(m => !m)} testID="maximize-drawer" />}
    </View>
    {open && <View style={{ flex: 1, minHeight: 0, flexDirection: 'row' }}>
      <View style={{ flex: 1, minWidth: 0 }}>
        {target && <View testID={target.scope === 'iteration' ? 'iteration-target' : target.role + '-target'} style={{ margin: 10, marginBottom: 0, padding: 10, borderRadius: 8, borderWidth: 1, borderColor: theme.primary, gap: 6 }}>
          <Copy small>{target.scope === 'iteration'
            ? '新建迭代分析 Agent：它读整棵候选树（各节点的运行、Δ父／Δ根、提议与预测核对、采纳决定），做横向比较、假设复盘和合并建议；提议必须指定挂在哪个已评测节点下。'
            : target.role === 'analyst'
            ? `为候选 ${target.label} 新建分析 Agent。它读 ${target.label} 的运行、对父的翻转和从根到这里每一轮的记录，提议默认挂在 ${target.label} 下。`
            : `为候选 ${target.label} 新建实现 Agent。它在该候选的 worktree 里工作，只拿到已批准提议中给实现者的字段。`}</Copy>
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6 }}>
            {creations.profiles.map((profile, i) => <Button key={profile.id} label={`用 ${profile.providerLabel} · ${profile.modelLabel} 新建`}
              primary={i === 0} disabled={!createEnabled} onPress={() => createAgent(profile.id)} testID={'target-create-' + profile.id} />)}
            <Button label="取消" onPress={onTargetDone} />
          </View>
          {!creations.profiles.length && <Copy small muted>服务器未配置可用的模型</Copy>}
          {createReason && <Copy small muted>{createReason}</Copy>}
          {creationStatus && <Text testID="target-creation-status" style={{ fontSize: 12, color: creationStatus.tone === 'error' ? theme.error : theme.muted }}>{creationStatus.text}</Text>}
          {creationStatus?.action && <View style={{ flexDirection: 'row' }}><Button label={creationStatus.action.label} onPress={creationStatus.action.onPress} /></View>}
        </View>}
        <ScrollView ref={scrollRef} style={{ flex: 1 }} contentContainerStyle={{ padding: compact ? 12 : 18, gap: 12 }} scrollEventThrottle={100}
          onScroll={event => { const { contentOffset, contentSize, layoutMeasurement } = event.nativeEvent;
            stickToBottom.current = contentSize.height - contentOffset.y - layoutMeasurement.height < 120; }}
          onContentSizeChange={() => { if (stickToBottom.current) scrollRef.current?.scrollToEnd({ animated: false }); }}>
          <View style={{ width: '100%', maxWidth: 820, alignSelf: 'center', gap: 12 }}>
          <AgentChat hasSession={Boolean(session)} remote={sessions.remote} page={sessions.page} viewingTail={sessions.viewingTail}
            outgoing={shownOutgoing} running={sessions.running} candidateReason={candidateReason}
            onOlder={sessions.older ? () => { candidates.invalidateSource(); sessions.older?.(); } : undefined}
            onLatest={() => { candidates.invalidateSource(); sessions.refresh(); }}
            onCandidate={(id, kind) => { candidates.requestCandidate(id, kind); setCandidateOpen(true); }}
            empty={<View style={{ gap: 8, alignItems: 'flex-start' }}>
              <Copy muted>这个迭代还没有 Agent。节点 Agent 从候选详情里新建：分析 Agent 绑定一个已评测候选，读它的运行与证据并在它下面提提议；实现 Agent 绑定一个候选，在它的 worktree 里改码。迭代分析 Agent 从谱系头部的“分析整个迭代”新建，读整棵树做横向比较。写动作只能申请，由你确认。</Copy>
            </View>} />
          {candidates.draftPanel && <View style={{ gap: 8 }}>
            {!candidateOpen && <Button label="继续发现草稿" testID="candidate-draft-reopen" onPress={() => setCandidateOpen(true)} />}
            <View style={{ display: candidateOpen ? 'flex' : 'none' }}><CandidateDraftPanel compact={compact} {...candidates.draftPanel} closeLabel="收起草稿" onCancel={() => setCandidateOpen(false)} /></View>
            {candidates.checkCandidateReceipt && <Button label="核对发现保存回执" testID="candidate-check-receipt" onPress={candidates.checkCandidateReceipt} disabled={candidates.checkingCandidate} />}
          </View>}
          </View>
        </ScrollView>
        {task && <View style={{ width: '100%', maxWidth: 868, alignSelf: 'center' }}><ChatComposer compact={compact} value={messageText} onChange={contexts.message.onChange} editable={Boolean(session) && !messages.editingLocked}
          canSend={canSend} onSend={send} placeholder={session ? '给 Agent 发消息（Enter 发送，Shift+Enter 换行）' : '先选择或新建一个 Agent'}
          attachments={contexts.message.attachments.map(item => ({ id: item.id, label: item.title }))}
          onOpenAttachment={id => { setMenu('context'); contexts.open(id); }} onRemoveAttachment={contexts.message.onRemoveReference}
          attachOpen={menu === 'context'} onToggleAttach={() => setMenu(m => m === 'context' ? 'none' : 'context')}
          attachEnabled={Boolean(session) && !messages.editingLocked}
          agentButton={<AgentButton label={currentAgent?.label ?? '选择 Agent'} open={menu === 'agent'}
            status={session && sessions.remote.status === 'available' ? sessions.remote.agentStatusLabel : undefined}
            onPress={() => setMenu(m => m === 'agent' ? 'none' : 'agent')} />}
          status={composerStatus}
          popover={menu === 'agent' ? <AgentMenu agents={sessionOptions}
            onSelect={id => { if (id !== session?.id) { contexts.invalidate(); candidates.invalidateSource(); sessions.selectSession(id); setChatError(null); } }}
            profiles={creations.profiles.map(profile => ({ id: profile.id, label: `${profile.providerLabel} · ${profile.modelLabel}`,
              detail: [profile.effortLabel && '思考 ' + profile.effortLabel, profile.modeLabel && '模式 ' + profile.modeLabel].filter(Boolean).join(' · ') || profile.id }))}
            createEnabled={createEnabled && Boolean(createFor)} createReason={createReason ?? contextReason}
            onCreate={createAgent} creation={creationStatus}
            link={{ value: sessions.link.value, onChange: sessions.link.onChange, onSubmit: sessions.link.onRequestLink,
              statusText: sessions.link.status === 'linking' ? '关联中…' : sessions.link.status === 'linked' ? '已关联' : sessions.link.status === 'error' ? '关联失败：' + (sessions.link.detail ?? '') : null }}
            maxHeight={Math.max(180, Math.min(420, height - 38 - 150 - (target ? 100 : 0)))}
            cwdLabel={!createFor ? '新建 Agent：节点 Agent 绑定所选候选，迭代分析 Agent 从谱系头部新建'
              : createFor.role === 'implementer' ? `新建的是 ${createFor.label} 的实现 Agent，工作目录为该候选 worktree`
              : createFor.scope === 'iteration' ? '新建的是迭代分析 Agent：读整棵候选树，对 SN 只读；提议须指定挂在哪个已评测节点下'
              : `新建的是 ${createFor.label} 的分析 Agent：工作目录为评测仓库，对 SN 只读`}
            onClose={() => setMenu('none')} />
            : menu === 'context' ? <ContextMenu mode={contexts.selection.mode} onModeChange={contexts.selection.onModeChange}
              options={contexts.selection.options} selectedId={contexts.selection.selectedOptionId} onSelect={contexts.selection.onSelectOption}
              path={contexts.selection.pathValue} onPathChange={contexts.selection.onPathChange} onPreview={contexts.requestPreview}
              preview={contexts.preview} addBlockedReason={contexts.addBlockedReason} onAdd={contexts.add} onClose={() => setMenu('none')} />
            : null} /></View>}
      </View>
      {findingsOpen && candidates.recordsPanel && <ScrollView testID="findings" style={{ width: compact ? '100%' : 380, borderLeftWidth: 1, borderColor: theme.line }}
        contentContainerStyle={{ padding: 12, gap: 8 }}>
        <CandidateRecordsPanel compact={compact} {...candidates.recordsPanel} />
        {candidates.checkDecisionReceipt && <Button label="核对人工判断回执" testID="candidate-decision-check-receipt" onPress={candidates.checkDecisionReceipt} disabled={candidates.checkingDecision} />}
      </ScrollView>}
    </View>}
  </View>;
}
