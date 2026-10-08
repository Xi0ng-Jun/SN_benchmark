# 工程熵清单 TRIAGE

更新：2026-10-08，已同步共享工件、compact 导出和角色打包。工作树：`feat/benchmark-protocol-correctness`。删除事实与原因见 [DELETION_LOG](DELETION_LOG.md)，本页继续登记尚未解决的技术债。

**只维护 QASPER、MultiHop-RAG、ALCE、QMSum、HotpotQA。** 其他旧 benchmark 专属实现、入口、兼容、测试、配置、受跟踪样本／结果和文档已删除。共享 Agent、Dashboard、身份、runtime、模型适配和五套内比较继续保留。服务器、Git 外私有工件、虚拟环境／第三方包及上传包未清理。

## 1. 分类依据

| 分类 | 含义 | 处理规则 |
| --- | --- | --- |
| 保留 | 对当前目标、真实消费者或不变量仍有作用 | 保留职责和风险覆盖，不承诺旧签名／默认值永远不变 |
| 待删／改写 | 已确认失效的预期、分支、参数或断言 | 修改责任边界与对应测试，不为旧测试叠加兼容 |
| 未知 | 有历史用途，但当前保留义务尚不明确 | 查具体工件／消费者后决定，不能仅以“有测试”证明价值 |
| 已删除／完成 | 退役范围内已实施 | 只记录在删除日志，不再列成待办或创建永久旧套件 archive |

先看当前目标与官方标准，再核对生产者、消费者、身份、失败状态和分母，最后判断测试。测试通过不反向定义需求。对保留的五套，保存历史工件不自动要求新调用继续默认旧协议；退役套件不再维护 reader。

## 2. 规模与变化

下表保留 2026-09-29 清理时源码／测试／配置规模证据；不代表 2026-10-08 加入共享存储后的当前数量。物理行数不是复杂度，测试文件数也不是通过数。

| 范围 | 清理前 | 清理后 |
| --- | ---: | ---: |
| src Python | 87 文件 | 66 文件／11,071 行 |
| scripts Python | 42 文件 | 27 文件／1,362 行 |
| tests Python | 70 文件 | 50 文件／7,606 行 |
| lectures Python | 9 文件 | 8 文件／239 行 |
| configs | 15 文件 | 14 文件 |

清理前离线基线 769 passed、2 skipped；清理后验证见 DELETION_LOG。数量下降源于退役专属覆盖退出，不以维持旧通过数为目标。三个 Node 单测、浏览器测试和独立 SN 观测集成测试继续保留，后两者有额外运行前提。

## 3. 已完成的退役边界

### D00 · 仓内完成：五套之外的旧 benchmark 专属内容

旧套件的适配／scorer、Native/Product/Selection CLI、调度模板、受跟踪样本、历史结果、专属文档和旧格式补偿均已退出当前树；旧模块没有留下转发壳。共享职责迁为 `identity`、`trace_contract`、`run_support`、`run_results`、`run_report`、`runtime_environment`、`model_adapter`；judge/reference 配置迁为 `configs/model-roles.example.json`。

报告与计划边界拒绝非五套；Dashboard 测试用真正冻结并重建验证的 QASPER 输入，保留缺失输出、完整分母、来源篡改、补评去重、组件身份和安全展示。SN 的取消、澄清、完整回答和持久化覆盖迁到 `test_sn_execution.py`，共享账本／trace 覆盖见 `test_run_results.py`。

ALCE 子任务、官方 QA 权重、五套外部方法与 QMSum BM25 不属于退役内容。保存格式名 `public-starter-run-v1` 仍用于五套历史工件；保留这个标记不意味着支持旧套件。Git 外和服务器清理未完成，本轮不越界操作。

| 原编号 | 本轮状态 |
| --- | --- |
| T02／T03／T06 | 旧无效参数、套件适用性／选择转换、专属兼容与测试已随退役路径删除 |
| T07 的非五套部分／T08 | 旧执行／恢复入口、根配置示例、timer 模板已删除 |
| G01 的旧 suite 部分／G08 旧检查脚本 | 已删除；当前验收采用 RUNBOOK 中 Python、Node、显式集成与服务器步骤 |
| T10 | 当前默认、导航、评分边界和历史测试数字说明已更新；后续仍随代码维护 |

## 4. 仍需处理的假设与兼容

### T01 · 待删／改写：新 Python 调用隐式使用旧协议

**旧假设：** 省略版本参数意味着保持老调用效果。当前正式工作要求 notebook-data-v3／notebook-request-v3，CLI 已选择 v3，但下列 API 仍默认旧版：

- [notebook_data.adapt](src/rag_eval/notebook_data.py)、[notebook_bundle.prepare](src/rag_eval/notebook_bundle.py)：默认 adaptation 为 v2。
- [notebook_bundle.request_question／partition_bundle](src/rag_eval/notebook_bundle.py)、[notebook_runner.execute](src/rag_eval/notebook_runner.py)：默认 request 为 v1。execute 注释明确写着保留 old callers。
- v1／v2 的中间 request 还包含 references、gold_document_ids、expected_answer；v3 去掉这些字段，并隐藏来源于标注的题型。**这证明中间请求契约不同，不等于已经证明 gold 被送入模型提示。**

固化旧默认的具体测试：

| 测试 | 旧预期 | 应如何处理 |
| --- | --- | --- |
| [test_notebook_requests.py](tests/test_notebook_requests.py) 中 test_v2_changes_only_instruction_and_preserves_legacy_and_gold_boundary | 不传版本的 partition_bundle 等于显式 v1，旧 manifest 不含 request_revision | 新调用默认行为应改为保护 v3／无 gold；确需旧重建的部分显式传历史版本，并移到历史读取测试 |
| [test_notebook_data.py](tests/test_notebook_data.py) 中 test_new_bundle_rebuilds_whitespace_selection_and_freezes_revision | 不传版本的 prepare 生成 v2 | 删除“新建必须为 v2”的断言，保留 source 重建、空白处理和非法版本拒绝的有效覆盖 |
| [test_notebook_data_v3.py](tests/test_notebook_data_v3.py) 中 test_prepare_cli_defaults_to_v3_and_explicit_v2_still_rebuilds | 同时覆盖当前 CLI 默认和显式旧版 | v3 保留；显式旧版生成是否必须保留，按 T04 的实际工件需要判断 |

**建议清理边界：** 新建／执行 API 收敛到 v3；旧读取根据保存的版本显式重建。不要为了通过旧默认测试再加“自动识别调用者”或新的兼容开关。验收要覆盖 Python 与 CLI、一份 gold 扰动不影响新输入的反例，以及所保留历史工件的只读重建。新默认切换前应枚举仓内调用，不能只改常量而让历史 loader 隐式跟着变。

### T04 · 未知：五套内部历史读取与缺字段兼容

五套与共用历史仍按用户原决定保留并归档；其他 benchmark 的专属 reader 已按 D00 删除。历史 reader 的长期维护需要明确消费场景，不能用后者需求反向扩大范围。已找到：

| 实现／测试 | 当前能证明什么 | 最小核实与处理 |
| --- | --- | --- |
| [旧 QASPER fixture](tests/fixtures/notebook/legacy-qasper-bundle.json)、test_legacy_qasper_bundle_keeps_original_exclusions（[测试文件](tests/test_notebook_data.py)） | 存在受跟踪旧格式快照和重建检查 | 若只用于证明过去行为，可保留工件和固定旧 checkout；不自动要求新 prepare 继续默认生成旧格式 |
| test_saved_run_reconstructs_own_request_revision（[测试文件](tests/test_notebook_requests.py)）、[validate_saved_run](src/rag_eval/notebook_runner.py) | v1／v2 保存版本可回放，改动材料被拒绝 | 列出仍需被当前报告／导出读取的工件类型；有消费者则保留窄 reader，无消费者则归档相关实现／测试 |
| [run_results.py](src/rag_eval/run_results.py) 不再为缺失 task/applicability 补造身份 | 不把缺失观测伪装为已知适用性 | 该旧套件专属补偿已删除；Notebook 自首版即保存这两个字段，不受影响 |

本次未盘点服务器或所有被忽略历史 run，不能宣布五套的 reader 已无用途。后续可由开发者依据工件清单决定，不要求用户为了旧成绩重跑，也不默认要求保留旧执行入口。其他 benchmark 即使有旧 run，也不再属于新版支持义务。
### T05 · 未知：正式比较入口自动补全旧评分身份

[benchmark_comparison._validate_scores](src/rag_eval/benchmark_comparison.py) 允许部分旧 score 缺少 metric_denominators／metric_case_ids 时，从完整 case scope 推断。代码对检索、引用、AutoAIS 等有额外限制，并在报告披露 inferred_from_full_case_scope；它不是无条件猜分母。

[test_matching_conditional_subsets_and_legacy_full_scope_are_disclosed](tests/test_benchmark_comparison.py) 前半保护显式条件子集，后半又要求删除 metric_case_ids 后能够推断完整范围。应拆成独立判断：

- **保留**逐指标真实 eligible IDs、条件分母、两侧集合一致、缺条件身份拒绝。
- **未知**新版正式比较是否仍要接受缺身份字段的旧分数。先找具体已保存 score 消费者；没有需要则要求新输入显式完整字段，删除推断分支和对应旧断言。
- 确需旧输入时，可在显式迁移／导入边界补齐并保存依据，保留原工件；不要让核心比较函数不断猜更多旧形状。

目前有推断披露和防错边界，尚无证据证明这一路已产生错误比较；不能把“可收敛”写成“所有历史比较无效”。

### T09 · 未知：只有构造测试的 DAG 原型

[agent_dag.py](src/rag_eval/agent_dag.py) 在受跟踪 Python 的静态 import 图中只有 [test_agent_dag.py](tests/test_agent_dag.py) 这个消费者；测试验证 metadata 和 DAG 构造，用替身 judge，不证明真实评测价值。

当前 [evaluate_agent_traces.py](scripts/evaluate_agent_traces.py) 做确定性诊断，不调用这个 DAG；原生 Agent 的主路径也不能由此原型的测试推导出来。CURRENT_STATE 将 DAG 排除在本轮范围。

最小保留依据应是一项具体计划或消费者。没有依据时，可归档原型并删除构造专属测试；“DeepEval 有这个功能”和“已经有测试”都不足以要求继续维护。未核实外部动态加载，故本次不标为已证明的死代码。


### T07 · 未知：五套内部派生入口是否继续独立

QMSum 单会议 BM25 与统一 reference、ALCE 派生 run 评分与正式 submission scorer 有不同工件消费者。当前保留；按真实工件判断收敛，不能仅凭名字相近合并，也不把当前 SN-only 暂缓外部执行当作废弃比较目标。

### T11 · 未知：通用教学替身与演示的长期用途

lecture_01–03、`lectures/torch.py`／`sympy` 以及 `build_dashboard_demo.py` 保留通用教学／合成示例；读取退役结果的 lecture_04 已删除。其长期使用者仍待核实。教学替身只在讲义子进程路径中使用，不能放入正式 scorer 环境解决依赖问题。

## 5. 门禁与已复现的输入分叉

| ID／分类 | 当前入口 | 保证和限制 |
| --- | --- | --- |
| G01 · 保留 | notebook_bundle＋五套重建测试 | 哈希、canonical 重建、来源、范围及版本拒绝；只有外层哈希不够 |
| G02 · 保留 | notebook_runner＋runtime_environment | 新目录、进程隔离、源码／配置身份；不是完整 campaign 恢复调度器 |
| G03 · 保留 | benchmark_submission＋benchmark_comparison | v3 答卷、scope、真实分母与配对资格；不自动证明模型／预算一致 |
| G04 · 保留 | native_sdk／native_agent／native_component_scoring | 固定 SDK、真实观测、逐指标落盘、超时终态；不由通用 trace 自动推出 Agent 分 |
| G05 · 保留 | release_gate=False | 尚无校准后的发布阈值；不能为过测试改成 true |
| G06 · 保留，结构预检 | validate_external_campaign | 验注册、题单和可选 bundle IDs；不验证服务／GPU／模型／完整 scorer 或 full 放行 |
| G07 · 待修 | preflight `_ids` 与 runtime `load_case_ids_file` | 同一文件的空行／注释处理不同，本轮未改 |
| G08 · 旧入口已删 | RUNBOOK 的分层验证 | 保留当前五套、Agent、Node、显式 integration 和服务器验收职责 |

G07 的已复现反例：preflight 接受空行并忽略 `# note`，runtime 拒绝空白或带空格的 ID；仓内五份 smoke 文件在两者结果相同。应在输入边界采用同一个明确契约，不能追加只在预检开启的容错模式。本轮没有把旧 benchmark 删除扩大为该输入契约修复。

默认 pytest 仅收集 `tests/`。`integrations/silicon-notebook/test_native_agent_contract.py` 需配对产品 checkout；QMSum 真实 Perl 测试需显式环境。Mock／合成 fixture 不验证真实权重、SN 生命周期或超长资料。仓内没有统一 CI／环境锁；远程分支保护、仓外 CI、服务器依赖和运行状态未查询。

## 6. 配置与交付边界

SN TOML／.env、独立 judge JSON、campaign 登记和 scope 各有职责，保留各自身份。DeepEval 4.2.2 固定版本、官方 scorer／权重与 Perl 环境仍需核验；pip extras 不覆盖整个实验环境。

Git 外 frozen bundles、scorer、upload 包及 var 中私有资产未操作；旧服务器 unit 是否部署仍未知。本轮扩大 data 忽略规则以覆盖本地原始资产，避免删除旧样本后误把私有数据重新纳入 Git；测试 fixture 仍在 tests/fixtures。

后续建议顺序：先收敛 T01 新建默认和 G07 输入契约，再核对 T04／T05／T07 具体保留消费者，最后决定 T09／T11 原型／教学用途。当前五套核心不变量不能等到熵清理完成才修；有正确性反例就处理责任边界。

### S01 · 已实现边界，服务器预算仍待实测

`artifact_store/bundle_index` 安装一次不可变 bundle 与分区 capsule；新 run 显式 `--artifact-root` 复用输入和 evaluator/SN 源码，runtime 不共享。`run_reader` 的调用内 context、compact scoring projection、set/dict case 查找、补评/ALCE attachment 引用发布和 results/review 包已实现，不能继续按“每个新 run 必定复制全 input/source”分析成本。

仍未解决：服务器实际 input/source/runtime/outputs/agent 占比、I/O/RSS、安装/导出/打包耗时和全量验收；旧物理副本首次读取仍有完整校验成本，run 导入／建索引仍独立，campaign 任务仍手动展开。QASPER/Hotpot response/captures 是 observation hash 契约的一部分，不以 allowlist 再裁剪；如果成为主要体积，需要独立证据协议与等价性校准。

同次 context 不是持久可信缓存，bounded capsule 报告不是 canonical 全审计；`outputs_sha256` 是读取时身份，不能当作历史生成时签封。review 包按现有 reader/scorer 依赖复核，不提供 runtime 清理依据。无自动删除/TTL/GC；旧 QASPER recovery、失败和运行中数据库继续保留。服务器操作与失效模型见[结果存储与导出](docs/result-storage-and-export.md)。

## 附录：清理后测试、脚本与配置清单

下列清单只包含仍存在的文件。混合职责逐断言判断；分类“保留”不代表任何旧默认都合理。删除的文件及原因只在 DELETION_LOG 中逐项记录。

### Python 测试

| 分组／分类 | 文件 |
| --- | --- |
| Notebook 数据与请求 · 保留；旧默认见 T01 | [test_notebook_data.py](tests/test_notebook_data.py)、[test_notebook_data_v3.py](tests/test_notebook_data_v3.py)、[test_notebook_requests.py](tests/test_notebook_requests.py)、[test_notebook_v3_runtime.py](tests/test_notebook_v3_runtime.py)、[test_hotpotqa.py](tests/test_hotpotqa.py) |
| Notebook 运行与诊断 · 保留 | [test_notebook_runner.py](tests/test_notebook_runner.py)、[test_notebook_execution.py](tests/test_notebook_execution.py)、[test_notebook_scoring.py](tests/test_notebook_scoring.py) |
| 官方评分 · 保留 | [test_benchmark_official.py](tests/test_benchmark_official.py)、[test_multihop_official.py](tests/test_multihop_official.py)、[test_qmsum_official.py](tests/test_qmsum_official.py)、[test_alce_official_cli.py](tests/test_alce_official_cli.py) |
| 证据与排名投影 · 保留 | [test_qasper_evidence.py](tests/test_qasper_evidence.py)、[test_hotpot_evidence.py](tests/test_hotpot_evidence.py)、[test_sn_retrieval.py](tests/test_sn_retrieval.py) |
| 答卷、比较与案例复核 · 保留；推断见 T05 | [test_benchmark_submission.py](tests/test_benchmark_submission.py)、[test_benchmark_comparison.py](tests/test_benchmark_comparison.py)、[test_benchmark_review.py](tests/test_benchmark_review.py)、[test_external_submission_import.py](tests/test_external_submission_import.py) |
| 参考／外部方法 · 保留 | [test_benchmark_reference.py](tests/test_benchmark_reference.py)、[test_lab_qasper.py](tests/test_lab_qasper.py)、[test_kg2rag_runner.py](tests/test_kg2rag_runner.py)、[test_alce_vanilla.py](tests/test_alce_vanilla.py)、[test_alce_human_import.py](tests/test_alce_human_import.py)、[test_hotpot_native_import.py](tests/test_hotpot_native_import.py)、[test_multimeta_import.py](tests/test_multimeta_import.py)、[test_qmsum_socratic_import.py](tests/test_qmsum_socratic_import.py) |
| Agent、组件与运行观测 · 保留 | [test_agent_diagnostics.py](tests/test_agent_diagnostics.py)、[test_agent_trace.py](tests/test_agent_trace.py)、[test_evaluate_agent_traces.py](tests/test_evaluate_agent_traces.py)、[test_execution_trace.py](tests/test_execution_trace.py)、[test_native_agent.py](tests/test_native_agent.py)、[test_native_component_scoring.py](tests/test_native_component_scoring.py)、[test_notebook_agent_execution.py](tests/test_notebook_agent_execution.py)、[test_usage_capture.py](tests/test_usage_capture.py) |
| DAG 原型 · 未知（T09） | [test_agent_dag.py](tests/test_agent_dag.py) |
| Dashboard 与工件浏览 · 保留 | [test_dashboard_delivery.py](tests/test_dashboard_delivery.py)、[test_experiment_contracts.py](tests/test_experiment_contracts.py)、[test_experiment_dashboard.py](tests/test_experiment_dashboard.py)、[test_explorer_artifacts.py](tests/test_explorer_artifacts.py) |
| 旧 Notebook 派生评分／BM25 · 保留 | [test_notebook_baseline.py](tests/test_notebook_baseline.py)、[test_notebook_alce_results.py](tests/test_notebook_alce_results.py) |
| System 轨道 · 保留 | [test_system_capture.py](tests/test_system_capture.py)、[test_sn_execution.py](tests/test_sn_execution.py) |
| 基础数据与指标 · 保留 | [test_cases.py](tests/test_cases.py)、[test_datasets.py](tests/test_datasets.py)、[test_deepeval_runner.py](tests/test_deepeval_runner.py)、[test_metrics.py](tests/test_metrics.py) |
| 新增共享契约／支持范围 · 保留 | [test_run_results.py](tests/test_run_results.py)、[test_supported_scope.py](tests/test_supported_scope.py) |
| 共享存储、投影与回传 · 保留 | [test_artifact_store.py](tests/test_artifact_store.py)、[test_bundle_index.py](tests/test_bundle_index.py)、[test_scoring_projection.py](tests/test_scoring_projection.py)、[test_export_scaling.py](tests/test_export_scaling.py)、[test_result_package.py](tests/test_result_package.py) |

合计 50 个文件。

### 脚本

| 分组／分类 | 文件 |
| --- | --- |
| Notebook／Agent 主执行 · 保留 | [prepare_notebook_benchmarks.py](scripts/prepare_notebook_benchmarks.py)、[run_notebook_benchmarks.py](scripts/run_notebook_benchmarks.py)、[run_notebook_agent.py](scripts/run_notebook_agent.py)、[score_native_components.py](scripts/score_native_components.py) |
| 正式协议与比较 · 保留 | [benchmark_protocol.py](scripts/benchmark_protocol.py)、[compare_benchmark_submissions.py](scripts/compare_benchmark_submissions.py)、[review_benchmark_comparison.py](scripts/review_benchmark_comparison.py)、[validate_external_campaign.py](scripts/validate_external_campaign.py) |
| 参考／作者受控方法 · 保留 | [run_benchmark_reference.py](scripts/run_benchmark_reference.py)、[run_lab_qasper.py](scripts/run_lab_qasper.py)、[run_kg2rag.py](scripts/run_kg2rag.py)、[run_alce_vanilla.py](scripts/run_alce_vanilla.py) |
| 公开答卷导入 · 保留 | [import_alce_human_predictions.py](scripts/import_alce_human_predictions.py)、[import_hotpot_predictions.py](scripts/import_hotpot_predictions.py)、[import_multimeta_predictions.py](scripts/import_multimeta_predictions.py)、[import_qmsum_socratic_predictions.py](scripts/import_qmsum_socratic_predictions.py) |
| Dashboard／轨迹诊断 · 保留 | [build_experiment_dashboard.py](scripts/build_experiment_dashboard.py)、[evaluate_agent_traces.py](scripts/evaluate_agent_traces.py) |
| 旧 Notebook 补评／对照 · 保留 | [rescore_notebook_run.py](scripts/rescore_notebook_run.py)、[score_notebook_alce.py](scripts/score_notebook_alce.py)、[run_notebook_baseline.py](scripts/run_notebook_baseline.py)、[compare_notebook_baseline.py](scripts/compare_notebook_baseline.py) |
| 基础检索／质量评估 · 保留 | [run_project_retrieval.py](scripts/run_project_retrieval.py)、[run_deepeval.py](scripts/run_deepeval.py)、[generate_project_goldens.py](scripts/generate_project_goldens.py) |
| 演示与讲义 · 保留；长期用途见 T11 | [build_dashboard_demo.py](scripts/build_dashboard_demo.py)、[build_lecture_traces.py](scripts/build_lecture_traces.py) |

合计 27 个文件。

### 配置

| 分类 | 文件 | 依据 |
| --- | --- | --- |
| 保留 | [notebook-external-method-registry-v1.json](configs/notebook-external-method-registry-v1.json)、[notebook-external-campaign-v1.json](configs/notebook-external-campaign-v1.json)、[notebook-external-execution-plan-v1.jsonl](configs/notebook-external-execution-plan-v1.jsonl) | 方法／范围／任务分工不同；29 方法、5 scope、16 candidate、23 行任务。SN-only 只取 6 方法／12 行模板，不能执行全部外部计划。 |
| 保留 | [smoke-alce.txt](configs/comparison-scopes/smoke-alce.txt)、[smoke-hotpotqa.txt](configs/comparison-scopes/smoke-hotpotqa.txt)、[smoke-multihop.txt](configs/comparison-scopes/smoke-multihop.txt)、[smoke-qasper.txt](configs/comparison-scopes/smoke-qasper.txt)、[smoke-qmsum.txt](configs/comparison-scopes/smoke-qmsum.txt) | 分别 1／4／4／5／4 个 ID；当前文件与两种解析器一致，后续输入差异见 G07。 |
| 保留 | [multimeta-e77e4638-full.json](configs/comparison-scopes/multimeta-e77e4638-full.json) | 当前完整 2,556 题比较 scope。 |
| 保留并归档使用说明 | [multimeta-e77e4638-146.json](configs/comparison-scopes/multimeta-e77e4638-146.json) | 146 题历史验收身份；不作为当前 full 范围。 |
| 保留（外部执行暂缓） | [qasper-lab-source-lock.json](configs/qasper-lab-source-lock.json)、[alce-vanilla-source-lock.json](configs/alce-vanilla-source-lock.json)、[hotpot-kg2rag-train-demonstrations.json](configs/hotpot-kg2rag-train-demonstrations.json) | 作者源码锁和训练示例仍约束受控方法；它们不是整个 Python 环境的 lock。 |
| 保留 | [model-roles.example.json](configs/model-roles.example.json) | 显式 tested／judge 模型配置，由 reference／Agent／补评复用。 |

合计 14 个文件。Node／浏览器／独立集成检查的入口与额外前提见 [RUNBOOK](RUNBOOK.md)。
