"""Role briefs: the first attachment an Agent receives, generated from server state.

The analyst brief points at the read tools and the evaluation documents; the
implementer brief carries only the approved card's implementer-facing fields
and the worktree, never questions, gold, case ids or scores.
"""
from __future__ import annotations

import json
from pathlib import Path

# Evaluation documents an analyst can use; paths are relative to the evaluation repository.
ANALYST_DOCS = (
    ('CURRENT_STATE.md', '目标、不变量与执行边界（先读）'),
    ('docs/goal-and-roadmap.md', '总目标、理想迭代循环、R1 规划与当前差距'),
    ('docs/evaluation-status.md', '按日期的实验事实与验证记录'),
    ('docs/failure-ledger.md', '已人工确认的失败归类与归因规则（含题面、gold 摘录）'),
    ('docs/hypotheses/', '已有假设卡（如 H1），包括被停止或否定的'),
    ('docs/notebook-benchmark-standards-and-conformance.md', '五套 benchmark 的官方协议与本实现的符合性'),
    ('docs/benchmark-result-tables.md', '正式结果表的口径与填报规则'),
    ('docs/notebook-comparison-case-review-2026-09-28.md', '一次比较案例复核的做法与结论'),
    ('docs/evaluation-context.md', '项目背景与数据来源'),
    ('skills/sn-eval/SKILL.md', '评测命令、付费边界与禁止事项'),
)

CARD_EXAMPLE = {
    'title': '检索块过多稀释了相关证据',
    'component': '检索 / 重排',
    'mechanism': 'chunk 模式取 16 个块且未重排，相关块排名靠后，生成时被无关块淹没',
    'evidence': [{'run_id': '<run_id>', 'case_id': '<case_id>', 'note': '上下文精度 0/16，gold 段落未进入前 16'}],
    'alternatives': ['gold 段落未被导入（需核对分块）'],
    'predictions': [
        {'case_id': '<case_id>', 'expect': 'improve', 'note': '证据召回应至少 1/2'},
        {'case_id': '<another_case_id>', 'expect': 'not_worse', 'guard': True},
    ],
    'expected_benefit': '检索漏召导致的低分题',
    'possible_harm': '需要多段汇总的题可能召回不足',
    'falsification': '改后目标题召回不变，或守护题变差',
    'change_scope': ['backend/app/services/retrieval.py'],
    'implementer_brief': '在 chunk 检索后启用重排并把送入生成的块数降到 6；保持其余参数不变；用合成文档写单元测试覆盖“相关块排名靠后”的情况。',
}


def analyst_brief(task, *, eval_root, tool, worktree, cwd):
    s = f'{tool} --task {task["id"]}'
    docs = '\n'.join(f'- `{eval_root}/{path}`：{note}' for path, note in ANALYST_DOCS)
    return f'''# 你的角色：分析 Agent（Silicon Notebook 评测）

任务：{task["title"]}
目标：{task["goal"] or "（未填写）"}
评测仓库：`{eval_root}`。你的工作目录：`{cwd}`{"（这个 Agent 按旧方式建在 SN worktree 里；角色仍是分析，不要修改 SN 代码）" if worktree and cwd == worktree else ""}。
被测 SN 的改动 worktree：`{worktree or "本任务未绑定"}`，你只读它来定位代码，**不要修改 SN 代码**。

你的职责：读评测结果和案例证据，做失败归类，提出可证伪的假设卡草稿。你能看 gold；正因为能看 gold，你不写 SN 代码，改码由另一个看不到 gold 的实现 Agent 完成。

## 查询工具（只读、免费，随时可用）
```
S="{s}"
$S task                       # 本任务目标、worktree、运行、假设卡
$S runs                       # 本任务的运行（--all 看全部已登记运行）
$S cases <run_id> --needs-read   # 待读题；去掉 --needs-read 看全部
$S case <run_id> <case_id>    # 单题完整证据：问题、SN 答案、逐题分、gold、引用、按排名的检索块
$S flips <left_run> <right_run>  # 同题配对翻转表
$S hypotheses                 # 本任务假设卡及验证结果
$S docs                       # 下方文档索引
```
输出是 JSON。run 目录里还有 outputs.jsonl、planned.jsonl、model-events.jsonl，可直接读。

## 提交假设卡草稿（需要人批准后才会实施）
把卡片写成 JSON 文件，然后 `$S hypothesis-draft --file card.json`。格式示例：
```json
{json.dumps(CARD_EXAMPLE, ensure_ascii=False, indent=1)}
```
- predictions 是逐题预测：expect 取 improve / unchanged / not_worse / worse；guard=true 表示守护题（不应变差）。验证时按翻转表逐条核对，不看均分。
- implementer_brief 是唯一会交给实现 Agent 的说明：写机制和改法，**不得包含题面、gold、case id、论文内容或分数**，提交前会做泄漏扫描。

## 可参考的评测文档
{docs}

## 规则
- 每个判断引用具体 run_id、case_id 或文件路径；区分实验事实、你的推断和未知。
- 低词面 F1 不等于语义错；gold 不是穷举；“没有记录到”不等于“没有发生”；导入失败或模型服务不可用归为基础设施，不当作 SN 质量问题。
- 样本小时把结论限定为“所选案例上的观察”。
- **不要运行 run_notebook_benchmarks.py 或任何会调用模型的评测命令**；需要新运行时写进假设卡，由人在界面确认预算后运行。
- 不修改 SN worktree、不提交 git。
'''


def implementer_brief(task, card, *, worktree, tool, hypothesis_id, start_dirty):
    s = f'{tool} --task {task["id"]} --role implementer --hypothesis {hypothesis_id}'
    scope = '\n'.join(f'- `{p}`' for p in card.get('change_scope') or []) or '- （卡片未限定，改动尽量集中在上述组件）'
    dirty = '\n'.join(f'- `{p}`' for p in start_dirty) or '- （无）'
    return f'''# 你的角色：实现 Agent（Silicon Notebook）

你的工作目录是 SN worktree `{worktree}`。你要实现一张已经由人批准的假设卡。

## 要实现的改动
- 标题：{card["title"]}
- 组件：{card["component"]}
- 机制：{card["mechanism"]}
- 改法说明：{card["implementer_brief"]}
- 可能的副作用（要用测试防住）：{card.get("possible_harm") or "（未写）"}

允许改动的范围：
{scope}

worktree 起点已有这些未提交改动，它们属于用户，不要覆盖或回退：
{dirty}

## 规则
- 你看不到、也**不要去找**评测题目、gold、案例或分数：不要读取评测仓库（`var/`、`docs/failure-ledger.md`、`docs/hypotheses/`、`apps/sn-workbench/test-results/` 等）。这样做是为了防止改动贴合考题。
- 不写死任何题面、论文、数据集名或 case id；用合成数据写单元测试，覆盖受益和可能受害两种情况。
- 遵守本仓库 `AGENTS.md`；运行 SN 自己的测试；不提交 git，不启动评测运行。
- 完成后自查：`{s} leak-check`（只报告是否与考题重叠，不显示考题内容）。
- 回复时说明：改了哪些文件、为什么、测试命令与结果、残余风险。
'''


def resolve_brief(repository, task_id, session_id, *, tool):
    """Snapshot pieces for the `brief` context kind."""
    with repository.connect() as conn:
        task = repository._require(conn, 'tasks', task_id)
        session = conn.execute('SELECT * FROM task_sessions WHERE id=? AND task_id=?', (session_id, task_id)).fetchone()
        if session is None:
            raise ValueError('Session is not attached to this task')
        workspace = repository._require(conn, 'workspaces', task['workspace_id'])
        bound = (conn.execute('SELECT * FROM task_worktrees WHERE task_id=?', (task_id,)).fetchone()
                 if conn.execute("SELECT 1 FROM sqlite_master WHERE name='task_worktrees'").fetchone() else None)
        cwd = repository.session_cwd(conn, session, task)
        card = None
        if session['role'] == 'implementer':
            from .hypothesis_worktrees import hypothesis_binding
            saved_binding = hypothesis_binding(conn, task_id, session['hypothesis_id'])
            if saved_binding is not None:
                bound = dict(saved_binding, start_snapshot_json=json.dumps(saved_binding['start_snapshot']))
            row = conn.execute('SELECT card FROM hypotheses WHERE id=? AND task_id=?',
                               (session['hypothesis_id'], task_id)).fetchone()
            if row is None:
                raise ValueError('Implementer session has no hypothesis card')
            card = json.loads(row['card'])
    worktree = cwd if session['role'] == 'implementer' else bound['worktree_path'] if bound else None
    if session['role'] == 'implementer':
        start = json.loads(bound['start_snapshot_json']) if bound else {}
        text = implementer_brief(task, card, worktree=worktree, tool=tool, hypothesis_id=session['hypothesis_id'],
                                 start_dirty=[d['path'] for d in start.get('dirty', [])])
        title = '实现 Agent 工作说明 · ' + card['title']
    else:
        text = analyst_brief(task, eval_root=str(Path(workspace['location'])), tool=tool, worktree=worktree, cwd=cwd)
        title = '分析 Agent 工作说明 · ' + task['title']
    return title, text, {'task_id': task_id, 'session_id': session_id, 'role': session['role'] or 'analyst'}
