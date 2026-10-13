import { History, judgmentLabels } from '../contracts';

// Prepared/started events are bookkeeping for one observed outcome; the
// records list shows the outcome. The full event stream stays in the export.
const hidden = new Set(['agent.creation.prepared', 'agent.creation.started', 'agent.message.prepared', 'agent.message.started']);
export const visibleHistory = (items: readonly History[]) => items.filter(item => !hidden.has(item.kind));

const messageOutcomes: Record<string, string> = {
  acknowledged: '已送达', not_attempted: '未发出', unknown: '结果未确认',
};

export function historyLabel(item: History): string {
  const c = item.content;
  switch (item.kind) {
    case 'task.created': return '创建任务';
    case 'result.attached': return '接入结果 · ' + String(c.suite ?? '');
    case 'review.saved': return '人工判断 · ' + (judgmentLabels[String(c.judgment)] ?? String(c.judgment));
    case 'note.saved': return '调查笔记';
    case 'plan.draft.saved': return '保存评测计划草稿';
    case 'job.prepared': return '准备评测运行';
    case 'job.inputs_source_captured': return '捕获 SN 源码与输入';
    case 'job.execution_started': return '开始评测运行';
    case 'job.execution_finished': return '评测运行结束';
    case 'job.execution_cancelled': case 'job.cancelled': return '取消评测运行';
    case 'session.attached': return '关联 Agent';
    case 'agent.creation.observed': return '新建 Agent · ' + (c.status === 'created' ? '成功' : String(c.status ?? '结果未确认'));
    case 'agent.message.observed': return '发送消息 · ' + (messageOutcomes[String(c.status)] ?? String(c.status ?? ''))
      + (c.code ? `（${String(c.code)}）` : '');
    case 'candidate.saved': return '保存候选';
    case 'candidate.decided': return '候选判断';
    case 'hypothesis.drafted': return '假设卡草稿 · ' + String(c.title ?? '');
    case 'hypothesis.decided': return '假设卡' + (c.decision === 'approve' ? '已批准' : '已驳回');
    case 'hypothesis.checked': return `核对预测 · 兑现 ${String(c.held)} / ${String(c.decided)}`;
    case 'hypothesis.concluded': return '假设卡结论';
    case 'hypothesis.worktree.requested': return '准备假设卡的独立工作目录';
    case 'hypothesis.worktree.ready': return '假设卡工作目录已就绪';
    case 'hypothesis.worktree.selected': return '切换当前假设卡工作目录';
    default: return item.kind;
  }
}
