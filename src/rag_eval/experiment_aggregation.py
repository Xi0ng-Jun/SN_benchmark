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
<style>body{margin:0;background:#091323;color:#eaf2ff;font:14px system-ui;padding:24px}h1{margin:0 0 4px}p{color:#9db0ca}.cards{display:flex;gap:12px;flex-wrap:wrap}.card,table{background:#122139;border:1px solid #284363;border-radius:10px}.card{padding:14px;min-width:150px}.value{font-size:25px;color:#55dcff}table{border-collapse:collapse;width:100%;margin-top:16px}td,th{padding:9px;border-bottom:1px solid #284363;text-align:left}th{color:#9db0ca}.ok{color:#65e6a2}.warn{color:#ffc857}select{background:#122139;color:#fff;border:1px solid #426184;padding:7px;border-radius:6px}</style>
<h1>Silicon Notebook 实验结果汇总</h1><p>离线读取已保存 run；不重新执行 SN、不调用模型。筛选后点击表格行查看单次运行。</p><div id="cards" class="cards"></div><p>指标筛选：<select id="metric"><option value="">全部 scorer</option></select></p><table><thead><tr><th>Suite</th><th>Task</th><th>Track</th><th>Mode</th><th>Scorer</th><th>Scored</th><th>Mean</th></tr></thead><tbody id="metrics"></tbody></table><h2>运行单元</h2><table><thead><tr><th>Run</th><th>Suite</th><th>Track</th><th>Mode</th><th>状态</th><th>输出/计划</th><th>警告</th></tr></thead><tbody id="runs"></tbody></table><pre id="detail" style="white-space:pre-wrap;background:#07101e;padding:14px;border-radius:8px"></pre>
<script>const d=__DATA__,s=d.summary;$('cards').innerHTML=[['Run',s.run_count],['计划输出',s.planned_outputs],['已保存输出',s.saved_outputs],['已保存评分',s.saved_scores],['已评分均值',s.mean_scored==null?'—':s.mean_scored.toFixed(3)],['状态',Object.entries(s.prediction_status).map(x=>x.join(': ')).join(' · ')||'—']].map(x=>`<div class="card"><div>${x[0]}</div><div class="value">${x[1]}</div></div>`).join('');const names=[...new Set(d.metrics.map(x=>x.scorer).filter(Boolean))];$('metric').innerHTML+=[...names].map(x=>`<option>${x}</option>`).join('');function render(){const f=$('metric').value;const m=d.metrics.filter(x=>!f||x.scorer===f);$('metrics').innerHTML=m.map(x=>`<tr><td>${x.suite||''}</td><td>${x.task||''}</td><td>${x.track||''}</td><td>${x.mode||''}</td><td>${x.scorer||''}</td><td>${x.status}</td><td>${x.score==null?'—':Number(x.score).toFixed(3)}</td></tr>`).join('')||'<tr><td colspan=7>暂无评分记录</td></tr>'}render();$('metric').onchange=render;$('runs').innerHTML=d.runs.map((x,i)=>`<tr data-i="${i}"><td>${x.run_id||x.path}</td><td>${x.suite||''}</td><td>${x.track||''}</td><td>${x.mode||''}</td><td>${x.phase}</td><td>${x.outputs}/${x.planned}</td><td>${(x.warnings||[]).length}</td></tr>`).join('');document.querySelectorAll('#runs tr').forEach(x=>x.onclick=()=>{$('detail').textContent=JSON.stringify(d.runs[x.dataset.i],null,2)});function $(id){return document.getElementById(id)}</script>'''
