# 评测状态

核实基线：2026-09-23（北京时间）。本页是当前快照；详细历史、旧命令和交接上下文见[文档导航](README.md)和[历史归档](archive/README.md)。

## 当前代码与 Git

- 本地 `benchmark-deepeval/main` 已合并 Dashboard 和原生评分恢复，HEAD 为 `03ca577`。
- 本地 `main` 相对 `origin/main` ahead 58；本轮没有推送，因此远程 `main` 仍为 `8b4b4de`。
- Dashboard 合并提交为 `e4c5333`，原生评分恢复合并提交为 `03ca577`。
- 远程开发分支仍保留：Dashboard 为 `a85fdef`，原生评分恢复为 `4d5c685`，公开 Agent 扩展为 `e045114`。
- 主线受管理文件干净。原生评分 worktree 另有 17 个未跟踪汇报图文件；它们不在主线提交中，也未清理。

## 当前可用入口

### Dashboard

`scripts/build_experiment_dashboard.py` 读取已有 run，生成实验地图、单题流程/原生 span 回放和结果分析。它不会重新问答、重新评分或修改原始工件。使用和比较限制见[实验 Dashboard](experiment-dashboard.md)。

### Notebook 场景

`scripts/prepare_notebook_benchmarks.py`、`scripts/run_notebook_benchmarks.py` 和 `scripts/run_notebook_baseline.py` 支持 QASPER、MultiHop-RAG、ALCE、QMSum 及 QMSum BM25 对照。完整资料、分区和请求版本必须按[Notebook 场景协议](notebook-benchmarks.md)记录。

### 原生 Agent 与组件补评

`scripts/run_notebook_agent.py` 使用 `sn-deepeval-native-v1`，默认评检索/合成组件；`--trajectory` 才评完整 Agent 轨迹。回答和组件先保存，judge 随后评分。`scripts/score_native_components.py` 可从已保存组件建立独立补评目录。入口、失败语义和超时边界见[原生 Agent](native-agent-evaluation.md)与[评分恢复](native-scoring-recovery.md)。

## 验证证据

本次合并后的本地回归：

- Python：`PYTHONPATH=. .venv/bin/pytest -q`，**447 passed**。
- Dashboard JavaScript：`node --test tests/dashboard_core.test.cjs tests/explorer_core.test.cjs tests/map_view.test.cjs`，**34 passed**。

这些检查验证代码和离线协议，不证明服务器模型、judge 网关或真实数据实验成功。历史本地验证按当时分支和依赖记录，见专题文档和归档索引。

## 服务器实验边界

服务器反馈曾报告 QMSum request-v2、BM25、DeepSeek Agent 批次和原生评分超时恢复。数字来自用户转述，本机没有读取服务器原始 run、当前进程或配置；不能把“最近正在运行”当作此刻仍在运行，也不能把缺分当作零分。

服务器下一步应先使用已保存答案/组件验证短样本评分链路，再决定是否扩大规模。不要重复启动已经交给服务器执行的任务，不要在本机下载数据或调用模型。

## 当前下一步

1. 用服务器实际产物核对短文档样本的运行身份、答案、组件和分数。
2. 用合并后的 Dashboard 只读展示身份明确的结果，确认实验地图、单题回放和对比视图是否足够解释。
3. 若补评目录不能直接进入 Dashboard，记录为具体协议缺口，不改数据伪装成普通 run。
4. 根据真实失败案例决定 judge 链路修复或扩大实验；不先重跑全部历史问答。
