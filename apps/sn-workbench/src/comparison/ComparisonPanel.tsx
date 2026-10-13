import React from 'react';
import { View } from 'react-native';
import type { ComparisonReason, ComparisonReport, Result, TaskComparison } from '../contracts';
import { Block, Button, Copy, Fold, s, useTheme } from '../ui';

// Controlled read-only comparison view. The parent owns the task-linked result
// list, both selections, the read request, busy/error and the last accepted
// TaskComparison. This file never fetches, stores, generates ids, selects a
// default/latest/highest result or recomputes, totals, ranks or tests anything:
// it renders the official batch values, real denominators and the core-produced
// paired estimates exactly as reported. A read failure keeps each side's real
// status/reason and still offers the matching open-result entry.

export type ComparisonPanelProps = {
  results: Pick<Result, 'id' | 'suite' | 'method'>[];
  leftId: string | null;
  rightId: string | null;
  onSelect: (side: 'left' | 'right', id: string) => void;
  onRead: () => void;
  busy: boolean;
  error: string | null;
  data: TaskComparison | null;
  onOpenResult: (id: string) => void;
};

const sides = ['left', 'right'] as const;

function shortId(id: string): string {
  return id.length > 8 ? id.slice(0, 8) : id;
}

function nonBlank(value: string | null): value is string {
  return value !== null && value.trim().length > 0;
}

function orUnspecified(value: string | null | undefined): string {
  return value !== null && value !== undefined && value.trim() !== '' ? value : '未提供';
}

function formatNumber(value: number): string {
  // Exact number display keeps valid zero and tiny non-zero estimates; no
  // rounding or per-case recomputation happens in this view.
  return String(value);
}

function formatSigned(value: number): string {
  return value > 0 ? `+${String(value)}` : String(value);
}

function metricListText(metrics: readonly string[]): string {
  return metrics.length > 0 ? metrics.join('、') : '无';
}

function comparisonStatusText(status: TaskComparison['status']): string {
  return status === 'comparable' ? '两份结果满足同题比较条件。'
    : status === 'not_comparable' ? '两份结果当前不能配对比较。'
      : status === 'unavailable' ? '比较当前不可用。'
        : '比较状态未确认。';
}

function methodLabel(index: number): string {
  return index === 0 ? '左' : index === 1 ? '右' : `方法 ${index + 1}`;
}

// Real reason fields are shown as given; blank fields are labeled instead of
// being silently dropped or replaced by an inferred cause.
function ReasonFacts({ reason }: { reason: ComparisonReason }) {
  return <View style={{ gap: 2, minWidth: 0 }}>
    <Copy small muted style={{ flexShrink: 1, minWidth: 0 }}>{`原因代码：${orUnspecified(reason.code)}`}</Copy>
    <Copy small style={{ flexShrink: 1, minWidth: 0 }}>{orUnspecified(reason.message)}</Copy>
    <Copy small muted style={{ flexShrink: 1, minWidth: 0 }}>{`详情：${orUnspecified(reason.detail)}`}</Copy>
  </View>;
}

function officialMetricText(method: ComparisonReport['methods'][number], metric: string,
  fallbackDenominator: number | undefined): string {
  if (!Object.prototype.hasOwnProperty.call(method.metrics, metric)) return '未提供';
  const denominator = Object.prototype.hasOwnProperty.call(method.metric_denominators, metric)
    ? method.metric_denominators[metric] : fallbackDenominator;
  return `${formatNumber(method.metrics[metric])} / ${typeof denominator === 'number' ? String(denominator) : '分母未提供'}`;
}

function SideFacts({ side, view, onOpenResult }: {
  side: 'left' | 'right';
  view: TaskComparison['left'];
  onOpenResult: (id: string) => void;
}) {
  const theme = useTheme();
  const name = side === 'left' ? '左' : '右';
  const summary = view.summary;
  return <View style={{ flexGrow: 1, flexBasis: 260, minWidth: 0, gap: 6, borderWidth: 1, borderColor: theme.line, borderRadius: 8, padding: 12 }}>
    <Copy small style={{ fontWeight: '600', flexShrink: 1, minWidth: 0 }}>{`${name} · ${shortId(view.result_id)}`}</Copy>
    <Copy small muted>{view.status === 'readable' ? '结果可读' : '结果不可用'}</Copy>
    {summary !== null && <>
      <Copy small muted style={{ flexShrink: 1, minWidth: 0 }}>{`套件：${orUnspecified(summary.suite)} · 范围：${orUnspecified(summary.scope)}`}</Copy>
      <Copy small muted style={{ flexShrink: 1, minWidth: 0 }}>{`方法：${orUnspecified(summary.method.name)} · 基准完整题数：${String(summary.bundle_case_count)} · 本结果题数：${String(summary.case_count)}`}</Copy>
    </>}
    {view.reason !== null && <ReasonFacts reason={view.reason} />}
    <Button label={`打开${name}成绩`} testID={side === 'left' ? 'open-left-result' : 'open-right-result'}
      accessibilityLabel={`打开${name}侧成绩：${shortId(view.result_id)}`}
      onPress={() => onOpenResult(view.result_id)} />
  </View>;
}

export function ComparisonPanel({ results, leftId, rightId, onSelect, onRead, busy, error, data, onOpenResult }: ComparisonPanelProps) {
  const theme = useTheme();
  const leftReady = nonBlank(leftId);
  const rightReady = nonBlank(rightId);
  const sameSelection = leftReady && rightReady && leftId === rightId;
  const selectionsReady = leftReady && rightReady && !sameSelection;
  const canRead = selectionsReady && !busy;
  // Pure projection gate: an accepted TaskComparison is only rendered as the
  // current comparison for the exact left/right tuple the parent requested.
  // Swapping or clearing a selection never re-labels the old report as the
  // current one; the old facts stay visible only as the last read record.
  const tupleMatches = data !== null && leftId === data.left_result_id && rightId === data.right_result_id;
  const staleSelection = data !== null && !tupleMatches;
  const report = data !== null && tupleMatches && data.status === 'comparable' ? data.report : null;
  const errorText = error === null ? null : error.trim().length > 0 ? error : '未提供错误详情';
  const selectionHint = results.length < 2
    ? `当前任务只有 ${results.length} 份已关联结果，至少需要两份不同结果才能读取比较。`
    : !leftReady || !rightReady ? '请选择同一任务中两个不同且已关联的结果，再读取比较。'
      : sameSelection ? '左右两侧选择了同一份结果，请改为两份不同结果。'
        : '左右已选择两份不同结果，可以读取比较。';

  return <View testID="comparison-panel" style={{ width: '100%', maxWidth: 820, alignSelf: 'center', minWidth: 0, gap: 12 }}>
    <View style={{ gap: 4, minWidth: 0 }}>
      <Copy small style={{ fontWeight: '600' }}>结果比较</Copy>
      <Copy small muted style={{ flexShrink: 1, minWidth: 0 }}>选择同一任务中两份不同且已关联的结果；本页只读既有结果，不重新评分、不生成新成绩。</Copy>
    </View>

    <Block title="选择结果">
      <View style={{ gap: 10, minWidth: 0 }}>
        {sides.map(side => {
          const selectedId = side === 'left' ? leftId : rightId;
          const name = side === 'left' ? '左' : '右';
          return <View key={side} style={{ gap: 6, minWidth: 0 }}>
            <Copy small style={{ fontWeight: '600' }}>{`${name}侧结果`}</Copy>
            {results.length === 0
              ? <Copy small muted>当前任务没有已关联结果。</Copy>
              : results.map(result => <View key={`${side}-${result.id}`} style={{ gap: 2, minWidth: 0 }}>
                <Button label={`${name} · ${orUnspecified(result.suite)} · ${orUnspecified(result.method.name)}`}
                  selected={selectedId === result.id} testID={`${side}-${result.id}`}
                  accessibilityLabel={`选择${name}侧结果：${orUnspecified(result.suite)}，${orUnspecified(result.method.name)}，结果 ID ${shortId(result.id)}`}
                  onPress={() => onSelect(side, result.id)} />
                <Copy small muted style={{ flexShrink: 1, minWidth: 0 }}>{`结果 ID：${shortId(result.id)}`}</Copy>
              </View>)}
          </View>;
        })}
      </View>
    </Block>

    <View style={{ gap: 6, minWidth: 0, borderTopWidth: 1, borderColor: theme.line, paddingTop: 10 }}>
      <View style={s.row}>
        <Button label="读取比较" primary testID="comparison-read" disabled={!canRead}
          accessibilityLabel="读取比较" onPress={() => { if (canRead) onRead(); }} />
      </View>
      <View testID="comparison-status" accessibilityRole={errorText !== null ? 'alert' : undefined} style={{ gap: 4, minWidth: 0 }}>
        {errorText !== null && <Copy small style={{ color: theme.error, flexShrink: 1, minWidth: 0 }}>{`读取比较失败：${errorText}`}</Copy>}
        {busy && <Copy small muted>正在读取比较…</Copy>}
        {data === null && !busy && errorText === null && <Copy small muted style={{ flexShrink: 1, minWidth: 0 }}>{selectionHint}</Copy>}
        {data !== null && <>
          {staleSelection
            ? <>
              <Copy small style={{ fontWeight: '600', flexShrink: 1, minWidth: 0 }}>左右选择已变化，当前显示的是上一次读取记录；请重新读取比较。</Copy>
              <Copy small muted style={{ flexShrink: 1, minWidth: 0 }}>{`上次读取（左 ${shortId(data.left_result_id)}、右 ${shortId(data.right_result_id)}）：${comparisonStatusText(data.status)}`}</Copy>
            </>
            : <Copy small style={{ fontWeight: '600' }}>{comparisonStatusText(data.status)}</Copy>}
          {tupleMatches && data.status === 'comparable' && data.report === null && <Copy small muted>状态显示可比，但本次没有可展示的完整报告。</Copy>}
          {data.reason !== null && <ReasonFacts reason={data.reason} />}
        </>}
      </View>
    </View>

    {data !== null && <Block title={staleSelection ? '上次读取的各侧状态' : '各侧读取状态'}>
      <View style={[s.row, { alignItems: 'stretch' }]}>
        <SideFacts side="left" view={data.left} onOpenResult={onOpenResult} />
        <SideFacts side="right" view={data.right} onOpenResult={onOpenResult} />
      </View>
    </Block>}

    {data !== null && tupleMatches && data.status === 'comparable' && report !== null && <>
      <Block title="原评分汇总">
        <View testID="comparison-official" style={{ gap: 8, minWidth: 0 }}>
          <Copy small style={{ flexShrink: 1, minWidth: 0 }}>{`套件：${orUnspecified(report.suite)} · 范围：${orUnspecified(report.scope)} · 本次比较题数：${String(report.case_count)}`}</Copy>
          {report.methods.length === 2
            ? report.common_metrics.map(metric => <View key={metric} style={[s.row, { minWidth: 0, alignItems: 'flex-start' }]}>
              <Copy small style={{ fontWeight: '600', flexShrink: 1, minWidth: 0 }}>{metric}</Copy>
              <Copy small muted>{`左 ${officialMetricText(report.methods[0], metric, report.metric_denominators[metric])}`}</Copy>
              <Copy small muted>{`右 ${officialMetricText(report.methods[1], metric, report.metric_denominators[metric])}`}</Copy>
            </View>)
            : <Copy small muted style={{ flexShrink: 1, minWidth: 0 }}>原报告的参与方法不是两份，无法按左右展示官方对照。</Copy>}
          {report.methods.map((method, index) => <View key={method.method_id} style={{ gap: 2, minWidth: 0, borderTopWidth: 1, borderColor: theme.line, paddingTop: 6 }}>
            <Copy small style={{ fontWeight: '600', flexShrink: 1, minWidth: 0 }}>{`${methodLabel(index)} · ${orUnspecified(method.method.name)} · ${shortId(method.method_id)}`}</Copy>
            <Copy small muted style={{ flexShrink: 1, minWidth: 0 }}>{`待评指标：${metricListText(method.pending_metrics)}`}</Copy>
            <Copy small muted style={{ flexShrink: 1, minWidth: 0 }}>{`批量指标：${metricListText(method.batch_only_metrics)}`}</Copy>
            <Copy small muted style={{ flexShrink: 1, minWidth: 0 }}>{`未共享指标：${metricListText(method.unshared_metrics)}`}</Copy>
            <Copy small muted style={{ flexShrink: 1, minWidth: 0 }}>{`缺少评分指标：${metricListText(method.unavailable_metrics)}`}</Copy>
          </View>)}
          <Fold title="本次比较的完整题目 ID（原报告）" value={report.case_ids} />
          <Fold title="每个指标的合格题目 ID（原报告）" value={report.metric_case_ids} />
          <Fold title="报告说明（原报告）" value={report.interpretation} />
        </View>
      </Block>

      <Block title="附加逐题配对">
        <View testID="comparison-paired" style={{ gap: 8, minWidth: 0 }}>
          {report.comparisons.length === 0
            ? <Copy small muted>原报告没有比较条目。</Copy>
            : report.comparisons.map((comparison, index) => <View key={String(index)} style={{ gap: 6, minWidth: 0, borderTopWidth: index === 0 ? 0 : 1, borderColor: theme.line, paddingTop: index === 0 ? 0 : 8 }}>
              <Copy small style={{ flexShrink: 1, minWidth: 0 }}>{`方向：${orUnspecified(comparison.direction)}${comparison.direction === 'right minus left' ? '（右成绩减左成绩）' : ''}`}</Copy>
              {Object.keys(comparison.paired_mean_differences).length === 0
                ? <Copy small muted>原报告没有逐题均值差。</Copy>
                : Object.entries(comparison.paired_mean_differences).map(([metric, value]) => <View key={metric} style={s.row}>
                  <Copy small style={{ fontWeight: '600', flexShrink: 1, minWidth: 0 }}>{metric}</Copy>
                  <Copy small muted>{`逐题均值差 ${formatSigned(value)}`}</Copy>
                </View>)}
              <Fold title="分组差异（原报告）" value={comparison.group_differences} />
              <Fold title="不确定性（原报告）" value={comparison.uncertainty} />
              <Fold title="逐题配对明细（原报告）" value={comparison.paired} />
            </View>)}
          <Fold title="方法差异（原报告）" value={report.method_differences} />
        </View>
      </Block>
    </>}
  </View>;
}
