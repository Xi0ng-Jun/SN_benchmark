import { useEffect, useRef, useState } from 'react';
import { api, date, requestKey } from '../contracts';
import type { AgentSnapshot, ReadEnvelope, Task, TaskSession, TimelineCursor, TimelinePage } from '../contracts';
import type { SessionLinkStatus, SessionPage, SessionRemote } from './chatTypes';

type LinkDraft = { value: string; key: string; status: SessionLinkStatus; detail?: string };
type ReadView = { generation: number; pageVersion: number; remote: SessionRemote; page: SessionPage;
  data?: TimelinePage; agentReleased: boolean };
const scopeKey = (taskId?: string, session?: TaskSession) => JSON.stringify([taskId ?? null, session?.id ?? null, session?.agent_id ?? null]);
const idlePage = (): SessionPage => ({ status: 'idle', freshness: 'stale', pendingAction: 'none', entries: [], hasOlder: false, hasNewer: false });
const initialView = (generation: number, selected: boolean): ReadView => ({ generation, pageVersion: 0, agentReleased: false,
  remote: selected ? { status: 'loading' } : { status: 'unselected' }, page: idlePage() });
const POLL_MS = 3000;
const BURST_MS = 90_000;

// One read page per selected session. Scope generations change even on
// A -> B -> A; old reads can finish on the server but cannot publish here.
// The latest linked session is selected by default and read on selection;
// the tail page is re-read while the Agent runs or shortly after a send.
export function useTaskSessions(task: Task | undefined, onLinked: (taskId: string) => void, options: { pollPaused?: boolean } = {}) {
  const [selections, setSelections] = useState<Record<string, string>>({});
  const latestSession = task?.sessions.reduce<TaskSession | undefined>((latest, s) => !latest || s.linked_at > latest.linked_at ? s : latest, undefined);
  const session = task?.sessions.find(s => s.id === selections[task.id]) ?? latestSession;
  const key = scopeKey(task?.id, session);
  const scope = useRef({ key, generation: 0 });
  const controllers = useRef(new Map<number, AbortController>());
  const changeScope = (next: string) => {
    if (scope.current.key !== next) {
      controllers.current.get(scope.current.generation)?.abort();
      scope.current = { key: next, generation: scope.current.generation + 1 };
    }
  };
  // The ref changes before rendering any actions for a new scope. Cancellation
  // is handled by event handlers/cleanup; publishing checks this ref too.
  if (scope.current.key !== key) scope.current = { key, generation: scope.current.generation + 1 };
  const generation = scope.current.generation;
  const [view, setView] = useState<ReadView>(() => initialView(generation, Boolean(session)));
  const current = view.generation === generation ? view : initialView(generation, Boolean(session));
  const currentRef = useRef(current);
  currentRef.current = current;
  const publish = (next: ReadView) => { currentRef.current = next; setView(next); };
  useEffect(() => () => { controllers.current.get(generation)?.abort(); }, [generation]);

  const [links, setLinks] = useState<Record<string, LinkDraft>>({});
  const linksRef = useRef(links);
  linksRef.current = links;
  const linking = useRef(new Set<string>());
  const link = task ? links[task.id] : undefined;
  const updateLink = (id: string, next: LinkDraft) => {
    linksRef.current = { ...linksRef.current, [id]: next };
    setLinks(linksRef.current);
  };
  const onLinkChange = (value: string) => {
    if (!task || linking.current.has(task.id)) return;
    updateLink(task.id, { value, key: requestKey(), status: 'idle' });
  };
  const requestLink = async () => {
    if (!task || linking.current.has(task.id)) return;
    const captured = linksRef.current[task.id];
    if (!captured?.value.trim()) return;
    const id = task.id;
    linking.current.add(id);
    updateLink(id, { ...captured, status: 'linking', detail: undefined });
    try {
      await api<TaskSession>(`/tasks/${id}/sessions`, { agent_id: captured.value, request_key: captured.key });
      if (linksRef.current[id]?.key === captured.key) updateLink(id, { ...captured, status: 'linked' });
      onLinked(id);
    } catch (error) {
      if (linksRef.current[id]?.key === captured.key) updateLink(id, { ...captured, status: 'error', detail: (error as Error).message });
    } finally { linking.current.delete(id); }
  };

  // Quiet reads (background polls) publish only their result, so the composer
  // and page do not flicker through a loading state every few seconds.
  const read = async (action: 'refresh' | 'older' | 'newer', cursor?: TimelineCursor, quiet = false) => {
    if (!task || !session || scope.current.key !== key || controllers.current.has(generation)) return;
    const before = currentRef.current;
    if (action !== 'refresh' && (!cursor || before.page.status !== 'ready' || before.page.freshness !== 'current'
        || before.remote.status !== 'available')) return;
    const controller = new AbortController();
    controllers.current.set(generation, controller);
    const pageVersion = before.pageVersion + 1;
    const pending = { ...before, generation, pageVersion, page: { ...before.page, status: 'loading' as const,
      freshness: 'stale' as const, pendingAction: action, detail: undefined },
      remote: action === 'refresh' ? { ...before.remote, status: 'loading' as const, detail: undefined } : before.remote };
    if (!quiet) publish(pending);
    const path = `/tasks/${task.id}/sessions/${session.id}`;
    const stillCurrent = () => scope.current.key === key && scope.current.generation === generation && !controller.signal.aborted;
    try {
      const requests = await Promise.allSettled([
        action === 'refresh' ? api<ReadEnvelope<{ agent: AgentSnapshot }>>(path + '/agent', undefined, controller.signal) : Promise.resolve(null),
        api<ReadEnvelope<TimelinePage>>(path + '/timeline', cursor
          ? { direction: action === 'older' ? 'before' : 'after', cursor } : {}, controller.signal),
      ]);
      if (!stillCurrent()) return;
      const [agent, history] = requests;
      let remote = before.remote;
      let agentReleased = before.agentReleased;
      if (action === 'refresh') {
        if (agent.status === 'rejected') {
          remote = { status: 'unavailable', detail: String(agent.reason.message) }; agentReleased = false;
        } else if (agent.value) {
          const envelope = agent.value;
          agentReleased = envelope.release.status === 'ok';
          const snapshot = envelope.result.status === 'ok' ? envelope.result.agent : undefined;
          remote = { status: envelope.result.status === 'ok' ? 'available' : envelope.result.status,
            observedAtLabel: date(envelope.observed_at),
            agentStatusLabel: snapshot?.status, agentTitle: snapshot?.title, agentProvider: snapshot?.provider, agentModel: snapshot?.model,
            detail: envelope.result.status !== 'ok' ? envelope.result.code : !agentReleased
              ? '读取成功，连接释放未确认：' + (envelope.release.code ?? envelope.release.status) : undefined };
        }
      }
      let page: SessionPage;
      let data = before.data;
      if (history.status === 'rejected') {
        page = { ...before.page, status: 'error', freshness: 'stale', pendingAction: 'none', detail: String(history.reason.message) };
      } else if (history.value.result.status !== 'ok') {
        page = { ...before.page, status: 'error', freshness: 'stale', pendingAction: 'none', detail: history.value.result.code };
      } else {
        data = history.value.result;
        const readable = history.value.release.status === 'ok' && agentReleased && !data.gap && !data.staleCursor;
        page = { status: 'ready', freshness: readable ? 'current' : 'stale', pendingAction: 'none',
          entries: data.entries.map(entry => ({ ...entry, activityType: entry.activityType ?? undefined })),
          hasOlder: data.hasOlder, hasNewer: data.hasNewer, observedAtLabel: date(history.value.observed_at),
          detail: history.value.release.status !== 'ok' ? '消息读取成功，连接释放未确认'
            : !agentReleased ? 'Agent 读取状态尚未核对，当前页不可引用'
              : data.gap || data.staleCursor ? '历史页存在间隙或过期游标，请刷新重新读取'
                : data.reset ? '已读取重置后的历史页，先前来源失效' : undefined };
      }
      publish({ generation, pageVersion, remote, page, data, agentReleased });
      tail.current = action === 'refresh';
    } finally { controllers.current.delete(generation); }
  };
  const selectSession = (id: string) => {
    const selected = task?.sessions.find(s => s.id === id);
    if (!task || !selected) return;
    changeScope(scopeKey(task.id, selected));
    setSelections(prev => ({ ...prev, [task.id]: id }));
  };
  // Older/newer pages stop the tail refresh until the user returns to latest.
  const tail = useRef(true);
  const burstUntil = useRef(0);
  const readRef = useRef(read);
  readRef.current = read;
  useEffect(() => { tail.current = true; if (session) void readRef.current('refresh'); }, [key]);
  const running = current.remote.agentStatusLabel === 'running' || current.remote.agentStatusLabel === 'initializing';
  const [, wake] = useState(0);
  useEffect(() => {
    if (!session || options.pollPaused) return;
    const timer = setInterval(() => {
      if (tail.current && (running || Date.now() < burstUntil.current)) void readRef.current('refresh', undefined, true);
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [key, running, options.pollPaused, burstUntil.current]);
  return { session, selectedSessionId: session?.id ?? null, selectSession, running, viewingTail: tail.current,
    // A just-sent message: keep reading the tail for a while so the reply appears.
    nudge: () => { burstUntil.current = Date.now() + BURST_MS; tail.current = true; wake(n => n + 1); },
    link: { value: link?.value ?? '', status: link?.status ?? 'idle', detail: link?.detail,
      onChange: onLinkChange, onRequestLink: () => { void requestLink(); } },
    remote: current.remote, page: current.page, data: current.data, generation, pageVersion: current.pageVersion,
    refresh: () => { void read('refresh'); },
    older: current.data?.startCursor ? () => { void read('older', current.data!.startCursor!); } : undefined,
    newer: current.data?.endCursor ? () => { void read('newer', current.data!.endCursor!); } : undefined };
}
