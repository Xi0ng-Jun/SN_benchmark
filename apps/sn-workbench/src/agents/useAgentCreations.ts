import { useEffect, useRef, useState } from 'react';
import { AgentCreationPanelProps, AgentCreationProfile, AgentCreationReceipt, AgentCreationStatus } from './AgentCreationPanel';
import { AgentCreationRecord, CreationProfile, Task } from '../contracts';
import { requestKey } from '../contracts';

type Role = 'analyst' | 'implementer';
type Draft = { taskId: string; actor: string; profileId: string | null; title: string; key: string | null;
  role?: Role; hypothesisId?: string; candidateId?: string;
  intentId: string | null; status: AgentCreationStatus; detail?: string; receipt: AgentCreationReceipt | null;
  record: AgentCreationRecord | null; correctable: boolean; checking: boolean };
type ListState = { status: 'loading' | 'ready' | 'error'; records: AgentCreationRecord[]; detail?: string };
type Intent = Readonly<Pick<Draft, 'taskId' | 'actor' | 'profileId' | 'title' | 'key'> & {
  intentId?: string; agentId?: string; sessionId?: string; profile?: Readonly<CreationProfile> }>;

export type AgentCreationRecordView = AgentCreationRecord;

const scopeOf = (taskId: string | null, actor: string) => JSON.stringify([taskId, actor]);
const isNonBlank = (value: unknown): value is string => typeof value === 'string' && value.trim() !== '';
const statusesWithPendingOutcome = new Set(['prepared', 'unknown']);
// Repository uid() is UUID4 hex; remote IDs use the bridge's standard UUID.
const localId = (value: unknown): value is string => typeof value === 'string' && /^[0-9a-f]{12}4[0-9a-f]{3}[89ab][0-9a-f]{15}$/i.test(value);
const remoteId = (value: unknown): value is string => typeof value === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
const bridgeCodes = ['INVALID_CONFIG', 'NOT_CONNECTED', 'CONNECTION_UNAVAILABLE', 'CONNECT_FAILED', 'READ_FAILED',
  'CATALOG_UNAVAILABLE', 'CLOSED', 'CLOSE_FAILED', 'INVALID_INPUT', 'IDENTITY_MISMATCH', 'SCOPE_MISMATCH',
  'RESPONSE_TOO_LARGE', 'INTERNAL_ERROR'];

function pendingRecordValid(record: AgentCreationRecord): boolean {
  if (record.session_id !== null) return false;
  if (record.envelope === null) return record.status === 'prepared' || record.status === 'unknown';
  const { result, release } = record.envelope;
  if (!result || !release || result.status !== record.status) return false;
  const releaseValid = release.status === 'ok' && Object.keys(release).length === 1
    || release.status === 'not_needed' && record.status === 'not_attempted' && Object.keys(release).length === 1
    || release.status === 'unavailable' && ['CLOSE_FAILED', 'BRIDGE_RELEASE_UNKNOWN'].includes(release.code ?? '') && Object.keys(release).length === 2;
  if (!releaseValid) return false;
  if (record.status === 'not_attempted') return [...bridgeCodes, 'WORKSPACE_NOT_FOUND', 'WORKSPACE_UNAVAILABLE',
    'AGENT_ALREADY_EXISTS', 'BRIDGE_INVALID_COMMAND', 'BRIDGE_UNAVAILABLE'].includes(result.code ?? '')
    && (result.agentId === undefined || result.agentId === record.agent_id);
  return record.status === 'unknown' && result.code === 'CREATE_OUTCOME_UNKNOWN' && result.agentId === record.agent_id
    && [...bridgeCodes, 'BRIDGE_TIMEOUT', 'BRIDGE_PROCESS_FAILED', 'BRIDGE_INVALID_RESPONSE', 'BRIDGE_UNAVAILABLE',
      'CREATE_FAILED', 'AGENT_NOT_FOUND'].includes(result.cause ?? '')
    && (result.observed === undefined || remoteId(result.observed?.handleId));
}

function profileView(profile: CreationProfile): AgentCreationProfile {
  return { id: profile.id, label: `${profile.provider}/${profile.model}`, providerLabel: profile.provider,
    modelLabel: profile.model, modeLabel: profile.modeId ?? null, effortLabel: profile.thinkingOptionId ?? null };
}

function titleForRecord(record: AgentCreationRecord): string { return record.title ?? ''; }

function recordMatchesDraft(record: AgentCreationRecord, draft: Intent): boolean {
  return record.task_id === draft.taskId && record.actor === draft.actor && record.profile_id === draft.profileId
    && record.request_key === draft.key && record.title === (draft.title === '' ? null : draft.title)
    && (!draft.intentId || record.id === draft.intentId) && (!draft.agentId || record.agent_id === draft.agentId)
    && (!draft.sessionId || record.session_id === draft.sessionId);
}

function recordIdentity(record: AgentCreationRecord): boolean {
  return localId(record.id) && remoteId(record.agent_id) && isNonBlank(record.task_id) && isNonBlank(record.actor)
    && isNonBlank(record.profile_id) && isNonBlank(record.request_key)
    && (record.title === null || typeof record.title === 'string');
}

export function creationReceipt(record: AgentCreationRecord, draft: Intent, profile?: CreationProfile): AgentCreationReceipt | null {
  if (!recordMatchesDraft(record, draft) || record.status !== 'created' || !record.envelope) return null;
  const result = record.envelope.result;
  const release = record.envelope.release;
  if (!result || typeof result !== 'object' || !release || typeof release !== 'object') return null;
  const agent = result.agent;
  if (!recordIdentity(record) || result.status !== 'created' || result.agentId !== record.agent_id
      || !localId(record.session_id) || typeof result.ready !== 'boolean'
      || (result.ready ? result.readiness !== 'idle' : !['not_idle', 'archived', 'incomplete_snapshot'].includes(result.readiness ?? ''))
      || !agent || agent.id !== record.agent_id || !isNonBlank(agent.provider) || typeof agent.model !== 'string'
      || (profile && (agent.provider !== profile.provider || agent.model !== profile.model))
      || (agent.status !== undefined && !['initializing', 'idle', 'running', 'error', 'closed'].includes(agent.status))
      || (agent.status === undefined && result.readiness !== 'incomplete_snapshot')
      || (result.ready && agent.status !== 'idle')
      || !(release.status === 'ok' && Object.keys(release).length === 1
        || release.status === 'unavailable' && ['CLOSE_FAILED', 'BRIDGE_RELEASE_UNKNOWN'].includes(release.code ?? '') && Object.keys(release).length === 2)) return null;
  return { current: true, intentId: record.id, profileId: record.profile_id, submittedTitle: draft.title,
    agentId: record.agent_id, sessionId: record.session_id, ready: result.ready, readiness: result.readiness!,
    agentStatus: agent.status ?? null, release };
}

async function readJson(path: string, init?: RequestInit): Promise<{ response: Response; value: unknown }> {
  const response = await fetch('/api' + path, { credentials: 'same-origin', headers: { 'X-Workbench': '1', 'Content-Type': 'application/json' }, ...init });
  let value: unknown = null;
  try { value = await response.json(); } catch { /* malformed or empty response is handled by the caller */ }
  return { response, value };
}

function errorText(value: unknown, fallback: string): string {
  return typeof value === 'object' && value !== null && typeof (value as { error?: unknown }).error === 'string'
    ? (value as { error: string }).error : fallback;
}

export function useAgentCreations(task: Task | undefined, actor: string, compact: boolean, configured: boolean,
  configuredProfiles: readonly CreationProfile[] = [], onCreated?: (taskId: string) => void) {
  const taskId = task?.id ?? null;
  const scopeKey = scopeOf(taskId, actor);
  const scopeRef = useRef({ key: scopeKey, generation: 0 });
  const draftsRef = useRef<Record<string, Draft>>({});
  const listsRef = useRef<Record<string, ListState>>({});
  const readsRef = useRef<Record<string, AbortController>>({});
  const intentsRef = useRef<Record<string, Record<string, Intent>>>({});
  const checksRef = useRef(new Map<string, object>());
  const configurationRef = useRef({ configured, profiles: configuredProfiles });
  configurationRef.current = { configured, profiles: configuredProfiles };
  const [, redraw] = useState(0);
  const bump = () => redraw(value => value + 1);
  if (scopeRef.current.key !== scopeKey) {
    const previous = draftsRef.current[scopeRef.current.key];
    if (previous?.status === 'saving') previous.status = 'unknown';
    if (previous) previous.checking = false;
    checksRef.current.delete(scopeRef.current.key);
    scopeRef.current = { key: scopeKey, generation: scopeRef.current.generation + 1 };
  }
  const generation = scopeRef.current.generation;
  const [opened, setOpened] = useState(false);
  const current = taskId ? (draftsRef.current[scopeKey] ??= {
    taskId, actor, profileId: null, title: '', key: null, intentId: null, status: 'idle', receipt: null,
    record: null, correctable: false, checking: false,
  }) : undefined;
  const list = listsRef.current[scopeKey] ?? { status: 'loading' as const, records: [] };
  const active = () => scopeRef.current.key === scopeKey && scopeRef.current.generation === generation;

  useEffect(() => {
    if (!taskId || !actor) return;
    const old = readsRef.current[scopeKey]; old?.abort();
    const controller = new AbortController(); readsRef.current[scopeKey] = controller;
    const previous = listsRef.current[scopeKey] ?? { status: 'loading' as const, records: [] };
    listsRef.current[scopeKey] = { ...previous, status: 'loading', detail: undefined }; bump();
    void (async () => {
      try {
        const { response, value } = await readJson(`/tasks/${encodeURIComponent(taskId)}/agent-creations`, { signal: controller.signal });
        if (!active() || controller.signal.aborted) return;
        if (!response.ok || !Array.isArray(value)) throw new Error(errorText(value, '创建记录读取失败'));
        listsRef.current[scopeKey] = { status: 'ready', records: value as AgentCreationRecord[] }; bump();
      } catch (error) {
        if (!active() || controller.signal.aborted) return;
        listsRef.current[scopeKey] = { status: 'error', records: previous.records, detail: (error as Error).message }; bump();
      }
    })();
    return () => controller.abort();
  }, [scopeKey, taskId, actor]);

  const update = (next: Partial<Draft>) => {
    if (!current) return;
    Object.assign(current, next); bump();
  };
  const unresolved = () => {
    const latest = listsRef.current[scopeKey];
    return !latest || latest.status !== 'ready' || latest.records.some(record => statusesWithPendingOutcome.has(record.status));
  };
  const available = configured && configuredProfiles.length > 0;
  const mayStartNewIntent = () => Boolean(configurationRef.current.configured && configurationRef.current.profiles.length > 0
    && current && !current.checking && !unresolved()
    && (current.status === 'created' || current.status === 'error' && current.correctable));
  const newIntent = () => {
    if (!active() || !mayStartNewIntent()) return;
    update({ profileId: null, title: '', key: null, intentId: null, status: 'idle', detail: undefined,
      receipt: null, record: null, correctable: false, checking: false });
  };
  const setProfile = (profileId: string) => {
    if (!active() || !current || current.status === 'saving' || current.status === 'unknown' || current.checking) return;
    if (!configurationRef.current.profiles.some(profile => profile.id === profileId)) return;
    update({ profileId, receipt: null, record: null, detail: undefined });
  };
  const setTitle = (title: string) => {
    if (!active() || !current || current.status === 'saving' || current.status === 'unknown' || current.checking) return;
    update({ title, receipt: null, record: null, detail: undefined });
  };
  // Settlement has exactly one caller-supplied immutable expectation. It never
  // chooses an intent from a response key. POST/check capture this before IO;
  // only selectRecord may explicitly establish a historical record binding.
  const publishRecord = (record: AgentCreationRecord, expected: Intent) => {
    if (!active() || !current || !expected.key) return;
    const intents = intentsRef.current[scopeKey] ??= Object.create(null) as Record<string, Intent>;
    if (!recordIdentity(record) || !recordMatchesDraft(record, expected)) {
      update({ status: 'unknown', receipt: null, record: null, correctable: false, checking: false,
        detail: '创建记录与冻结意图不一致，仍需继续核对' }); return;
    }
    const next: Draft = { ...current, profileId: expected.profileId, title: expected.title, key: expected.key,
      intentId: record.id, status: 'unknown', detail: undefined, receipt: null, record: null, correctable: false, checking: false };
    if (record.status === 'created') {
      const receipt = creationReceipt(record, expected, expected.profile);
      if (receipt) Object.assign(next, { status: 'created', receipt, record });
      else next.detail = '创建记录回执字段不完整，仍需继续核对';
    } else if (record.status === 'prepared' || record.status === 'unknown' || record.status === 'not_attempted') {
      next.detail = record.status === 'prepared' ? '服务器已准备创建意图，尚未确认结果' : '创建结果尚未确认，请核对本地创建记录';
    } else next.detail = `创建记录状态：${record.status}`;
    if (next.receipt || pendingRecordValid(record)) {
      intents[expected.key] = { ...expected, intentId: record.id, agentId: record.agent_id,
        ...(next.receipt ? { sessionId: next.receipt.sessionId } : {}) };
    } else {
      next.intentId = expected.intentId ?? null;
      next.detail = '创建记录回执字段不完整，仍需继续核对';
      // Even malformed first selections retain their original request identity
      // for a later read; they cannot establish server IDs or a fresh key.
      intents[expected.key] = { ...expected };
    }
    Object.assign(current, next); bump();
    if (next.receipt) onCreated?.(current.taskId);
  };
  const check = async () => {
    if (!current || current.checking || !current.key || !active()) return;
    const frozen = intentsRef.current[scopeKey]?.[current.key];
    if (!frozen) return;
    const captured: Intent = { ...frozen };
    const token = {}; checksRef.current.set(scopeKey, token);
    update({ checking: true, detail: undefined });
    try {
      const { response, value } = await readJson(`/tasks/${encodeURIComponent(captured.taskId)}/agent-creations`);
      if (!active() || checksRef.current.get(scopeKey) !== token || draftsRef.current[scopeKey]?.key !== captured.key) return;
      if (!response.ok || !Array.isArray(value)) throw new Error(errorText(value, '创建记录读取失败'));
      listsRef.current[scopeKey] = { status: 'ready', records: value as AgentCreationRecord[] };
      const found = (value as AgentCreationRecord[]).find(record => record.task_id === captured.taskId
        && record.actor === captured.actor && record.request_key === captured.key);
      if (!found) update({ status: 'unknown', checking: false, detail: '尚未找到本地创建记录，请稍后再次核对' });
      else publishRecord(found, captured);
    } catch (error) {
      if (active() && draftsRef.current[scopeKey]?.key === captured.key) update({ status: 'unknown', checking: false, detail: (error as Error).message });
    } finally {
      // An abandoned scope releases its own lock. It must never release a
      // newer read's lock after returning to the same task/actor.
      if (checksRef.current.get(scopeKey) === token) {
        checksRef.current.delete(scopeKey);
        const draft = draftsRef.current[scopeKey]; if (draft) draft.checking = false;
        if (active()) bump();
      }
    }
  };
  const create = async () => {
    if (!active() || !current || !taskId || !current.profileId || !configurationRef.current.configured || current.correctable || current.checking || unresolved()
        || current.status === 'saving' || current.status === 'unknown' || current.status === 'created') return;
    if (current.title !== '' && current.title.trim() === '') return;
    const key = current.key ?? requestKey();
    const target = { role: current.role, hypothesisId: current.hypothesisId, candidateId: current.candidateId };
    const intents = intentsRef.current[scopeKey] ??= Object.create(null) as Record<string, Intent>;
    const frozen = intents[key];
    if (frozen && (frozen.profileId !== current.profileId || frozen.title !== current.title)) return;
    const profile = configurationRef.current.profiles.find(item => item.id === current.profileId);
    if (!profile) return;
    intents[key] ??= { taskId, actor, profileId: current.profileId, title: current.title, key,
      profile: { ...profile } };
    const captured: Intent = { ...intents[key] };
    if (!current.key) current.key = key;
    update({ key, status: 'saving', detail: undefined, receipt: null, record: null, correctable: false });
    try {
      const body: Record<string, string> = { profile_id: captured.profileId!, request_key: key };
      if (captured.title !== '') body.title = captured.title;
      if (target.role) body.role = target.role;
      if (target.hypothesisId) body.hypothesis_id = target.hypothesisId;
      if (target.candidateId) body.candidate_id = target.candidateId;
      const { response, value } = await readJson(`/tasks/${encodeURIComponent(taskId)}/agent-creations`, { method: 'POST', body: JSON.stringify(body) });
      if (!active() || draftsRef.current[scopeKey]?.key !== key) return;
      if (!response.ok) {
        if ([400, 401, 403, 404, 409, 413, 422].includes(response.status)) {
          update({ status: 'error', detail: errorText(value, `创建请求被拒绝（${response.status}）`) });
          // The closed CreationAPI validation route emits this exact 422
          // before prepare()/domain writes. Other refusals (including 409)
          // grant no permission to replace the frozen request with a new key.
          if (response.status === 422 && errorText(value, '') === 'Invalid creation request') update({ correctable: true });
        } else update({ status: 'unknown', detail: '创建结果尚未确认，请核对本地创建记录' });
        return;
      }
      if (typeof value !== 'object' || value === null) { update({ status: 'unknown', detail: '创建结果尚未确认，请核对本地创建记录' }); return; }
      const record = value as AgentCreationRecord;
      publishRecord(record, captured);
      const existing = listsRef.current[scopeKey]?.records ?? [];
      const merged = existing.filter(item => item.id !== record.id);
      if (recordIdentity(record) && recordMatchesDraft(record, captured)) listsRef.current[scopeKey] = { status: 'ready', records: [...merged, record] };
      bump();
    } catch {
      if (active() && draftsRef.current[scopeKey]?.key === key) update({ status: 'unknown', detail: '创建结果尚未确认，请核对本地创建记录' });
    }
  };
  const selectRecord = (id: string) => {
    const latest = listsRef.current[scopeKey];
    if (!active() || !current || current.checking || latest?.status !== 'ready' || current.status === 'saving' || current.status === 'unknown') return;
    const record = latest.records.find(item => item.id === id);
    if (!record || !recordIdentity(record) || record.task_id !== current.taskId || record.actor !== current.actor) return;
    const intents = intentsRef.current[scopeKey] ??= Object.create(null) as Record<string, Intent>;
    const expected: Intent = { ...(intents[record.request_key] ?? { taskId: current.taskId, actor: current.actor,
      profileId: record.profile_id, title: titleForRecord(record), key: record.request_key }) };
    intents[expected.key!] ??= expected;
    // This is the only action that deliberately changes the displayed intent.
    update({ profileId: expected.profileId, title: expected.title, key: expected.key,
      intentId: expected.intentId ?? null, receipt: null, record: null, correctable: false });
    publishRecord(record, expected);
  };

  const profiles = configuredProfiles.map(profileView);
  const frozen = current?.key ? intentsRef.current[scopeKey]?.[current.key] : undefined;
  const blockedReason = !configured ? '服务器尚未提供 Agent 创建能力'
    : current?.correctable ? '输入校验在写入前被拒绝，请先开始新意图，再修正输入'
    : unresolved() ? '本地创建记录尚未确认，暂不能创建新意图'
    : frozen && (frozen.title !== current?.title || frozen.profileId !== current?.profileId) ? '请求内容已冻结，请核对原意图'
    : current && current.title !== '' && current.title.trim() === '' ? '标题不能只包含空白' : null;
  const panel: AgentCreationPanelProps | null = current ? {
    compact, scopeLabel: task?.title ?? '', profiles, selectedProfileId: current.profileId,
    onProfileSelect: setProfile, title: current.title, onTitleChange: setTitle, status: current.status,
    blockedReason, detail: current.detail, intentId: current.intentId, receipt: current.receipt,
    checking: current.checking, onCreate: create, onCheck: current.intentId ? check : undefined,
    onCancel: () => setOpened(false),
  } : null;
  return {
    available, opened, profiles, panel, status: current?.status ?? 'idle',
    intentId: current?.intentId ?? null, title: current?.title ?? '', records: list.records,
    listStatus: list.status, listDetail: list.detail, checking: current?.checking ?? false,
    open: () => setOpened(true), close: () => setOpened(false),
    // Role of the next creation; an implementer is always for one approved card.
    setTarget: (role: Role, hypothesisId?: string, candidateId?: string) => { if (current && current.status !== 'saving' && current.status !== 'unknown') {
      current.role = role; current.hypothesisId = hypothesisId; current.candidateId = candidateId; } },
    newIntent, check, checkPending: check, selectRecord,
    canNewIntent: mayStartNewIntent(),
    canCheckPending: Boolean(current?.key && !current.intentId && (current.status === 'unknown' || current.status === 'error')),
    historicalReceipt: current?.receipt && current.record && !configuredProfiles.some(profile => profile.id === current.receipt!.profileId)
      ? { receipt: current.receipt, record: current.record } : null,
  };
}
