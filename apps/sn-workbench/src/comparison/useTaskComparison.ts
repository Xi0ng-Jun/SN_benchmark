import { useEffect, useRef, useState } from 'react';
import { api } from '../contracts';
import type { ComparisonSide, Result, Task, TaskComparison } from '../contracts';

export type ComparisonSelectionSide = 'left' | 'right';
type Summary = Omit<Result, 'original_at'>;
type ObjectValue = Record<string, unknown>;
const object = (value: unknown): value is ObjectValue => typeof value === 'object' && value !== null && !Array.isArray(value);
const text = (value: unknown): value is string => typeof value === 'string' && value.trim() !== '';
const hash = (value: unknown): value is string => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value);
const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
const positiveInteger = (value: unknown): value is number => finite(value) && Number.isInteger(value) && value > 0;
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every(text) && new Set(value).size === value.length;
const numbers = (value: unknown): value is Record<string, number> => object(value) && Object.values(value).every(finite);
const counts = (value: unknown): value is Record<string, number> => object(value) && Object.values(value).every(positiveInteger);
const keysAre = (value: ObjectValue, keys: string[]) => Object.keys(value).length === keys.length
  && keys.every(key => Object.hasOwn(value, key));
const reason = (value: unknown) => object(value) && text(value.code) && text(value.message) && text(value.detail);
const method = (value: unknown) => object(value) && text(value.name) && text(value.kind) && ['sn', 'reference', 'published'].includes(value.kind) && object(value.configuration);

function equalJson(left: unknown, right: unknown): boolean {
  if (left === right) return true;
  if (Array.isArray(left) && Array.isArray(right)) return left.length === right.length && left.every((v, i) => equalJson(v, right[i]));
  return object(left) && object(right) && keysAre(right, Object.keys(left)) && Object.keys(left).every(key => equalJson(left[key], right[key]));
}

function sourceHashes(value: unknown): boolean {
  return object(value) && keysAre(value, ['submission', 'scores']) && Object.values(value).every(hash);
}

function summary(value: unknown, id: string): value is Summary {
  if (!object(value) || value.id !== id || !text(value.suite) || !text(value.scope) || !['full', 'subset'].includes(value.scope)
      || !method(value.method) || !positiveInteger(value.case_count) || !positiveInteger(value.bundle_case_count)
      || value.case_count > value.bundle_case_count || !object(value.coverage) || !object(value.metrics)
      || !strings(value.pending_metrics) || !hash(value.scores_id) || !hash(value.submission_id)
      || !hash(value.scorer_id) || !text(value.profile) || !object(value.source) || !sourceHashes(value.source_hashes)) return false;
  return Object.values(value.coverage).every(item => typeof item === 'boolean' || finite(item) && Number.isInteger(item) && item >= 0)
    && Object.values(value.metrics).every(metric => object(metric) && finite(metric.value) && positiveInteger(metric.denominator)
      && metric.denominator <= (value.case_count as number) && text(metric.kind) && ['per-case', 'batch-only'].includes(metric.kind));
}

function side(value: unknown, id: string): value is ComparisonSide {
  if (!object(value) || value.result_id !== id || !object(value.read_reference)
      || !keysAre(value.read_reference, ['kind', 'result_id']) || value.read_reference.kind !== 'result' || value.read_reference.result_id !== id) return false;
  return value.status === 'readable' ? summary(value.summary, id) && value.reason === null
    : value.status === 'unavailable' && value.summary === null && reason(value.reason);
}

function metricScopes(value: unknown, denominators: Record<string, number>, caseIds: string[]): boolean {
  return object(value) && keysAre(value, Object.keys(denominators)) && Object.entries(value).every(([name, ids]) => {
    if (!strings(ids) || ids.length !== denominators[name]) return false;
    const eligible = new Set(ids);
    return equalJson(ids, caseIds.filter(id => eligible.has(id)));
  });
}

function reportMethod(value: unknown, saved: Summary, caseIds: string[]): boolean {
  if (!object(value) || !hash(value.method_id) || !method(value.method) || !equalJson(value.method, saved.method)
      || value.scores_id !== saved.scores_id || value.submission_id !== saved.submission_id || !numbers(value.metrics)
      || !counts(value.metric_denominators) || !keysAre(value.metric_denominators, Object.keys(value.metrics))
      || !metricScopes(value.metric_case_ids, value.metric_denominators, caseIds)
      || !strings(value.pending_metrics) || !equalJson(value.pending_metrics, saved.pending_metrics)
      || !strings(value.batch_only_metrics) || !strings(value.unshared_metrics) || !strings(value.unavailable_metrics)
      || !object(value.files) || value.files.snapshot_id !== saved.id || !equalJson(value.files.source_hashes, saved.source_hashes)) return false;
  return keysAre(value.metrics, Object.keys(saved.metrics)) && Object.entries(saved.metrics).every(([name, metric]) =>
    (value.metrics as Record<string, number>)[name] === metric.value
      && (value.metric_denominators as Record<string, number>)[name] === metric.denominator
      && (value.batch_only_metrics as string[]).includes(name) === (metric.kind === 'batch-only'))
    && value.batch_only_metrics.every(name => Object.hasOwn(saved.metrics, name));
}

function report(value: unknown, left: Summary, right: Summary): boolean {
  if (!object(value) || value.format !== 'benchmark-comparison-v1' || !hash(value.report_id) || !hash(value.bundle_id)
      || value.suite !== left.suite || value.suite !== right.suite || value.scope !== left.scope || value.scope !== right.scope
      || value.profile !== left.profile || value.profile !== right.profile || value.scorer_id !== left.scorer_id || value.scorer_id !== right.scorer_id
      || !positiveInteger(value.case_count) || value.case_count !== left.case_count || value.case_count !== right.case_count
      || !strings(value.case_ids) || value.case_ids.length !== value.case_count
      || !object(value.source) || !equalJson(value.source, left.source) || !equalJson(value.source, right.source)
      || !strings(value.common_metrics) || !value.common_metrics.length || !strings(value.paired_metrics)
      || !value.paired_metrics.every(name => (value.common_metrics as string[]).includes(name))
      || !counts(value.metric_denominators) || !keysAre(value.metric_denominators, value.common_metrics)
      || !metricScopes(value.metric_case_ids, value.metric_denominators, value.case_ids)
      || !Array.isArray(value.methods) || value.methods.length !== 2
      || !reportMethod(value.methods[0], left, value.case_ids) || !reportMethod(value.methods[1], right, value.case_ids)
      || !Array.isArray(value.comparisons) || value.comparisons.length !== 1
      || !object(value.method_differences) || !strings(value.interpretation)) return false;
  const pair = value.comparisons[0];
  if (!object(pair) || pair.direction !== 'right minus left'
      || pair.left_method_id !== value.methods[0].method_id || pair.right_method_id !== value.methods[1].method_id
      || !numbers(pair.batch_differences) || !keysAre(pair.batch_differences, value.common_metrics)
      || !numbers(pair.paired_mean_differences) || !keysAre(pair.paired_mean_differences, value.paired_metrics)
      || !Array.isArray(pair.paired) || pair.paired.length !== value.case_count
      || !Array.isArray(pair.group_differences) || !object(pair.uncertainty)) return false;
  return pair.paired.every((row, index) => object(row) && row.case_id === (value.case_ids as string[])[index]
      && text(row.group_id) && text(row.task) && text(row.left_status) && text(row.right_status)
      && numbers(row.left) && numbers(row.right) && numbers(row.differences)
      && keysAre(row.right, Object.keys(row.left)) && keysAre(row.differences, Object.keys(row.left))
      && Object.keys(row.left).every(name => (value.paired_metrics as string[]).includes(name)))
    && pair.group_differences.every(group => object(group) && text(group.group_id) && positiveInteger(group.case_count) && numbers(group.mean_differences))
    && Object.values(pair.uncertainty).every(detail => object(detail) && typeof detail.computed === 'boolean'
      && (!detail.computed || finite(detail.mean) && Array.isArray(detail.ci95) && detail.ci95.length === 2 && detail.ci95.every(finite)));
}

// Validate the actual response before publishing. This checks identity and
// readable DTO fields; it does not rescore, average, or implement comparison.
function comparison(value: unknown, taskId: string, leftId: string, rightId: string): value is TaskComparison {
  if (!object(value) || value.task_id !== taskId || value.left_result_id !== leftId || value.right_result_id !== rightId
      || !side(value.left, leftId) || !side(value.right, rightId)) return false;
  if (value.status === 'comparable') return value.reason === null && value.left.status === 'readable' && value.right.status === 'readable'
    && value.left.summary !== null && value.right.summary !== null && report(value.report, value.left.summary, value.right.summary);
  return text(value.status) && ['not_comparable', 'unavailable'].includes(value.status) && value.report === null && reason(value.reason)
    && (value.status === 'unavailable' || value.left.status === 'readable' && value.right.status === 'readable');
}

type Owner = { taskId: string | null; membership: string; generation: number; leftId: string | null; rightId: string | null;
  pending: AbortController | null; busy: boolean; error: string | null; data: TaskComparison | null };

/** Explicit, read-only comparison. Parent routing must cancel synchronously
 * before changing routes, and pass undefined while its loaded Task is stale.
 * Each render's actions belong to one generation, including Task/choice ABA.
 */
export function useTaskComparison(task: Task | undefined) {
  const [, redraw] = useState(0);
  const bump = () => redraw(value => value + 1);
  const mounted = useRef(true);
  const latestTask = useRef(task); latestTask.current = task;
  // The ref is authoritative between renders, including the busy lock.
  const owner = useRef<Owner>({ taskId: null, membership: '', generation: 0, leftId: null, rightId: null,
    pending: null, busy: false, error: null, data: null });
  const invalidate = () => {
    const state = owner.current;
    state.generation += 1;
    state.pending?.abort(); state.pending = null;
    state.busy = false; state.error = null; state.data = null;
  };
  const reconcile = () => {
    const latest = latestTask.current;
    const taskId = latest?.id ?? null;
    const linked = latest?.results.map(result => result.id).filter(hash).sort() ?? [];
    const membership = JSON.stringify(linked);
    const state = owner.current;
    if (state.taskId === taskId && state.membership === membership) return false;
    const differentTask = state.taskId !== taskId;
    invalidate(); state.taskId = taskId; state.membership = membership;
    if (differentTask || !linked.includes(state.leftId ?? '')) state.leftId = null;
    if (differentTask || !linked.includes(state.rightId ?? '')) state.rightId = null;
    return true;
  };
  reconcile();
  const state = owner.current;
  const generation = state.generation;
  const active = () => {
    if (reconcile() && mounted.current) bump();
    return mounted.current && owner.current.generation === generation;
  };
  useEffect(() => {
    mounted.current = true; bump();
    return () => { mounted.current = false; invalidate(); };
  }, []);

  const select = (side: ComparisonSelectionSide, id: string | null) => {
    if (!active() || !state.taskId || side !== 'left' && side !== 'right') return;
    const opposite = side === 'left' ? state.rightId : state.leftId;
    if (id !== null && (!latestTask.current?.results.some(result => result.id === id) || !hash(id) || id === opposite)) return;
    const field = side === 'left' ? 'leftId' : 'rightId';
    if (state[field] === id) return;
    state[field] = id; invalidate(); bump();
  };
  const cancel = () => { if (active()) { invalidate(); bump(); } };
  const read = async () => {
    if (!active() || state.pending || !state.taskId || !state.leftId || !state.rightId || state.leftId === state.rightId) return;
    const captured = { taskId: state.taskId, leftId: state.leftId, rightId: state.rightId, generation };
    const pending = new AbortController(); state.pending = pending;
    state.busy = true; state.error = null; state.data = null; bump();
    const current = () => active() && owner.current.pending === pending && !pending.signal.aborted;
    try {
      const query = new URLSearchParams({ left_result_id: captured.leftId, right_result_id: captured.rightId });
      const value = await api<unknown>(`/tasks/${encodeURIComponent(captured.taskId)}/comparison?${query}`, undefined, pending.signal);
      if (!current()) return;
      if (!comparison(value, captured.taskId, captured.leftId, captured.rightId)) throw new Error('比较响应格式或结果绑定无效，请重新读取。');
      state.data = value;
    } catch (error) {
      if (current()) state.error = error instanceof Error ? error.message : '比较读取失败，请重试。';
    } finally {
      // An abandoned request may neither publish nor unlock its successor.
      if (current()) { state.pending = null; state.busy = false; bump(); }
    }
  };
  const blockedReason = !state.taskId ? '当前任务尚未加载。'
    : !state.leftId || !state.rightId ? '请明确选择两个不同的已关联结果。'
      : state.busy ? '正在读取比较结果。' : null;
  return { leftId: state.leftId, rightId: state.rightId, select, read, cancel,
    canRead: blockedReason === null, blockedReason, busy: state.busy, error: state.error, data: state.data, generation };
}
