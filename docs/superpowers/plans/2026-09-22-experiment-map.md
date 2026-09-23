# Experiment Map Implementation Plan

> For agentic workers: use superpowers:subagent-driven-development. The user has approved implementation; proceed without another approval gate.

**Goal:** QMSum 的实验地图、可回放单题流程、原生轨迹/组件分及同题比较能从同一页面联动访问。
**Architecture:** Python 验证工件并生成轻量索引/逐题数据片；纯 JS 选择状态与比较；HTML/CSS/SVG 呈现流程和详情。现有 aggregate_runs 公共离线逻辑继续复用，write_dashboard 改写生成 v3。
**Tech Stack:** Python stdlib、原生 JS/CSS/SVG、pytest、Node test、本地 Chromium。无新增运行依赖。
**Spec:** ../specs/2026-09-22-experiment-map-design.md

## Global constraints

只读工件，不下载数据或运行模型。run 外路径拒绝。完整原文在懒加载片中保存，摘要不塞证据。重评分与再次问答明确区分。原生 Agent 用 sn-deepeval-native-v1，旧树不迁移。当前分支 feat/dashboard-experiment-map，基于 e045114。

## Shared v3 contract

`write_explorer_data(run_dirs, output) -> dict` 写逐题 details/*.js 并返回索引（output 已由调用者新建）。索引保留 runs/entries/observations/catalog/summary/audit，format=sn-experiment-dashboard-v3；增加 graph={nodes,edges}。

- run 保留 key/run_id/suite/track/mode/phase/manifest/config_family，增加 kind=generation|rescoring、source_id、manifest_sha256、detail 信息/配置摘要。
- observation 索引：id/run_key/case_id/task/suite/mode/question（仅预览）/output_status/detail_file；不含全文。索引是以 id 为键的对象。
- entry 保留 id/observation_id/run_key/run_id/suite/task/track/mode/scorer/status/output_status/score/reason/config_family/pairing_id；增加 scope=answer|retrieval|synthesis|trajectory、metric_name、judge（若存在）、sample_id/span_id/request_id（若存在）。索引不放 plan/result 及完整理由，真实项留 detail.entries。
- detail：id、run_key、case_id、case（原始完整case）、output（普通输出）、events、entries（完整指标项）、run（配置和身份）、steps、native={manifest,components,scores,diagnostics,traces,errors,judge_events}。
- step：id/title/status(recorded|observed|missing)/description/input/output/artifacts（相对文件名数组）/code（文件路径数组）/fields（字段名→说明对象）。固定顺序 source/normalize/partition/import/retrieve/synthesize/answer/score；reasoning 子动作在 native trace 展开，不伪造固定流程时间。
- graph node：id/kind(dataset|run|answers|scores|external)/label/status/run_key（若存在）/source_id。edge：source/target/kind/provenance（依据说明）。
- 每个 details/*.js 使用 `window.SNExplorer.registerDetail(id, payload)`；UI 在加载之前注册此回调。payload 通过 JSON.parse 包装安全编码。URL hash 保存 view/run/case/step/span，未知 ID 提示并回到可用选择。

## Task 1 — artifact/index producer

Owner data agent：新建 src/rag_eval/explorer_artifacts.py 及 tests/test_explorer_artifacts.py，可拆 explorer_steps.py 归同一所有权。不修改现有生成器或前端。

- [ ] 先用构造 run 验证独立 answer vs rescoring 来源、native 多查询/分节/双快照关联，以及源码/产物越界失败。
- [ ] 实现上述接口，复用 aggregate_runs([run])、现有 redaction/loader，逐 run 释放全文；native JSONL 按题组织，未知/重复身份拒绝或明确记录，不静默混入。
- [ ] 索引没有证据正文；detail 保留完整字段。缺文件、空检索、部分运行有解释；文件来源与链接可追溯。
- [ ] 测试 fixture 包括篡改来源哈希、同名不同来源 case、重评分源未提供、缺 trace、脚本注入文本。

## Task 2 — interactive visual UI

Owner UI agent：dashboard/template.html/app.js/style.css；新增 explorer-core.js、tests/explorer_core.test.cjs。现有 core.js 保留并可调用；不改 Python。

- [ ] 按 shared contract 编写纯函数/测试：hash roundtrip、动态筛选、同题匹配、native 组件拒绝伪配对、不同资料/指标/judge 的原因。
- [ ] 主画布实验地图、流程阶段、原生树/详情联动；顶部三视图切换及全局当前 run/case，播放只改变步骤选择，支持键盘和 reduced-motion。
- [ ] 本地异步 detail loader，缓存且过期选择不会覆盖当前界面；URL 定位/复制与导出当前筛选可用。
- [ ] 分析视图保留动态标签、多组比较、配置和逐题跳转；错误/N/A不填0，题数与评分数分开。

## Task 3 — root integration and demonstrable delivery

Owner root：experiment_aggregation.write_dashboard、CLI、构造 QMSum demo 生成脚本、文档、浏览器验收。

- [ ] write_dashboard 安全新建 output 后调用 producer，嵌入轻量索引/资源；保留 dashboard-data.json/summary.json/audit.json 名称，更新旧输出说明。
- [ ] 提供清楚标为构造数据的离线演示，覆盖 chunk/reasoning/BM25、一次重评分、多span/组件、失败/缺分。真实 JSON 形状经现有 loader 验证，不调用模型。
- [ ] 用本地 Chromium 检验从打开到地图→题目→步骤→span→比较→分享/重载、窄屏、旧请求竞态及未加载全文行为；截图审阅。
- [ ] 相关 Python/Node 回归后运行全部 benchmark 离线检查；独立审查重要接口/来源和UI。修具体问题，不扩大SN测试。
- [ ] 更新 dashboard 指南、状态、README、服务器 prompt，提交本地分支，提供可打开演示与明确使用方式。
