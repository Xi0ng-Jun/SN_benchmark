import { useEffect, useRef, useState } from 'react';
import { api, requestKey } from '../contracts';
import type { ContextReference, ContextSnapshot, Task, TaskSession } from '../contracts';
import type { ContextMode, ContextPreview, ContextSnapshotView } from './chatTypes';

type Selection = { mode: ContextMode; optionId: string | null; path: string };
type Attachment = ContextSnapshot & { id: string };
export type MessageDraft = { text: string; attachments: Attachment[] };
type Preview = { taskId: string; generation: number; value: ContextPreview; full: Attachment | null };
const initialSelection = (): Selection => ({ mode: 'reference', optionId: null, path: '' });
const emptyPreview = (): ContextPreview => ({ status: 'idle', current: false, snapshot: null });
const referenceKey = (ref: ContextReference) => ref.kind === 'goal' ? 'goal' : ref.kind === 'task_runs' ? 'task_runs'
  : ref.kind === 'brief' ? JSON.stringify(['brief', ref.session_id])
  : ref.kind === 'file' ? JSON.stringify(['file', ref.path])
    : ref.kind === 'result' ? JSON.stringify(['result', ref.result_id])
      : ref.kind === 'run_case' ? JSON.stringify(['run_case', ref.run_id, ref.case_id])
        : JSON.stringify(['case', ref.result_id, ref.case_id]);
const asPreview = (snapshot: Attachment): ContextSnapshotView => ({ id: snapshot.id, title: snapshot.title,
  text: snapshot.text, sha256: snapshot.sha256, complete: true });

// Only successful complete server responses become attachments. Store the
// normalized reference and full source_refs; never reconstruct text or hash.
export function useMessageContext(task: Task | undefined, session: TaskSession | undefined,
  sessionGeneration: number, draftId: string, explicitCase: { result_id: string; case_id: string } | null,
  edit?: { canWrite: () => boolean; onWritten: () => void }, runCase: { run_id: string; case_id: string; label: string } | null = null,
  hasRuns = false, implementer = false) {
  const [selections, setSelections] = useState<Record<string, Selection>>({});
  const selection = task ? selections[task.id] ?? initialSelection() : initialSelection();
  const options: { id: string; label: string; reference: ContextReference }[] = task ? [
    { id: 'goal', label: '任务目标', reference: { kind: 'goal' } },
    ...(hasRuns && !implementer ? [{ id: 'task_runs', label: '本任务的评测运行', reference: { kind: 'task_runs' as const } }] : []),
    ...(implementer ? [] : task.results).map(result => ({ id: referenceKey({ kind: 'result', result_id: result.id }),
      label: result.suite.toUpperCase() + ' · 成绩摘要', reference: { kind: 'result' as const, result_id: result.id } })),
  ] : [];
  // An implementer never gets benchmark material; the server enforces this too.
  if (!implementer && explicitCase && task?.results.some(result => result.id === explicitCase.result_id)) {
    const reference: ContextReference = { kind: 'case', ...explicitCase };
    options.push({ id: referenceKey(reference), label: '当前案例 · ' + explicitCase.case_id, reference });
  }
  if (runCase && !implementer) {
    const reference: ContextReference = { kind: 'run_case', run_id: runCase.run_id, case_id: runCase.case_id };
    options.push({ id: referenceKey(reference), label: '正在看的 run 案例 · ' + runCase.label, reference });
  }
  const reference: ContextReference | null = selection.mode === 'path'
    ? selection.path.trim() ? { kind: 'file', path: selection.path } : null
    : options.find(option => option.id === selection.optionId)?.reference ?? null;
  const key = JSON.stringify([task?.id ?? null, session?.id ?? null, session?.agent_id ?? null,
    sessionGeneration, draftId, selection, reference]);
  const scope = useRef({ key, generation: 0 });
  const controller = useRef<AbortController | null>(null);
  const invalidate = () => { controller.current?.abort(); controller.current = null; scope.current.generation += 1; };
  if (scope.current.key !== key) { invalidate(); scope.current.key = key; }
  const generation = scope.current.generation;
  useEffect(() => () => { controller.current?.abort(); }, []);
  const [preview, setPreview] = useState<Preview | null>(null);
  const previewRef = useRef(preview);
  previewRef.current = preview;
  const publish = (value: Preview) => { previewRef.current = value; setPreview(value); };
  const current = preview?.generation === generation && preview.taskId === task?.id;
  const displayed = preview && task && preview.taskId === task.id ? { ...preview.value, current: Boolean(current && preview.value.current),
    status: current ? preview.value.status : preview.value.snapshot ? 'ready' as const : 'idle' as const } : emptyPreview();
  const active = () => scope.current.key === key && scope.current.generation === generation;

  const [drafts, setDrafts] = useState<Record<string, MessageDraft>>({});
  const draftsRef = useRef(drafts);
  // The ref is authoritative between React renders; writes update it synchronously.
  const draft = drafts[draftId] ?? { text: '', attachments: [] };
  const latestDraft = useRef(draftId);
  latestDraft.current = draftId;
  const updateDraft = (value: MessageDraft) => {
    if (edit && !edit.canWrite()) return;
    const previous = draftsRef.current[draftId];
    if (previous && previous.text === value.text && previous.attachments.length === value.attachments.length
        && previous.attachments.every((a, i) => a === value.attachments[i])) return;
    draftsRef.current = { ...draftsRef.current, [draftId]: value }; setDrafts(draftsRef.current);
    edit?.onWritten();
  };
  const changeSelection = (next: Selection) => {
    if (!task || !active()) return;
    invalidate(); setSelections(previous => ({ ...previous, [task.id]: next }));
  };
  const requestPreview = async () => {
    if (!task || !reference || !active() || controller.current) return;
    const captured = { taskId: task.id, generation, key };
    const pending = new AbortController(); controller.current = pending;
    publish({ taskId: task.id, generation, full: null,
      value: { status: 'loading', current: false, snapshot: displayed.snapshot } });
    const valid = () => scope.current.key === captured.key && scope.current.generation === captured.generation && !pending.signal.aborted;
    try {
      const snapshot = await api<ContextSnapshot>(`/tasks/${captured.taskId}/context`, { reference }, pending.signal);
      if (!valid()) return;
      // An invalid success response must not receive the complete projection.
      if (!snapshot || typeof snapshot.title !== 'string' || typeof snapshot.text !== 'string'
          || typeof snapshot.sha256 !== 'string' || !/^[a-f0-9]{64}$/.test(snapshot.sha256)
          || snapshot.reference?.kind !== reference.kind || !snapshot.source_refs
          || typeof snapshot.source_refs !== 'object' || Array.isArray(snapshot.source_refs)
          || Object.values(snapshot.source_refs).some(value => typeof value !== 'string')
          || (reference.kind === 'result' && (snapshot.reference as { result_id?: string }).result_id !== reference.result_id)
          || (reference.kind === 'case' && ((snapshot.reference as { result_id?: string }).result_id !== reference.result_id
            || (snapshot.reference as { case_id?: string }).case_id !== reference.case_id))
          || (reference.kind === 'run_case' && ((snapshot.reference as { run_id?: string }).run_id !== reference.run_id
            || (snapshot.reference as { case_id?: string }).case_id !== reference.case_id))
          || (reference.kind === 'file' && typeof (snapshot.reference as { path?: string }).path !== 'string')) {
        throw new Error('上下文预览响应无效，不能加入草稿');
      }
      const full = { ...snapshot, id: requestKey() };
      publish({ ...captured, full, value: { status: 'ready', current: true, snapshot: asPreview(full) } });
    } catch (error) {
      if (valid()) publish({ ...captured, full: null, value: { status: 'error', current: false,
        snapshot: displayed.snapshot, detail: (error as Error).message } });
    } finally { if (controller.current === pending) controller.current = null; }
  };
  const full = current && preview?.value.status === 'ready' ? preview.full : null;
  const duplicate = full && draft.attachments.some(item => referenceKey(item.reference) === referenceKey(full.reference) && item.sha256 === full.sha256);
  const add = (id: string) => {
    const latest = previewRef.current;
    if (!task || !session || !active() || latest?.generation !== generation || latest.taskId !== task.id
        || latest.value.status !== 'ready' || !latest.value.current || latest.full?.id !== id) return;
    const saved = draftsRef.current[draftId] ?? { text: '', attachments: [] };
    if (saved.attachments.some(item => referenceKey(item.reference) === referenceKey(latest.full!.reference) && item.sha256 === latest.full!.sha256)) return;
    updateDraft({ ...saved, attachments: [...saved.attachments, latest.full] });
  };
  // Attach without touching the picker: used for the Task's run summary on a
  // new draft. Skipped when the Agent already received this exact snapshot.
  const autoAttach = async (wanted: { reference: ContextReference; skipSha: string | null }[]) => {
    if (!task || !session) return;
    const captured = { taskId: task.id, draftId };
    const fresh: Attachment[] = [];
    for (const { reference: ref, skipSha } of wanted) {
      try {
        const snapshot = await api<ContextSnapshot>(`/tasks/${captured.taskId}/context`, { reference: ref });
        if (snapshot && typeof snapshot.text === 'string' && /^[a-f0-9]{64}$/.test(snapshot.sha256 ?? '')
            && snapshot.reference?.kind === ref.kind && snapshot.sha256 !== skipSha) fresh.push({ ...snapshot, id: requestKey() });
      } catch { /* optional convenience; the user can still attach it manually */ }
    }
    const saved = draftsRef.current[captured.draftId] ?? { text: '', attachments: [] };
    const missing = fresh.filter(item => !saved.attachments.some(old => referenceKey(old.reference) === referenceKey(item.reference)));
    if (latestDraft.current !== captured.draftId || !missing.length) return;
    // Independently loaded automatic attachments must still put the role
    // instructions before the materials, regardless of response order.
    const attachments = [...missing, ...saved.attachments].sort((a, b) =>
      Number(b.reference.kind === 'brief') - Number(a.reference.kind === 'brief'));
    updateDraft({ ...saved, attachments });
  };
  const open = (id: string) => {
    if (!task || !session || !active()) return;
    const attachment = draftsRef.current[draftId]?.attachments.find(item => item.id === id);
    if (!attachment) return;
    // Reading a saved snapshot never authorizes adding it as a new preview.
    invalidate();
    publish({ taskId: task.id, generation: scope.current.generation, full: null, value: {
      status: 'ready', current: false, snapshot: asPreview(attachment), detail: '草稿中保存的快照' } });
  };
  return { selection: { mode: selection.mode, options, selectedOptionId: selection.optionId, pathValue: selection.path,
      onModeChange: (mode: ContextMode) => changeSelection({ ...selection, mode }),
      // One write: a second change in the same tick would be dropped as a stale scope.
      onSelectOption: (optionId: string) => changeSelection({ ...selection, mode: 'reference', optionId }),
      onPathChange: (path: string) => changeSelection({ ...selection, path }), previewBlockedReason: null },
    preview: displayed, destinationLabel: task && session ? task.title + ' · ' + session.agent_id : null,
    addBlockedReason: edit && !edit.canWrite() ? '当前消息意图已冻结，请核对或显式开始新草稿' : !session ? '请先明确选择消息草稿目的地' : duplicate ? '此来源快照已在当前消息草稿中' : null,
    requestPreview: () => { void requestPreview(); }, add, open, invalidate,
    autoAttach: (wanted: { reference: ContextReference; skipSha: string | null }[]) => { void autoAttach(wanted); },
    readDraft: () => draftsRef.current[draftId] ?? { text: '', attachments: [] },
    // Only the intent owner calls this after moving to a new, unlocked draft.
    clearDraft: () => updateDraft({ text: '', attachments: [] }),
    message: { text: draft.text, attachments: draft.attachments,
      onChange: (text: string) => { if (active()) updateDraft({ ...(draftsRef.current[draftId] ?? { text: '', attachments: [] }), text }); },
      onRemoveReference: (id: string) => { if (active()) updateDraft({ ...(draftsRef.current[draftId] ?? draft),
        attachments: (draftsRef.current[draftId]?.attachments ?? []).filter(item => item.id !== id) }); } } };
}
