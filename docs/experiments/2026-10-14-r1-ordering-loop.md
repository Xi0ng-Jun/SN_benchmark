# R1 固定三题：证据呈现顺序实验（2026-10-14）

本轮已完成用户批准的第 1–3 步并停止：从批准假设卡创建独立 worktree；真实 pi 分析与实现；同一固定计划运行前后、官方评分、逐题预测与守护核对。**候选未达到预注册采纳标准，保留在实验 worktree，不合并、不推广。** 这是单论文三题的一次实验，不代表 R1 所有能力或正式 benchmark 成绩已完成。

## 结果与结论

| 固定案例 | 预注册预测 | 基线 answer_f1 | 候选 answer_f1 | 差值 | 核对 |
| --- | --- | ---: | ---: | ---: | --- |
| A · `6e040e80` | improve | 0.256410 | 0.250000 | -0.006410 | 未兑现；仍未列出具体条目 |
| B · `aebd1f0d` | not_worse，guard | 0.237288 | 0.260870 | +0.023581 | 兑现，守护未退化 |
| C · `cb4086ad` | not_worse | 0.117647 | 0.800000 | +0.682353 | 兑现 |

两边都是 3/3 success，无 missing/error，配对准入 admitted。answer_f1 均值 0.203782 → 0.436957；evidence_f1 均值 0.133333 → 0.612795。指标均来自独立官方 QASPER scorer，未由前端或本报告重算；逐题完整精度和身份见下方工件。

**效果：regression_or_tradeoff；原因：undecided。** 三条预测兑现两条，但目标题下降，违反本轮 improve 预测；均分上涨不替代预注册逐题验收。目标答案从“资料未明确给出”变成笼统的数量描述，依然没有完成条目枚举；此处为执行 Agent 的语义复核，不冒充独立人工金标准。

不能把总分差归因于排序：A/B 的查询改写模型输出在两次运行中不同，实际入选的 16 个证据块集合也不同，上下文长度分别为 15237→13508 和 13338→14677；B 从一个改写子问题变为两个，候选多一次查询向量调用。C 的改写语义相同，入选证据文本多重集合和上下文长度 13309 均相同，实际呈现顺序改变，答案与引用改善；这提供有限机制线索，但单次生成不能排除模型随机性。没有追加 stage replay、固定中间状态重放或新实验。

## 实现与隔离

- 修正版假设卡：`d616c0858d8a4035bab5cf729d0cca6a`；Task：`da5e6fdf04504a1590ce3ed56282789e`。用户明确批准具体卡片后才创建实现 Agent。
- 分析 Agent（pi）：`9bc78b81-be5e-4ade-97d6-34348379b147`。实现 Agent（pi）：`7360ab31-e9ab-4052-8bb9-82af80f60692`。工作台实际创建和消息回执已保存；实现者只收到批准说明，没有附加题面、gold、运行摘要或分数。文件访问层仍无强制沙箱。
- 独立目录：`/home/wabiwabi/.paseo/worktrees/31u90a1x/sn-hyp-d616c0858d8a4035bab5cf729d0cca6a`；Paseo workspace `wks_1c3fdb3b67b6a358`；干净起点 `0680aa12313a5d3e67f4e0a4be94144a5b6af28c`。未复用停止的旧 H1 改动。
- 仅非 overlay chunk 合成启用阅读顺序排列。先完成原选择、保留席、去重和字符预算截断，再排列最终已发出的证据行，保留原 k 键、文本、省略号和映射。按实际生产者 `el-<source_id>-<index:04d>` 与来源身份识别序号，未知 ID 保留组内槽位。其他渲染调用默认行为不变。
- 两边实际 captured-source 各 663 文件，只有 `backend/app/services/ask_service.py`、`backend/app/services/evidence_context.py` 两处字节不同。源码 ref：基线 `81a682e4e65f66b5f1a69c3b18bce6d637a6dbb6c04605676fe357b182599a5a`，候选 `aa7a40970de3da035e98434a6386e9e68c2189b76cbafd8642db55672f0ae8dc`。
- bundle、scope、case IDs、scorer、metric denominators 均相同；模型配置、依赖和运行期配置的冻结文件哈希相同。模型名称/配置相同不保证远端生成确定性。

## 验证与审查

工作台相关两组测试分别 66、60 passed（有重叠，不相加），TypeScript、Expo Web 构建和隔离 Chromium 流程通过。实际服务返回的新入口、首页和脚本已核验。审查修复了卡片派发漏传身份、旧卡片 brief/扫描串目录、NULL-cwd 旧会话被切换，以及捕获 A 后选择 B 导致来源标签错配。多目录持有同一源码 ref 的歧义目前拒绝派发，不猜测。

SN 最终相关测试 271 passed；独立纯函数检查覆盖 9,331 组合成序列；最终四文件哈希与独立审查一致，0 open Important / 0 Blocking。泄漏扫描 clean，检查 4 文件。全量 backend lane 曾启动但中断、没有结果；本轮没有宣称 G1 全量通过或产品发布就绪。

## 原失败与费用

第一次基线在 importing 阶段失败：三次来源元素向量调用约 60 秒后失败，触发 circuit breaker，后续分块向量直接拒绝；三题均 missing，原 Job、空 outputs、官方缺失覆盖与失败日志完整保留。预算没有停止，失败时账本约 ¥0.014503 / ¥0.50。HTTP HEAD 能响应并不能证明向量健康；未确定服务端超时根因。

用户随后明确提高预算至低于 ¥8；本轮总上限设 ¥7.50。新建恢复 Job 使用**原已捕获基线源码**，保留同一计划、三题和冻结配置；恢复基线与候选各 ¥3，上限承诺合计含原运行为 ¥6.50。恢复运行和候选均成功，没有修改模型或为过测换题。

三次 SN 运行账本合计 **¥0.0437579**：有返回用量的估算 ¥0.0327194，原三次超时请求未结算的保守预留 ¥0.0110385。原/恢复/候选分别 14/30/31 次实际请求。数字基于本地账本费率，不是供应商账单，不含 pi 编码/分析 Agent 费用。

## 可回访工件

根目录：`var/sn-eval/r1-loop-20261014/`。

- `fixed-plan.json`、`reviewed-card.json`、`approval-receipt.json`、`budget-authorization-v2.json`：预注册范围、具体批准与追加预算。
- `worktree-receipt.json`、`baseline-preparation.json`、`baseline-recovery-preparation.json`、`candidate-preparation.json`：目录、基线保持和源码捕获。
- `baseline-failure-diagnosis.json`：原失败。有效 baseline execution `271ecd31c52a480fbda5d0345d0be820`；candidate `50f00516bf234e179e531bc1357c32c5`，各 run id 为 execution ID 后加 `-1`。
- `paired-raw-summary.json`、`paired-flips.json`、`hypothesis-check.json`、`hypothesis-conclusion.json`：官方逐题结果、配对与已保存结论。
- `observed-context-checks.json`、`rewrite-observations.json`、`comparison-controls.json`：实际呈现变量和因果限制。
- `cost-summary.json`、`candidate-file-hashes.json`、`implementer-finished.json`、`review-report.md`：费用、验证和独立审查。

本轮在第 3 步停止。未再运行模型、未扩展套件、未实现 stage replay、未合并或提交 SN 候选。
