# 删除记录

日期：2026-09-29。工作树：`feat/benchmark-protocol-correctness`；删除前 HEAD：`86addcf421d627f62553204275b506b4d8bc022b`。本轮尚未提交或推送。

依据：用户明确只保留 **QASPER、MultiHop-RAG、ALCE、QMSum、HotpotQA**，并授权实际删除。仓内清理已执行：**95 个旧文件删除、6 个文件迁名，共 101 个旧路径退出当前树**。下表按删除前快照与当前文件系统逐项核对，不把迁名误写成共用功能消失。

本轮只处理评测工作树；未操作产品 checkout、服务器、Git 外私有数据、虚拟环境／第三方包或上传包，未运行实际 SN／生成模型／judge 实验。退役专属历史可由 Git 回溯，不另建永久旧 suite archive；五套与共用工程历史保留。仍未处理的五套内部默认／兼容问题见 [TRIAGE](TRIAGE.md)。

## 删除的文件

表中的路径为已删除文件，不提供失效跳转。

| 删了什么 | 为什么 |
| --- | --- |
| `src/rag_eval/starter_protocol.py` | 删除旧套件协议与选择逻辑；五套共用 fingerprint／require_text 原样迁入 identity.py。 |
| `src/rag_eval/starter_runner.py` | 删除旧套件执行／评分分支与审计参数；五套使用的状态、JSONL 读取和引用对象存在诊断迁入 run_support.py。 |
| `src/rag_eval/starter_native.py` | 退役 benchmark 专属适配、执行或兼容；五套共用职责已迁出 |
| `src/rag_eval/starter_product.py` | 退役 benchmark 专属适配、执行或兼容；五套共用职责已迁出 |
| `src/rag_eval/starter_not_applicable.py` | 退役 benchmark 专属适配、执行或兼容；五套共用职责已迁出 |
| `src/rag_eval/public_benchmarks.py` | 退役 benchmark 专属适配、执行或兼容；五套共用职责已迁出 |
| `src/rag_eval/public_expansion_protocol.py` | 删除旧扩展套件元数据／答案归一化；共用 TraceEnvelope 原样迁入 trace_contract.py。 |
| `src/rag_eval/public_expansion_native.py` | 退役 benchmark 专属适配、执行或兼容；五套共用职责已迁出 |
| `src/rag_eval/public_expansion_scoring.py` | 退役 benchmark 专属适配、执行或兼容；五套共用职责已迁出 |
| `src/rag_eval/public_expansion_sources.py` | 退役 benchmark 专属适配、执行或兼容；五套共用职责已迁出 |
| `src/rag_eval/public_selection.py` | 退役 benchmark 专属适配、执行或兼容；五套共用职责已迁出 |
| `src/rag_eval/selection_bundle.py` | 退役 benchmark 专属适配、执行或兼容；五套共用职责已迁出 |
| `src/rag_eval/selection_execution.py` | 退役 benchmark 专属适配、执行或兼容；五套共用职责已迁出 |
| `src/rag_eval/selection_partitions.py` | 退役 benchmark 专属适配、执行或兼容；五套共用职责已迁出 |
| `src/rag_eval/selection_report.py` | 退役 benchmark 专属适配、执行或兼容；五套共用职责已迁出 |
| `src/rag_eval/system_product.py` | 退役 benchmark 专属适配、执行或兼容；五套共用职责已迁出 |
| `src/rag_eval/system_scoring.py` | 退役 benchmark 专属适配、执行或兼容；五套共用职责已迁出 |
| `src/rag_eval/ifeval_protocol.py` | 退役 benchmark 专属适配、执行或兼容；五套共用职责已迁出 |
| `src/rag_eval/cli.py` | 退役 benchmark 专属适配、执行或兼容；五套共用职责已迁出 |
| `scripts/audit_public_benchmark.py` | 五套之外的旧评测命令／门禁退役；现行五套使用 Notebook 与官方协议入口 |
| `scripts/automate_public_benchmark.py` | 五套之外的旧评测命令／门禁退役；现行五套使用 Notebook 与官方协议入口 |
| `scripts/check_public_benchmark_offline.py` | 五套之外的旧评测命令／门禁退役；现行五套使用 Notebook 与官方协议入口 |
| `scripts/check_public_expansion_offline.py` | 五套之外的旧评测命令／门禁退役；现行五套使用 Notebook 与官方协议入口 |
| `scripts/plan_public_selection.py` | 五套之外的旧评测命令／门禁退役；现行五套使用 Notebook 与官方协议入口 |
| `scripts/prepare_public_benchmark.py` | 五套之外的旧评测命令／门禁退役；现行五套使用 Notebook 与官方协议入口 |
| `scripts/prepare_public_starter.py` | 五套之外的旧评测命令／门禁退役；现行五套使用 Notebook 与官方协议入口 |
| `scripts/report_public_benchmark.py` | 五套之外的旧评测命令／门禁退役；现行五套使用 Notebook 与官方协议入口 |
| `scripts/report_public_selection.py` | 五套之外的旧评测命令／门禁退役；现行五套使用 Notebook 与官方协议入口 |
| `scripts/report_public_starter.py` | 五套之外的旧评测命令／门禁退役；现行五套使用 Notebook 与官方协议入口 |
| `scripts/report_public_system.py` | 五套之外的旧评测命令／门禁退役；现行五套使用 Notebook 与官方协议入口 |
| `scripts/run_public_benchmark.py` | 五套之外的旧评测命令／门禁退役；现行五套使用 Notebook 与官方协议入口 |
| `scripts/run_public_retrieval.py` | 五套之外的旧评测命令／门禁退役；现行五套使用 Notebook 与官方协议入口 |
| `scripts/run_public_starter.py` | 五套之外的旧评测命令／门禁退役；现行五套使用 Notebook 与官方协议入口 |
| `scripts/run_public_system.py` | 五套之外的旧评测命令／门禁退役；现行五套使用 Notebook 与官方协议入口 |
| `tests/test_audit_public_benchmark.py` | 仅为退役 suite 的适配、scorer、旧调用和元数据服务；共用性质另迁测试 |
| `tests/test_automate_public_benchmark.py` | 仅为退役 suite 的适配、scorer、旧调用和元数据服务；共用性质另迁测试 |
| `tests/test_public_benchmarks.py` | 仅为退役 suite 的适配、scorer、旧调用和元数据服务；共用性质另迁测试 |
| `tests/test_public_system.py` | 仅为退役 suite 的适配、scorer、旧调用和元数据服务；共用性质另迁测试 |
| `tests/test_public_expansion_offline.py` | 仅为退役 suite 的适配、scorer、旧调用和元数据服务；共用性质另迁测试 |
| `tests/test_public_expansion_protocol.py` | 仅为退役 suite 的适配、scorer、旧调用和元数据服务；共用性质另迁测试 |
| `tests/test_public_expansion_reporting.py` | 仅为退役 suite 的适配、scorer、旧调用和元数据服务；共用性质另迁测试 |
| `tests/test_public_expansion_scoring.py` | 仅为退役 suite 的适配、scorer、旧调用和元数据服务；共用性质另迁测试 |
| `tests/test_public_expansion_sources.py` | 仅为退役 suite 的适配、scorer、旧调用和元数据服务；共用性质另迁测试 |
| `tests/test_expansion_completion_reporting.py` | 仅为退役 suite 的适配、scorer、旧调用和元数据服务；共用性质另迁测试 |
| `tests/test_starter_native_integrity.py` | 仅为退役 suite 的适配、scorer、旧调用和元数据服务；共用性质另迁测试 |
| `tests/test_public_selection.py` | 仅为退役 suite 的适配、scorer、旧调用和元数据服务；共用性质另迁测试 |
| `tests/test_selection_execution.py` | 仅为退役 suite 的适配、scorer、旧调用和元数据服务；共用性质另迁测试 |
| `tests/test_selection_partitions.py` | 仅为退役 suite 的适配、scorer、旧调用和元数据服务；共用性质另迁测试 |
| `tests/test_selection_report.py` | 仅为退役 suite 的适配、scorer、旧调用和元数据服务；共用性质另迁测试 |
| `tests/test_system_product.py` | 仅为退役 suite 的适配、scorer、旧调用和元数据服务；共用性质另迁测试 |
| `tests/test_system_scoring.py` | 仅为退役 suite 的适配、scorer、旧调用和元数据服务；共用性质另迁测试 |
| `config.example.json` | 旧 benchmark 专属配置／自动调度模板，不用于当前五套 |
| `configs/public-benchmark-v1.json` | 旧 benchmark 专属配置／自动调度模板，不用于当前五套 |
| `systemd/public-benchmark.service` | 旧 benchmark 专属配置／自动调度模板，不用于当前五套 |
| `systemd/public-benchmark.timer` | 旧 benchmark 专属配置／自动调度模板，不用于当前五套 |
| `data/public-benchmark-v1/manifest.json` | 退役 SQuAD／DROP 的受跟踪样本与 manifest |
| `data/public-benchmark-v1/drop/questions.jsonl` | 退役 SQuAD／DROP 的受跟踪样本与 manifest |
| `data/public-benchmark-v1/drop/documents.jsonl` | 退役 SQuAD／DROP 的受跟踪样本与 manifest |
| `data/public-benchmark-v1/squad/questions.jsonl` | 退役 SQuAD／DROP 的受跟踪样本与 manifest |
| `data/public-benchmark-v1/squad/documents.jsonl` | 退役 SQuAD／DROP 的受跟踪样本与 manifest |
| `src/rag_eval/span_evidence.py` | 仅服务已退役公开评测流水线的评分、比较或身份模块及其专属测试；现行 Notebook／Agent 使用独立实现 |
| `tests/test_span_evidence.py` | 仅服务已退役公开评测流水线的评分、比较或身份模块及其专属测试；现行 Notebook／Agent 使用独立实现 |
| `src/rag_eval/comparison.py` | 仅服务已退役公开评测流水线的评分、比较或身份模块及其专属测试；现行 Notebook／Agent 使用独立实现 |
| `tests/test_comparison.py` | 仅服务已退役公开评测流水线的评分、比较或身份模块及其专属测试；现行 Notebook／Agent 使用独立实现 |
| `src/rag_eval/quality_metrics.py` | 仅服务已退役公开评测流水线的评分、比较或身份模块及其专属测试；现行 Notebook／Agent 使用独立实现 |
| `tests/test_quality_metrics.py` | 仅服务已退役公开评测流水线的评分、比较或身份模块及其专属测试；现行 Notebook／Agent 使用独立实现 |
| `src/rag_eval/scoring_identity.py` | 仅服务已退役公开评测流水线的评分、比较或身份模块及其专属测试；现行 Notebook／Agent 使用独立实现 |
| `tests/test_scoring_identity.py` | 仅服务已退役公开评测流水线的评分、比较或身份模块及其专属测试；现行 Notebook／Agent 使用独立实现 |
| `src/rag_eval/benchmark_judge.py` | 仅服务已退役公开评测流水线的评分、比较或身份模块及其专属测试；现行 Notebook／Agent 使用独立实现 |
| `tests/test_benchmark_judge.py` | 仅服务已退役公开评测流水线的评分、比较或身份模块及其专属测试；现行 Notebook／Agent 使用独立实现 |
| `lectures/lecture_04.py` | 退役评测协议的历史混合结果或对应教学展示，不能作为当前五套标准化成绩 |
| `results/public-retrieval-50.json` | 退役评测协议的历史混合结果或对应教学展示，不能作为当前五套标准化成绩 |
| `results/public-retrieval-current.json` | 退役评测协议的历史混合结果或对应教学展示，不能作为当前五套标准化成绩 |
| `results/public-benchmark-status.md` | 退役评测协议的历史混合结果或对应教学展示，不能作为当前五套标准化成绩 |
| `docs/deepeval-public-starter-implementation.md` | 退役套件的协议、执行、评分、选择或历史验证说明；不再维护旧入口，当前五套与共用工程知识已在保留文档中整理。 |
| `docs/deepeval-public-starter-orchestration.md` | 退役套件的协议、执行、评分、选择或历史验证说明；不再维护旧入口，当前五套与共用工程知识已在保留文档中整理。 |
| `docs/deepeval-public-starter-plan.md` | 退役套件的协议、执行、评分、选择或历史验证说明；不再维护旧入口，当前五套与共用工程知识已在保留文档中整理。 |
| `docs/evaluation-case-walkthrough.md` | 退役套件专属示例／交互教程，不再作为当前实现说明。 |
| `docs/examples/drop-smoke-case.json` | 退役套件专属示例／交互教程，不再作为当前实现说明。 |
| `docs/ifeval-direct-scoring.md` | 退役套件的协议、执行、评分、选择或历史验证说明；不再维护旧入口，当前五套与共用工程知识已在保留文档中整理。 |
| `docs/offline-regression-2026-09-14.md` | 退役套件的协议、执行、评分、选择或历史验证说明；不再维护旧入口，当前五套与共用工程知识已在保留文档中整理。 |
| `docs/public-benchmark-agent-expansion-design.md` | 退役套件的协议、执行、评分、选择或历史验证说明；不再维护旧入口，当前五套与共用工程知识已在保留文档中整理。 |
| `docs/public-benchmark-execution.md` | 退役套件的协议、执行、评分、选择或历史验证说明；不再维护旧入口，当前五套与共用工程知识已在保留文档中整理。 |
| `docs/public-benchmark-selection-plan.md` | 退役套件的协议、执行、评分、选择或历史验证说明；不再维护旧入口，当前五套与共用工程知识已在保留文档中整理。 |
| `docs/public-system-plan.md` | 退役套件的协议、执行、评分、选择或历史验证说明；不再维护旧入口，当前五套与共用工程知识已在保留文档中整理。 |
| `docs/sn-public-system-adaptation.md` | 退役套件的协议、执行、评分、选择或历史验证说明；不再维护旧入口，当前五套与共用工程知识已在保留文档中整理。 |
| `docs/squad-evaluation-flow.html` | 退役套件专属示例／交互教程，不再作为当前实现说明。 |
| `docs/squad-flow-interactive.html` | 退役套件专属示例／交互教程，不再作为当前实现说明。 |
| `docs/superpowers/plans/2026-09-09-public-benchmark-evaluation.md` | 退役套件专属实施计划；共用 Agent／当前五套的计划与契约另行保留。 |
| `docs/superpowers/plans/2026-09-10-public-starter-implementation.md` | 退役套件专属实施计划；共用 Agent／当前五套的计划与契约另行保留。 |
| `docs/superpowers/plans/2026-09-10-public-starter-orchestration.md` | 退役套件专属实施计划；共用 Agent／当前五套的计划与契约另行保留。 |
| `docs/superpowers/plans/2026-09-11-public-benchmark-agent-expansion.md` | 退役套件专属实施计划；共用 Agent／当前五套的计划与契约另行保留。 |
| `docs/superpowers/plans/2026-09-13-expansion-code-completion.md` | 退役套件专属实施计划；共用 Agent／当前五套的计划与契约另行保留。 |
| `docs/superpowers/plans/2026-09-13-sn-public-system.md` | 退役套件专属实施计划；共用 Agent／当前五套的计划与契约另行保留。 |
| `docs/superpowers/plans/2026-09-14-public-selection-implementation.md` | 退役套件专属实施计划；共用 Agent／当前五套的计划与契约另行保留。 |

## 迁移共用职责，同时删除旧路径

以下不是删除当前能力；所有消费者已直接使用新模块，没有兼容转发壳。

| 删了什么 | 为什么 |
| --- | --- |
| `src/rag_eval/starter_results.py` → [run_results.py](src/rag_eval/run_results.py) | 保留五套／Agent／Dashboard 共用职责，去除旧套件分支或旧命名。 |
| `src/rag_eval/starter_report.py` → [run_report.py](src/rag_eval/run_report.py) | 保留五套／Agent／Dashboard 共用职责，去除旧套件分支或旧命名。 |
| `src/rag_eval/starter_runtime.py` → [runtime_environment.py](src/rag_eval/runtime_environment.py) | 保留五套／Agent／Dashboard 共用职责，去除旧套件分支或旧命名。 |
| `src/rag_eval/starter_model.py` → [model_adapter.py](src/rag_eval/model_adapter.py) | 保留五套／Agent／Dashboard 共用职责，去除旧套件分支或旧命名。 |
| `configs/public-starter-models.example.json` → [model-roles.example.json](configs/model-roles.example.json) | 保留 tested／judge 显式配置，名称不再暗示旧 benchmark。 |
| `tests/test_system_execution.py` → [test_sn_execution.py](tests/test_sn_execution.py) | 保留 SN 澄清、取消、完整输出、持久化和分母测试，移除旧 suite 专属 runner 断言。 |

## 混合文件内删除与同步

| 删了什么 | 为什么 |
| --- | --- |
| datasets.py 的旧三文件数据集 loader，test_datasets／test_experiment_contracts 的对应断言 | 仅为退役套件服务；保留 JSONL、Unicode 边界、通用 retrieval 指标、实际上下文和候选 gold 校验。 |
| benchmark_runtime.py 的旧环境／问答批处理／snapshot 包装 | 当前五套只需 read_json、prepare_notebook、evidence_checks；保留真实导入、索引、对象映射及证据校验。 |
| run_results.py 的旧 Native track、套件 scorer 默认、旧标签覆盖和缺 task/applicability 补偿 | 当前 Notebook 从首版即保存完整身份；新边界只接受五套 R 路径，不再用旧兼容补造缺失身份。 |
| run_report.py 的旧套件重建、Selection/System/IFEval/BoolQ 处理和旧指标表 | 只读取五套 Notebook／QMSum baseline，保留严格来源、计划、分母、状态和重评分校验。 |
| metric_catalog.py 的旧套件指标与 experiment_aggregation.py 的旧 Selection 分支／报告入口提示 | 展示范围必须与真实支持集合一致；保留五套诊断、官方评分边界与组件展示。 |
| Dashboard Python／Node 测试中的旧 suite fixture 和旧十套默认指标断言 | Python fixture 改为真正 prepare／partition／plan 的 QASPER 工件，补评也走现行 rescore_run；继续验证完整分母、篡改拒绝、来源差异、事件绑定和安全展示。 |
| pyproject.toml 的旧 rag-eval console entry point | 该命令调用退役 loader；通用 rag-eval-deepeval 与 Notebook scripts 保留。 |
| build_lecture_traces.py 默认列表中的 lecture_04 和 lectures/README 的旧结果指引 | 对应旧混合结果已删除；lecture_01–03 的通用教学保留。 |
| .gitignore 的旧 benchmark 专名数据规则 | 改为忽略 data 下本地资产（保留 data/README.md），继续阻止私有／大数据被意外纳入 Git；不删除忽略的数据。 |
| docs/ 下混合能力方案、指标说明、交接、学习页和归档导航中的旧套件内容 | 提取保留五套／Agent／Dashboard 的有用职责，去除失效入口、旧成绩和已删除文档链接。 |
| README、AGENTS、RUNBOOK、ARCHITECTURE_CURRENT、CURRENT_STATE、TRIAGE、evaluation-context/status、data/results/var README 的清理前状态和旧引用 | 当前文档反映实际删除与迁移，旧通过数标明历史，剩余债务仍保持待办。 |
| 外部方法 registry 和 Agent CLI help 的旧模型配置文件名 | 对齐 model-roles.example.json，显式模型身份和参数解析契约保留。 |

## 保留边界

- 五套数据、标准化评分、官方答卷、外部方法／reference、QMSum BM25、证据投影及检索排名链路保留。ALCE 的 ASQA／QAMPARI／ELI5 和官方 QA 权重 roberta-large-squad 不是退役对象。
- Agent 原生观测、组件补评、取消／迟到处理、Dashboard、runtime 隔离及结果落盘仍有当前消费者和离线覆盖。
- 五套已保存 run 的 `public-starter-run-v1`／report 格式标记保留；这是保存身份的一部分，不代表支持旧套件。五套 v1/v2 API 默认和 reader 收敛未在本轮处理。
- Git 外本地符号链接、原始 run、数据／模型缓存和服务器 unit 未删除；上传包也未重打包。当前源码清理不代表服务器部署已同步。

## 验证

- 删除前基线：Python **769 passed、2 skipped**。
- 新增“五套范围、拒绝退役输入”测试先失败后通过；复核后为缺完整计划身份增加两项反例，同样先失败再移除旧补偿。
- 删除后 Python 全量离线回归：**538 passed、1 skipped**；跳过项为显式本地 Perl ROUGE，未执行实际模型实验。Node Dashboard：**34 passed**。
- 独立代码复核：检查提取前后共享职责、调用方、遗留兼容和测试迁移；发现并修复缺字段补偿及旧配置引用，复核通过。
- 删除清单与前后快照核对：101 个旧路径全部对应本日志（95 删除＋6 迁名）；无未登记删除。
- 静态检查：152 个 Python 文件 AST 可解析、无失效内部导入；全库相对链接检查无仓内缺失目标，20 个既有产品同级链接需正确 checkout 拓扑（不是本轮删除引起）。11 个 CLI／子命令 help 检查通过；10 个受管 JSON 文件及全部 JSONL 可解析；26 段入口文档 shell 语法通过，git diff --check 无空白错误。
- 未验收：真实 SN／judge／官方模型评分、服务器 smoke/full、浏览器交互、产品跨仓库集成、环境安装和 wheel 构建。以上结果不能作为模型成绩或服务器实验完成证据。
