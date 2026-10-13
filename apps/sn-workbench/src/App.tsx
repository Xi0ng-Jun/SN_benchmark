import React, { useEffect, useRef, useState } from 'react';
import { Linking, Modal, Platform, Pressable, ScrollView, Text, useWindowDimensions, View } from 'react-native';
import { BookOpen, ClipboardList, FileText, Settings, Plus, PanelLeftOpen, PanelRightClose, PanelRightOpen } from 'lucide-react-native';
import { api, Bootstrap, date, History, Result, Source, Task, requestKey } from './contracts';
import { Materials, useResource } from './materials';
import { Block, Button, Copy, Fold, Input, s, ThemeContext, themes } from './ui';
import { CHROME_HEIGHT, IconButton, ResizeHandle, useShellLayout, WorkspaceSidebar } from './shell';
import { useTaskSessions } from './agents/useTaskSessions';
import { useMessageContext } from './agents/useMessageContext';
import { useAgentMessages, MessageDraftPort } from './agents/useAgentMessages';
import { useCandidates } from './agents/useCandidates';
import { CandidateDraftPanel } from './agents/CandidateDraftPanel';
import { CandidateRecordsPanel } from './agents/CandidateRecordsPanel';
import { useAgentCreations } from './agents/useAgentCreations';
import { AgentChat, Outgoing } from './agents/AgentChat';
import { ChatComposer, ComposerStatus } from './agents/ChatComposer';
import { AgentButton, AgentMenu, agentStatusLabels } from './agents/AgentPicker';
import { ContextMenu } from './agents/ContextMenu';
import type { SessionEntry } from './agents/chatTypes';
import { useTaskComparison } from './comparison/useTaskComparison';
import { ComparisonPanel } from './comparison/ComparisonPanel';
import { usePlanDrafts, PlanDraftController } from './plans/usePlanDrafts';
import { PlanDraftEditor } from './plans/PlanDraftEditor';
import { PlanDraftHistory } from './plans/PlanDraftHistory';
import { TaskViewNav, TaskView } from './workspace/TaskViewNav';
import { WorkspaceConfiguration } from './workspace/WorkspaceConfiguration';
import { useRunViews } from './runs/useRunViews';
import { DecisionRow, DiffCard, RunCard, TimelineLabel } from './runs/RunCard';
import { RunMaterials, RunPanel, RunSelection } from './runs/RunMaterials';
import { ChangesPage } from './runs/ChangesPage';
import { HypothesesPanel } from './runs/HypothesesPanel';
import type { AgentMessageRecord } from './contracts';
import { Interpretation, InterpretationButton } from './runs/Interpretation';
import { caseLabel, attentionRuleLabel } from './runs/contracts';
import { historyLabel, visibleHistory } from './workspace/historyLabels';

const roleLabels: Record<string, string> = { analyst: '分析', implementer: '实现' };
function plainTitle(title: string | null): string | null {
  if (!title) return null;
  try { const body = JSON.parse(title); if (typeof body?.message === 'string') return body.message.slice(0, 40); } catch { /* plain title */ }
  return title.startsWith('{"message"') ? null : title.slice(0, 40);
}
type Route = { task: string | null; result: string | null; caseId: string | null; panel: string; view: TaskView; run: string | null; runCase: string | null };
function initialRoute(): Route {
  const hash = Platform.OS === 'web' ? new URLSearchParams(window.location.hash.slice(1)) : new URLSearchParams();
  const panel = hash.get('panel') ?? '';
  const requestedView = hash.get('view');
  const view: TaskView = panel === 'resources' ? 'results' : panel === 'history' ? 'records'
    : requestedView === 'results' || requestedView === 'plans' || requestedView === 'records' || requestedView === 'changes' ? requestedView : 'work';
  return { task: hash.get('task'), result: hash.get('result'), caseId: hash.get('case'),
    panel: ['scores', 'cases', 'source', 'run-cases', 'run-flips'].includes(panel) ? panel : '', view, run: hash.get('run'), runCase: hash.get('rcase') };
}

export default function App() {
  const { width } = useWindowDimensions();
  const layout = useShellLayout(width);
  const compact = layout.compact;
  const [dark, setDark] = useState(false);
  const theme = dark ? themes.dark : themes.light;
  const [route, setRoute] = useState<Route>(initialRoute);
  const [boot, setBoot] = useState<Bootstrap>();
  const [auth, setAuth] = useState(false);
  const [access, setAccess] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [refresh, setRefresh] = useState(0);
  const [nav, setNav] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [comparisonOpen, setComparisonOpen] = useState(false);
  const [plansTaskId, setPlansTaskId] = useState<string | null>(() => initialRoute().view === 'plans' ? initialRoute().task : null);
  const [candidateOpen, setCandidateOpen] = useState(false);
  // One popover above the composer at a time: Agent/model menu or materials.
  const [menu, setMenu] = useState<'none' | 'agent' | 'context'>('none');
  const [outgoing, setOutgoing] = useState<(Outgoing & { sessionId: string }) | null>(null);
  const [chatError, setChatError] = useState<{ sessionId: string; text: string } | null>(null);
  const scrollRef = useRef<ScrollView>(null);
  const stickToBottom = useRef(true);
  const [selectedHistory, setSelectedHistory] = useState<{ taskId: string; item: History } | null>(null);
  const [interpretationOpen, setInterpretationOpen] = useState(false);
  const [runFilter, setRunFilter] = useState<{ needsRead: boolean; against: string | null; provenance: boolean }>({ needsRead: true, against: null, provenance: false });
  const [globalRunsOpen, setGlobalRunsOpen] = useState(false);
  const taskActivation = useRef(0);
  const activation = taskActivation.current;
  const lastPanel = useRef(route.panel || 'scores');
  useEffect(() => { if (route.panel) lastPanel.current = route.panel; }, [route.panel]);
  const [newTask, setNewTask] = useState(false);
  const [taskTitle, setTaskTitle] = useState('');
  const [goal, setGoal] = useState('');
  const [taskKey, setTaskKey] = useState(requestKey);
  const [busy, setBusy] = useState(false);
  const selectedRef = useRef(route.task);
  selectedRef.current = route.task;
  const taskState = useResource<Task>(route.task && boot ? `/tasks/${route.task}` : null, refresh);
  const task = taskState.data;
  const currentTask = task?.id === route.task ? task : undefined;
  const comparison = useTaskComparison(currentTask);
  const comparisonScope = useRef({ task: currentTask, generation: comparison.generation });
  comparisonScope.current = { task: currentTask, generation: comparison.generation };
  const planPort = useRef<PlanDraftController | null>(null);
  const headsAfterSave = useRef<{ taskId: string; generation: number } | null>(null);
  const planDrafts = usePlanDrafts(plansTaskId === currentTask?.id || route.view === 'plans' ? currentTask : undefined, boot?.actor ?? '', boot?.inputs ?? [],
    saved => {
      if (selectedRef.current !== saved.task_id || !planPort.current) return;
      headsAfterSave.current = { taskId: saved.task_id, generation: planPort.current.generation };
      setRefresh(value => value + 1);
    });
  planPort.current = planDrafts;
  useEffect(() => {
    const pending = headsAfterSave.current;
    if (!pending) return;
    if (currentTask?.id !== pending.taskId || planDrafts.generation !== pending.generation) {
      headsAfterSave.current = null; return;
    }
    // A list started before the commit may still be pending. Wait for it to
    // settle, then explicitly read the committed heads; never adopt its old
    // snapshot as the just-saved revision or replace the fixed selection.
    if (!planDrafts.headsLoading) {
      headsAfterSave.current = null;
      void planDrafts.refreshHeads();
    }
  }, [refresh, currentTask?.id, planDrafts.generation, planDrafts.headsLoading]);
  const resultId = route.result ?? task?.results[0]?.id ?? null;
  const resultState = useResource<Result>(resultId && task?.results.some(r => r.id === resultId) ? `/results/${resultId}` : null);
  const result = resultState.data;
  useEffect(() => { if (route.view === 'plans' && currentTask) setPlansTaskId(currentTask.id); }, [route.view, currentTask?.id]);
  const workspace = boot?.workspaces.find(w => w.id === task?.workspace_id) ?? boot?.workspaces[0];
  const runViews = useRunViews((boot?.workspaces ?? []).flatMap(w => w.tasks.map(t => t.id)), currentTask?.id ?? null, refresh, Boolean(boot));
  const taskOverview = currentTask ? runViews.overview[currentTask.id] : undefined;
  // Task timelines contain only explicitly associated runs. The global catalog retains all runs.
  // Runs belong to a Task only through backend association (trusted registration or saved Task
  // capture). Unbound tasks use an explicit run.task_id when provided; never every run, never same-HEAD.
  const taskRuns = !currentTask ? [] : taskOverview && taskOverview.status !== 'unbound'
    ? runViews.runs.filter(run => taskOverview.runs.includes(run.id)) : runViews.runs.filter(run => run.task_id === currentTask.id);
  const sessions = useTaskSessions(task, id => { if (selectedRef.current === id) setRefresh(n => n + 1); }, { pollPaused: candidateOpen });
  const session = sessions.session;
  const messagePort = useRef<MessageDraftPort | null>(null);
  const messages = useAgentMessages(task, session, boot?.actor ?? '', sessions.generation,
    boot?.capabilities?.agent_send === true, sessions.remote.status === 'available' && sessions.remote.agentStatusLabel !== 'archived', messagePort);
  const messageDraftId = messages.draftId;
  const contexts = useMessageContext(task, session, sessions.generation, messageDraftId,
    route.caseId && resultId ? { result_id: resultId, case_id: route.caseId } : null, messages.edit,
    route.run && route.runCase && route.panel.startsWith('run-') ? { run_id: route.run, case_id: route.runCase, label: caseLabel(route.runCase) } : null,
    taskRuns.length > 0, session?.role === 'implementer');
  messagePort.current = { read: contexts.readDraft, clear: contexts.clearDraft };
  const candidates = useCandidates(task, boot?.actor ?? '', sessions,
    id => { if (selectedRef.current === id) setRefresh(n => n + 1); });
  const creations = useAgentCreations(task, boot?.actor ?? '', compact, boot?.capabilities?.agent_create === true,
    boot?.creation_profiles ?? [], id => { if (selectedRef.current === id) setRefresh(n => n + 1); });

  const updateRoute = (changes: Partial<Route>) => setRoute(prev => ({ ...prev, ...changes }));
  const openRun = (run: string, panel: RunPanel, changes: Partial<typeof runFilter> & { caseId?: string | null } = {}) => {
    const { caseId = null, ...filter } = changes;
    setRunFilter(prev => ({ ...prev, ...filter })); updateRoute({ run, runCase: caseId, panel });
  };
  const runSelection: RunSelection | null = route.run ? { runId: route.run, caseId: route.runCase, panel: route.panel === 'run-flips' ? 'run-flips' : 'run-cases', ...runFilter } : null;
  const historyItem = selectedHistory && selectedHistory.taskId === currentTask?.id ? selectedHistory.item : null;
  const restorePanel = lastPanel.current === 'history' && !historyItem ? 'scores' : lastPanel.current;
  const selectView = (view: TaskView) => { if (view === 'plans' && currentTask) setPlansTaskId(currentTask.id); updateRoute({ view }); };
  const toggleMaterials = () => updateRoute({ panel: route.panel ? '' : restorePanel });
  useEffect(() => {
    if (Platform.OS !== 'web') return;
    const shortcut = (event: KeyboardEvent) => {
      if (!(event.ctrlKey || event.metaKey) || event.altKey || event.shiftKey) return;
      if (event.key.toLowerCase() === 'b') { event.preventDefault(); if (compact) setNav(n => !n); else layout.toggleLeft(); }
      if (event.key.toLowerCase() === 'e') { event.preventDefault(); setRoute(p => ({ ...p, panel: p.panel ? '' : restorePanel })); }
    };
    window.addEventListener('keydown', shortcut);
    return () => window.removeEventListener('keydown', shortcut);
  }, [compact, layout.toggleLeft, restorePanel]);
  const reload = async () => {
    const data = await api<Bootstrap>('/bootstrap');
    setBoot(data); setAuth(false);
    setRoute(prev => ({ ...prev, task: prev.task ?? data.workspaces[0]?.tasks[0]?.id ?? null }));
  };
  useEffect(() => {
    let active = true;
    const connect = async () => {
      try {
        const token = Platform.OS === 'web' ? new URLSearchParams(window.location.hash.slice(1)).get('access') : null;
        if (token) {
          await api('/session', { token });
          window.history.replaceState(null, '', window.location.pathname);
        }
        if (active) await reload();
      } catch (e) { if (active) { setAuth(true); setError((e as Error).message); } }
    };
    void connect();
    return () => { active = false; };
  }, []);
  useEffect(() => {
    if (!boot || Platform.OS !== 'web') return;
    const params = new URLSearchParams();
    if (route.task) params.set('task', route.task);
    if (resultId) params.set('result', resultId);
    if (route.caseId) params.set('case', route.caseId);
    if (route.panel) params.set('panel', route.panel);
    if (route.view !== 'work') params.set('view', route.view);
    if (route.run) params.set('run', route.run);
    if (route.runCase) params.set('rcase', route.runCase);
    window.history.replaceState(null, '', window.location.pathname + '#' + params.toString());
  }, [route, resultId, boot]);
  useEffect(() => {
    if (!notice) return;
    const timer = setTimeout(() => setNotice(''), 6000);
    return () => clearTimeout(timer);
  }, [notice]);

  const createTask = async () => {
    if (!workspace) return;
    setBusy(true); setError('');
    try {
      const created = await api<Task>('/tasks', { workspace_id: workspace.id, title: taskTitle, goal, request_key: taskKey });
      await reload(); comparison.cancel(); planDrafts.deactivate(); taskActivation.current++; selectedRef.current = created.id;
      contexts.invalidate(); candidates.invalidateSource();
      setPlansTaskId(null); setComparisonOpen(false); setCandidateOpen(false); setMenu('none'); setSelectedHistory(null); lastPanel.current = 'scores'; setRoute({ task: created.id, result: null, caseId: null, panel: '', view: 'work', run: null, runCase: null });
      setNewTask(false); setTaskTitle(''); setGoal(''); setTaskKey(requestKey()); setNotice('任务已保存');
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  const importSource = async (source: Source) => {
    if (!task) return;
    const id = task.id;
    setBusy(true); setError('');
    try {
      const attached = await api<Result>(`/tasks/${id}/results`, { source_id: source.id });
      setRefresh(n => n + 1);
      if (selectedRef.current === id) updateRoute({ result: attached.id, caseId: null, panel: 'scores' });
      setNotice('真实结果已核验并接入任务');
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  // --- Agent chat -------------------------------------------------------
  const sessionOptions = (task?.sessions ?? []).slice().sort((a, b) => b.linked_at.localeCompare(a.linked_at)).map(saved => {
    const created = creations.records.find(record => record.agent_id === saved.agent_id && record.status === 'created');
    const agent = created?.envelope?.result.agent;
    const live = saved.id === session?.id && sessions.remote.status === 'available' ? sessions.remote : null;
    const provider = live?.agentProvider ?? agent?.provider;
    const model = live?.agentModel ?? agent?.model;
    // Paseo titles an Agent from its first prompt, i.e. the JSON message body;
    // the model is the stable label and the typed text is only a hint.
    const title = plainTitle(live?.agentTitle ?? agent?.title ?? null);
    const label = (saved.role ? roleLabels[saved.role] + ' · ' : '')
      + (provider ? [provider, model].filter(Boolean).join(' · ') : 'Agent ' + saved.agent_id.slice(0, 8));
    return { id: saved.id, label, selected: saved.id === session?.id,
      detail: [title, `${date(saved.linked_at)} 关联`, saved.agent_id.slice(0, 8)].filter(Boolean).join(' · ') };
  });
  const currentAgent = sessionOptions.find(option => option.selected);
  const binding = runViews.changes?.read?.binding;
  // New Agents from the menu are analysts: they work in the evaluation repository.
  // Implementers are created from an approved card on the Changes page.
  const cwdLabel = '工作目录为评测仓库，对 SN 只读；实现 Agent 在“更改”页从已批准的假设卡新建';
  const pageCurrent = sessions.page.status === 'ready' && sessions.page.freshness === 'current' && sessions.page.pendingAction === 'none';
  const candidateReason = (entry: SessionEntry): string | null => entry.candidateBlockedReason
    ?? (entry.truncated ? '正文已截断，不能作为候选来源' : !entry.text?.trim() ? '此条目没有可引用正文'
      : !pageCurrent || sessions.remote.status !== 'available' ? '对话正在刷新，稍后再试' : null);
  const messageText = contexts.message.text;
  const sending = useRef<{ taskId: string; draftId: string; text: string } | null>(null);
  const refill = useRef<{ taskId: string; fromDraft: string; text: string } | null>(null);
  const send = () => {
    if (!task || !session) return;
    sending.current = { taskId: task.id, draftId: messages.draftId, text: messageText };
    setChatError(null); stickToBottom.current = true;
    setOutgoing({ sessionId: session.id, text: messageText, attachments: contexts.message.attachments.length, state: 'sending',
      before: sessions.page.entries.map(entry => entry.id) });
    messages.send();
  };
  // A settled send frees the composer for the next message: an ACK starts a new
  // draft and keeps reading the reply; a server-confirmed pre-send refusal
  // starts a new draft with the same text so it can be resent.
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
  // Each new draft starts with what this session has not received yet, visible and
  // removable: its role brief, and (not for implementers) this Task's run summary.
  // "Received" is read from the server's acknowledged message records.
  const [sentTick, setSentTick] = useState(0);
  const sentRecords = useResource<AgentMessageRecord[]>(task && session ? `/tasks/${task.id}/agent-messages` : null, sentTick);
  // Records read before the latest send are stale: wait for the refetch.
  const [recordsAt, setRecordsAt] = useState(-1);
  useEffect(() => { if (sentRecords.data) setRecordsAt(sentTick); }, [sentRecords.data]);
  const autoTried = useRef(new Set<string>());
  useEffect(() => {
    if (!session || !sentRecords.data || recordsAt !== sentTick || messages.editingLocked || messages.status !== 'idle') return;
    const delivered = sentRecords.data.filter(r => r.session_id === session.id && r.status === 'acknowledged').flatMap(r => r.contexts);
    const lastSha = (kind: string) => delivered.filter(c => c.reference.kind === kind).at(-1)?.sha256 ?? null;
    const wanted = [
      { reference: { kind: 'brief' as const, session_id: session.id }, skipSha: lastSha('brief') },
      ...(session.role !== 'implementer' && taskRuns.length ? [{ reference: { kind: 'task_runs' as const }, skipSha: lastSha('task_runs') }] : []),
    ].filter(({ reference }) => {
      // The run catalog may arrive after the brief. Consume each kind only
      // when it is ready; a user-removed attachment stays removed in this draft.
      const key = `${messages.draftId}:${reference.kind}`;
      if (autoTried.current.has(key)) return false;
      autoTried.current.add(key);
      return true;
    });
    if (wanted.length) contexts.autoAttach(wanted);
  }, [session?.id, messages.draftId, messages.editingLocked, messages.status, Boolean(sentRecords.data), recordsAt, sentTick, taskRuns.map(run => run.id + run.state.phase).join()]);
  const shownOutgoing = outgoing && outgoing.sessionId === session?.id ? outgoing : null;
  const shownError = chatError && chatError.sessionId === session?.id ? chatError.text : null;
  const composerStatus: ComposerStatus | null = !task || !session ? null
    : messages.status === 'unknown' ? { text: '上一条消息的发送结果未确认，确认前不能再发。' + (messages.detail ? `（${messages.detail}）` : ''), tone: 'error',
      action: messages.checking ? undefined : { label: '核对', onPress: messages.check } }
    : shownError ? { text: shownError, tone: 'error' }
    : sessions.remote.status === 'loading' && !sessions.remote.agentStatusLabel ? { text: '正在连接 Agent…', tone: 'muted' }
    : sessions.remote.status === 'missing' || sessions.remote.status === 'unavailable' ? null
    : messages.blockedReason && !/正在取得消息写入权|当前意图已冻结/.test(messages.blockedReason) ? { text: messages.blockedReason, tone: 'muted' }
    : sessions.running ? { text: 'Agent 正在运行：现在发送会打断当前这一轮', tone: 'muted' } : null;
  const canSend = Boolean(session) && messages.blockedReason === null && messages.status === 'idle' && messageText.trim().length > 0;
  const createAgent = (profileId: string, role: 'analyst' | 'implementer' = 'analyst', hypothesisId?: string) => {
    if (creations.canNewIntent) creations.newIntent();
    creations.setTarget(role, hypothesisId);
    creations.panel?.onProfileSelect(profileId);
    creations.panel?.onCreate?.();
  };
  const implementers = Object.fromEntries((task?.sessions ?? []).filter(s => s.role === 'implementer' && s.hypothesis_id)
    .map(s => [s.hypothesis_id!, sessionOptions.find(o => o.id === s.id)?.label ?? s.agent_id.slice(0, 8)]));
  const [worktreePending, setWorktreePending] = useState(false);
  const canCreateWorktree = boot?.capabilities?.hypothesis_worktrees === true;
  const implementerBlocked = worktreePending ? '正在准备独立工作空间…' : !binding && !canCreateWorktree ? '本任务未绑定 SN worktree，不能新建实现 Agent（先在本页绑定）'
    : !creations.available ? '服务器未开放 Agent 创建' : creations.status === 'saving' ? '正在新建 Agent…' : null;
  const startImplementer = async (hypothesisId: string, profileId: string) => {
    if (!task || worktreePending) return;
    const taskId = task.id;
    setWorktreePending(true); setError('');
    try {
      if (canCreateWorktree) {
        const base = `/tasks/${taskId}/hypotheses/${hypothesisId}/worktree`;
        const result = await api<{ status: string }>(base, {});
        setRefresh(n => n + 1);
        if (result.status !== 'ready') throw new Error('工作空间创建结果未确认，请在假设卡核对；不会重复新建。');
        if (selectedRef.current !== taskId) return;
        await api(base + '/activate', {});
        setRefresh(n => n + 1);
      }
      if (selectedRef.current === taskId) createAgent(profileId, 'implementer', hypothesisId);
    } catch (e) { setError((e as Error).message); } finally { setWorktreePending(false); }
  };
  // Switch to a newly created Agent once its session is linked to the Task.
  const adopted = useRef(new Set<string>());
  const createdSession = creations.panel?.receipt?.sessionId;
  useEffect(() => {
    if (!createdSession || adopted.current.has(createdSession) || !task?.sessions.some(saved => saved.id === createdSession)) return;
    adopted.current.add(createdSession); contexts.invalidate(); candidates.invalidateSource(); sessions.selectSession(createdSession); setMenu('none');
    updateRoute({ view: 'work' });
  }, [createdSession, task?.sessions.length]);
  const creationStatus = creations.status === 'saving' ? { text: '正在新建 Agent…', tone: 'muted' as const }
    : creations.status === 'unknown' ? { text: '新建结果尚未确认' + (creations.panel?.detail ? `：${creations.panel.detail}` : ''), tone: 'error' as const,
      action: creations.panel?.onCheck ? { label: creations.checking ? '核对中…' : '核对', onPress: creations.panel.onCheck } : undefined }
    : creations.status === 'error' || creations.status === 'not_attempted' ? { text: '新建失败' + (creations.panel?.detail ? `：${creations.panel.detail}` : ''), tone: 'error' as const }
    : null;
  // Case evidence panels open the materials menu with that case selected and previewed.
  const attachOption = (prefix: string) => {
    const option = contexts.selection.options.find(item => item.id.startsWith(prefix));
    if (!option) return;
    updateRoute({ view: 'work' }); setGlobalRunsOpen(false); setMenu('context');
    contexts.selection.onSelectOption(option.id);
  };
  const attachCase = () => attachOption('["case"');
  const attachRunCase = () => attachOption('["run_case"');
  const openContext = (id: string) => { setMenu('context'); contexts.open(id); };
  const viewTask = (id: string) => {
    comparison.cancel(); planDrafts.deactivate(); setPlansTaskId(null); setComparisonOpen(false); setCandidateOpen(false); setMenu('none'); setSelectedHistory(null); lastPanel.current = 'scores';
    taskActivation.current++; selectedRef.current = id;
    contexts.invalidate();
    candidates.invalidateSource();
    setRoute({ task: id, result: null, caseId: null, panel: '', view: 'work', run: null, runCase: null }); setNav(false); setError('');
  };

  const dock = layout.sizes(Boolean(route.panel));
  const sidebar = <WorkspaceSidebar width={compact ? Math.min(320, width - 40) : dock.left} compact={compact}
    workspaces={boot?.workspaces ?? []} taskId={route.task} overview={runViews.overview} onTask={viewTask}
    onNewTask={() => { setNewTask(true); setNav(false); }} onClose={() => compact ? setNav(false) : layout.toggleLeft()}
    onSettings={() => { setSettingsOpen(true); setNav(false); }}
    onRuns={() => { setGlobalRunsOpen(true); setNav(false); }} />;
  return <ThemeContext.Provider value={theme}>
    <View style={{ flex: 1, backgroundColor: theme.bg, flexDirection: 'row' }}>
      {auth ? <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center', padding: 24 }}><View style={{ width: '100%', maxWidth: 380, gap: 18 }}>
        <Copy style={s.heading}>Silicon Notebook 工作台</Copy><Copy muted>使用启动时的访问链接或访问码打开已保存材料</Copy>
        <Input label="工作台访问码" value={access} onChange={setAccess} />
        <Button label="打开工作台" primary onPress={async () => {
          try { await api('/session', { token: access }); await reload(); setError(''); } catch (e) { setError((e as Error).message); }
        }} /><Copy small style={{ color: theme.error }}>{error}</Copy>
      </View></View> : !boot ? <View style={{ padding: 32 }}><Copy muted>正在读取工作台…</Copy></View> : <>
        {!compact && layout.leftOpen && <View style={{ position: 'relative', width: dock.left }}>{sidebar}<ResizeHandle side="left" width={dock.left} onResize={n => layout.resize('left', n)} /></View>}
        <View testID="work-surface" style={{ flex: 1, minWidth: 0 }}>
          <View style={{ height: compact ? 48 : 44, borderBottomWidth: 1, borderColor: theme.line, paddingHorizontal: 8, flexDirection: 'row', alignItems: 'center', gap: 8 }}>
            {(compact || !layout.leftOpen) && <IconButton label="展开工作区侧栏" icon={PanelLeftOpen} onPress={() => compact ? setNav(n => !n) : layout.toggleLeft()} testID="toggle-left-sidebar" />}
            <Text numberOfLines={1} style={{ flexShrink: 1, minWidth: 60, maxWidth: compact ? 140 : 320, fontSize: 14, fontWeight: '500', color: theme.text }}>{globalRunsOpen ? '全局 run 目录' : task?.title ?? 'SN 评测研发'}</Text>
            {!globalRunsOpen && <TaskViewNav view={route.view} onSelect={view => { setGlobalRunsOpen(false); selectView(view); }} disabled={!currentTask} />}
            <View style={{ flex: 1 }} />
            {(compact || !layout.leftOpen) && <IconButton label="工作区设置" icon={Settings} onPress={() => setSettingsOpen(true)} testID="open-settings" />}
            <InterpretationButton onPress={() => setInterpretationOpen(true)} />
            <IconButton label={route.panel ? '收起材料侧栏' : '展开材料侧栏'} icon={route.panel ? PanelRightClose : PanelRightOpen} onPress={toggleMaterials} selected={Boolean(route.panel)} testID="toggle-right-sidebar" />
          </View>
          {(error || taskState.error || resultState.error) && <View style={{ padding: 12, borderBottomWidth: 1, borderColor: theme.line, gap: 4 }}>
            <Copy small style={{ color: theme.error }}>{error || taskState.error || resultState.error}</Copy><Button label="重新读取记录" onPress={() => { setRefresh(n => n + 1); void reload().catch(e => setError(e.message)); }} />
          </View>}
          <ScrollView ref={scrollRef} style={{ flex: 1 }} contentContainerStyle={{ padding: compact ? 18 : 32 }} scrollEventThrottle={100}
            onScroll={event => { const { contentOffset, contentSize, layoutMeasurement } = event.nativeEvent;
              stickToBottom.current = contentSize.height - contentOffset.y - layoutMeasurement.height < 120; }}
            onContentSizeChange={() => { if (route.view === 'work' && !globalRunsOpen && stickToBottom.current) scrollRef.current?.scrollToEnd({ animated: false }); }}>
            <View style={{ width: '100%', maxWidth: 820, alignSelf: 'center', gap: 24 }}>
              {(!task || globalRunsOpen) && <Block title="全局 run 目录">
                {task && <View style={s.row}><Button label="← 返回任务" onPress={() => setGlobalRunsOpen(false)} testID="toggle-global-runs" /></View>}
                <View testID="global-run-catalog" style={{ gap: 10 }}>
                  {runViews.runs.map(run => <RunCard key={run.id} run={run}
                    onCases={needsRead => openRun(run.id, 'run-cases', { needsRead })}
                    onFlips={() => openRun(run.id, 'run-flips')}
                    onProvenance={() => openRun(run.id, 'run-cases', { provenance: true })} />)}
                  {!runViews.runs.length && <Copy small muted>{runViews.runsError ? 'run 目录暂不可用' : '尚无已登记的 run'}</Copy>}
                </View>
              </Block>}
              {globalRunsOpen && task ? null : !task ? <Block title="从已有结果开始"><Copy muted>任务保存目标和证据关系。先读真实结果，再记录判断与下一步</Copy><Button label="建立第一个任务" icon={Plus} primary onPress={() => setNewTask(true)} /></Block> : <>
                <View testID="task-view-work" style={{ display: route.view === 'work' ? 'flex' : 'none', gap: 20 }}>
                  <View testID="task-timeline" style={{ gap: 10 }}>
                    {!!task.goal && <Text testID="task-goal-line" numberOfLines={3} style={{ fontSize: 13, lineHeight: 20, color: theme.muted }}>目标：{task.goal}</Text>}
                    {taskRuns.map((run, index) => <RunCard key={run.id} run={run} defaultOpen={index === taskRuns.length - 1}
                      onCases={needsRead => openRun(run.id, 'run-cases', { needsRead })} onFlips={() => openRun(run.id, 'run-flips')}
                      onProvenance={() => openRun(run.id, route.panel === 'run-flips' ? 'run-flips' : 'run-cases', { provenance: true })} />)}
                    {!taskRuns.length && <View style={s.row} testID="task-runs-empty">
                      <Copy small muted>{runViews.runsError ? `run 列表读取失败：${runViews.runsError}` : runViews.loading ? '正在读取评测运行…' : '此任务还没有评测运行。在“更改”页选择计划并运行。'}</Copy>
                      {runViews.runsError && <Button label="重试" onPress={() => setRefresh(n => n + 1)} />}
                    </View>}
                    {runViews.changes?.read && <DiffCard changes={runViews.changes} onOpen={() => selectView('changes')} />}
                    {taskRuns.filter(run => run.attention.count).map(run => <DecisionRow key={run.id} testID="read-row" tone="read"
                      text={`${run.attention.count} 题待读（${attentionRuleLabel(run.attention.rule)}）· 首题 ${caseLabel(run.attention.case_ids[0])}`}
                      actions={[{ label: '读首题证据', primary: true, testID: 'decision-open-first', onPress: () => openRun(run.id, 'run-cases', { needsRead: true, caseId: run.attention.case_ids[0] }) },
                        { label: '全部待读', onPress: () => openRun(run.id, 'run-cases', { needsRead: true }) }]} />)}
                    {!!taskOverview?.pending_hypotheses && <DecisionRow testID="decision-hypotheses"
                      text={`${taskOverview.pending_hypotheses} 张假设卡等你批准或下结论`}
                      actions={[{ label: '查看假设卡', primary: true, onPress: () => selectView('changes') }]} />}
                    {!!taskOverview && taskOverview.pending - (taskOverview.pending_hypotheses ?? 0) > 0 && <DecisionRow testID="decision-row"
                      text={`${taskOverview.pending - (taskOverview.pending_hypotheses ?? 0)} 条候选或人工判断还未决定`}
                      actions={[{ label: '打开记录', primary: true, testID: 'decision-open-records', onPress: () => selectView('records') }]} />}
                  </View>
                  <TimelineLabel>{currentAgent ? '与 ' + currentAgent.label + ' 的对话' : 'Agent 对话'}</TimelineLabel>
                  <AgentChat hasSession={Boolean(session)} remote={sessions.remote} page={sessions.page} viewingTail={sessions.viewingTail}
                    outgoing={shownOutgoing} running={sessions.running} candidateReason={candidateReason}
                    onOlder={sessions.older ? () => { candidates.invalidateSource(); sessions.older?.(); } : undefined}
                    onLatest={() => { candidates.invalidateSource(); sessions.refresh(); }}
                    onCandidate={(id, kind) => { candidates.requestCandidate(id, kind); setCandidateOpen(true); }}
                    empty={<View style={{ gap: 8, alignItems: 'flex-start' }}>
                      <Copy muted>这个任务还没有 Agent。先新建一个分析 Agent：它在评测仓库里工作，能用 sn-eval 工具自己查运行、案例和文档，读错题后提交假设卡草稿。你在“更改”页批准假设卡后，再为它新建实现 Agent，实现 Agent 在 SN worktree 里改代码，看不到考题和 gold。</Copy>
                      <Copy small muted>第一条消息会自动附上这个 Agent 的工作说明和本任务的运行摘要（输入框里可见，可移除）。</Copy>
                      <Button label="新建分析 Agent" primary icon={Plus} onPress={() => setMenu('agent')} testID="chat-new-agent" />
                    </View>} />
                  {candidates.draftPanel && <View style={{ gap: 8 }}>
                    {!candidateOpen && <Button label="继续候选草稿" testID="candidate-draft-reopen" onPress={() => setCandidateOpen(true)} />}
                    <View style={{ display: candidateOpen ? 'flex' : 'none' }}><CandidateDraftPanel compact={compact} {...candidates.draftPanel} closeLabel="收起草稿" onCancel={() => setCandidateOpen(false)} /></View>
                    {candidates.checkCandidateReceipt && <Button label="核对候选保存回执" testID="candidate-check-receipt"
                      onPress={candidates.checkCandidateReceipt} disabled={candidates.checkingCandidate} />}
                  </View>}
                </View>
                {currentTask && <View testID="task-view-changes" style={{ display: route.view === 'changes' ? 'flex' : 'none', gap: 16 }}>
                  <ChangesPage key={currentTask.id} onRefresh={() => setRefresh(n => n + 1)} taskId={currentTask.id} changes={runViews.changes} error={runViews.changesError}
                    executionWorktrees={boot.execution_worktrees ?? []}
                    onOpenRun={id => openRun(id, 'run-cases', { needsRead: true })}
                    hypotheses={<HypothesesPanel taskId={currentTask.id} runs={taskRuns.map(run => ({ id: run.id, title: run.title }))}
                      profiles={creations.profiles.map(p => ({ id: p.id, label: `${p.providerLabel} · ${p.modelLabel}` }))}
                      implementers={implementers} canCreateImplementer={implementerBlocked}
                      canCreateWorktree={canCreateWorktree} activeWorktree={binding?.worktree_path}
                      onWorktreeChanged={() => setRefresh(n => n + 1)} onStartImplementer={startImplementer}
                      onOpenCase={(runId, caseId) => openRun(runId, 'run-cases', { caseId })} refreshKey={refresh} />} />
                </View>}
                <View testID="task-view-results" style={{ display: route.view === 'results' ? 'flex' : 'none', gap: 16 }}>
                  <View style={s.row}>
                    <Button label="比较结果" testID="open-comparison" selected={comparisonOpen} disabled={!currentTask} onPress={() => setComparisonOpen(open => !open)} />
                    <Button label="完整成绩" icon={ClipboardList} onPress={() => updateRoute({ panel: 'scores' })} disabled={!resultId} />
                    <Button label="查看案例" icon={BookOpen} onPress={() => updateRoute({ panel: 'cases', caseId: null })} disabled={!resultId} testID="open-cases" />
                  </View>
                  {currentTask && <View style={{ display: comparisonOpen ? 'flex' : 'none' }}><ComparisonPanel results={currentTask.results}
                  leftId={comparison.leftId} rightId={comparison.rightId} onSelect={comparison.select} onRead={comparison.read}
                  busy={comparison.busy} error={comparison.error} data={comparison.data}
                  onOpenResult={id => {
                    const latest = comparisonScope.current;
                    if (taskActivation.current === activation && selectedRef.current === currentTask.id && latest.task?.id === currentTask.id
                      && latest.generation === comparison.generation && latest.task.results.some(result => result.id === id))
                      updateRoute({ result: id, caseId: null, panel: 'scores' });
                  }} /></View>}
                {task.results.map(r => <View key={r.id} style={[s.card, { borderColor: theme.line }]} testID="result-card">
                  <View style={[s.row, { justifyContent: 'space-between' }]}><Copy style={{ fontWeight: '600' }}>{r.suite.toUpperCase()} · {r.method.name}</Copy><Copy small muted>{r.method.kind === 'sn' ? 'SN 答卷' : '公开方法答卷'}</Copy></View>
                  <Copy small muted>{r.case_count} / {r.bundle_case_count} 题 · 实验时间未知 · {r.scope === 'full' ? '冻结范围全量' : '显式子集'}</Copy>
                  <View style={s.row}>{Object.entries(r.metrics).slice(0, 3).map(([name, m]) => <View key={name} style={{ flex: 1, minWidth: 100, gap: 4 }}><Copy small muted>{name}</Copy><Copy style={{ fontSize: 18, fontVariant: ['tabular-nums'] }}>{m.value.toFixed(4)} <Text style={{ fontSize: 11, color: theme.muted }}>/ {m.denominator}</Text></Copy></View>)}</View>
                  <Copy small muted>{r.pending_metrics.length ? `${r.pending_metrics.length} 项评分缺口，见完整成绩` : '该批次未声明 pending 指标'}</Copy>
                  <View style={s.row}><Button label="完整成绩与分母" onPress={() => updateRoute({ result: r.id, caseId: null, panel: 'scores' })} /><Button label="阅读案例证据" icon={BookOpen} onPress={() => updateRoute({ result: r.id, caseId: null, panel: 'cases' })} /><Button label="来源" icon={FileText} onPress={() => updateRoute({ result: r.id, caseId: null, panel: 'source' })} /></View>
                </View>)}
                  <Block title="接入已有结果">
                    {boot.sources.map(source => <View key={source.id} style={{ gap: 4, paddingVertical: 8 }}>
                      <Copy>{source.title}</Copy><Copy small muted>{source.description}</Copy><Button label={busy ? '接入中…' : '接入当前任务'} onPress={() => importSource(source)} disabled={busy} testID={'resource-import-' + source.id} />
                    </View>)}
                  </Block>
                </View>
                {currentTask && <View testID="task-view-plans" style={{ display: route.view === 'plans' ? 'flex' : 'none', gap: 16 }}>
                  <PlanDraftEditor controller={planDrafts} />
                  <PlanDraftHistory records={planDrafts.heads} selected={planDrafts.selected}
                    onSelect={planDrafts.openRevision} disabled={planDrafts.locked} loading={planDrafts.headsLoading} error={planDrafts.listError} />
                  <View style={{ gap: 8 }}>
                    <Button label="刷新草稿列表" onPress={planDrafts.refreshHeads} disabled={planDrafts.busy || planDrafts.headsLoading} testID="plans-refresh" />
                    {planDrafts.heads.map(record => <Button key={record.plan_id}
                      label={`继续编辑最新版 · ${record.plan_id.slice(0, 8)}`} disabled={planDrafts.locked}
                      onPress={() => planDrafts.openHead(record.plan_id)} testID={`plan-head-${record.plan_id}`} />)}
                    {planDrafts.revisions.length > 0 && <Copy small muted>打开过的历史版本（按原样打开，不会换成最新版）</Copy>}
                    {planDrafts.revisions.map(record => <Button key={`${record.planId}-${record.revision}`}
                      label={`历史版本 · ${record.revision.slice(0, 8)}`} disabled={planDrafts.locked}
                      onPress={() => planDrafts.openRevision(record.planId, record.revision)} testID={`plan-revision-${record.revision}`} />)}
                  </View>
                </View>}
                <View testID="task-view-records" style={{ display: route.view === 'records' ? 'flex' : 'none', gap: 16 }}>
                <View style={{ gap: 8 }}>
                  <CandidateRecordsPanel compact={compact} {...candidates.recordsPanel} />
                  {candidates.checkDecisionReceipt && <Button label="核对人工判断回执" testID="candidate-decision-check-receipt"
                    onPress={candidates.checkDecisionReceipt} disabled={candidates.checkingDecision} />}
                </View>
                  <Block title="当前任务记录">
                    {visibleHistory(task.history).map(item => <View key={item.seq} style={{ gap: 4, paddingVertical: 8, borderBottomWidth: 1, borderColor: theme.line }}>
                      <Copy small muted>{date(item.registered_at)}</Copy>
                      <Button label={historyLabel(item)}
                        testID={item.kind === 'review.saved' ? 'history-review' : `history-record-${item.seq}`}
                        onPress={() => { setSelectedHistory({ taskId: task.id, item: structuredClone(item) }); updateRoute({ panel: 'history' }); }} />
                      {item.kind === 'review.saved' && <Button label="打开判断对应案例" onPress={() => updateRoute({ result: String(item.content.result_id), caseId: String(item.content.case_id), panel: 'cases' })} />}
                      {item.kind === 'note.saved' && item.content.context != null && <Button label="打开笔记引用的案例" onPress={() => { const context = item.content.context as { result_id: string; case_id: string }; updateRoute({ result: context.result_id, caseId: context.case_id, panel: 'cases' }); }} />}
                    </View>)}
                    {!task.history.length && <Copy small muted>尚无保存记录</Copy>}
                    <Button label="导出任务与修订历史" icon={FileText} testID="export-task-records" onPress={() => Linking.openURL('/api/tasks/' + task.id + '/export')} />
                  </Block>
                </View>
              </>}
            </View>
          </ScrollView>
          {task && route.view === 'work' && !globalRunsOpen && <ChatComposer compact={compact} value={messageText}
            onChange={contexts.message.onChange} editable={Boolean(session) && !messages.editingLocked} canSend={canSend} onSend={send}
            placeholder={session ? '给 Agent 发消息（Enter 发送，Shift+Enter 换行）' : '先选择或新建一个 Agent'}
            attachments={contexts.message.attachments.map(item => ({ id: item.id, label: item.title }))}
            onOpenAttachment={openContext} onRemoveAttachment={contexts.message.onRemoveReference}
            attachOpen={menu === 'context'} onToggleAttach={() => setMenu(open => open === 'context' ? 'none' : 'context')}
            attachEnabled={Boolean(session) && !messages.editingLocked}
            agentButton={<AgentButton label={currentAgent?.label ?? '选择 Agent'} open={menu === 'agent'}
              status={session && sessions.remote.status === 'available' ? sessions.remote.agentStatusLabel : undefined}
              onPress={() => setMenu(open => open === 'agent' ? 'none' : 'agent')} />}
            status={composerStatus}
            popover={menu === 'agent' ? <AgentMenu agents={sessionOptions}
              onSelect={id => { if (id !== session?.id) { contexts.invalidate(); candidates.invalidateSource(); sessions.selectSession(id); setChatError(null); } }}
              profiles={creations.profiles.map(profile => ({ id: profile.id, label: `${profile.providerLabel} · ${profile.modelLabel}`,
                detail: [profile.effortLabel && '思考 ' + profile.effortLabel, profile.modeLabel && '模式 ' + profile.modeLabel].filter(Boolean).join(' · ') || profile.id }))}
              createEnabled={creations.available && creations.status !== 'saving' && creations.status !== 'unknown' && !creations.checking
                && (creations.panel?.blockedReason == null || creations.canNewIntent)}
              createReason={!creations.available ? '服务器未开放 Agent 创建'
                : creations.panel?.blockedReason && !creations.canNewIntent ? creations.panel.blockedReason : null}
              onCreate={createAgent} creation={creationStatus}
              link={{ value: sessions.link.value, onChange: sessions.link.onChange, onSubmit: sessions.link.onRequestLink,
                statusText: sessions.link.status === 'linking' ? '关联中…' : sessions.link.status === 'linked' ? '已关联' : sessions.link.status === 'error' ? '关联失败：' + (sessions.link.detail ?? '') : null }}
              cwdLabel={cwdLabel} onClose={() => setMenu('none')} />
              : menu === 'context' ? <ContextMenu mode={contexts.selection.mode} onModeChange={contexts.selection.onModeChange}
                options={contexts.selection.options} selectedId={contexts.selection.selectedOptionId} onSelect={contexts.selection.onSelectOption}
                path={contexts.selection.pathValue} onPathChange={contexts.selection.onPathChange} onPreview={contexts.requestPreview}
                preview={contexts.preview} addBlockedReason={contexts.addBlockedReason} onAdd={contexts.add} onClose={() => setMenu('none')} />
              : null} />}
        </View>
        <View testID="workspace-materials" style={compact ? { position: 'absolute', zIndex: 10, top: 0, bottom: 0, left: 0, right: 0, display: route.panel ? 'flex' : 'none' } : { position: 'relative', width: dock.right, display: route.panel ? 'flex' : 'none', borderLeftWidth: 1, borderColor: theme.line }}>
          {!compact && <ResizeHandle side="right" width={dock.right} onResize={n => layout.resize('right', n)} />}
          {runSelection && route.panel.startsWith('run-') ? <RunMaterials runs={runViews.runs} selection={runSelection} compact={compact}
            onClose={() => updateRoute({ panel: '' })} onAttachCase={session && session.role !== 'implementer' ? attachRunCase : undefined}
            onChange={changes => { const { runId, caseId, panel, ...filter } = changes;
              setRunFilter(prev => ({ ...prev, ...filter }));
              updateRoute({ ...(runId !== undefined ? { run: runId } : {}), ...(caseId !== undefined ? { runCase: caseId } : {}), ...(panel ? { panel } : {}) }); }} />
          : <Materials panel={route.panel} task={task} result={result} caseId={route.caseId} actor={boot.actor} selectedHistory={historyItem} compact={compact}
            onClose={() => updateRoute({ panel: '' })} onPanel={panel => updateRoute({ panel })} onSelect={caseId => updateRoute({ caseId: caseId || null })}
            onSaved={() => setRefresh(n => n + 1)} onAttach={attachCase} />}
        </View>
        {compact && nav && <View style={{ position: 'absolute', zIndex: 20, top: 0, bottom: 0, left: 0, right: 0 }}>
          <Pressable accessibilityRole="button" accessibilityLabel="关闭工作区导航" onPress={() => setNav(false)} style={{ position: 'absolute', top: 0, bottom: 0, left: 0, right: 0, backgroundColor: '#0005' }} />{sidebar}
        </View>}
        {notice && <View accessibilityRole="alert" style={{ position: 'absolute', bottom: 14, alignSelf: 'center', left: compact ? 12 : dock.left + 12, right: 12, alignItems: 'center', pointerEvents: 'none' }}><View style={{ backgroundColor: theme.text, padding: 12, borderRadius: 6 }}><Text style={{ color: theme.bg, fontSize: 12 }}>{notice}</Text></View></View>}
      </>}
    </View>
    <Interpretation visible={interpretationOpen} onClose={() => setInterpretationOpen(false)} />
    <WorkspaceConfiguration visible={settingsOpen} workspace={workspace} actor={boot?.actor ?? ''} capabilities={boot?.capabilities}
      profiles={boot?.creation_profiles ?? []} inputs={boot?.inputs ?? []} theme={dark ? 'dark' : 'light'} onThemeChange={value => setDark(value === 'dark')} onClose={() => setSettingsOpen(false)} />
    <Modal visible={newTask} transparent animationType="fade" onRequestClose={() => setNewTask(false)}>
      <View style={{ flex: 1, backgroundColor: '#0007', alignItems: 'center', justifyContent: 'center', padding: 24 }}><View style={{ backgroundColor: theme.bg, width: '100%', maxWidth: 480, padding: 24, borderRadius: 12, gap: 16 }}>
        <Copy style={s.heading}>建立持续任务</Copy><Copy small muted>{workspace?.title} · 目标与证据跨会话保存</Copy>
        <Input label="任务标题" value={taskTitle} onChange={v => { setTaskTitle(v); setTaskKey(requestKey()); }} placeholder="例如：调查会议总结的决定遗漏" testID="task-title" />
        <Input label="任务目标" value={goal} onChange={v => { setGoal(v); setTaskKey(requestKey()); }} multiline placeholder="写下希望核对的问题和下一步" testID="task-goal" />
        {error && <Copy small style={{ color: theme.error }}>{error}</Copy>}
        <View style={[s.row, { justifyContent: 'flex-end' }]}><Button label="关闭" onPress={() => setNewTask(false)} disabled={busy} /><Button label={busy ? '保存中…' : '建立任务'} onPress={createTask} disabled={busy || !taskTitle.trim()} primary testID="create-task" /></View>
      </View></View>
    </Modal>
  </ThemeContext.Provider>;
}
