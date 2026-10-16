# Deferred notebook metadata implementation plan

> 2026-10-16 后续修正：用户要求优先快速完成实验。下文初版“不同策略不能合并”的设计已撤回；原始运行记录保留，导出允许旧结果与提速后剩余题合并。当前行为及服务器续跑见 `docs/deferred-notebook-metadata.md`。

**Goal:** 减少每题资料准备期间重复的整本元数据合成，保留完整语料和 SN 单篇处理流程。

**Architecture:** 在评测 runner 的串行上传循环内使用 SN 现有 metadata-only suppression context；全部资料完成 chunk/embedding 校验及索引构建后，在该 context 外调用一次 `repo._augment_notebook_meta(notebook_id)`。旧策略仍默认；新策略显式 opt-in，进入运行与提交身份。

**Tech Stack:** Python、pytest、SN repository/ContextVar、现有 CLI 与 JSON 工件。

**Spec:** 用户本轮批准的开发侧优化；约束以 `CURRENT_STATE.md` 为准。当前会话自行实施，不新建 subagent。

## 边界与关键假设

- 策略名：`per-source-metadata-v1`（默认）和 `deferred-notebook-metadata-v1`。
- 不修改 SN 主树，不开启缓存，不改变题内串行顺序、候选资料、摘要、论文元数据、embedding、Ask 或评分。
- 先检查 SN suppression/context 和最终刷新入口可用；不支持即失败，不默默回退。策略不匹配的已有准备状态拒绝复用。
- 导入失败/取消必须退出 suppression，保留已保存 mapping，不做最终刷新、不标 prepared。最终刷新沿用 SN best-effort；回执只说明调用及返回，不声称模型成功。
- 本地用真实 SN repository + 假模型客户端检验 API 假设；不调用真实模型。若最终刷新看不到全量摘要、上下文恢复失败或单篇处理发生改变，则停止采用该实现。
- 服务器 448→8 次 notebook_metadata 是短记录/默认分组的调用数推算；约 51→13 分钟不是实测收益。字符分组和服务负载可能改变结果。

## 实施与验收

- [x] `tests/test_notebook_preparation.py`：先覆盖完整导入、默认行为、失败/取消恢复、无效策略和旧状态冲突；执行红灯验证。
- [x] `src/rag_eval/benchmark_runtime.py`：新增策略校验；`with suppression:` 只包串行导入；校验及建索引后调用最终刷新并记录准备状态。
- [x] `notebook_runner.py`、`notebook_batch.py`、单次/批量 CLI：传递 `preparation_policy`，写入 runtime/manifest/job；在启动前拒绝无效值。完整 identity 自动参与提交族指纹；补充可读报告和错误信息。
- [x] 端到端 CLI fixture 验证策略从参数到 preparation、identity、submission；不同策略不能混成同一提交。真实 SN 离线 probe 验证 100 篇全部完成、summary/paper_metadata/embedding 不减少，metadata 从 448 降至 8。
- [x] 执行相关 Notebook、batch、submission、report/identity 回归；检查 diff，记录实际通过范围与尚待服务器实测的内容。
- [x] 更新 `CURRENT_STATE.md`、`docs/evaluation-status.md` 和运行说明；本轮不修改服务器排程及在跑任务，不重跑 13 个失败题。

验证入口：`PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_notebook_preparation.py tests/test_notebook_batch.py tests/test_notebook_v3_runtime.py tests/test_benchmark_submission.py`；真实 SN probe 使用独立子进程及临时数据库，输出只保留工作量计数与断言。

最终相关回归 364 passed；精确 SN `970b6793` 独立 probe 通过。开发交付及尚未运行的服务器验证见 `docs/deferred-notebook-metadata.md`、`docs/evaluation-status.md`。
