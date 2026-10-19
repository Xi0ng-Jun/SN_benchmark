# 迭代树：Agent 链路与父/根比较记录 smoke（2026-10-19）

范围：补完[第一阶段 smoke](2026-10-17-iteration-identity-smoke.md)未做的 pi-flash 链路，并验收[设计](../product/sn-workbench/iteration-tree-design.md)第 10 节第 2 步（比较记录）。分支 `feat/workbench-iteration-tree`。本记录区分实验事实、限制与人工待判项；三题样本上的分差**不是**成绩结论。

## 环境变化（事实）

- 聊天模型由 `qwen-turbo` 换为 `qwen-flash`：同一 key 下 qwen-turbo 持续 429，用户 2026-10-19 决定更换。评测侧模型配置副本只改了 `model` 一行；预算守卫按官方第一档价（输入 ¥0.15、输出 ¥1.5／百万 token）计费，超出第一档的请求直接拒绝（`7dc120a`）。**与 10-13／10-14 的 qwen-turbo 结果不可比。**
- 另一 Agent 在 10-19 留下的未提交改动与 smoke state 未采用：代码存于分支 `wip/agent-b-snapshot`，state 归档为 `var/sn-workbench.agent-b-20261019`。本轮从干净 state 新建迭代。

## 链路（事实）

| 步骤 | 结果 |
| --- | --- |
| 新迭代 I1，selection_hash | `5cb19a8a…`（与 10-14 相同）；C0 冻结于起点 `995c9fc` |
| C0 attempt 1 | 3/3 success，answer_f1 0.2429（0.4225／0.1224／0.1837），¥0.037；单次准入 admitted，成为参考 |
| 分析 Agent（pi-flash，经工作台建会话、brief 作为上下文） | 读三题证据、只读定位 SN 代码，落一张挂在 C0 下的提议：答案合成缺少“先列作答要点、首句直答”的形状步骤；预测 `aebd1f0d`、`6e040e80` improve，`cb4086ad` not_worse（守护）；泄漏扫描 clean |
| 批准（smoke 操作者）→ 派生 A | A worktree 起点 = C0 冻结 commit |
| 实现 Agent（pi-flash） | brief 不含题面、case id、分数；只改 `prompt_layers.py` 的 L1 片段并新增合成提示合同测试；SN 相关测试 963 passed（1 个因快照缺脚本未选） |
| 冻结 A | `91eab80`，作者 `sn-eval-coordinator`，恰为上述两个文件 |
| A attempt 1 | 3/3 success，¥0.012；admitted |
| A 的 Δ父（= Δ根，父即根） | 记录 `99add591…`：0 好 0 平 3 差，均值 0.2429 → 0.2050（−0.038）；逐题 −0.042／−0.043／−0.029；重复请求返回同一记录 |
| 预测核对（经比较记录） | 0/3 兑现，守护题下降；核对写入 `comparison_id`；候选 worktree 泄漏扫描 clean |

答案词数（事实，单次运行）：C0 56／104／91 → A 14／196／127。卡片预期的“短答、首句直答”只在第一题出现，另两题反而更长。

## 遗留数据验收（事实）

在 live state 的只读副本上，对 10-14 迁移出的 R1 树（C0 人工节点 → A）生成 Δ父：C0 的两次执行中 importing 失败的 `b3edf004` 不准入，参考取恢复运行 `271ecd31`；记录为 2 好 1 差，逐题差 −0.006410／+0.023581／+0.682353，与 [10-14 报告](2026-10-14-r1-ordering-loop.md)逐位一致。遗留迭代没有根候选，Δ根 显示“不可用”。

## 本轮修复（均有回归测试）

- `sn-eval` 构造 IterationService 时没有执行记录，`hypothesis-draft --parent` 恒 409、`task` 把已完成候选显示为 failed（`b040375`）。
- 迭代 Task 的分析 brief 写“本任务未绑定”且只教旧草稿命令，照做会落成旧式 Task 卡而不是候选下的提议（`de38046`）。

## 限制与观察

- pi-flash 两个 Agent 各遇 1–2 次 provider 连接错误；工作台消息桥对 error 状态的 Agent 返回 `AGENT_UNAVAILABLE`，续接只能经 Paseo 直接发送，这一段消息不在工作台回执里。
- 同一源码同一模型重跑三题均值差约 0.017（本轮 C0 0.2429，另一 Agent 的同源运行 0.2260）。A 的逐题降幅约 0.03–0.04，与这一波动同量级；单次运行不足以区分改动效果与随机性。
- 比较记录按请求生成（读取不改写已有记录）；scorer／model／config 仍不在 preparation 契约内，配对准入是唯一的同构保证。

## 待人工判断

- 这张卡的结论（effect／cause）：预测全部未兑现、守护题下降、答案长度与预期相反；是否判为“否定”、是否需要重复测量，由人决定。
- 是否采纳／放弃 A（`decide`）。
