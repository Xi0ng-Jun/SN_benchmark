import React, { useEffect, useRef, useState } from 'react';
import { Text, TextInput, View } from 'react-native';
import { api, ApiError, PlanDraftRecord, requestKey } from '../contracts';
import { Button, Copy, useTheme } from '../ui';

type Job = { job_id: string; state_version: number; plan_id: string; plan_revision: string };
type Preparation = { preparation: { preparation_id: string; source: { artifact_ref: { id: string } } } };
type Execution = { execution_id: string; job_id: string; state: string; terminal: boolean; exit_code: number | null;
  run_state: { stage?: string; planned?: number } | null };
type RunCommand = { expected_state_version: number; preparation_id: string; budget: { confirmed: boolean; limit_cny: number; timeout_seconds: number }; request_key: string };
type Pending = { jobId: string; command: RunCommand };
function isRunRejection(error: unknown, key: string) {
  if (!(error instanceof ApiError) || !error.value || typeof error.value !== 'object') return false;
  const value = error.value as Record<string, unknown>;
  return value.code === 'JOB_COMMAND_REJECTED' && value.request_key === key && value.operation === 'run';
}
type Profile = { mode: string; models: string[]; max_limit_cny: number; input_ids: string[] };

// Mounted separately per Task. No mutation is automatically retried after an uncertain response.
export function ExecutionPanel({ taskId, onRefresh }: { taskId: string; onRefresh: () => void }) {
  const theme = useTheme();
  const storageKey = `workbench-pending-run:${taskId}`;
  const pending = useRef<Pending | null>(null);
  const persistPending = (value: Pending | null) => {
    if (value) sessionStorage.setItem(storageKey, JSON.stringify(value));
    else sessionStorage.removeItem(storageKey);
    pending.current = value;
  };
  const alive = useRef(true);
  const refresh = useRef(onRefresh); refresh.current = onRefresh;
  const [plans, setPlans] = useState<PlanDraftRecord[]>([]);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [prepared, setPrepared] = useState<{ job: Job; receipt: Preparation; plan: PlanDraftRecord } | null>(null);
  const [execution, setExecution] = useState<Execution | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [limit, setLimit] = useState('1');
  const [timeout, setTimeoutValue] = useState('3600');
  const [uncertain, setUncertain] = useState(false);
  const [reload, setReload] = useState(0);
  const prefix = `/tasks/${encodeURIComponent(taskId)}/jobs`;
  const plan = plans.find(p => p.revision === selected);
  useEffect(() => {
    alive.current = true;
    try {
      const raw = sessionStorage.getItem(storageKey);
      if (raw) { pending.current = JSON.parse(raw); setUncertain(true); }
    } catch { setError('无法恢复原运行请求，请保留当前页面。'); }
    const load = async () => {
      try {
        const [heads, config, jobs] = await Promise.all([
          api<PlanDraftRecord[]>(`/tasks/${encodeURIComponent(taskId)}/plans`),
          api<Profile>('/execution-profile'), api<Job[]>(prefix),
        ]);
        const records = await Promise.all(jobs.map(j => api<Execution | null>(`${prefix}/${j.job_id}/execution`)));
        if (!alive.current) return;
        setPlans(heads); setProfile(config);
        const latest = records.filter((r): r is Execution => !!r).reverse();
        const recovered = latest.find(r => r.job_id === pending.current?.jobId);
        if (recovered) { persistPending(null); setPrepared(null); setUncertain(false); }
        setExecution(latest.find(r => !r.terminal) ?? latest[0] ?? null);
      } catch (cause) { if (alive.current) setError(String(cause)); }
    };
    void load();
    return () => { alive.current = false; };
  }, [taskId, reload]);
  useEffect(() => {
    if (!execution || execution.terminal) return;
    let active = true;
    const poll = async () => {
      try {
        const value = await api<Execution>(`${prefix}/${execution.job_id}/executions/${execution.execution_id}`);
        if (active && alive.current) { setExecution(value); refresh.current(); }
      } catch { if (active) setError('状态读取暂时失败；运行不会自动重发。'); }
    };
    const timer = setInterval(() => void poll(), 2000);
    return () => { active = false; clearInterval(timer); };
  }, [execution?.execution_id, execution?.terminal]);
  const prepare = async () => {
    if (!plan || busy) return;
    setBusy(true); setError(null); setPrepared(null);
    try {
      const job = (await api<{ record: Job }>(prefix, { plan_id: plan.plan_id, revision: plan.revision,
        expected_content_hash: plan.content_hash, request_key: requestKey() })).record;
      const receipt = (await api<{ record: Preparation }>(`${prefix}/${job.job_id}/preparations`, {
        expected_state_version: job.state_version, request_key: requestKey(),
      })).record;
      if (alive.current) { setPrepared({ job, receipt, plan }); setUncertain(false); }
    } catch (cause) { if (alive.current) setError(`准备失败：${String(cause)}`); }
    finally { if (alive.current) setBusy(false); }
  };
  const dispatch = async (original: Pending) => {
    setBusy(true); setError(null); setUncertain(true);
    try {
      persistPending(original);
      const record = (await api<{ record: Execution }>(`${prefix}/${original.jobId}/run`, original.command)).record;
      if (alive.current) { persistPending(null); setExecution(record); setPrepared(null); setUncertain(false); refresh.current(); }
    } catch (cause) {
      const record = await api<Execution | null>(`${prefix}/${original.jobId}/execution`).catch(() => null);
      if (alive.current) {
        if (record) { persistPending(null); setExecution(record); setPrepared(null); setUncertain(false); refresh.current(); }
        else if (isRunRejection(cause, original.command.request_key)) {
          persistPending(null); setUncertain(false); setError(`运行被拒绝：${String(cause)}。请核对预算或重新准备。`);
        } else setError(`运行响应未确认：${String(cause)}。可核对或恢复原请求；不会自动重发。`);
      }
    } finally { if (alive.current) setBusy(false); }
  };
  const run = async () => {
    if (!prepared || busy || uncertain) return;
    await dispatch({ jobId: prepared.job.job_id, command: {
      expected_state_version: prepared.job.state_version, preparation_id: prepared.receipt.preparation.preparation_id,
      budget: { confirmed: true, limit_cny: Number(limit), timeout_seconds: Number(timeout) }, request_key: requestKey(),
    }});
  };
  const cancel = async () => {
    if (!execution || busy) return;
    setBusy(true);
    try {
      const record = (await api<{ record: Execution }>(`${prefix}/${execution.job_id}/executions/${execution.execution_id}/cancel`, { request_key: requestKey() })).record;
      if (alive.current) { setExecution(record); refresh.current(); }
    } catch (cause) { if (alive.current) setError(String(cause)); }
    finally { if (alive.current) setBusy(false); }
  };
  const live = !!execution && !execution.terminal;
  const validBudget = Number.isFinite(Number(limit)) && Number(limit) > 0 && Number(limit) <= (profile?.max_limit_cny ?? 10)
    && Number.isInteger(Number(timeout)) && Number(timeout) > 0 && Number(timeout) <= 86400;
  const inputStyle = { borderWidth: 1, borderColor: theme.line, borderRadius: 6, padding: 8, color: theme.text, width: 140 };
  return <View testID="execution-panel" style={{ gap: 10 }}>
    <Copy small muted>选择已保存计划，准备时固定题单并捕获当前源码。确认运行后启动独立评测，生成结束后进行官方文本评分。</Copy>
    <Button label="重新读取计划与运行" onPress={() => { setReload(n => n + 1); refresh.current(); }} testID="execution-refresh" />
    {!plans.length && <Copy small muted>请先在“计划”页选择题目并保存计划。</Copy>}
    {plans.map(p => <Button key={p.revision} testID={`execution-plan-${p.plan_id}`}
      label={`${selected === p.revision ? '已选 · ' : ''}${p.title} · ${p.content.selection.case_ids.length} 题 · ${p.content.execution_intent.mode}`}
      disabled={busy || live || uncertain} onPress={() => { setSelected(p.revision); setPrepared(null); }} />)}
    {plan && <Copy small>固定版本 {plan.revision.slice(0, 12)} · {plan.content.input.suite} · {plan.content.selection.case_ids.length} 题 · {plan.content.selection.partition_ids.length} 分区 · {plan.content.execution_intent.mode}</Copy>}
    {profile && <Copy small muted>{profile.mode === 'smoke' ? '测试配置：无模型 smoke，不生成评测成绩。' : `模型：${profile.models.join('、')}；费用按请求预留估算，非账户账单。`}</Copy>}
    <Button label={busy ? '处理中…' : '捕获源码并准备运行'} testID="verify-prepare" onPress={prepare}
      disabled={busy || live || uncertain || !plan || !profile?.input_ids.includes(plan.content.input.input_id)} />
    {plan && profile && !profile.input_ids.includes(plan.content.input.input_id) && <Copy small muted>此输入尚未配置可信执行环境；当前支持 QASPER。</Copy>}
    {prepared && <View testID="verify-prepared" style={{ gap: 8 }}>
      <Copy small>准备完成 · {prepared.plan.content.selection.case_ids.length} 题 · {prepared.plan.content.selection.partition_ids.length} 分区 · 源码 {prepared.receipt.preparation.source.artifact_ref.id.slice(0, 12)}</Copy>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <Copy small>费用上限（元，最多 {profile?.max_limit_cny}）</Copy>
        <TextInput testID="verify-budget" value={limit} onChangeText={setLimit} editable={!busy && !uncertain} style={inputStyle} />
        <Copy small>总超时（秒）</Copy>
        <TextInput testID="verify-timeout" value={timeout} onChangeText={setTimeoutValue} editable={!busy && !uncertain} style={inputStyle} />
      </View>
      <Button label="确认预算并运行" testID="verify-run-button" onPress={run} disabled={busy || live || uncertain || !validBudget} />
    </View>}
    {execution && <View testID="verify-status" style={{ gap: 6 }}>
      <Text style={{ color: theme.text, fontSize: 12 }}>执行状态：{execution.state}{execution.run_state?.stage ? ` · ${execution.run_state.stage}` : ''}{execution.exit_code !== null ? ` · 退出码 ${execution.exit_code}` : ''}</Text>
      {live ? <Button label="取消运行" testID="verify-cancel" onPress={cancel} disabled={busy} />
        : <Copy small muted>已保存执行记录。下方关联 run 可查看答案、待读题和官方评分；评分缺失会保持未知。</Copy>}
    </View>}
    {uncertain && <Button label="核对／恢复原运行请求" testID="verify-recover" disabled={busy || !pending.current}
      onPress={() => { if (pending.current) void dispatch(pending.current); }} />}
    {error && <Text testID="verify-error" style={{ color: theme.error, fontSize: 12 }}>{error}</Text>}
  </View>;
}
