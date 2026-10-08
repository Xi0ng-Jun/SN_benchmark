# Silicon Notebook RAG Benchmark + DeepEval

这是 Silicon Notebook 的离线评测仓库。目标是用公开任务、Notebook 场景和 Agent 轨迹解释系统的回答、检索、引用和评分表现，并保存可复查的输入、输出、配置和错误。

## 当前入口

先读 [CURRENT_STATE.md](CURRENT_STATE.md) 了解当前目标、不变量与执行范围；安装、启动、测试和服务器交付看 [RUNBOOK.md](RUNBOOK.md)，实现关系看[当前架构](ARCHITECTURE_CURRENT.md)，执行证据看[验证与实验记录](docs/evaluation-status.md)。当前主线已经合并 Dashboard 实验地图和原生评分恢复；Notebook 协议与比较实现位于 `feat/benchmark-protocol-correctness`，执行前核实实际 checkout，服务器状态以实际产物为准。

外部方法比较的最新本地结果与重放命令见[外部答卷与受控方法](docs/notebook-external-results-2026-09-28.md)：Multi-Meta-RAG 两模型完整 2,556 题、ALCE 八份样本答卷及 ASQA 文本配对、QMSum Socratic SegEnc 的 281 题 Perl 重评分，以及 QASPER LAB、HotpotQA KG2RAG、ALCE VANILLA 受控运行入口；正式 SN 对外部方法的比较仍待服务器运行。

当前主要命令：

- `scripts/build_experiment_dashboard.py`：只读已有 run，生成实验地图、单题回放和结果分析。
- `scripts/prepare_notebook_benchmarks.py`、`scripts/run_notebook_benchmarks.py`：准备和运行 QASPER、MultiHop-RAG、ALCE、QMSum、HotpotQA。
- `scripts/run_notebook_baseline.py`：运行 QMSum BM25 + 显式生成模型对照。
- `scripts/run_notebook_agent.py`：运行 SN 原生 DeepEval 组件或显式完整轨迹评测。
- `scripts/score_native_components.py`：从已保存组件建立独立补评批次。

这些命令可能调用 Silicon Notebook 或模型服务。当前本地只做离线检查，不自动启动 SN、judge、下载、timer 或生产部署。服务器操作必须使用专题文档中的独立 run-dir、配置和身份。

**2026-09-29 支持范围已确定只保留 QASPER、MultiHop-RAG、ALCE、QMSum、HotpotQA。** 其他旧 benchmark 专属代码、测试、配置、受跟踪数据／结果与文档已清理，详见 [DELETION_LOG.md](DELETION_LOG.md)；五套内外部方法、Agent 和 Dashboard 继续保留。删除边界与测试／门禁／兼容层盘点见 [TRIAGE.md](TRIAGE.md)。

## 文档分层

- [当前状态 SPEC](CURRENT_STATE.md)：目标、不变量、活跃约束、废弃假设、暂缓债务和非目标；开发首读页。
- [当前架构](ARCHITECTURE_CURRENT.md)：入口、数据存储、模块调用、依赖、演进和高风险修改边界。
- [验证与实验记录](docs/evaluation-status.md)：代码快照、已有验证证据和阶段进展。
- [稳定约束](docs/evaluation-context.md)：范围、证据分层、隔离和非目标。
- [文档导航](docs/README.md)：当前协议、专题说明和历史归档的索引。
- [历史归档](docs/archive/README.md)：五套与共用功能的旧交接和阶段性设计；退役套件专属文档已删除。

## 安装

```bash
# 在所选 evaluator checkout 根目录执行；已有 .venv 时不要覆盖。
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev,deepeval,notebook]'
```

完整离线测试需要 `dev,deepeval` 依赖，但不调用模型。产品观测集成测试与 Perl ROUGE 测试的额外前提见 RUNBOOK。产品依赖、模型服务配置和密钥不随仓库分发。

## 测试

```bash
.venv/bin/python -m pytest -q -rs
node --test tests/dashboard_core.test.cjs tests/explorer_core.test.cjs tests/map_view.test.cjs
```

本地测试验证代码、协议和离线报告。它们不等于服务器模型或 judge 实验验收。

## 数据和结果

大体量数据、原始运行目录、产品源码快照、数据库、模型日志和私有候选资料保留在本机隔离目录，不随 Git 上传。结果记录必须区分本地核实、服务器转述、人工判断和暂定假设；错误、缺失和不适用不补零。

五套 benchmark 的官方成绩与 Agent／组件指标分别报告，不合成未经校准的总分或发布门槛。评测仓库地址为 [Xi0ng-Jun/SN_benchmark](https://github.com/Xi0ng-Jun/SN_benchmark)。
