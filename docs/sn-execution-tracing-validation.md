# SN 执行轨迹交付验证（2026-09-21）

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
