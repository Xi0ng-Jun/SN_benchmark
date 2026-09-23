# SN 执行轨迹交付验证

## 2026-09-22：原生 DeepEval 交付

当前实现见[原生评测协议](native-agent-evaluation.md)。SN 提交 `052b7373`，基于旧埋点提交 `1b4eb2b3`；只修改独立 `feat/evaluation-tracing` worktree。主目录 `project` 的 tracked 文件保持干净，既有未跟踪 `.deepeval/` 和 `results/` 未操作。实际开始时间为北京时间 08:56；此前没有建立零点定时任务。

| 检查 | 实际结果 | 范围与限制 |
| --- | --- | --- |
| benchmark 全部测试及跨仓库契约检查 | **419 passed，0 skipped，网络尝试 0** | 真实 DeepEval 4.2.2，judge/业务替身；包含普通 Notebook、旧答案报告/评分、组件/整轨迹、逐查询、错误/取消、持久化与身份 |
| SN 最终定向回归 | **32 passed** | 关闭时不导入 SDK/不改业务次数，真实业务投影，线程/异步上下文，分节/多查询，原始异常和实际流式取消子类；基础 SN 环境无需 DeepEval |
| SN 标准 gate：contracts | **54 passed** | 架构及契约检查 |
| SN 标准 gate：backend | **12762 passed，1 failed** | 既有 `test_packaged_migration_helper_runs_with_bundled_python_layout` 缺 dotenv；下方历史记录保留此前未修改基准上的复现证据 |
| SN 标准 gate：frontend | **Node 2730 passed；组件 1187 passed** | 构建与 TypeScript 检查也通过 |
| 增量补丁 | SHA256 校验及暂存索引重放通过 | 从 manifest 的 base 应用后 tree 与 `052b7373` tree 完全一致；不表示服务器不同基准没有冲突 |
| CLI / 静态检查 | help、diff --check 通过 | 不存在旧 `_trace_dict` 写入、手工 SDK 树转换路径 |

标准 gate 后针对审查发现的取消子类分类做了最后一次局部修正，以最终 32 项定向回归验证；未重复跑未改动的前端/契约。后端已有打包环境失败未借本次任务修改。不能把本交付说成“SN 全部测试通过”。

全量 benchmark 命令使用 `PYTHONPATH=.:src`、`SILICON_NOTEBOOK_PROJECT_ROOT=<SN worktree>`，并在进程启动前设置 `DEEPEVAL_TELEMETRY_OPT_OUT=YES`、`DEEPEVAL_DISABLE_DOTENV=1`、`CONFIDENT_TRACE_FLUSH=0`；调用 pytest 前拦截 socket connect/connect_ex/create_connection 并计数。测试目录为 `tests` 和 `integrations/silicon-notebook/test_native_agent_contract.py`，后者还需 `SN_EVALUATION_SOURCE=<SN worktree>`。

跨仓库检查真实加载 SN 的观测模块和真实 SDK，验证带原生父 span 的线程检索、真实上下文字段、回答先持久化、评分失败仍保留成功回答；业务与 judge 是本地替身，**不等于真实 SN 模型实验通过**。

代码审查修正了 SDK 挂接 test case 覆盖检索输出的问题（保留真实候选 ID/内容），以及流式取消被误记 error、外层重复 finish 可能覆盖取消的问题。未扩展混合/KG 检索；原生协议只覆盖所列具名边界，Notebook runtime 禁用 KG overlay。服务器必须先按最大题两模式，再 meeting18 完成真实验收。

未下载数据、启动真实 SN/在线 judge、部署生产、生成 Dashboard 或恢复 timer。旧结果归档/保留规则见当前协议。

## 历史：2026-09-21 旧采集器交付

下列证据只对应旧 `sn-execution-trace-v1`。旧命令已退役，不应用作新版本验收结果。

仅使用离线样本、测试替身和已安装依赖。没有下载 benchmark 数据、调用产品模型/judge、启动 SN 服务或 Dashboard、恢复 timer。

| 验证 | 结果 | 覆盖与限制 |
| --- | --- | --- |
| benchmark 相关回归 | 83 passed，1 条 SDK DeprecationWarning | 新旧轨迹、生命周期审计、真实 DeepEval 4.2.2 typed spans + 官方树投影、假 judge 跑实际 TaskCompletion/StepEfficiency、计划缺失/澄清 N/A、原 Notebook/System/BM25 路径 |
| SN 针对性回归 | 最新 89 passed；此前相关套件 290 passed | 含真实原生方法 + 假传输、计划/已确认意图、工具检索、合成、缓存/取消，以及异步任务/线程 ContextVar 隔离、关闭状态、无新增 I/O 和异常文本泄漏 |
| 跨仓库接口探针 | passed | 使用修改后的真实 SN collector，经过 benchmark `run_system_question` 记录澄清，再转换为 SDK 原生树；证实 intent 位于根内，澄清不调用 Ask |
| SN 标准 gate：contracts | passed，54 tests | 架构/契约脚本与相关检查通过 |
| SN 标准 gate：backend | **12754 passed，1 failed** | 唯一失败为既有打包迁移测试，见下文 |
| SN 标准 gate：frontend | passed | Node 2730 tests；Vitest 108 files / 1187 tests；生产构建与 TypeScript 类型检查通过 |
| Git 差异检查 | passed | 两仓库代码/文档 `git diff --check`；format-patch 生成文件保留 Git 上下文空白，通过补丁回放验证 |
| 补丁回放 | passed | 干净 `74e9c61e` worktree 上 `git apply --check` 与 `git am --3way` 成功，应用后的完整 Git tree 与实现提交一致；SHA256 校验通过 |

标准 gate 的失败是 `backend/tests/test_packaging_model_services.py::test_packaged_migration_helper_runs_with_bundled_python_layout`。测试将当前虚拟环境 Python 再软链接到模拟安装包，子进程无法找到 `dotenv`。在**不含任何本次修改的 `74e9c61e` 独立 worktree** 上只运行该项，同样复现 `ModuleNotFoundError: No module named 'dotenv'`。因此它是本机既有 Python/打包测试环境问题，本次未扩大范围修改产品打包逻辑，不能声称标准 gate 全绿。

benchmark 使用的验证命令：

```bash
DEEPEVAL_TELEMETRY_OPT_OUT=YES DEEPEVAL_DISABLE_DOTENV=1 \
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest \
  tests/test_execution_trace.py tests/test_agent_trace.py \
  tests/test_agent_diagnostics.py tests/test_agent_deepeval.py \
  tests/test_agent_dag.py tests/test_evaluate_agent_traces.py \
  tests/test_system_execution.py tests/test_system_capture.py \
  tests/test_notebook_execution.py tests/test_notebook_requests.py \
  tests/test_notebook_baseline.py -q
```

本机使用评测项目原有 `.venv/bin/python`，SN 使用其原有 `.venv/bin/python`。不把测试中的假 judge 分数当作质量实验结果。SDK 内部调用 `asyncio.get_event_loop()` 产生一条弃用警告，未掩盖或禁用测试。

补丁的 base/head 与 SHA256 见 [manifest](../integrations/silicon-notebook/manifest.json)。服务器还需要确认一个真实 QMSum 分区的两模式轨迹、实际采集体积和资源成本。模型环境、不同 SN 版本的三方合并及真实 judge 分数尚未在本机验证。
