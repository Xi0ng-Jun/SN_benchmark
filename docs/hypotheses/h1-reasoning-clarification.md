# H1：单来源资料问答的过度澄清

状态：2026-10-13 用户授权实施及同题单验证，尚未生成验证结果。统筹 Sol 执行，Fable 退出本轮。

## 假设与机制

原 reasoning 基线72题中30题为 clarification。部分资料内容问题在检索前被反问题目本身所求，导致无答案。假设：意图预览把“尚不知道资料中的答案”误当成“用户意图缺少必需信息”；在仅有一个来源且问题明确要求资料内容时，应先检索求证。

组件：reasoning 的意图预览／澄清判定（`reasoning_agent` 及其调用逻辑）。改动限于这些边界及必要的契约文档和单元测试。候选 owning 文件为 SN `backend/app/services/query_intent.py`、`backend/app/services/prompts.py` 及 Ask 传递来源范围的调用位置；实际文件在交付后补齐。

不读取 gold，不写死 QASPER、论文题型、case ID 或题面；不整体关闭澄清。真正存在多个解释、缺少必要操作对象／约束、资料外问题仍可澄清。优先利用现有来源范围与意图契约，避免另建评测专用行为。

## 预期受益与风险

受益：单一来源中定义、实验设置、方法／数据／结果等明确的资料事实问题，从无检索澄清变成实际检索和有据回答。

可能受害：真正歧义的指代、要求资料外信息或不足以确定用户目标的问题，从合理澄清变成臆测错答。必须用不含基准题面的合成单元测试覆盖资料未知≠意图歧义、单来源不自动取消澄清、真正歧义／资料外问题保留。

最可能推翻假设的结果：改动后 clarification 不减少；或减少主要来自无证据的错答，原本正确题退化。此时如实记录负结果，不补跑调参或改阈值追分。

## 实验与边界

- 唯一写入位置：Paseo workspace `wks_fc0534ddd56f1261`，worktree `/home/wabiwabi/.paseo/worktrees/31u90a1x/sn-eval-exp-1`，branch `sn-eval-exp-1`；原SN树、原配置、数据库不变。
- Task 起点绑定必须在改码前完成；改动后用 M2-C `run_argv` 做新 `capture_sn_source`，保存 Task/source-ref 关联。
- 原题单 `var/sn-eval/baselines/qasper-20-20261013/case-ids.txt`，固定seed20261013，20篇72题；仅 reasoning 一次、parallel=1，新目录 `h1-reasoning/`。
- 同实验配置SHA `e5a596292eb049fe984ccd28c6ac4b669ec2ad2501d316c6c43d941835aa9ab5`；共用原 `model-budget.json` 与¥10总上限，不追加、不释放未知预留。批准时可用约¥3.3722816，最终以账本为准。
- 若预算耗尽，按固定顺序停止真实请求，剩余分区登记 missing；不换题、不筛成功题、不重跑。
- 原reasoning72题基线作before，不用补跑择优替换；独立export／官方评分／阶段／新账本／逐分区翻转报告。不伪造跨分区统一evaluator身份。
- 留出5篇17题照常运行、自动评分与汇总，禁止用于读案例／归因／调参。结论包含clarification变化、翻转计数、新错答风险、留出表现和成本，不以均分差单独下结论。
- 改动前实际捕获与基线source-ref一致（`81a682e4…`），全部backend/app文件hash相同；证据 `h1/prechange-source-comparison.json`。改动后必须保持其余捕获字节不变，运行身份差异仍披露。

## 结果（待执行）

实际改动文件、SN测试命令／退出码、捕获ref、翻转结果、clarification变化、新错答与留出汇总、预算将在完成后写入本节。真实oracle、chunk H2、五套扩展、真实Job、preview切换不在本轮范围。
