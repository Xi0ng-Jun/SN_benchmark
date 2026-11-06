import React, { useEffect, useRef, useState } from 'react';
import { View } from 'react-native';
import { api, CreationProfile, useResource } from '../contracts';
import { Button, Copy, Input, useTheme } from '../ui';
import { Section } from '../tree/parts';
import { CalibrationDetail, Confirmation, controlPath, Forecast, parameterLabels, patchProposal, ProposalItem, ProposalView, simulationKey } from './v41control';
import { ForecastSummary, Tradeoff, V4Calibration } from './V4Calibration';
import { duration } from './contracts';
import { V4Confirmation } from './V4Confirmation';

function Field({ label, ...props }: React.ComponentProps<typeof Input>) {
  return <View style={{ gap: 4 }}><Copy small>{label}</Copy><Input label={label} {...props} /></View>;
}

function ParameterEditor({ name, item, locked, iterationId, onSaved, onDraft }: { name: string; item: ProposalItem; locked: boolean; iterationId: string;
  onSaved: () => void; onDraft: (text: string | null) => void }) {
  const theme = useTheme();
  const [draft, setDraft] = useState(JSON.stringify(item.value)), [busy, setBusy] = useState(false), [error, setError] = useState('');
  useEffect(() => setDraft(JSON.stringify(item.value)), [item.value]);
  const change = (text: string) => {
    setDraft(text); setError('');
    onDraft(text === JSON.stringify(item.value) ? null : text);
  };
  const save = async () => {
    setBusy(true); setError('');
    try { await patchProposal(iterationId, name, JSON.parse(draft)); onDraft(null); onSaved(); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };
  return <View testID={`v41-param-${name}`} style={{ padding: 12, borderWidth: 1, borderColor: theme.line, borderRadius: 8, gap: 5 }}>
    <Copy>{parameterLabels[name] ?? name}</Copy>
    <Copy small>规则值 {JSON.stringify(item.rule_value) ?? '—'} · 顾问值 {item.agent_value == null ? '未提供／未采纳' : JSON.stringify(item.agent_value)} · 当前 {JSON.stringify(item.value)} · 来源 {({ rule: '规则', agent: '顾问', user: '用户', constant: '方法常数' } as Record<string, string>)[item.source] ?? item.source}</Copy>
    <Copy small muted>{item.reason} · 置信度 {({ high: '高', medium: '中', low: '低' } as Record<string, string>)[item.confidence]}</Copy>
    <Copy small muted>允许：{item.range.type}{item.range.min != null ? ` ${item.range.min}–${item.range.max}` : ''}{name === 'calibration_k' ? '；校准完成后不能更改已测遍数' : ''}</Copy>
    <View style={{ flexDirection: 'row', gap: 8, alignItems: 'center' }}>
      <View style={{ flex: 1 }}><Field label="修改值（JSON 数值、布尔值或对象）" value={draft} onChange={change} editable={!locked && !busy && name !== 'calibration_k'} testID={`v41-edit-${name}`} /></View>
      <Button label={busy ? '保存中…' : '保存修改'} onPress={save} disabled={locked || busy || name === 'calibration_k' || draft === JSON.stringify(item.value)} testID={`v41-save-${name}`} />
    </View>
    {!!error && <Copy small style={{ color: theme.error }}>{error}</Copy>}
  </View>;
}

export function NewAutoRunWizard({ iterationId, profiles = [], onStarted }: { iterationId: string; profiles?: CreationProfile[]; onStarted: () => void }) {
  const theme = useTheme(), base = controlPath(iterationId);
  const [tick, setTick] = useState(0), [hours, setHours] = useState('8'), [k, setK] = useState(3);
  const [calibrationConcurrency, setCalibrationConcurrency] = useState('2');
  const [tokens, setTokens] = useState('0.5'), [answers, setAnswers] = useState('0.02'), [delta, setDelta] = useState('');
  const [spend, setSpend] = useState(''), [price, setPrice] = useState(''), [authorized, setAuthorized] = useState(false);
  const [selectedProfiles, setProfiles] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const [preview, setPreview] = useState<{ value: Confirmation; body: Record<string, unknown> } | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({}), [simulation, setSimulation] = useState<Forecast | null>(null), [simError, setSimError] = useState('');
  const restored = useRef(false);
  const draftKey = JSON.stringify(drafts);
  const hasDraft = Object.keys(drafts).length > 0;
  const calibration = useResource<CalibrationDetail>(base + '/calibration', tick);
  const ready = calibration.data?.complete && !calibration.data.alive;
  const proposal = useResource<ProposalView>(ready ? base + '/proposal' : null, tick);
  useEffect(() => {
    if (ready) return;
    const timer = setInterval(() => setTick(t => t + 1), 3000);
    return () => clearInterval(timer);
  }, [ready]);
  useEffect(() => {
    const saved = calibration.data?.launch_inputs;
    if (saved?.adapter) setProfiles(saved.adapter as Record<string, string>);
    if (saved?.test_set_authorized != null) setAuthorized(saved.test_set_authorized === true);
    const inputs = calibration.data?.inputs ?? saved;
    if (!inputs || restored.current) return;
    restored.current = true;
    setHours(String(inputs.time_budget_hours ?? 8)); setK(Number(inputs.calibration_k ?? 3));
    setCalibrationConcurrency(String(calibration.data?.calibration_concurrency ?? saved?.calibration_concurrency ?? 1));
    const safeguards = inputs.safeguards as Record<string, number> | undefined;
    setTokens(String(safeguards?.tokens_per_task_ratio ?? .5)); setAnswers(String(safeguards?.answer_produced_rate ?? .02));
    setDelta(inputs.delta == null && inputs.min_meaningful_improvement == null ? '' : String(inputs.delta ?? inputs.min_meaningful_improvement));
    setSpend(inputs.spend_budget == null ? '' : String(inputs.spend_budget)); setPrice(inputs.price_per_1k_tokens == null ? '' : String(inputs.price_per_1k_tokens));
  }, [calibration.data]);
  useEffect(() => {
    setSimulation(null); setSimError('');
    if (!hasDraft || !ready) return;
    const params: Record<string, unknown> = {};
    try { for (const [name, text] of Object.entries(drafts)) { const parsed = JSON.parse(text); const key = simulationKey(name); if (key) params[key] = parsed; } }
    catch { setSimError('修改值不是有效 JSON，无法预览'); return; }
    const controller = new AbortController();
    const timer = setTimeout(() => {
      api<Forecast>(base + '/simulate', { params }, controller.signal).then(value => {
        if (!controller.signal.aborted) setSimulation(value);
      }).catch(e => { if (!controller.signal.aborted) setSimError(e.message); });
    }, 450);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [base, draftKey, ready, proposal.data]);
  const act = async (action: 'calibrate' | 'run' | 'resume') => {
    setError(''); setBusy(true);
    try {
      const positive = (text: string, label: string, zero = false) => {
        const value = Number(text);
        if (!text.trim() || !Number.isFinite(value) || (zero ? value < 0 : value <= 0)) throw new Error(`${label}需要${zero ? '非负' : '正'}数`);
        return value;
      };
      const concurrency = positive(calibrationConcurrency, '校准执行并发');
      if (action === 'calibrate' && (!Number.isInteger(concurrency) || concurrency > 16)) throw new Error('校准执行并发需要 1–16 的整数');
      const body = action === 'calibrate' ? { inputs: calibration.data?.launch_inputs ?? {
        time_budget_hours: positive(hours, '时间预算'), calibration_k: k,
        calibration_concurrency: concurrency,
        safeguards: { tokens_per_task_ratio: positive(tokens, 'token 容忍量', true), answer_produced_rate: positive(answers, '回答率容忍量', true) },
        ...(delta ? { min_meaningful_improvement: positive(delta, '最小有意义提升') } : {}),
        ...(spend ? { spend_budget: positive(spend, '测量费用预算', true) } : {}),
        ...(price ? { price_per_1k_tokens: positive(price, 'token 单价', true) } : {}),
        adapter: selectedProfiles, test_set_authorized: authorized,
      } } : {};
      if (action === 'calibrate' && spend && !price) throw new Error('填写测量费用预算时必须提供同一货币单位的 token 单价');
      const value = await api<Confirmation>(base + (action === 'run' ? '/run/preview' : action === 'resume' ? '/run/resume' : '/calibrate'), body);
      setPreview({ value, body });
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  const confirm = async () => {
    if (!preview) return;
    setBusy(true); setError('');
    try {
      await api(base + (preview.value.action === 'run' ? '/run' : preview.value.action === 'resume' ? '/run/resume' : '/calibrate'), { ...preview.body, confirmation_token: preview.value.confirmation_token });
      setPreview(null); setTick(t => t + 1);
      if (preview.value.action !== 'calibrate') onStarted();
    } catch (e) { setPreview(null); setError((e as Error).message); } finally { setBusy(false); }
  };
  const locked = busy || !!preview || !!calibration.data?.alive;
  const inputLocked = locked || !!calibration.data?.launch_inputs;
  return <View testID="v41-wizard" style={{ gap: 14 }}>
    <Copy>自动优化 · 目标与预算 → 校准 → 建议 → 确认启动</Copy>
    {preview ? <V4Confirmation preview={preview.value} busy={busy} onConfirm={confirm} onCancel={() => setPreview(null)} /> : !calibration.data ? <Copy small muted>{calibration.error ?? '读取校准状态…'}</Copy> : !ready ? <>
      <Copy small muted>题池与起点已登记，主指标沿用输入的官方评分协议。先测种子的噪声和耗时，再决定搜索／验证划分。</Copy>
      <Field label="时间预算（小时）" value={hours} onChange={setHours} editable={!inputLocked} testID="v41-hours" />
      <Copy small muted>由你决定愿意投入的运行时间，模拟器据此估计候选数。</Copy>
      <Copy small>校准遍数：由你权衡测量成本与噪声估计稳定性。</Copy>
      <View style={{ flexDirection: 'row', gap: 8 }}>{[2, 3, 4, 5].map(n => <Button key={n} label={String(n)} selected={k === n} disabled={inputLocked} onPress={() => setK(n)} testID={`v41-k-${n}`} />)}</View>
      <Field label="校准执行并发（1–16）" value={calibrationConcurrency} onChange={setCalibrationConcurrency} editable={!inputLocked} testID="v41-calibration-concurrency" />
      <Copy small muted>默认 2，让 P 与 D 同时测量；1 为串行。P 首遍后其余遍可并行，受此上限约束。并发是评测执行数，每次执行内部仍可能并行调用模型。启动后按原值恢复。</Copy>
      <Field label="token 用量允许增加比例（0.5 = 50%）" value={tokens} onChange={setTokens} editable={!inputLocked} />
      <Field label="回答率允许下降（0.02 = 2 个百分点）" value={answers} onChange={setAnswers} editable={!inputLocked} />
      <Copy small muted>这是产品能接受的退化，由你定义；判定时还会计入实测噪声。</Copy>
      <Field label="最小有意义提升 δ（选填）" value={delta} onChange={setDelta} editable={!inputLocked} />
      <Copy small muted>填写你认为值得投入的分数增量，用来检查预计分辨率是否足够。</Copy>
      <Section title="费用与 Agent 设置" defaultOpen={false}>
        <Field label="测量费用预算（选填）" value={spend} onChange={setSpend} editable={!inputLocked} />
        <Field label="每千 token 单价（同一货币单位）" value={price} onChange={setPrice} editable={!inputLocked} />
        <Copy small muted>未知价格不能按零费用计算。预算用于测量预测，Agent 和既有校准费用另计。</Copy>
        {['analyst', 'implementer', 'reviewer', 'calibrator'].map(role => <View key={role} style={{ gap: 4 }}>
          <Copy small>{({ analyst: '分析', implementer: '实现', reviewer: '审查', calibrator: '校准顾问' } as Record<string, string>)[role]} Agent</Copy>
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 4 }}>
            <Button label="服务器默认" selected={!selectedProfiles[role + '_profile']} disabled={inputLocked} onPress={() => setProfiles(p => { const next = { ...p }; delete next[role + '_profile']; return next; })} />
            {profiles.map(p => <Button key={p.id} label={`${p.provider} · ${p.model}`} selected={selectedProfiles[role + '_profile'] === p.id} disabled={inputLocked} onPress={() => setProfiles(v => ({ ...v, [role + '_profile']: p.id }))} />)}
          </View>
        </View>)}
        <Copy small muted>选择已登记的模型配置，权衡 Agent 成本与能力。</Copy>
      </Section>
      <Button label={authorized ? '终评授权：已登记' : '终评授权：未授权'} selected={authorized} disabled={inputLocked} onPress={() => setAuthorized(a => !a)} />
      <Copy small muted>终评使用封存题，需独立确认；此向导不会执行终评。</Copy>
      {calibration.data.alive ? <Copy testID="v41-calibrating">校准／顾问运行中 · 已用 {duration(calibration.data.elapsed_s)}：P {calibration.data.probe_size ?? calibration.data.summary?.n_probe ?? '待登记'} · D {calibration.data.diagnosis_size ?? calibration.data.summary?.n_diagnosis ?? '待登记'} · 执行并发上限 {calibration.data.calibration_concurrency ?? calibrationConcurrency} · P 已完成 {calibration.data.probe_completed_k ?? 0} 遍 · D 已完成 {calibration.data.diagnosis_completed_cases ?? 0} 题 · 已完成 {(calibration.data.completed_stages ?? []).join('、') || (calibration.data.complete ? '测量，等待顾问' : '等待测量回执')}</Copy>
        : <Button label={calibration.data.phase === 'calibration' ? '预览恢复校准' : '预览校准'} primary disabled={locked} onPress={() => act('calibrate')} testID="v41-calibrate" />}
    </> : proposal.data ? <>
      <Copy>{proposal.data.proposal.notes.advisor?.message ?? '使用规则建议。校准顾问未提供说明。'}</Copy>
      <ForecastSummary forecast={simulation ?? proposal.data.forecast} />
      {hasDraft && <Copy small muted>{simulation ? '正在预览尚未保存的修改；保存后才会用于运行。' : '修改尚未保存，正在计算新预测…'}</Copy>}
      {!!simError && <Copy small style={{ color: theme.error }}>{simError}</Copy>}
      <Tradeoff iterationId={iterationId} calibration={calibration.data} forecast={simulation ?? proposal.data.forecast} />
      <Section title="参数建议" meta={proposal.data.frozen ? '已冻结' : '保存后来源记为用户'}>
        {Object.entries(proposal.data.proposal.items).map(([name, item]) => <ParameterEditor key={name} name={name} item={item} iterationId={iterationId}
          locked={locked || proposal.data!.frozen} onSaved={() => setTick(t => t + 1)} onDraft={text => setDrafts(old => { const next = { ...old }; if (text === null) delete next[name]; else next[name] = text; return next; })} />)}
      </Section>
      <Section title="查看校准证据" defaultOpen={false}><V4Calibration detail={calibration.data} forecast={proposal.data.forecast} /></Section>
      {!proposal.data.frozen && <Button label="预览冻结配置与启动" primary onPress={() => act('run')} disabled={locked || hasDraft} testID="v41-run-preview" />}
      {proposal.data.frozen && <><Copy small muted>配置已冻结。启动中断时，可按原配置恢复。</Copy><Button label="预览恢复已冻结运行" onPress={() => act('resume')} disabled={locked} testID="v41-frozen-resume" /></>}
    </> : <><Copy small muted>{proposal.error ?? '读取建议书…'}</Copy>{proposal.error && <Button label="预览恢复校准与建议书" disabled={locked} onPress={() => act('calibrate')} testID="v41-recover-proposal" />}</>}
    {!!error && <Copy small style={{ color: theme.error }}>{error}</Copy>}
  </View>;
}
