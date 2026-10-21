import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Platform, Pressable, ScrollView, Text, useWindowDimensions, View } from 'react-native';
import { PanelLeftOpen, X } from 'lucide-react-native';
import { api, Bootstrap, date, SessionRemoval, Task, useResource } from './contracts';
import { Button, Copy, Input, ThemeContext, themes } from './ui';
import { IconButton, ResizeHandle, useShellLayout } from './shell';
import { WorkspaceConfiguration } from './workspace/WorkspaceConfiguration';
import { ExecutionCases, RunMaterials } from './runs/RunMaterials';
import type { RunList } from './runs/contracts';
import { ActionRequest, Candidate, Iteration, IterationTree, Proposal, short, verbLabels } from './tree/contracts';
import { makeActions, treeLabels } from './tree/actions';
import { ConfirmDialog, ConfirmSpec, Mono } from './tree/parts';
import { IterationSidebar, IterationStatus, Todo } from './tree/IterationSidebar';
import { Lineage } from './tree/Lineage';
import { CandidateDetail, ProposalDetail } from './tree/NodeDetail';
import { CompareMatrix } from './tree/CompareMatrix';
import { CommandPalette, PaletteItem } from './tree/CommandPalette';
import { NewIterationDialog } from './tree/NewIterationDialog';
import { AgentDrawer, AgentTarget } from './tree/AgentDrawer';
import { agentsOf, analystsOf, iterationAnalystsOf, stepsOf } from './tree/next';
import { useLive } from './tree/useLive';

// Project → iteration → candidate tree (design §7). The five-tab Task surface was
// removed on 2026-10-20 (DELETION_LOG.md); backend routes are unchanged.
type Route = { iteration: string | null; node: string | null; proposal: string | null; matrix: boolean; rank: boolean };
function initialRoute(): Route {
  const hash = Platform.OS === 'web' ? new URLSearchParams(window.location.hash.slice(1)) : new URLSearchParams();
  return { iteration: hash.get('it'), node: hash.get('node'), proposal: hash.get('proposal'), matrix: hash.get('view') === 'matrix', rank: hash.get('rank') === 'root' };
}

export default function App() {
  const { width, height } = useWindowDimensions();
  const layout = useShellLayout(width);
  const compact = layout.compact;
  const [dark, setDark] = useState(() => Platform.OS === 'web' && window.matchMedia?.('(prefers-color-scheme: dark)').matches);
  const theme = dark ? themes.dark : themes.light;
  const [route, setRoute] = useState<Route>(initialRoute);
  const update = (changes: Partial<Route>) => setRoute(prev => ({ ...prev, ...changes }));
  const [boot, setBoot] = useState<Bootstrap>();
  const [auth, setAuth] = useState(false);
  const [access, setAccess] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [refresh, setRefresh] = useState(0);
  const bump = () => setRefresh(n => n + 1);
  const [nav, setNav] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [newIteration, setNewIteration] = useState(false);
  const [palette, setPalette] = useState(false);
  const [confirm, setConfirm] = useState<ConfirmSpec | null>(null);
  const [picked, setPicked] = useState<string[]>([]);
  const [reader, setReader] = useState<{ runId: string; caseId: string | null; execution?: string } | null>(null);
  const [drawer, setDrawer] = useState(false);
  const [target, setTarget] = useState<AgentTarget | null>(null);
  const [focus, setFocus] = useState<{ sessionId: string; nonce: number } | null>(null);

  const iterations = useResource<Iteration[]>(boot ? '/iterations' : null, refresh);
  const iterationId = route.iteration ?? iterations.data?.at(-1)?.id ?? null;
  const treeState = useResource<IterationTree>(boot && iterationId ? `/iterations/${iterationId}` : null, refresh);
  const tree = treeState.data?.id === iterationId ? treeState.data : undefined;
  const proposals = useResource<Proposal[]>(tree ? `/tasks/${tree.task_id}/hypotheses` : null, refresh);
  const requests = useResource<ActionRequest[]>(boot ? '/action-requests' : null, refresh);
  const taskState = useResource<Task>(tree ? `/tasks/${tree.task_id}` : null, refresh);
  const runList = useResource<RunList>(reader && !reader.execution ? '/run-views/runs' : null, refresh);
  const labels = useMemo(() => tree ? treeLabels(tree) : {}, [tree]);
  const label = (id: string | null) => id ? labels[id] ?? short(id) : '—';
  const proposalById = Object.fromEntries((proposals.data ?? []).map(p => [p.id, p]));
  const titles = Object.fromEntries((tree?.candidates ?? []).map(c => [c.id, c.id === tree?.root_candidate_id ? '' : (c.proposal_id && proposalById[c.proposal_id]?.card.title) || c.note || '']));
  const actions = tree ? makeActions(tree, label) : null;
  const candidate = tree?.candidates.find(c => c.id === route.node);
  const proposal = route.proposal ? proposalById[route.proposal] : undefined;
  const allRequests = requests.data ?? [];
  const treeRequests = tree ? allRequests.filter(r => r.task_id === tree.task_id) : [];
  const task = taskState.data?.id === tree?.task_id ? taskState.data : undefined;
  const { live, changes, ready: liveReady } = useLive(task, tree, refresh);
  const steps = tree && task && liveReady && proposals.data ? stepsOf({ tree, proposals: proposalById, requests: treeRequests, sessions: task?.sessions ?? [], live, changes }) : {};
  const agentMarks = Object.fromEntries((tree?.candidates ?? []).map(c => [c.id, agentsOf(c.id, task?.sessions ?? []).map(s => ({ sessionId: s.id, role: s.role!, status: live[s.id]?.status ?? null }))]));
  const openAgent = (sessionId: string) => { setTarget(null); setFocus({ sessionId, nonce: Date.now() }); setDrawer(true); };
  // Analysis is per node: continue the node's latest analyst, or create one bound to it.
  const newAnalyst = (c: Candidate) => { setTarget({ role: 'analyst', candidateId: c.id, label: label(c.id) }); setDrawer(true); };
  const analyse = (c: Candidate) => {
    const bound = analystsOf(c.id, task?.sessions ?? []).at(-1);
    if (bound) openAgent(bound.id); else newAnalyst(c);
  };
  const iterationAgent = iterationAnalystsOf(task?.sessions ?? []).at(-1)?.id ?? null;
  const evaluatedCount = tree?.candidates.filter(c => c.state === 'evaluated').length ?? 0;
  // Removal hides the session everywhere; the Paseo archive is best effort.
  const removeAgent = async (sessionId: string) => {
    if (!task) return;
    const removed = await api<SessionRemoval>(`/tasks/${task.id}/sessions/${sessionId}/remove`, {});
    bump();
    setNotice(removed.archive.status === 'unavailable'
      ? `已从工作台移除；Paseo 归档失败：${removed.archive.code ?? '未知'}，可在 Paseo 中手动归档` : 'Agent 已从工作台移除');
  };

  // Poll while something moves: a run in flight or a request being executed.
  const moving = Boolean(tree?.candidates.some(c => c.state === 'running' || c.state === 'provisioning') || allRequests.some(r => r.status === 'confirmed'));
  useEffect(() => {
    if (!moving) return;
    const timer = setInterval(bump, 4000);
    return () => clearInterval(timer);
  }, [moving]);
  const reload = async () => { const data = await api<Bootstrap>('/bootstrap'); setBoot(data); setAuth(false); };
  useEffect(() => {
    let active = true;
    void (async () => {
      try {
        const token = Platform.OS === 'web' ? new URLSearchParams(window.location.hash.slice(1)).get('access') : null;
        if (token) { await api('/session', { token }); window.history.replaceState(null, '', window.location.pathname); }
        if (active) await reload();
      } catch (e) { if (active) { setAuth(true); setError((e as Error).message); } }
    })();
    return () => { active = false; };
  }, []);
  useEffect(() => {
    if (!boot || Platform.OS !== 'web') return;
    const params = new URLSearchParams();
    if (iterationId) params.set('it', iterationId);
    if (route.node) params.set('node', route.node);
    if (route.proposal) params.set('proposal', route.proposal);
    if (route.matrix) params.set('view', 'matrix');
    if (route.rank) params.set('rank', 'root');
    window.history.replaceState(null, '', window.location.pathname + '#' + params.toString());
  }, [route, iterationId, boot]);
  useEffect(() => { if (!notice) return; const t = setTimeout(() => setNotice(''), 6000); return () => clearTimeout(t); }, [notice]);
  useEffect(() => {
    if (Platform.OS !== 'web') return;
    const shortcut = (event: KeyboardEvent) => {
      if (!(event.ctrlKey || event.metaKey) || event.altKey || event.shiftKey) return;
      const key = event.key.toLowerCase();
      if (key === 'k') { event.preventDefault(); setPalette(p => !p); }
      if (key === 'b') { event.preventDefault(); if (compact) setNav(n => !n); else layout.toggleLeft(); }
      if (key === 'j') { event.preventDefault(); setDrawer(d => !d); }
    };
    window.addEventListener('keydown', shortcut);
    return () => window.removeEventListener('keydown', shortcut);
  }, [compact, layout.toggleLeft]);

  const selectIteration = (id: string) => { setPicked([]); setReader(null); setTarget(null); setRoute({ iteration: id, node: null, proposal: null, matrix: false, rank: false }); setNav(false); };
  const selectNode = (id: string) => { setReader(null); update({ node: id, proposal: null }); };
  const openRequest = (r: ActionRequest) => {
    const it = iterations.data?.find(i => i.task_id === r.task_id);
    if (r.target_id && it) { if (it.id !== iterationId) selectIteration(it.id); setReader(null); update({ iteration: it.id, node: r.target_id, proposal: null }); }
    else if (actions) setConfirm(actions.confirmRequest(r));
    setNav(false);
  };
  // Everything waiting on the person: next steps in this iteration, then requests elsewhere.
  const todo: Todo[] = [
    ...(tree?.candidates ?? []).flatMap(c => {
      const step = steps[c.id];
      if (step?.who !== 'you') return [];
      return [{ id: 'step-' + c.id, rank: step.rank, label: `${label(c.id)} · ${step.text}`, detail: tree!.title,
        kind: step.action === 'request' ? 'request' as const : step.action === 'proposal' ? 'proposal' as const : 'step' as const,
        onPress: () => { setReader(null); setNav(false); step.action === 'proposal' && step.proposalId ? update({ proposal: step.proposalId, node: null }) : update({ node: c.id, proposal: null }); } }];
    }).sort((a, b) => a.rank - b.rank),
    ...allRequests.filter(r => r.status === 'pending' && (r.task_id !== tree?.task_id || !r.target_id)).map(r => ({ id: 'request-' + r.id,
      label: `${verbLabels[r.verb] ?? r.verb}请求`, detail: iterations.data?.find(i => i.task_id === r.task_id)?.title ?? '其他迭代', kind: 'request' as const, onPress: () => openRequest(r) })),
  ];
  // Opening an iteration lands on what needs the person most — once every fact the
  // inference reads has loaded, and not on narrow screens where detail covers the list.
  const landed = useRef<string | null>(null);
  useEffect(() => {
    if (!tree || landed.current === tree.id || !task || !proposals.data || !liveReady) return;
    landed.current = tree.id;
    if (route.node || route.proposal || compact) return;
    todo.find(t => t.id.startsWith('step-'))?.onPress();
  }, [tree?.id, Boolean(task), Boolean(proposals.data), liveReady]);
  const statuses: Record<string, IterationStatus> = Object.fromEntries((iterations.data ?? []).map(it => {
    const waiting = it.id === tree?.id ? todo.length : allRequests.filter(r => r.task_id === it.task_id && r.status === 'pending').length;
    const running = it.id === tree?.id && tree.candidates.some(c => c.state === 'running');
    return [it.id, running ? { tone: 'running', label: '运行中' } : waiting ? { tone: 'warning', label: `${waiting} 项待处理` } : { tone: null, label: '' }];
  }));
  const paletteItems: PaletteItem[] = [
    ...(iterations.data ?? []).map(it => ({ id: 'it-' + it.id, group: '跳到迭代', label: it.title, hint: it.protocol?.mode, run: () => selectIteration(it.id) })),
    ...(tree?.candidates ?? []).map(c => ({ id: 'node-' + c.id, group: '跳到候选', label: `${label(c.id)}  ${titles[c.id] ?? ''}`, hint: c.state, run: () => selectNode(c.id) })),
    ...(candidate && actions ? [
      candidate.state === 'implementing' && { id: 'a-freeze', label: `冻结 ${label(candidate.id)}`, run: () => setConfirm(actions.freeze(candidate)) },
      ['frozen', 'failed', 'evaluated'].includes(candidate.state) && { id: 'a-run', label: `运行 ${label(candidate.id)}`, run: () => setConfirm(actions.run(candidate)) },
      ['evaluated', 'failed'].includes(candidate.state) && { id: 'a-analyse', label: `分析 ${label(candidate.id)}`, run: () => analyse(candidate) },
      candidate.state === 'evaluated' && { id: 'a-derive', label: `从 ${label(candidate.id)} 派生`, run: () => setConfirm(actions.derive(candidate)) },
      candidate.state === 'evaluated' && candidate.id !== tree?.root_candidate_id && { id: 'a-adopt', label: `采纳 ${label(candidate.id)}`, run: () => setConfirm(actions.decide(candidate, 'adopted')) },
      candidate.state === 'evaluated' && candidate.id !== tree?.root_candidate_id && { id: 'a-reject', label: `放弃 ${label(candidate.id)}`, run: () => setConfirm(actions.decide(candidate, 'rejected')) },
    ].filter((x): x is { id: string; label: string; run: () => void } => Boolean(x)).map(x => ({ ...x, group: '当前候选的动作', hint: x.id === 'a-analyse' ? 'Agent' : '需确认' })) : []),
    ...allRequests.filter(r => r.status === 'pending').map(r => ({ id: 'r-' + r.id, group: '待确认请求', label: `${verbLabels[r.verb] ?? r.verb} ${r.target_id ? label(r.target_id) : ''}`,
      hint: r.requested_by, run: () => actions && setConfirm(actions.confirmRequest(r)) })),
    ...(picked.length ? [{ id: 'compare', group: '视图', label: `比较所选（${picked.length}）`, run: () => update({ matrix: true }) }] : []),
    { id: 'rank', group: '视图', label: route.rank ? '切回谱系' : '按 Δ根 排行', run: () => update({ rank: !route.rank, matrix: false }) },
    { id: 'drawer', group: '视图', label: drawer ? '收起 Agent 抽屉' : '打开 Agent 抽屉', hint: 'Ctrl+J', run: () => setDrawer(d => !d) },
    { id: 'new-it', group: '其他', label: '新建迭代', run: () => setNewIteration(true) },
    { id: 'theme', group: '其他', label: dark ? '浅色主题' : '深色主题', run: () => setDark(d => !d) },
  ];

  const dock = layout.sizes(!compact);
  const detailWidth = compact ? width : Math.max(360, dock.right);
  const sidebar = <IterationSidebar width={compact ? Math.min(320, width - 40) : dock.left} compact={compact} project={boot?.workspaces[0]?.title ?? '项目'}
    iterations={iterations.data ?? []} statuses={statuses} iterationId={iterationId} todo={todo}
    onIteration={selectIteration} onNew={() => { setNewIteration(true); setNav(false); }} onPalette={() => setPalette(true)} onSettings={() => setSettingsOpen(true)}
    onClose={() => compact ? setNav(false) : layout.toggleLeft()} />;
  const detail = reader?.execution ? <ExecutionCases executionId={reader.execution}
    selected={reader.caseId ? { runId: reader.runId, caseId: reader.caseId } : null}
    onSelect={(runId, caseId) => setReader({ runId, caseId, execution: reader.execution })}
    onBack={() => setReader({ runId: '', caseId: null, execution: reader.execution })} onClose={() => setReader(null)} />
    : reader && runList.data ? <RunMaterials runs={runList.data.runs} compact={compact}
    selection={{ runId: reader.runId, caseId: reader.caseId, panel: 'run-cases', needsRead: false, against: null, provenance: false }}
    onClose={() => setReader(null)} onChange={changes => setReader(prev => prev && ({ runId: changes.runId ?? prev.runId, caseId: changes.caseId !== undefined ? changes.caseId : prev.caseId }))} />
    : reader ? <Copy small muted>正在读取运行…</Copy>
    : tree && actions && proposal ? <ProposalDetail proposal={proposal} labels={labels} actions={actions} onConfirm={setConfirm} />
    : tree && actions && candidate ? <CandidateDetail key={candidate.id} tree={tree} candidate={candidate} labels={labels} refreshKey={refresh}
      proposal={candidate.proposal_id ? proposalById[candidate.proposal_id] : undefined} requests={treeRequests} actions={actions} onConfirm={setConfirm}
      onReadRun={(runId, execution) => setReader({ runId, caseId: null, execution })}
      onImplementer={(c: Candidate, p: Proposal) => { setTarget({ role: 'implementer', candidateId: c.id, hypothesisId: p.id, label: label(c.id) }); setDrawer(true); }}
      step={steps[candidate.id] ?? null} onOpenAgent={openAgent} onProposal={id => update({ proposal: id, node: null })}
      onAnalyst={() => analyse(candidate)} onNewAnalyst={() => newAnalyst(candidate)} onRemoveAgent={removeAgent}
      agents={agentsOf(candidate.id, task?.sessions ?? []).map(s => ({ sessionId: s.id, role: s.role!, status: live[s.id]?.status ?? null,
        label: `${s.role === 'analyst' ? '分析' : '实现'} Agent · ${s.agent_id.slice(0, 8)} · ${date(s.linked_at)} 关联` }))} />
    : <View style={{ gap: 8, paddingTop: 8 }}><Copy small muted>选一个候选看详情：假设与预测、对父的翻转、代码差异、运行与准入。</Copy>
      {treeRequests.some(r => r.status === 'pending') && <Copy small style={{ color: theme.warning }}>有 {treeRequests.filter(r => r.status === 'pending').length} 条待确认请求，见左栏“待处理”。</Copy>}</View>;
  const detailOpen = Boolean(reader || proposal || candidate);

  return <ThemeContext.Provider value={theme}>
    <View style={{ flex: 1, backgroundColor: theme.bg, flexDirection: 'row' }}>
      {auth ? <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center', padding: 24 }}><View style={{ width: '100%', maxWidth: 380, gap: 16 }}>
        <Text style={{ fontSize: 20, fontWeight: '600', color: theme.text }}>Silicon Notebook 工作台</Text><Copy muted>使用启动时的访问链接或访问码</Copy>
        <Input label="工作台访问码" value={access} onChange={setAccess} testID="access-code" />
        <Button label="打开工作台" primary testID="access-submit" onPress={async () => {
          try { await api('/session', { token: access }); await reload(); setError(''); } catch (e) { setError((e as Error).message); }
        }} /><Copy small style={{ color: theme.error }}>{error}</Copy>
      </View></View> : !boot ? <View style={{ padding: 32 }}><Copy muted>正在读取工作台…</Copy></View> : <>
        {!compact && layout.leftOpen && <View style={{ position: 'relative', width: dock.left }}>{sidebar}<ResizeHandle side="left" width={dock.left} onResize={n => layout.resize('left', n)} /></View>}
        <View testID="work-surface" style={{ flex: 1, minWidth: 0 }}>
          <View style={{ flex: 1, minHeight: 0, flexDirection: 'row' }}>
            <View style={{ flex: 1, minWidth: 0 }}>
              <View style={{ height: 44, borderBottomWidth: 1, borderColor: theme.line, paddingHorizontal: 12, flexDirection: 'row', alignItems: 'center', gap: 10 }}>
                {(compact || !layout.leftOpen) && <IconButton label="展开侧栏" icon={PanelLeftOpen} onPress={() => compact ? setNav(n => !n) : layout.toggleLeft()} testID="toggle-left-sidebar" />}
                <Text numberOfLines={1} style={{ flexShrink: 1, fontSize: 14, fontWeight: '600', color: theme.text }}>{tree?.title ?? '迭代'}</Text>
                {tree && !compact && <View style={{ flexDirection: 'row', gap: 10, alignItems: 'center', flexShrink: 1 }}>
                  <Mono size={11} muted>起点 {short(tree.start_commit, 7)}</Mono>
                  <Mono size={11} muted>题单 {short(tree.selection_hash, 8)}（冻结）</Mono>
                  <Mono size={11} muted>{tree.protocol?.mode} · {tree.protocol?.input?.suite ?? tree.protocol?.input?.input_id ?? ''}</Mono>
                </View>}
                <View style={{ flex: 1 }} />
                <Pressable onPress={() => setPalette(true)} testID="palette-hint" style={{ flexDirection: 'row', alignItems: 'center', gap: 6, height: 26, paddingHorizontal: 10,
                  borderRadius: 8, borderWidth: 1, borderColor: theme.line, backgroundColor: theme.surface }}>
                  <Text style={{ fontSize: 12, color: theme.muted }}>命令</Text><Mono size={11} muted>Ctrl K</Mono>
                </Pressable>
              </View>
              {(error || iterations.error || treeState.error) && <View style={{ padding: 10, borderBottomWidth: 1, borderColor: theme.line, flexDirection: 'row', gap: 8, alignItems: 'center' }}>
                <Copy small style={{ color: theme.error, flex: 1 }}>{error || iterations.error || treeState.error}</Copy><Button label="重新读取" onPress={bump} />
              </View>}
              <ScrollView style={{ flex: 1 }} contentContainerStyle={{ padding: compact ? 14 : 24 }}>
                <View style={{ width: '100%', maxWidth: 1080, alignSelf: 'center', gap: 16 }}>
                  {!iterations.data ? <Copy small muted>正在读取迭代…</Copy>
                    : !iterations.data.length ? <View style={{ gap: 10, alignItems: 'flex-start' }}><Copy muted>还没有迭代。迭代固定一个起点 commit 和冻结题单，候选从这里派生成树。</Copy>
                      <Button label="新建迭代" primary onPress={() => setNewIteration(true)} testID="empty-new-iteration" /></View>
                    : !tree ? <Copy small muted>正在读取迭代树…</Copy>
                      : route.matrix ? <CompareMatrix tree={tree} labels={labels} picked={picked} onClose={() => update({ matrix: false })}
                        onReadCase={(runId, caseId) => setReader({ runId, caseId })} />
                        : <Lineage tree={tree} labels={labels} titles={titles} selectedId={route.proposal ?? route.node} onSelect={selectNode}
                          onSelectProposal={id => { setReader(null); update({ proposal: id, node: null }); }} rank={route.rank} onRank={rank => update({ rank })}
                          picked={picked} onPick={id => setPicked(p => p.includes(id) ? p.filter(x => x !== id) : [...p, id])} onCompare={() => update({ matrix: true })} compact={compact}
                          steps={steps} agents={agentMarks} onOpenAgent={openAgent} iterationAgent={iterationAgent}
                          onIterationAnalyst={() => { setTarget({ role: 'analyst', scope: 'iteration', label: '迭代' }); setDrawer(true); }} />}
                  {tree && tree.unassigned_executions.length > 0 && <Copy small muted>{tree.unassigned_executions.length} 次执行未归属任何候选（保留，不计入比较）。</Copy>}
                </View>
              </ScrollView>
            </View>
            {!compact && <View testID="detail-column" style={{ position: 'relative', width: detailWidth, borderLeftWidth: 1, borderColor: theme.line, backgroundColor: theme.bg }}>
              <ResizeHandle side="right" width={detailWidth} onResize={n => layout.resize('right', n)} />
              {reader && runList.data ? detail : <ScrollView contentContainerStyle={{ padding: 18 }}>{detail}</ScrollView>}
            </View>}
          </View>
          <AgentDrawer task={task} boot={boot} compact={compact} open={drawer} available={height - 44} labels={labels} focus={focus}
            onToggle={() => setDrawer(d => !d)} hasRuns={Boolean(tree?.candidates.some(c => c.attempts.length))}
            target={target} onTargetDone={() => setTarget(null)} onTaskChanged={bump} onRemoveAgent={removeAgent} evaluated={evaluatedCount}
            node={candidate ? { candidateId: candidate.id, label: label(candidate.id), state: candidate.state, hypothesisId: candidate.proposal_id ?? undefined } : null} />
        </View>
        {compact && detailOpen && <View style={{ position: 'absolute', zIndex: 15, top: 0, bottom: 0, left: 0, right: 0, backgroundColor: theme.bg }}>
          <View style={{ height: 44, flexDirection: 'row', alignItems: 'center', paddingHorizontal: 8, borderBottomWidth: 1, borderColor: theme.line }}>
            <IconButton label="关闭详情" icon={X} onPress={() => { setReader(null); update({ node: null, proposal: null }); }} testID="close-detail" />
          </View>
          {reader && runList.data ? detail : <ScrollView contentContainerStyle={{ padding: 14 }}>{detail}</ScrollView>}
        </View>}
        {compact && nav && <View style={{ position: 'absolute', zIndex: 20, top: 0, bottom: 0, left: 0, right: 0 }}>
          <Pressable accessibilityRole="button" accessibilityLabel="关闭导航" onPress={() => setNav(false)} style={{ position: 'absolute', top: 0, bottom: 0, left: 0, right: 0, backgroundColor: '#0006' }} />{sidebar}
        </View>}
        {!!notice && <View accessibilityRole="alert" style={{ position: 'absolute', bottom: 54, left: 0, right: 0, alignItems: 'center', pointerEvents: 'none' }}>
          <View style={{ backgroundColor: theme.text, paddingVertical: 10, paddingHorizontal: 14, borderRadius: 8 }}><Text style={{ color: theme.bg, fontSize: 12 }}>{notice}</Text></View>
        </View>}
      </>}
    </View>
    <ConfirmDialog spec={confirm} onClose={() => setConfirm(null)} onDone={() => { const title = confirm?.title; setConfirm(null); bump(); setNotice(`${title ?? '操作'}：已完成`); }} />
    <CommandPalette open={palette} items={paletteItems} onClose={() => setPalette(false)} />
    <NewIterationDialog open={newIteration} inputs={boot?.inputs ?? []} onClose={() => setNewIteration(false)}
      onCreated={created => { setNewIteration(false); bump(); selectIteration(created.id); setNotice('迭代已建立，正在准备 C0'); }} />
    <WorkspaceConfiguration visible={settingsOpen} workspace={boot?.workspaces[0]} actor={boot?.actor ?? ''} capabilities={boot?.capabilities}
      profiles={boot?.creation_profiles ?? []} inputs={boot?.inputs ?? []} theme={dark ? 'dark' : 'light'} onThemeChange={value => setDark(value === 'dark')}
      onClose={() => setSettingsOpen(false)} />
  </ThemeContext.Provider>;
}
