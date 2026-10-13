import { useEffect, useRef, useState } from 'react';
import { requestKey } from '../contracts';
import type { AgentMessageRecord, ContextReference, Task, TaskSession } from '../contracts';
import type { MessageDraft } from './useMessageContext';
import type { MessageBehavior, MessageStatus } from './chatTypes';

type Selection = { reference: ContextReference; sha256: string };
type Refusal = { status: number; error: string; code: string; request_key: string };
type Intent = { taskId: string; actor: string; sessionId: string; agentId: string; key: string;
  draftId: string; version: number; behavior: MessageBehavior; contexts: Selection[];
  textHash?: string; text?: string; intentId?: string; messageId?: string; inputSha256?: string; bodySha256?: string;
  identityUnavailable?: true; refusal?: Refusal };
type Entry = { draftId: string; version: number; behavior: MessageBehavior | null;
  status: MessageStatus; intent?: Intent; receipt?: AgentMessageRecord;
  correction?: boolean; detail?: string; checking?: number; archived: AgentMessageRecord[] };
export type MessageDraftPort = { read: () => MessageDraft; clear: () => void };
const WRITE_LOCK = 'sn-workbench-message-write-owner-v1';
const STORAGE = 'sn-workbench-message-intents-v1';
const binding = (actor: string, task?: Task, session?: TaskSession) => JSON.stringify([actor, task?.id ?? null, session?.id ?? null, session?.agent_id ?? null]);
// Chat default: a new message replaces the Agent's running turn, as in Paseo.
const fresh = (id: string): Entry => ({ draftId: id + ':' + requestKey(), version: 0, behavior: 'interrupt', status: 'idle', archived: [] });
const hash = async (text: string) => Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text))), b => b.toString(16).padStart(2, '0')).join('');
const obj = (v: unknown): v is Record<string, any> => Boolean(v && typeof v === 'object' && !Array.isArray(v));
const hex = (v: unknown) => typeof v === 'string' && /^[a-f0-9]{64}$/.test(v);
const uid = (v: unknown) => typeof v === 'string' && /^[a-f0-9]{12}4[a-f0-9]{3}[89ab][a-f0-9]{15}$/i.test(v);
const uuid = (v: unknown) => typeof v === 'string' && /^[a-f0-9]{8}-[a-f0-9]{4}-[1-8][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/i.test(v);
const same = (a: unknown, b: unknown): boolean => {
  if (a === b) return true;
  if (Array.isArray(a) && Array.isArray(b)) return a.length === b.length && a.every((v, i) => same(v, b[i]));
  if (obj(a) && obj(b)) return Object.keys(a).length === Object.keys(b).length && Object.keys(a).every(k => Object.hasOwn(b, k) && same(a[k], b[k]));
  return false;
};
const closed = (v: unknown, required: string[], optional: string[] = []) => obj(v)
  && required.every(k => Object.hasOwn(v, k)) && Object.keys(v).every(k => required.includes(k) || optional.includes(k));
const bridgeCodes = ['INVALID_CONFIG', 'NOT_CONNECTED', 'CONNECTION_UNAVAILABLE', 'CONNECT_FAILED', 'READ_FAILED', 'CATALOG_UNAVAILABLE', 'CLOSED', 'CLOSE_FAILED', 'INVALID_INPUT', 'IDENTITY_MISMATCH', 'SCOPE_MISMATCH', 'RESPONSE_TOO_LARGE', 'INTERNAL_ERROR'];
const precheckCodes = [...bridgeCodes, 'AGENT_NOT_FOUND', 'AGENT_ARCHIVED', 'INCOMPLETE_SNAPSHOT', 'PERMISSION_REQUIRED', 'AGENT_UNAVAILABLE', 'BRIDGE_INVALID_COMMAND', 'BRIDGE_UNAVAILABLE'];
const unknownCauses = [...bridgeCodes, 'BRIDGE_TIMEOUT', 'BRIDGE_PROCESS_FAILED', 'BRIDGE_INVALID_RESPONSE', 'BRIDGE_UNAVAILABLE', 'SEND_FAILED'];
const refusalContracts = [
  [409, 'MESSAGE_CONTEXT_CHANGED_PREWRITE', 'Selected message context changed before write'],
  [422, 'MESSAGE_CONTEXT_UNAVAILABLE_PREWRITE', 'Selected message context unavailable before write'],
  [422, 'MESSAGE_BRIDGE_UNCONFIGURED_PREWRITE', 'Message bridge is not configured'],
  [422, 'MESSAGE_INPUT_REJECTED_PREWRITE', 'Message input rejected before write'],
] as const;
const refused = (status: number, value: unknown, key: string): value is Omit<Refusal, 'status'> =>
  closed(value, ['error', 'code', 'request_key']) && obj(value) && value.request_key === key
  && refusalContracts.some(([expected, code, error]) => status === expected && value.code === code && value.error === error);
const savedRefusal = (value: unknown, key: string): value is Refusal => obj(value)
  && closed(value, ['status', 'error', 'code', 'request_key'])
  && refused(value.status, { error: value.error, code: value.code, request_key: value.request_key }, key);

// A structurally valid alternative is still a different frozen server message.
// Recheck synchronously when pinning, because two validators may await hashes.
const identityMatches = (intent: Intent, record: AgentMessageRecord) => !intent.identityUnavailable
  && (intent.intentId === undefined || intent.intentId === record.id)
  && (intent.messageId === undefined || intent.messageId === record.message_id)
  && (intent.inputSha256 === undefined || intent.inputSha256 === record.input_sha256)
  && (intent.bodySha256 === undefined || intent.bodySha256 === record.body_sha256);
const rememberIdentity = (intent: Intent, record: AgentMessageRecord) => {
  if (!identityMatches(intent, record)) return false;
  intent.intentId = record.id; intent.messageId = record.message_id;
  intent.inputSha256 = record.input_sha256; intent.bodySha256 = record.body_sha256;
  return true;
};

async function validated(value: unknown, intent: Intent): Promise<AgentMessageRecord | null> {
  if (!obj(value) || !uid(value.id) || !uuid(value.agent_id) || !uuid(value.message_id)
      || (intent.intentId && value.id !== intent.intentId) || value.task_id !== intent.taskId || value.actor !== intent.actor
      || value.session_id !== intent.sessionId || value.agent_id !== intent.agentId || value.request_key !== intent.key
      || value.active_turn_behavior !== intent.behavior || typeof value.text !== 'string'
      || (intent.text !== undefined && value.text !== intent.text) || (intent.textHash && await hash(value.text) !== intent.textHash)
      || !same(value.contexts, intent.contexts) || value.body_format !== 'workbench-message-v1'
      || !hex(value.input_sha256) || !hex(value.body_sha256) || !identityMatches(intent, value as AgentMessageRecord)
      || typeof value.prepared_at !== 'string' || !value.prepared_at
      || ![null, 'string'].includes(value.started_at === null ? null : typeof value.started_at)
      || ![null, 'string'].includes(value.finished_at === null ? null : typeof value.finished_at)) return null;
  if (value.status === 'prepared') return value.envelope === null && value.started_at === null && value.finished_at === null ? value as AgentMessageRecord : null;
  if (value.status === 'unknown' && value.envelope === null) return typeof value.started_at === 'string' && value.finished_at === null ? value as AgentMessageRecord : null;
  if (!closed(value.envelope, ['result', 'release'])) return null;
  const result = value.envelope.result, release = value.envelope.release;
  if (!obj(result) || result.status !== value.status || typeof value.started_at !== 'string' || !value.started_at
      || typeof value.finished_at !== 'string' || !value.finished_at) return null;
  if (value.status === 'not_attempted') {
    if (!closed(result, ['status', 'code'], ['agentId', 'messageId']) || !precheckCodes.includes(result.code)
        || (result.agentId !== undefined && result.agentId !== value.agent_id)
        || (result.messageId !== undefined && result.messageId !== value.message_id)) return null;
  } else if (value.status === 'acknowledged' || value.status === 'unknown') {
    if (!closed(result, value.status === 'unknown' ? ['status', 'agentId', 'messageId', 'code', 'cause'] : ['status', 'agentId', 'messageId'], value.status === 'unknown' ? ['observed'] : [])
        || result.agentId !== value.agent_id || result.messageId !== value.message_id) return null;
    if (value.status === 'unknown' && (result.code !== 'SEND_OUTCOME_UNKNOWN' || !unknownCauses.includes(result.cause)
        || (result.observed !== undefined && (!closed(result.observed, ['sdkResolved']) || result.observed.sdkResolved !== true)))) return null;
  } else return null;
  if (!obj(release)) return null;
  if (release.status === 'unavailable') {
    if (!closed(release, ['status', 'code']) || !['CLOSE_FAILED', 'BRIDGE_RELEASE_UNKNOWN'].includes(release.code)) return null;
  } else if (!closed(release, ['status']) || (release.status !== 'ok' && !(release.status === 'not_needed' && value.status === 'not_attempted'))) return null;
  return value as AgentMessageRecord;
}

// Intent ownership is separate from preview ownership. Only these refs grant
// writes/dispatch; React state is a rendering projection, never the lock.
export function useAgentMessages(task: Task | undefined, session: TaskSession | undefined, actor: string,
  sessionGeneration: number, capable: boolean, targetAvailable: boolean,
  port: { current: MessageDraftPort | null }) {
  const entries = useRef<Record<string, Entry>>({});
  const archiveIntents = useRef<Intent[]>([]);
  const [revision, render] = useState(0);
  const storageFailure = useRef(false);
  const lease = useRef<object | null>(null);
  const leaseQueue = useRef<Promise<unknown>>(Promise.resolve());
  const [writeOwner, setWriteOwner] = useState<'acquiring' | 'owned' | 'blocked' | 'unsupported'>('acquiring');
  const restore = () => {
    entries.current = {}; archiveIntents.current = []; storageFailure.current = false;
    try {
      const saved = JSON.parse(localStorage.getItem(STORAGE) ?? '[]');
      if (!Array.isArray(saved)) throw Error();
      for (const i of saved) {
        if (!obj(i) || !['taskId', 'actor', 'sessionId', 'agentId', 'key', 'draftId'].every(k => typeof i[k] === 'string' && i[k])
            || !uuid(i.agentId) || !hex(i.textHash) || !Number.isSafeInteger(i.version) || i.version < 0
            || !['interrupt', 'steer'].includes(i.behavior) || !Array.isArray(i.contexts)) throw Error();
        if ((i.intentId !== undefined && !uid(i.intentId)) || (i.messageId !== undefined && !uuid(i.messageId))
            || (i.inputSha256 !== undefined && !hex(i.inputSha256)) || (i.bodySha256 !== undefined && !hex(i.bodySha256))
            || (i.identityUnavailable !== undefined && i.identityUnavailable !== true)) throw Error();
        if (i.refusal !== undefined && !savedRefusal(i.refusal, i.key)) throw Error();
        const identity = ['intentId', 'messageId', 'inputSha256', 'bodySha256'];
        // Only genuinely pre-observation metadata can establish a first tuple.
        // Older observed records omitted it; the original identity is unknowable.
        if ((identity.some(k => i[k] !== undefined) || i.archived === true)
            && !identity.every(k => i[k] !== undefined)) i.identityUnavailable = true;
        if (i.archived === true) { archiveIntents.current.push(i as Intent); continue; }
        const id = JSON.stringify([i.actor, i.taskId, i.sessionId, i.agentId]);
        if (entries.current[id]) throw Error('Duplicate saved message binding');
        entries.current[id] = { draftId: i.draftId, version: i.version, behavior: i.behavior, status: i.refusal ? 'error' : 'unknown', correction: false, intent: i as Intent, archived: [],
          detail: i.identityUnavailable ? '旧恢复记录缺少完整消息身份，保留未知锁，不能猜测原 ACK' : undefined };
      }
    } catch { storageFailure.current = true; }
  };
  useEffect(() => {
    let cancelled = false; const token = {};
    let release: (() => void) | undefined;
    const locks = navigator.locks;
    if (!locks) { setWriteOwner('unsupported'); return; }
    // Serialize StrictMode cleanup/reacquisition, including a held request's release.
    leaseQueue.current = leaseQueue.current.catch(() => {}).then(async () => {
      if (cancelled) return;
      await locks.request(WRITE_LOCK, { mode: 'exclusive', ifAvailable: true }, async lock => {
        if (cancelled) return;
        if (!lock) { setWriteOwner('blocked'); return; }
        restore(); // Constructor snapshots cannot authorize writes after another page.
        lease.current = token; setWriteOwner('owned'); render(n => n + 1);
        await new Promise<void>(resolve => { release = resolve; });
      });
    }).catch(() => { if (!cancelled) setWriteOwner('blocked'); });
    return () => { cancelled = true; if (lease.current === token) lease.current = null; release?.(); };
  }, []);
  const owns = (token: object | null) => token !== null && lease.current === token;
  const id = binding(actor, task, session);
  const scope = useRef({ key: '', generation: 0 });
  const scopeKey = JSON.stringify([id, sessionGeneration]);
  if (scope.current.key !== scopeKey) scope.current = { key: scopeKey, generation: scope.current.generation + 1 };
  const generation = scope.current.generation;
  const latest = useRef({ id, capable, targetAvailable, task, session, actor });
  latest.current = { id, capable, targetAvailable, task, session, actor };
  entries.current[id] ??= fresh(id);
  const entry = entries.current[id];
  const draftId = entry.draftId;
  const publish = () => render(n => n + 1);
  const active = () => scope.current.key === scopeKey && scope.current.generation === generation;
  const pendingForAgent = () => Object.values(entries.current).some(e => e.intent?.actor === actor && e.intent.agentId === session?.agent_id
    && (['sending', 'unknown'].includes(e.status) || Boolean(e.intent.refusal && !e.correction)));
  const bindingAvailable = () => {
    const current = latest.current;
    return Boolean(current.actor && current.task && current.session && current.session.task_id === current.task.id
      && current.task.sessions.some(saved => saved.id === current.session!.id && saved.task_id === current.task!.id
        && saved.agent_id === current.session!.agent_id));
  };
  const canWrite = () => lease.current !== null && bindingAvailable() && active() && latest.current.id === id && !storageFailure.current && !pendingForAgent()
    && entries.current[id]?.draftId === draftId && entries.current[id].status === 'idle' && !entries.current[id].checking;
  const persist = (token = lease.current) => {
    if (!owns(token)) throw Error('Message write lease unavailable');
    // Preserve keys and bindings before dispatch; no private full text/snapshots.
    const intents = [...Object.values(entries.current).filter(e => e.intent).map(e => e.intent!),
      ...archiveIntents.current.map(i => ({ ...i, archived: true }))].map(i => {
      const { text: _privateText, ...metadata } = i; return metadata;
    });
    localStorage.setItem(STORAGE, JSON.stringify(intents));
  };
  const pin = (intent: Intent, record: AgentMessageRecord, token: object | null) => {
    if (!owns(token)) return false;
    const first = intent.intentId === undefined;
    if (!rememberIdentity(intent, record)) return false;
    if (first) { try { persist(token); } catch { storageFailure.current = true; } }
    return true;
  };
  const apply = (captured: Entry, record: AgentMessageRecord, token: object | null) => {
    if (!owns(token)) return;
    captured.receipt = record;
    captured.status = record.status === 'acknowledged' ? 'accepted' : record.status === 'not_attempted' ? 'error' : 'unknown';
    captured.correction = record.status === 'not_attempted';
    captured.detail = record.status === 'acknowledged' && record.envelope?.release.status === 'unavailable'
      ? 'ACK 已核对，连接释放未确认：' + record.envelope.release.code
      : record.status === 'not_attempted' ? '服务端确认未尝试发送，可显式开始新草稿' : '本地记录仍未确认 ACK，禁止重发';
    try { persist(token); } catch { storageFailure.current = true; }
  };
  const send = async () => {
    if (!canWrite() || !latest.current.capable || !latest.current.targetAvailable || !task || !session || !actor) return;
    const token = lease.current;
    const captured = entries.current[id], draft = port.current?.read();
    if (!draft?.text.trim() || !['interrupt', 'steer'].includes(captured.behavior ?? '')) return;
    const intent: Intent = { taskId: task.id, actor, sessionId: session.id, agentId: session.agent_id, key: requestKey(),
      draftId: captured.draftId, version: captured.version, behavior: captured.behavior!, text: draft.text,
      contexts: draft.attachments.map(a => ({ reference: structuredClone(a.reference), sha256: a.sha256 })) };
    captured.intent = intent; captured.status = 'sending'; captured.detail = undefined; publish(); // lock before first await
    try {
      intent.textHash = await hash(intent.text!);
      if (!owns(token)) return;
      persist(token); // fail closed: no POST when recovery persistence failed
    } catch {
      if (!owns(token)) return;
      captured.status = 'error'; captured.correction = true; captured.detail = '恢复身份保存失败，未发送；请显式开始新草稿';
      if (active()) publish(); return;
    }
    // Hashing can outlive the binding. Before the first HTTP side effect,
    // recheck lifecycle and capability; an old actor must not use new cookies.
    if (!owns(token) || !active() || !bindingAvailable() || latest.current.id !== id || !latest.current.capable || !latest.current.targetAvailable
        || entries.current[id] !== captured || captured.draftId !== intent.draftId || captured.version !== intent.version) {
      captured.status = 'unknown'; captured.detail = '发送前目的地已变化，保留原请求身份；请在原任务核对';
      if (active()) publish();
      return;
    }
    try {
      const response = await fetch(`/api/tasks/${intent.taskId}/agent-messages`, { method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-Workbench': '1' }, body: JSON.stringify({ session_id: intent.sessionId,
          text: intent.text, contexts: intent.contexts, request_key: intent.key, active_turn_behavior: intent.behavior }) });
      const value = await response.json();
      if (!owns(token)) return;
      if (!response.ok) {
        if (refused(response.status, value, intent.key)) {
          intent.refusal = { status: response.status, ...value };
          captured.status = 'error'; captured.correction = true;
          captured.detail = '服务端确认在写入前拒绝，可显式开始新草稿';
          try { persist(token); } catch { storageFailure.current = true; }
          if (active() && entries.current[id] === captured) publish();
          return;
        }
        throw Error('结果未确认');
      }
      const record = await validated(value, intent);
      if (record && captured.version === intent.version && captured.draftId === intent.draftId && pin(intent, record, token)) {
        if (active() && entries.current[id] === captured) apply(captured, record, token);
        else if (record.status === 'acknowledged') captured.archived.push(record); // late ACK cannot unlock current scope
      } else if (owns(token) && active() && (captured.status as MessageStatus) !== 'accepted') { captured.status = 'unknown'; captured.detail = '回执字段无法与冻结意图匹配'; }
    } catch { if (owns(token) && active()) { captured.status = 'unknown'; captured.detail = '发送结果未知，只能核对原请求'; } }
    finally {
      if (!owns(token)) return;
      if (captured.status === 'sending') captured.status = 'unknown';
      if (active()) publish();
    }
  };
  const check = async () => {
    const token = lease.current;
    if (!owns(token) || !active() || !task || !actor || storageFailure.current) return;
    const captured = entries.current[id];
    if (!captured) return;
    const intent = captured.intent;
    const oldIntents = archiveIntents.current.filter(i => i.actor === actor && i.taskId === task.id
      && i.sessionId === session?.id && i.agentId === session.agent_id);
    if ((!intent && !oldIntents.length) || captured.checking === generation || captured.status === 'sending') return;
    captured.checking = generation; if (intent?.refusal) captured.correction = false; publish();
    try {
      const response = await fetch(`/api/tasks/${task.id}/agent-messages`, { credentials: 'same-origin', headers: { 'X-Workbench': '1' } });
      if (!response.ok) throw Error();
      const records: unknown = await response.json();
      if (!Array.isArray(records)) throw Error();
      for (const old of oldIntents) {
        const matches = records.filter(r => obj(r) && r.request_key === old.key);
        const checked = matches.length === 1 ? await validated(matches[0], old) : null;
        const receipt = checked && pin(old, checked, token) ? checked : null;
        if (!owns(token) || !active() || entries.current[id] !== captured || captured.checking !== generation) return;
        if (receipt?.status === 'acknowledged' && !captured.archived.some(r => r.id === receipt.id)) captured.archived.push(receipt);
      }
      if (!intent) return;
      const matches = records.filter(r => obj(r) && r.request_key === intent.key);
      if (intent.refusal) {
        if (!owns(token) || !active() || entries.current[id] !== captured || captured.checking !== generation) return;
        if (matches.length === 0) {
          captured.status = 'error'; captured.correction = true;
          captured.detail = '原请求无本地消息记录，保留服务端已确认的写入前拒绝，可显式开始新草稿';
        } else {
          delete intent.refusal; captured.status = 'unknown'; captured.correction = false;
          captured.detail = '写入前拒绝与本地同键记录冲突，保留未知锁';
          try { persist(token); } catch { storageFailure.current = true; }
        }
        return;
      }
      const checked = matches.length === 1 ? await validated(matches[0], intent) : null;
      const record = checked && pin(intent, checked, token) ? checked : null;
      if (!owns(token) || !active() || entries.current[id] !== captured || captured.checking !== generation) return;
      if (record && captured.version === intent.version && captured.draftId === intent.draftId
          && (captured.status !== 'accepted' || record.status === 'acknowledged')) apply(captured, record, token);
      else if (captured.status === 'accepted') captured.detail = '已核对的 SDK ACK 保留；本次读取未取得同一权威回执';
      else { captured.status = 'unknown'; captured.detail = '原请求缺失或回执无效，不能证明未发送'; }
    } catch { if (owns(token) && active() && captured.checking === generation) { captured.detail = '本地请求核对失败，保留未知锁'; } }
    finally { if (owns(token) && captured.checking === generation) { captured.checking = undefined; if (active()) publish(); } }
  };
  useEffect(() => {
    // Returning A->B->A uses this new generation's local GET, never old POST.
    if (lease.current === null) return;
    const current = entries.current[id];
    if (current.status === 'sending') current.status = 'unknown';
    if ((current.intent && (current.status === 'unknown' || Boolean(current.intent.refusal))) || archiveIntents.current.some(i => i.actor === actor
        && i.taskId === task?.id && i.sessionId === session?.id && i.agentId === session.agent_id)) void check();
  }, [scopeKey, writeOwner]);
  useEffect(() => () => { scope.current = { key: '', generation: scope.current.generation + 1 }; }, []);
  const checking = entry.checking === generation;
  const canNewDraft = lease.current !== null && active() && !checking && !storageFailure.current && (entry.status === 'accepted' || (entry.status === 'error' && entry.correction === true));
  const newDraft = () => {
    const current = entries.current[id];
    if (lease.current === null || !current || !active() || current.checking === generation || storageFailure.current
        || !(current.status === 'accepted' || (current.status === 'error' && current.correction === true))) return;
    if (current.status === 'accepted' && current.intent && !archiveIntents.current.some(i => i.key === current.intent!.key
        && i.actor === actor && i.taskId === task?.id)) archiveIntents.current.push({ ...current.intent });
    const next = fresh(id);
    next.archived = [...current.archived, ...(current.status === 'accepted' && current.receipt ? [current.receipt] : [])];
    entries.current[id] = next;
    try { persist(); } catch { storageFailure.current = true; }
    publish(); // new identity yields an empty context draft; no implicit clearing
  };
  void revision;
  return { writeOwner, canCheck: Boolean(entry.intent || archiveIntents.current.length), draftId: entry.draftId, version: entry.version, status: entry.status, behavior: entry.behavior,
    detail: entry.detail, checking, receipt: entry.status === 'accepted' ? entry.receipt : undefined,
    archived: entry.archived, intentId: entry.intent?.intentId ?? null,
    edit: { canWrite, onWritten: () => { if (canWrite()) { entries.current[id].version++; publish(); } } },
    onBehaviorChange: (behavior: MessageBehavior) => { if (canWrite() && ['interrupt', 'steer'].includes(behavior)
      && entries.current[id].behavior !== behavior) { entries.current[id].behavior = behavior; entries.current[id].version++; publish(); } },
    send: () => { void send(); }, check: () => { void check(); }, canNewDraft, newDraft,
    editingLocked: !canWrite(),
    canReadOldReceipts: archiveIntents.current.some(i => i.actor === actor && i.taskId === task?.id
      && i.sessionId === session?.id && i.agentId === session.agent_id),
    blockedReason: writeOwner !== 'owned' ? writeOwner === 'unsupported'
      ? '当前浏览器不支持消息写入锁，禁止编辑和发送' : writeOwner === 'blocked'
        ? '另一工作台页面拥有消息写入权；请关闭另一页面后重新加载本页' : '正在取得消息写入权，禁止编辑和发送'
      : storageFailure.current ? '恢复身份不可用，禁止发送' : pendingForAgent() ? '此 Agent 存在未决消息意图，请在原任务核对'
      : entry.status !== 'idle' ? '当前意图已冻结，请显式开始新消息草稿' : !capable ? '当前服务未配置消息发送能力'
        : !session ? '请先明确选择消息会话' : !bindingAvailable() ? '消息会话不属于当前任务，禁止发送' : !targetAvailable ? '当前消息目的地不可用，请读取会话状态' : null };
}
