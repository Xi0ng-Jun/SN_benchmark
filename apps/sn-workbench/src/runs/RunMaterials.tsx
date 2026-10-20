import React, { useEffect, useMemo, useState } from 'react';
import { Platform, Pressable, ScrollView, Text, View } from 'react-native';
import { ChevronLeft, IdCard, X } from 'lucide-react-native';
import { useResource } from '../contracts';
import { Button, Copy, fmt, Input, Pill, s, StatusDot, useTheme, hover } from '../ui';
import { CHROME_HEIGHT, IconButton } from '../shell';
import { CaseDetail as CaseDetailData, CaseList as CaseListData, caseLabel, caseStatusLabels, Flip, FlipTable as FlipData, Paper,
  RunCase, RunView, short, stageLabels } from './contracts';

export type RunPanel = 'run-cases' | 'run-flips';
export type RunSelection = { runId: string; caseId: string | null; panel: RunPanel; needsRead: boolean; against: string | null; provenance: boolean };

const scrollToId = (id: string) => {
  if (Platform.OS !== 'web') return;
  // react-native-web maps nativeID to the DOM id; scroll the nearest scroller only.
  requestAnimationFrame(() => document.getElementById(id)?.scrollIntoView({ block: 'center' }));
};

function Section({ title, right, children, testID }: { title: string; right?: React.ReactNode; children: React.ReactNode; testID?: string }) {
  const theme = useTheme();
  return <View testID={testID} style={{ gap: 8, paddingVertical: 12, borderBottomWidth: 1, borderColor: theme.line }}>
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
      <Text style={{ flex: 1, fontSize: 12, fontWeight: '600', color: theme.muted }}>{title}</Text>{right}
    </View>{children}
  </View>;
}

function Chip({ label, selected, onPress, disabled, testID }: { label: string; selected: boolean; onPress: () => void; disabled?: boolean; testID?: string }) {
  const theme = useTheme();
  return <Pressable accessibilityRole="button" accessibilityState={{ selected, disabled }} onPress={onPress} disabled={disabled} testID={testID}
    style={{ height: 24, paddingHorizontal: 9, borderRadius: 999, justifyContent: 'center', borderWidth: 1,
      borderColor: selected ? theme.text : theme.line, backgroundColor: selected ? theme.selected : 'transparent', opacity: disabled ? .45 : 1 }}>
    <Text style={{ fontSize: 12, color: selected ? theme.text : theme.muted }}>{label}</Text>
  </Pressable>;
}

const caseTone = (row: RunCase) => row.status === 'error' || row.status === 'missing' ? 'danger' : row.needs_read ? 'warning' : 'idle';

function CaseRows({ run, against, needsRead, onNeedsRead, onSelect }: { run: RunView; against: string | null; needsRead: boolean;
  onNeedsRead: (v: boolean) => void; onSelect: (id: string) => void }) {
  const theme = useTheme();
  const list = useResource<CaseListData>(`/run-views/runs/${encodeURIComponent(run.id)}/cases${against ? '?against=' + encodeURIComponent(against) : ''}`);
  const [status, setStatus] = useState('all');
  const [delta, setDelta] = useState<'all' | 'worse' | 'better' | 'same'>('all');
  const [query, setQuery] = useState('');
  const rows = (list.data?.cases ?? []).filter(row => (!needsRead || row.needs_read) && (status === 'all' || row.status === status)
    && (delta === 'all' || row.delta != null && (delta === 'worse' ? row.delta < 0 : delta === 'better' ? row.delta > 0 : row.delta === 0))
    && (row.case_id + ' ' + (row.question ?? '')).toLowerCase().includes(query.toLowerCase()));
  const statuses = Object.keys(run.progress.status_counts);
  const metric = list.data?.metric;
  if (list.error) return <Copy small style={{ color: theme.error }}>{list.error}</Copy>;
  return <View testID="run-case-list" style={{ gap: 10 }}>
    <Input label="搜索案例" value={query} onChange={setQuery} placeholder="搜索问题或案例 ID" />
    <View style={s.row}>
      <Chip label={`待读 ${run.attention.count}`} selected={needsRead} disabled={!run.attention.count} onPress={() => onNeedsRead(true)} testID="filter-needs-read" />
      <Chip label={`全部 ${run.progress.planned}`} selected={!needsRead} onPress={() => onNeedsRead(false)} testID="filter-all" />
      <View style={{ width: 1, height: 16, backgroundColor: theme.line }} />
      <Chip label="任意状态" selected={status === 'all'} onPress={() => setStatus('all')} />
      {statuses.map(name => <Chip key={name} label={`${caseStatusLabels[name] ?? name} ${run.progress.status_counts[name]}`} selected={status === name} onPress={() => setStatus(name)} />)}
    </View>
    <View style={s.row}>
      <Text style={{ fontSize: 12, color: theme.muted }}>分差{against ? ` · 相对基线 ${against}` : ''}</Text>
      {(['all', 'worse', 'better', 'same'] as const).map(id => <Chip key={id} label={{ all: '不限', worse: '变差', better: '变好', same: '不变' }[id]}
        selected={delta === id} disabled={!against && id !== 'all'} onPress={() => setDelta(id)} testID={'filter-delta-' + id} />)}
      {!against && <Text style={{ fontSize: 11, color: theme.muted }}>在翻转页选择另一个基线 run 后可用</Text>}
    </View>
    <Text style={{ fontSize: 11, color: theme.muted }}>{list.data ? `${rows.length} / ${list.data.total} 题 · 原题单顺序 · 待读规则：${run.attention.rule}` : '正在读取案例…'}</Text>
    <View>{rows.map(row => <Pressable key={row.case_id} testID="run-case-row" accessibilityRole="button" accessibilityLabel={row.question ?? row.case_id}
      onPress={() => onSelect(row.case_id)}
      style={hover(hovered => ({ paddingVertical: 9, paddingHorizontal: 8, marginHorizontal: -8, borderRadius: 8, gap: 4, flexDirection: 'row',
        backgroundColor: hovered ? theme.selected : 'transparent' }))}>
      <View style={{ paddingTop: 7 }}><StatusDot tone={caseTone(row)} /></View>
      <View style={{ flex: 1, gap: 2 }}>
        <Text numberOfLines={2} style={{ fontSize: 13, lineHeight: 19, color: theme.text }}>{row.question ?? '（问题文本不可用）'}</Text>
        <Text style={{ fontSize: 11, color: theme.muted, fontVariant: ['tabular-nums'] }}>
          {caseLabel(row.case_id)} · {caseStatusLabels[row.status] ?? row.status}
          {metric ? ` · ${metric} ${fmt(row.official.metrics[metric])}` : ''}
          {row.delta != null ? ` · Δ ${row.delta > 0 ? '+' : ''}${row.delta.toFixed(3)}` : ''}</Text>
      </View>
    </Pressable>)}</View>
    {list.data && !rows.length && <Copy small muted>没有符合当前筛选的案例</Copy>}
  </View>;
}

const markStyle = (marks: string[], theme: ReturnType<typeof useTheme>, showRetrieved: boolean) => {
  if (marks.includes('gold')) return { backgroundColor: theme.gold };
  if (marks.includes('cited')) return { backgroundColor: theme.cited };
  if (marks.includes('retrieved') && showRetrieved) return { backgroundColor: theme.retrieved };
  return null;
};

function PaperText({ paper, showRetrieved, onlyMarked }: { paper: Paper; showRetrieved: boolean; onlyMarked: boolean }) {
  const theme = useTheme();
  return <View testID="paper-text" style={{ gap: 10 }}>
    {paper.paragraphs.map((para, index) => {
      const kinds = new Set(para.segments.flatMap(seg => seg.marks));
      const relevant = kinds.has('gold') || kinds.has('cited') || (showRetrieved && kinds.has('retrieved'));
      if (onlyMarked && !relevant) return null;
      return <View key={para.start} nativeID={`${paper.document_id}-p${index}`}
        style={{ paddingLeft: 10, borderLeftWidth: 2, borderColor: kinds.has('gold') ? theme.warning : kinds.has('cited') ? theme.running : 'transparent' }}>
        <Text selectable style={{ fontSize: 13, lineHeight: 21, color: relevant ? theme.text : theme.muted }}>
          {para.segments.map((seg, i) => <Text key={i} testID={seg.marks.includes('gold') ? 'highlight-gold' : seg.marks.includes('cited') ? 'highlight-cited' : undefined}
            style={markStyle(seg.marks, theme, showRetrieved)}>{seg.text}</Text>)}
        </Text>
      </View>;
    })}
  </View>;
}

function Answer({ text, onCite }: { text: string; onCite: (key: string) => void }) {
  const theme = useTheme();
  const parts = text.split(/(\[k\d+\])/);
  return <Text selectable testID="run-case-answer" style={{ fontSize: 14, lineHeight: 22, color: theme.text }}>
    {parts.map((part, i) => {
      const key = /^\[(k\d+)\]$/.exec(part)?.[1];
      return key ? <Text key={i} onPress={() => onCite(key)} accessibilityRole="link"
        style={{ color: theme.running, backgroundColor: theme.cited, borderRadius: 4, paddingHorizontal: 3, fontSize: 12 }}>{key}</Text> : <Text key={i}>{part}</Text>;
    })}
  </Text>;
}

function CaseDetail({ runId, caseId, onAttach }: { runId: string; caseId: string; onAttach?: () => void }) {
  const theme = useTheme();
  const detail = useResource<CaseDetailData>(`/run-views/runs/${encodeURIComponent(runId)}/case?case_id=${encodeURIComponent(caseId)}`);
  const [showRetrieved, setShowRetrieved] = useState(false);
  const [onlyMarked, setOnlyMarked] = useState(false);
  const [openRetrieved, setOpenRetrieved] = useState(false);
  const d = detail.data;
  const paragraphOf = (paper: Paper, predicate: (labels: string[], marks: string[]) => boolean) =>
    paper.paragraphs.findIndex(p => p.segments.some(seg => predicate(seg.labels, seg.marks)));
  const jump = (predicate: (labels: string[], marks: string[]) => boolean) => {
    for (const paper of d?.papers ?? []) {
      const index = paragraphOf(paper, predicate);
      if (index >= 0) { scrollToId(`${paper.document_id}-p${index}`); return; }
    }
  };
  // Land on the first gold highlight as soon as the case opens.
  useEffect(() => { if (d) setTimeout(() => jump((_, marks) => marks.includes('gold')), 60); }, [d?.case_id]);
  if (detail.error) return <Copy small style={{ color: theme.error }}>{detail.error}</Copy>;
  if (!d) return <Copy small muted>正在读取案例证据…</Copy>;
  const metrics = Object.entries(d.official.metrics);
  return <View testID="run-case-detail">
    <Section title="问题">
      <Text selectable style={{ fontSize: 15, lineHeight: 23, color: theme.text, fontWeight: '500' }}>{d.question}</Text>
      <View style={s.row}>
        <Pill label={caseStatusLabels[d.status] ?? d.status} tone={d.status === 'success' ? undefined : 'danger'} />
        {metrics.map(([name, value]) => <Pill key={name} label={`${name} ${fmt(value)}`} tone={name === d.official.primary_metric && value < 1 ? 'warning' : undefined} />)}
        {(['gold_evidence_recall', 'context_precision', 'citation_precision'] as const).map(key =>
          <Pill key={key} label={`${stageLabels[key]} ${fmt(d.stage[key], 2)}`} />)}
      </View>
      <Text style={{ fontSize: 11, color: theme.muted }}>{d.case_id}</Text>
      {onAttach && <View style={s.row}><Button label="附加到 Agent 消息" onPress={onAttach} testID="run-case-attach" /></View>}
    </Section>
    <Section title="SN 答案">
      {d.answer ? <Answer text={d.answer} onCite={key => jump(labels => labels.includes(key))} />
        : <Copy small muted>（{caseStatusLabels[d.status] ?? d.status} · 无答案正文）</Copy>}
    </Section>
    <Section title={`gold 参考答案 · ${d.gold?.annotations.length ?? 0} 份标注`} testID="run-case-gold">
      {(d.references ?? []).map((ref, i) => <View key={i} style={{ flexDirection: 'row', gap: 8 }}>
        <Text style={{ fontSize: 12, color: theme.muted, width: 16, fontVariant: ['tabular-nums'] }}>{i + 1}</Text>
        <Text selectable style={{ flex: 1, fontSize: 13, lineHeight: 20, color: theme.text }}>{ref.trim() || '（空）'}</Text>
      </View>)}
      {!d.references?.length && <Copy small muted>该题没有参考答案</Copy>}
    </Section>
    <Section title={`引用对象 · ${d.citations.length}`} testID="run-case-citations">
      {d.citations.map(c => <Pressable key={c.key} onPress={() => jump(labels => labels.includes(c.key))} accessibilityRole="button"
        style={hover(hovered => ({ flexDirection: 'row', gap: 8, padding: 8, marginHorizontal: -8, borderRadius: 8, backgroundColor: hovered ? theme.selected : 'transparent' }))}>
        <Text style={{ fontSize: 12, color: theme.running, width: 26 }}>{c.key}</Text>
        <View style={{ flex: 1, gap: 2 }}>
          <Text style={{ fontSize: 12, color: theme.text }}>{c.object_type} · 检索第 {c.rank ?? '?'} 位 · {c.contains_gold ? '含 gold 证据' : c.contains_gold === false ? '不含 gold 证据' : '未知'}</Text>
          <Text numberOfLines={2} style={{ fontSize: 12, lineHeight: 18, color: theme.muted }}>{c.snippet}</Text>
          <Text style={{ fontSize: 11, color: theme.muted }}>{short(c.object_id, 14)}</Text>
        </View>
      </Pressable>)}
      {!d.citations.length && <Copy small muted>答案没有引用锚点</Copy>}
    </Section>
    {d.papers.map(paper => <Section key={paper.document_id} title={`论文原文 · ${paper.title ?? paper.document_id}`} testID="run-case-paper"
      right={<Text style={{ fontSize: 11, color: theme.muted }}>{paper.characters} 字符</Text>}>
      <View style={s.row}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 4 }}><View style={{ width: 10, height: 10, borderRadius: 2, backgroundColor: theme.gold }} /><Text style={{ fontSize: 11, color: theme.muted }}>gold 证据</Text></View>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 4 }}><View style={{ width: 10, height: 10, borderRadius: 2, backgroundColor: theme.cited }} /><Text style={{ fontSize: 11, color: theme.muted }}>答案引用</Text></View>
        <Chip label={`检索块 ${d.retrieved.length}`} selected={showRetrieved} onPress={() => setShowRetrieved(v => !v)} testID="toggle-retrieved" />
        <Chip label="只看高亮段" selected={onlyMarked} onPress={() => setOnlyMarked(v => !v)} />
        <Button label="跳到 gold" onPress={() => jump((_, marks) => marks.includes('gold'))} testID="jump-gold" />
      </View>
      {!!paper.unmatched.length && <Text style={{ fontSize: 11, color: theme.warning }}>
        {paper.unmatched.length} 段未在原文中精确定位：{paper.unmatched.map(u => u.label).join('、')}</Text>}
      <PaperText paper={paper} showRetrieved={showRetrieved} onlyMarked={onlyMarked} />
    </Section>)}
    {!d.papers.length && <Section title="论文原文"><Copy small muted>该 run 的冻结输入中没有本题资料</Copy></Section>}
    <Section title={`检索上下文 · ${d.retrieved.length} 块 · ${d.retrieved.filter(r => r.contains_gold).length} 块含 gold`}
      right={<Button label={openRetrieved ? '收起' : '展开'} onPress={() => setOpenRetrieved(v => !v)} />}>
      {openRetrieved && d.retrieved.map(block => <View key={block.rank} style={{ flexDirection: 'row', gap: 8 }}>
        <Text style={{ fontSize: 11, color: theme.muted, width: 22, fontVariant: ['tabular-nums'] }}>#{block.rank}</Text>
        <Text numberOfLines={3} style={{ flex: 1, fontSize: 12, lineHeight: 18, color: block.contains_gold ? theme.text : theme.muted }}>
          {block.key}{block.cited ? ' · 被引用' : ''}{block.contains_gold ? ' · 含 gold' : ''} — {block.excerpt}</Text>
      </View>)}
    </Section>
    <Judgment detail={d} />
    <Section title="成本观测">
      <Text style={{ fontSize: 12, color: theme.muted }}>provider 调用 {d.usage.call_count ?? '—'} · tokens {d.usage.total_tokens ?? '—'} · 问答 {d.usage.latency_seconds == null ? '—' : d.usage.latency_seconds.toFixed(2) + 's'} · 证据快照 {d.evidence_snapshot ?? '—'}</Text>
    </Section>
  </View>;
}

/** Judgments go to docs/failure-ledger.md (written by the coordinator); this page stores nothing. */
function Judgment({ detail: d }: { detail: CaseDetailData }) {
  const theme = useTheme();
  const [copied, setCopied] = useState(false);
  const metrics = Object.entries(d.official.metrics).map(([k, v]) => `${k} ${fmt(v)}`).join(' · ') || '无官方逐题值';
  const entry = [`- run: ${d.run_id} · case: ${d.case_id}`, `  状态: ${d.status} · ${metrics}`,
    `  引用: ${d.citations.map(c => `${c.key}(${c.object_type}, 检索第 ${c.rank ?? '?'} 位, ${c.contains_gold ? '含 gold' : '不含 gold'})`).join('; ') || '无'}`,
    '  失败类别: ', '  归因组件: ', '  证据路径: ', '  人工确认: undecided'].join('\n');
  const copy = async () => {
    if (Platform.OS === 'web') try { await navigator.clipboard.writeText(entry); setCopied(true); setTimeout(() => setCopied(false), 1600); } catch { setCopied(false); }
  };
  return <Section title="人工判读" testID="run-case-judgment" right={<Button label={copied ? '已复制' : '复制判读条目'} onPress={copy} testID="copy-judgment" />}>
    <Text style={{ fontSize: 12, lineHeight: 18, color: theme.muted }}>判断记入失败账本 docs/failure-ledger.md（由统筹写入）；本页不保存判断，也不改分数。</Text>
    <Text selectable style={{ fontFamily: 'monospace', fontSize: 11, lineHeight: 17, color: theme.text, padding: 8, borderRadius: 6, backgroundColor: theme.bg }}>{entry}</Text>
  </Section>;
}

const flipLabels: Record<Flip, string> = { better: '变好', worse: '变差', same: '不变', unpaired: '未配对' };

function FlipView({ runs, left, right, onPick, onCase }: { runs: RunView[]; left: string; right: string;
  onPick: (side: 'left' | 'right', id: string) => void; onCase: (id: string) => void }) {
  const theme = useTheme();
  const table = useResource<FlipData>(`/run-views/flips?left=${encodeURIComponent(left)}&right=${encodeURIComponent(right)}`);
  const t = table.data;
  return <View testID="flip-table" style={{ gap: 12 }}>
    {(['left', 'right'] as const).map(side => <View key={side} style={s.row}>
      <Text style={{ fontSize: 12, color: theme.muted, width: 36 }}>{side === 'left' ? '基线' : '对照'}</Text>
      {runs.map(run => <Chip key={run.id} label={run.title} selected={(side === 'left' ? left : right) === run.id} onPress={() => onPick(side, run.id)} />)}
    </View>)}
    {t?.admission === 'not_admitted' && <View testID="flip-not-admitted"><Copy small>两次运行尚不能配对；请分别阅读案例。</Copy>{(t.reasons ?? []).map((reason, i) => <Copy key={i} small muted>{reason}</Copy>)}</View>}
    {t?.same_run && <View testID="flip-self" style={{ padding: 10, borderRadius: 8, borderWidth: 1, borderStyle: 'dashed', borderColor: theme.warning }}>
      <Text style={{ fontSize: 12, lineHeight: 18, color: theme.text }}>两侧为同一次运行，已核对答卷与评分身份；此表用于回访，不代表两次实验的效果差异。</Text>
    </View>}
    {table.error && <Copy small style={{ color: theme.error }}>{table.error}</Copy>}
    {t && <>
      <View style={s.row}>{(Object.keys(flipLabels) as Flip[]).map(f => <Pill key={f} label={`${flipLabels[f]} ${t.counts[f]}`}
        tone={f === 'worse' && t.counts[f] ? 'danger' : f === 'better' && t.counts[f] ? 'success' : undefined} />)}
        <Text style={{ fontSize: 11, color: theme.muted }}>指标 {t.metric ?? '未知'} · 准入 {t.admission}</Text></View>
      <View style={{ borderWidth: 1, borderColor: theme.line, borderRadius: 8, overflow: 'hidden' }}>
        <View style={{ flexDirection: 'row', paddingHorizontal: 10, height: 30, alignItems: 'center', backgroundColor: theme.bg, borderBottomWidth: 1, borderColor: theme.line }}>
          {['案例', '基线', '对照', 'Δ', '翻转'].map((h, i) => <Text key={h} style={{ flex: i === 0 ? 2 : 1, fontSize: 11, color: theme.muted, textAlign: i ? 'right' : 'left' }}>{h}</Text>)}
        </View>
        {t.rows.map(row => <Pressable key={row.case_id} onPress={() => onCase(row.case_id)} testID="flip-row" accessibilityRole="button"
          style={hover(hovered => ({ flexDirection: 'row', paddingHorizontal: 10, height: 32, alignItems: 'center', backgroundColor: hovered ? theme.selected : theme.bg }))}>
          <Text numberOfLines={1} style={{ flex: 2, fontSize: 12, color: theme.text }}>{caseLabel(row.case_id)}</Text>
          {[fmt(row.left), fmt(row.right), row.delta == null ? '—' : (row.delta > 0 ? '+' : '') + row.delta.toFixed(3)].map((v, i) =>
            <Text key={i} style={{ flex: 1, fontSize: 12, color: theme.text, textAlign: 'right', fontVariant: ['tabular-nums'] }}>{v}</Text>)}
          <Text style={{ flex: 1, fontSize: 12, textAlign: 'right', color: row.flip === 'worse' ? theme.danger : row.flip === 'better' ? theme.success : theme.muted }}>{flipLabels[row.flip]}</Text>
        </Pressable>)}
      </View>
    </>}
  </View>;
}

function KV({ k, v, mono = true }: { k: string; v: React.ReactNode; mono?: boolean }) {
  const theme = useTheme();
  return <View style={{ flexDirection: 'row', gap: 10, paddingVertical: 3 }}>
    <Text style={{ width: 150, flexShrink: 0, fontSize: 12, color: theme.muted }}>{k}</Text>
    <Text selectable style={[{ flex: 1, minWidth: 0, fontSize: 12, color: theme.text }, mono ? { fontFamily: 'monospace', fontSize: 11, wordBreak: 'break-all' } as object : null]}>{v ?? '未知'}</Text>
  </View>;
}

export function ProvenanceDrawer({ run, onClose }: { run: RunView; onClose: () => void }) {
  const theme = useTheme();
  const p = run.provenance;
  const dirty = (p.source.modified_paths?.length ?? 0) + (p.source.added_paths?.length ?? 0) + (p.source.deleted_paths?.length ?? 0);
  return <View testID="provenance-drawer" style={{ position: 'absolute', left: 0, right: 0, bottom: 0, maxHeight: '72%', backgroundColor: theme.bg,
    borderTopWidth: 1, borderColor: theme.line, borderTopLeftRadius: 12, borderTopRightRadius: 12, shadowColor: '#000', shadowOpacity: .12, shadowRadius: 16 }}>
    <View style={{ height: 40, paddingHorizontal: 12, flexDirection: 'row', alignItems: 'center', gap: 8, borderBottomWidth: 1, borderColor: theme.line }}>
      <IdCard size={14} color={theme.muted} />
      <Text style={{ flex: 1, fontSize: 13, color: theme.text }}>run 身份与 provenance · {run.title}</Text>
      <IconButton label="关闭身份抽屉" icon={X} onPress={onClose} testID="close-provenance" />
    </View>
    <ScrollView contentContainerStyle={{ padding: 14, gap: 4 }}>
      <Section title="源码">
        <KV k="sn_source_ref" v={p.source.sn_source_ref} /><KV k="head_revision" v={p.source.head_revision} /><KV k="head_tree" v={p.source.head_tree} />
        <KV k="捕获根" v={(p.source.selected_roots ?? []).join(', ')} /><KV k="dirty 路径" v={dirty ? `${dirty} 个（改 ${p.source.modified_paths?.length ?? 0} · 增 ${p.source.added_paths?.length ?? 0} · 删 ${p.source.deleted_paths?.length ?? 0}）` : '0（捕获字节等于 head）'} mono={false} />
        <KV k="product archive" v={p.source.product_archive_sha256} />
      </Section>
      <Section title="配置 hash">
        {Object.entries(p.configuration).map(([k, v]) => <KV key={k} k={k} v={v} />)}
      </Section>
      <Section title="有效特性集合">
        <KV k="状态" v={p.features.status === 'not_projected' ? '未投影进 run 身份' : '已记录'} mono={false} />
        <KV k="说明" v={p.features.status === 'not_projected' ? 'runner 关闭的 KG／profile／经验／记忆等模块未写入 manifest，比较身份暂不含特性集合' : p.features.reason} mono={false} />
      </Section>
      <Section title={`模型服务代际 · ${p.model_services.status}`}>
        {p.model_services.services.map(svc => <KV key={svc.service_id} k={`${svc.service_id} · ${svc.kind}`} v={`${svc.model} · ${svc.protocol} · gen ${short(svc.generation, 12)}`} />)}
        <KV k="strict bindings" v={String(p.model_services.strict_bindings)} /><KV k="registry 指纹" v={p.model_services.fingerprint} />
        <KV k="未核验项" v={Object.entries(p.model_services.limits).filter(([, v]) => v !== 'loaded_observed').map(([k]) => k).join(', ') || '无'} mono={false} />
      </Section>
      <Section title="依赖">{Object.entries(p.runtime_dependencies).map(([k, v]) => <KV key={k} k={k} v={v} />)}</Section>
      <Section title="位置"><KV k="run_dir" v={run.run_dir} /><KV k="账本" v={run.ledger.present ? '已入账' : '未在账本中找到'} mono={false} /></Section>
    </ScrollView>
  </View>;
}

export function RunMaterials({ runs, selection, compact, onChange, onClose, onAttachCase }: { runs: RunView[]; selection: RunSelection; compact: boolean;
  onChange: (changes: Partial<RunSelection>) => void; onClose: () => void; onAttachCase?: () => void }) {
  const theme = useTheme();
  const run = runs.find(r => r.id === selection.runId);
  // Baseline = selection.against (also drives case-list deltas); the compared side defaults to this run.
  const [flipRight, setFlipRight] = useState<string | null>(null);
  const left = selection.against ?? selection.runId;
  const right = flipRight && runs.some(r => r.id === flipRight) ? flipRight : selection.runId;
  const title = useMemo(() => run ? `${(run.suite ?? '').toUpperCase()} · ${run.mode} · ${run.title}` : 'run 材料', [run]);
  return <View style={{ flex: 1, backgroundColor: theme.sidebar }}>
    <View style={{ minHeight: compact ? 48 : CHROME_HEIGHT, flexDirection: 'row', alignItems: 'center', paddingHorizontal: 12, gap: 4, borderBottomWidth: 1, borderColor: theme.line }}>
      <Text numberOfLines={1} style={{ flex: 1, minWidth: 0, color: theme.text }}>{title}</Text>
      {run && <IconButton label="run 身份" icon={IdCard} selected={selection.provenance} onPress={() => onChange({ provenance: !selection.provenance })} testID="toggle-provenance" />}
      <IconButton label="关闭材料" icon={X} onPress={onClose} testID="close-material" />
    </View>
    <View style={{ flexDirection: 'row', borderBottomWidth: 1, borderColor: theme.line, paddingHorizontal: 8 }}>
      {([['run-cases', '案例'], ['run-flips', '翻转']] as const).map(([id, label]) => <Pressable key={id} accessibilityRole="tab" accessibilityState={{ selected: selection.panel === id }}
        onPress={() => onChange({ panel: id, caseId: null })} testID={'panel-' + id} style={{ paddingHorizontal: 12, height: 36, justifyContent: 'center' }}>
        <Text style={{ color: selection.panel === id ? theme.text : theme.muted }}>{label}</Text></Pressable>)}
    </View>
    <ScrollView style={{ flex: 1, minHeight: 0 }} contentContainerStyle={{ padding: compact ? 16 : 14, paddingBottom: selection.provenance ? 360 : 24 }}>
      {!run ? <Copy small muted>该 run 未登记或不可读</Copy>
        : selection.caseId ? <>
          <Button label="返回案例列表" icon={ChevronLeft} onPress={() => onChange({ caseId: null })} testID="back-to-cases" />
          <CaseDetail key={run.id + selection.caseId} runId={run.id} caseId={selection.caseId} onAttach={onAttachCase} />
        </> : selection.panel === 'run-flips' ? <FlipView runs={runs} left={left} right={right}
          onPick={(side, id) => side === 'left' ? onChange({ against: id }) : setFlipRight(id)}
          onCase={id => onChange({ caseId: id })} />
          : <CaseRows run={run} against={selection.against && selection.against !== run.id ? selection.against : null}
            needsRead={selection.needsRead && run.attention.count > 0} onNeedsRead={needsRead => onChange({ needsRead })} onSelect={id => onChange({ caseId: id })} />}
    </ScrollView>
    {run && selection.provenance && <ProvenanceDrawer run={run} onClose={() => onChange({ provenance: false })} />}
  </View>;
}

type ExecutionCaseRow = { case_id: string; run_id: string; status: string | null; value: number | null };
type ExecutionCaseList = { execution_id: string; metric: string | null; partitions: number; cases: number; mean: number | null; rows: ExecutionCaseRow[] };

// A whole-benchmark execution has one run per partition; read it as one case table, lowest score first.
export function ExecutionCases({ executionId, selected, onSelect, onBack, onClose }: { executionId: string;
  selected: { runId: string; caseId: string } | null; onSelect: (runId: string, caseId: string) => void; onBack: () => void; onClose: () => void }) {
  const theme = useTheme();
  const list = useResource<ExecutionCaseList>(`/run-views/executions/${encodeURIComponent(executionId)}/cases`);
  const [query, setQuery] = useState('');
  const header = <View style={[s.row, { justifyContent: 'space-between', alignItems: 'center' }]}>
    {selected ? <Button label="返回逐题表" icon={ChevronLeft} onPress={onBack} testID="execution-cases-back" />
      : <Copy small muted>{list.data ? `${list.data.cases} 题 · ${list.data.partitions} 个分区 · ${list.data.metric ?? '主指标'} 均值 ${fmt(list.data.mean)}` : '正在读取逐题分数…'}</Copy>}
    <IconButton label="关闭" icon={X} onPress={onClose} testID="execution-cases-close" />
  </View>;
  if (selected) return <View style={{ gap: 10 }}>{header}<CaseDetail key={selected.runId + selected.caseId} runId={selected.runId} caseId={selected.caseId} /></View>;
  if (list.error) return <View style={{ gap: 10 }}>{header}<Copy small style={{ color: theme.error }}>{list.error}</Copy></View>;
  const rows = (list.data?.rows ?? []).filter(row => row.case_id.toLowerCase().includes(query.toLowerCase()));
  return <View testID="execution-case-list" style={{ gap: 10 }}>
    {header}
    <Input label="搜索案例" value={query} onChange={setQuery} placeholder="案例 ID，例如 alce:12" />
    {rows.slice(0, 300).map(row => <Pressable key={row.case_id} onPress={() => onSelect(row.run_id, row.case_id)}
      testID={'execution-case-' + row.case_id}
      style={hover(hovered => ({ flexDirection: 'row', gap: 10, paddingVertical: 4, paddingHorizontal: 6, borderRadius: 6,
        backgroundColor: hovered ? theme.selected : 'transparent' }))}>
      <Text style={{ width: 90, fontSize: 12, color: theme.text }}>{row.case_id}</Text>
      <Text style={{ width: 70, fontSize: 12, textAlign: 'right', color: theme.text }}>{fmt(row.value)}</Text>
      <Text style={{ flex: 1, fontSize: 12, color: theme.muted }}>{row.status ?? '—'}</Text>
    </Pressable>)}
    {rows.length > 300 && <Copy small muted>只显示前 300 题（低分在前）；用搜索找其余案例。</Copy>}
  </View>;
}
