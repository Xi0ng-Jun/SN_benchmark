"""Readable report of an iteration's frozen failure clusters, for people.

Run through `scripts/cluster-report` on the machine of a running workbench
server. Frozen clusters (description, signatures, members, per-attempt
observations) are read from the state database opened read-only, the same
source as `sn-eval cluster show`; there is no HTTP route for them. Round
rankings, cards and candidates, and gold answers of a few example cases, come
from the server's ordinary GET routes. Nothing is written except the output.

The report shows questions, gold answers and SN answers of search cases. It is
for people only: never give it, or its JSON, to an analysis or implementation
Agent, and never commit a generated report.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from html import escape
import json
import math
import os
from pathlib import Path
import sqlite3
import sys
from urllib.parse import quote

FORMAT = 'sn-cluster-report-v1'

FIELD_LABELS = {
    'status': '运行状态',
    'prediction': 'SN 的回答',
    'response.answer': 'SN 的回答',
    'response.grounded': '回答是否有依据',
    'response.evidence_level': '回答的证据等级',
    'intent_preview.needs_clarification': '是否判定需要澄清',
    'deterministic.evidence_hit': '检索是否命中证据',
    'deterministic.citation_valid_count': '有效引用数',
    'deterministic.citation_invalid_count': '无效引用数',
    'stage_metrics.gold_evidence_recall.value': '检索召回的 gold 证据比例',
    'stage_metrics.context_precision.value': '检索上下文精度',
    'stage_metrics.citation_precision.value': '引用精度',
    'usage.total_tokens': '消耗的 token 数',
    'retrieval_context.*.text': '任一检索片段的文本',
}


# ---------------------------------------------------------------- collection

def _read_clusters(state, iteration_id):
    database = Path(state) / 'workbench.sqlite'
    if not database.is_file():
        raise SystemExit(f'找不到状态数据库：{database}')
    # Read-only: the server keeps writing this database while rounds run.
    conn = sqlite3.connect(f'file:{quote(str(database.resolve()))}?mode=ro', uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        if iteration_id is None:
            row = conn.execute('SELECT iteration_id FROM clusters ORDER BY frozen_at DESC LIMIT 1').fetchone()
            if row is None:
                raise SystemExit('数据库里还没有任何冻结的簇')
            iteration_id = row['iteration_id']
        rows = conn.execute('SELECT * FROM clusters WHERE iteration_id=? ORDER BY frozen_at, cluster_id',
                            (iteration_id,)).fetchall()
    except sqlite3.OperationalError as exc:
        raise SystemExit(f'读取簇失败：{exc}') from exc
    finally:
        conn.close()
    clusters = []
    for row in rows:
        record = dict(row)
        for key in ('signature', 'missing_signature', 'violation_signature', 'scan'):
            record[key] = json.loads(record.pop(key + '_json'))
        record['frozen_from_execution'] = json.loads(record['frozen_from_execution'])
        clusters.append(record)
    return iteration_id, clusters


def _attempt_outcome(row, primary, threshold):
    value = (row.get('metrics') or {}).get(primary)
    if row.get('status') in ('error', 'missing'):
        return 'fail', value
    if type(value) not in (int, float) or not math.isfinite(value):
        return 'unknown', None
    return ('pass' if value >= threshold else 'fail'), value


def _round_links(invocations):
    """cluster_id -> what each round did with it (rank, score, selection, card, candidate)."""
    links = {}
    for invocation in invocations or []:
        for rnd in invocation.get('rounds') or []:
            selection = rnd.get('selection') or {}
            chosen = {c.get('cluster_id'): c for c in rnd.get('cards') or selection.get('cards') or []}
            candidates = {c.get('cluster_id'): c for c in rnd.get('candidates') or []}
            for rank, row in enumerate(selection.get('ranking') or [], 1):
                cid = row.get('cluster_id')
                candidate = candidates.get(cid) or {}
                verdict = candidate.get('verdict') or {}
                links.setdefault(cid, []).append(dict(
                    invocation_id=invocation.get('id'), round=rnd.get('number'), rank=rank,
                    ranked=len(selection['ranking']), score=row.get('score'), q=row.get('q'), m=row.get('m'),
                    v=row.get('v'), pi=row.get('pi'), fallback=row.get('fallback'), status=row.get('status'),
                    selected=cid in chosen, hypothesis_id=row.get('hypothesis_id') or (chosen.get(cid) or {}).get(
                        'hypothesis_id'),
                    candidate=dict(candidate_id=candidate.get('candidate_id'), phase=candidate.get('phase'),
                                   status=candidate.get('status'), reason=candidate.get('reason'),
                                   verdict=verdict.get('verdict'), rho=verdict.get('rho'),
                                   delta=verdict.get('delta'), eta=verdict.get('eta')) if candidate else None))
    return links


def _card(record):
    card = record.get('card') or {}
    if isinstance(card, str):
        card = json.loads(card)
    contract = card.get('contract') or {}
    return dict(id=record.get('id'), status=record.get('status'), title=card.get('title'),
                component=card.get('component'), mechanism=card.get('mechanism'),
                change_scope=card.get('change_scope') or [], expected_benefit=card.get('expected_benefit'),
                predicted_flips=contract.get('predicted_flips') or [], guards=contract.get('guards') or [],
                verdict=((record.get('verification') or {}).get('verdict') or {}).get('verdict')
                if isinstance(record.get('verification'), dict) else None)


def collect(state, *, url=None, iteration_id=None, examples=4):
    iteration_id, clusters = _read_clusters(state, iteration_id)
    warnings, iteration, invocations, cards = [], {}, [], {}
    client = None
    if url:
        from .operator_client import Client
        client = Client(url, (Path(state) / 'access-token').read_text().strip())

    def get(path, what):
        if client is None:
            return None
        try:
            return client.call('GET', path)
        except (SystemExit, OSError, ValueError) as exc:
            warnings.append(f'{what}读取失败：{str(exc)[:160]}')
            return None

    iteration = get(f'/api/iterations/{iteration_id}', '迭代信息') or {}
    invocations = get(f'/api/iterations/{iteration_id}/rounds', '轮次') or []
    if iteration.get('task_id'):
        for record in get(f"/api/tasks/{iteration['task_id']}/hypotheses", '假设卡') or []:
            try:
                cards[record.get('id')] = _card(record)
            except (TypeError, ValueError):
                continue
    protocol = iteration.get('protocol') or {}
    splits = protocol.get('splits') or {}
    search, sealed = set(splits.get('search') or []), set(splits.get('sealed') or [])
    links = _round_links(invocations)

    run_of = {}  # (execution_id, case_id) -> run id, filled per execution on demand
    def run_id(execution_id, case_id):
        if execution_id not in {e for e, _ in run_of}:
            body = get(f'/api/run-views/executions/{execution_id}/cases', '执行的题目列表') or {}
            for row in body.get('rows') or body.get('cases') or []:
                run_of[(execution_id, row.get('case_id'))] = row.get('run_id')
            run_of.setdefault((execution_id, None), None)
        return run_of.get((execution_id, case_id))

    out = []
    for cluster in clusters:
        scan = cluster['scan']
        primary, threshold = scan.get('primary') or 'answer_f1', scan.get('success_threshold', .5)
        executions = scan.get('executions') or []
        members = []
        for cid in scan.get('members') or []:
            if cid in sealed or (search and cid not in search):
                continue  # Clusters are search-only by construction; never show anything else.
            rows = (scan.get('observations') or {}).get(cid) or []
            attempts = []
            for row in rows:
                outcome, value = _attempt_outcome(row, primary, threshold)
                attempts.append(dict(outcome=outcome, score=value, status=row.get('status'),
                                     answer=row.get('prediction') or (row.get('response') or {}).get('answer')))
            question = next((r.get('question') for r in rows if r.get('question')), None)
            members.append(dict(case_id=cid, question=question, attempts=attempts,
                                failed=sum(a['outcome'] == 'fail' for a in attempts)))
        # Examples: the most consistently failing members first.
        members.sort(key=lambda m: (-m['failed'], m['case_id']))
        for member in members[:examples]:
            for execution_id in executions:
                rid = run_id(execution_id, member['case_id'])
                if rid is None:
                    continue
                detail = get(f"/api/run-views/runs/{quote(rid)}/case?case_id={quote(member['case_id'])}", '题目详情')
                if detail:
                    gold = (detail.get('gold') or {}).get('annotations') or []
                    member['gold'] = [g.get('answer') for g in gold if g.get('answer')]
                    member['example'] = True
                break
        round_rows = links.get(cluster['cluster_id'], [])
        hypothesis_ids = [r['hypothesis_id'] for r in round_rows if r.get('hypothesis_id')]
        out.append(dict(
            cluster_id=cluster['cluster_id'], description=cluster['description'], frozen_at=cluster['frozen_at'],
            signature=cluster['signature'], missing_signature=cluster['missing_signature'],
            violation_signature=cluster['violation_signature'], primary=primary, success_threshold=threshold,
            stats={k: scan.get(k) for k in ('n_cases', 'n_attempts', 'n_success', 'n_failed', 'n_unknown',
                                            'n_missing', 'n_violation', 'q', 'm', 'v', 'retrieval_ceiling',
                                            'verification_ceiling')},
            rounds=round_rows, cards=[cards[h] for h in dict.fromkeys(hypothesis_ids) if h in cards],
            members=members))
    settings = {}
    for invocation in invocations:
        settings = invocation.get('settings') or settings
    return dict(format=FORMAT, generated_at=datetime.now(timezone.utc).isoformat(timespec='seconds'),
                iteration=dict(id=iteration_id, title=iteration.get('title') or protocol.get('title'),
                               suite=(protocol.get('input') or {}).get('suite'),
                               search=len(splits.get('search') or []), validation=len(splits.get('validation') or []),
                               success_threshold=protocol.get('success_threshold', .5)),
                settings=dict(b=settings.get('b'), pi=settings.get('pi'), max_clusters=settings.get('max_clusters')),
                clusters=out, warnings=warnings)


# ------------------------------------------------------------------ rendering

def _pct(value):
    return '—' if value is None else f'{value * 100:.0f}%'


def _num(value, digits=2):
    return '—' if value is None else f'{value:.{digits}f}'


def _field(name):
    if name.startswith('metrics.'):
        return '答案得分 ' + name.split('.', 1)[1]
    return FIELD_LABELS.get(name, name)


def describe(signature):
    """A signature as nested plain-language HTML list items."""
    if 'all_of' in signature:
        return '<li>同时满足以下全部：<ul>' + ''.join(describe(s) for s in signature['all_of']) + '</ul></li>'
    if 'any_of' in signature:
        return '<li>满足以下任一：<ul>' + ''.join(describe(s) for s in signature['any_of']) + '</ul></li>'
    if 'not' in signature:
        return '<li>不满足：<ul>' + describe(signature['not']) + '</ul></li>'
    field = _field(signature.get('field', '?'))
    if signature.get('kind') == 'regex':
        return (f'<li>「{escape(field)}」包含符合正则 <code>{escape(signature.get("pattern", ""))}</code> '
                '的文字</li>')
    low, high = signature.get('min'), signature.get('max')
    if low is not None and high is not None:
        bound = f'等于 {low:g}' if low == high else f'在 {low:g} 到 {high:g} 之间'
    elif low is not None:
        bound = f'不小于 {low:g}'
    else:
        bound = f'不大于 {high:g}'
    return f'<li>「{escape(field)}」{bound}</li>'


STYLE = """
.viz-root{color-scheme:light;--surface-1:#fcfcfb;--surface-2:#f3f2ef;--border:#e2e1dc;
 --text-primary:#0b0b0b;--text-secondary:#52514e;--text-muted:#77766f;--series-1:#2a78d6;
 --track:#ecebe7;--good:#0ca30c;--critical:#d03b3b;--unknown:#9a9992;--accent-soft:#e8f1fc}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])) .viz-root{color-scheme:dark;
 --surface-1:#1a1a19;--surface-2:#242422;--border:#383835;--text-primary:#fff;--text-secondary:#c3c2b7;
 --text-muted:#9a998f;--series-1:#3987e5;--track:#2e2e2c;--accent-soft:#1d2a3b}}
*{box-sizing:border-box}
body{margin:0;background:var(--surface-1);color:var(--text-primary);
 font:15px/1.65 -apple-system,"PingFang SC","Microsoft YaHei","Noto Sans CJK SC",sans-serif}
main{max-width:1080px;margin:0 auto;padding:32px 24px 64px}
h1{font-size:26px;margin:0 0 4px}h2{font-size:20px;margin:40px 0 12px}h3{font-size:17px;margin:0}
.sub{color:var(--text-secondary);margin:0 0 24px}
.panel{background:var(--surface-2);border:1px solid var(--border);border-radius:10px;padding:16px 20px;margin:16px 0}
.steps{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:12px}
.step{background:var(--surface-1);border:1px solid var(--border);border-radius:8px;padding:12px 14px}
.step b{display:block;margin-bottom:4px}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--border);vertical-align:top}
th{color:var(--text-secondary);font-weight:600;white-space:nowrap}
td.num{font-variant-numeric:tabular-nums;white-space:nowrap}
.bar{display:flex;align-items:center;gap:8px;min-width:180px}
.bar .track{flex:1;height:10px;background:var(--track);border-radius:0 4px 4px 0}
.bar .fill{height:10px;background:var(--series-1);border-radius:0 4px 4px 0}
.bar span{color:var(--text-secondary);font-variant-numeric:tabular-nums;min-width:40px}
.badge{display:inline-block;font-size:12px;padding:1px 8px;border-radius:999px;border:1px solid var(--border);
 color:var(--text-secondary);margin-right:4px;white-space:nowrap}
.badge.sel{background:var(--accent-soft);color:var(--text-primary);border-color:var(--series-1)}
.cluster{border:1px solid var(--border);border-radius:12px;padding:20px 22px;margin:20px 0;background:var(--surface-1)}
.cluster header{display:flex;flex-wrap:wrap;gap:8px;align-items:baseline;margin-bottom:8px}
.cid{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:15px}
.desc{font-size:16px;margin:4px 0 16px}
.tiles{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:12px 0}
.tile{background:var(--surface-2);border-radius:8px;padding:10px 12px}
.tile .v{font-size:24px;font-weight:600;font-variant-numeric:tabular-nums}
.tile .k{color:var(--text-secondary);font-size:13px}
.tile .h{color:var(--text-muted);font-size:12px}
.sig ul{margin:4px 0;padding-left:20px}.sig>ul{padding-left:18px}
details{margin:10px 0}summary{cursor:pointer;color:var(--text-secondary)}
.ex{border-left:3px solid var(--series-1);padding:8px 14px;margin:12px 0;background:var(--surface-2);border-radius:0 8px 8px 0}
.ex .q{font-weight:600}.ex .row{margin-top:4px}.ex .lab{color:var(--text-secondary);display:inline-block;min-width:72px}
.dot{display:inline-block;width:20px;height:20px;line-height:20px;text-align:center;border-radius:50%;
 color:#fff;font-size:12px;font-weight:700;margin-right:3px}
.dot.pass{background:var(--good)}.dot.fail{background:var(--critical)}.dot.unknown{background:var(--unknown)}
.muted{color:var(--text-muted)}code{font-size:13px;background:var(--surface-2);padding:1px 5px;border-radius:4px}
.warn{border-color:#fab219}
@media (max-width:760px){.steps,.tiles{grid-template-columns:1fr 1fr}}
"""

OUTCOME = {'pass': ('✓', '答对'), 'fail': ('✗', '答错'), 'unknown': ('?', '没有得分')}


def _dots(attempts):
    out = []
    for i, attempt in enumerate(attempts, 1):
        mark, word = OUTCOME[attempt['outcome']]
        score = '' if attempt['score'] is None else f'，得分 {attempt["score"]:.2f}'
        out.append(f'<span class="dot {attempt["outcome"]}" title="第 {i} 次运行：{word}{score}">{mark}</span>')
    return ''.join(out)


def _latest(rounds):
    return rounds[-1] if rounds else None


def _verdict_text(candidate):
    if not candidate:
        return '—'
    words = {'accept': '通过', 'audit': '审计', 'reject': '拒绝', 'unmeasurable': '无法测量', 'pending': '测量中'}
    verdict = candidate.get('verdict')
    if verdict:
        return words.get(verdict, verdict)
    return {'running': '进行中', 'stalled': '停滞', 'failed': '失败', 'done': '已结束'}.get(
        candidate.get('status'), candidate.get('status') or '—')


def render(data):
    it, clusters = data['iteration'], data['clusters']
    pi = (data.get('settings') or {}).get('pi')
    scores = [(_latest(c['rounds']) or {}).get('score') for c in clusters]
    top = max([s for s in scores if s is not None] or [1]) or 1
    order = sorted(range(len(clusters)), key=lambda i: (scores[i] is None, -(scores[i] or 0), clusters[i]['cluster_id']))
    p = []
    p.append(f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
             f'<meta name="viewport" content="width=device-width,initial-scale=1">'
             f'<title>失败簇报告 · {escape(it.get("title") or it["id"])}</title><style>{STYLE}</style></head>'
             f'<body class="viz-root"><main>')
    p.append(f'<h1>失败簇报告</h1><p class="sub">{escape(it.get("title") or "")} · 迭代 '
             f'<code>{escape(it["id"][:8])}</code> · {len(clusters)} 个簇 · 生成于 {escape(data["generated_at"][:16].replace("T", " "))} UTC</p>')
    for warning in data.get('warnings') or []:
        p.append(f'<div class="panel warn">⚠ {escape(warning)}</div>')

    threshold = it.get('success_threshold', .5)
    p.append('<div class="panel"><h3>什么是“簇”？</h3>'
             f'<p>系统先让 SN 把 {it.get("search") or "搜索集"} 道搜索题各答几遍，记下每道题答对没有（得分 ≥ '
             f'{threshold:g} 算答对）。分析 Agent 再看答错的题，找出<b>同一种原因</b>导致的一批题，写成一条'
             '<b>机器可以逐题核对的规则</b>（签名）。规则一旦冻结，就由程序而不是 Agent 来判定哪些题属于这个簇。'
             '这样每个簇都是“一类可复现的失败”，而不是一个笼统的印象。</p><div class="steps">'
             '<div class="step"><b>① 找共性</b>从答错的题里归纳出一类失败，比如“题目问了多个对象，SN 只答了一个”。</div>'
             '<div class="step"><b>② 冻结规则</b>把这类失败写成可机械判定的签名，程序据此圈出成员题、算出成功率。</div>'
             '<div class="step"><b>③ 按收益挑选</b>按“能挽回多少题”排序，每轮挑前几名，各派一个 Agent 改 SN 的代码去修，'
             '再用没见过的题验收。</div></div></div>')

    p.append('<h2>总览：哪些簇最值得修</h2>'
             '<p class="muted">排序分 = 簇内题数 × 失败率 × max(m, v, π)，可以理解为“修好这一类大约能挽回几道题”。'
             f'π = {_num(pi, 1)} 是 m、v 都测不出来时使用的保守假设。带“本轮修复”标记的是被选中的簇。</p>')
    p.append('<table><thead><tr><th>簇</th><th>这一类失败是什么</th><th class="num">题数</th>'
             '<th class="num">成功率 q</th><th>排序分</th><th>修复情况</th></tr></thead><tbody>')
    for i in order:
        c = clusters[i]
        latest = _latest(c['rounds']) or {}
        score = latest.get('score')
        width = 0 if score is None else max(2, score / top * 100)
        selected = [r for r in c['rounds'] if r.get('selected')]
        status = ''.join(f'<span class="badge sel">第 {r["round"]} 轮修复 · {_verdict_text(r.get("candidate"))}</span>'
                         for r in selected) or '<span class="badge">未选中</span>'
        title = '无法计算' if score is None else f'{score:.2f}'
        p.append(f'<tr><td><a class="cid" href="#c-{escape(c["cluster_id"])}">{escape(c["cluster_id"])}</a></td>'
                 f'<td>{escape(c["description"][:120])}{"…" if len(c["description"]) > 120 else ""}</td>'
                 f'<td class="num">{c["stats"].get("n_cases") or 0}</td><td class="num">{_pct(c["stats"].get("q"))}</td>'
                 f'<td><div class="bar" title="排序分 {title}"><div class="track"><div class="fill" '
                 f'style="width:{width:.1f}%"></div></div><span>{title}</span></div></td><td>{status}</td></tr>')
    p.append('</tbody></table>')

    p.append('<h2>逐个簇看</h2>')
    for i in order:
        c = clusters[i]
        s, latest = c['stats'], _latest(c['rounds']) or {}
        p.append(f'<section class="cluster" id="c-{escape(c["cluster_id"])}"><header>'
                 f'<h3 class="cid">{escape(c["cluster_id"])}</h3>')
        if latest:
            p.append(f'<span class="badge">第 {latest["round"]} 轮排名 {latest["rank"]}/{latest["ranked"]}</span>')
        for r in c['rounds']:
            if r.get('selected'):
                p.append(f'<span class="badge sel">第 {r["round"]} 轮修复 · {_verdict_text(r.get("candidate"))}</span>')
        p.append(f'<span class="muted">冻结于 {escape(c["frozen_at"][:16].replace("T", " "))}</span></header>'
                 f'<p class="desc">{escape(c["description"])}</p>')
        failed_rate = None if s.get('q') is None else 1 - s['q']
        p.append('<div class="tiles">'
                 f'<div class="tile"><div class="k">成员题数</div><div class="v">{s.get("n_cases") or 0}</div>'
                 f'<div class="h">共 {s.get("n_attempts") or 0} 次运行</div></div>'
                 f'<div class="tile"><div class="k">成功率 q</div><div class="v">{_pct(s.get("q"))}</div>'
                 f'<div class="h">失败 {_pct(failed_rate)}</div></div>'
                 f'<div class="tile"><div class="k">m · 没找到资料</div><div class="v">{_pct(s.get("m"))}</div>'
                 f'<div class="h">失败里检索没召回 gold 证据的比例</div></div>'
                 f'<div class="tile"><div class="k">v · 有资料却答坏</div><div class="v">{_pct(s.get("v"))}</div>'
                 f'<div class="h">失败里无效引用或误称无法回答的比例</div></div></div>')
        if s.get('m') is None and s.get('v') is None:
            p.append('<p class="muted">m、v 显示“—”表示这个数据集没有逐题的 gold 证据标注，测不出来；排序时改用 π。</p>')
        p.append('<div class="sig"><b>成员规则（签名）</b>：一道题只要有一次运行符合下面的条件，就算这个簇的成员。'
                 f'<ul>{describe(c["signature"])}</ul></div>')
        p.append('<details><summary>m、v 用的判定规则</summary><div class="sig">“没找到资料”：'
                 f'<ul>{describe(c["missing_signature"])}</ul>“有资料却答坏”：'
                 f'<ul>{describe(c["violation_signature"])}</ul></div></details>')

        examples = [m for m in c['members'] if m.get('example')] or c['members'][:2]
        if examples:
            p.append('<b>典型例子</b>')
        for m in examples:
            answers = [a for a in m['attempts'] if a.get('answer')]
            p.append(f'<div class="ex"><div class="q">{escape(m.get("question") or m["case_id"])}</div>')
            if m.get('gold'):
                gold = '；'.join(escape(g[:300]) for g in m['gold'][:3])
                p.append(f'<div class="row"><span class="lab">标准答案</span>{gold}</div>')
            if answers:
                p.append(f'<div class="row"><span class="lab">SN 回答</span>{escape(str(answers[0]["answer"])[:500])}</div>')
            p.append(f'<div class="row"><span class="lab">各次运行</span>{_dots(m["attempts"])}'
                     f'<span class="muted"> {escape(m["case_id"])}</span></div></div>')

        for card in c['cards']:
            flips, guards = len(card['predicted_flips']), len(card['guards'])
            p.append(f'<div class="panel"><b>修复方案（假设卡）</b>：{escape(card.get("title") or "")}'
                     f'<div class="row"><span class="muted">改动位置：</span>{escape(card.get("component") or "—")}'
                     + (f' · <code>{escape(", ".join(card["change_scope"][:6]))}</code>' if card['change_scope'] else '')
                     + f'</div><div class="row"><span class="muted">为什么会有效：</span>{escape(card.get("mechanism") or "—")}</div>'
                     f'<div class="row"><span class="muted">合同：</span>预测 {flips} 道题会从错变对，'
                     f'{guards} 道守卫题不能变差</div></div>')
        for r in c['rounds']:
            cand = r.get('candidate')
            if not (r.get('selected') and cand):
                continue
            details = []
            if cand.get('rho') is not None:
                details.append(f'合同兑现率 ρ = {_pct(cand["rho"])}')
            if cand.get('delta') is not None:
                details.append(f'验证集分差 Δ = {cand["delta"]:+.3f}（噪声带 η = {_num(cand.get("eta"), 3)}）')
            if cand.get('reason'):
                details.append('原因：' + str(cand['reason']))
            p.append(f'<p><b>第 {r["round"]} 轮结果</b>：<span class="badge sel">{_verdict_text(cand)}</span>'
                     f' {escape("；".join(details))}</p>')

        p.append(f'<details><summary>全部 {len(c["members"])} 道成员题</summary><table><thead><tr><th>各次运行</th>'
                 '<th>题目</th><th>SN 第一次的回答</th></tr></thead><tbody>')
        for m in c['members']:
            first = next((a.get('answer') for a in m['attempts'] if a.get('answer')), '')
            p.append(f'<tr><td>{_dots(m["attempts"])}</td><td>{escape((m.get("question") or m["case_id"])[:160])}</td>'
                     f'<td class="muted">{escape(str(first)[:160])}</td></tr>')
        p.append('</tbody></table></details></section>')
    p.append('<p class="muted">这份报告含有搜索集的题目、标准答案和 SN 的回答，只供人阅读：'
             '不要交给分析或实现 Agent，也不要提交进仓库。</p></main></body></html>')
    return ''.join(p)


# ------------------------------------------------------------------------ CLI

def main(argv=None):
    parser = argparse.ArgumentParser(description='把一个迭代的失败簇渲染成一页 HTML 报告')
    parser.add_argument('--state', default=os.environ.get('SN_WORKBENCH_STATE'),
                        help='工作台 state 目录（含 workbench.sqlite 和 access-token）')
    parser.add_argument('--url', default=os.environ.get('SN_WORKBENCH_URL'),
                        help='工作台地址，例如 http://127.0.0.1:8093；不给就只读数据库，缺轮次和标准答案')
    parser.add_argument('--iteration', help='迭代 ID；默认取最近冻结过簇的迭代')
    parser.add_argument('--examples', type=int, default=4, help='每个簇展示几道带标准答案的例子')
    parser.add_argument('--out', help='输出 HTML 路径；默认 cluster-report-<迭代前8位>.html')
    parser.add_argument('--json-out', help='另存采集到的数据（JSON），可拿到别处用 --from-json 渲染')
    parser.add_argument('--from-json', help='不连服务器，直接渲染之前导出的 JSON')
    args = parser.parse_args(argv)
    if args.from_json:
        try:
            data = json.loads(Path(args.from_json).read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise SystemExit(f'读不了 {args.from_json}：{exc}') from exc
        if not isinstance(data, dict) or data.get('format') != FORMAT:
            raise SystemExit('不是 cluster-report 导出的 JSON')
    else:
        if not args.state:
            raise SystemExit('需要 --state（或环境变量 SN_WORKBENCH_STATE）')
        data = collect(args.state, url=args.url, iteration_id=args.iteration, examples=args.examples)
    out = Path(args.out or f'cluster-report-{data["iteration"]["id"][:8]}.html')
    out.write_text(render(data), encoding='utf-8')
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'{out}  ({len(data["clusters"])} 个簇'
          + (f'，{len(data["warnings"])} 条警告' if data['warnings'] else '') + ')', file=sys.stderr)
    for warning in data['warnings']:
        print('  ' + warning, file=sys.stderr)


if __name__ == '__main__':
    main()
