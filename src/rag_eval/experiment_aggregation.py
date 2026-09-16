"""Offline aggregation for completed or in-progress SN benchmark runs."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import json

from .starter_report import load_run


def discover_runs(root: str | Path) -> list[Path]:
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError(f"Run root does not exist: {root}")
    return sorted(p.parent for p in root.rglob("manifest.json") if p.parent.is_dir())


def aggregate_runs(run_dirs: list[str | Path]) -> dict:
    if not run_dirs:
        raise ValueError("No run directories supplied")
    runs = [load_run(Path(p).resolve()) for p in run_dirs]
    cells, metrics, statuses, errors = [], [], Counter(), Counter()
    for run in runs:
        manifest = run["manifest"]
        if manifest is None:
            cells.append({"path": run["path"], "phase": run["state"].get("phase", "unknown"), "suite": None, "track": None, "mode": None, "planned": 0, "outputs": 0, "scores": 0, "warnings": run["warnings"]})
            continue
        outputs = run["outputs"]
        statuses.update(o.get("status", "unknown") for o in outputs)
        errors.update((o.get("behavior") or {}).get("kind", "unknown") for o in outputs)
        cells.append({"path": run["path"], "run_id": manifest.get("run_id"), "suite": manifest.get("suite"),
                      "track": manifest.get("track"), "mode": manifest.get("mode"),
                      "phase": run["state"].get("phase", "unknown"), "planned": len(run["planned"]),
                      "outputs": len(outputs), "scores": len(run["scores"]), "warnings": run["warnings"]})
        for row in run["scores"]:
            metrics.append({"suite": manifest.get("suite"), "task": row.get("task", ""), "track": manifest.get("track"),
                            "mode": manifest.get("mode"), "case_id": row.get("case_id"), "scorer": row.get("scorer"),
                            "score": row.get("score"), "status": row.get("status"), "reason": row.get("reason")})
    numeric = [m["score"] for m in metrics if m["status"] == "scored" and isinstance(m["score"], (int, float))]
    return {"format": "sn-experiment-dashboard-v1", "runs": cells, "metrics": metrics,
            "summary": {"run_count": len(runs), "planned_outputs": sum(c["planned"] for c in cells),
                        "saved_outputs": sum(c["outputs"] for c in cells), "saved_scores": sum(c["scores"] for c in cells),
                        "mean_scored": sum(numeric) / len(numeric) if numeric else None,
                        "prediction_status": dict(statuses), "behavior": dict(errors)},
            "limitations": ["Scores are grouped by scorer; they are not averaged across metric families.",
                            "Missing, clarification, refusal and not_applicable records remain visible.",
                            "This report reads saved artifacts and never starts SN or calls a judge."]}


def write_dashboard(run_dirs: list[str | Path], output: str | Path) -> Path:
    data = aggregate_runs(run_dirs)
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    (output / "dashboard-data.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    html = _HTML.replace("__DATA__", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
    (output / "dashboard.html").write_text(html, encoding="utf-8")
    return output


_HTML = r'''<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>SN Experiment Dashboard</title>
<style>
:root{--bg:#07111f;--panel:#102038;--line:#294461;--text:#eef6ff;--muted:#9eb1c9;--cyan:#49dcff;--green:#69e6a4;--amber:#ffc857;--red:#ff718d}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 15% 0,#173457 0,#07111f 48%);color:var(--text);font:14px/1.5 system-ui,sans-serif;padding:28px;max-width:1500px;margin:auto}h1{margin:0;font-size:30px}h2{font-size:17px;margin:26px 0 10px}.muted{color:var(--muted)}.hero{display:flex;justify-content:space-between;gap:20px;align-items:end;margin-bottom:24px}.hero p{max-width:780px}.cards{display:grid;grid-template-columns:repeat(5,1fr);gap:12px}.card,.panel{background:rgba(16,32,56,.9);border:1px solid var(--line);border-radius:13px;padding:16px}.card label{color:var(--muted);display:block}.value{font-size:28px;color:var(--cyan);font-weight:700;margin-top:5px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}.barrow{display:grid;grid-template-columns:115px 1fr 45px;align-items:center;gap:8px;margin:10px 0}.bar{height:10px;background:#1b304b;border-radius:99px;overflow:hidden}.bar i{display:block;height:100%;background:linear-gradient(90deg,var(--cyan),var(--green));border-radius:99px}.donut{width:150px;height:150px;border-radius:50%;display:grid;place-items:center;background:conic-gradient(var(--green) var(--ok),var(--amber) 0 var(--warn),var(--red) 0);margin:10px auto}.donut:after{content:attr(data-label);width:96px;height:96px;border-radius:50%;background:var(--panel);display:grid;place-items:center;text-align:center;color:var(--text);font-weight:700}.legend{display:flex;justify-content:center;gap:15px;color:var(--muted);font-size:12px}.legend b{color:var(--green)}.legend b:nth-child(2){color:var(--amber)}.legend b:nth-child(3){color:var(--red)}table{border-collapse:collapse;width:100%;background:rgba(16,32,56,.9);border:1px solid var(--line);border-radius:13px;overflow:hidden}td,th{padding:10px 12px;border-bottom:1px solid #203853;text-align:left}th{color:var(--muted);font-weight:500}tr.clickable{cursor:pointer}tr.clickable:hover{background:#193452}.score{font-weight:700;color:var(--green)}select,button{background:#132a45;border:1px solid #416181;color:var(--text);border-radius:7px;padding:8px 10px}.flow{display:flex;align-items:center;justify-content:center;gap:5px;flex-wrap:wrap;padding:16px 0}.flow span{background:#163352;border:1px solid #3d6388;border-radius:20px;padding:7px 11px}.flow em{color:var(--cyan);font-style:normal}.detail{white-space:pre-wrap;background:#081421;border:1px solid var(--line);border-radius:9px;padding:14px;color:#cce3ff;max-height:260px;overflow:auto}.empty{text-align:center;color:var(--muted);padding:20px}@media(max-width:900px){.cards{grid-template-columns:repeat(2,1fr)}.grid{grid-template-columns:1fr}}@media(max-width:550px){body{padding:16px}.cards{grid-template-columns:1fr}.hero{display:block}}
</style>
<div class="hero"><div><h1>Silicon Notebook 实验看板</h1><p class="muted">把“题目 → SN → 评分 → 报告”压缩成一眼能读懂的实验全景。数据来自已保存 run；本页面不会重新执行实验。</p></div><div><button id="reset">清除筛选</button></div></div>
<div class="flow"><span>公开 Benchmark</span><em>→</em><span>隔离 notebook</span><em>→</em><span>SN Ask</span><em>→</em><span>chunk / reasoning</span><em>→</em><span>DeepEval + 审计</span></div>
<div id="cards" class="cards"></div>
<div class="grid"><section class="panel"><h2>回答状态分布</h2><div id="donut"></div><div id="legend" class="legend"></div></section><section class="panel"><h2>各指标表现</h2><div id="bars"></div></section></div>
<h2>指标明细 <select id="metric"><option value="">全部 scorer</option></select></h2><table><thead><tr><th>Suite</th><th>Task</th><th>Track</th><th>Mode</th><th>Scorer</th><th>状态</th><th>分数</th></tr></thead><tbody id="metrics"></tbody></table>
<h2>运行单元</h2><p class="muted">点击某一行查看该 run 的路径和警告。</p><table><thead><tr><th>Run</th><th>Suite</th><th>Track</th><th>Mode</th><th>状态</th><th>输出 / 计划</th><th>警告</th></tr></thead><tbody id="runs"></tbody></table><pre id="detail" class="detail">尚未选择运行单元。</pre>
<script>
const d=__DATA__,s=d.summary,$=id=>document.getElementById(id);const total=s.planned_outputs||0, saved=s.saved_outputs||0;function pct(a,b){return b?Math.round(a/b*100):0}$("cards").innerHTML=[['运行单元',s.run_count],['计划输出',total],['已保存输出',saved+' ('+pct(saved,total)+'%)'],['已保存评分',s.saved_scores],['平均已评分',s.mean_scored==null?'—':s.mean_scored.toFixed(3)]].map(x=>`<div class="card"><label>${x[0]}</label><div class="value">${x[1]}</div></div>`).join('');const st=s.prediction_status||{},ok=st.success||0,warn=(st.clarification||0)+(st.no_answer||0),bad=(st.error||0)+(st.product_error||0);$('donut').innerHTML=`<div class="donut" style="--ok:${pct(ok,saved)}%;--warn:${pct(ok+warn,saved)}%" data-label="${pct(ok,saved)}%<br>成功</div>`;$('legend').innerHTML=`<b>● 成功 ${ok}</b><b>● 待处理 ${warn}</b><b>● 错误 ${bad}</b>`;const grouped={};(d.metrics||[]).forEach(x=>{if(x.status==='scored'&&typeof x.score==='number'){const k=x.scorer||'unknown';(grouped[k]??=[]).push(x.score)}});const av=Object.entries(grouped).map(([k,v])=>[k,v.reduce((a,b)=>a+b,0)/v.length]);$('bars').innerHTML=av.length?av.map(([k,v])=>`<div class="barrow"><span title="${k}">${k.replace('product.','')}</span><div class="bar"><i style="width:${Math.max(0,Math.min(100,v*100))}%"></i></div><strong>${v.toFixed(3)}</strong></div>`).join(''):'<div class="empty">暂无已评分指标</div>';const names=[...new Set(d.metrics.map(x=>x.scorer).filter(Boolean))];$('metric').innerHTML+=[...names].map(x=>`<option>${x}</option>`).join('');function render(){const f=$('metric').value,m=d.metrics.filter(x=>!f||x.scorer===f);$('metrics').innerHTML=m.map(x=>`<tr><td>${x.suite||''}</td><td>${x.task||''}</td><td>${x.track||''}</td><td>${x.mode||''}</td><td>${x.scorer||''}</td><td>${x.status}</td><td class="${x.status==='scored'?'score':''}">${x.score==null?'—':Number(x.score).toFixed(3)}</td></tr>`).join('')||'<tr><td colspan=7 class="empty">暂无评分记录</td></tr>'}render();$('metric').onchange=render;$('reset').onclick=()=>{$('metric').value='';render()};$('runs').innerHTML=d.runs.map((x,i)=>`<tr class="clickable" data-i="${i}"><td>${x.run_id||x.path}</td><td>${x.suite||'—'}</td><td>${x.track||'—'}</td><td>${x.mode||'—'}</td><td>${x.phase}</td><td>${x.outputs}/${x.planned}</td><td>${(x.warnings||[]).length}</td></tr>`).join('');document.querySelectorAll('#runs tr').forEach(x=>x.onclick=()=>{$('detail').textContent=JSON.stringify(d.runs[x.dataset.i],null,2)});
</script>'''
