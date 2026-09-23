# Silicon Notebook RAG Benchmark + DeepEval

这是 Silicon Notebook 的离线评测仓库。目标是用公开任务、Notebook 场景和 Agent 轨迹解释系统的回答、检索、引用和评分表现，并保存可复查的输入、输出、配置和错误。

## 当前入口

先看[评测状态](docs/evaluation-status.md)和[文档导航](docs/README.md)。当前主线已经合并 Dashboard 实验地图和原生评分恢复；本地 `main` 尚未推送到远程，服务器实验状态仍需以实际产物核实。

当前主要命令：

- `scripts/build_experiment_dashboard.py`：只读已有 run，生成实验地图、单题回放和结果分析。
- `scripts/prepare_notebook_benchmarks.py`、`scripts/run_notebook_benchmarks.py`：准备和运行 QASPER、MultiHop-RAG、ALCE、QMSum。
- `scripts/run_notebook_baseline.py`：运行 QMSum BM25 + 显式生成模型对照。
- `scripts/run_notebook_agent.py`：运行 SN 原生 DeepEval 组件或显式完整轨迹评测。
- `scripts/score_native_components.py`：从已保存组件建立独立补评批次。

这些命令可能调用 Silicon Notebook 或模型服务。当前本地只做离线检查，不自动启动 SN、judge、下载、timer 或生产部署。服务器操作必须使用专题文档中的独立 run-dir、配置和身份。

## 文档分层

- [当前状态](docs/evaluation-status.md)：合并后的代码、验证证据和下一步。
- [稳定约束](docs/evaluation-context.md)：范围、证据分层、隔离和非目标。
- [文档导航](docs/README.md)：当前协议、专题说明和历史归档的索引。
- [历史归档](docs/archive/README.md)：旧交接、退役路径和阶段性设计，保留原始证据。

## 安装

```bash
cd /home/wabiwabi/silicon-notebook/benchmark-deepeval
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev,deepeval]'
```

完整离线测试需要 `dev,deepeval` 依赖，但不调用模型。原生 judge JSON 契约测试在同级存在 Silicon Notebook `project/` 时增加覆盖；缺少时按测试说明跳过。产品依赖、模型服务配置和密钥不随仓库分发。

## 测试

```bash
PYTHONPATH=. .venv/bin/pytest -q
node --test tests/dashboard_core.test.cjs tests/explorer_core.test.cjs tests/map_view.test.cjs
```

本地测试验证代码、协议和离线报告。它们不等于服务器模型或 judge 实验验收。

## 数据和结果

大体量数据、原始运行目录、产品源码快照、数据库、模型日志和私有候选资料保留在本机隔离目录，不随 Git 上传。结果记录必须区分本地核实、服务器转述、人工判断和暂定假设；错误、缺失和不适用不补零。

公开 benchmark、Notebook 和 Agent 轨道分别报告，不合成未经校准的总分或发布门槛。评测仓库地址为 [Xi0ng-Jun/SN_benchmark](https://github.com/Xi0ng-Jun/SN_benchmark)。
