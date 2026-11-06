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
派生、冻结、运行、采纳／放弃、新建迭代**只能申请**：`$S node derive <父> --proposal <已批准提议> --reason '…'`、`$S node run <候选> --reason '…'`、`$S node adopt|reject <候选> --reason '…'`。命令先登记请求；冻结预算内符合自动执行条件的请求由服务器自动确认执行，其他情况需人在工作台确认。不要用 `scripts/sn-workbench`，那是人确认请求用的。

'''


ITERATION_DUTIES = (
    '跨节点看整棵候选树，单个节点的分析交给绑定该节点的分析 Agent：'
    '（1）横向比较——兄弟或任意两节点谁好、各自好在哪些题差在哪些题，哪些题所有候选都答错（顽固题）；'
    '（2）假设复盘——各提议的逐题预测兑现了多少，哪类改动有效、哪类无效，下一步往哪投入；'
    '（3）合并建议——改动互补（好转的题少重叠）时建议合并派生；'
    '（4）全树守护——同一批题反复来回翻转（过拟合信号）、采纳路径上相对根下降的题。'
    '结论落到可证伪的假设卡草稿或合并派生申请。')

# lcd-v2: adoption is screen + fresh confirmation; same-round merging is gone.
ITERATION_DUTIES_V2 = (
    '跨节点看整棵候选树，单个节点的分析交给绑定该节点的分析 Agent：'
    '（1）横向比较——兄弟或任意两节点谁好、各自好在哪些题差在哪些题，哪些题所有候选都答错（顽固题）；'
    '（2）假设复盘——各提议的筛选与确认结果、验证池 Δ 与新鲜批次 Δ 的缩水、归因标签（支持／反驳／证据不足），哪类改动有效、下一步往哪投入；'
    '（3）rebase 复盘——父节点前进后哪些排队候选 rebase 冲突被丢弃，是否值得在新父节点上重新提出；'
    '（4）全树守护——同一批题反复来回翻转（过拟合信号）、采纳路径上相对根下降的题、保护指标的漂移。'
    '结论落到可证伪的假设卡草稿。')

ITERATION_SCOPE_REQUESTS = (
    '作为迭代分析 Agent，你**只申请派生**：从某个已评测候选派生（`--proposal` 指定已批准提议），'
    '或合并派生 `$S node derive <主父候选> --integrate <被并入候选> --method merge|cherry-pick|rewrite --reason \'…\'`'
    '（合并后的候选必须自己重跑，不用分差相加估计）。冻结、运行、采纳／放弃属于具体候选，不要申请。\n\n')


def _rule_id(protocol):
    return ((protocol or {}).get('gate_rule') or {}).get('id', 'lcd-v1')


# A complete lcd-v2 mechanism, parallel to the retrieval_gap cluster example below.
MECHANISM_EXAMPLE = {
    'slice_predicate': {'kind': 'range', 'field': 'deterministic.citation_valid_count', 'max': 0},
    'manipulation': {'direction': 'decrease', 'threshold': 0.3},
    'slice_effect': {'direction': 'positive', 'magnitude': [0.02, 0.15]},
}


def _mechanism_instructions(protocol):
    import inspect
    from .clusters import FIELDS
    from .gate import TRAJECTORY_FIELDS, attribute
    rule = protocol['gate_rule']
    allowed = ', '.join(sorted(TRAJECTORY_FIELDS))
    excluded = ', '.join(sorted(FIELDS - TRAJECTORY_FIELDS) + ['metrics.<official_primary_metric>', 'case_id'])
    n_min = inspect.signature(attribute).parameters['n_min'].default
    guards = '、'.join(s['key'] for s in protocol.get('safeguards') or []) or '预注册保护指标'
    example = json.dumps(dict(cluster_id='retrieval_gap', contract=MECHANISM_EXAMPLE), ensure_ascii=False)
    return f"""卡片顶层写 `"cluster_id"`，并增加机制假设 `"contract"`，它**恰好**有三个键（不能带 cluster_id、predicted_flips、guards、tau，那是 lcd-v1 合同）：
```json
{example}
```
- `slice_predicate`：切片谓词 P，算子与簇签名相同（range／regex／all_of／any_of／not），但在**父版本轨迹**上求值：父版本任一 attempt 命中即入切片，成员由父版本固定，候选的输出不改变成员。归因时切片在**新鲜批次**上重新求值（成员是新鲜批次里父版本命中 P 的题），所以 P 要描述一类行为，而不是圈出 search 里的某几题。字段白名单比簇签名**更窄**，只能用：{allowed}。不能用：{excluded}——它们来自分数、gold 或题目身份；上面簇签名示例里的 deterministic.evidence_hit 就不能进 P。retrieval_context.*.text 仍只支持 regex。
- `manipulation`：`{{"feature"?: 谓词, "direction": "decrease"|"increase", "threshold": 0–1}}`，预测改动后切片成员上 feature 的命中率至少下降（或上升）threshold。省略 feature 时就用 slice_predicate 本身（上例：改后这些题不再零引用的比例至少 0.3）；feature 也只能用上面的白名单字段。
- `slice_effect`：`{{"direction": "positive", "magnitude"?: [lo, hi]}}`，预测切片内配对分数差为正；magnitude 可省略，给出时 lo ≤ hi。
- 起草时严格校验：多余键、类型错、字段越出白名单都判"合同无效"，卡片进不了实现。

**合同不决定采纳。** 采纳只看配对净效用：先在验证池上筛选（Δ − {rule['z_screen']}·SE > 0），通过后在一个从未用过的新鲜批次上确认（Δ − {rule['z_confirm']}·SE > 0），且保护指标（{guards}）不越过预注册边界；Δ 是逐题原始分（不按阈值二值化）的候选减父版本均值，SE 按 paper 聚类。合同只给确认后的改动打归因标签：支持／反驳／证据不足，标签不影响任何判定。
所以**不要为了好兑现把切片写窄**：切片成员少于 {n_min} 题时标签只能是"证据不足"；切片外变差会抵消切片内的收益，净效用过不了线就不会被采纳。切片应覆盖真正承载这个机制的全部题，让 manipulation 描述可机械测量的行为变化。
"""


def _lcd_analyst_instructions(protocol=None):
    from .clusters import FIELDS
    from .run_views import PRIMARY_METRIC
    v2 = _rule_id(protocol) == 'lcd-v2'
    primary_fields = ', '.join(f'{suite}: metrics.{metric}' for suite, metric in sorted(PRIMARY_METRIC.items()))
    fields = ', '.join(sorted(FIELDS | {'metrics.<official_primary_metric>', 'retrieval_context.*.text'}))
    contract = (_mechanism_instructions(protocol) if v2 else """卡片增加 `"contract":{"cluster_id":"retrieval_gap","predicted_flips":["<search 成员标识>"],"guards":[],"tau":0.5}`。predicted_flips 非空且为冻结成员子集，与 guards 不重叠；两列表各自不重复且最多 200 项，tau 可省略或取 [0,1]。顶层 cluster_id 若存在必须与合同一致。标识仅留分析侧，不得复制进 implementer_brief。
""")
    ranking = ("""排序按可提升空间 |C|·(1 − 父版本在簇内的原始主分均值)：|C| 是簇的 search 成员数，均值取父版本各 attempt 的官方主分原值（出错或缺失按 0）。不估计 m、v 或先验 π，服务器用同一口径选前 b 个有合法卡的未解决簇。只为未解决簇起草合法卡；card_approval=auto 仍对 title、component、mechanism、implementer_brief、possible_harm、change_scope 做泄漏扫描（题面、gold 8 词片段、注册 case id），并做机制合同校验；manual 等待批准。
""" if v2 else """排序公式 |C|·(1−q)·max(m, v, π)，π 缺省 0.3；m 或 v 不可测时保留 null，不当作 0；对可测维度与 π 取最大值，两者都不可测时注明退化为 |C|·(1−q)·π 及原因。只为未解决簇起草合法卡；card_approval=auto 仍做泄漏扫描与合同校验，manual 等待批准。
""")
    signatures = ("missing_signature 描述缺失，violation_signature 描述违规；lcd-v2 下它们只作描述、不参与排序，可省略。"
                  if v2 else "missing_signature 描述缺失，violation_signature 描述违规；省略时采用服务默认值，建议显式给出。")
    return ("## LCD 机械簇与机制假设（lcd-v2）\n" if v2 else "## LCD 机械簇与合同\n") + """长时间执行会在你的 turn 结束后继续。结果就绪但缺少下一步产物时，轮次服务会发送后续消息；请继续完成卡片草案。
先读 search 证据。本轮基准候选、max_clusters 与 b 由服务器消息指定；最多提 max_clusters 个簇，为排名前 b 的簇各起草一张带 contract 的卡。
**一次调用读全部 search 证据**：`$S evidence --parent <基准候选>`（节点会话省略 --parent 即读本节点）。输出 JSON lines：首行给出执行顺序和 attempt_columns（列名就是签名字段名），之后每行一题：case_id、question、各 attempt 的列值、官方主分均值 mean、failed（任一 attempt 出错、缺失或低于 success_threshold）。`--failed-only` 只留失败题，`--fields a,b` 只取需要的列。不要逐题调用 case 拼表，也不要用 `runs --all` 加脚本汇总；只在需要 gold、引用或检索块原文时，对少数代表题用 `$S case <run_id> <case_id>`。下方评测文档按需查阅，不要开局通读。
写簇前先试算：`$S cluster preview --file cluster.json` 与 propose 走同一套校验和扫描，返回成员、q、m、v 及 cluster_id 是否已被占用，不写入、不冻结，可反复调整签名。满意后用 `$S cluster propose --file cluster.json` 写入簇。节点会话自动绑定来源；迭代会话须在 JSON 加 candidate_id，或命令加 --parent <已评测候选>。
```json
{"cluster_id":"retrieval_gap","description":"检索证据缺失机制","signature":{"kind":"range","field":"deterministic.evidence_hit","min":0,"max":0},"missing_signature":{"kind":"range","field":"stage_metrics.gold_evidence_recall.value","min":0,"max":0},"violation_signature":{"kind":"range","field":"deterministic.citation_invalid_count","min":1}}
```
JSON 算子：`{"kind":"regex","field":"response.answer","pattern":"表达式"}`；`{"kind":"range","field":"usage.total_tokens","min":0,"max":100}`（至少一个有限数值边界，包含边界）；`{"all_of":[签名,...]}`、`{"any_of":[签名,...]}`、`{"not":签名}`。组合含 1–32 子签名，嵌套最多 8 层；regex 长度 1–512。retrieval_context.*.text 仅支持 regex，任一块匹配即可。
精确字段白名单：""" + fields + """。metrics.<official_primary_metric> 替换成当前套件实际官方主指标字段，不能用其他 metrics 字段。当前套件对应：""" + primary_fields + """。
""" + signatures + """签名、归因签名及成员写入即冻结，不得改写；新机制另提新簇。用 `$S cluster list` / `$S cluster show <cluster_id>` 查看。
""" + contract + ranking + "\n"


def analyst_brief(task, *, eval_root, tool, worktree, cwd, candidates=None, node=None, tree=None, protocol=None):
    """``candidates`` are (id, frozen_commit, worktree) rows when the Task backs an iteration.

    ``node`` (from ``node_context``) binds the analyst to one evaluated candidate:
    the brief then carries that node, its path from the root, and drafts default
    to it, instead of listing the whole tree. ``tree`` (from ``iteration_context``)
    binds it to the whole iteration: cross-node comparison, review and merging.
    ``protocol`` is the iteration protocol; its gate rule picks the contract form.
    """
    v2 = _rule_id(protocol) == 'lcd-v2'
    example = dict(CARD_EXAMPLE, predictions=[], cluster_id='retrieval_gap', contract=MECHANISM_EXAMPLE,
                   falsification='零引用回答的比例没有下降，或新鲜批次上净效用不为正') if v2 else CARD_EXAMPLE
    predictions = ('- predictions 可写 `[]`。lcd-v2 下逐题预测只作披露，任何判定都不读它，写得多或准都不影响采纳；不要为它去挑 case id。'
                   if v2 else '- predictions 是逐题预测：expect 取 improve / unchanged / not_worse / worse；guard=true 表示守护题（不应变差）。验证时按翻转表逐条核对，不看均分。')
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

你的职责：{(ITERATION_DUTIES_V2 if v2 else ITERATION_DUTIES) if tree else ("读节点 " + node["label"] + " 的评测结果和案例证据（与它的父节点对照）" if node else "读评测结果和案例证据") + "，做失败归类，提出可证伪的假设卡草稿。"}你能看 gold；正因为能看 gold，你不写 SN 代码，改码由另一个看不到 gold 的实现 Agent 完成。

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

{_lcd_analyst_instructions(protocol) if candidates or node or tree else ""}## 提交假设卡草稿（按批准策略实施）
把卡片写成 JSON 文件，然后 {draft}。格式示例：
```json
{json.dumps(example, ensure_ascii=False, indent=1)}
```
{predictions}
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


def implementer_brief(task, card, *, worktree, tool, hypothesis_id, start_dirty, candidate_id=None, k_base=2, protocol=None):
    s = f'{tool} --task {task["id"]} --role implementer --hypothesis {hypothesis_id}'
    v2 = _rule_id(protocol) == 'lcd-v2'
    checks = ('改动仍须通过泄漏扫描；是否采纳只看验证池筛选与新鲜批次确认上的整体净效用。' if v2
              else '改动仍须通过泄漏扫描与合同核对。')
    if candidate_id:
        s += f' --candidate {candidate_id}'
    from .clusters import referenced_cluster
    cluster_id = referenced_cluster(card)
    evidence = (f'\n- 你的簇：`{cluster_id}`。用 `{s} cluster show {cluster_id}` 读取 search 成员的题目、SN 答案、上下文块、阶段指标和官方主分（m01 等序号）；也可接收 cluster_case 上下文。没有 gold 答案、gold 证据文本或 case_id。\n' if cluster_id and candidate_id else '')
    integration = card.get('component') == 'integration'
    sequence = ''
    if candidate_id:
        cluster_steps = (f" → `{s} node cluster-run {candidate_id} --reason '…'` → 等待并读取 `{s} cluster result {cluster_id} --wait 1200`"
                         if cluster_id and not integration else '')
        sequence = (f"\n## LCD 执行顺序\n长时间执行会在你的 turn 结束后继续；结果就绪时服务会发后续消息，请读取结果并继续后续步骤。\n实现 → 只跑与改动相关的 SN 测试（禁止全量套件） → `{s} leak-check`"
                    f" → `{s} node freeze {candidate_id} --reason '…'`{cluster_steps}"
                    f" → `{s} node run {candidate_id} --attempts {k_base} --reason '…'`。\n"
                    "每步用 requests 确认前一步成功，冻结成功后才能评测。attempts 取冻结协议 k_base。"
                    "命令登记请求；冻结预算内符合自动执行条件时服务器自动确认执行，其他情况查看 requests 等待确认，禁止绕过预算。"
                    "执行由服务器派发，不要自行设置模型凭据或读取服务器进程环境。"
                    + ("cluster-run 之后用 `cluster result … --wait 1200` 在一次调用里等待：状态落定立即返回，最多等 1200 秒，"
                       "返回当前 state 和 waited_seconds；仍是 pending 就再调用一次同样的命令。"
                       "不要写 shell sleep／轮询循环。"
                       "cluster result 的 state：pending 仍在派发或执行；failed 是候选自身执行失败；"
                       "infrastructure_error 是服务器环境问题，不是你的候选的问题：停止，报告后等待，不要重试或改代码。"
                       + ("cluster result 只是簇内诊断，不决定采纳；不要为了簇内翻转牺牲其他题。" if v2 else '')
                       if cluster_steps else '') + "\n")
    if integration:
        sequence = ("\n这是 integration 合并候选：只合并服务器指定来源、解决同一文件／提示片段冲突，不加新改动；"
                    "无单簇归属，跳过 cluster-run 和 cluster result。\n" + sequence)
    scope = '\n'.join(f'- `{p}`' for p in card.get('change_scope') or []) or '- （卡片未限定，改动尽量集中在上述组件）'
    dirty = '\n'.join(f'- `{p}`' for p in start_dirty) or '- （无）'
    return f'''# 你的角色：实现 Agent（Silicon Notebook）

你的工作目录是 SN worktree `{worktree}`。你要实现一张已经批准的假设卡。

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
- 只使用批准卡片与自己簇的脱敏 search 证据；不得寻找 gold 标注、validation／sealed 逐题或其他簇材料，不得直接读取评测原始文件（`var/`、`docs/failure-ledger.md`、`docs/hypotheses/`、`apps/sn-workbench/test-results/` 等）。{checks}
- 不写死任何题面、论文、数据集名或 case id；用合成数据写单元测试，覆盖受益和可能受害两种情况。
- 遵守本仓库 `AGENTS.md`；只跑与改动相关的 SN 测试，禁止全量套件；不提交 git，不直接启动评测运行。
- 完成后自查：`{s} leak-check`（只报告是否与考题重叠，不显示考题内容）。
{sequence}- 回复时说明：改了哪些文件、为什么、测试命令与结果、残余风险。
'''


# ---------------------------------------------------------------- method-v3
# Agents created by an automatic method-v3 run (creation intent brief='method-v3')
# get these instead of the workbench briefs: the run coordinator derives, freezes,
# evaluates and decides, so the workbench verbs above would only be refused
# (auto_runs.ANALYST_ALLOWED / IMPLEMENTER_ALLOWED list exactly what these allow).
V3_ANALYST_COMMANDS = ('docs', 'leak-check', 'proposal draft')
V3_IMPLEMENTER_COMMANDS = ('card', 'leak-check')
V4_ANALYST_COMMANDS = (*V3_ANALYST_COMMANDS, 'source')


def v3_analyst_brief(task, *, tool, candidate_id):
    s = f'{tool} --task {task["id"]} --role analyst --node {candidate_id}'
    return f'''# Method-v3 analyst · node {candidate_id}

You are the analyst of an automatic method-v3 run on iteration task "{task["title"]}", bound to candidate `{candidate_id}` (the round's parent). Each round's message names one evidence file (absolute path and sha256): diagnostic views of the parent's search cases, as c1, c2, ... marked [failure] or [control], and the outcome labels of hypotheses already tried under this parent. That file is your only input.

Commands (`$S` = `{s}`):
- `$S docs`: evaluation documents
- `$S leak-check`: benchmark-text leak scan
- `$S proposal draft --parent {candidate_id} --file card.json`: save one card under this node (it is leak-scanned and validated)
Nothing else. Never request runs, freezes, derivations, adoptions or other decisions; the coordinator does all of that.

Card (lcd-v2): JSON with title, component, mechanism, implementer_brief, falsification, predictions (may be []), optional change_scope, and contract = {{slice_predicate, manipulation: {{direction: decrease|increase, threshold in [0, 1]}}, slice_effect: {{direction: positive}}}}. slice_predicate is a closed signature (kind regex or range on one field; all_of / any_of / not) over behaviour fields or trace.* step features such as trace.n_empty_retrievals; never case ids, scores or gold. One card per distinct failure mode.

Rules: you see no gold answers, scores, validation or fresh cases and must not look for them. Do not read files under var/ except the evidence file named in the message; do not open other iterations or run directories; do not edit SN code.
'''


def v3_implementer_brief(task, *, tool, hypothesis_id, candidate_id, worktree):
    s = f'{tool} --task {task["id"]} --role implementer --hypothesis {hypothesis_id} --candidate {candidate_id}'
    return f'''# Method-v3 implementer · candidate {candidate_id}

You implement one approved card (hypothesis `{hypothesis_id}`) in candidate `{candidate_id}`) of an automatic method-v3 run. Your worktree: `{worktree or "not ready"}` — this is the SN product repo (backend/, frontend/, etc.). Edit ONLY files inside this worktree path; never read or write anything outside it. If the card names a path that does not exist here (e.g. src/rag_eval/), find the equivalent SN product file (e.g. the answerer prompt in backend/app/services/) and change that instead.

Commands (`$S` = `{s}`):
- `$S card`: the approved card (what to change and why)
- `$S leak-check`: benchmark-text leak scan of your change
Nothing else.

Order: implement the card -> run only the SN tests relevant to your change -> leak-check. Leave your changes uncommitted. Never commit, freeze, run or request evaluations, or request anything else: the coordinator freezes and evaluates the candidate after you finish. Use only the card's implementer material and, in a repair (a second attempt after the coordinator's smoke check), the one feedback file its message names: read no other file under var/; never look for gold answers, scores, validation or benchmark cases.

When you finish, reply with the files you changed and why, the test commands and their results, and remaining risks.
'''


V4_HISTORY_LABELS_TEXT = ('preflight_skipped (implementer reported a source/local-check reason to stop before evaluation; '
                          'not an independently proved rejection), smoke_fail (did not run, or the predicted behaviour did not change on the smoke cases), '
                          'minibatch_fail (clearly worse on the first validation slice), below_threshold (validation '
                          'gain too small for its mechanism evidence), qualified_not_adopted (good enough, another '
                          'card was better this round: you may propose it again on the new parent), remeasure_failed '
                          '(qualified once, but a second measurement did not confirm the gain), adopted, '
                          'safeguard_breached (with the safeguard names), pruned (adopted, then removed by ablation), '
                          'run_failed (the candidate failed to run on the validation cases), implementation_failed (with '
                          'a class: timeout, empty diff, workspace error or malformed preflight report; the hypothesis '
                          'is untested), in_progress (still being '
                          'implemented or measured: do not propose it again); entries from an earlier parent are marked')


def v4_analyst_brief(task, *, tool, candidate_id):
    s = f'{tool} --task {task["id"]} --role analyst --node {candidate_id}'
    return f'''# Method-v4 analyst · node {candidate_id}

You are the analyst of an automatic method-v4 run on iteration task "{task["title"]}", bound to candidate `{candidate_id}` (the round's parent). Each round's message names one evidence file (absolute path and sha256): diagnostic views of the parent's search cases, as c1, c2, ... marked [failure] or [control], search scores when supplied, and outcome labels of hypotheses tried under this or earlier parents (earlier parents are marked). Read that file and the preparation-source and adopted-change inventory files it explicitly names. The adopted inventory preserves the current lineage beyond the bounded recent history. You may also inspect the frozen parent product source through the source command below.

Commands (`$S` = `{s}`):
- `$S source --path <path> [--query literal] [--start N --lines N]`: bounded frozen-parent product source/tests (one file; 1-based lines, default 80, maximum 200); backend/app/, backend/tests/, frontend/src/, frontend/tests/ or frontend/__tests__/ source files only. Query is literal text, not regex. Never read the dirty checkout as parent evidence.
- `$S docs`: evaluation documents
- `$S leak-check`: benchmark-text leak scan
- `$S proposal draft --parent {candidate_id} --file card.json`: save one card under this node (it is leak-scanned and validated)
Nothing else. Never request runs, freezes, derivations, adoptions or other decisions; the coordinator does all of that.

Card (lcd-v2): JSON with title, component, mechanism, implementer_brief, falsification, predictions (may be []), direction (a short name of the idea the card pursues, e.g. "query-rewrite-empty-retrieval"; reuse the same direction name for another attempt at the same idea), optional change_scope and search_refs (opaque references such as ["c1", "c3"] from this exact round evidence), and contract = {{slice_predicate, manipulation: {{direction: decrease|increase, threshold in [0, 1]}}, slice_effect: {{direction: positive}}}}. The slice_predicate is the card's behaviour expectation: a closed signature (kind regex or range on one field; all_of / any_of / not) over behaviour fields or trace.* step features such as trace.n_empty_retrievals; never case ids, scores or gold. The coordinator checks it mechanically on validation pairs (and on smoke cases when the optional behaviour smoke gate is enabled) and an independent reviewer judges whether the change took effect: a change whose mechanism is confirmed is adopted with a smaller validation gain, so make the expectation specific. One card per distinct failure mode; draft the most promising card first (cards are implemented in the order you draft them).

Aim to produce useful candidates this round: prioritize small, reversible answer-path changes supported by the supplied search evidence. A concrete failure observation, a plausible product mechanism, and a bounded change can justify an experiment with limited uncertainty. Full causal proof is not a prerequisite for drafting. Do not invent cards to fill a quota; if no justified candidate remains, explain the specific missing evidence or refutation.

Before drafting: distinguish observed search evidence, the code fact in a named frozen path/line, and the causal hypothesis. Use 1–2 cheap checks focused on the assumption most likely to invalidate the change, including whether current code already meets the proposed guarantee. Broaden inspection only when a concrete finding requires it; do not exhaustively investigate unrelated paths. In falsification, name the cheapest discriminating source check, synthetic test or local probe and the result that would refute the hypothesis; do not require an expensive suite by default. State any remaining uncertainty and the expected observable behaviour change. Use search_refs to cite only the cases actually supporting this card; do not invent references or copy raw case IDs. Missing references on historical cards mean legacy context, not verified exact citations.

Cost: the preparation cache source list is named in the evidence file. A ::symbol entry covers only that definition or assignment, not the entire file. Covered changes may require a new preparation snapshot; matching snapshots can be reused across evaluations. Answer-path prompts outside those symbols do not by themselves invalidate preparation. Prefer answer-path changes when suitable; identify required preparation changes in implementer_brief.

Outcome labels you may see: {V4_HISTORY_LABELS_TEXT}; with a mechanism tier (confirmed / unconfirmed) when the candidate was fully measured, and possibly a human-review flag (a reviewer reported possible score gaming; this never rejects a candidate by itself).

Directions: the evidence summarises history per direction. When a direction already has two or more attempts that did not qualify (below_threshold, minibatch_fail, smoke_fail, safeguard_breached, remeasure_failed), choose a different direction, or state in the card's mechanism what is essentially different from those attempts. Directions marked "inferred" were derived from older card titles.

Use history to choose the next experiment. Do not repeat rejected or already-satisfied changes under a new title, or duplicate adopted/in-progress work. A materially revised hypothesis may be proposed when supplied evidence or a frozen-source fact explains what changed and how it addresses the earlier failure. An inconclusive preflight is not a measured rejection; distinguish uncertainty and infrastructure failure from evidence against the mechanism. When no candidate was adopted, inspect the stated reason and pursue another supported mechanism or a justified revision; nonadoption alone does not prove there is no useful change.

Rules: you may use the search scores supplied in this evidence file to diagnose search failures. Never seek gold answers, benchmark validation or test cases, or their scores; never put case ids, scores or gold into card predicates. Propose general product improvements, never benchmark-specific branches, memorized answers, score-targeting shortcuts or changes to evaluation rules. Do not read files under var/ except the evidence file named in the message and its explicitly named preparation-source/adopted-change files; do not open other iterations or run directories; do not edit SN code. The source command enforces a narrow read boundary, not a filesystem sandbox.
'''


def v4_implementer_brief(task, *, tool, hypothesis_id, candidate_id, worktree):
    s = f'{tool} --task {task["id"]} --role implementer --hypothesis {hypothesis_id} --candidate {candidate_id}'
    return f'''# Method-v4 implementer · candidate {candidate_id}

You implement one approved card (hypothesis `{hypothesis_id}`) in candidate `{candidate_id}` of an automatic method-v4 run. Your worktree: `{worktree or "not ready"}` — this is the SN product repo (backend/, frontend/, etc.). Edit product files ONLY inside this worktree path; the sole outside-worktree write exception is the coordinator-named preflight report file. If the card names a path that does not exist here (e.g. src/rag_eval/), find the equivalent SN product file (e.g. the answerer prompt in backend/app/services/) and change that instead.

Commands (`$S` = `{s}`):
- `$S card`: the approved card (what to change and why)
- `$S leak-check`: benchmark-text leak scan of your change
Nothing else.

Order: read the approved card and the cited search evidence file named in the message -> inspect relevant product source and relevant tests -> perform 1–2 cheap checks of the assumption most likely to invalidate the change. Separate observations from the causal claim. A source check alone can be enough; use a small synthetic test or local probe when it resolves an uncertainty. Broaden preflight only for a concrete unresolved risk. No costly suite, exhaustive causal investigation or extra Agent is required.

If the hypothesis is refuted or already satisfied, stop before editing product code and report decision=skip with the actual checks and reason. If checks remain inconclusive but supplied search evidence and inspected source justify a small, reversible change and no check refutes it, prefer implement/inconclusive with the uncertainty explicitly recorded. Do not skip solely because causal attribution remains uncertain or a local check cannot predict model behaviour. Still skip when no concrete change is justified; never invent support. Implement the smallest change that tests the approved hypothesis, run checks proportionate to its risk (only relevant SN tests when needed), and leak-check. Leave changes uncommitted. Never commit, freeze, run or request evaluations: the coordinator handles these after an implement decision.

Write exactly one JSON object to the preflight report path supplied in the dispatch message, using its candidate_id, hypothesis_id and parent_candidate_id identity values:
{{"candidate_id": "<from message>", "hypothesis_id": "<from message>", "parent_candidate_id": "<from message>", "decision": "implement|skip", "reason": "supported|refuted|already_satisfied|inconclusive", "summary": "short conclusion", "checks": [{{"kind": "source|test|probe", "target": "path:line or command", "observation": "what was actually observed"}}]}}
Respect the report byte, text and check-count limits in the dispatch message. Select one value for each enum, not the pipe-separated text. Both implement and skip require 1–32 concrete checks actually attempted. Use implement with reason supported or inconclusive; use skip with reason refuted, already_satisfied or inconclusive. Missing or invalid reports are failures, never inferred skips. Reports are implementer claims, not independent proof. A repair message names the same required report and current identities; refresh it after the repair checks.

Cost: the message names the preparation cache source list. A ::symbol entry covers only that definition or assignment, not the entire file. Covered changes may require a new snapshot, which later evaluations can reuse. Prefer answer-path changes when suitable.

For evidence, read only the files the message names (the analyst's search evidence, the preparation source list and, in a repair, the feedback file); read no other file under var/. Search scores may appear in the named search evidence. Never seek gold answers, benchmark validation or test cases, or their scores. Implement general product improvements, never benchmark-specific branches, memorized answers, score-targeting shortcuts or changes to evaluation rules. You may inspect product source and relevant tests inside your worktree.

When you finish, reply with the files you changed and why, the test commands and their results, and remaining risks.
'''


def v4_reviewer_brief(task, *, candidate_id):
    return f'''# Method-v4 reviewer · candidate {candidate_id}

You are the independent reviewer of one candidate change of an automatic method-v4 run on iteration task "{task["title"]}". You do not propose or implement changes. Each message names one evidence file (absolute path and sha256) and one verdict file path. The evidence file holds the card (failure mode and change hypothesis), the change's diff, and several anonymous pairs of trajectory views labelled p1, p2, ...; in each pair the two sides are labelled A and B in random order.

For every pair answer three questions: did the change described by the card take effect in this pair (which side shows it), does the pair show a side effect, and does it show score gaming (e.g. a shorter answer with the same content, more refusals, answer text shaped to a metric rather than to the question). Some pairs may show no difference at all; say so. Identical structured fields are named once; changed fields show both A and B. A [truncated] value is incomplete evidence: do not infer equality from matching prefixes. If the visible evidence cannot establish the mechanism, use effect=false and changed_side=null and explain the missing evidence in note.

Write exactly one JSON object to the verdict file named in the message (nothing else in it), then stop:
{{"pairs": [{{"pair": "p1", "effect": true|false, "changed_side": "A"|"B"|null, "side_effect": true|false, "gaming": true|false, "note": "one or two sentences"}}, ...]}}

Rules: read only the evidence file named in the message and write only the verdict file; run no other command, open no other file or directory, never look for gold answers, scores or case identities. Each message is a new, independent review.
'''


def v41_calibrator_brief(task, *, candidate_id, tool, run_id):
    return f'''# Method-v4.1 calibration advisor
You advise before the run of task {task['title']!r}; you never implement or launch evaluations.
Read only the named advisor-evidence.json: aggregate calibration numbers and D case views.
P questions, answers and trajectories, validation cases and endpoint information are forbidden.
All commands must use: {tool} --task {task['id']} --role calibrator --node {candidate_id} --run-id {run_id}
Allowed read-only commands: calibration show; simulate --params JSON; history;
source --path PATH [--query TEXT] [--start N --lines N]. No other sn-eval commands.
Write only the named proposal.agent.json. Suggest each value with its simulation sim_id and
full sim_params, a short reason and confidence high/medium/low. Do not change user inputs,
calibration_k, safeguard tolerances, method constants or user-edited values.
Also provide failure_modes (short descriptions based on D) and a message explaining budget,
forecast intervals and risks. Mark assumed quantities clearly. Finish within ten minutes.
'''


def _creation_brief(conn, task_id, agent_id):
    """The brief selector recorded with the Agent's creation intent (None: workbench brief)."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='agent_creation_intents'").fetchone():
        return None
    row = conn.execute('SELECT payload FROM agent_creation_intents WHERE task_id=? AND agent_id=? '
                       'ORDER BY prepared_at LIMIT 1', (task_id, agent_id)).fetchone()
    return json.loads(row['payload']).get('brief') if row else None


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
        from .agent_creations import BRIEF_V3, BRIEF_V4
        selected = _creation_brief(conn, task_id, session['agent_id'])
        if selected == BRIEF_V4:
            candidate_id = session['candidate_id'] if 'candidate_id' in session.keys() else None
            role = session['role'] or 'analyst'
            refs = {'task_id': task_id, 'session_id': session_id, 'role': role, 'brief': BRIEF_V4,
                    **({'candidate_id': candidate_id} if candidate_id else {})}
            if role == 'calibrator':
                owner = conn.execute('SELECT a.run_id FROM auto_runs a JOIN iterations i ON i.id=a.iteration_id '
                                     'WHERE i.task_id=?', (task_id,)).fetchone()
                if not owner:
                    raise ValueError('calibrator has no automatic run binding')
                return ('Method-v4.1 calibrator brief', v41_calibrator_brief(task, candidate_id=candidate_id,
                        tool=tool, run_id=owner['run_id']), refs)
            if role == 'reviewer':
                return ('Method-v4 reviewer brief · ' + str(candidate_id), v4_reviewer_brief(task, candidate_id=candidate_id), refs)
            if role == 'implementer':
                return ('Method-v4 implementer brief · ' + str(candidate_id),
                        v4_implementer_brief(task, tool=tool, hypothesis_id=session['hypothesis_id'],
                                             candidate_id=candidate_id, worktree=cwd), refs)
            return ('Method-v4 analyst brief · ' + str(candidate_id), v4_analyst_brief(task, tool=tool, candidate_id=candidate_id), refs)
        if selected == BRIEF_V3:
            candidate_id = session['candidate_id'] if 'candidate_id' in session.keys() else None
            refs = {'task_id': task_id, 'session_id': session_id, 'role': session['role'] or 'analyst',
                    'brief': BRIEF_V3, **({'candidate_id': candidate_id} if candidate_id else {})}
            if session['role'] == 'implementer':
                return ('Method-v3 implementer brief · ' + str(candidate_id),
                        v3_implementer_brief(task, tool=tool, hypothesis_id=session['hypothesis_id'],
                                             candidate_id=candidate_id, worktree=cwd), refs)
            return ('Method-v3 analyst brief · ' + str(candidate_id),
                    v3_analyst_brief(task, tool=tool, candidate_id=candidate_id), refs)
        card = None
        candidates = None
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='iterations'").fetchone():
            candidates = [(r['id'], r['frozen_commit'], (json.loads(r['binding_json']) or {}).get('worktree_path')
                           if r['binding_json'] else None) for r in conn.execute(
                'SELECT c.id, c.frozen_commit, w.binding_json FROM iterations i JOIN candidates c '
                'ON c.iteration_id=i.id LEFT JOIN candidate_worktrees w ON w.candidate_id=c.id '
                'WHERE i.task_id=? ORDER BY c.created_at', (task_id,))]
        protocol = None
        candidate_id = session['candidate_id'] if 'candidate_id' in session.keys() else None
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='iterations'").fetchone():
            row = (conn.execute('SELECT i.protocol_json FROM candidates c JOIN iterations i ON i.id=c.iteration_id '
                                'WHERE c.id=?', (candidate_id,)) if candidate_id else
                   conn.execute('SELECT protocol_json FROM iterations WHERE task_id=?', (task_id,))).fetchone()
            protocol = json.loads(row['protocol_json']) if row and row['protocol_json'] else None
        k_base = (protocol or {}).get('k_base', 2)
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
                                 start_dirty=[d['path'] for d in start.get('dirty', [])], candidate_id=candidate_id, k_base=k_base,
                                 protocol=protocol)
        title = '实现 Agent 工作说明 · ' + card['title']
    else:
        node = node_context(repository, run_views, task_id, candidate_id) if candidate_id else None
        scoped = 'scope' in session.keys() and session['scope'] == 'iteration'
        tree = iteration_context(repository, run_views, task_id) if scoped else None
        text = analyst_brief(task, eval_root=str(Path(workspace['location'])), tool=tool, worktree=worktree, cwd=cwd,
                             candidates=candidates, node=node, tree=tree, protocol=protocol)
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
