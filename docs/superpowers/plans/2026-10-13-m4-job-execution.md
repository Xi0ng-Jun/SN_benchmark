# R1 M4：工作台 Job 可执行闭环

## 目标

让工作台的“验证”动作从已验证的固定计划生成受信 argv，在独立 run-dir 中启动现有 benchmark runner，并持续读取 `state.json`、结果工件和 Job 事件。完成、澄清、错误、缺失、超时和取消都保留 runner 的真实状态与已有工件。

## 契约与边界

- 旧的 `sn-workbench-evaluation-job-v1` 记录保持可读；执行能力以新的 execution record/事件层加入，不重写历史 receipt。
- 只有固定 plan、成功的 input/source preparation、显式预算确认和当前 Job 版本匹配时才能派发。
- dispatcher 只接受后端构造的 argv；启动前只校验 run-dir 为新的独立路径，由 runner 首次创建它，避免触发 runner 的禁止 resume 保护；不接受 UI 任意命令或路径。
- 一个 Job 对应一个独立 run-dir 和一个受控进程句柄。状态事实优先来自 runner 的 `state.json`，Job 状态只表示派发/进程生命周期。
- 取消只终止该 Job 的进程组，保留 run-dir；已终态运行不可被取消覆盖。
- request key 对派发和取消分别幂等；状态变更采用当前 Job 版本检查。
- UI 只读取 Job/run 状态与结果，不重新计算官方评分。

## 实施步骤（TDD）

1. 为执行规格、预算确认、派发状态、run-dir 和状态投影增加最小失败测试；覆盖未准备、预算缺失、旧版本、重复 request key、独立目录、终态取消保护。
2. 实现 execution record 的持久化与离线校验，复用现有 preparation receipt 中的 bundle/index/SN source 绑定，生成冻结 argv 和预算摘要。
3. 实现受信 dispatcher：使用 `subprocess.Popen` 的独立进程组写入启动回执，后台监视 `state.json`/退出码，持久化 running/finished/failed/cancelled 投影，保留输出。
4. 增加 HTTP `run`、`cancel`、`status` 端点和 CLI `job-run`/`job-status`，保持既有 prepare/cancel/receipt 兼容。
5. 将 `RunViews._verification` 暴露为可执行，并让 Changes 页面根据状态显示运行、进度、结果回访和取消动作；轮询使用现有 run-view API。
6. 增加后端、API、CLI、RunViews/UI 定向测试；运行全套相关测试、浏览器检查和 `git diff --check`。
7. 更新 `goal-and-roadmap.md`、`CURRENT_STATE.md` 或 `docs/evaluation-status.md`，仅记录实际 smoke 结果和未验证边界。

## 验收

- 从一个已绑定 Task 的固定 plan 在 UI/API 发起一次运行，能看到固定 Job/argv/预算、独立 run-dir 和实时状态。
- runner 完成后答案、证据、`state.json` 和官方评分可通过现有 RunViews 回访；clarification/error 等状态原样可见。
- 取消运行后进程结束、状态和已有工件保留；重复派发/取消请求不会创建第二个 run 或覆盖终态。
- 未满足准备或预算条件时无进程、无 run 工件、无状态写入。
