import type { TaskSession } from '../contracts';
import type { ActionRequest, Candidate, IterationTree, Proposal } from './contracts';

// What each node is waiting for, inferred only from recorded state: candidate state,
// proposals, requests, comparison records, the implementer's live status and the
// candidate worktree's changes. `you` steps carry the action the page offers.
export type Who = 'you' | 'agent' | 'run';
export type StepAction = 'run' | 'freeze' | 'check' | 'decide' | 'implementer' | 'open-agent' | 'request' | 'proposal' | 'derive-proposal' | 'analyst';
export type Step = { who: Who; text: string; action?: StepAction; proposalId?: string; requestId?: string; sessionId?: string; rank: number };

export type AgentLive = { status: string | null };
export type NextContext = {
  tree: IterationTree; proposals: Record<string, Proposal>; requests: ActionRequest[];
  sessions: TaskSession[]; live: Record<string, AgentLive>; changes: Record<string, number | undefined>;
};

const busy = (status: string | null | undefined) => status === 'running' || status === 'initializing';

export function implementersOf(candidateId: string, sessions: TaskSession[]) {
  return sessions.filter(s => s.role === 'implementer' && s.candidate_id === candidateId);
}

export function nextStep(c: Candidate, ctx: NextContext): Step | null {
  const isRoot = c.id === ctx.tree.root_candidate_id;
  const proposal = c.proposal_id ? ctx.proposals[c.proposal_id] : undefined;
  const pending = ctx.requests.find(r => r.status === 'pending' && r.target_id === c.id);
  if (pending) return { who: 'you', text: 'Agent 请求' + ({ 'node-run': '运行', 'node-freeze': '冻结', 'node-derive': '派生', 'node-adopt': '采纳', 'node-reject': '放弃' }[pending.verb] ?? pending.verb) + '，等你确认', action: 'request', requestId: pending.id, rank: 0 };
  switch (c.state) {
    case 'provisioning': return { who: 'run', text: '正在准备 worktree', rank: 9 };
    case 'running': return { who: 'run', text: '运行中', rank: 9 };
    case 'frozen': return { who: 'you', text: isRoot ? '还没有基线，先跑一次' : '已冻结，还没运行', action: 'run', rank: 3 };
    case 'failed': return { who: 'you', text: '运行没通过准入，看原因后再跑一次', action: 'run', rank: 4 };
    case 'implementing': {
      const agents = implementersOf(c.id, ctx.sessions);
      const working = agents.find(s => busy(ctx.live[s.id]?.status));
      const files = ctx.changes[c.id];
      if (working) return { who: 'agent', text: '实现 Agent 正在改', action: 'open-agent', sessionId: working.id, rank: 8 };
      if (files) return { who: 'you', text: `改了 ${files} 个文件，检查后冻结`, action: 'freeze', rank: 2 };
      if (agents.length) return { who: 'you', text: '实现 Agent 停下了但还没有改动，去对话里看看', action: 'open-agent', sessionId: agents.at(-1)!.id, rank: 5 };
      if (proposal) return { who: 'you', text: '建实现 Agent 来改代码', action: 'implementer', rank: 3 };
      // A manual derive has no implementer brief: the person edits the worktree. Freezing an
      // empty change is refused, so it is only offered once there is something to freeze.
      return files === 0 ? { who: 'you', text: 'worktree 还没有改动；在里面改好后再冻结', rank: 5 }
        : { who: 'you', text: '在 worktree 里改好后冻结', action: 'freeze', rank: 5 };
    }
    case 'evaluated': {
      const under = Object.values(ctx.proposals).filter(p => p.parent_candidate_id === c.id);
      const draft = under.find(p => p.status === 'draft');
      if (draft) return { who: 'you', text: `提议「${draft.card.title}」等你批准`, action: 'proposal', proposalId: draft.id, rank: 1 };
      const approved = under.find(p => p.status === 'approved' && !ctx.tree.candidates.some(x => x.proposal_id === p.id));
      if (approved) return { who: 'you', text: `提议已批准，派生候选`, action: 'derive-proposal', proposalId: approved.id, rank: 2 };
      if (proposal && ['approved', 'implementing'].includes(proposal.status) && c.comparisons?.parent.status === 'admitted')
        return { who: 'you', text: '有结果了，核对提议的逐题预测', action: 'check', rank: 2 };
      if (!isRoot && !c.decision) return { who: 'you', text: '下结论：采纳、放弃，或再跑一次看波动', action: 'decide', rank: 6 };
      const frontier = (isRoot || c.decision?.decision === 'adopted') && !ctx.tree.candidates.some(x => x.parent_candidate_id === c.id) && !under.length;
      if (frontier) return ctx.sessions.some(s => s.role === 'analyst')
        ? { who: 'agent', text: '让分析 Agent 读题并提议', action: 'analyst', rank: 7 }
        : { who: 'you', text: '建分析 Agent，从这里提下一个假设', action: 'analyst', rank: 7 };
      return null;
    }
    default: return null;
  }
}

export function stepsOf(ctx: NextContext): Record<string, Step | null> {
  return Object.fromEntries(ctx.tree.candidates.map(c => [c.id, nextStep(c, ctx)]));
}
