# 实验结果探索器实施计划

> 执行方式：使用 subagent-driven-development 分离前端实现与本地数据汇总，按接口合并审阅。用户已要求实现、验证并同步远程；本轮不运行在线实验。

**Goal:** 按 benchmark/task/track/mode/scorer/status 等标签任意组合筛选，展示分布、逐题评分依据和多组可比结果。

**Architecture:** 沿用 load_run 校验保存的运行，以 planned.jsonl 为基表左联接输出与评分。Python 生成自包含 HTML/JSON；浏览器用纯 JavaScript 筛选、绘图、保存比较组，离线打开，无 CDN、无 SDK 执行。

**Spec:** 本轮用户明确的四列表、自由组合标签、可视化分析、条目详情与可比多组比较即功能要求。

## Global Constraints

- 不修改 SN、运行 ledger 或重新评分；只读已保存产物。
- 条目 = run × case × scorer；问答次数 = 去重后的 run × case。缺失分数保留空值和计划分母。
- 不提供混合指标总均分。汇总按 suite/track/scorer/config_family 分面，再按 mode/task 等分组。
- 比较锁定 suite/track/scorer/config_family，配对采用 pairing_id + case_id + scorer；重复题、不同资料/版本、缺身份、无交集明确阻止配对；非共同题不能用于模式差值。
- 自包含页面不联网；结构字段中的密钥和服务地址脱敏；文本安全渲染。
- 真实 judge 调用和原始分数可查看；不存在的评价过程标记未保存，不伪造 LLM 内部思考。

## Task 1：评分目录与运行数据

文件：experiment_aggregation.py、metric_catalog.py、tests/test_experiment_dashboard.py、docs/benchmark-metrics-reference.md。

- [x] 写可执行合成 run 回归：多 scorer 不膨胀问题数；漏评分仍有条目；完整记录 drilldown；错误 ledger 拒绝；发现目录不混入 input manifest。
- [x] 提供 dashboard v2 数据：runs、entries、observations、catalog、summary、warnings；保留来源原始 case、request/response、context、citations、scores/details、匹配 model-events。
- [x] 建立指标计算说明及四列表，纠正之前把 Answer Relevancy/Contextual 当成新十套默认输出、DROP Product 被说成自动数值 exact match 的表述。

## Task 2：浏览器探索界面

文件：src/rag_eval/dashboard/{template.html,style.css,core.js,app.js}、tests/dashboard_core.test.cjs。

- [x] 先 Node 回归组合筛选、去重分母、多指标禁止混合、严格配对及重复运行。
- [x] 多维多选（同字段 OR、字段之间 AND），全文搜索；清除/导出当前选择；有数量的状态图与分数直方图、分面条形图。
- [x] 条目分页、键盘可打开详情；详情显示输入、响应、上下文、引用、原始结构、judge 事件、公式/步骤和评分缺失原因。
- [x] 保存多个标签组合，显示每组分布/均值和样本数；可比组计算共同有效配对均值差、wins/ties/losses 与缺失数量，不做因果或显著性声明。

## Task 3：集成与交付

- [x] CLI 沿用旧命令；输出 dashboard.html、dashboard-data.json、summary.json、audit.json；增加打包静态资源。
- [x] 合成数据端到端生成和浏览器点击验证，检查 HTML 安全、空状态、手机宽度，保存示意预览（明确非实验成绩）。
- [x] 更新 README、使用说明、服务器 Agent prompt、状态文档；针对性检查通过后提交推送原分支。

## 接口约定

entry: {id, observation_id, run_key, run_id, suite, task, track, mode, scorer, status, output_status, behavior, partition, phase, config_family, pairing_id, case_id, score, reason, plan, result}。
observation（字典，键 observation_id）: {case, output, events, warnings}。
run: {key, path, run_id, suite, track, mode, phase, planned_predictions, planned_scores, saved_outputs, recorded_outputs, recorded_scores, warnings, manifest}。
catalog（键 scorer）: {name, method, implementation, inputs, formula, limitations, code}。
mode 的 Native 值为 native。scoring status 的缺失值为 missing。config_family 保留数据/模型/代码/配置/审计身份；非 selection 运行还保留完整 product_bundle。经过 loader 核验的 selection 运行以共同 partition_plan_sha256 等 selection_context 字段替代分区 product_bundle，仅移除 partition_id/case_ids。不同候选库或不同分区计划不能混算，具体配对仍必须匹配完整 pairing_id。此处按独立审阅修正了最初直接删除 product_bundle/selection_context 的定义。

## 接口审阅

| 接口 | 决定 |
|---|---|
| Task 1 → Task 2 | 数据结构固定如上，缺字段显示未记录，前端不猜测评分 |
| Task 2 → Task 3 | 单 HTML 内联全部资源，JSON 脚本转义 <；脚本只用安全 DOM/textContent |
| Task 1 自洽 | 汇总保持 planned 分母，与现有 loader 的完整性校验兼容 |
| Task 2 自洽 | 可任意筛选，但仅可比的同口径群组才能计算差值 |
| Task 3 自洽 | UI 合成测试不视为真实 benchmark 实验 |

Ruling: 用户已授权的 Dashboard 改进包括离线测试和远程同步；保留服务器实验运行状态，不在这里重复实验。

## 实施验证（2026-09-16）

- 26 项 Python 回归通过：新 Dashboard + selection/report 兼容检查。
- 13 项 Node 回归通过：组合筛选、去重、不同口径隔离、150k 分数分桶、缺身份/重复题/共同题比较。
- Chromium 浏览器验证通过：实际生成的自包含 HTML，标签、图表、比较、六类详情、导出、搜索、Native、judge、HTML 注入文本、手机宽度；页面脚本错误和 HTTP 请求均为 0。
- 本地 wheel 构建成功，包含 template.html/style.css/core.js/app.js。没有执行 SN 或任何模型评分。
- 独立审阅修正：SVG 渲染调用错误；同名不同 run 的身份选择；候选库和分区计划身份被过度省略的问题。
- 合成预览保存在忽略目录 var/dashboard-demo-v2/final，明确标注非真实实验成绩；正式服务器报告需读取真实 run 重新生成。
