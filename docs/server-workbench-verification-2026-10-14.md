# 服务器交付的本机验证记录

日期：2026-10-14。交付入口：[服务器 Agent 手册](server-workbench-handoff.md)。本文件所在 Git 提交为交付版本；服务器应记录实际检出的 SHA。这里没有远端实测结果。

## 本次改动

- 将此前已完成但尚未入 Git 的工作台、Agent bridge、源码/依赖身份、执行、比较、测试与相关文档一起纳入交付。SN 生产仓库和未采纳的排序实验候选不在本提交范围内。
- `configure_workbench_server.py init` 生成服务器私有配置，拒绝覆盖，使用既有 bundle loader/store 校验并登记实际引用。无模型执行配置、无猜测的 pi profile，初始执行保持关闭。
- `check` 校验登记工件；`--execution` 增加运行文件、目录隔离、解释器和声明语法检查。Paseo/凭据/模型/实际 SN import 保持未验证；scorer 源码哈希单独检查，见手册。
- 启动脚本兼容现有本机配置；新 clone 使用 server 配置/state。Agent 工具使用相同默认路径，并保留显式 config/state/python 覆盖。
- 忽略本机配置、旧 Web 构建快照、bridge 测试临时目录；没有提交凭据、数据库、原始实验工件或 node_modules。

## 已验证

1. **干净 Git 内容快照**：使用待提交 tree 的 `git archive` 导出独立目录，不包含本机配置、var 或构建缓存。Python 3.12 独立 venv（不继承系统包）安装 `.[workbench,dev,notebook]`，editable 指向该快照，`pip check` 通过。验证实际 `rag_eval.__file__` 在快照内。
2. **干净依赖构建**：两个 app 在快照内分别 `npm ci` 并构建通过。原目录的前端 typecheck/build、bridge typecheck 通过；bridge 离线测试 **92 passed**。Node 24.20.0；锁文件要求的最低版本为 22.18。
3. **快照定向回归**：setup、configuration、notebook_job、job_worktree_provenance、hypothesis_worktrees、plan_cli 共 **72 passed**。覆盖无本机配置启动、配置不可覆盖、工件篡改、空执行配置拒绝、解释器身份、输出隔离与 Agent 默认路径。角色/上下文相关切片另为 **21 passed**，有重叠，不相加。
4. **实际启动/API**：从快照启动 loopback 服务，读取构建 HTML 成功；未认证 bootstrap 被拒绝，认证 bootstrap 和创建任务成功；`scripts/sn-eval --task … task` 用服务器默认配置读到同一任务。验收后正常停止仅此测试服务。
5. **扩展回归**：当前工作树 workbench 与相关源码/模型/依赖身份测试首跑 **1,195 passed / 9 failed**。9 项均为 CLI 子进程没有 `PYTHONPATH` 且原 venv 未 editable 安装导致 `ModuleNotFoundError: rag_eval`，不是领域断言失败。以 `PYTHONPATH=src` 重跑对应 plan_cli 文件 **17 passed**；同一文件也在已安装的干净快照环境通过。没有声称全仓库或完整 SN backend 测试通过。
6. **独立审查**：迁移入口与 Agent 工具默认路径经只读复核，无 Critical/Important 遗留。scorer 目录存在不代表源码哈希通过的文档缺口已补上具体校验命令。shell 语法检查通过。

新增解释器测试曾发现 `sys.executable` 含 `..` 与配置标准化路径的比较不一致，已按照既有 runner 的解释器规则修复：executable 做 abspath、保留 venv symlink；prefix 做 resolve。新增 Agent 默认路径测试先复现找不到本机配置，再修复并通过。

## 尚未验证与限制

- 本机浏览器自动化尝试因 Chromium 缺少 `libnspr4.so` 未能启动；本次新快照的 UI 证据限构建和 HTTP/API 验收，不声称浏览器交互通过。既有前轮浏览器证据不替代本次检查。
- 新 Python 环境出现 Starlette/httpx 弃用提示，测试未失败；它不是完整 SN 运行依赖冻结。服务器安装所选 SN 的依赖后要再做 pip check 并保存实际 freeze。
- 未访问服务器，没有核验其 Paseo、输入文件、SN 版本、模型连接或实际评分。没有调用新模型、花费实验预算、迁移旧 state 或重启生产 SN。
- GitHub 只同步代码/文档。数据和模型配置按手册在服务器复用或重建；历史 `var/` 不随 Git 交付。首次付费实验需新题单与预算批准。

原始本机日志在被忽略的 `var/server-delivery/` 与 `var/workbench-server-*.log`。远端 Agent 重新执行后应保存自己的 `setup-report.md`，区分安装完成、接线完成与真实实验通过。
