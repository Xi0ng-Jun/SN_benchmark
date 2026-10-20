import { api, requestKey } from '../contracts';
import type { ConfirmSpec } from './parts';
import { ActionRequest, Candidate, IterationTree, Proposal, short, verbLabels } from './contracts';

// The person's writes (design §8): each builds a confirm spec whose request key is
// fixed when the dialog opens, so a retry inside one dialog cannot run twice.
export const RUN_BUDGET = { timeout_seconds: 3600 };

export function makeActions(tree: IterationTree, label: (candidateId: string | null) => string) {
  const node = (c: Candidate) => [{ label: '候选', value: `${label(c.id)} · ${short(c.id)}` }];
  return {
    freeze: (c: Candidate): ConfirmSpec => ({
      title: `冻结 ${label(c.id)}`, confirmLabel: '冻结',
      lines: [...node(c), { label: '效果', value: '协调服务把 worktree 的改动提交为冻结 commit；之后再改须派生子节点' },
        { label: 'worktree', value: c.worktree?.path ?? '—' }],
      run: () => api(`/candidates/${c.id}/freeze`, {}),
    }),
    run: (c: Candidate): ConfirmSpec => {
      const key = requestKey();
      return {
        title: `运行 ${label(c.id)}（新 attempt ${c.attempts.length + 1}）`, confirmLabel: '开始运行',
        lines: [...node(c), { label: '源码', value: c.frozen_commit ?? '—' },
          { label: '题单', value: `${tree.title} · selection ${short(tree.selection_hash, 12)}` },
          { label: '预算', value: `超时 ${RUN_BUDGET.timeout_seconds} 秒；不设费用上限，因此没有费用账本` },
          { label: '模型', value: '调用评测配置里的真实模型服务，会产生费用' }],
        run: () => api(`/candidates/${c.id}/run`, { budget: RUN_BUDGET, request_key: key }),
      };
    },
    derive: (c: Candidate): ConfirmSpec => {
      const key = requestKey();
      return {
        title: `从 ${label(c.id)} 派生子节点`, confirmLabel: '派生',
        lines: [...node(c), { label: '起点', value: c.frozen_commit ?? '—' }, { label: '效果', value: '在 SN 项目里从该冻结 commit 建一个新 worktree' }],
        note: { label: '说明', required: true, placeholder: '这个子节点要做什么（不经提议的手动派生必须写明）' },
        run: note => api(`/candidates/${c.id}/derive`, { request_key: key, note }),
      };
    },
    decide: (c: Candidate, decision: 'adopted' | 'rejected'): ConfirmSpec => ({
      title: `${decision === 'adopted' ? '采纳' : '放弃'} ${label(c.id)}`, confirmLabel: decision === 'adopted' ? '采纳' : '放弃',
      lines: [...node(c), { label: '性质', value: '研究判断：只在工作台标记，不改分数，不推送 SN 分支' }],
      note: { label: '理由', required: true, placeholder: '依据（区分事实与推断）' },
      run: note => api(`/candidates/${c.id}/decision`, { decision, note }),
    }),
    check: (c: Candidate): ConfirmSpec => ({
      title: `核对 ${label(c.id)} 的逐题预测`, confirmLabel: '核对',
      lines: [...node(c), { label: '依据', value: 'Δ父 比较记录（不可变）；同时对候选 worktree 做泄漏扫描' }],
      run: () => api(`/candidates/${c.id}/check`, {}),
    }),
    provision: (c: Candidate): ConfirmSpec => ({
      title: `重新准备 ${label(c.id)} 的 worktree`, confirmLabel: '准备',
      lines: node(c), run: () => api(`/candidates/${c.id}/provision`, {}),
    }),
    proposal: (p: Proposal, decision: 'approve' | 'reject'): ConfirmSpec => ({
      title: `${decision === 'approve' ? '批准' : '驳回'}提议「${p.card.title}」`, confirmLabel: decision === 'approve' ? '批准' : '驳回',
      lines: [{ label: '父候选', value: label(p.parent_candidate_id) }, { label: '作者', value: p.author },
        { label: '逐题预测', value: p.card.predictions.map(x => `${x.case_id.split(':').pop()?.slice(0, 8)} ${x.expect}${x.guard ? '（守护）' : ''}`).join('；') }],
      note: { label: '说明', required: decision === 'reject' },
      run: note => api(`/tasks/${p.task_id}/hypotheses/${p.id}/decision`, { decision, note }),
    }),
    deriveFromProposal: (p: Proposal): ConfirmSpec => {
      const key = requestKey();
      return {
        title: `按提议「${p.card.title}」派生候选`, confirmLabel: '派生',
        lines: [{ label: '父候选', value: label(p.parent_candidate_id) }, { label: '效果', value: '从父候选冻结 commit 建 worktree；之后可新建实现 Agent' }],
        run: () => api(`/candidates/${p.parent_candidate_id}/derive`, { request_key: key, proposal_id: p.id, note: p.card.title }),
      };
    },
    confirmRequest: (r: ActionRequest): ConfirmSpec => ({
      title: `确认请求：${verbLabels[r.verb] ?? r.verb} ${r.target_id ? label(r.target_id) : ''}`, confirmLabel: '确认并执行',
      lines: [{ label: '发起', value: `${r.requested_by} · ${new Date(r.created_at).toLocaleString('zh-CN', { hour12: false })}` },
        { label: '参数', value: JSON.stringify(r.params) }, { label: '理由', value: r.reason },
        { label: '执行', value: '服务器按上面登记的参数原样执行，回执写回请求' }],
      note: { label: '备注', required: false },
      run: note => api(`/action-requests/${r.id}/confirm`, { note }),
    }),
    rejectRequest: (r: ActionRequest): ConfirmSpec => ({
      title: `驳回请求：${verbLabels[r.verb] ?? r.verb} ${r.target_id ? label(r.target_id) : ''}`, confirmLabel: '驳回',
      lines: [{ label: '发起', value: r.requested_by }, { label: '理由', value: r.reason }],
      note: { label: '驳回说明', required: true },
      run: note => api(`/action-requests/${r.id}/reject`, { note }),
    }),
  };
}
export type Actions = ReturnType<typeof makeActions>;

// Short tree labels: C0, then A, B … for its children, A1, A2 … below them.
export function treeLabels(tree: IterationTree): Record<string, string> {
  const labels: Record<string, string> = {};
  const children: Record<string, Candidate[]> = {};
  for (const c of tree.candidates) (children[c.parent_candidate_id ?? ''] ??= []).push(c);
  const visit = (c: Candidate, name: string) => {
    labels[c.id] = name;
    (children[c.id] ?? []).forEach((child, i) => visit(child,
      name === 'C0' ? String.fromCharCode(65 + (i % 26)) + (i >= 26 ? String(Math.floor(i / 26)) : '')
        : /\d$/.test(name) ? `${name}.${i + 1}` : `${name}${i + 1}`));
  };
  const roots = tree.candidates.filter(c => !c.parent_candidate_id || !tree.candidates.some(p => p.id === c.parent_candidate_id));
  roots.forEach((root, i) => visit(root, root.id === tree.root_candidate_id ? 'C0' : `L${i}`));
  return labels;
}
