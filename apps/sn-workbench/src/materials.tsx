import React, { useEffect, useRef, useState } from 'react';
import { Linking, Pressable, ScrollView, Text, View } from 'react-native';
import { BookOpen, Check, ChevronLeft, ChevronRight, FileText, X } from 'lucide-react-native';
import { api, CaseRow, date, History, judgmentLabels, Packet, Result, Review, Source, statusLabels, Task, literal, requestKey } from './contracts';
import { Block, Button, Copy, Fold, Input, s, useTheme } from './ui';
import { CHROME_HEIGHT, IconButton } from './shell';

export function useResource<T>(path: string | null, refresh = 0) {
  const [state, setState] = useState<{ path: string | null; data?: T; error?: string }>({ path: null });
  useEffect(() => {
    if (!path) return;
    const controller = new AbortController();
    api<T>(path, undefined, controller.signal).then(data => {
      if (!controller.signal.aborted) setState({ path, data });
    }).catch(error => {
      if (!controller.signal.aborted) setState({ path, error: String(error.message) });
    });
    return () => controller.abort();
  }, [path, refresh]);
  return state.path === path ? state : { path };
}

export function Scores({ result }: { result: Result }) {
  const theme = useTheme();
  return <>
    <Block title="完整声明范围的成绩">
      <Copy small muted>{result.suite.toUpperCase()} · {result.case_count} / {result.bundle_case_count} 题 · {result.scope === 'full' ? '冻结 bundle 全范围' : '显式子集'}</Copy>
      <View style={[s.row, { paddingBottom: 8, borderBottomWidth: 1, borderColor: theme.line }]}>
        <Copy small muted style={{ flex: 1 }}>指标</Copy><Copy small muted>原汇总 · 分母</Copy>
      </View>
      {Object.entries(result.metrics).map(([name, metric]) => <View key={name} style={[s.row, { paddingVertical: 8, borderBottomWidth: 1, borderColor: theme.line }]}>
        <View style={{ flex: 1 }}><Copy small>{name}</Copy><Copy small muted>{metric.kind === 'batch-only' ? '仅批量值' : '有逐题值'}</Copy></View>
        <Copy style={{ fontVariant: ['tabular-nums'] }}>{metric.value.toFixed(6)}</Copy><Copy small muted> / {metric.denominator}</Copy>
      </View>)}
    </Block>
    <Block title="生成覆盖">
      <View style={s.row}>{['planned', 'success', 'no_answer', 'clarification', 'error', 'missing'].map(name => <Copy small key={name}>{({ planned: '计划', success: '回答', no_answer: '空回答', clarification: '澄清', error: '错误', missing: '缺失' } as Record<string, string>)[name]} {String(result.coverage[name] ?? '未知')}</Copy>)}</View>
    </Block>
    <Block title="评分缺口">
      {result.pending_metrics.length ? result.pending_metrics.map(item => <Copy small key={item}>{item}</Copy>) : <Copy small muted>该批次未声明 pending 指标</Copy>}
    </Block>
  </>;
}

function CaseList({ result, onSelect }: { result: Result; onSelect: (id: string) => void }) {
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState('all');
  const [page, setPage] = useState(0);
  const rows = (result.cases ?? []).filter(row =>
    (row.question + row.case_id).toLowerCase().includes(query.toLowerCase()) &&
    (filter === 'all' || filter === 'zero' && Object.values(row.metrics).some(value => value === 0) || filter === 'unanswered' && row.status !== 'success'));
  useEffect(() => setPage(0), [query, filter]);
  return <Block title="案例与调查入口">
    <Input label="搜索案例" value={query} onChange={setQuery} placeholder="搜索问题或案例 ID" />
    <View style={s.row}>{[['all', '全部'], ['zero', '含有效零分'], ['unanswered', '未成功回答']].map(([id, title]) => <Button key={id} label={title} selected={filter === id} onPress={() => setFilter(id)} />)}</View>
    <Copy small muted>{rows.length} / {result.case_count} 题 · 按原题单顺序</Copy>
    {rows.slice(page * 40, (page + 1) * 40).map(row => <CaseItem key={row.case_id} row={row} onPress={() => onSelect(row.case_id)} />)}
    {!rows.length && <Copy muted>没有符合当前筛选的案例</Copy>}
    <View style={s.row}><Button label="上一页" icon={ChevronLeft} disabled={page === 0} onPress={() => setPage(page - 1)} /><Copy small>{page + 1} / {Math.max(1, Math.ceil(rows.length / 40))}</Copy><Button label="下一页" icon={ChevronRight} disabled={(page + 1) * 40 >= rows.length} onPress={() => setPage(page + 1)} /></View>
  </Block>;
}

function CaseItem({ row, onPress }: { row: CaseRow; onPress: () => void }) {
  const theme = useTheme();
  return <View style={{ borderBottomWidth: 1, borderColor: theme.line, paddingVertical: 10, gap: 4 }}>
    <Button label={row.question} onPress={onPress} testID="case-row" />
    <Copy small muted>{row.case_id} · {statusLabels[row.status] ?? row.status}</Copy>
    <Copy small muted>{Object.entries(row.metrics).map(([key, value]) => `${key} ${value.toFixed(3)}`).join(' · ') || '逐题指标未提供'}</Copy>
  </View>;
}

function DocumentBody({ resultId, id }: { resultId: string; id: string }) {
  const document = useResource<{ title: string; text: string; text_sha256: string }>(`/results/${resultId}/document?document_id=${encodeURIComponent(id)}`);
  if (document.error) return <Copy>{document.error}</Copy>;
  if (!document.data) return <Copy muted>正在读取完整源资料…</Copy>;
  return <><Copy small muted>完整保存文本 · {document.data.text_sha256}</Copy><Copy>{document.data.text}</Copy></>;
}

type Draft = { reason: string; judgment: string; evidence: string[]; base: string | null; receipt: string | null; requestKey: string; dirty: boolean };
function Evidence({ task, result, caseId, actor, onSaved, onAttach, drafts, setDrafts }: {
  task: Task; result: Result; caseId: string; actor: string; onSaved: () => void; onAttach: () => void;
  drafts: Record<string, Draft>; setDrafts: React.Dispatch<React.SetStateAction<Record<string, Draft>>>;
}) {
  const key = `${task.id}:${result.id}:${caseId}`;
  const packet = useResource<Packet>(`/results/${result.id}/case?case_id=${encodeURIComponent(caseId)}`);
  const [refresh, setRefresh] = useState(0);
  const reviews = useResource<Review[]>(`/tasks/${task.id}/reviews?result_id=${result.id}&case_id=${encodeURIComponent(caseId)}`, refresh);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [openDocuments, setOpenDocuments] = useState<Record<string, boolean>>({});
  const [documentLimit, setDocumentLimit] = useState(8);
  useEffect(() => { setError(null); setDocumentLimit(8); }, [key]);
  const latest = reviews.data?.filter(r => r.reviewer === actor).at(-1);
  useEffect(() => {
    if (reviews.data) setDrafts(prev => prev[key] ? prev : { ...prev, [key]: {
      reason: latest?.reason ?? '', judgment: latest?.judgment ?? 'undecided', evidence: latest?.evidence ?? [],
      base: latest?.id ?? null, receipt: latest?.id ?? null, requestKey: requestKey(), dirty: false,
    } });
  }, [key, reviews.data, actor]);
  const draft = drafts[key];
  const modify = (changes: Partial<Draft>) => setDrafts(prev => ({ ...prev, [key]: { ...prev[key], ...changes, receipt: null, requestKey: requestKey(), dirty: true } }));
  const save = async () => {
    if (!draft) return;
    setBusyKey(key); setError(null);
    try {
      const receipt = await api<Review>(`/tasks/${task.id}/reviews`, { result_id: result.id, case_id: caseId,
        judgment: draft.judgment, reason: draft.reason, evidence: draft.evidence, base_revision: draft.base, request_key: draft.requestKey });
      setDrafts(prev => ({ ...prev, [key]: { ...prev[key], base: receipt.id, receipt: receipt.id, dirty: false, requestKey: requestKey() } }));
      setRefresh(n => n + 1); onSaved();
    } catch (e) { setError((e as Error).message); } finally { setBusyKey(null); }
  };
  const theme = useTheme();
  if (packet.error || reviews.error) return <Copy>{packet.error ?? reviews.error}</Copy>;
  if (!packet.data) return <Copy muted>正在读取固定案例…</Copy>;
  const p = packet.data;
  return <>
    <Block title={p.question}><Copy small muted>{p.case_id} · {statusLabels[p.status] ?? p.status}</Copy>
      <Copy small muted>snapshot {result.id.slice(0, 12)} · scores {result.scores_id.slice(0, 12)}</Copy>
      <Button label="附加到 Agent 消息" icon={BookOpen} onPress={onAttach} />
    </Block>
    <Block title="已保存答案"><Copy small muted>字段来源：{p.answer_source}</Copy><Copy testID="case-answer">{p.answer || `（${statusLabels[p.status] ?? p.status} · 无答案正文）`}</Copy></Block>
    {p.prediction !== p.answer && <Block title="提交给评分桥的答案"><Copy>{p.prediction || '空字符串'}</Copy></Block>}
    <Block title="逐题指标"><Copy>{Object.entries(p.metric_states).map(([name, state]) => `${name}  ${state === 'not-applicable' ? '不适用（不在指标 eligible 范围）' : state === 'batch-only' ? '仅批量值，见完整成绩表' : p.metrics[name].toFixed(6)}`).join('\n') || '该案例无逐题数值；批量成绩见完整成绩表'}</Copy></Block>
    <Block title="参考与标注"><Fold title="打开参考答案" value={p.references} /><Fold title="打开原始标注" value={p.gold} /></Block>
    <Block title="源资料">
      <Copy small muted>共 {p.documents.length} 份关联资料，当前展开前 {Math.min(documentLimit, p.documents.length)} 份</Copy>
      {p.documents.slice(0, documentLimit).map(doc => <View key={doc.id} style={{ gap: 8, paddingBottom: 16 }}>
        <Copy style={{ fontWeight: '500' }}>{doc.title}</Copy><Copy>{doc.excerpt}</Copy>
        <Copy small muted>节选 0–{doc.excerpt_end} / {doc.characters} 字符 · {doc.text_sha256.slice(0, 12)}</Copy>
        <Button label={openDocuments[key + doc.id] ? '收起完整资料' : '读取完整资料'} icon={FileText} onPress={() => setOpenDocuments(prev => ({ ...prev, [key + doc.id]: !prev[key + doc.id] }))} />
        {openDocuments[key + doc.id] && <DocumentBody resultId={result.id} id={doc.id} />}
      </View>)}
      {documentLimit < p.documents.length && <Button label="查看更多关联资料" onPress={() => setDocumentLimit(n => n + 8)} />}
      {!p.documents.length && <Copy small muted>当前结果快照没有该案例的材料文本</Copy>}
    </Block>
    <Block title="保存的观测">{p.evidence.map(e => <Fold key={e.id} title={e.label} value={e.value} />)}<Copy small muted>未提供：{p.unavailable.join('、')}</Copy></Block>
    <Block title="你的判断">
      <Copy small muted>评审者 {actor} · 标准 case-observation-v1</Copy>
      {draft ? <>
        <View style={s.row}>{Object.entries(judgmentLabels).map(([id, label]) => <Button key={id} label={label} selected={draft.judgment === id} onPress={() => modify({ judgment: id })} disabled={busyKey === key} />)}</View>
        <Input label="判断理由" value={draft.reason} onChange={reason => modify({ reason })} multiline placeholder="问题是否成立？记录支持、反证和未知项" testID="review-reason" editable={busyKey !== key} />
        <Copy small muted>选择支持这份判断的材料</Copy>
        <View style={s.row}>{p.evidence_ids.map(id => <Button key={id} label={id} icon={draft.evidence.includes(id) ? Check : undefined} selected={draft.evidence.includes(id)} disabled={busyKey === key} onPress={() => modify({ evidence: draft.evidence.includes(id) ? draft.evidence.filter(e => e !== id) : [...draft.evidence, id] })} />)}</View>
        <Button label={busyKey === key ? '保存中…' : '保存判断'} primary onPress={save} disabled={busyKey === key || !draft.reason.trim()} testID="save-review" />
        {draft.receipt && <Copy small muted testID="review-receipt">已保存修订 {draft.receipt.slice(0, 12)}</Copy>}
        {draft.dirty && <Copy small muted>有尚未保存的判断；切换案例后可回来继续编辑</Copy>}
        {error && <><Copy small style={{ color: theme.error }}>{error}</Copy><Button label="读取当前修订，保留本地理由" onPress={async () => {
          try {
            const rows = await api<Review[]>(`/tasks/${task.id}/reviews?result_id=${result.id}&case_id=${encodeURIComponent(caseId)}`);
            modify({ base: rows.filter(r => r.reviewer === actor).at(-1)?.id ?? null }); setRefresh(n => n + 1); setError(null);
          } catch (e) { setError((e as Error).message); }
        }} /></>}
      </> : <Copy muted>正在读取已有修订…</Copy>}
      <Fold title={`已保存修订 (${reviews.data?.length ?? 0})`} value={reviews.data} />
    </Block>
  </>;
}

export function Materials({ panel, task, result, caseId, actor, selectedHistory, compact, onClose, onPanel, onSelect, onSaved, onAttach }: {
  panel: string; task: Task | undefined; result: Result | undefined; caseId: string | null; actor: string; selectedHistory: History | null;
  compact: boolean; onClose: () => void; onPanel: (id: string) => void; onSelect: (id: string) => void; onSaved: () => void; onAttach: () => void;
}) {
  const theme = useTheme(); const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const title = panel === 'history' ? selectedHistory ? `记录 #${selectedHistory.seq} · ${selectedHistory.kind}` : '固定记录' : result ? `${result.suite.toUpperCase()} · ${result.method.name}` : '结果材料';
  return <View style={{ flex: 1, backgroundColor: theme.sidebar }}>
    <View style={{ minHeight: compact ? 48 : CHROME_HEIGHT, flexDirection: 'row', alignItems: 'center', paddingHorizontal: 12, borderBottomWidth: 1, borderColor: theme.line }}><Text numberOfLines={2} style={{ flex: 1, minWidth: 0, color: theme.text }}>{title}</Text><IconButton label="关闭材料" icon={X} onPress={onClose} testID="close-material" /></View>
    {panel !== 'history' && <View style={{ flexDirection: 'row', borderBottomWidth: 1, borderColor: theme.line, paddingHorizontal: 8 }}>{[['scores','成绩'],['cases','案例'],['source','来源']].map(([id,label]) => <Pressable key={id} accessibilityRole="tab" accessibilityLabel={label} accessibilityState={{ selected: panel === id }} onPress={() => onPanel(id)} testID={'panel-' + id} style={{ paddingHorizontal: 12, height: 36, justifyContent: 'center' }}><Text style={{ color: panel === id ? theme.text : theme.muted }}>{label}</Text></Pressable>)}</View>}
    <ScrollView style={{ flex: 1, minHeight: 0 }} contentContainerStyle={{ padding: compact ? 18 : 16 }}>
      {panel === 'history' ? selectedHistory ? <Block title="固定保存记录"><Copy small muted>{date(selectedHistory.registered_at)} · 登记时间</Copy><Copy small muted>{selectedHistory.object_id}</Copy><Fold title="当时保存的完整内容" value={selectedHistory.content} /></Block> : <Copy muted>在当前任务记录中选择一条保存记录</Copy>
      : !result ? <Copy muted>在结果工作面选择一份结果，再打开成绩与证据</Copy>
      : panel === 'scores' ? <Scores result={result} />
      : panel === 'cases' ? caseId && task ? <><Button label="返回案例列表" icon={ChevronLeft} onPress={() => onSelect('')} /><Evidence task={task} result={result} caseId={caseId} actor={actor} onSaved={onSaved} onAttach={onAttach} drafts={drafts} setDrafts={setDrafts} /></> : <CaseList result={result} onSelect={onSelect} />
      : <Block title="来源与被测身份"><Copy>{result.method.name} · {result.method.kind === 'sn' ? 'SN 答卷' : '外部方法答卷'}</Copy><Copy small muted>实验时间：未知</Copy><Copy small style={s.mono}>scores {result.scores_id}{'\n'}submission {result.submission_id}{'\n'}snapshot {result.id}{'\n'}scorer {result.scorer_id}</Copy><Fold title="原方法与源码身份" value={result.method} /><Fold title="数据来源" value={result.source} /><Fold title="导入原文件字节 hash" value={result.source_hashes} /></Block>}
    </ScrollView>
  </View>;
}
