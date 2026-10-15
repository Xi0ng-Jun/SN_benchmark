import { useEffect, useState } from 'react';
import { useResource } from '../materials';
import type { RunList, TaskChanges, TaskOverview } from './contracts';

// Run views remain read-only for scores/cases; the Changes page owns the
// explicit Job execution action.
// `enabled` waits for the authenticated bootstrap so the first read cannot 401.
export function useRunViews(taskIds: string[], taskId: string | null, refresh: number, enabled = true) {
  const [tick, setTick] = useState(0);
  const runs = useResource<RunList>(enabled ? '/run-views/runs' : null, refresh + tick);
  const ids = taskIds.join(',');
  const overview = useResource<{ tasks: Record<string, TaskOverview> }>(enabled && ids ? `/run-views/tasks?ids=${encodeURIComponent(ids)}` : null, refresh + tick);
  const changes = useResource<TaskChanges>(enabled && taskId ? `/run-views/tasks/${encodeURIComponent(taskId)}/changes` : null, refresh + tick);
  // Running runs update in place: poll state files only while any is non-terminal.
  const live = (runs.data?.runs ?? []).some(run => run.state && !run.state.terminal);
  useEffect(() => {
    if (!live) return;
    const timer = setInterval(() => setTick(n => n + 1), 5000);
    return () => clearInterval(timer);
  }, [live]);
  return { runs: runs.data?.runs.filter(run => run.status !== 'unreadable') ?? [], runsError: runs.error,
    loading: enabled && ((!runs.data && !runs.error) || Boolean(ids && !overview.data && !overview.error)),
    overview: overview.data?.tasks ?? {}, changes: changes.data, changesError: changes.error };
}
