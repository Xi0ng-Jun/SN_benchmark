"""Sealed lineage curve (method v2 §2.5 end step) as one HTML page and a CSV.

Run through `scripts/lcd-curve` on the machine of the workbench server. The
curve is read from the state database opened read-only; the server computes
and freezes it the first time the iteration ledger is read after every sealed
execution finished, so `--url` reads that route once before reading the
database. Nothing is written except the outputs.

The page holds aggregate scores only, no sealed case. Still, do not give it to
an Agent while any optimisation on the same data is running.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from html import escape
import json
import os
from pathlib import Path
import sqlite3
import sys
from urllib.parse import quote

from .cluster_report import STYLE as BASE_STYLE

FORMAT = 'sn-lcd-curve-v1'
COLUMNS = ('node_index', 'candidate_id', 'frozen_commit', 'status', 'score', 'se', 'delta_vs_seed', 'se_delta',
           'lower', 'upper', 'p', 'p_holm', 'lower_adj', 'upper_adj', 'holds_holm')


# ---------------------------------------------------------------- collection

def _read(state, iteration_id):
    database = Path(state) / 'workbench.sqlite'
    if not database.is_file():
        raise SystemExit(f'找不到状态数据库：{database}')
    # Read-only: the server keeps writing this database.
    conn = sqlite3.connect(f'file:{quote(str(database.resolve()))}?mode=ro', uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        if iteration_id is None:
            row = conn.execute('SELECT iteration_id FROM sealed_lineage_claims ORDER BY claimed_at DESC LIMIT 1').fetchone()
            if row is None:
                raise SystemExit('数据库里还没有任何封存集谱系评测')
            iteration_id = row['iteration_id']
        claim = conn.execute('SELECT * FROM sealed_lineage_claims WHERE iteration_id=?', (iteration_id,)).fetchone()
        if claim is None:
            raise SystemExit(f'迭代 {iteration_id} 还没有封存集谱系评测')
        title = conn.execute('SELECT title FROM iterations WHERE id=?', (iteration_id,)).fetchone()
        slots = conn.execute('SELECT COUNT(*) AS n, COUNT(execution_id) AS dispatched FROM sealed_lineage_slots '
                             'WHERE iteration_id=?', (iteration_id,)).fetchone()
    except sqlite3.OperationalError as exc:
        raise SystemExit(f'读取封存集评测失败：{exc}') from exc
    finally:
        conn.close()
    if claim['result_json']:
        lineage = json.loads(claim['result_json'])
    else:
        lineage = dict(kind='lineage', status=claim['status'], confirmed_by=claim['confirmed_by'],
                       claimed_at=claim['claimed_at'], k=claim['k'], nodes=len(json.loads(claim['nodes_json'])),
                       curve=None, progress=dict(slots=slots['n'], dispatched=slots['dispatched']))
    return dict(id=iteration_id, title=title['title'] if title else ''), lineage


def collect(state, *, url=None, iteration_id=None):
    warnings = []
    if url:
        if iteration_id is None:
            iteration_id = _read(state, None)[0]['id']
        from .operator_client import Client
        try:
            Client(url, (Path(state) / 'access-token').read_text().strip()).call(
                'GET', f'/api/iterations/{iteration_id}/ledger')
        except (SystemExit, OSError, ValueError) as exc:
            warnings.append(f'服务器结算读取失败：{str(exc)[:160]}')
    iteration, lineage = _read(state, iteration_id)
    if lineage.get('curve') is None:
        warnings.append('曲线尚未生成：封存集执行未全部结束，或服务器还没读过这个迭代的记账（加 --url 让服务器结算）。')
    return dict(format=FORMAT, generated_at=datetime.now(timezone.utc).isoformat(timespec='seconds'),
                iteration=iteration, lineage=lineage, warnings=warnings)


# ----------------------------------------------------------------- rendering

STYLE = BASE_STYLE + """
.chart{width:100%;height:auto;display:block}
.chart .grid{stroke:var(--border);stroke-width:1}
.chart .zero{stroke:var(--text-muted);stroke-width:1;stroke-dasharray:4 3}
.chart .axis{fill:var(--text-secondary);font-size:12px}
.chart .line{fill:none;stroke:var(--series-1);stroke-width:2}
.chart .err{stroke:var(--series-1);stroke-width:2;stroke-linecap:round}
.chart .err.adj{stroke:var(--track);stroke-width:8}
.chart .pt{fill:var(--series-1);stroke:var(--surface-1);stroke-width:2}
.chart .pt.miss{fill:var(--unknown)}
.chart .lab{fill:var(--text-primary);font-size:12px;font-variant-numeric:tabular-nums}
"""


def _num(value, digits=3):
    return '—' if value is None else f'{value:.{digits}f}'


def _pvalue(value):
    return '—' if value is None else ('<0.001' if value < .001 else f'{value:.3f}')


def _chart(curve, *, value, low, high, low_adj=None, high_adj=None, zero=False, caption):
    """One series over node index, intervals as vertical bars, direct label on the last point."""
    width, height, left, right, top, bottom = 960, 300, 56, 24, 16, 40
    points = [p for p in curve if p[value] is not None]
    bounds = [p[k] for p in points for k in (value, low, high, low_adj, high_adj) if k and p.get(k) is not None]
    if zero:
        bounds.append(0.)
    lo, hi = (min(bounds), max(bounds)) if bounds else (0., 1.)
    if hi - lo < 1e-9:
        lo, hi = lo - .05, hi + .05
    pad = (hi - lo) * .08
    lo, hi = lo - pad, hi + pad
    count = max(len(curve) - 1, 1)
    x = lambda i: left + (width - left - right) * (i / count if len(curve) > 1 else .5)  # noqa: E731
    y = lambda v: top + (height - top - bottom) * (hi - v) / (hi - lo)  # noqa: E731
    p = [f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" aria-label="{escape(caption)}">']
    for t in range(5):
        v = lo + (hi - lo) * t / 4
        p.append(f'<line class="grid" x1="{left}" x2="{width - right}" y1="{y(v):.1f}" y2="{y(v):.1f}"/>'
                 f'<text class="axis" x="{left - 8}" y="{y(v) + 4:.1f}" text-anchor="end">{v:.2f}</text>')
    if zero and lo < 0 < hi:
        p.append(f'<line class="zero" x1="{left}" x2="{width - right}" y1="{y(0):.1f}" y2="{y(0):.1f}"/>')
    for point in curve:
        i = point['node_index']
        p.append(f'<text class="axis" x="{x(i):.1f}" y="{height - 16}" text-anchor="middle">'
                 f'{"种子" if i == 0 else i}</text>')
    if len(points) > 1:
        p.append('<polyline class="line" points="' +
                 ' '.join(f'{x(q["node_index"]):.1f},{y(q[value]):.1f}' for q in points) + '"/>')
    for point in curve:
        i, v = point['node_index'], point[value]
        if v is None:
            p.append(f'<circle class="pt miss" cx="{x(i):.1f}" cy="{height - bottom:.1f}" r="5">'
                     f'<title>节点 {i}：无法测量</title></circle>')
            continue
        if low_adj and point.get(low_adj) is not None:
            p.append(f'<line class="err adj" x1="{x(i):.1f}" x2="{x(i):.1f}" '
                     f'y1="{y(point[low_adj]):.1f}" y2="{y(point[high_adj]):.1f}"/>')
        if point.get(low) is not None:
            p.append(f'<line class="err" x1="{x(i):.1f}" x2="{x(i):.1f}" '
                     f'y1="{y(point[low]):.1f}" y2="{y(point[high]):.1f}"/>')
        tip = (f'节点 {i} · {point["candidate_id"][:8]}\n封存集分数 {_num(point["score"])} ± {_num(point["se"])}\n'
               f'相对种子 Δ {_num(point["delta_vs_seed"])} [{_num(point["lower"])}, {_num(point["upper"])}]\n'
               f'校正后 [{_num(point["lower_adj"])}, {_num(point["upper_adj"])}] · Holm p {_pvalue(point["p_holm"])}')
        p.append(f'<circle class="hit" cx="{x(i):.1f}" cy="{y(v):.1f}" r="14" fill="transparent">'
                 f'<title>{escape(tip)}</title></circle>'
                 f'<circle class="pt" cx="{x(i):.1f}" cy="{y(v):.1f}" r="5"><title>{escape(tip)}</title></circle>')
    if points:
        last = points[-1]
        p.append(f'<text class="lab" x="{x(last["node_index"]) - 10:.1f}" y="{y(last[value]) - 12:.1f}" '
                 f'text-anchor="end">{_num(last[value])}</text>')
    p.append('</svg>')
    return ''.join(p)


def _score_band(curve, z):
    """Score ± z·SE, for the first chart."""
    return [dict(c, score_low=None if c['se'] is None else c['score'] - z * c['se'],
                 score_high=None if c['se'] is None else c['score'] + z * c['se']) for c in curve]


def render(data):
    it, lineage = data['iteration'], data['lineage']
    curve = lineage.get('curve')
    p = [f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
         f'<meta name="viewport" content="width=device-width,initial-scale=1">'
         f'<title>封存集谱系曲线 · {escape(it.get("title") or it["id"])}</title><style>{STYLE}</style></head>'
         f'<body class="viz-root"><main>',
         f'<h1>封存集谱系曲线</h1><p class="sub">{escape(it.get("title") or "")} · 迭代 '
         f'<code>{escape(it["id"][:8])}</code> · 生成于 {escape(data["generated_at"][:16].replace("T", " "))} UTC</p>']
    for warning in data['warnings']:
        p.append(f'<div class="panel warn">⚠ {escape(warning)}</div>')
    tiles = [('状态', lineage.get('status') or '—', ''), ('谱系节点', lineage.get('nodes') or '—', '种子 + 已确认改动'),
             ('每节点重跑 k', lineage.get('k') or '—', '同任务配对'),
             ('封存题数 n', lineage.get('n') or '—', lineage.get('metric') or '')]
    p.append('<div class="tiles">' + ''.join(
        f'<div class="tile"><div class="v">{escape(str(v))}</div><div class="k">{escape(k)}</div>'
        f'<div class="h">{escape(h)}</div></div>' for k, v, h in tiles) + '</div>')
    if not curve:
        progress = lineage.get('progress') or {}
        p.append(f'<div class="panel">已派发 {progress.get("dispatched", "—")} / {progress.get("slots", "—")} 次封存集评测'
                 + (f'，已结束 {progress["terminal"]}' if 'terminal' in progress else '') + '。</div></main></body></html>')
        return ''.join(p)
    z, z_adj, alpha = lineage['z'], lineage['z_adj'], lineage['alpha']
    p.append(f'<h2>封存集分数</h2><p class="muted">纵轴：每个节点 {escape(lineage.get("metric") or "")} 的封存集均值（k 次重跑取均值后对题平均）；'
             f'竖线为 ±{z:.2f}·SE。横轴：已确认改动序号。</p>')
    p.append(_chart(_score_band(curve, z), value='score', low='score_low', high='score_high', caption='封存集分数'))
    p.append(f'<h2>相对种子的配对差 Δ</h2><p class="muted">深色竖线：原始区间 Δ ± {z:.2f}·SE（单侧 α={alpha:g}）；'
             f'浅色宽带：按 {lineage["m"]} 个节点校正的同时区间（z={_num(z_adj, 2)}）。虚线为 0。'
             f'SE 方法：{escape(", ".join(sorted({c.get("se_method") or "—" for c in curve if c["status"] == "measured"})))}'
             + (f'，{lineage["n_families"]} 个任务族' if lineage.get('n_families') else '') + '。</p>')
    p.append(_chart(curve, value='delta_vs_seed', low='lower', high='upper', low_adj='lower_adj',
                    high_adj='upper_adj', zero=True, caption='相对种子的配对差'))
    p.append('<h2>数据表</h2><table><tr><th>序号</th><th>候选</th><th>分数 ± SE</th><th>Δ vs 种子</th>'
             '<th>原始区间</th><th>校正区间</th><th>p</th><th>Holm p</th><th>Holm 成立</th></tr>')
    for c in curve:
        holds = {True: '✓ 成立', False: '✗ 不成立', None: '—'}[c['holds_holm']]
        p.append(f'<tr><td class="num">{"种子" if c["node_index"] == 0 else c["node_index"]}</td>'
                 f'<td><code>{escape(c["candidate_id"][:8])}</code> <span class="muted">{escape((c["frozen_commit"] or "")[:8])}</span></td>'
                 + (f'<td class="num">{_num(c["score"])} ± {_num(c["se"])}</td>' if c['status'] == 'measured'
                    else '<td class="muted">无法测量</td>')
                 + f'<td class="num">{_num(c["delta_vs_seed"])}</td>'
                 f'<td class="num">[{_num(c["lower"])}, {_num(c["upper"])}]</td>'
                 f'<td class="num">[{_num(c["lower_adj"])}, {_num(c["upper_adj"])}]</td>'
                 f'<td class="num">{_pvalue(c["p"])}</td><td class="num">{_pvalue(c["p_holm"])}</td><td>{holds}</td></tr>')
    p.append(f'</table><p class="muted">p 为 Δ ≤ 0 的单侧检验；Holm 按 {lineage["m"]} 个可测节点逐步校正，'
             f'"成立" 指 Holm p &lt; {alpha:g}。确认者 {escape(lineage.get("confirmed_by") or "—")}，'
             f'认领于 {escape((lineage.get("claimed_at") or "")[:16].replace("T", " "))}。封存集只评这一次。</p>'
             '</main></body></html>')
    return ''.join(p)


def write_csv(data, path):
    with open(path, 'w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, extrasaction='ignore')
        writer.writeheader()
        for row in data['lineage'].get('curve') or []:
            writer.writerow(row)


# ------------------------------------------------------------------------ CLI

def main(argv=None):
    parser = argparse.ArgumentParser(description='把一个迭代的封存集谱系评测画成曲线（HTML）并导出 CSV')
    parser.add_argument('--state', default=os.environ.get('SN_WORKBENCH_STATE'),
                        help='工作台 state 目录（含 workbench.sqlite 和 access-token）')
    parser.add_argument('--url', default=os.environ.get('SN_WORKBENCH_URL'),
                        help='工作台地址；给出时先让服务器结算曲线，再只读数据库')
    parser.add_argument('--iteration', help='迭代 ID；默认取最近一次封存集谱系评测')
    parser.add_argument('--out', help='输出 HTML 路径；默认 lcd-curve-<迭代前8位>.html')
    parser.add_argument('--csv', help='输出 CSV 路径；默认 lcd-curve-<迭代前8位>.csv')
    parser.add_argument('--json-out', help='另存采集到的数据（JSON），可拿到别处用 --from-json 渲染')
    parser.add_argument('--from-json', help='不连服务器，直接渲染之前导出的 JSON')
    args = parser.parse_args(argv)
    if args.from_json:
        try:
            data = json.loads(Path(args.from_json).read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise SystemExit(f'读不了 {args.from_json}：{exc}') from exc
        if not isinstance(data, dict) or data.get('format') != FORMAT:
            raise SystemExit('不是 lcd-curve 导出的 JSON')
    else:
        if not args.state:
            raise SystemExit('需要 --state（或环境变量 SN_WORKBENCH_STATE）')
        data = collect(args.state, url=args.url, iteration_id=args.iteration)
    stem = f'lcd-curve-{data["iteration"]["id"][:8]}'
    out = Path(args.out or stem + '.html')
    out.write_text(render(data), encoding='utf-8')
    table = Path(args.csv or stem + '.csv')
    write_csv(data, table)
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
    curve = data['lineage'].get('curve')
    print(f'{out}  {table}  ' + (f'({len(curve)} 个节点)' if curve else f'(尚无曲线：{data["lineage"].get("status")})'),
          file=sys.stderr)
    for warning in data['warnings']:
        print('  ' + warning, file=sys.stderr)


if __name__ == '__main__':
    main()
