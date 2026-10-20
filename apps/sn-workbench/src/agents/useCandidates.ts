import { useEffect, useRef, useState } from 'react';
import { api, date, requestKey } from '../contracts';
import type { SourceLocator, Task, TimelineEntry } from '../contracts';
import type { useTaskSessions } from './useTaskSessions';
import type { CandidateDraftKind, CandidateDraftPanelProps, CandidateDraftSaveStatus } from './CandidateDraftPanel';
import type { CandidateDecisionStatus, CandidateJudgment, CandidateRecordsPanelProps } from './CandidateRecordsPanel';

type Decision = { id: string; candidate_id: string; actor: string; judgment: CandidateJudgment;
  reason: string; saved_at: string; parent_id: string | null; base_revision: string | null };
type Candidate = { id: string; session_id: string; kind: CandidateDraftKind; text: string; origin: string;
  selected_by: string; saved_at: string; source: SourceLocator & { agent_id: string; text: string };
  decision?: Decision | null; decisions: Decision[] };
type SourceDraft = { taskId: string; sessionId: string; agentId: string; generation: number; pageVersion: number;
  key: string; entry: TimelineEntry; scopeLabel: string; kind: CandidateDraftKind; text: string;
  status: CandidateDraftSaveStatus; detail?: string };
type DecisionDraft = { key: string; base: string | null; judgment: CandidateJudgment | null; reason: string;
  status: CandidateDecisionStatus; detail?: string; receipt?: Decision };
type List = { taskId: string; generation: number; status: 'loading' | 'ready' | 'error'; records: Candidate[]; detail?: string };
const labels: Record<CandidateDraftKind, string> = { finding: '发现', direction: '方向', next_step: '下一步' };
const locked = (status: string) => status === 'saving' || status === 'unknown';
const sameLocator = (left: SourceLocator, right: SourceLocator) => left.epoch === right.epoch
  && left.seq_start === right.seq_start && left.seq_end === right.seq_end && left.turn_id === right.turn_id
  && left.sha256 === right.sha256 && JSON.stringify(left.source_seq_ranges) === JSON.stringify(right.source_seq_ranges);
class WriteRejected extends Error {}
type Receipt<T> = { status: 'missing' } | { status: 'ok'; receipt: T };
const candidateReceiptMatches = (receipt: Candidate, intent: SourceDraft, actor: string) => Boolean(receipt
  && typeof receipt.id === 'string' && receipt.id && receipt.session_id === intent.sessionId
  && receipt.kind === intent.kind && receipt.text === intent.text.trim() && receipt.origin === 'agent'
  && receipt.selected_by === actor && receipt.source?.agent_id === intent.agentId
  && receipt.source.text === intent.entry.text && sameLocator(receipt.source, intent.entry.source_locator));
const decisionReceiptMatches = (receipt: Decision, intent: DecisionDraft, candidateId: string, actor: string) => Boolean(receipt
  && typeof receipt.id === 'string' && receipt.id && receipt.candidate_id === candidateId && receipt.actor === actor
  && receipt.judgment === intent.judgment && receipt.reason === intent.reason.trim()
  && receipt.base_revision === intent.base && typeof receipt.saved_at === 'string');

// Unlike a read failure, a lost write response may follow a committed write.
// Only explicit no-write HTTP rejections are retryable. Never abort a save on
// task/form changes, infer cancellation or automatically replay an unknown.
async function write<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch('/api' + path, { method: 'POST', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', 'X-Workbench': '1' }, body: JSON.stringify(body) });
  if (!response.ok) {
    if ([400, 401, 403, 404, 409, 413, 422].includes(response.status)) {
      let value: { error?: unknown } | null = null;
      try { value = await response.json(); } catch { /* The no-write status is authoritative even without JSON. */ }
      throw new WriteRejected(typeof value?.error === 'string' ? value.error : '保存请求被拒绝');
    }
    throw new Error('保存回执不可核对');
  }
  return response.json();
}

export function useCandidates(task: Task | undefined, actor: string, sessions: ReturnType<typeof useTaskSessions>,
  onSaved: (taskId: string) => void) {
  const taskKey = JSON.stringify([task?.id ?? null, actor]);
  const scope = useRef({ key: taskKey, generation: 0 });
  if (scope.current.key !== taskKey) scope.current = { key: taskKey, generation: scope.current.generation + 1 };
  const generation = scope.current.generation;
  const active = () => scope.current.key === taskKey && scope.current.generation === generation;
  const [list, setList] = useState<List | null>(null);
  const listRef = useRef(list); listRef.current = list;
  const reads = useRef<AbortController | null>(null);
  const refreshList = async () => {
    if (!task || !active()) return;
    reads.current?.abort();
    const controller = new AbortController(); reads.current = controller;
    const previous = listRef.current?.taskId === task.id ? listRef.current.records : [];
    const publish = (value: List) => { listRef.current = value; setList(value); };
    publish({ taskId: task.id, generation, records: previous, status: 'loading' });
    try {
      const records = await api<Candidate[]>(`/tasks/${task.id}/candidates`, undefined, controller.signal);
      if (active() && !controller.signal.aborted) publish({ taskId: task.id, generation, records, status: 'ready' });
    } catch (error) {
      if (active() && !controller.signal.aborted) publish({ taskId: task.id, generation, records: previous,
        status: 'error', detail: (error as Error).message });
    }
  };
  useEffect(() => { void refreshList(); return () => { reads.current?.abort(); }; }, [taskKey]);
  const listCurrent = list?.taskId === task?.id && list?.generation === generation && list?.status === 'ready';
  const records = list?.taskId === task?.id ? list?.records ?? [] : [];
  const [selected, setSelected] = useState<Record<string, string | null>>({});
  const selectedId = task ? selected[task.id] ?? null : null;
  const record = records.find(item => item.id === selectedId);
  const head = record?.decisions.filter(item => item.actor === actor).at(-1);
  const decisionKey = JSON.stringify([task?.id ?? null, selectedId, actor]);
  const [decisions, setDecisions] = useState<Record<string, DecisionDraft>>({});
  const decisionsRef = useRef(decisions); decisionsRef.current = decisions;
  const updateDecision = (key: string, value: DecisionDraft) => {
    decisionsRef.current = { ...decisionsRef.current, [key]: value }; setDecisions(decisionsRef.current);
  };
  const decision = decisions[decisionKey];
  const selectCandidate = (id: string) => {
    if (!task || !active() || !records.some(item => item.id === id)) return;
    const key = JSON.stringify([task.id, id, actor]);
    if (!decisionsRef.current[key]) {
      const ownHead = records.find(item => item.id === id)?.decisions.filter(item => item.actor === actor).at(-1);
      updateDecision(key, { key: requestKey(), base: ownHead?.id ?? null, judgment: null, reason: '', status: 'idle' });
    }
    setSelected(previous => ({ ...previous, [task.id]: id }));
  };
  const modifyDecision = (changes: Partial<Pick<DecisionDraft, 'judgment' | 'reason'>>) => {
    const current = decisionsRef.current[decisionKey];
    if (!task || !record || !active() || !current || locked(current.status)) return;
    updateDecision(decisionKey, { ...current, ...changes, key: requestKey(),
      base: current.status === 'saved' ? (listCurrent ? head?.id : current.receipt?.id) ?? current.receipt?.id ?? null : current.base,
      receipt: undefined, status: 'idle', detail: undefined });
  };
  const saveDecision = async () => {
    const intent = decisionsRef.current[decisionKey];
    if (!task || !record || !active() || !listCurrent || !intent || !['idle', 'error'].includes(intent.status)
        || !intent.judgment || !intent.reason.trim() || Array.from(intent.reason).length > 10000
        || intent.base !== (head?.id ?? null)) return;
    const taskId = task.id; const candidateId = record.id;
    updateDecision(decisionKey, { ...intent, status: 'saving', detail: undefined });
    try {
      const receipt = await write<Decision>(`/tasks/${taskId}/candidates/${candidateId}/decisions`, {
        judgment: intent.judgment, reason: intent.reason, base_revision: intent.base, request_key: intent.key });
      if (!decisionReceiptMatches(receipt, intent, candidateId, actor)) throw new Error('保存回执与判断意图不一致');
      if (decisionsRef.current[decisionKey]?.key === intent.key) updateDecision(decisionKey, {
        ...intent, status: 'saved', reason: receipt.reason, receipt });
      if (active()) { void refreshList(); onSaved(taskId); }
    } catch (error) {
      if (decisionsRef.current[decisionKey]?.key === intent.key) updateDecision(decisionKey, { ...intent,
        status: error instanceof WriteRejected ? 'error' : 'unknown', detail: (error as Error).message });
    }
  };

  const [sourceDrafts, setSourceDrafts] = useState<Record<string, SourceDraft>>({});
  const sourceRef = useRef(sourceDrafts); sourceRef.current = sourceDrafts;
  const [forms, setForms] = useState<Record<string, string | null>>({});
  const form = task ? sourceDrafts[forms[task.id] ?? ''] : undefined;
  const updateSource = (id: string, value: SourceDraft) => {
    sourceRef.current = { ...sourceRef.current, [id]: value }; setSourceDrafts(sourceRef.current);
  };
  const readable = sessions.session && sessions.remote.status === 'available' && sessions.page.status === 'ready'
    && sessions.page.freshness === 'current' && sessions.page.pendingAction === 'none';
  const sourceKey = JSON.stringify([task?.id, sessions.session?.id, sessions.session?.agent_id,
    sessions.generation, sessions.pageVersion, Boolean(readable)]);
  const sourceScope = useRef({ key: sourceKey, generation: 0 });
  if (sourceScope.current.key !== sourceKey) sourceScope.current = { key: sourceKey, generation: sourceScope.current.generation + 1 };
  const sourceGeneration = sourceScope.current.generation;
  const sourceActive = () => sourceScope.current.key === sourceKey && sourceScope.current.generation === sourceGeneration;
  const sourceCurrent = (value: SourceDraft) => Boolean(sourceActive() && readable && value.taskId === task?.id
    && value.sessionId === sessions.session?.id && value.agentId === sessions.session?.agent_id
    && value.generation === sessions.generation && value.pageVersion === sessions.pageVersion
    && sessions.data?.entries.some(entry => entry.id === value.entry.id && entry.candidateBlockedReason === null
      && entry.kind === 'assistant' && !entry.truncated && entry.text === value.entry.text
      && sameLocator(entry.source_locator, value.entry.source_locator)));
  const requestCandidate = (entryId: string, kind: CandidateDraftKind) => {
    const entry = sessions.data?.entries.find(item => item.id === entryId);
    if (!task || !sessions.session || !active() || !sourceActive() || !readable || !entry || entry.kind !== 'assistant'
        || entry.truncated || !entry.text?.trim() || entry.candidateBlockedReason !== null) return;
    // Closing a submitted form cannot turn the same uncertain intent into a
    // new key. Reopen that form until a genuine receipt is available.
    const pending = Object.entries(sourceRef.current).find(([, value]) => value.taskId === task.id
      && value.sessionId === sessions.session!.id && value.entry.id === entry.id && locked(value.status));
    if (pending) { setForms(previous => ({ ...previous, [task.id]: pending[0] })); return; }
    const id = requestKey();
    updateSource(id, { taskId: task.id, sessionId: sessions.session.id, agentId: sessions.session.agent_id,
      generation: sessions.generation, pageVersion: sessions.pageVersion, key: requestKey(),
      entry: structuredClone(entry), scopeLabel: task.title + ' · ' + sessions.session.agent_id + ' · ' + entry.label,
      kind, text: '', status: 'idle' });
    setForms(previous => ({ ...previous, [task.id]: id }));
  };
  const modifySource = (changes: Partial<Pick<SourceDraft, 'kind' | 'text'>>) => {
    if (!task || !active()) return;
    const id = forms[task.id]; const current = id ? sourceRef.current[id] : undefined;
    if (!id || !current || locked(current.status)) return;
    updateSource(id, { ...current, ...changes, key: requestKey(), status: 'idle', detail: undefined });
  };
  const saveCandidate = async () => {
    const id = task ? forms[task.id] : null; const intent = id ? sourceRef.current[id] : undefined;
    if (!id || !intent || !active() || !sourceCurrent(intent) || !['idle', 'error'].includes(intent.status)
        || !intent.text.trim() || Array.from(intent.text).length > 10000) return;
    updateSource(id, { ...intent, status: 'saving', detail: undefined });
    try {
      const receipt = await write<Candidate>(`/tasks/${intent.taskId}/sessions/${intent.sessionId}/candidates`, {
        locator: intent.entry.source_locator, kind: intent.kind, text: intent.text, request_key: intent.key });
      if (!candidateReceiptMatches(receipt, intent, actor)) {
        throw new Error('保存回执与发现意图不一致');
      }
      if (sourceRef.current[id]?.key === intent.key) updateSource(id, { ...intent, text: receipt.text, status: 'saved' });
      if (active()) { void refreshList(); onSaved(intent.taskId); }
    } catch (error) {
      if (sourceRef.current[id]?.key === intent.key) updateSource(id, { ...intent,
        status: error instanceof WriteRejected ? 'error' : 'unknown', detail: (error as Error).message });
    }
  };
  const draftPanel: Omit<CandidateDraftPanelProps, 'compact'> | null = form ? {
    source: { entryId: form.entry.id, label: form.entry.label, scopeLabel: form.scopeLabel, kind: form.entry.kind,
      text: form.entry.text, sha256: form.entry.source_locator.sha256, truncated: form.entry.truncated,
      current: sourceCurrent(form), blockedReason: form.entry.candidateBlockedReason },
    kind: form.kind, text: form.text, saveStatus: form.status, statusDetail: form.detail,
    saveBlockedReason: Array.from(form.text).length > 10000 ? '发现内容超过服务端 10000 字符上限' : null,
    onKindChange: kind => modifySource({ kind }), onTextChange: text => modifySource({ text }),
    onSave: () => { void saveCandidate(); }, onCancel: () => { if (task && active()) setForms(previous => ({ ...previous, [task.id]: null })); },
  } : null;
  const recordsPanel: Omit<CandidateRecordsPanelProps, 'compact'> = {
    records: records.map(item => ({ id: item.id, label: labels[item.kind] + ' · ' + item.id.slice(0, 8),
      kindLabel: labels[item.kind], text: item.text, origin: item.origin, selectedByLabel: item.selected_by,
      savedAtLabel: date(item.saved_at), source: { scopeLabel: (task?.title ?? '') + ' · ' + item.source.agent_id
        + ' · 序号 ' + item.source.seq_start + '–' + item.source.seq_end,
        text: item.source.text, sha256: item.source.sha256 }, decisions: item.decisions.map(saved => ({ id: saved.id,
        actorLabel: saved.actor, judgment: saved.judgment, reason: saved.reason, savedAtLabel: date(saved.saved_at) })) })),
    selectedCandidateId: selectedId, onSelectCandidate: selectCandidate,
    listStatus: list?.taskId === task?.id && list?.generation === generation ? list!.status : 'idle', listCurrent,
    listDetail: list?.taskId === task?.id ? list?.detail : undefined,
    decision: { candidateId: record?.id ?? null, actorLabel: actor,
      headLabel: head ? '当前操作者修订 · ' + head.id : '当前操作者尚无判断修订',
      judgment: decision?.judgment ?? null, reason: decision?.reason ?? '', status: decision?.status ?? 'idle',
      blockedReason: !record || !decision ? '请选择已保存发现' : Array.from(decision.reason).length > 10000
        ? '判断理由超过服务端 10000 字符上限' : decision.status !== 'saved' && decision.base !== (head?.id ?? null)
          ? '当前操作者修订已变化，请取消编辑并重新选择发现' : null,
      detail: decision?.detail, receipt: decision?.receipt ? { candidateId: decision.receipt.candidate_id,
        revisionId: decision.receipt.id, actorLabel: decision.receipt.actor, judgment: decision.receipt.judgment,
        reason: decision.receipt.reason, savedAtLabel: date(decision.receipt.saved_at),
        current: decision.status === 'saved' } : null },
    onJudgmentChange: judgment => modifyDecision({ judgment }), onReasonChange: reason => modifyDecision({ reason }),
    onSave: () => { void saveDecision(); }, onRefresh: () => { void refreshList(); },
    onCancel: () => { if (!task || !active()) return;
      if (decision && !locked(decision.status)) {
        const next = { ...decisionsRef.current }; delete next[decisionKey]; decisionsRef.current = next; setDecisions(next);
      }
      setSelected(previous => ({ ...previous, [task.id]: null })); },
  };
  const [checking, setChecking] = useState<Record<string, boolean>>({});
  const checkingRef = useRef(new Set<string>());
  const checkReceipt = async (type: 'candidate' | 'decision') => {
    const formId = task ? forms[task.id] : null;
    const candidateIntent = formId ? sourceRef.current[formId] : undefined;
    const decisionIntent = decisionsRef.current[decisionKey];
    const intent = type === 'candidate' ? candidateIntent : decisionIntent;
    if (!task || !active() || !intent || intent.status !== 'unknown' || checkingRef.current.has(intent.key)) return;
    if (type === 'decision' && !record) return;
    const intentKey = intent.key;
    checkingRef.current.add(intentKey); setChecking(previous => ({ ...previous, [intentKey]: true }));
    const path = type === 'candidate'
      ? `/tasks/${candidateIntent!.taskId}/sessions/${candidateIntent!.sessionId}/candidate-receipt`
      : `/tasks/${task.id}/candidates/${record!.id}/decision-receipt`;
    const updateDetail = (detail: string) => {
      if (type === 'candidate' && formId && sourceRef.current[formId]?.key === intentKey
          && sourceRef.current[formId].status === 'unknown') updateSource(formId, { ...candidateIntent!, detail });
      if (type === 'decision' && decisionsRef.current[decisionKey]?.key === intentKey
          && decisionsRef.current[decisionKey].status === 'unknown') updateDecision(decisionKey, { ...decisionIntent, detail });
    };
    try {
      const result = await api<Receipt<Candidate | Decision>>(path, { request_key: intentKey });
      if (result.status === 'missing') { updateDetail('尚未找到已提交回执；原保存可能仍在途，保持结果未知'); return; }
      if (result.status !== 'ok') throw new Error('保存回执核对响应无效');
      if (type === 'candidate') {
        const receipt = result.receipt as Candidate;
        if (!candidateReceiptMatches(receipt, candidateIntent!, actor)) throw new Error('保存回执与发现意图不一致');
        if (formId && sourceRef.current[formId]?.key === intentKey && sourceRef.current[formId].status === 'unknown') {
          updateSource(formId, { ...candidateIntent!, status: 'saved', text: receipt.text, detail: undefined });
        }
      } else {
        const receipt = result.receipt as Decision;
        if (!decisionReceiptMatches(receipt, decisionIntent, record!.id, actor)) throw new Error('保存回执与判断意图不一致');
        if (decisionsRef.current[decisionKey]?.key === intentKey && decisionsRef.current[decisionKey].status === 'unknown') {
          updateDecision(decisionKey, { ...decisionIntent, status: 'saved', reason: receipt.reason, receipt, detail: undefined });
        }
      }
      if (active()) { void refreshList(); onSaved(task.id); }
    } catch (error) { updateDetail((error as Error).message); }
    finally { checkingRef.current.delete(intentKey); setChecking(previous => ({ ...previous, [intentKey]: false })); }
  };
  return { requestCandidate, draftPanel, recordsPanel,
    checkCandidateReceipt: form?.status === 'unknown' ? () => { void checkReceipt('candidate'); } : undefined,
    checkDecisionReceipt: decision?.status === 'unknown' ? () => { void checkReceipt('decision'); } : undefined,
    checkingCandidate: form ? checking[form.key] ?? false : false, checkingDecision: decision ? checking[decision.key] ?? false : false,
    invalidateSource: () => { sourceScope.current.generation += 1; } };
}
