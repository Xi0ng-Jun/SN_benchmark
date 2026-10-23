"""Role briefs: the first attachment an Agent receives, generated from server state.

The analyst brief points at the read tools and the evaluation documents; the
implementer brief carries only the approved card's implementer-facing fields
and the worktree. Referenced clusters provide observed search evidence without gold annotations or case ids.
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


ITERATION_VERBS = '''## 迭代动词（只读；写动作只能申请）
```
$S iteration show             # 本迭代：起点、冻结题单、候选树与每个候选的 Δ父／Δ根 摘要
$S node list [--rank root]    # 谱系（默认树序；--rank root 按 Δ根 平铺排行）
$S node show <候选>           # attempt 与准入结论、参考运行、比较记录、相关请求
$S compare <候选> <参考候选>   # 对父／根返回已存比较记录，其他为临时比较（不入库）
$S requests [--status pending]
$S request withdraw <请求> --reason '…'   # 撤回误提且仍待确认的请求
```
派生、冻结、运行、采纳／放弃、新建迭代**只能申请**：`$S node derive <父> --proposal <已批准提议> --reason '…'`、`$S node run <候选> --reason '…'`、`$S node adopt|reject <候选> --reason '…'`。请求只登记，不执行；人在工作台确认后由服务器按登记的参数执行。不要用 `scripts/sn-workbench`，那是人确认请求用的。

'''


ITERATION_DUTIES = (
    '跨节点看整棵候选树，单个节点的分析交给绑定该节点的分析 Agent：'
    '（1）横向比较——兄弟或任意两节点谁好、各自好在哪些题差在哪些题，哪些题所有候选都答错（顽固题）；'
    '（2）假设复盘——各提议的逐题预测兑现了多少，哪类改动有效、哪类无效，下一步往哪投入；'
    '（3）合并建议——改动互补（好转的题少重叠）时建议合并派生；'
    '（4）全树守护——同一批题反复来回翻转（过拟合信号）、采纳路径上相对根下降的题。'
    '结论落到可证伪的假设卡草稿或合并派生申请。')

ITERATION_SCOPE_REQUESTS = (
    '作为迭代分析 Agent，你**只申请派生**：从某个已评测候选派生（`--proposal` 指定已批准提议），'
    '或合并派生 `$S node derive <主父候选> --integrate <被并入候选> --method merge|cherry-pick|rewrite --reason \'…\'`'
    '（合并后的候选必须自己重跑，不用分差相加估计）。冻结、运行、采纳／放弃属于具体候选，不要申请。\n\n')


def analyst_brief(task, *, eval_root, tool, worktree, cwd, candidates=None, node=None, tree=None):
    """``candidates`` are (id, frozen_commit, worktree) rows when the Task backs an iteration.

    ``node`` (from ``node_context``) binds the analyst to one evaluated candidate:
    the brief then carries that node, its path from the root, and drafts default
    to it, instead of listing the whole tree. ``tree`` (from ``iteration_context``)
    binds it to the whole iteration: cross-node comparison, review and merging.
    """
    s = f'{tool} --task {task["id"]}'
    if tree:
        s += ' --scope iteration'
        rows = '\n'.join(f'- 候选 `{c}`：冻结 commit `{commit or "未冻结"}`，worktree `{path or "未就绪"}`'
                         for c, commit, path in candidates or ())
        target = (tree['section'] + '\n\n## 各候选的代码\n' + rows +
                  '\n你只读这些 worktree 来定位代码，**不要修改 SN 代码**。每条新消息会附上最新的候选树摘要。')
        draft = ('提议必须**指定**挂在哪个已评测候选下（它的运行就是你引用的证据）：'
                 '`$S proposal draft --parent <候选 id> --file card.json`')
    elif node:
        s += f' --node {node["id"]}'
        target = node['section'] + ('\n你只读候选 worktree 来定位代码，**不要修改 SN 代码**。'
                                    f'本节点的 worktree：`{node["worktree"] or "未就绪"}`。')
        draft = f'`$S proposal draft --file card.json`（自动挂在 {node["label"]} 下；要从别的节点提议，请在那个节点另开分析 Agent）'
    elif candidates:
        rows = '\n'.join(f'- 候选 `{c}`：冻结 commit `{commit or "未冻结"}`，worktree `{path or "未就绪"}`'
                         for c, commit, path in candidates)
        target = ('本任务是一个迭代的载体，被测代码按候选区分（`$S node list` 看谱系与 Δ父／Δ根）：\n' + rows +
                  '\n你只读这些 worktree 来定位代码，**不要修改 SN 代码**。')
        draft = ('提议必须挂在一个**已评测**候选下（它的运行就是你引用的证据）：'
                 '`$S proposal draft --parent <候选 id> --file card.json`')
    else:
        target = f'被测 SN 的改动 worktree：`{worktree or "本任务未绑定"}`，你只读它来定位代码，**不要修改 SN 代码**。'
        draft = '`$S hypothesis-draft --file card.json`'
    docs = '\n'.join(f'- `{eval_root}/{path}`：{note}' for path, note in ANALYST_DOCS)
    return f'''# 你的角色：分析 Agent（Silicon Notebook 评测）

{"迭代" if node or tree else "任务"}：{task["title"]}{"　·　本会话分析节点 **" + node["label"] + "**" if node else "　·　本会话分析**整个迭代**" if tree else ""}
{"" if node or tree else "目标：" + (task["goal"] or "（未填写）") + chr(10)}评测仓库：`{eval_root}`。你的工作目录：`{cwd}`{"（这个 Agent 按旧方式建在 SN worktree 里；角色仍是分析，不要修改 SN 代码）" if worktree and cwd == worktree else ""}。
{target}

你的职责：{ITERATION_DUTIES if tree else ("读节点 " + node["label"] + " 的评测结果和案例证据（与它的父节点对照）" if node else "读评测结果和案例证据") + "，做失败归类，提出可证伪的假设卡草稿。"}你能看 gold；正因为能看 gold，你不写 SN 代码，改码由另一个看不到 gold 的实现 Agent 完成。

## 查询工具（只读、免费，随时可用）
```
S="{s}"
$S task                       # 本任务目标、worktree、运行、假设卡
$S runs                       # 本任务的运行（--all 看全部已登记运行）
$S cases <run_id> --needs-read   # 待读题；去掉 --needs-read 看全部
$S case <run_id> <case_id>    # 单题完整证据：问题、SN 答案、逐题分、gold、引用、按排名的检索块
$S execution-cases <execution_id>  # 整套 benchmark 的执行（几百个分区）：逐题分数表，低分在前；再用 case 看单题
$S flips <left_run> <right_run>  # 同题配对翻转表
$S hypotheses                 # 本任务假设卡及验证结果
$S docs                       # 下方文档索引
```
输出是 JSON。run 目录里还有 outputs.jsonl、planned.jsonl、model-events.jsonl，可直接读。

## 提交假设卡草稿（需要人批准后才会实施）
把卡片写成 JSON 文件，然后 {draft}。格式示例：
```json
{json.dumps(CARD_EXAMPLE, ensure_ascii=False, indent=1)}
```
- predictions 是逐题预测：expect 取 improve / unchanged / not_worse / worse；guard=true 表示守护题（不应变差）。验证时按翻转表逐条核对，不看均分。
- implementer_brief 是唯一会交给实现 Agent 的说明：写机制和改法，**不得包含题面、gold、case id、论文内容或分数**，提交前会做泄漏扫描。

{ITERATION_VERBS if candidates or node or tree else ""}{ITERATION_SCOPE_REQUESTS if tree else ""}## 可参考的评测文档
{docs}

## 规则
- 每个判断引用具体 run_id、case_id 或文件路径；区分实验事实、你的推断和未知。
- 低词面 F1 不等于语义错；gold 不是穷举；“没有记录到”不等于“没有发生”；导入失败或模型服务不可用归为基础设施，不当作 SN 质量问题。
- 样本小时把结论限定为“所选案例上的观察”。
- **不要运行 run_notebook_benchmarks.py 或任何会调用模型的评测命令**；需要新运行时写进假设卡，由人在界面确认题单与源码后运行。
- 不修改 SN worktree、不提交 git。
'''


def implementer_brief(task, card, *, worktree, tool, hypothesis_id, start_dirty, candidate_id=None):
    s = f'{tool} --task {task["id"]} --role implementer --hypothesis {hypothesis_id}'
    if candidate_id:
        s += f' --candidate {candidate_id}'
    from .clusters import referenced_cluster
    cluster_id = referenced_cluster(card)
    evidence = (f'\n- 你的簇：`{cluster_id}`。用 `{s} cluster show {cluster_id}` 读取 search 成员的题目、SN 答案、上下文块、阶段指标和官方主分（m01 等序号）；也可接收 cluster_case 上下文。没有 gold 答案、gold 证据文本或 case_id。\n' if cluster_id and candidate_id else '')
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
{evidence}

worktree 起点已有这些未提交改动，它们属于用户，不要覆盖或回退：
{dirty}

## 规则
- 只使用批准卡片与自己簇的脱敏 search 证据；不得寻找 gold 标注、validation／sealed 逐题或其他簇材料，不得直接读取评测原始文件（`var/`、`docs/failure-ledger.md`、`docs/hypotheses/`、`apps/sn-workbench/test-results/` 等）。改动仍须通过泄漏扫描与合同核对。
- 不写死任何题面、论文、数据集名或 case id；用合成数据写单元测试，覆盖受益和可能受害两种情况。
- 遵守本仓库 `AGENTS.md`；运行 SN 自己的测试；不提交 git，不启动评测运行。
- 完成后自查：`{s} leak-check`（只报告是否与考题重叠，不显示考题内容）。
{f"- 实现与测试都完成后，可以申请冻结和运行（只登记请求，人确认后服务器才执行）：`{s} node freeze {candidate_id} --reason '…'`，冻结确认后再 `{s} node run {candidate_id} --reason '…'`；`{s} requests` 看请求状态。" + chr(10) if candidate_id else ""}- 回复时说明：改了哪些文件、为什么、测试命令与结果、残余风险。
'''


def resolve_brief(repository, task_id, session_id, *, tool, run_views=None):
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
        candidates = None
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='iterations'").fetchone():
            candidates = [(r['id'], r['frozen_commit'], (json.loads(r['binding_json']) or {}).get('worktree_path')
                           if r['binding_json'] else None) for r in conn.execute(
                'SELECT c.id, c.frozen_commit, w.binding_json FROM iterations i JOIN candidates c '
                'ON c.iteration_id=i.id LEFT JOIN candidate_worktrees w ON w.candidate_id=c.id '
                'WHERE i.task_id=? ORDER BY c.created_at', (task_id,))]
        candidate_id = session['candidate_id'] if 'candidate_id' in session.keys() else None
        if session['role'] == 'implementer':
            from .hypothesis_worktrees import hypothesis_binding
            from .iterations import candidate_binding
            saved_binding = (candidate_binding(conn, candidate_id) if candidate_id
                             else hypothesis_binding(conn, task_id, session['hypothesis_id']))
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
                                 start_dirty=[d['path'] for d in start.get('dirty', [])], candidate_id=candidate_id)
        title = '实现 Agent 工作说明 · ' + card['title']
    else:
        node = node_context(repository, run_views, task_id, candidate_id) if candidate_id else None
        scoped = 'scope' in session.keys() and session['scope'] == 'iteration'
        tree = iteration_context(repository, run_views, task_id) if scoped else None
        text = analyst_brief(task, eval_root=str(Path(workspace['location'])), tool=tool, worktree=worktree, cwd=cwd,
                             candidates=candidates, node=node, tree=tree)
        title = '分析 Agent 工作说明 · ' + task['title'] + (' · 节点 ' + node['label'] if node else ' · 整个迭代' if tree else '')
    refs = {'task_id': task_id, 'session_id': session_id, 'role': session['role'] or 'analyst'}
    if candidate_id:
        refs['candidate_id'] = candidate_id
    return title, text, refs


def tree_labels(tree):
    """C0, A, B, A1, A1.1 …: the same names the workbench draws (apps/sn-workbench/src/tree/actions.ts)."""
    labels, children, ids = {}, {}, {c['id'] for c in tree['candidates']}
    for c in tree['candidates']:
        children.setdefault(c['parent_candidate_id'] or '', []).append(c)

    def visit(c, name):
        labels[c['id']] = name
        for i, child in enumerate(children.get(c['id'], [])):
            visit(child, chr(65 + i % 26) + (str(i // 26) if i >= 26 else '') if name == 'C0'
                  else f'{name}.{i + 1}' if name[-1].isdigit() else f'{name}{i + 1}')
    roots = [c for c in tree['candidates'] if not c['parent_candidate_id'] or c['parent_candidate_id'] not in ids]
    for i, root in enumerate(roots):
        visit(root, 'C0' if root['id'] == tree['root_candidate_id'] else f'L{i}')
    return labels


def _delta(entry):
    """One side of ``comparisons.summary`` as a sentence."""
    status = entry.get('status')
    if status == 'none':
        return None
    if status == 'same_as_parent':
        return '同父（父即根）'
    if status != 'admitted':
        return '不可用：' + str(entry.get('reason') or status)
    counts = entry['counts']
    delta = entry['mean_delta']
    line = (f'{entry["metric"]} Δ {delta:+.3f}' if delta is not None else f'{entry["metric"]} Δ —') + \
        f'，好 {counts["better"]} / 差 {counts["worse"]} / 平 {counts["same"]}'
    regressed = entry.get('regressed') or []
    if regressed:
        # A full benchmark can regress hundreds of cases; the comparison record keeps them all.
        line += '；下降的题：' + '、'.join(f'`{c}`' for c in regressed[:20]) + (
            f' 等 {len(regressed)} 题' if len(regressed) > 20 else '')
    return line


def _tree_reader(repository, run_views, task_id, candidate_id=None):
    """The iteration a Task backs, its labels and a per-candidate summary ``step(c, detail)``."""
    from .agent_tools import iteration_service
    from .comparisons import ComparisonService, summary
    if run_views is None:
        raise ValueError('Run views are needed to brief an analyst of an iteration')
    # Read-only: a brief is also resolved inside the message-sending write transaction.
    service = iteration_service(repository, run_views, initialize=False)
    comparisons = ComparisonService(repository, service, run_views, initialize=False)
    if candidate_id is not None:
        iteration_id = service.candidate(candidate_id)['iteration_id']
    else:
        found = [i for i in service.iterations() if i['task_id'] == task_id]
        if not found:
            raise ValueError('This Task backs no iteration')
        iteration_id = found[0]['id']
    from .splits import Policy
    policy = Policy(repository, task_id)
    tree = policy.project(service.iteration(iteration_id))
    if tree['task_id'] != task_id:
        raise ValueError('The candidate belongs to another Task')
    labels = tree_labels(tree)

    def runs(c):
        def score(run_id):
            official = run_views.run(run_id).get('official') or {}
            metric = official.get('primary_metric')
            value = (official.get('metrics') or {}).get(metric)
            return f'（{metric} {value:.3f}）' if isinstance(value, (int, float)) else ''

        def ids(execution_id):
            found = run_views.execution_runs(execution_id)
            # A whole-benchmark execution has hundreds of partitions: point at the table instead.
            if len(found) > 5:
                return f'execution `{execution_id}`（{len(found)} 个分区，逐题看 `$S execution-cases {execution_id}`）'
            return '、'.join(f'`{r.id}`{score(r.id)}' for r in found) or '无 run'
        reference = c.get('reference')
        if reference is not None:
            return f'参考运行（attempt {reference["attempt"]}）' + ids(reference['execution_id'])
        # A node without an admitted run: each attempt's state and runs, to diagnose why.
        tried = [f'attempt {a["attempt"]} {a["execution_state"] or "状态未知"}'
                 + (f' {ids(a["execution_id"])}' if a.get('execution_id') else '') for a in c['attempts']]
        return '没有通过准入的运行' + ('（' + '；'.join(tried) + '）' if tried else '')

    def step(c, detail):
        label, lines = labels[c['id']], []
        both = comparisons.for_candidate(c['id'], actor='workbench:brief', iteration=tree, store=False)
        parent, root = summary(policy.project(both['parent'])), summary(policy.project(both['root']))
        head = f'**{label}**（`{c["id"]}`）commit `{c["frozen_commit"] or "未冻结"}` · {runs(c)}'
        if c['parent_candidate_id'] is None:
            lines.append(head + ' · 起点')
        else:
            lines.append(head + f' · 父 {labels.get(c["parent_candidate_id"], "?")}')
        if c['proposal_id']:
            with repository.connect() as conn:
                row = conn.execute('SELECT card,verification FROM hypotheses WHERE id=? AND task_id=?',
                                   (c['proposal_id'], task_id)).fetchone()
            body = json.loads(row['card'])
            check = json.loads(row['verification']) if row['verification'] else None
            lines.append(f'提议「{body.get("title")}」：{body.get("mechanism")}' +
                         (f'；逐题预测 {check["held"]}/{check["decided"]} 兑现' if check else '；预测未核对'))
        elif c['parent_candidate_id'] is not None:
            lines.append('手工派生（没有提议）' + (f'：{c["note"]}' if c.get('note') else ''))
        if c.get('integrated_from'):
            lines.append('并入：' + '、'.join(labels.get(i.get('candidate_id'), '?') for i in c['integrated_from']))
        for name, side in (('Δ父', parent), ('Δ根', root)):
            text = _delta(side)
            if text and (detail or name == 'Δ父'):
                lines.append(f'{name}：{text}')
        decision = c.get('decision') or {}
        if c['parent_candidate_id'] is not None:
            lines.append('结论：' + ({'adopted': '采纳', 'rejected': '放弃', 'undecided': '未决'}.get(
                decision.get('decision'), '未下') + (f'（{decision["note"]}）' if decision.get('note') else '')))
        return policy.project(lines)
    return tree, labels, step


def node_context(repository, run_views, task_id, candidate_id):
    """The analyst's node: its reference runs, Δ父／Δ根, its proposal and the path from the root.

    Memory across rounds lives in these records (proposal, prediction check,
    comparison, decision), not in one ever-growing conversation.
    """
    tree, labels, step = _tree_reader(repository, run_views, task_id, candidate_id)
    by_id = {c['id']: c for c in tree['candidates']}
    path, cursor = [], by_id[candidate_id]
    while cursor is not None:
        path.insert(0, cursor)
        cursor = by_id.get(cursor['parent_candidate_id'])
    node = by_id[candidate_id]
    focus = step(node, True)
    section = f'\n## 本轮分析对象：{labels[candidate_id]}\n' + '\n'.join('- ' + line for line in focus)
    if len(path) > 1:
        section += '\n\n## 从根到 ' + labels[candidate_id] + ' 的路径（之前各轮的记录）\n' + '\n'.join(
            f'{i}. ' + '\n   '.join(step(c, False)) for i, c in enumerate(path[:-1], 1))
    section += ('\n\n其他分支不在这里；需要时 `$S node list`、`$S node show <候选>`。'
                '比较以父节点为准：Δ父 回答“这一步带来了什么”，Δ根 只作守护参考。')
    worktree = (node.get('worktree') or {}).get('path')
    return dict(id=candidate_id, label=labels[candidate_id], section=section, worktree=worktree)


def iteration_context(repository, run_views, task_id):
    """The whole tree for an iteration analyst: every candidate in tree order with Δ父／Δ根 and its record.

    Per-case rows stay out (an execution can hold hundreds of partitions); the
    Agent reads them with ``execution-cases``, ``compare`` and ``case``.
    """
    tree, labels, step = _tree_reader(repository, run_views, task_id)
    states = {'evaluated': '已评测', 'failed': '失败', 'running': '运行中', 'frozen': '已冻结', 'implementing': '实施中',
              'provisioning': '准备中', 'legacy': '遗留'}
    blocks = [f'{"  " * c["depth"]}- [{states.get(c["state"], c["state"])}] ' + '\n  '.join(
        ('  ' * c['depth'] + line if i else line) for i, line in enumerate(step(c, True)))
        for c in tree['candidates']]
    evaluated = sum(c['state'] == 'evaluated' for c in tree['candidates'])
    section = (f'\n## 候选树（{len(tree["candidates"])} 个候选，{evaluated} 个已评测；树序：父在前、兄弟按派生时间）\n'
               + '\n'.join(blocks) +
               '\n\nΔ父 回答“这一步带来了什么”，Δ根 是从起点累计；均值变好不等于每题不退化。'
               '任意两节点用 `$S compare <候选> <参考候选>`（非父／根为临时比较）。')
    return dict(id=tree['id'], section=section, labels=labels)


def iteration_tree_context(repository, run_views, task_id):
    """The ``iteration_tree`` message context: a fresh tree summary for an iteration analyst."""
    tree = iteration_context(repository, run_views, task_id)
    return '迭代候选树（最新）', tree['section'].strip(), {'task_id': task_id, 'iteration_id': tree['id']}
