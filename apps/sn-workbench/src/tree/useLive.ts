import { useEffect, useState } from 'react';
import { api, AgentSnapshot, liveSessions, ReadEnvelope, Task } from '../contracts';
import type { CandidateChanges, IterationTree } from './contracts';
import type { AgentLive } from './next';

// Live facts the next-step inference needs and the tree does not carry: each linked
// Agent's status and the change count of candidates still being implemented.
// Re-read every 10 s while an Agent or an implementation is in progress.
export function useLive(task: Task | undefined, tree: IterationTree | undefined, refresh: number) {
  const [live, setLive] = useState<Record<string, AgentLive>>({});
  const [changes, setChanges] = useState<Record<string, number | undefined>>({});
  const [tick, setTick] = useState(0);
  const [readyFor, setReadyFor] = useState<string | null>(null);
  const sessions = liveSessions(task?.sessions ?? []).slice(-12);
  const implementing = (tree?.candidates ?? []).filter(c => c.state === 'implementing' && c.worktree?.status === 'ready');
  const key = `${task?.id}|${sessions.map(s => s.id).join()}|${implementing.map(c => c.id).join()}|${refresh}|${tick}`;
  useEffect(() => {
    if (!task) return;
    const controller = new AbortController();
    const statuses = Promise.all(sessions.map(async s => {
      try {
        const read = await api<ReadEnvelope<{ agent: AgentSnapshot }>>(`/tasks/${task.id}/sessions/${s.id}/agent`, undefined, controller.signal);
        return [s.id, { status: read.result.status === 'ok' ? read.result.agent.status : null }] as const;
      } catch { return [s.id, { status: null }] as const; }
    })).then(rows => { if (!controller.signal.aborted) setLive(Object.fromEntries(rows)); });
    const counts = Promise.all(implementing.map(async c => {
      try { return [c.id, (await api<CandidateChanges>(`/candidates/${c.id}/changes`, undefined, controller.signal)).file_count] as const; }
      catch { return [c.id, undefined] as const; }
    })).then(rows => { if (!controller.signal.aborted) setChanges(Object.fromEntries(rows)); });
    void Promise.all([statuses, counts]).then(() => { if (!controller.signal.aborted) setReadyFor(task.id); });
    return () => controller.abort();
  }, [key]);
  const active = implementing.length > 0 || Object.values(live).some(l => l.status === 'running' || l.status === 'initializing');
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => setTick(n => n + 1), 10000);
    return () => clearInterval(timer);
  }, [active]);
  // ready: live facts for this Task have been read at least once.
  return { live, changes, ready: Boolean(task && readyFor === task.id) };
}
