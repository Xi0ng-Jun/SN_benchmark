import { useEffect, useRef, useState } from 'react';
import { api, ApiError, requestKey } from '../contracts';
import type { PlanDraftCommand, PlanDraftRecord, PlanInput, Source, Task } from '../contracts';

// These are boundary validators, not a second plan schema or a hash engine.
// In particular, matching a digest never substitutes for matching its content.
type JsonObject = Record<string, unknown>;
type InputIdentity = PlanDraftRecord['content']['input'];
const object = (v: unknown): v is JsonObject => v !== null && typeof v === 'object' && !Array.isArray(v);
const keys = (v: unknown, expected: string[]): v is JsonObject => object(v)
  && Object.keys(v).length === expected.length && expected.every(k => Object.hasOwn(v, k));
// Python str.isspace()/strip(): Unicode category Zs or bidi class WS/B/S.
// JS trim()/\s omit NEL and U+001C..001F, and wrongly include U+FEFF.
// Share the domain rule for nonempty text and whitespace-free identities;
// validation never trims or normalizes the original request/record strings.
const pythonWhitespace = /[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]/u;
function text(v: unknown, limit = 200): v is string {
  if (typeof v !== 'string') return false;
  // Match Python len(str) in the domain: code points, not UTF-16 units or
  // grapheme clusters. Bound the scan without allocating or changing the text.
  let count = 0;
  let nonWhitespace = false;
  for (const char of v) {
    if (++count > limit) return false;
    if (!pythonWhitespace.test(char)) nonWhitespace = true;
  }
  return nonWhitespace;
}
const id = (v: unknown): v is string => typeof v === 'string' && !!v && !pythonWhitespace.test(v);
const hash = (v: unknown): v is string => typeof v === 'string' && /^[0-9a-f]{64}$/.test(v);
const ids = (v: unknown): v is string[] => Array.isArray(v) && v.length > 0 && v.every(id) && new Set(v).size === v.length;
const equal = (a: readonly string[], b: readonly string[]) => a.length === b.length && a.every((v, i) => v === b[i]);
const identityKeys = ['input_id', 'adapter', 'bundle_id', 'suite', 'source_revision', 'adaptation_revision'];
function identity(v: unknown): v is InputIdentity {
  return keys(v, identityKeys) && id(v.input_id) && v.adapter === 'notebook-bundle' && hash(v.bundle_id)
    && typeof v.suite === 'string' && ['qasper', 'multihop_rag', 'alce', 'qmsum', 'hotpotqa'].includes(v.suite)
    && typeof v.source_revision === 'string' && /^sha256:[0-9a-f]{64}$/.test(v.source_revision)
    && v.adaptation_revision === 'notebook-data-v3';
}
function inputIdentity(v: PlanInput): InputIdentity {
  return { input_id: v.input_id, adapter: v.adapter, bundle_id: v.bundle_id, suite: v.suite,
    source_revision: v.source_revision, adaptation_revision: v.adaptation_revision };
}
function sameIdentity(a: InputIdentity, b: InputIdentity) {
  return identityKeys.every(k => a[k as keyof InputIdentity] === b[k as keyof InputIdentity]);
}
function catalogue(v: unknown, inputId: string): v is PlanInput {
  if (!keys(v, [...identityKeys, 'case_ids', 'partitions']) || v.input_id !== inputId
      || !identity(Object.fromEntries(identityKeys.map(k => [k, v[k]]))) || !ids(v.case_ids)
      || !Array.isArray(v.partitions) || !v.partitions.length) return false;
  const all = new Set(v.case_ids), seen = new Set<string>(), partitions = new Set<string>();
  for (const p of v.partitions) {
    if (!keys(p, ['partition_id', 'case_ids']) || !id(p.partition_id) || partitions.has(p.partition_id) || !ids(p.case_ids)) return false;
    partitions.add(p.partition_id);
    for (const cid of p.case_ids) { if (!all.has(cid) || seen.has(cid)) return false; seen.add(cid); }
  }
  return seen.size === all.size;
}
function record(v: unknown, taskId: string, workspaceId: string): v is PlanDraftRecord {
  if (!keys(v, ['plan_id', 'revision', 'parent_revision', 'task_id', 'workspace_id', 'actor', 'title', 'state', 'execution', 'content_hash', 'saved_at', 'content'])
      || !id(v.plan_id) || !id(v.revision) || !(v.parent_revision === null || id(v.parent_revision)) || v.revision === v.parent_revision
      || v.task_id !== taskId || v.workspace_id !== workspaceId || !text(v.actor) || !text(v.title, 500)
      || v.state !== 'draft' || v.execution !== false || !hash(v.content_hash) || !text(v.saved_at) || !Number.isFinite(Date.parse(v.saved_at))
      || !keys(v.content, ['format', 'title', 'input', 'selection', 'execution_intent', 'source_intent'])) return false;
  const c = v.content;
  return c.format === 'sn-workbench-evaluation-plan-draft-v1' && c.title === v.title && identity(c.input)
    && keys(c.selection, ['kind', 'case_ids', 'partition_ids', 'selection_hash']) && c.selection.kind === 'explicit'
    && ids(c.selection.case_ids) && ids(c.selection.partition_ids) && hash(c.selection.selection_hash)
    && keys(c.execution_intent, ['entrypoint', 'mode', 'request_revision', 'model_services_ref', 'official_scorer_ref'])
    && c.execution_intent.entrypoint === 'scripts/run_notebook_benchmarks.py'
    && (c.execution_intent.mode === 'chunk' || c.execution_intent.mode === 'reasoning')
    && c.execution_intent.request_revision === 'notebook-request-v3'
    && c.execution_intent.model_services_ref === null && c.execution_intent.official_scorer_ref === null
    && keys(c.source_intent, ['kind', 'workspace_id', 'binding']) && c.source_intent.kind === 'task-workspace-at-freeze'
    && c.source_intent.workspace_id === workspaceId && c.source_intent.binding === 'not_captured';
}
function sameJson(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (Array.isArray(a) && Array.isArray(b)) return a.length === b.length && a.every((v, i) => sameJson(v, b[i]));
  return object(a) && keys(b, Object.keys(a)) && Object.keys(a).every(k => sameJson(a[k], b[k]));
}
function immutable<T>(v: T): T {
  if (v !== null && typeof v === 'object') { Object.values(v).forEach(immutable); Object.freeze(v); }
  return v;
}
function selection(description: PlanInput, selected: string[]) {
  const chosen = new Set(selected);
  return { cases: description.case_ids.filter(cid => chosen.has(cid)),
    partitions: description.partitions.filter(p => p.case_ids.some(cid => chosen.has(cid))).map(p => p.partition_id) };
}

export type PlanDraftBody = { title: string; inputId: string | null; caseIdsText: string; mode: 'chunk' | 'reasoning' };
export type PlanDraftBinding = { kind: 'new' | 'head' | 'fixed'; planId: string | null; baseRevision: string | null };
export type PlanRevisionLocator = { planId: string; revision: string };
export type PlanDraftStatus = 'editing' | 'saving' | 'checking' | 'unknown' | 'rejected' | 'saved';
type Frozen = { command: PlanDraftCommand; description: PlanInput; actor: string; taskId: string; workspaceId: string; knownPlans: Set<string> };
type Operation = { kind: 'save' | 'receipt' | 'open'; abort: AbortController };
type Scope = {
  taskId: string; workspaceId: string; actor: string; epoch: number;
  draft: PlanDraftBody; binding: PlanDraftBinding; pin: InputIdentity | null; descriptor: PlanInput | null;
  status: PlanDraftStatus; error: string | null; saved: PlanDraftRecord | null; frozen: Frozen | null;
  op: Operation | null; describe: AbortController | null; list: AbortController | null;
  heads: PlanDraftRecord[]; listError: string | null;
  // Only detects a server changing an already observed immutable revision.
  // Reopening/listing always goes to the server; this is never a local ledger.
  known: Map<string, PlanDraftRecord>;
};
const body = (): PlanDraftBody => ({ title: '', inputId: null, caseIdsText: '', mode: 'chunk' });
const binding = (): PlanDraftBinding => ({ kind: 'new', planId: null, baseRevision: null });
function makeScope(task: Task, actor: string): Scope {
  return { taskId: task.id, workspaceId: task.workspace_id, actor, epoch: 0, draft: body(), binding: binding(), pin: null,
    descriptor: null, status: 'editing', error: null, saved: null, frozen: null, op: null, describe: null, list: null,
    heads: [], listError: null, known: new Map() };
}
const revisionKey = (r: PlanDraftRecord) => JSON.stringify([r.plan_id, r.revision]);
const locked = (s: Scope) => !!s.op || ['saving', 'checking', 'unknown'].includes(s.status);
function suspend(s: Scope | null) {
  if (!s) return;
  s.op?.abort.abort(); s.describe?.abort(); s.list?.abort();
  if (s.op?.kind === 'save' || s.op?.kind === 'receipt') s.status = 'unknown';
  s.op = null; s.describe = null; s.list = null;
}
function matches(v: PlanDraftRecord, f: Frozen) {
  const c = f.command, selected = selection(f.description, c.case_ids);
  return v.actor === f.actor && (c.plan_id === null ? !f.knownPlans.has(v.plan_id) : v.plan_id === c.plan_id)
    && v.parent_revision === c.base_revision && v.title === c.title && v.content.title === c.title
    && sameIdentity(v.content.input, inputIdentity(f.description)) && v.content.execution_intent.mode === c.mode
    && equal(v.content.selection.case_ids, selected.cases) && equal(v.content.selection.partition_ids, selected.partitions);
}
function rejected(error: unknown, key: string) {
  return error instanceof ApiError && [404, 409, 422].includes(error.status)
    && keys(error.value, ['error', 'code', 'request_key']) && text(error.value.error, Number.MAX_SAFE_INTEGER)
    && error.value.code === 'PLAN_SAVE_REJECTED' && error.value.request_key === key;
}

/** Local draft controller. The parent passes undefined for an unloaded/stale
 * task, and may deactivate() synchronously before routing away. Every action
 * belongs to its activation and draft binding, including task/actor ABA.
 * onSaved is invoked only for a validated current-activation receipt.
 */
export function usePlanDrafts(task: Task | undefined, actor: string, inputs: Source[] = [], onSaved?: (saved: PlanDraftRecord) => void) {
  const [, redraw] = useState(0);
  const bump = () => redraw(n => n + 1);
  const mounted = useRef(true);
  const latest = useRef({ task, actor, inputs, onSaved }); latest.current = { task, actor, inputs, onSaved };
  const scopes = useRef(new Map<string, Scope>());
  const owner = useRef<{ key: string | null; generation: number; enabled: boolean; scope: Scope | null }>({ key: null, generation: 0, enabled: true, scope: null });
  const reconcile = () => {
    const p = latest.current;
    const key = p.task && text(p.task.id) && text(p.task.workspace_id) && text(p.actor)
      ? JSON.stringify([p.task.id, p.task.workspace_id, p.actor]) : null;
    let changed = owner.current.key !== key;
    if (changed) {
      suspend(owner.current.scope);
      owner.current.generation++; owner.current.key = key; owner.current.enabled = true;
      if (key !== null && p.task) {
        if (!scopes.current.has(key)) scopes.current.set(key, makeScope(p.task, p.actor));
        owner.current.scope = scopes.current.get(key)!;
      } else owner.current.scope = null;
    }
    const s = owner.current.scope;
    if (s?.draft.inputId && !p.inputs.some(i => i.id === s.draft.inputId)) {
      changed ||= !!s.describe || !!s.descriptor;
      s.describe?.abort(); s.describe = null; s.descriptor = null;
    }
    return changed;
  };
  reconcile();
  const s = owner.current.scope, generation = owner.current.generation, epoch = s?.epoch;
  const active = () => {
    if (reconcile() && mounted.current) bump();
    return mounted.current && owner.current.enabled && owner.current.generation === generation && owner.current.scope === s;
  };
  const editable = () => active() && s !== null && s.epoch === epoch && !locked(s);
  const bound = () => active() && s !== null && s.epoch === epoch;
  const registered = (inputId: string) => latest.current.inputs.some(i => i.id === inputId);
  const path = () => `/tasks/${encodeURIComponent(s!.taskId)}/plans`;
  const knownOkay = (value: PlanDraftRecord) => {
    const prior = s!.known.get(revisionKey(value));
    return !prior || sameJson(prior, value);
  };
  const remember = (value: PlanDraftRecord) => s!.known.set(revisionKey(value), immutable(value));
  const modified = () => { s!.status = 'editing'; s!.frozen = null; s!.saved = null; s!.error = null; };
  const readInput = async (inputId: string) => {
    if (!bound() || !registered(inputId) || s!.draft.inputId !== inputId) return;
    s!.describe?.abort(); const abort = new AbortController(); s!.describe = abort; s!.descriptor = null; bump();
    const current = () => bound() && s!.describe === abort && !abort.signal.aborted && s!.draft.inputId === inputId && registered(inputId);
    try {
      const value = await api<unknown>(`/inputs/${encodeURIComponent(inputId)}`, undefined, abort.signal);
      if (!current()) return;
      if (!catalogue(value, inputId) || s!.pin && !sameIdentity(s!.pin, inputIdentity(value))) {
        s!.error = '输入目录格式或冻结身份不匹配；请核对登记来源，旧草稿身份保持不变。'; return;
      }
      const base = s!.known.get(JSON.stringify([s!.binding.planId, s!.binding.baseRevision]));
      if (base && sameIdentity(base.content.input, inputIdentity(value))) {
        const selected = selection(value, base.content.selection.case_ids);
        if (!equal(selected.cases, base.content.selection.case_ids) || !equal(selected.partitions, base.content.selection.partition_ids)) {
          s!.error = '固定草稿选题或派生 partition 与完整输入目录不匹配；不能追加该版本。'; return;
        }
      }
      s!.descriptor = immutable(value); s!.pin = immutable(inputIdentity(value)); s!.error = null;
    } catch { if (current()) s!.error = '无法读取登记输入目录；正文与原输入身份已保留。'; }
    finally { if (current()) { s!.describe = null; bump(); } }
  };
  const refreshHeads = async () => {
    if (!active() || !s || s.list) return;
    const abort = new AbortController(); s.list = abort; s.listError = null; bump();
    const current = () => active() && s.list === abort && !abort.signal.aborted;
    try {
      const value = await api<unknown>(path(), undefined, abort.signal);
      if (!current()) return;
      if (!Array.isArray(value) || !value.every(v => record(v, s.taskId, s.workspaceId) && knownOkay(v))
          || new Set(value.map(v => v.plan_id)).size !== value.length) { s.listError = '计划列表格式或任务绑定无效。'; return; }
      value.forEach(remember); s.heads = immutable(value);
    } catch { if (current()) s.listError = '无法读取服务器草稿列表，请重试。'; }
    finally { if (current()) { s.list = null; bump(); } }
  };
  useEffect(() => {
    mounted.current = true; owner.current.enabled = true; bump();
    return () => { mounted.current = false; suspend(owner.current.scope); owner.current.generation++; owner.current.enabled = false; };
  }, []);
  useEffect(() => { void refreshHeads(); }, [generation]);

  const setTitle = (value: string) => { if (editable() && typeof value === 'string' && s!.draft.title !== value) { modified(); s!.draft.title = value; bump(); } };
  const setCaseIdsText = (value: string) => { if (editable() && typeof value === 'string' && s!.draft.caseIdsText !== value) { modified(); s!.draft.caseIdsText = value; bump(); } };
  const setMode = (value: 'chunk' | 'reasoning') => {
    if (editable() && (value === 'chunk' || value === 'reasoning') && s!.draft.mode !== value) { modified(); s!.draft.mode = value; bump(); }
  };
  const selectInput = (inputId: string | null) => {
    if (!editable() || inputId !== null && (!id(inputId) || !registered(inputId))) return;
    if (s!.draft.inputId === inputId) { if (inputId && !s!.descriptor && !s!.describe) void readInput(inputId); return; }
    modified(); s!.describe?.abort(); s!.describe = null; s!.descriptor = null; s!.pin = null; s!.draft.inputId = inputId; bump();
    if (inputId !== null) void readInput(inputId);
  };
  const newDraft = () => {
    if (!editable()) return;
    s!.describe?.abort(); s!.describe = null; s!.draft = body(); s!.binding = binding(); s!.pin = null; s!.descriptor = null;
    modified(); s!.epoch++; bump();
  };
  const open = async (planId: string, revision?: string) => {
    if (!editable() || !id(planId) || revision !== undefined && !id(revision)) return;
    const op: Operation = { kind: 'open', abort: new AbortController() }; s!.op = op; s!.error = null; bump();
    const current = () => bound() && s!.op === op && !op.abort.signal.aborted;
    try {
      const query = revision === undefined ? '' : '?' + new URLSearchParams({ revision });
      const value = await api<unknown>(`${path()}/${encodeURIComponent(planId)}${query}`, undefined, op.abort.signal);
      if (!current()) return;
      if (!record(value, s!.taskId, s!.workspaceId) || value.plan_id !== planId
          || revision !== undefined && value.revision !== revision || !knownOkay(value)) {
        s!.error = '草稿版本响应格式或绑定无效；当前正文已保留。'; return;
      }
      remember(value); s!.describe?.abort(); s!.describe = null;
      s!.draft = { title: value.title, inputId: value.content.input.input_id,
        caseIdsText: value.content.selection.case_ids.join('\n'), mode: value.content.execution_intent.mode };
      s!.binding = { kind: revision === undefined ? 'head' : 'fixed', planId: value.plan_id, baseRevision: value.revision };
      s!.pin = value.content.input; s!.descriptor = null; modified();
      // Finish this operation before advancing the draft binding. Old handlers
      // and any previous describe cannot act on the newly loaded body.
      s!.op = null; s!.epoch++; bump();
    } catch { if (current()) s!.error = '无法读取固定草稿；当前正文已保留。'; }
    finally { if (current()) { s!.op = null; bump(); } }
  };
  // A new render's descriptor request belongs to the adopted draft epoch.
  // Frozen revision reads themselves never depend on source availability.
  useEffect(() => {
    if (active() && s?.draft.inputId && !s.descriptor && !s.describe && registered(s.draft.inputId)) void readInput(s.draft.inputId);
  }, [generation, epoch]);

  const validation = (): string | null => {
    if (!s || !owner.current.enabled) return '当前任务尚未加载。';
    if (!text(s.draft.title, 500)) return '请填写草稿标题（最多 500 字）';
    if (!s.draft.inputId || !registered(s.draft.inputId)) return '请明确选择一个已登记的完整 Notebook 输入。';
    // Catalogue validation and deep freezing happen once at the HTTP boundary.
    // Keystrokes only check the live binding and explicit selection membership.
    if (!s.descriptor || !s.pin || s.descriptor.input_id !== s.draft.inputId || !sameIdentity(s.pin, inputIdentity(s.descriptor))) return '请先读取并核对登记输入目录。';
    const chosen = s.draft.caseIdsText.split(/\r?\n/);
    if (!ids(chosen)) return 'case ID 必须一行一个、非空且不重复，不能包含空白；不会自动修剪或去重。';
    const available = new Set(s.descriptor.case_ids);
    if (!chosen.every(cid => available.has(cid))) return '选题必须全部属于当前完整输入目录。';
    return null;
  };
  const receive = (value: unknown, f: Frozen, allowMissing: boolean): 'saved' | 'missing' | 'invalid' => {
    if (!keys(value, ['request_key', 'record']) || value.request_key !== f.command.request_key) return 'invalid';
    if (value.record === null) return allowMissing ? 'missing' : 'invalid';
    if (!record(value.record, f.taskId, f.workspaceId) || !matches(value.record, f) || !knownOkay(value.record)) return 'invalid';
    remember(value.record); s!.saved = value.record; s!.status = 'saved'; s!.error = null;
    s!.binding = { kind: 'head', planId: value.record.plan_id, baseRevision: value.record.revision };
    return 'saved';
  };
  const send = async (kind: 'save' | 'receipt', f: Frozen) => {
    if (!bound() || s!.op) return;
    const op: Operation = { kind, abort: new AbortController() }; s!.op = op;
    s!.status = kind === 'save' ? 'saving' : 'checking'; s!.error = null; bump();
    const current = () => bound() && s!.op === op && !op.abort.signal.aborted && s!.frozen === f;
    let accepted: PlanDraftRecord | null = null;
    try {
      const url = kind === 'receipt' ? `${path()}/receipt?${new URLSearchParams({ request_key: f.command.request_key })}` : path();
      const value = await api<unknown>(url, kind === 'save' ? f.command : undefined, op.abort.signal);
      if (!current()) return;
      const outcome = receive(value, f, kind === 'receipt');
      if (outcome === 'saved') accepted = s!.saved;
      else { s!.status = 'unknown'; s!.error = outcome === 'missing'
        ? '尚未观察到该 key 的回执；不能据此判断未保存。请稍后查回执或同 key 重试。'
        : '回执 key、格式或原命令绑定不匹配；保存结果未知，原意图保持冻结。'; }
    } catch (error) {
      if (!current()) return;
      if (kind === 'save' && rejected(error, f.command.request_key)) {
        s!.status = 'rejected'; s!.error = '本次草稿命令已被拒绝；正文保留，可核对输入与基础版本后修改。';
      } else { s!.status = 'unknown'; s!.error = '未取得可信保存回执；原命令与 key 已保留，请查回执或同 key 重试。'; }
    } finally {
      if (current()) {
        s!.op = null;
        if (accepted) s!.epoch++;
        bump();
        // Callback exceptions cannot turn an accepted save into an unknown
        // transport outcome, and an obsolete activation never calls it.
        if (accepted && active()) latest.current.onSaved?.(accepted);
      }
    }
  };
  const save = async () => {
    if (!editable() || s!.status === 'saved') return;
    const error = validation(); if (error) { s!.error = error; bump(); return; }
    if (!s!.frozen) {
      const description = s!.descriptor!;
      s!.frozen = { command: immutable({ title: s!.draft.title, input_id: s!.draft.inputId!, expected_bundle_id: description.bundle_id,
        case_ids: s!.draft.caseIdsText.split(/\r?\n/), mode: s!.draft.mode, plan_id: s!.binding.planId,
        base_revision: s!.binding.baseRevision, request_key: requestKey() }), description, actor: s!.actor,
        taskId: s!.taskId, workspaceId: s!.workspaceId, knownPlans: new Set([...s!.known.values()].map(r => r.plan_id)) };
    }
    await send('save', s!.frozen);
  };
  const retrySave = async () => {
    if (!bound() || s!.op || !s!.frozen || !['unknown', 'rejected'].includes(s!.status)) return;
    await send('save', s!.frozen);
  };
  const checkReceipt = async () => {
    if (!bound() || s!.op || !s!.frozen || !['unknown', 'rejected'].includes(s!.status)) return;
    await send('receipt', s!.frozen);
  };
  const deactivate = () => {
    if (!active()) return;
    suspend(s); owner.current.enabled = false; owner.current.generation++; bump();
  };
  const revisions: PlanRevisionLocator[] = [];
  for (const event of task?.history ?? []) {
    if (event.kind !== 'plan.draft.saved' || !object(event.content) || !id(event.object_id)
        || event.content.plan_id !== event.object_id || !id(event.content.revision)) continue;
    if (!revisions.some(r => r.planId === event.object_id && r.revision === event.content.revision)) revisions.push({ planId: event.object_id, revision: event.content.revision });
  }
  const isLocked = !owner.current.enabled || !s || locked(s);
  const blockedReason = validation();
  return {
    draft: s ? { ...s.draft } : body(), binding: s ? { ...s.binding } : binding(),
    status: s?.status ?? 'editing' as PlanDraftStatus, saved: s?.saved ?? null, error: s?.error ?? null,
    selected: s?.known.get(JSON.stringify([s.binding.planId, s.binding.baseRevision])) ?? null,
    descriptor: s?.descriptor ?? null, inputLoading: !!s?.describe, inputs,
    heads: s?.heads ?? [], headsLoading: !!s?.list, listError: s?.listError ?? null, revisions,
    generation, busy: !!s?.op, locked: isLocked, blockedReason,
    canSave: !isLocked && s?.status !== 'saved' && blockedReason === null,
    canRecover: !!s?.frozen && !s.op && owner.current.enabled && ['unknown', 'rejected'].includes(s.status),
    pendingRequestKey: s?.frozen?.command.request_key ?? null,
    setTitle, setCaseIdsText, setMode, selectInput, newDraft, save, retrySave, checkReceipt,
    refreshHeads, openHead: (planId: string) => open(planId),
    openRevision: (planId: string, revision: string) => open(planId, revision), deactivate,
  };
}
export type PlanDraftController = ReturnType<typeof usePlanDrafts>;
