# SN 工作台：迭代与候选树

2026-10-20 起界面按“项目 → 迭代 → 候选树”组织（[设计](../../docs/product/sn-workbench/iteration-tree-design.md)第 7 节）。旧的工作／更改／结果／计划／记录五页签已删除，见 [DELETION_LOG](../../DELETION_LOG.md)；后端 HTTP 与 CLI 未删。视觉语言取 Linear，取用与偏离写在本目录 [DESIGN.md](DESIGN.md) 顶部。

## 启动

```bash
.venv/bin/python -m pip install -e '.[workbench]'
npm --prefix apps/sn-workbench ci
npm --prefix apps/sn-workbench run build
PYTHONPATH=src .venv/bin/python -m rag_eval.workbench.cli --state var/sn-workbench serve --config configs/sn-workbench-local.json
```

默认 `http://127.0.0.1:8091`；首次访问输入 state 目录下 `access-token` 中的访问码（不要把它或完整访问链接写进版本控制）。后端同时提供静态前端与 API：前端改动重新 build 后刷新页面，Python 改动需重启服务。

## 界面

- **左栏**：项目下的迭代（状态点：运行中／有待处理），以及“等你处理”——每条一句话，点开即跳到对应节点。底部是工作区设置。
- **中间：候选谱系**。树序（父在前、兄弟按派生时间），列出状态、参考运行的主指标、Δ父、Δ根、对父翻转计数；提议是虚线行。“按 Δ根 排行”是平铺视图，不改树序。勾选候选后“比较所选”展开逐题矩阵（列 = 所选候选 + 根，非 success 状态原样显示），点格子在右栏读该题答案、gold 与证据。
- **右栏：节点详情**。待确认请求、假设与逐题预测、对父的翻转（不可变比较记录）、相对根、代码差异、运行与准入（标出参考运行）、审批与结论。
- **底部 Agent 抽屉**（Ctrl/Cmd+J）：原对话、附件、Agent 新建与“发现”整体移入。头部写明它绑定的对象（分析 · 整个迭代／实现 · 候选 A）与可见范围；拖上沿调高度（记住），可最大化到整个工作区。实现 Agent 从候选详情“新建实现 Agent”创建；谱系行上的 Agent 标记（工作中为强调色）点开即切到该候选的实现 Agent。
- **Ctrl/Cmd+K**：跳到迭代或候选，或选择动作。Ctrl/Cmd+B 收起左栏。

**下一步从记录推出**：每个候选的下一步只从已记录的状态推断——候选状态、提议、请求、比较记录、实现 Agent 是否在工作、候选 worktree 有无改动——并分清在等你、等 Agent 还是等运行。等你的一步在谱系行下以琥珀色一行显示，详情里该动作是唯一的主按钮；打开迭代时（宽屏）自动落在最需要你的节点上。

**所有写操作都经确认框**：派生、冻结、运行、采纳／放弃、批准／驳回提议、确认／驳回 Agent 请求，框内列出对象、源码、题单与参数后才调用服务器。运行只提交超时，不设费用上限，因此没有费用账本。Agent 侧写动作只能申请（`sn-eval`），由人在这里确认后服务器按登记参数执行；本机无沙箱，请求账本记录谁申请、谁确认。

## 浏览器验收

`tests/tree-browser.mjs` 针对一个运行中的工作台（至少一个已评测子候选，例如 smoke state）。默认只读：两种主题的谱系、节点详情与比较记录、确认框取消不写入、排行不改树序、逐题矩阵与读题、Ctrl+K、Agent 抽屉、窄屏详情。给出 `WORKBENCH_CONFIRM_REQUEST`／`WORKBENCH_REJECT_REQUEST`（待确认请求 id）时经页面确认／驳回并核对回执。

```bash
WORKBENCH_TEST_URL='http://127.0.0.1:PORT/#access=CODE' npm --prefix apps/sn-workbench run browser-test
```

本机（WSL）Playwright 自带的浏览器版本与缓存不符且缺系统库：用 `WORKBENCH_CHROMIUM` 指向 `~/.cache/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell`，`LD_LIBRARY_PATH` 指向已解包的 `libnspr4`／`libnss3`／`libasound`，中文截图需让 fontconfig 能找到 CJK 字体（例如加入 `/mnt/c/Windows/Fonts`）。这些只影响测试环境，不改系统配置。截图写入 `test-results/`，不纳入 Git。

旧界面时期的验证与阶段记录见 `docs/superpowers/plans/` 与 Git 历史。
